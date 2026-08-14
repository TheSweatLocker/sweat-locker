"""Dashboard CLI reporter (2026-08-14).

Session A part 4. Reads hit_rate_snapshots + rule_fire_stats + dashboard_alerts,
renders a scannable dashboard so the operator can eyeball state in <30 sec.

Sections (top to bottom):
    1. UNACKNOWLEDGED ALERTS  — must-look, ordered critical → warn → info
    2. HEADLINE HIT RATES     — per-sport 7d/30d/90d aggregate
    3. TIER RATES             — PRIME / STRONG breakdown per sport
    4. RULE-FIRE HOTLIST      — bottom-5 rules by hit rate at n>=15
    5. SILENT-STALL WATCH     — surfaces with sample_stall pattern

The report is READ-ONLY. Every write happens in the compute or alert
scripts. This just presents.

CLI:
    python report_dashboard.py [--date YYYY-MM-DD] [--sport MLB|ALL]
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
from typing import Optional

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


def _et_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _paginate(url: str, params: dict, page: int = 1000) -> list:
    out = []; offset = 0
    while offset < 5000:
        p = {**params, 'limit': str(page), 'offset': str(offset)}
        r = requests.get(url, headers=H_READ, params=p, timeout=30)
        if r.status_code != 200: return out
        rows = r.json()
        if not isinstance(rows, list) or not rows: break
        out.extend(rows)
        if len(rows) < page: break
        offset += page
    return out


def _sev_color(sev: str) -> str:
    return {'critical': '🚨', 'warn': '⚠️ ', 'info': 'ℹ️ '}.get(sev, '  ')


def section_alerts(snapshot_date: date):
    r = _paginate(f'{SB}/rest/v1/dashboard_alerts',
        {'acknowledged': 'eq.false', 'select': '*',
         'order': 'severity.asc,alert_date.desc,created_at.desc'})
    print('═' * 80)
    print(f'  🚨 UNACKNOWLEDGED ALERTS ({len(r)})')
    print('═' * 80)
    if not r:
        print('  ✓ no unacknowledged alerts')
        return
    # Sort: critical > warn > info, then most recent first
    priority = {'critical': 0, 'warn': 1, 'info': 2}
    r.sort(key=lambda a: (priority.get(a.get('severity','info'), 9),
                          a.get('created_at') or ''),
           reverse=False)
    for a in r[:15]:
        sev = a.get('severity', 'info')
        cat = a.get('category', '?')
        msg = (a.get('message') or '')[:120]
        print(f'  {_sev_color(sev)} [{cat:16}] {msg}')
    if len(r) > 15:
        print(f'\n  ... and {len(r)-15} more (mark acknowledged in dashboard_alerts)')


def section_headline_hit_rates(snapshot_date: date, sports: list):
    r = _paginate(f'{SB}/rest/v1/hit_rate_snapshots',
        {'snapshot_date': f'eq.{snapshot_date.isoformat()}',
         'tier': 'is.null',
         'select': 'sport,surface,window_days,wins,losses,hit_rate,sample_n'})
    print()
    print('═' * 80)
    print(f'  📊 HEADLINE HIT RATES · {snapshot_date}')
    print('═' * 80)
    if not r:
        print('  no snapshots for this date — run compute_hit_rate_dashboard.py first')
        return
    # Bucket per (sport, surface) → {window: (hit, n, w, l)}
    b = defaultdict(dict)
    for row in r:
        b[(row['sport'], row['surface'])][row['window_days']] = row
    # Print in sport order
    for sport in sports:
        printed_header = False
        for (s, surface), windows in sorted(b.items()):
            if s != sport: continue
            if not printed_header:
                print(f'\n  ▸ {sport}')
                print(f'    {"surface":18} {"7d":>17} {"30d":>17} {"90d":>17}')
                printed_header = True
            def _fmt(w):
                if w is None: return '       —        '
                h = w.get('hit_rate'); n = w.get('sample_n', 0)
                if h is None: return f'  ({n:3} no grades)'
                marker = '🔥' if h >= 60 else '✓' if h >= 52 else '⚠️' if h >= 45 else '🚨'
                return f' {h:5.1f}% ({w.get("wins",0):2}-{w.get("losses",0):2}) {marker}'
            r7 = windows.get(7); r30 = windows.get(30); r90 = windows.get(90)
            print(f'    {surface:18} {_fmt(r7)} {_fmt(r30)} {_fmt(r90)}')


def section_tier_rates(snapshot_date: date, sports: list):
    r = _paginate(f'{SB}/rest/v1/hit_rate_snapshots',
        {'snapshot_date': f'eq.{snapshot_date.isoformat()}',
         'window_days': 'eq.30',
         'tier': 'in.(PRIME,STRONG,LEAN)',
         'select': 'sport,surface,tier,hit_rate,sample_n,wins,losses'})
    print()
    print('═' * 80)
    print(f'  🎯 TIER HIT RATES · 30-day')
    print('═' * 80)
    if not r:
        print('  no tier snapshots'); return
    # (sport, surface) → {tier: row}
    b = defaultdict(dict)
    for row in r:
        b[(row['sport'], row['surface'])][row['tier']] = row
    for sport in sports:
        printed = False
        for (s, surface), tiers in sorted(b.items()):
            if s != sport: continue
            if not printed:
                print(f'\n  ▸ {sport}')
                printed = True
            for tier_name in ('PRIME', 'STRONG', 'LEAN'):
                t = tiers.get(tier_name)
                if not t: continue
                h = t.get('hit_rate'); n = t.get('sample_n', 0)
                if h is None:
                    line = f'    {surface:18} {tier_name:7}  (no grades, n={n})'
                else:
                    marker = '🔥' if h >= 60 else '✓' if h >= 52 else '⚠️' if h >= 45 else '🚨'
                    line = (f'    {surface:18} {tier_name:7}  {h:5.1f}% '
                            f'({t.get("wins",0)}-{t.get("losses",0)}) n={n} {marker}')
                print(line)


def section_rule_hotlist(snapshot_date: date):
    r = _paginate(f'{SB}/rest/v1/rule_fire_stats',
        {'snapshot_date': f'eq.{snapshot_date.isoformat()}',
         'window_days': 'eq.30',
         'sample_n': 'gte.15',
         'order': 'hit_rate.asc',
         'select': 'sport,rule_name,rule_class,fires,wins_when_fired,losses_when_fired,hit_rate,sample_n'})
    print()
    print('═' * 80)
    print(f'  🔴 RULE-FIRE HOTLIST · 30d · bottom-8 by hit rate (n>=15)')
    print('═' * 80)
    if not r:
        print('  no rule-fire stats — run compute_rule_fire_stats.py'); return
    for row in r[:8]:
        h = row.get('hit_rate')
        marker = '🚨' if h and h < 30 else '⚠️' if h and h < 45 else '✓' if h and h >= 55 else '  '
        print(f'  {marker} {row["sport"]:5} {row["rule_class"]:22} '
              f'{row["rule_name"]:32} '
              f'{row["wins_when_fired"]:3}-{row["losses_when_fired"]:3} '
              f'({h if h is not None else "—"}%) fires={row["fires"]}')


def section_data_quality(snapshot_date: date):
    """Recent data-quality event summary (Session B)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    r = _paginate(f'{SB}/rest/v1/data_quality_events',
        {'event_ts': f'gte.{since}', 'select': 'source,check_name,severity',
         'order': 'event_ts.desc'})
    print()
    print('═' * 80)
    print(f'  🔬 DATA QUALITY — trips last 24h')
    print('═' * 80)
    if not r:
        print('  ✓ no data-quality events logged (or check_data_quality_daily.py hasn\'t run)')
        return
    # Bucket by (source, check_name) → count per severity
    b = defaultdict(lambda: {'critical': 0, 'warn': 0, 'info': 0})
    for row in r:
        key = (row.get('source'), row.get('check_name'))
        sev = row.get('severity', 'info')
        b[key][sev] += 1
    # Sort: critical count desc, then warn count desc
    entries = sorted(b.items(), key=lambda z: (-z[1]['critical'], -z[1]['warn']))
    for (source, check), counts in entries[:10]:
        c = counts['critical']; w = counts['warn']; i = counts['info']
        marker = '🚨' if c else '⚠️' if w >= 3 else '  '
        print(f'  {marker} {(source or "-")[:32]:32} · {(check or "-")[:35]:35} '
              f'· crit={c} warn={w} info={i}')


