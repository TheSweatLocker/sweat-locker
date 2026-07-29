"""Backtest v2 cohort family against mlb_game_results history.

For each historical game:
  1. Build a game context dict (from mlb_game_results columns)
  2. Look up prior-day results for both teams (for cohorts needing yesterday info)
  3. Run cohorts_v2.compute_v2_breakdown
  4. Grade each fired cohort side vs actual home_win

Report per-cohort:
  fire count, side split (home/away calls), win rate, EV per $100 at -110 juice

USAGE:
  python backtest_cohorts_v2.py                    # full history
  python backtest_cohorts_v2.py --lookback 90      # last 90 days
"""
import argparse
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

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
H = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}

sys.path.insert(0, str(Path(__file__).parent))
from cohorts_v2 import compute_v2_breakdown


COLS = ','.join([
    'game_id', 'game_date', 'away_team', 'home_team',
    'home_score', 'away_score', 'home_win', 'total_runs',
    'timezone_change', 'series_game_number',
    'away_consecutive_road_games', 'days_since_last_home_game',
    'away_team_xwoba', 'home_team_xwoba',
    'away_sp_xera', 'home_sp_xera',
    'away_wrc_proxy_l14', 'home_wrc_proxy_l14',
    'away_sp_days_rest', 'home_sp_days_rest',
    'away_bp_relievers_3d', 'home_bp_relievers_3d',
    'away_sp_last5_era', 'home_sp_last5_era',
])


def paginate_results(date_from: str, date_to: str, page_size: int = 1000) -> list:
    out = []
    for pg in range(20):
        hdrs = {**H, 'Range': f'{pg*page_size}-{(pg+1)*page_size-1}'}
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_results',
            params={'select': COLS,
                    'game_date': f'gte.{date_from}',
                    'game_date_1': f'lte.{date_to}',
                    'order': 'game_date.asc'},
            headers=hdrs, timeout=60,
        )
        # PostgREST doesn't accept duplicate keys via params dict — build URL manually
        break
    # Rebuild with proper URL
    out = []
    url = (f'{SB}/rest/v1/mlb_game_results'
           f'?select={COLS}&game_date=gte.{date_from}&game_date=lte.{date_to}'
           f'&order=game_date.asc')
    for pg in range(20):
        hdrs = {**H, 'Range': f'{pg*page_size}-{(pg+1)*page_size-1}'}
        r = requests.get(url, headers=hdrs, timeout=60)
        j = r.json()
        if not isinstance(j, list) or not j:
            break
        out.extend([x for x in j if isinstance(x, dict)])
        print(f'  page {pg+1}: {len(j)} rows (total {len(out)})', file=sys.stderr, flush=True)
        if len(j) < page_size: break
    return out


def build_prior_day_map(games: list) -> dict:
    """Map (game_date, team) → prior game result info for that team."""
    team_timeline = defaultdict(list)
    for g in games:
        d = g.get('game_date')
        away = g.get('away_team'); home = g.get('home_team')
        if not d or not away or not home: continue
        team_timeline[away].append({
            'date': d,
            'home_or_away': 'away',
            'opponent': home,          # NEW: opponent name for same-series check
            'won': not bool(g.get('home_win')) if g.get('home_win') is not None else None,
            'runs_scored': g.get('away_score'),
            'played': True,
        })
        team_timeline[home].append({
            'date': d,
            'home_or_away': 'home',
            'opponent': away,          # NEW
            'won': bool(g.get('home_win')) if g.get('home_win') is not None else None,
            'runs_scored': g.get('home_score'),
            'played': True,
        })
    for team in team_timeline:
        team_timeline[team].sort(key=lambda x: x['date'])

    out = {}
    for team, tl in team_timeline.items():
        for i, entry in enumerate(tl):
            if i == 0: continue
            prev = tl[i-1]
            three_days_before = (datetime.strptime(entry['date'], '%Y-%m-%d')
                                 - timedelta(days=3)).strftime('%Y-%m-%d')
            games_last_3 = sum(1 for e in tl[:i] if e['date'] > three_days_before)
            out[(entry['date'], team)] = {
                **prev,
                'games_last_3': games_last_3,
            }
    return out


