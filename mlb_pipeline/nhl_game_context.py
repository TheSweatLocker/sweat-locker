"""NHL game context enrichment cron (2026-08-16).

Daily pipeline: pulls the next 7 days of NHL schedule + enriches each
game with team stats + team analytics (MoneyPuck) + goalie stats +
market odds + rest/back-to-back computation. Writes to nhl_game_context.

MVP scope (v1):
  * Schedule from NHL API
  * Team analytics from MoneyPuck (xGF/60, xGA/60, PP%, PK%)
  * Team season stats from NHL API
  * Odds API for ML + puckline + total
  * Rest days + back-to-back computed from schedule history
  * Goalie enrichment ATTEMPTED — falls back to None when starters
    unconfirmed (~1h before puck drop for most games)
  * NO public splits yet (those need scraper extensions — Phase 2)
  * NO ensemble scoring on write — that's a separate pass called by
    upsert_context (mirrors mlb_game_context pattern)

CLI:
  python nhl_game_context.py --date 2026-10-07     # single day
  python nhl_game_context.py --days 7              # today + next 7
  python nhl_game_context.py --dry-run             # print only
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

# Import our NHL data client
sys.path.insert(0, str(Path(__file__).parent))
from nhl_data_client import (
    get_schedule, get_gamecenter, get_team_stats,
    get_goalie_stats, get_team_analytics_mp,
)

ODDS_BASE = 'https://api.the-odds-api.com/v4/sports'
SPORT_KEY = 'icehockey_nhl'
# During preseason use icehockey_nhl_preseason if it exists.


def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _season_for_date(d: date) -> int:
    """NHL season key: starts in Oct, so games in Oct-Dec belong to
    that year's season; games Jan-June belong to the season that
    started prior October."""
    return d.year if d.month >= 9 else d.year - 1


# ═══════════════════════════════════════════════════════════════════════
# Enrichment stages
# ═══════════════════════════════════════════════════════════════════════

def enrich_team_stats(rows: list[dict], season: int) -> None:
    """In-place: add home/away team season stats from NHL API + MoneyPuck."""
    # Cache per-team so we don't refetch
    team_stats_cache: dict = {}
    team_analytics_cache: dict = {}
    for row in rows:
        for prefix, abbrev in (('home', row.get('home_team_abbrev')),
                                ('away', row.get('away_team_abbrev'))):
            if not abbrev: continue
            # NHL API season stats
            if abbrev not in team_stats_cache:
                team_stats_cache[abbrev] = get_team_stats(abbrev, season) or {}
            ts = team_stats_cache[abbrev]
            row[f'{prefix}_pp_pct'] = ts.get('pp_pct')
            row[f'{prefix}_pk_pct'] = ts.get('pk_pct')
            # MoneyPuck advanced analytics
            if abbrev not in team_analytics_cache:
                team_analytics_cache[abbrev] = get_team_analytics_mp(abbrev, season) or {}
            ta = team_analytics_cache[abbrev]
            row[f'{prefix}_xgf_per60'] = ta.get('xgf_per60')
            row[f'{prefix}_xga_per60'] = ta.get('xga_per60')
            row[f'{prefix}_high_danger_for'] = ta.get('high_danger_for')
            row[f'{prefix}_high_danger_against'] = ta.get('high_danger_against')
            row[f'{prefix}_5v5_cf'] = ta.get('corsi_for_pct')


def enrich_goalies(rows: list[dict], season: int) -> None:
    """Attempt goalie enrichment via gamecenter (works when starters
    confirmed ~1h before puck drop). Falls back cleanly to None."""
    goalie_cache: dict = {}
    for row in rows:
        gid = row.get('game_id')
        if not gid: continue
        gc = get_gamecenter(gid)
        if not gc: continue
        # NHL API structure varies; try both known paths
        matchup = gc.get('matchup', {})
        starters = matchup.get('goalieComparison', {})
        home_g = None; away_g = None
        try:
            home_g = starters.get('homeTeam', {}).get('leaders', [])[0].get('name', {}).get('default')
            away_g = starters.get('awayTeam', {}).get('leaders', [])[0].get('name', {}).get('default')
        except (IndexError, TypeError, AttributeError):
            pass
        if home_g:
            row['home_goalie'] = home_g
            row['home_goalie_confirmed'] = True
            if home_g not in goalie_cache:
                goalie_cache[home_g] = get_goalie_stats(home_g, season) or {}
            gs = goalie_cache[home_g]
            row['home_goalie_sv_pct'] = gs.get('sv_pct')
            row['home_goalie_gsaa'] = gs.get('gsaa')
        if away_g:
            row['away_goalie'] = away_g
            row['away_goalie_confirmed'] = True
            if away_g not in goalie_cache:
                goalie_cache[away_g] = get_goalie_stats(away_g, season) or {}
            gs = goalie_cache[away_g]
            row['away_goalie_sv_pct'] = gs.get('sv_pct')
            row['away_goalie_gsaa'] = gs.get('gsaa')


def enrich_market(rows: list[dict]) -> None:
    """Odds API pull. NHL market keys: h2h (ML), spreads (puckline), totals."""
    if not ODDS_KEY:
        print('  ⚠ ODDS_API_KEY missing — skipping market enrichment')
        return
    try:
        r = requests.get(f'{ODDS_BASE}/{SPORT_KEY}/odds/'
                         f'?apiKey={ODDS_KEY}&regions=us&markets=h2h,spreads,totals&oddsFormat=american',
                         timeout=20)
        if r.status_code != 200:
            print(f'  ⚠ Odds API {r.status_code}: {r.text[:150]}')
            return
        events = r.json()
    except Exception as e:
        print(f'  ⚠ Odds API error: {e}')
        return

    # Index by (home_team, away_team) canonical name for join
    events_by_matchup: dict = {}
    for e in events:
        key = (e.get('home_team'), e.get('away_team'))
        events_by_matchup[key] = e

    for row in rows:
        # NHL API returns just city (e.g. "Boston") — try to match odds API
        # which returns full team ("Boston Bruins")
        home_c = row.get('home_team') or ''
        away_c = row.get('away_team') or ''
        matched = None
        for (h, a), ev in events_by_matchup.items():
            if home_c and home_c in h and away_c and away_c in a:
                matched = ev; break
        if not matched: continue
        for book in matched.get('bookmakers', [])[:1]:  # first book = quick MVP
            for market in book.get('markets', []):
                if market['key'] == 'h2h':
                    for o in market.get('outcomes', []):
                        if o['name'] == matched['home_team']:
                            row['home_ml_close'] = int(o['price'])
                        elif o['name'] == matched['away_team']:
                            row['away_ml_close'] = int(o['price'])
                elif market['key'] == 'spreads':
                    for o in market.get('outcomes', []):
                        if o['name'] == matched['home_team']:
                            row['close_puckline'] = float(o.get('point'))
                elif market['key'] == 'totals':
                    for o in market.get('outcomes', []):
                        if o['name'] == 'Over':
                            row['close_total'] = float(o.get('point'))


def enrich_rest_and_travel(rows: list[dict]) -> None:
    """Compute rest days, back-to-back, road-trip length from recent
    schedule history. Reads nhl_game_context for last 5 days per team."""
    if not rows: return
    # Bulk fetch last 7 days of schedule to compute rest per team
    cutoff = (min(row['game_date'] for row in rows if row.get('game_date')) - timedelta(days=7)).isoformat() \
        if any(isinstance(row.get('game_date'), date) for row in rows) else \
        (date.today() - timedelta(days=7)).isoformat()
    try:
        r = requests.get(f'{SB}/rest/v1/nhl_game_context'
                         f'?game_date=gte.{cutoff}'
                         '&select=game_date,home_team_abbrev,away_team_abbrev',
                         headers=H_READ, timeout=15)
        history = r.json() if r.status_code == 200 else []
    except Exception:
        history = []
    # Also include the rows we're about to write so multi-day pulls see each other
    # Build per-team ordered dates
    team_dates: dict = {}
    for h in history + rows:
        d = h.get('game_date')
        if isinstance(d, str): d = date.fromisoformat(d)
        if not d: continue
        for abbrev in (h.get('home_team_abbrev'), h.get('away_team_abbrev')):
            if abbrev:
                team_dates.setdefault(abbrev, set()).add(d)

    for row in rows:
        d = row.get('game_date')
        if isinstance(d, str): d = date.fromisoformat(d)
        for prefix, abbrev in (('home', row.get('home_team_abbrev')),
                                ('away', row.get('away_team_abbrev'))):
            if not abbrev or not d: continue
            prior_games = sorted(x for x in team_dates.get(abbrev, set()) if x < d)
            if prior_games:
                row[f'{prefix}_rest_days'] = (d - prior_games[-1]).days
                row[f'{prefix}_back_to_back'] = (d - prior_games[-1]).days == 1
            else:
                row[f'{prefix}_rest_days'] = None
                row[f'{prefix}_back_to_back'] = False
            # Consecutive road games: for away team, count how many recent games
            # were also on the road (we don't have h/a per historical entry
            # cleanly — approximation for MVP)


# ═══════════════════════════════════════════════════════════════════════
# Upsert
# ═══════════════════════════════════════════════════════════════════════

def _apply_ensemble(row: dict) -> None:
    """Ensemble cutover (mirror of nfl_game_context / ncaaf_game_context).
    Runs ensemble_scorer.score_game('NHL', row) and writes result to
    row['primary_play'] in the same shape the app already reads. Legacy
    fallback: leaves primary_play None if ensemble returns nothing."""
    try:
        from ensemble_scorer import score_game as _ensemble_score
        from game_context import _compose_ensemble_sub
        decision = _ensemble_score('NHL', row)
        if decision is None: return
        top = decision.top()
        if top.pick is None: return
        row['primary_play'] = {
            'type': top.market, 'tier': top.tier, 'label': top.display_label,
            'side': top.side, 'line': top.line, 'conviction': top.conviction,
            'score': round(top.score, 2), 'sub': _compose_ensemble_sub(top),
            'audit_note': (f'ensemble_scorer v2 · NHL · {len(top.contributions)} sources · '
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


def upsert_context(rows: list[dict], dry_run: bool = False) -> int:
    if not rows: return 0
    # Ensemble scoring pass (writes primary_play in-place)
    for row in rows:
        _apply_ensemble(row)
    # Normalize date to string
    for row in rows:
        d = row.get('game_date')
        if isinstance(d, date): row['game_date'] = d.isoformat()
        row['fetched_at'] = datetime.now(timezone.utc).isoformat()
        row['updated_at'] = row['fetched_at']

    if dry_run:
        for r in rows:
            print(f'  [DRY] {r.get("game_date")} {r.get("away_team")} @ {r.get("home_team"):<15} '
                  f'ml={r.get("home_ml_close")}/{r.get("away_ml_close")} '
                  f'pl={r.get("close_puckline")} tot={r.get("close_total")} '
                  f'goalies={r.get("home_goalie") or "?"} / {r.get("away_goalie") or "?"} '
                  f'rest={r.get("home_rest_days")}/{r.get("away_rest_days")}')
        return len(rows)

    # Union keys for batch upsert (PostgREST)
    all_keys = set()
    for row in rows: all_keys.update(row.keys())
    normalized = [{k: r.get(k) for k in all_keys} for r in rows]

    written = 0
    for i in range(0, len(normalized), 100):
        chunk = normalized[i:i+100]
        pr = requests.post(
            f'{SB}/rest/v1/nhl_game_context?on_conflict=game_id',
            headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204): written += len(chunk)
        else: print(f'  ✗ upsert failed: {pr.status_code} {pr.text[:200]}')
    return written


# ═══════════════════════════════════════════════════════════════════════
# Top-level run
# ═══════════════════════════════════════════════════════════════════════

def run_for_date(game_date: date, dry_run: bool = False) -> int:
    print(f'  {game_date}')
    games = get_schedule(game_date.isoformat())
    if not games:
        print('    no games')
        return 0
    # Add game_date + season stamps
    for g in games:
        g['game_date'] = game_date
        g['season'] = _season_for_date(game_date)
    season = _season_for_date(game_date)

    print(f'    fetched {len(games)} games — enriching...')
    enrich_market(games)
    enrich_team_stats(games, season)
    enrich_goalies(games, season)
    enrich_rest_and_travel(games)

    written = upsert_context(games, dry_run=dry_run)
    print(f'    {"[DRY] " if dry_run else ""}wrote {written} contexts')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--days', type=int, default=1,
                   help='number of days including start date (default: 1)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    start = date.fromisoformat(args.date) if args.date else _et_now().date()
    print(f'=== NHL context enrichment · {start} + {args.days-1} days ===')
    total = 0
    for i in range(args.days):
        d = start + timedelta(days=i)
        total += run_for_date(d, dry_run=args.dry_run)
    print(f'\n{"[DRY] " if args.dry_run else ""}total written: {total}')


if __name__ == '__main__':
    main()