def section_stall_watch(snapshot_date: date):
    r = _paginate(f'{SB}/rest/v1/hit_rate_snapshots',
        {'snapshot_date': f'eq.{snapshot_date.isoformat()}',
         'window_days': 'eq.7', 'tier': 'is.null',
         'sample_n': 'eq.0', 'select': 'sport,surface,sample_n'})
    print()
    print('═' * 80)
    print(f'  💀 SILENT-STALL WATCH · surfaces with 0 fires last 7 days')
    print('═' * 80)
    if not r:
        print('  ✓ no stalled surfaces')
        return
    for row in r:
        print(f'  💀 {row["sport"]:5} {row["surface"]:18} — no picks in 7d')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='snapshot date; defaults today ET')
    p.add_argument('--sport', default='ALL', help='MLB/NFL/... or ALL')
    args = p.parse_args()
    sd = date.fromisoformat(args.date) if args.date else _et_today()
    sports = (['MLB','NFL','NCAAF','NCAAB','NBA','NHL','UFC']
              if args.sport == 'ALL' else [args.sport])
    print(f'\n  Sweat Locker · Monitoring Dashboard · {sd}\n')
    section_alerts(sd)
    section_headline_hit_rates(sd, sports)
    section_tier_rates(sd, sports)
    section_rule_hotlist(sd)
    section_data_quality(sd)
    section_stall_watch(sd)
    print()


if __name__ == '__main__':
    main()