def backtest(lookback_days: int | None = None):
    if lookback_days:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=lookback_days)
        date_from = start.isoformat()
        date_to = end.isoformat()
    else:
        date_from = '2026-01-01'
        date_to = datetime.now(timezone.utc).date().isoformat()

    print(f'== v2 cohort backtest · {date_from} → {date_to} ==', file=sys.stderr)
    games = paginate_results(date_from, date_to)
    resolved = [g for g in games if g.get('home_win') is not None]
    print(f'  {len(games)} games pulled · {len(resolved)} resolved', file=sys.stderr)

    prior_day_map = build_prior_day_map(games)
    print(f'  prior-day map size: {len(prior_day_map)}', file=sys.stderr)

    stats = defaultdict(lambda: {'w': 0, 'l': 0, 'home_calls': 0, 'away_calls': 0})
    stats_by_side = defaultdict(lambda: defaultdict(lambda: {'w': 0, 'l': 0}))
    v2_net_stats = defaultdict(lambda: {'w': 0, 'l': 0})

    for g in resolved:
        # Build ctx dict from row (all needed cols already in select)
        ctx = dict(g)
        winner_letter = 'home' if g['home_win'] else 'away'
        breakdown, net = compute_v2_breakdown(ctx, prior_day_map)

        # Per-cohort stats
        for name, info in breakdown.items():
            if name == 'not_fired': continue
            side = info['side']
            if side not in ('home', 'away'): continue
            won = side == winner_letter
            stats[name]['w' if won else 'l'] += 1
            if side == 'home': stats[name]['home_calls'] += 1
            else: stats[name]['away_calls'] += 1
            stats_by_side[name][side]['w' if won else 'l'] += 1

        # v2_net magnitude stats
        if net != 0:
            pick = 'home' if net > 0 else 'away'
            won = pick == winner_letter
            mag = abs(net)
            v2_net_stats[mag]['w' if won else 'l'] += 1

    def line(w, l):
        n = w + l
        return f'{w}-{l} ({round(w/n*100,1)}%) n={n}' if n else 'no fires'

    print('\n### v2 COHORTS — lifetime backtest ###')
    print(f'{"cohort":30s}  {"record":26s}  home/away  marker')
    rows = []
    for name, s in stats.items():
        n = s['w'] + s['l']
        if n == 0: continue
        rate = s['w'] / n
        rows.append((rate, n, name, s))
    rows.sort(key=lambda x: -x[0])
    for rate, n, name, s in rows:
        marker = '⭐' if rate >= 0.58 else ('✗' if rate < 0.47 else '')
        print(f'  {name:30s}  {line(s["w"], s["l"]):26s}  H{s["home_calls"]}/A{s["away_calls"]}  {marker}')

    print('\n### v2 COHORTS — side split ###')
    for name, sides in stats_by_side.items():
        h = sides.get('home', {'w':0,'l':0})
        a = sides.get('away', {'w':0,'l':0})
        if h['w']+h['l']+a['w']+a['l'] == 0: continue
        print(f'  {name:30s}  HOME: {line(h["w"], h["l"]):22s}  AWAY: {line(a["w"], a["l"])}')

    print('\n### v2_net magnitude buckets ###')
    for mag in sorted(v2_net_stats.keys()):
        s = v2_net_stats[mag]
        marker = '⭐' if s["w"]/(s["w"]+s["l"]) >= 0.58 else ('✗' if s["w"]/(s["w"]+s["l"]) < 0.47 else '')
        print(f'  |v2_net|={mag}  {line(s["w"], s["l"])}  {marker}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--lookback', type=int, help='Days to backtest (default: full history)')
    args = p.parse_args()
    backtest(lookback_days=args.lookback)
