"""Live compute of v2 cohort family for today's slate.

Runs as shadow-of-v1 during nightly cron:
  1. Pull today's mlb_game_context (facts for cohort compute)
  2. Pull yesterday's mlb_game_results (for prior-day map)
  3. Run cohorts_v2.compute_v2_breakdown on each game
  4. PATCH mlb_game_context.signal_confluence_v2_breakdown + _v2_net

v1 (signal_confluence_breakdown/_net) stays untouched. compare_v1_v2_nightly.py
grades both against actual results next morning.

USAGE:
  python compute_cohorts_v2.py                 # today ET
  python compute_cohorts_v2.py --date 2026-07-29
  python compute_cohorts_v2.py --dry-run
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

sys.path.insert(0, str(Path(__file__).parent))
from cohorts_v2 import compute_v2_breakdown


# Cohort inputs live in mlb_game_results (fuller schema than mlb_game_context —
# specifically series_game_number + sp_days_rest + sp_last5_era exist there).
# We READ from results and WRITE to context.
CTX_COLS = ','.join([
    'game_id', 'game_date', 'away_team', 'home_team',
    'close_spread', 'close_total',
    'timezone_change', 'series_game_number',
    'away_consecutive_road_games', 'days_since_last_home_game',
    'away_team_xwoba', 'home_team_xwoba',
    'away_sp_xera', 'home_sp_xera',
    'away_wrc_proxy_l14', 'home_wrc_proxy_l14',
    'away_sp_days_rest', 'home_sp_days_rest',
    'away_bp_relievers_3d', 'home_bp_relievers_3d',
    'away_sp_last5_era', 'home_sp_last5_era',
])


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def load_today(game_date: str) -> list:
    # Read from mlb_game_results (superset schema); this includes today's
    # pre-game rows (home_win=null) with all needed cohort inputs.
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_results',
        params={'game_date': f'eq.{game_date}', 'select': CTX_COLS},
        headers=H_READ, timeout=30,
    )
    return r.json() if r.status_code == 200 else []


def load_recent_results(game_date: str, days_back: int = 5) -> list:
    """Pull last N days of results to build prior-day map."""
    d = datetime.strptime(game_date, '%Y-%m-%d')
    start = (d - timedelta(days=days_back)).strftime('%Y-%m-%d')
    end = (d - timedelta(days=1)).strftime('%Y-%m-%d')
    url = (f'{SB}/rest/v1/mlb_game_results'
           f'?select=game_date,away_team,home_team,away_score,home_score,home_win'
           f'&game_date=gte.{start}&game_date=lte.{end}&order=game_date.asc')
    r = requests.get(url, headers=H_READ, timeout=30)
    return r.json() if r.status_code == 200 else []


def build_prior_day_map(games: list) -> dict:
    team_timeline = defaultdict(list)
    for g in games:
        d = g.get('game_date')
        away = g.get('away_team'); home = g.get('home_team')
        if not d or not away or not home: continue
        team_timeline[away].append({
            'date': d, 'home_or_away': 'away', 'opponent': home,
            'won': not bool(g.get('home_win')) if g.get('home_win') is not None else None,
            'runs_scored': g.get('away_score'), 'played': True,
        })
        team_timeline[home].append({
            'date': d, 'home_or_away': 'home', 'opponent': away,
            'won': bool(g.get('home_win')) if g.get('home_win') is not None else None,
            'runs_scored': g.get('home_score'), 'played': True,
        })
    for team in team_timeline:
        team_timeline[team].sort(key=lambda x: x['date'])

    # For live compute, prior_day entry for a given date = team's LATEST prior game
    # (indexed by TODAY's date, contains most-recent prior game info)
    out = {}
    for team, tl in team_timeline.items():
        if not tl: continue
        latest = tl[-1]
        # Count games in 3 days before latest — inclusive of latest
        three_days_before = (datetime.strptime(latest['date'], '%Y-%m-%d')
                             - timedelta(days=3)).strftime('%Y-%m-%d')
        games_last_3 = sum(1 for e in tl if e['date'] > three_days_before)
        # Key by any date >= latest+1 (typically today) to capture "prior game" lookup
        for future_date in [
            (datetime.strptime(latest['date'], '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')
            for i in range(1, 4)
        ]:
            out[(future_date, team)] = {**latest, 'games_last_3': games_last_3}
    return out


def update_context(game_id: str, breakdown: dict, net: int) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/mlb_game_context?game_id=eq.{game_id}',
        headers=H_WRITE,
        json={'signal_confluence_v2_breakdown': breakdown, 'signal_confluence_v2_net': net},
        timeout=15,
    )
    if r.status_code not in (200, 204):
        print(f'    ⚠ patch {r.status_code}: {r.text[:200]}')
        return False
    return True


def run(game_date: str | None = None, dry_run: bool = False):
    game_date = game_date or _et_today()
    print(f'== compute_cohorts_v2 · {game_date} ==')
    ctxs = load_today(game_date)
    prior_games = load_recent_results(game_date, days_back=5)
    prior_map = build_prior_day_map(prior_games)
    print(f'  {len(ctxs)} games · {len(prior_games)} prior · {len(prior_map)} timeline keys')

    updated = 0
    for c in ctxs:
        breakdown, net = compute_v2_breakdown(c, prior_map)
        fired = [k for k in breakdown if k != 'not_fired']
        home_votes = sum(1 for k in fired if breakdown[k].get('side') == 'home')
        away_votes = sum(1 for k in fired if breakdown[k].get('side') == 'away')
        print(f"  {c['away_team'][:14]:14s} @ {c['home_team'][:14]:14s}  "
              f"v2_net={net:+d}  H{home_votes}/A{away_votes}  fired={fired}")
        if not dry_run:
            if update_context(c['game_id'], breakdown, net):
                updated += 1

    print(f'\nSummary: {updated} contexts updated')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (defaults to today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)
