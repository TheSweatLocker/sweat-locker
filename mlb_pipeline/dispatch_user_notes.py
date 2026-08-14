"""User-facing note dispatcher (2026-08-14).

Session D output layer. Reads monitoring signals (dashboard_alerts,
hit_rate_snapshots) and generates in-app notes for user surfacing.

Constraints enforced (per 2026-08-14 direction):
  * IN-APP ONLY (no email, no push)
  * DATA-FIRST tone (no apologies, no customer-service voice)
  * FREQUENCY CAP: max 1 note per category per 21 days
  * DEFAULT to quiet fix — only surface notes for genuinely user-visible events

Templates handled in this dispatch (each with its own trigger condition):

  losing_streak      : 7d aggregate hit rate drops ≥12pp below 30d baseline,
                       AND 7d n ≥ 15 (enough sample for signal, not noise).
                       Fires from Session A's tier_hit_drop alerts, filtered
                       to the surface='pipeline_prop' aggregate row.

  track_record_recap : monthly (first day of month). Publishes hit-rate
                       summary from the previous month's snapshots.

  model_change       : manually triggered — copywriter runs this with a
                       --template model_change --title X --body Y invocation.
                       Not auto-generated; ships when a lens/rule change
                       warrants user notice.

  new_user_variance  : per-user, fires when user's first 7 days show a
                       hit rate <45% on the picks they viewed. Requires
                       an event stream we don't yet have (in-app view
                       tracking) — deferred, framework in place.

CLI:
    python dispatch_user_notes.py [--dry-run]
    python dispatch_user_notes.py --template model_change --title "..." --body "..."
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timedelta, timezone
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

# Frequency cap: no user (or category) sees more than 1 note per this many days.
CATEGORY_MIN_DAYS_BETWEEN = 21

# Losing streak thresholds
LOSING_STREAK_DROP_PP = 12       # 7d hit rate must be ≥12pp below 30d
LOSING_STREAK_MIN_7D_N = 15      # 7d sample floor

USER_NOTE_CATEGORIES = (
    'losing_streak',
    'track_record_recap',
    'model_change',
    'new_user_variance',
)


def category_gate_open(category: str) -> bool:
    """Passes if no note in this category was published within the min-day window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CATEGORY_MIN_DAYS_BETWEEN)).isoformat()
    r = requests.get(f'{SB}/rest/v1/user_notes', headers=H_READ,
        params={'category': f'eq.{category}',
                'publish_at': f'gte.{cutoff}',
                'select': 'id', 'limit': '1'}, timeout=10)
    if r.status_code != 200: return True   # fail-open (better to send than miss)
    return len(r.json()) == 0


def publish_note(*, category: str, title: str, body: str,
                  severity: str = 'info', cohort: str = 'all',
                  ttl_days: int = 7,
                  source_alert_id: Optional[int] = None,
                  context: Optional[dict] = None,
                  dry_run: bool = False) -> bool:
    if not category_gate_open(category):
        print(f'  ⏸ category={category} still in frequency-cap window (min '
              f'{CATEGORY_MIN_DAYS_BETWEEN} days between). Skipping.')
        return False
    now = datetime.now(timezone.utc)
    payload = {
        'category': category,
        'title': title,
        'body': body,
        'severity': severity,
        'cohort': cohort,
        'publish_at': now.isoformat(),
        'expires_at': (now + timedelta(days=ttl_days)).isoformat(),
        'dismissible': True,
        'source_alert_id': source_alert_id,
        'context': context or {},
    }
    if dry_run:
        print(f'  [DRY] PUBLISH {category} · {severity} · ttl={ttl_days}d')
        print(f'    title: {title}')
        print(f'    body:  {body[:200]}')
        return True
    pr = requests.post(f'{SB}/rest/v1/user_notes',
        headers=H_WRITE, json=payload, timeout=10)
    if pr.status_code in (200, 201, 204):
        print(f'  ✓ published {category} note: {title[:50]}')
        return True
    print(f'  ✗ publish failed: {pr.status_code} {pr.text[:200]}')
    return False


# ─── Auto template: losing streak ────────────────────────────────────

