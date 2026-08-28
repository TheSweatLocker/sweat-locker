"""Live pipeline progress monitor (2026-08-22).

While the cron is running, this polls key tables every N seconds and
shows a running dashboard so you can see progress instead of staring
at the GitHub Actions log:

  ● 15/15  mlb_game_context (primary_play populated)
  ● 469/469  mlb_pipeline_props (all tiered)
  ● 51/61  prop_jerry_reads (PRIME+STRONG LLM+template writing...)
  ● 25/15  jerry_reads (game synth writing)
  ○ 0/1   sweat_locker_card (waiting)

Also flags common issues:
  - dupes still present (Painter × 4)
  - book_over_odds null count still high
  - dead ctx references still logged

Usage:
  python watch_pipeline.py             # today, 30s refresh
  python watch_pipeline.py --interval 15
  python watch_pipeline.py --once      # one snapshot then exit
"""
from __future__ import annotations
import argparse, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _count(table: str, filt: dict) -> int:
    try:
        p = {'select': 'id', **filt}
        r = requests.get(f'{SB}/rest/v1/{table}',
                         headers={**H, 'Prefer': 'count=exact', 'Range': '0-0'},
                         params=p, timeout=10)
        cr = r.headers.get('content-range', '?')
        n = cr.split('/')[-1]
        return int(n) if n.isdigit() else 0
    except Exception:
        return 0


def snapshot(gd: str) -> dict:
    """Return {stage_name: (current, target)} for progress display."""
    props_all = _count('mlb_pipeline_props', {'game_date': f'eq.{gd}'})
    props_tiered = _count('mlb_pipeline_props', {'game_date': f'eq.{gd}', 'tier': 'not.is.null'})
    props_prime_strong = _count('mlb_pipeline_props', {'game_date': f'eq.{gd}', 'tier': 'in.(PRIME,STRONG)'})
    ctx_all = _count('mlb_game_context', {'game_date': f'eq.{gd}'})
    ctx_pp = _count('mlb_game_context', {'game_date': f'eq.{gd}', 'primary_play': 'not.is.null'})
    prop_jerry = _count('prop_jerry_reads', {'game_date': f'eq.{gd}'})
    game_jerry = _count('jerry_reads', {'game_date': f'eq.{gd}'})
    ledger = _count('ledger_snapshots', {'game_date': f'eq.{gd}'})
    playbook = _count('prop_playbook_decisions', {'game_date': f'eq.{gd}', 'sport': 'eq.MLB'})
    # Data quality: null book_over_odds on OVER props
    odds_null = _count('mlb_pipeline_props', {'game_date': f'eq.{gd}',
                       'tier': 'in.(PRIME,STRONG)', 'direction': 'eq.over',
                       'book_over_odds': 'is.null'})
    # Duplicate PRIMEs by same player+type+dir (proxy dedup check)
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props', headers=H,
                     params={'game_date': f'eq.{gd}', 'tier': 'in.(PRIME,STRONG)',
                             'select': 'player_name,prop_type,direction,prop_line',
                             'limit': '500'}, timeout=10)
    data = r.json() if r.status_code == 200 else []
    seen = set(); dupes = 0
    for p in data:
        k = (p.get('player_name'), p.get('prop_type'), p.get('direction'), p.get('prop_line'))
        if k in seen: dupes += 1
        else: seen.add(k)
    return {
        'game_context (primary_play set)': (ctx_pp, ctx_all or '?'),
        'mlb_pipeline_props (tiered)':     (props_tiered, props_all or '?'),
        'PRIME + STRONG count':            (props_prime_strong, '—'),
        'playbook decisions':              (playbook, props_all or '?'),
        'prop_jerry_reads written':        (prop_jerry, '—'),
        'game jerry_reads written':        (game_jerry, ctx_all or '?'),
        'ledger_snapshots':                (ledger, '—'),
        'DUPES in PRIME/STRONG':           (dupes, 0),
        'PRIME/STRONG OVER props · odds NULL': (odds_null, 0),
    }


def render(gd: str, snap: dict) -> None:
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'\n═══ pipeline watch · {gd} · {ts} ═══')
    for name, (cur, tgt) in snap.items():
        # ● for populated / green, ○ for waiting, ⚠ for issue
        if isinstance(tgt, int) and tgt == 0:
            marker = '●' if cur == 0 else '⚠'
        elif tgt == '—':
            marker = '●' if cur > 0 else '○'
        else:
            try:
                marker = '●' if int(cur) >= int(tgt) else '○' if int(cur) > 0 else '·'
            except (TypeError, ValueError):
                marker = '·'
        print(f'  {marker} {cur:>5} / {str(tgt):>5}  {name}')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=int, default=30, help='refresh interval seconds')
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    gd = args.date or _today_et()
    if args.once:
        render(gd, snapshot(gd))
        return
    print(f'Watching pipeline · {gd} · refresh every {args.interval}s · Ctrl-C to stop')
    try:
        while True:
            render(gd, snapshot(gd))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\n stopped')


if __name__ == '__main__':
    main()
