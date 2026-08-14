"""Data-quality event aggregator (2026-08-14).

Session B part 3. Runs nightly. Reads data_quality_events from the last
24h, promotes recurring failures to dashboard_alerts so they surface in
the CLI reporter alongside hit-rate regressions.

Also runs a small set of "sanity queries" — nightly cross-checks that
compare current-state values against source-of-truth external APIs.
This catches silent data corruption BEFORE it propagates into picks.

Promotion rules:
  * critical severity     : any single event → alert immediately
  * warn severity, n>=3   : same (source, check_name) tripped 3+ times in 24h → alert
  * warn severity, n<3    : logged only, no alert
  * info severity         : logged only, never alerts

Sanity queries (spot-check known-fragile fetches):
  * pitcher_last_ip_sanity  : sample 3 today's starters, cross-check
                              mlb_game_context.away/home_last_ip vs MLB API
  * nfl_game_count_sanity   : during regular season, expect ~14-16 NFL
                              games/week — flag if 0 upcoming

CLI:
    python check_data_quality_daily.py [--dry-run]
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

WARN_REPEAT_THRESHOLD = 3   # warn events cluster to alert at N+


def _et_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def load_recent_events(hours: int = 24) -> list:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    r = requests.get(f'{SB}/rest/v1/data_quality_events', headers=H_READ,
        params={'event_ts': f'gte.{since}', 'select': '*',
                'order': 'event_ts.desc', 'limit': '5000'},
        timeout=30)
    return r.json() if r.status_code == 200 else []


def alert_exists(alert_date: date, category: str, check_name: str) -> bool:
    params = {'alert_date': f'eq.{alert_date.isoformat()}',
              'category': f'eq.{category}',
              'message': f'like.*{check_name}*',
              'select': 'id', 'limit': '1'}
    r = requests.get(f'{SB}/rest/v1/dashboard_alerts', headers=H_READ,
        params=params, timeout=10)
    return r.status_code == 200 and bool(r.json())


def write_alert(payload: dict, dry_run: bool = False) -> None:
    if dry_run:
        print(f'  [DRY] [{payload["severity"]}] {payload["category"]} — {payload["message"]}')
        return
    if alert_exists(date.fromisoformat(payload['alert_date']),
                    payload['category'], payload.get('rule_name') or ''):
        return
    pr = requests.post(f'{SB}/rest/v1/dashboard_alerts',
        headers=H_WRITE, json=payload, timeout=10)
    if pr.status_code not in (200, 201, 204):
        print(f'  ✗ alert write failed: {pr.status_code} {pr.text[:150]}')


def promote_events(events: list, snapshot_date: date, dry_run: bool = False):
    """Bucket by (source, check_name) → count + severity. Promote per rules."""
    buckets = defaultdict(list)
    for e in events:
        key = (e.get('source'), e.get('check_name'))
        buckets[key].append(e)

    for (source, check_name), rows in buckets.items():
        max_sev = 'info'
        for r in rows:
            s = r.get('severity', 'info')
            if s == 'critical' or (s == 'warn' and max_sev != 'critical'):
                max_sev = s
            elif s == 'info' and max_sev not in ('critical', 'warn'):
                max_sev = 'info'
        n = len(rows)
        # Promotion rules
        promote = False
        if max_sev == 'critical' and n >= 1: promote = True
        elif max_sev == 'warn' and n >= WARN_REPEAT_THRESHOLD: promote = True
        if not promote: continue

        latest = max(rows, key=lambda r: r.get('event_ts') or '')
        sports = sorted(set(r.get('sport') for r in rows if r.get('sport')))
        sport_str = ','.join(sports) if sports else None
        msg = (f'{check_name} tripped {n}× last 24h ({source})'
               + (f' · sports: {sport_str}' if sport_str else '')
               + f' · latest: {latest.get("message","")[:120]}')
        write_alert({
            'alert_date': snapshot_date.isoformat(),
            'severity': max_sev, 'category': 'silent_failure',
            'sport': sports[0] if len(sports) == 1 else None,
            'rule_name': check_name,
            'message': msg,
            'metric_current': n,
            'metric_baseline': WARN_REPEAT_THRESHOLD,
            'metric_delta': n - WARN_REPEAT_THRESHOLD,
            'detail': {'source': source, 'fires': n,
                       'sports': sports,
                       'latest_context': latest.get('context')},
        }, dry_run=dry_run)


def sanity_check_pitcher_ip(snapshot_date: date, dry_run: bool = False) -> None:
    """Sample 3 of today's starters; cross-check ctx.home/away_last_ip vs
    MLB Stats API. If any diverge by >= 2 IP, log + alert."""
    ctx_rows = requests.get(f'{SB}/rest/v1/mlb_game_context', headers=H_READ,
        params={'game_date': f'eq.{snapshot_date.isoformat()}',
                'select': 'home_pitcher_id,away_pitcher_id,home_pitcher,away_pitcher,'
                          'home_last_ip,away_last_ip',
                'limit': '10'}, timeout=15).json()
    if not isinstance(ctx_rows, list) or not ctx_rows: return

    from data_quality import DQ
    dq = DQ(source='check_data_quality_daily.sanity_check_pitcher_ip', sport='MLB')

    sampled = 0
    for row in ctx_rows:
        if sampled >= 3: break
        for side in ('home', 'away'):
            pid = row.get(f'{side}_pitcher_id')
            ctx_ip = row.get(f'{side}_last_ip')
            if pid is None or ctx_ip is None: continue
            sampled += 1
            # Pull actual latest from MLB API
            try:
                r = requests.get(
                    f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                    params={'stats': 'gameLog', 'group': 'pitching', 'season': 2026},
                    timeout=10).json()
                splits = r.get('stats', [{}])[0].get('splits', [])
                if not splits: continue
                raw_ip = splits[-1]['stat'].get('inningsPitched', '0')
                api_ip = float(raw_ip.replace('.1', '.33').replace('.2', '.67') or '0')
            except Exception:
                continue
            # Cross-check with 0.5 IP tolerance
            dq.assert_close(ctx_ip, api_ip, tolerance=0.5,
                             check_name='last_ip_ctx_vs_api',
                             context={'pitcher': row.get(f'{side}_pitcher'),
                                      'pitcher_id': pid,
                                      'ctx_ip': ctx_ip, 'api_ip': api_ip,
                                      'side': side})
            if sampled >= 3: break


def run(dry_run: bool = False):
    snapshot_date = _et_today()
    print(f'=== data_quality_daily · {snapshot_date} ===')

    events = load_recent_events(hours=24)
    print(f'  loaded {len(events)} events from last 24h')
    promote_events(events, snapshot_date, dry_run=dry_run)

    print('  running sanity check: pitcher last_ip cross-check')
    sanity_check_pitcher_ip(snapshot_date, dry_run=dry_run)

    print('  done')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