def check_losing_streak(dry_run: bool = False) -> None:
    """Fires when the aggregate pipeline_prop 7d hit rate drops ≥12pp
    below 30d baseline with 7d n ≥ 15. Data-first tone per language guardrails."""
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    # Read today's snapshots at both windows for pipeline_prop aggregate (tier NULL)
    r = requests.get(f'{SB}/rest/v1/hit_rate_snapshots', headers=H_READ,
        params={'snapshot_date': f'eq.{today.isoformat()}',
                'sport': 'eq.MLB',
                'surface': 'eq.pipeline_prop',
                'tier': 'is.null',
                'window_days': 'in.(7,30,90)',
                'select': 'window_days,hit_rate,sample_n'}, timeout=10)
    if r.status_code != 200: return
    rows = r.json()
    by_w = {row['window_days']: row for row in rows}
    r7 = by_w.get(7); r30 = by_w.get(30); r90 = by_w.get(90)
    if not (r7 and r30): return
    h7 = r7.get('hit_rate'); n7 = r7.get('sample_n', 0)
    h30 = r30.get('hit_rate'); n30 = r30.get('sample_n', 0)
    h90 = (r90 or {}).get('hit_rate')
    if h7 is None or h30 is None: return
    if n7 < LOSING_STREAK_MIN_7D_N: return
    drop = float(h30) - float(h7)
    if drop < LOSING_STREAK_DROP_PP: return

    # Language-guardrails-compliant copy
    title = 'Model hit rate below recent baseline'
    body = (
        f'7-day model hit rate: {h7}% (n={n7})\n'
        f'30-day baseline: {h30}%\n'
    )
    if h90 is not None:
        body += f'90-day: {h90}%\n'
    body += (
        '\n'
        'Recent variance below the rolling average. '
        'No methodology change from this observation.\n'
        f'Reset conditions: next {LOSING_STREAK_MIN_7D_N} graded picks vs 30-day baseline.'
    )
    publish_note(
        category='losing_streak', title=title, body=body,
        severity='notice', ttl_days=7,
        context={'h7': h7, 'n7': n7, 'h30': h30, 'n30': n30, 'h90': h90,
                 'drop_pp': round(drop, 2)},
        dry_run=dry_run,
    )


# ─── Auto template: monthly track record recap ──────────────────────

def check_track_record_recap(dry_run: bool = False) -> None:
    """First day of month → publish previous month's recap."""
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    if today.day != 1: return
    last_month_start = date(today.year - (1 if today.month == 1 else 0),
                             12 if today.month == 1 else today.month - 1, 1)
    # Sample a 30d snapshot as of the last day of the previous month
    prev_month_end = today - timedelta(days=1)
    r = requests.get(f'{SB}/rest/v1/hit_rate_snapshots', headers=H_READ,
        params={'snapshot_date': f'eq.{prev_month_end.isoformat()}',
                'window_days': 'eq.30',
                'tier': 'is.null',
                'select': 'sport,surface,hit_rate,sample_n'}, timeout=15)
    if r.status_code != 200 or not r.json(): return
    lines = []
    for row in r.json():
        if not (row.get('hit_rate') and row.get('sample_n', 0) >= 20):
            continue
        lines.append(f'{row["sport"]} {row["surface"]}: {row["hit_rate"]}% '
                     f'(n={row["sample_n"]})')
    if not lines: return
    body = f'30-day hit rates as of {prev_month_end.strftime("%b %d")}:\n\n' + '\n'.join(lines)
    body += '\n\nAggregate across published tiers. Track record view has the tier-level detail.'
    publish_note(
        category='track_record_recap',
        title=f'{last_month_start.strftime("%B")} recap',
        body=body, severity='info', ttl_days=14,
        dry_run=dry_run,
    )


# ─── Manual template: model change (CLI-invoked) ────────────────────

def publish_model_change(title: str, body: str, dry_run: bool = False):
    publish_note(category='model_change', title=title, body=body,
                  severity='info', ttl_days=10, dry_run=dry_run)


def run_daily(dry_run: bool = False) -> None:
    print('=== dispatch_user_notes ===')
    check_losing_streak(dry_run=dry_run)
    check_track_record_recap(dry_run=dry_run)
    print('  done')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--template', help='manual template: model_change')
    p.add_argument('--title')
    p.add_argument('--body')
    args = p.parse_args()

    if args.template == 'model_change':
        if not (args.title and args.body):
            print('--template model_change requires --title and --body'); sys.exit(1)
        publish_model_change(args.title, args.body, dry_run=args.dry_run)
    else:
        run_daily(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
