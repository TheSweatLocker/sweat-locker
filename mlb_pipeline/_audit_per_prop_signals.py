"""Phase 1 — Per-prop signal predictiveness audit.

For each prop type, dump the signals that contribute to conviction and
measure each signal's actual hit-rate lift vs baseline.

User direction (2026-06-22):
  - Do all prop types regardless of current performance
  - Backtest everything before implementing
  - No hunches or guesses — only data

Output per prop_type:
  - Baseline hit rate
  - For each signal key:
    n_fires (count of graded picks where signal was present)
    win_rate_when_present
    lift vs baseline
    classification: STRONG_PRED / MILD / NOISE / FADE
"""
import os
import re
from collections import defaultdict
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

PROP_TYPES = [
    'ks_over', 'ks_under',
    'bb_over', 'bb_under',
    'ha_over', 'ha_under',
    'outs_over', 'outs_under',
    'er_over', 'er_under',
    'hits_over', 'hits_under',
]

# Min sample size to report a signal — below this, too noisy to trust
MIN_N = 15
# Lift thresholds for classification
LIFT_STRONG = 8.0     # +8pt lift = strong predictor
LIFT_MILD = 3.0       # +3-8pt = mild predictor
LIFT_FADE = -3.0      # <-3pt = fade signal (negative lift)


def fl(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def pull(prop_type, days=90):
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_pipeline_props?game_date=gte.{since}'
            f'&prop_type=eq.{prop_type}'
            f'&select=tier,conviction,result,signals,final_value,prop_line'
            f'&order=game_date.asc&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return [p for p in rows if isinstance(p, dict) and p.get('result') in ('Win', 'Loss')]


def signal_keys_for_row(row):
    """Extract the signal keys that fired for this prop pick.

    Signals dict contains both narrative strings (with descriptive keys like
    'xera_high', 'l3_bad') and computed values (keys starting with '_').
    A signal "fires" when its key is present in the dict (excluding internal
    underscored keys and the book-recalibration metadata)."""
    sigs = row.get('signals') or {}
    if not isinstance(sigs, dict):
        return set()
    skip_keys = {
        '_book_line', '_edge_at_book', '_projected_bb', '_projected_ks',
        '_projected_outs', '_projected_hits', '_projected_er',
        '_display_label', '_pre_recal_tier', '_recal_multiplier',
        '_pre_recal_conviction', '_internal_suggested_line',
        '_starter_pen_relievers_3d',
        'book_recalibration',
    }
    return {k for k in sigs.keys() if k not in skip_keys}


def classify(lift):
    if lift >= LIFT_STRONG: return 'STRONG_PRED'
    if lift >= LIFT_MILD: return 'MILD_PRED'
    if lift <= LIFT_FADE: return 'FADE'
    return 'NOISE'


def audit_prop(prop_type):
    rows = pull(prop_type, days=90)
    n_total = len(rows)
    if n_total < 30:
        print(f'\n{prop_type:>12s}: n={n_total} too thin')
        return None
    wins = sum(1 for r in rows if r['result'] == 'Win')
    baseline = 100 * wins / n_total

    # Tally per-signal hits
    sig_fires = defaultdict(lambda: [0, 0])  # signal_key → [wins, losses]
    for r in rows:
        won = r['result'] == 'Win'
        for sk in signal_keys_for_row(r):
            if won:
                sig_fires[sk][0] += 1
            else:
                sig_fires[sk][1] += 1

    # Compute lift per signal
    findings = []
    for sk, (w, l) in sig_fires.items():
        n = w + l
        if n < MIN_N:
            continue
        rate = 100 * w / n
        lift = rate - baseline
        cls = classify(lift)
        findings.append((sk, w, l, n, rate, lift, cls))
    findings.sort(key=lambda x: -x[5])  # by lift desc

    print(f'\n{"=" * 90}')
    print(f'{prop_type.upper()}  baseline={baseline:.1f}% (n={n_total})')
    print(f'{"=" * 90}')
    print(f'{"signal":>35s}  {"W-L":>10s}  {"rate":>6s}  {"lift":>7s}  class')
    print('-' * 90)
    for sk, w, l, n, rate, lift, cls in findings:
        marker = '🔥' if cls == 'STRONG_PRED' else (
            '✓' if cls == 'MILD_PRED' else ('🚨' if cls == 'FADE' else '·')
        )
        print(f'  {sk:>33s}  {w:>3}-{l:<3} (n={n:>3}) {rate:>5.1f}%  {lift:>+6.1f}pt  {cls:>11s} {marker}')

    return {
        'prop_type': prop_type,
        'baseline': baseline,
        'n_total': n_total,
        'findings': findings,
    }


def main():
    print('=' * 90)
    print('PER-PROP SIGNAL PREDICTIVENESS AUDIT (Phase 1)')
    print('90d window, MIN_N=15 per signal for reporting')
    print('=' * 90)

    summary = {}
    for pt in PROP_TYPES:
        result = audit_prop(pt)
        if result:
            summary[pt] = result

    # Final summary table
    print('\n\n' + '=' * 90)
    print('CROSS-PROP SUMMARY — signal counts by class')
    print('=' * 90)
    print(f'{"prop":>14s}  {"base":>6s}  {"total_n":>7s}  {"STRONG":>7s}  {"MILD":>5s}  {"NOISE":>6s}  {"FADE":>5s}')
    print('-' * 70)
    for pt, info in summary.items():
        cls_counts = defaultdict(int)
        for f in info['findings']:
            cls_counts[f[6]] += 1
        print(f'  {pt:>12s}  {info["baseline"]:>5.1f}%  {info["n_total"]:>7d}  '
              f'{cls_counts["STRONG_PRED"]:>7d}  {cls_counts["MILD_PRED"]:>5d}  '
              f'{cls_counts["NOISE"]:>6d}  {cls_counts["FADE"]:>5d}')


if __name__ == '__main__':
    main()
