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
    for i in range(0, len(normalized), 100):
        chunk = normalized[i:i+100]
        pr = requests.post(f'{SB}/rest/v1/nba_game_context?on_conflict=game_id',
                           headers=H_WRITE, json=chunk, timeout=30)
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
    written = upsert(all_rows, dry_run=dry_run)
    print(f'\n  {"[DRY] " if dry_run else ""}wrote {written}/{len(all_rows)} rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--days', type=int, default=1, help='Days from --date to include')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(target_date=args.date, days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        from season_gate import season_gate_or_exit
        season_gate_or_exit('NBA')
    except ImportError:
        pass
    main()
