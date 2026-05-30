"""Hits-Under cohort audit — last 30 days of resolved hits_under props.

Reports:
- Overall hit rate by tier
- Single-signal hit rate (when this signal is present)
- Signal-absence hit rate (control)
- Two-signal combinations with edge or anti-edge
- Tonight's plays scored against the signal table

Per project_may17_hits_under_audit: PRIME tier audited at 56.8% (under-
performs STRONG 64.6%), so the PRIME gate isn't gating cleanly. The
single-signal view tells us which gating predicates are pulling weight
and which are dead weight.

Run: python _audit_hits_under_cohort.py [days=30]
"""
import os, sys, io, json, urllib.request
from collections import defaultdict, Counter
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
URL = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def get(p):
    with urllib.request.urlopen(urllib.request.Request(URL + p, headers=H), timeout=30) as r:
        return json.loads(r.read())


def pct(w, l):
    n = w + l
    return f'{w}-{l} {w/n*100:5.1f}% (n={n:3d})' if n else '0-0'


def main(days=30):
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()
    print(f'Audit window: {start} -> {end} ({days} days)')
    print()

    # Pull all hits_under props in window, resolved + unresolved
    rows = get(
        f'/rest/v1/mlb_pipeline_props?game_date=gte.{start}&game_date=lt.{end}'
        f'&prop_type=eq.hits_under&select=player_name,prop_type,tier,conviction,'
        f'result,final_value,signals,player_team,matchup,game_date'
    )
    resolved = [p for p in rows if p.get('result') in ('Win', 'Loss')]
    unresolved = [p for p in rows if p.get('result') not in ('Win', 'Loss')]
    print(f'hits_under props pulled: {len(rows)}')
    print(f'  resolved: {len(resolved)}, unresolved: {len(unresolved)}')
    print()

    # By tier
    print('--- BY TIER ---')
    for tier in ('PRIME', 'STRONG', 'LEAN'):
        f = [p for p in resolved if p.get('tier') == tier]
        w = sum(1 for p in f if p['result'] == 'Win')
        l = sum(1 for p in f if p['result'] == 'Loss')
        print(f'  {tier:7s}: {pct(w, l)}')
    print()

    # Single-signal hit rate: for each signal key, compute win rate of props
    # where that signal is present
    signal_stats = defaultdict(lambda: {'wins': 0, 'losses': 0})
    signal_absent_stats = defaultdict(lambda: {'wins': 0, 'losses': 0})
    all_signal_keys = set()
    for p in resolved:
        sigs = p.get('signals') or {}
        if isinstance(sigs, dict):
            for k in sigs.keys():
                # Skip internal/trace keys
                if k.startswith('_') or k in ('book_recalibration', '_display_label'):
                    continue
                all_signal_keys.add(k)
    # Now classify each prop per signal
    for sig_key in all_signal_keys:
        for p in resolved:
            sigs = p.get('signals') or {}
            has_sig = isinstance(sigs, dict) and sig_key in sigs
            bucket = signal_stats if has_sig else signal_absent_stats
            if p['result'] == 'Win':
                bucket[sig_key]['wins'] += 1
            else:
                bucket[sig_key]['losses'] += 1

    # Filter to signals with n>=10 when present, sort by hit rate
    present_rows = []
    for sig in all_signal_keys:
        s = signal_stats[sig]
        n = s['wins'] + s['losses']
        if n >= 10:
            rate = s['wins'] / n
            present_rows.append((sig, s['wins'], s['losses'], n, rate))
    present_rows.sort(key=lambda x: -x[4])

    print('--- SINGLE-SIGNAL HIT RATES (n>=10, when signal is PRESENT) ---')
    for sig, w, l, n, rate in present_rows:
        flag = '✅' if rate >= 0.55 else ('⚠️' if rate >= 0.45 else '❌')
        # Also show absent rate for comparison
        a = signal_absent_stats[sig]
        an = a['wins'] + a['losses']
        ar = (a['wins'] / an * 100) if an else 0
        delta = (rate - (ar / 100)) * 100 if an else 0
        print(f'  {flag} {sig:32s}  {w:3d}-{l:3d}  {rate*100:5.1f}%  (n={n:3d})  '
              f'| absent: {ar:5.1f}% n={an:3d}  | delta {delta:+5.1f}pt')
    print()

    # Two-signal co-occurrence (top 10 most common pairs)
    print('--- TWO-SIGNAL CO-OCCURRENCE (top pairs by n, n>=10) ---')
    pair_stats = defaultdict(lambda: {'wins': 0, 'losses': 0})
    for p in resolved:
        sigs = p.get('signals') or {}
        if not isinstance(sigs, dict):
            continue
        keys = sorted(k for k in sigs.keys() if not k.startswith('_')
                      and k not in ('book_recalibration', '_display_label'))
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                pair = (keys[i], keys[j])
                if p['result'] == 'Win':
                    pair_stats[pair]['wins'] += 1
                else:
                    pair_stats[pair]['losses'] += 1
    pair_rows = []
    for pair, s in pair_stats.items():
        n = s['wins'] + s['losses']
        if n >= 10:
            pair_rows.append((pair, s['wins'], s['losses'], n, s['wins'] / n))
    # Show top by hit rate (best + worst)
    pair_rows.sort(key=lambda x: -x[4])
    print('  TOP combinations:')
    for pair, w, l, n, rate in pair_rows[:8]:
        print(f"    ✅ {pair[0][:22]:22s} + {pair[1][:22]:22s}  {w:3d}-{l:3d}  {rate*100:5.1f}%  (n={n:3d})")
    print('  WORST combinations:')
    for pair, w, l, n, rate in pair_rows[-8:]:
        print(f"    ❌ {pair[0][:22]:22s} + {pair[1][:22]:22s}  {w:3d}-{l:3d}  {rate*100:5.1f}%  (n={n:3d})")
    print()

    # Tonight's hits_under plays scored against the signal table
    today_props = get(
        f'/rest/v1/mlb_pipeline_props?game_date=eq.{today.isoformat()}'
        f'&prop_type=eq.hits_under&tier=in.(PRIME,STRONG)&select=*'
    )
    if today_props:
        print(f"--- TONIGHT'S hits_under PRIME/STRONG plays scored against signals ---")
        # Build a sig→rate lookup
        sig_rate = {sig: rate for sig, w, l, n, rate in present_rows}
        for p in today_props:
            sigs = p.get('signals') or {}
            if not isinstance(sigs, dict):
                continue
            print(f"\n  {p.get('player_name')} ({p.get('tier')} {p.get('conviction')})  vs  {p.get('matchup')}")
            relevant_sigs = [k for k in sigs.keys() if not k.startswith('_')
                             and k not in ('book_recalibration', '_display_label')
                             and k in sig_rate]
            for sig in relevant_sigs:
                rate = sig_rate.get(sig, 0)
                flag = '✅' if rate >= 0.55 else ('⚠️' if rate >= 0.45 else '❌')
                print(f"      {flag} {sig:32s}  audits {rate*100:5.1f}%")


if __name__ == '__main__':
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    main(days)
