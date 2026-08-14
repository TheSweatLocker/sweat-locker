"""Anomaly alert engine (2026-08-14).

Session A part 3. Reads hit_rate_snapshots + rule_fire_stats, applies
regression rules, writes alerts to dashboard_alerts.

Rules fire in order of severity. Each alert is written EXACTLY ONCE
per (category, sport, surface/rule, alert_date) so re-running the
engine same day doesn't double-post.

CATEGORIES + THRESHOLDS

  tier_hit_drop
    (sport, surface, tier) 7d hit_rate drops >= HIT_DROP_PP below 30d
    baseline, AND both windows have sample_n >= MIN_SAMPLE_FOR_DROP.
    severity: warn at 8pp, critical at 15pp.

  rule_hit_drop
    Rule at n >= 20 hits < RULE_HIT_LOW_PCT. Immediate warn/critical
    depending on hit rate.
    - critical: hit < 30% (rule inverted like FORCE_FADE_TRAP)
    - warn: hit < 45%

  sample_stall
    (sport, surface) had daily fires last week but 0 today. Signals
    a pipeline break (e.g., a script crashed silently).
    severity: warn.

  stale_snapshot
    snapshot_date is older than 24hrs — the nightly compute didn't run.
    severity: critical (monitoring itself is broken).

Idempotency: uses (alert_date, category, sport, COALESCE(surface,''),
COALESCE(rule_name,'')) as dedupe key. Silently skips if already
present.

CLI:
    python alert_dashboard_anomalies.py [--date YYYY-MM-DD]
                                         [--dry-run] [--severity-min info|warn|critical]
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Tunables — start conservative, tighten as we gather production experience
HIT_DROP_WARN_PP     = 8    # 7d hit rate 8pp below 30d = warn
HIT_DROP_CRIT_PP     = 15   # 15pp = critical
MIN_SAMPLE_FOR_DROP  = 15   # both 7d + 30d windows need this many graded
RULE_HIT_WARN_PCT    = 45
RULE_HIT_CRIT_PCT    = 30
RULE_MIN_SAMPLE      = 20
STALE_SNAPSHOT_HOURS = 30   # any snapshot > 30h old = compute broke


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


def load_snapshots_for_date(snapshot_date: date) -> list:
    return _paginate(f'{SB}/rest/v1/hit_rate_snapshots',
        {'snapshot_date': f'eq.{snapshot_date.isoformat()}', 'select': '*'})


def load_rule_fires_for_date(snapshot_date: date) -> list:
    return _paginate(f'{SB}/rest/v1/rule_fire_stats',
        {'snapshot_date': f'eq.{snapshot_date.isoformat()}', 'select': '*'})


def alert_exists(alert_date: date, category: str, sport: Optional[str],
                 surface: Optional[str], rule_name: Optional[str]) -> bool:
    params = {'alert_date': f'eq.{alert_date.isoformat()}',
              'category': f'eq.{category}',
              'select': 'id', 'limit': '1'}
    if sport: params['sport'] = f'eq.{sport}'
    else: params['sport'] = 'is.null'
    if surface: params['surface'] = f'eq.{surface}'
    else: params['surface'] = 'is.null'
    if rule_name: params['rule_name'] = f'eq.{rule_name}'
    else: params['rule_name'] = 'is.null'
    r = requests.get(f'{SB}/rest/v1/dashboard_alerts', headers=H_READ,
        params=params, timeout=10)
    if r.status_code != 200: return False
    return bool(r.json())


def write_alert(payload: dict, dry_run: bool = False) -> None:
    if dry_run:
        print(f'  [DRY] [{payload["severity"]}] {payload["category"]:16} '
              f'sport={payload.get("sport","-"):5} surface={payload.get("surface","-"):15} '
              f'rule={payload.get("rule_name","-"):25} — {payload["message"]}')
        return
    # Skip duplicate
    if alert_exists(date.fromisoformat(payload['alert_date']),
                    payload['category'], payload.get('sport'),
                    payload.get('surface'), payload.get('rule_name')):
        return
    pr = requests.post(f'{SB}/rest/v1/dashboard_alerts',
        headers=H_WRITE, json=payload, timeout=10)
    if pr.status_code not in (200, 201, 204):
        print(f'  ✗ alert write failed: {pr.status_code} {pr.text[:150]}')


def check_tier_hit_drops(snapshots: list, snapshot_date: date, dry_run: bool):
    """Compare 7d vs 30d for each (sport, surface, tier). Flag drops."""
    # Bucket snapshots by (sport, surface, tier) → {window: (hit, n)}
    buckets = defaultdict(dict)
    for s in snapshots:
        key = (s.get('sport'), s.get('surface'), s.get('tier'))
        buckets[key][s.get('window_days')] = (s.get('hit_rate'), s.get('sample_n'))

    for (sport, surface, tier), windows in buckets.items():
        w7 = windows.get(7);  w30 = windows.get(30)
        if not (w7 and w30): continue
        h7, n7 = w7; h30, n30 = w30
        if h7 is None or h30 is None: continue
        if n7 < MIN_SAMPLE_FOR_DROP or n30 < MIN_SAMPLE_FOR_DROP: continue
        drop = float(h30) - float(h7)
        if drop < HIT_DROP_WARN_PP: continue
        severity = 'critical' if drop >= HIT_DROP_CRIT_PP else 'warn'
        tier_str = tier or 'ALL'
        msg = (f'{sport} {surface} tier={tier_str}: 7d hit {h7}% (n={n7}) '
               f'vs 30d {h30}% (n={n30}) — down {drop:.1f}pp')
        write_alert({
            'alert_date': snapshot_date.isoformat(),
            'severity': severity, 'category': 'tier_hit_drop',
            'sport': sport, 'surface': surface, 'tier': tier,
            'message': msg,
            'metric_current': float(h7), 'metric_baseline': float(h30),
            'metric_delta': -drop,
            'detail': {'n_7d': n7, 'n_30d': n30},
        }, dry_run=dry_run)


def check_rule_hit_drops(rule_stats: list, snapshot_date: date, dry_run: bool):
    """Flag rules at n>=20 with hit < 45%. Critical below 30%."""
    for rs in rule_stats:
        if rs.get('window_days') != 30: continue  # focus on 30d for stability
        n = rs.get('sample_n') or 0
        h = rs.get('hit_rate')
        if h is None or n < RULE_MIN_SAMPLE: continue
        if h >= RULE_HIT_WARN_PCT: continue
        severity = 'critical' if h < RULE_HIT_CRIT_PCT else 'warn'
        msg = (f'{rs["sport"]} rule {rs["rule_name"]} 30d hit rate '
               f'{h}% (n={n}) — below {RULE_HIT_WARN_PCT}% threshold. '
               f'Consider disabling like FORCE_FADE_TRAP (70b3c793).')
        write_alert({
            'alert_date': snapshot_date.isoformat(),
            'severity': severity, 'category': 'rule_hit_drop',
            'sport': rs['sport'], 'rule_name': rs['rule_name'],
            'message': msg,
            'metric_current': float(h), 'metric_baseline': RULE_HIT_WARN_PCT,
            'metric_delta': float(h) - RULE_HIT_WARN_PCT,
            'detail': {'sample_n': n, 'fires': rs.get('fires'),
                       'rule_class': rs.get('rule_class')},
        }, dry_run=dry_run)


def check_stale_snapshot(snapshots: list, snapshot_date: date, dry_run: bool):
    """If today's snapshot exists but yesterday's didn't, compute broke somewhere."""
    if not snapshots:
        write_alert({
            'alert_date': snapshot_date.isoformat(),
            'severity': 'critical', 'category': 'stale_snapshot',
            'message': (f'No hit_rate_snapshots for {snapshot_date} — '
                        f'nightly compute may have failed. Monitoring is DARK.'),
            'metric_current': 0, 'metric_baseline': 100,
            'metric_delta': -100,
        }, dry_run=dry_run)
        return
    # Check freshness of computed_at
    latest_computed = max(s.get('computed_at') or '' for s in snapshots)
    try:
        latest_dt = datetime.fromisoformat(latest_computed.replace('Z', '+00:00'))
        age_hours = (datetime.now(timezone.utc) - latest_dt).total_seconds() / 3600
        if age_hours > STALE_SNAPSHOT_HOURS:
            write_alert({
                'alert_date': snapshot_date.isoformat(),
                'severity': 'critical', 'category': 'stale_snapshot',
                'message': (f'Snapshots for {snapshot_date} are {age_hours:.1f}h old '
                            f'— compute pipeline broke.'),
                'metric_current': age_hours, 'metric_baseline': STALE_SNAPSHOT_HOURS,
                'metric_delta': age_hours - STALE_SNAPSHOT_HOURS,
            }, dry_run=dry_run)
    except Exception: pass


