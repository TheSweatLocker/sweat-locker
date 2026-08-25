"""Daily sharp-pattern dashboard (2026-08-09).

Run against today's slate. For every unstarted game, show:
  - Sharp signals (ML + TOTAL divergence)
  - Which fade rules fire
  - Recommended cap (per current thresholds)
  - Sample stats behind each rule

Usage:
    python sharp_pattern_dashboard.py [--date YYYY-MM-DD]
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

sys.path.insert(0, str(Path(__file__).parent))
from sharp_fade_rules import compute_fade_context
from sharp_fade_flag import compute_sharp_fade_flag


def load_today(date: str):
    r = requests.get(f'{SB}/rest/v1/mlb_game_context', headers=H,
        params={'game_date': f'eq.{date}',
                'select': '*'}, timeout=15)
    return r.json() if isinstance(r.json(), list) else []


def load_jerry(date: str):
    r = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H,
        params={'game_date': f'eq.{date}', 'sport': 'eq.MLB',
                'select': 'game_id,call_market,call_side,call_line,conviction'}, timeout=15)
    return {j['game_id']: j for j in (r.json() if isinstance(r.json(), list) else [])}


def load_results(date: str):
    r = requests.get(f'{SB}/rest/v1/mlb_game_results', headers=H,
        params={'game_date': f'eq.{date}',
                'select': 'game_id,home_score,away_score'}, timeout=15)
    return {row['game_id']: (row.get('home_score') is not None)
            for row in (r.json() if isinstance(r.json(), list) else [])}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def analyze(date: str):
    games = load_today(date)
    jerry_map = load_jerry(date)
    result_map = load_results(date)
    print(f'\n{"="*100}')
    print(f'  SHARP PATTERN DASHBOARD  · {date}  · {len(games)} MLB games')
    print(f'{"="*100}')

    cards_capped = []
    for g in games:
        started = result_map.get(g['game_id'], False)
        status = '⏹ STARTED' if started else '⏳'
        matchup = f'{g["away_team"]} @ {g["home_team"]}'
        j = jerry_map.get(g['game_id'], {})
        j_call = f'{(j.get("call_market") or "-").upper()} {j.get("call_side") or "-"}'
        if j.get('call_line') is not None:
            j_call += f' {j.get("call_line")}'
        j_conv = j.get('conviction')

        # Skip games with no Jerry call
        if not j.get('call_market') or j.get('call_market') == 'pass':
            continue

        # Build sharp context
        oc = g.get('oddscrowd_snapshot')
        if isinstance(oc, str):
            try: oc = json.loads(oc)
            except: oc = None
        if not isinstance(oc, dict): oc = {}

        # Compute rule fires for Jerry's pick side
        pick_market = (j.get('call_market') or '').lower()
        pick_side = (j.get('call_side') or '').upper()
        if pick_market in ('ml', 'total') and pick_side:
            fade_ctx = compute_fade_context(g, pick_market, pick_side)
            bucket_flag = compute_sharp_fade_flag(oc, pick_market, pick_side)
        else:
            fade_ctx = {'triggers': [], 'active_count': 0, 'cap_directive': None}
            bucket_flag = None

        active_triggers = [t for t in fade_ctx['triggers'] if t.get('mode') == 'ACTIVE']
        log_triggers    = [t for t in fade_ctx['triggers'] if t.get('mode') == 'LOG']
        cap_msg = ''
        if fade_ctx['cap_directive'] == 'CAP_TO_READ_49':
            cap_msg = '🚨🚨 CAP-TO-READ (2+ ACTIVE rules)'
        elif fade_ctx['cap_directive'] == 'CAP_TO_LEAN_55':
            cap_msg = '🚨 CAP-TO-LEAN (1 ACTIVE rule)'
        elif bucket_flag and bucket_flag.get('cap_directive'):
            cap_msg = '🚨 CAP-TO-LEAN (bucket cap)'

        # Only print games with any sharp activity or triggers
        has_sharp_signal = any(oc.get(m) and oc[m].get('div') and abs(oc[m]['div']) >= 10 for m in ('ml','total'))
        if not (has_sharp_signal or active_triggers or log_triggers):
            continue

        print(f'\n{status}  {matchup}')
        print(f'   Jerry call: {j_call} (conv {j_conv})')
        for mkt in ('ml','total'):
            b = oc.get(mkt) or {}
            div = b.get('div')
            if div is not None and div != -1 and abs(div) >= 10:
                print(f'   Sharp {mkt.upper()}: {b.get("pick")} div={div:+d}pp (money {b.get("money")}% / bets {b.get("bets")}%)')
        if active_triggers or log_triggers:
            print(f'   Rules fired:')
            for t in active_triggers:
                print(f'     🚨 {t["rule"]}: {t["reason"][:110]}')
            for t in log_triggers:
                print(f'     📌 {t["rule"]} (log): {t["reason"][:100]}')
        if bucket_flag:
            fl = bucket_flag.get('flag','')
            if 'FADE' in fl or 'BOOST' in fl:
                tag = '🚨' if bucket_flag.get('cap_directive') else '📌'
                print(f'     {tag} bucket {bucket_flag["bucket"]}: {bucket_flag["reason"][:110]}')
        if cap_msg:
            print(f'   VERDICT: {cap_msg}')
            cards_capped.append(matchup)

    print(f'\n{"="*100}')
    print(f'  SUMMARY: {len(cards_capped)} games flagged for cap-to-LEAN based on sharp fade rules')
    for c in cards_capped:
        print(f'   → {c}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    date = args.date or today_et()
    analyze(date)


if __name__ == '__main__':
    main()
