"""Head-to-head v1 vs v2 confluence net grader — runs post-game each night.

For each resolved game in the target date, compares:
  v1 pick: sign(signal_confluence_net) → home/away
  v2 pick: sign(signal_confluence_v2_net) → home/away

Reports per-magnitude and per-agreement bucket:
  agree_both_correct   both v1 & v2 point same side, and won
  disagree_v1_won      opposite sides, v1 was right
  disagree_v2_won      opposite sides, v2 was right
  v1_only_side         v1 fires, v2 silent
  v2_only_side         v2 fires, v1 silent

Also grades v2_net magnitude buckets (looking for the 75%-hit-rate |net|=3
sweet spot from backtest to hold up in live data).

USAGE:
  python compare_v1_v2_nightly.py                   # yesterday ET
  python compare_v1_v2_nightly.py --date 2026-07-29
  python compare_v1_v2_nightly.py --lookback 14     # last 14 days aggregate
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
H = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}


def _et_yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4) - timedelta(days=1)).date().isoformat()


def load_games(date_from: str, date_to: str) -> list:
    """Join mlb_game_results (winners) with mlb_game_context (v1+v2 nets)
    across the target range."""
    url = (f'{SB}/rest/v1/mlb_game_context'
           f'?select=game_id,game_date,away_team,home_team,signal_confluence_net,'
           f'signal_confluence_v2_net'
           f'&game_date=gte.{date_from}&game_date=lte.{date_to}')
    ctxs = requests.get(url, headers=H, timeout=30).json()
    ctx_map = {c['game_id']: c for c in ctxs if isinstance(c, dict)}
    if not ctx_map: return []

    url2 = (f'{SB}/rest/v1/mlb_game_results'
            f'?select=game_id,home_win'
            f'&game_date=gte.{date_from}&game_date=lte.{date_to}'
            f'&home_win=not.is.null')
    res = requests.get(url2, headers=H, timeout=30).json()

    joined = []
    for r in res if isinstance(res, list) else []:
        c = ctx_map.get(r['game_id'])
        if not c: continue
        joined.append({**c, 'home_win': r['home_win']})
    return joined


def bucket(w, l):
    n = w + l
    return f'{w}-{l} ({round(w/n*100,1)}%) n={n}' if n else '0-0'


def run(date_from: str, date_to: str):
    print(f'== v1 vs v2 · {date_from} → {date_to} ==')
    games = load_games(date_from, date_to)
    print(f'  {len(games)} resolved games with both v1+v2 populated\n')

    v1_stats = {'w': 0, 'l': 0}
    v2_stats = {'w': 0, 'l': 0}
    agree_both_correct = 0
    agree_both_wrong = 0
    disagree_v1_won = 0
    disagree_v2_won = 0
    v2_net_magnitude = defaultdict(lambda: {'w': 0, 'l': 0})
    v1_net_magnitude = defaultdict(lambda: {'w': 0, 'l': 0})

    for g in games:
        winner = 'H' if g['home_win'] else 'A'
        v1 = g.get('signal_confluence_net') or 0
        v2 = g.get('signal_confluence_v2_net') or 0
        v1_side = 'H' if v1 > 0 else ('A' if v1 < 0 else None)
        v2_side = 'H' if v2 > 0 else ('A' if v2 < 0 else None)

        if v1_side:
            v1_won = v1_side == winner
            v1_stats['w' if v1_won else 'l'] += 1
            v1_net_magnitude[abs(v1)]['w' if v1_won else 'l'] += 1
        if v2_side:
            v2_won = v2_side == winner
            v2_stats['w' if v2_won else 'l'] += 1
            v2_net_magnitude[abs(v2)]['w' if v2_won else 'l'] += 1

        if v1_side and v2_side and v1_side == v2_side:
            if v1_side == winner: agree_both_correct += 1
            else: agree_both_wrong += 1
        elif v1_side and v2_side and v1_side != v2_side:
            if v1_side == winner: disagree_v1_won += 1
            elif v2_side == winner: disagree_v2_won += 1

    print('### Overall accuracy ###')
    print(f'  v1: {bucket(v1_stats["w"], v1_stats["l"])}')
    print(f'  v2: {bucket(v2_stats["w"], v2_stats["l"])}')

    print('\n### v1 magnitude buckets ###')
    for m in sorted(v1_net_magnitude):
        print(f'  |v1|={m}  {bucket(v1_net_magnitude[m]["w"], v1_net_magnitude[m]["l"])}')

    print('\n### v2 magnitude buckets ###')
    for m in sorted(v2_net_magnitude):
        print(f'  |v2|={m}  {bucket(v2_net_magnitude[m]["w"], v2_net_magnitude[m]["l"])}')

    print('\n### Head-to-head ###')
    total_agree = agree_both_correct + agree_both_wrong
    total_disagree = disagree_v1_won + disagree_v2_won
    print(f'  agree both correct: {agree_both_correct}')
    print(f'  agree both wrong:   {agree_both_wrong}')
    print(f'  disagree v1 won:    {disagree_v1_won}')
    print(f'  disagree v2 won:    {disagree_v2_won}')
    if total_disagree:
        v1_disagree_rate = disagree_v1_won / total_disagree * 100
        v2_disagree_rate = disagree_v2_won / total_disagree * 100
        print(f'  In disagreements: v1 wins {v1_disagree_rate:.1f}% · v2 wins {v2_disagree_rate:.1f}%')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='Single date YYYY-MM-DD (defaults yesterday ET)')
    p.add_argument('--lookback', type=int, help='N-day aggregate window')
    args = p.parse_args()
    if args.lookback:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=args.lookback)
        run(start.isoformat(), end.isoformat())
    else:
        d = args.date or _et_yesterday()
        run(d, d)
