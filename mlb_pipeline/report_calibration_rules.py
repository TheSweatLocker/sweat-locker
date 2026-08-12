"""Weekly calibration rule report (2026-08-12).

Reads conviction_calibration_events + joins to jerry_reads / prop_jerry_reads
results to compute hit rate per RULE. Answers: "is each calibration rule
actually helping?"

The problem this solves: rules like MULTI_SIGNAL_PROMO (force conv 78 when
confluence+MC+refit align) or HOLE_60_64_CAP (route 60-64 UNDERs to 55) are
theory-driven. Without tracking, we can't measure whether they hit at the
promised rate. This aggregates every rule application with graded outcomes
so we can:
  * Keep rules that hit at expected rate
  * Tune rules that underperform (raise threshold, add tie-breakers)
  * Kill rules that consistently lose

Backfills: also updates hit + resolved_at on events by cross-referencing
the source row's grade. Runs safely on every invocation — idempotent.

Usage:
    python report_calibration_rules.py [--days 30]

Suggest running weekly (Sunday cron) after nightly resolver completes.
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _backfill_event_grades(events: list) -> int:
    """For events without hit resolved yet, look up the source row's result
    and stamp hit=True/False + resolved_at on the event."""
    updated = 0
    # Group events by source_table for batch lookups
    by_table = defaultdict(list)
    for e in events:
        if e.get('hit') is None and e.get('source_id'):
            by_table[e['source_table']].append(e)

    for tbl, evs in by_table.items():
        ids = [str(e['source_id']) for e in evs]
        # Batch fetch source rows in chunks of 100
        result_by_id = {}
        for i in range(0, len(ids), 100):
            chunk = ids[i:i+100]
            r = requests.get(f'{SB}/rest/v1/{tbl}',
                params={'id': f'in.({",".join(chunk)})',
                        'select': 'id,result'},
                headers=H_READ, timeout=15).json()
            for row in (r if isinstance(r, list) else []):
                result_by_id[row['id']] = row.get('result')

        for e in evs:
            source_result = result_by_id.get(e['source_id'])
            if source_result in ('Win', 'Loss'):
                hit = source_result == 'Win'
                requests.patch(f'{SB}/rest/v1/conviction_calibration_events'
                               f'?id=eq.{e["id"]}',
                               headers=H_WRITE,
                               json={'hit': hit,
                                     'resolved_at': datetime.now(timezone.utc).isoformat()},
                               timeout=10)
                updated += 1
    return updated


def run(days: int = 30) -> None:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    events = requests.get(f'{SB}/rest/v1/conviction_calibration_events',
        params={'game_date': f'gte.{since}', 'select': '*',
                'order': 'game_date.desc'},
        headers=H_READ, timeout=30).json()
    if not isinstance(events, list):
        print(f'  fetch failed: {events}'); return
    print(f'=== conviction_calibration_events ({days}d) · n={len(events)} ===\n')

    # Backfill grades for unresolved events
    backfilled = _backfill_event_grades(events)
    if backfilled:
        print(f'  backfilled {backfilled} event grades from source rows\n')
        # Re-fetch after backfill
        events = requests.get(f'{SB}/rest/v1/conviction_calibration_events',
            params={'game_date': f'gte.{since}', 'select': '*',
                    'order': 'game_date.desc'},
            headers=H_READ, timeout=30).json()

    # Aggregate by rule
    by_rule = defaultdict(lambda: {'total': 0, 'graded': 0, 'wins': 0, 'losses': 0,
                                     'pending': 0})
    for e in events:
        r = e.get('rule') or 'UNKNOWN'
        by_rule[r]['total'] += 1
        hit = e.get('hit')
        if hit is True: by_rule[r]['wins'] += 1; by_rule[r]['graded'] += 1
        elif hit is False: by_rule[r]['losses'] += 1; by_rule[r]['graded'] += 1
        else: by_rule[r]['pending'] += 1

    print(f'{"rule":32s} {"total":>6s} {"graded":>7s} {"W-L":>8s} {"hit%":>7s} {"verdict":>18s}')
    for rule in sorted(by_rule.keys()):
        d = by_rule[rule]
        n = d['graded']
        hit_pct = 100 * d['wins'] / n if n else 0
        # Verdict on the rule
        if n < 10:
            verdict = 'SMALL (n<10)'
        elif hit_pct >= 60:
            verdict = '✅ WORKING'
        elif hit_pct >= 52.4:
            verdict = '✓ profitable'
        elif hit_pct >= 48:
            verdict = '⚠️ break-even'
        else:
            verdict = '❌ LOSING (tune/kill)'
        print(f'  {rule:30s} {d["total"]:>6d} {n:>7d} {d["wins"]:>3d}-{d["losses"]:>3d} '
              f'{hit_pct:>6.1f}% {verdict:>18s}')

    print(f'\n=== Rule audit complete ===')
    print(f'Sub-52.4% rules should be tuned (raise thresholds, add tie-breakers) or killed.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=30)
    args = p.parse_args()
    run(days=args.days)


if __name__ == '__main__':
    main()
