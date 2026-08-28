"""Model degradation alarm (2026-08-07).

Nightly checker that alarms when any tracked model's recent hit rate
drops materially vs its longer-window baseline. Root cause of 8/7
audit finding: V4 (MODEL_SPREAD) degraded from 58.4% ATS at set-time
(2026-07-21) to 44.4% ML in last 14 days, dragging picks down at 0.3-0.5
weight in the composite spread formula. No alarm fired for 2+ weeks.

This module compares per-sport per-market per-model hit rates across
windows and prints an actionable alarm when:
  - lifetime → 90d drop of >= 5pp
  - 90d → 30d drop of >= 5pp
  - 30d → 14d drop of >= 5pp
  - any window hit rate <= 45% (structurally losing)

Runs as a cron job. Writes ALARMS to stdout for now (email/slack
integration deferred until we have a notification channel).

Sport-universal — reads model_track_records filtered by sport, cycles
through every (model, market) combo present.

Usage:
    python model_degradation_alarm.py [--sport MLB]
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


# Alarm thresholds
_DROP_PCT_ALARM = 5.0             # material drop between adjacent windows
_STRUCTURAL_LOSS_PCT = 45.0       # any window hit rate at or below = alarm
_MIN_N_FOR_ALARM = 25             # ignore small-sample noise

# Window ordering — least recent first for drop-detection
_WINDOWS = ['lifetime', '90d', '30d', '14d']


def load_tracker(sport: str) -> list:
    r = requests.get(
        f'{SB}/rest/v1/model_track_records',
        headers=H,
        params={
            'sport': f'eq.{sport}',
            'select': 'model_name,market,bucket_window,hit_rate,sample_n,roi_pct,computed_at',
            'limit': '500',
        }, timeout=20,
    )
    return r.json() if r.status_code == 200 else []


def analyze(rows: list) -> list:
    """Returns list of alarm dicts. Groups by (model, market),
    inspects window progression."""
    # Group by (model, market) → {window: latest row}
    groups = defaultdict(dict)
    for row in rows:
        m = row.get('model_name'); mkt = row.get('market'); w = row.get('bucket_window')
        if not m or not mkt or not w: continue
        # If multiple rows per (model, market, window), keep latest by computed_at
        existing = groups[(m, mkt)].get(w)
        if not existing or (row.get('computed_at') or '') > (existing.get('computed_at') or ''):
            groups[(m, mkt)][w] = row

    alarms = []
    for (model, market), by_window in groups.items():
        # Build sorted trace
        trace = []
        for w in _WINDOWS:
            r = by_window.get(w)
            if not r: continue
            hit = r.get('hit_rate')
            n = r.get('sample_n') or 0
            if hit is None or n < _MIN_N_FOR_ALARM: continue
            trace.append({'window': w, 'hit': float(hit), 'n': int(n),
                          'roi': r.get('roi_pct')})
        if len(trace) < 2: continue

        # Check adjacent-window drops
        for i in range(1, len(trace)):
            prev = trace[i-1]; curr = trace[i]
            drop = prev['hit'] - curr['hit']
            if drop >= _DROP_PCT_ALARM:
                alarms.append({
                    'severity': 'HIGH' if drop >= 10 else 'MED',
                    'model': model, 'market': market,
                    'kind': 'window_drop',
                    'from_window': prev['window'], 'from_hit': prev['hit'], 'from_n': prev['n'],
                    'to_window': curr['window'], 'to_hit': curr['hit'], 'to_n': curr['n'],
                    'drop_pct': round(drop, 1),
                    'roi_now': curr['roi'],
                    'note': (f'{model} {market} dropped {drop:.1f}pp: '
                             f'{prev["window"]} {prev["hit"]:.1f}% (n={prev["n"]}) → '
                             f'{curr["window"]} {curr["hit"]:.1f}% (n={curr["n"]})'),
                })

        # Check structural loss on most recent window
        latest = trace[-1]
        if latest['hit'] <= _STRUCTURAL_LOSS_PCT:
            alarms.append({
                'severity': 'HIGH',
                'model': model, 'market': market,
                'kind': 'structural_loss',
                'from_window': latest['window'], 'from_hit': latest['hit'], 'from_n': latest['n'],
                'roi_now': latest['roi'],
                'note': (f'{model} {market} STRUCTURAL LOSS: '
                         f'{latest["window"]} at {latest["hit"]:.1f}% '
                         f'(n={latest["n"]}, ROI {latest["roi"]:+.1f}%)'),
            })

    return alarms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB')
    args = ap.parse_args()

    print(f'=== model_degradation_alarm · {args.sport} ===')
    rows = load_tracker(args.sport)
    print(f'  loaded {len(rows)} tracker rows')
    alarms = analyze(rows)

    if not alarms:
        print()
        print('  ✅ No alarms — all tracked models within tolerance.')
        return

    print()
    print(f'🚨 {len(alarms)} ALARM(S)')
    # Sort HIGH before MED, then by drop magnitude
    def sort_key(a):
        sev_rank = 0 if a['severity'] == 'HIGH' else 1
        drop = a.get('drop_pct', 0)
        return (sev_rank, -drop)
    for a in sorted(alarms, key=sort_key):
        icon = '🔴' if a['severity'] == 'HIGH' else '🟡'
        print()
        print(f'  {icon} [{a["severity"]}] {a["kind"].upper()}')
        print(f'      {a["note"]}')
        if a.get('kind') == 'window_drop':
            print(f'      Current ROI: {a.get("roi_now"):+.1f}%')
    print()
    print('  Recommended action: investigate degrading models before next pipeline run.')
    print('  If confirmed degradation: reduce weight in composite formulas or gate the model.')


if __name__ == '__main__':
    main()
