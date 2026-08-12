"""Pipeline health dashboard reader (2026-08-12).

Reads pipeline_health_events and reports daily/weekly trend so you
can SEE whether the pipeline is stabilizing before flipping paid subs.

Launch-readiness thresholds (proposed):
  * 0 critical_block events for 14 consecutive days
  * auto_repair fires trending DOWN week-over-week
  * No new rule categories appearing (all rules are known/handled)

Usage:
    python report_pipeline_health.py [--days 30] [--sport MLB]
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


def run(days: int = 30, sport: str = 'MLB'):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = requests.get(f'{SB}/rest/v1/pipeline_health_events',
        headers=H_READ,
        params={'sport': f'eq.{sport}', 'event_date': f'gte.{since}',
                'order': 'event_date.desc', 'select': '*'},
        timeout=30).json()
    if not isinstance(rows, list):
        print(f'  fetch failed: {rows}'); return

    print(f'{"═"*80}')
    print(f'  PIPELINE HEALTH DASHBOARD · {sport} · last {days} days')
    print(f'{"═"*80}\n')

    # By date, by class
    by_date = defaultdict(lambda: Counter())
    by_rule = Counter()
    by_class_totals = Counter()
    for row in rows:
        d = row['event_date']
        cls = row['event_class']
        by_date[d][cls] += row['count']
        by_class_totals[cls] += row['count']
        if row.get('rule'):
            by_rule[row['rule']] += row['count']

    print(f'=== DAILY SUMMARY ===')
    print(f'{"date":12s} {"clean":>6s} {"auto_repair":>12s} {"critical":>9s} {"stat_flag":>10s}')
    days_sorted = sorted(by_date.keys(), reverse=True)
    green_streak = 0
    counted_streak = False
    for d in days_sorted[:days]:
        c = by_date[d]
        crit = c.get('critical_block', 0)
        clean = c.get('clean_run', 0)
        rep = c.get('auto_repair', 0)
        stat = c.get('stat_verifier_flag', 0)
        marker = '✅' if crit == 0 else '🚨'
        print(f'  {d} {clean:>6d} {rep:>12d} {crit:>9d} {stat:>10d}  {marker}')
        # Compute green streak from most-recent backward
        if not counted_streak:
            if crit == 0: green_streak += 1
            else: counted_streak = True

    print()
    print(f'=== TOTALS ({days}d) ===')
    for cls, n in by_class_totals.most_common():
        print(f'  {cls:20s}: {n}')

    print()
    print(f'=== TOP REPAIR RULES ({days}d) ===')
    for rule, n in by_rule.most_common(10):
        print(f'  {rule:35s}: {n} fires')

    print()
    print(f'=== LAUNCH-READINESS METRICS ===')
    total_days = len(days_sorted[:days])
    critical_days = sum(1 for d in days_sorted[:days] if by_date[d].get('critical_block', 0) > 0)
    critical_free_pct = 100 * (total_days - critical_days) / total_days if total_days else 0
    print(f'  Days scored: {total_days}')
    print(f'  Critical-free days: {total_days - critical_days} ({critical_free_pct:.0f}%)')
    print(f'  Current green streak: {green_streak} consecutive days')
    print()
    if green_streak >= 14:
        print(f'  ✅ LAUNCH-READY: 14-day green streak met')
    else:
        print(f'  ⏳ NOT READY: need {14 - green_streak} more consecutive clean days')

    # Trend: compare last 7d vs prior 7d
    if total_days >= 14:
        recent_repair = sum(by_date[d].get('auto_repair', 0) for d in days_sorted[:7])
        prior_repair = sum(by_date[d].get('auto_repair', 0) for d in days_sorted[7:14])
        recent_crit = sum(by_date[d].get('critical_block', 0) for d in days_sorted[:7])
        prior_crit = sum(by_date[d].get('critical_block', 0) for d in days_sorted[7:14])
        print()
        print(f'=== WEEK-OVER-WEEK ===')
        print(f'  auto_repair fires: prior 7d={prior_repair} · recent 7d={recent_repair} '
              f'({"↓ improving" if recent_repair < prior_repair else "↑ degrading" if recent_repair > prior_repair else "flat"})')
        print(f'  critical blocks: prior 7d={prior_crit} · recent 7d={recent_crit} '
              f'({"↓ improving" if recent_crit < prior_crit else "↑ degrading" if recent_crit > prior_crit else "flat"})')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=30)
    p.add_argument('--sport', default='MLB')
    args = p.parse_args()
    run(days=args.days, sport=args.sport)


if __name__ == '__main__':
    main()