def check_sample_stall(snapshots: list, snapshot_date: date, dry_run: bool):
    """(sport, surface) had 30d sample > 20 but 7d sample = 0.
    Signals pipeline stopped producing picks in that surface."""
    by_ss = defaultdict(dict)
    for s in snapshots:
        if s.get('tier'): continue  # aggregate rows only
        by_ss[(s.get('sport'), s.get('surface'))][s.get('window_days')] = s.get('sample_n', 0)
    for (sport, surface), windows in by_ss.items():
        n7 = windows.get(7, 0); n30 = windows.get(30, 0)
        if n30 >= 20 and n7 == 0:
            write_alert({
                'alert_date': snapshot_date.isoformat(),
                'severity': 'warn', 'category': 'sample_stall',
                'sport': sport, 'surface': surface,
                'message': (f'{sport} {surface}: 30d sample n={n30} but 7d n=0 — '
                            f'pipeline may have stopped writing picks.'),
                'metric_current': 0, 'metric_baseline': n30,
                'metric_delta': -n30,
            }, dry_run=dry_run)


def run(snapshot_date: date, dry_run: bool = False):
    print(f'=== dashboard_alerts · {snapshot_date} ===')
    snapshots = load_snapshots_for_date(snapshot_date)
    rule_stats = load_rule_fires_for_date(snapshot_date)
    print(f'  loaded {len(snapshots)} snapshots · {len(rule_stats)} rule-fire rows')

    check_stale_snapshot(snapshots, snapshot_date, dry_run)
    check_tier_hit_drops(snapshots, snapshot_date, dry_run)
    check_rule_hit_drops(rule_stats, snapshot_date, dry_run)
    check_sample_stall(snapshots, snapshot_date, dry_run)
    print(f'  {"[DRY] " if dry_run else ""}alerts pass complete')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='snapshot date; defaults today ET')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sd = date.fromisoformat(args.date) if args.date else _et_today()
    run(sd, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
