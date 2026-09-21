"""NBA game context enrichment (2026-08-17 rebuild).

Daily pipeline: pulls today's + next 3 days of NBA schedule from ESPN,
enriches with market odds (Odds API), rest days, back-to-back detection.
Writes to nba_game_context.

MVP scope:
  * Schedule from ESPN API
  * Odds from The Odds API (h2h, spreads, totals)
  * Rest / back-to-back computed from ctx history
  * Season labeling (preseason/regular/playoffs based on date)
  * NO ensemble scoring on write (model choice deferred; scoring can
    be layered on later via primary_play upsert path)

Team stats + advanced metrics come from a separate script
(nba_team_stats_pull.py — future addition).

CLI:
  python nba_game_context.py --date 2026-10-22
  python nba_game_context.py --days 4
  python nba_game_context.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

sys.path.insert(0, str(Path(__file__).parent))
from nba_data_client import get_schedule

ODDS_BASE = 'https://api.the-odds-api.com/v4/sports'
SPORT_KEY = 'basketball_nba'


# ═══════════════════════════════════════════════════════════════════════
# Elo model apply (2026-08-17)
# ═══════════════════════════════════════════════════════════════════════

_ELO_RATINGS_CACHE = None  # trained once per run


def _load_elo_ratings():
    """Train Elo from nba_game_results (all history). Cached per run.
    Returns dict {team_full_name: {'elo': X, 'avg_pts_for': Y, 'avg_pts_against': Z}}."""
    global _ELO_RATINGS_CACHE
    if _ELO_RATINGS_CACHE is not None: return _ELO_RATINGS_CACHE
    try:
        from nba_elo import train
        _ELO_RATINGS_CACHE = train(season=None)
    except Exception as e:
        print(f'  ⚠ Elo training failed: {e}')
        _ELO_RATINGS_CACHE = {}
    return _ELO_RATINGS_CACHE


def enrich_elo(rows: list[dict]) -> None:
    """Attach Elo-derived projected_spread + projected_total +
    projected_home_wp to each ctx row."""
    ratings = _load_elo_ratings()
    if not ratings:
        print('  ⚠ Elo ratings empty — skipping enrichment')
        return
    from nba_elo import predict as elo_predict
    for row in rows:
        pred = elo_predict(row.get('home_team',''), row.get('away_team',''), ratings)
        row['projected_spread'] = pred['projected_spread']
        row['projected_total'] = pred['projected_total']
        row['projected_home_wp'] = pred['projected_home_wp']
        row['elo_home'] = pred['home_elo']
        row['elo_away'] = pred['away_elo']
        row['elo_updated_at'] = datetime.now(timezone.utc).isoformat()


def _season_label(gd: date) -> tuple[str, str]:
    """Returns (season_str, season_type). Season string uses NBA's
    cross-year format ('2025-26'). Season type based on typical NBA
    calendar (regular starts late Oct, playoffs mid-April)."""
    year = gd.year
    if gd.month >= 8:
        season = f'{year}-{str(year+1)[-2:]}'
    else:
        season = f'{year-1}-{str(year)[-2:]}'

    m = gd.month; d = gd.day
    if (m == 10 and d < 20) or m == 9 or (m == 8):
        return season, 'preseason'
    if m == 4 and d >= 15 or m in (5, 6):
        return season, 'playoffs'
    return season, 'regular'


def enrich_market(rows: list[dict]) -> None:
    """Pull odds for h2h + spreads + totals. Attaches to ctx rows via
    home/away_team full name match."""
    if not ODDS_KEY:
        print('  ⚠ ODDS_API_KEY missing — skipping market enrichment')
        return
    try:
        r = requests.get(
            f'{ODDS_BASE}/{SPORT_KEY}/odds/'
            f'?apiKey={ODDS_KEY}&regions=us,us2&markets=h2h,spreads,totals'
            '&oddsFormat=american', timeout=20)
        if r.status_code != 200:
            print(f'  ⚠ Odds API {r.status_code}: {r.text[:150]}')
            return
        events = r.json()
    except Exception as e:
        print(f'  ⚠ Odds API error: {e}')
        return

    by_matchup = {}
    for e in events:
        by_matchup[(e.get('home_team'), e.get('away_team'))] = e

    for row in rows:
        home_full = row.get('home_team') or ''
        away_full = row.get('away_team') or ''
        ev = None
        for (h, a), e_ in by_matchup.items():
            if home_full and home_full in h and away_full and away_full in a:
                ev = e_; break
        if not ev: continue

        # Prefer DraftKings if available; else first bookmaker
        books = ev.get('bookmakers', [])
        book = next((b for b in books if b.get('key') == 'draftkings'), books[0] if books else None)
        if not book: continue

        for mkt in book.get('markets', []):
            key = mkt.get('key')
            for o in mkt.get('outcomes', []):
                name = o.get('name')
                price = o.get('price')
                point = o.get('point')
                if key == 'h2h':
                    if name == home_full: row['home_ml_close'] = int(price)
                    elif name == away_full: row['away_ml_close'] = int(price)
                elif key == 'spreads':
                    if name == home_full and point is not None: row['close_spread'] = float(point)
                elif key == 'totals':
                    if name == 'Over' and point is not None: row['close_total'] = float(point)


def enrich_rest(rows: list[dict]) -> None:
    """Compute rest days + back-to-back from ctx history."""
    if not rows: return
    all_teams = set()
    for row in rows:
        all_teams.add(row.get('home_team'))
        all_teams.add(row.get('away_team'))
    all_teams.discard(None)
    if not all_teams: return

    min_date = min(row['game_date'] for row in rows)
    lookback = (min_date if isinstance(min_date, date) else
                datetime.fromisoformat(str(min_date)).date()) - timedelta(days=7)
    lookback_iso = lookback.isoformat()

    # Bulk fetch recent games for these teams
    teams_list = ','.join(f'"{t}"' for t in all_teams if t)
    r = requests.get(f'{SB}/rest/v1/nba_game_results'
                     f'?game_date=gte.{lookback_iso}'
                     f'&or=(home_team.in.({teams_list}),away_team.in.({teams_list}))'
                     '&select=game_date,home_team,away_team',
                     headers=H_READ, timeout=15)
    history = r.json() if r.status_code == 200 else []

    def last_game_for(team: str, before_dt: date) -> Optional[date]:
        candidates = []
        for h in history:
            if h['home_team'] == team or h['away_team'] == team:
                try: gd = date.fromisoformat(h['game_date'])
                except Exception: continue
                if gd < before_dt: candidates.append(gd)
        return max(candidates) if candidates else None

    for row in rows:
        gd = row['game_date']
        if isinstance(gd, str): gd = date.fromisoformat(gd)
        h_last = last_game_for(row.get('home_team'), gd)
        a_last = last_game_for(row.get('away_team'), gd)
        row['home_rest_days'] = (gd - h_last).days if h_last else None
        row['away_rest_days'] = (gd - a_last).days if a_last else None
        row['home_is_b2b'] = row['home_rest_days'] == 1 if row['home_rest_days'] is not None else None
        row['away_is_b2b'] = row['away_rest_days'] == 1 if row['away_rest_days'] is not None else None


def enrich_team_stats(rows: list) -> None:
    """Join nba_team_stats onto each context row.

    2026-09-21: nothing did this. nba_four_factors_pull writes
    nba_team_stats and nba_game_context never read it, so
    home_net_rating / off / def / pace were NULL on every row — which is
    why the first scored slate produced an EMPTY confluence breakdown on
    every game and leaned entirely on the elo-vs-market spread edge.
    """
    r = requests.get(f'{SB}/rest/v1/nba_team_stats',
                     headers=H_READ,
                     params={'select': 'team_name,team_abbrev,net_rating,'
                                       'off_rating,def_rating,pace,efg_pct'},
                     timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ nba_team_stats fetch {r.status_code} — ratings stay NULL')
        return
    # nba_team_stats holds MORE THAN ONE generation of rows per team:
    # nba_four_factors_pull writes efg/tov/orb/pace with NULL ratings,
    # while an older pull left net/off/def ratings on a prior season row.
    # Taking whichever row sorts last silently won with the ratings-less
    # one — the join reported "28 sides matched" and every rating came
    # back None. Merge per team instead, field by field, keeping the
    # first non-null seen. Rows are walked newest-season-first so current
    # numbers win and older ones only fill gaps.
    merged: dict = {}
    rows_sorted = sorted(r.json(),
                         key=lambda t: str(t.get('season') or ''), reverse=True)
    FIELDS = ('net_rating', 'off_rating', 'def_rating', 'pace', 'efg_pct')
    for t in rows_sorted:
        for k in (t.get('team_name'), t.get('team_abbrev')):
            if not k:
                continue
            key = str(k).strip().lower()
            slot = merged.setdefault(key, {})
            for f in FIELDS:
                if slot.get(f) is None and t.get(f) is not None:
                    slot[f] = t.get(f)
    by_name = merged
    hit = miss = 0
    for row in rows:
        for side in ('home', 'away'):
            team = str(row.get(f'{side}_team') or '').strip().lower()
            t = by_name.get(team)
            if not t:
                miss += 1
                continue
            hit += 1
            row[f'{side}_net_rating'] = t.get('net_rating')
            row[f'{side}_off_rating'] = t.get('off_rating')
            row[f'{side}_def_rating'] = t.get('def_rating')
            row[f'{side}_pace'] = t.get('pace')
    print(f'  team stats joined: {hit} sides matched, {miss} unmatched')


def compute_confluence(row: dict) -> tuple:
    """Count home-leaning vs away-leaning signals. -> (net, breakdown).

    2026-09-21. NBA had no confluence at all, which is why it had no
    sweat_score: the score is a blend of model edge and confluence, and
    one of the two inputs did not exist.

    Signals are the ones NBA context already carries — no new data pull.
    Each contributes at most one vote, so a single dimension cannot
    manufacture a high score on its own.
    """
    b = {}

    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    hn, an = _f(row.get('home_net_rating')), _f(row.get('away_net_rating'))
    if hn is not None and an is not None and abs(hn - an) >= 3.0:
        b['net_rating'] = 'home' if hn > an else 'away'

    ho, ao = _f(row.get('home_off_rating')), _f(row.get('away_off_rating'))
    if ho is not None and ao is not None and abs(ho - ao) >= 3.0:
        b['off_rating'] = 'home' if ho > ao else 'away'

    # Defensive rating is inverted — LOWER is better.
    hd, ad = _f(row.get('home_def_rating')), _f(row.get('away_def_rating'))
    if hd is not None and ad is not None and abs(hd - ad) >= 3.0:
        b['def_rating'] = 'home' if hd < ad else 'away'

    # Rest edge. A two-day advantage is the point where it starts to show.
    hr, ar = _f(row.get('home_rest_days')), _f(row.get('away_rest_days'))
    if hr is not None and ar is not None and abs(hr - ar) >= 2:
        b['rest'] = 'home' if hr > ar else 'away'

    # Back-to-back is a penalty for the team ON it, and only counts when
    # the opponent is NOT also on one.
    hb, ab = bool(row.get('home_is_b2b')), bool(row.get('away_is_b2b'))
    if hb != ab:
        b['back_to_back'] = 'away' if hb else 'home'

    hi, ai = _f(row.get('home_injury_impact')), _f(row.get('away_injury_impact'))
    if hi is not None and ai is not None and abs(hi - ai) >= 2.0:
        # Higher impact = more hurt, so it favours the OTHER side.
        b['injuries'] = 'away' if hi > ai else 'home'

    he, ae = _f(row.get('elo_home')), _f(row.get('elo_away'))
    if he is not None and ae is not None and abs(he - ae) >= 60:
        b['elo'] = 'home' if he > ae else 'away'

    h = sum(1 for v in b.values() if v == 'home')
    a = sum(1 for v in b.values() if v == 'away')
    return h - a, b


def compute_sweat_score(projected_spread, close_spread, confluence_net,
                        projected_total, close_total) -> int:
    """0-100 composite. Ported from ncaab_game_context so basketball
    scores the same way in both leagues; NFL/NCAAF carry the same shape.

    close_spread is home-perspective NEGATIVE = home favored, and
    projected_spread is positive = home favored, so the edge is their
    SUM. This is the convention that has been inverted three separate
    times in this codebase — do not "simplify" it to a subtraction.
    """
    score = 45
    if projected_spread is not None and close_spread is not None:
        edge = abs(float(projected_spread) + float(close_spread))
        if edge >= 4.0:
            score += 25
        elif edge >= 3.0:
            score += 18
        elif edge >= 2.0:
            score += 12
        elif edge >= 1.0:
            score += 6
    ac = abs(int(confluence_net or 0))
    if ac >= 5:
        score += 18
    elif ac >= 4:
        score += 12
    elif ac >= 3:
        score += 8
    elif ac >= 2:
        score += 4
    if projected_total is not None and close_total is not None:
        te = abs(float(projected_total) - float(close_total))
        if te >= 8.0:
            score += 8
        elif te >= 5.0:
            score += 5
        elif te >= 3.0:
            score += 3
    return min(100, max(0, score))


def sweat_tier(score) -> str:
    """Cutoffs identical to play_of_day._sweat_tier and every other
    sport, so a PRIME means the same thing across the app."""
    s = int(score or 0)
    if s >= 80:
        return 'PRIME'
    if s >= 65:
        return 'STRONG'
    if s >= 50:
        return 'LIGHT_LEAN'
    return 'PASS'


def enrich_sweat(rows: list) -> None:
    """Write the server-owned score onto every row.

    This is the column the app must read. NBA scoring has lived in
    app/index.tsx since launch (~106 lines, `modelMismatch` x66) while
    MLB moved server-side years-equivalent ago; once this populates, the
    client block can be deleted and the two can stop disagreeing.
    """
    capped = 0
    for row in rows:
        net, bd = compute_confluence(row)
        row['signal_confluence_net'] = net
        row['signal_confluence_breakdown'] = bd
        score = compute_sweat_score(row.get('projected_spread'),
                                    row.get('close_spread'), net,
                                    row.get('projected_total'),
                                    row.get('close_total'))
        # NO-CORROBORATION CAP.
        #
        # The first scored NBA slate came out 9-of-14 STRONG with an
        # EMPTY confluence breakdown on every game: ratings were NULL, so
        # the entire score was one preseason elo-vs-market spread edge —
        # and preseason elo carries last season's roster. A score built
        # on a single dimension is not confluence, and shipping it would
        # reproduce the tier inflation we spent the weekend removing.
        #
        # If nothing corroborates, the game cannot exceed LIGHT_LEAN. The
        # raw score is preserved for audit so the cap is visible rather
        # than silently rewriting the number.
        if not bd:
            if score >= 65:
                capped += 1
            row['sweat_breakdown'] = {'sweat_score_raw': score,
                                      'capped': 'no_corroborating_signals'}
            score = min(score, 64)
        row['sweat_score'] = score
        row['sweat_tier'] = sweat_tier(score)
        row['sweat_tier_current'] = row['sweat_tier']
    if capped:
        print(f'  ⚠ {capped} game(s) capped to LIGHT_LEAN — no corroborating '
              f'signals (ratings missing?)')


def _apply_ensemble(row: dict) -> None:
    """2026-08-20: Ensemble scoring for NBA (parity with NHL/NFL/NCAAF/MLB).
    Runs ensemble_scorer.score_game('NBA', row) and writes result to
    row['primary_play'] before upsert. Ensemble is sport-universal — as
    long as signal_sources rows for NBA are enabled, this works.
    Fallback: leaves primary_play None if ensemble errors or returns None."""
    try:
        from ensemble_scorer import score_game as _ensemble_score
        from game_context import _compose_ensemble_sub
        decision = _ensemble_score('NBA', row)
        if decision is None: return
        top = decision.top()
        if top.pick is None: return
        row['primary_play'] = {
            'type': top.market, 'tier': top.tier, 'label': top.display_label,
            'side': top.side, 'line': top.line, 'conviction': top.conviction,
            'score': round(top.score, 2), 'sub': _compose_ensemble_sub(top),
            'audit_note': (f'ensemble_scorer v2 · NBA · {len(top.contributions)} sources · '
                           f'score={top.score:.2f} margin={top.margin:+.2f}'),
            '_engine': 'ensemble_v2',
            '_ensemble_sources': [
                {'signal_key': c.signal_key, 'class': c.signal_class,
                 'side': c.side, 'weight': round(c.weight, 2),
                 'n': c.n, 'contribution': round(c.contribution, 2),
                 'prose': c.display_prose}
                for c in top.contributions[:8]
            ],
            '_ensemble_all_markets': {
                'ml':    {'pick': decision.ml.pick, 'label': decision.ml.display_label,
                          'tier': decision.ml.tier, 'conviction': decision.ml.conviction},
                'rl':    {'pick': decision.rl.pick, 'label': decision.rl.display_label,
                          'tier': decision.rl.tier, 'conviction': decision.rl.conviction},
                'total': {'pick': decision.total.pick, 'label': decision.total.display_label,
                          'tier': decision.total.tier, 'conviction': decision.total.conviction},
            },
        }
        # 2026-09-08 wire defensive_gates for NBA parity with NFL/NCAAF/MLB.
        # OC-flip → MC-dissent → juice-trap (NBA cap -400) → publish gate.
        #
        # 2026-09-19 CORRECTION: the line that used to sit here said "LR
        # override no-ops (no NBA LR model trained — blocked on historical
        # odds backfill)". That stopped being true on 09-14 when the
        # historical-odds backfill landed. models/nba_ml_logreg.json was
        # retrained 09-17: 70.4% test accuracy vs 53.5% baseline, +16.8pp
        # on n=1,324. defensive_gates loads it as _LR_MODEL_NBA_ML and the
        # LR override DOES fire for NBA.
        #
        # MC-dissent, however, still no-ops: there is no nba_mc_simulator,
        # so nothing populates ctx['mc_probabilities'] and that gate reads
        # an empty dict on every NBA game. A gate that never fires is not
        # a safe gate — it is an untested one. Tracked for pre-season.
        try:
            from defensive_gates import apply_all_defensive_gates
            row['primary_play'] = apply_all_defensive_gates(
                row['primary_play'], row, sport='NBA')
        except Exception:
            pass  # gates unavailable — keep raw ensemble output
    except Exception:
        pass  # ensemble unavailable — leave primary_play alone


def upsert(rows: list[dict], dry_run: bool = False) -> int:
    if not rows: return 0
    # Ensemble scoring pass — writes primary_play in-place. Placed BEFORE
    # date normalization so scorer sees typed date objects. Runs per-row
    # so a bad row doesn't kill the whole slate (each _apply_ensemble is
    # wrapped in try/except).
    for row in rows:
        _apply_ensemble(row)
    for row in rows:
        d = row.get('game_date')
        if isinstance(d, date): row['game_date'] = d.isoformat()
        row['updated_at'] = datetime.now(timezone.utc).isoformat()

    if dry_run:
        for r in rows:
            print(f'  [DRY] {r.get("game_date")} {r.get("away_team")} @ {r.get("home_team")}  '
                  f'sp={r.get("close_spread")} tot={r.get("close_total")} '
                  f'ml={r.get("home_ml_close")}/{r.get("away_ml_close")} '
                  f'rest={r.get("home_rest_days")}/{r.get("away_rest_days")}')
        return len(rows)

    all_keys = set()
    for row in rows: all_keys.update(row.keys())
    normalized = [{k: r.get(k) for k in all_keys} for r in rows]

    written = 0
    # 2026-08-23 Wave 1b multi-sport: snapshot writer
    try:
        from snapshot_writer import write_primary_play_snapshot
        _snap = write_primary_play_snapshot
    except Exception:
        _snap = None
    from pgrst_strip_retry import post_with_strip_retry
    for i in range(0, len(normalized), 100):
        chunk = normalized[i:i+100]
        pr, stripped = post_with_strip_retry(
            f'{SB}/rest/v1/nba_game_context?on_conflict=game_id',
            H_WRITE, chunk)
        if stripped and pr.status_code in (200, 201, 204):
            print(f"  ⚠ nba_game_context: stripped missing cols ({', '.join(stripped[:5])}) — schema lag, apply pending migrations")
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
            if _snap:
                for row in chunk:
                    try: _snap(SB, H_WRITE, 'NBA', row)
                    except Exception: pass
        else: print(f'  ✗ upsert failed: {pr.status_code} {pr.text[:200]}')
    return written


def run(target_date: Optional[str] = None, days: int = 1, dry_run: bool = False):
    start_d = date.fromisoformat(target_date) if target_date else \
              (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    print(f'=== nba_game_context · {start_d} (+{days-1} days) ===')

    all_rows: list[dict] = []
    for i in range(days):
        gd = start_d + timedelta(days=i)
        games = get_schedule(gd.isoformat())
        if not games:
            print(f'  {gd}: no games')
            continue
        season, season_type = _season_label(gd)
        for g in games:
            g['game_date'] = gd
            g['season'] = season
            g['season_type'] = season_type
        all_rows.extend(games)
        print(f'  {gd}: {len(games)} games · {season_type}')

    if not all_rows:
        print('  no games in window'); return

    enrich_market(all_rows)
    enrich_rest(all_rows)
    enrich_elo(all_rows)
    enrich_team_stats(all_rows)
    # Must run AFTER market (close_spread/close_total) and elo
    # (projected_spread/projected_total) — the score is the disagreement
    # between those two, so running it earlier scores against nulls and
    # every game comes out at the 45 base.
    enrich_sweat(all_rows)
    written = upsert(all_rows, dry_run=dry_run)
    print(f'\n  {"[DRY] " if dry_run else ""}wrote {written}/{len(all_rows)} rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--days', type=int, default=1, help='Days from --date to include')
    p.add_argument('--dry-run', action='store_true')
    # season_gate reads this straight off sys.argv, but strict argparse
    # rejects the unknown arg before the gate ever sees it — so the
    # documented bypass was impossible to pass to this script. Declared
    # here purely so argparse lets it through; season_gate still owns the
    # behaviour. Needed to build context for a season that has not
    # started, which is exactly the preseason readiness case.
    p.add_argument('--force-offseason', action='store_true',
                   help='build context even when the sport is out of season')
    args = p.parse_args()
    run(target_date=args.date, days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        from season_gate import season_gate_or_exit
        season_gate_or_exit('NBA')
    except ImportError:
        pass
    main()
