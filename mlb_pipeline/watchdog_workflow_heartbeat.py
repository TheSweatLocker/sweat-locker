"""Report pipelines whose scheduled run never started, or started very late.

Andy 2026-09-28, 9:26am ET: "dont see a sweat card in app or POTD."

Answering that took six queries and a wrong first conclusion. workflow_heartbeat
already holds everything needed — the 2026-08-27 note that created it says so
outright: "If no start row for today, GitHub Actions dropped the cron." But
NOTHING READS IT. The only file in the repo that touches the table is
_morning_audit_v3_2026_09_06.py, which is an underscore-prefixed scratch file
and therefore gitignored. The telemetry was collected and never checked.

WHAT THIS ANSWERS IN ONE COMMAND
  * did today's run start at all?            (missing row = cron dropped)
  * did it start but die?                    (start with no end)
  * is it merely late, or actually missing?  (vs that slot's own history)

WHY "LATE" NEEDS A MEASURED BASELINE, NOT THE CRON TIME
The nominal cron time is useless as a reference here. mlb_pipeline's primary
trigger was '0 10 * * *' and over 20 days it started between 13:20 and 16:22
UTC — a consistent 3.5-6 hour queue delay. Comparing against 10:00 would have
flagged all 20 days as late and taught everyone to ignore the alert. So
lateness is measured against the p90 of that slot's OWN last-14-day history,
which adapts if GitHub's queueing changes.

A slot with fewer than 4 historical starts is reported as UNKNOWN rather than
late or on-time — a baseline built from three samples is not a baseline.

EXIT CODE
  0  every expected slot either started, or is still inside its normal window
  1  at least one slot is MISSING past its window  (run_step.sh records it,
     the --gate step turns the run red)

CLI
  python watchdog_workflow_heartbeat.py
  python watchdog_workflow_heartbeat.py --date 2026-09-28
  python watchdog_workflow_heartbeat.py --quiet     # only problems
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Which (workflow, slot) pairs are expected on which weekdays.
# None = every day. Weekdays are Python's Monday=0.
EXPECTED = {
    ('mlb_pipeline', '06'): None,
    ('mlb_pipeline', '1230'): None,
    ('mlb_pipeline', '18'): None,
    ('nhl_pipeline', None): None,       # any slot; NHL runs daily in season
    ('mlb_grade_overnight', None): None,
    # 2026-09-29: the cross-sport nightly. It owns NCAAF's only daily resolver
    # and the only writer of team_stats_rolling_history, so a dropped cron here
    # costs a permanently missing day of history for all six sports — exactly
    # what happened on 09-27/09-28. Watched from the day it exists rather than
    # after it first fails.
    ('nightly_cross_sport', None): None,
}

LOOKBACK_DAYS = 14
MIN_BASELINE = 4          # starts needed before "late" means anything


def _page(path, params):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=45)
        if r.status_code != 200:
            raise SystemExit(f'{path} -> {r.status_code}: {r.text[:200]}')
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _mins(ts: str) -> int | None:
    """Minutes past midnight UTC for an ISO timestamp."""
    try:
        return int(ts[11:13]) * 60 + int(ts[14:16])
    except (ValueError, IndexError, TypeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today UTC)')
    ap.add_argument('--quiet', action='store_true', help='print problems only')
    a = ap.parse_args()

    now = datetime.now(timezone.utc)
    day = a.date or now.date().isoformat()
    since = (datetime.fromisoformat(day) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()

    rows = _page('workflow_heartbeat',
                 {'select': 'workflow,event,cron_slot,fired_at',
                  'fired_at': f'gte.{since}'})

    # history[(workflow, slot)] = [minutes-past-midnight of each past start]
    history = defaultdict(list)
    today_start, today_end = {}, {}
    for r in rows:
        wf, slot = r.get('workflow'), str(r.get('cron_slot') or '')
        ts = str(r.get('fired_at') or '')
        d, m = ts[:10], _mins(ts)
        if m is None or not wf:
            continue
        if r.get('event') == 'start':
            if d == day:
                today_start.setdefault((wf, slot), m)
            elif slot != 'manual':
                history[(wf, slot)].append(m)
        elif r.get('event') == 'end' and d == day:
            today_end.setdefault((wf, slot), m)

    now_m = now.hour * 60 + now.minute if day == now.date().isoformat() else 24 * 60
    problems, lines = [], []

    for (wf, want_slot) in EXPECTED:
        # Match any slot when the expectation does not name one.
        keys = [k for k in set(list(today_start) + list(history))
                if k[0] == wf and (want_slot is None or k[1] == want_slot)]
        if want_slot is not None:
            keys = [k for k in keys if k[1] == want_slot] or [(wf, want_slot)]

        started = any(k in today_start for k in keys)
        past = [m for k in keys for m in history.get(k, [])]
        label = f'{wf}:{want_slot or "any"}'

        if started:
            k = next(k for k in keys if k in today_start)
            st = today_start[k]
            done = k in today_end
            lines.append(f'  OK       {label:34s} started {st//60:02d}:{st%60:02d} UTC'
                         + ('' if done else '  (no end yet)'))
            continue

        if len(past) < MIN_BASELINE:
            lines.append(f'  UNKNOWN  {label:34s} no start today; only '
                         f'{len(past)} prior runs — no baseline to judge')
            continue

        past.sort()
        p90 = past[int(len(past) * 0.9)] if len(past) > 1 else past[0]
        window = p90 + 60          # one hour of grace past the p90
        if now_m <= window:
            lines.append(f'  PENDING  {label:34s} not started; normal window ends '
                         f'{window//60:02d}:{window%60:02d} UTC '
                         f'(p90 {p90//60:02d}:{p90%60:02d})')
        else:
            msg = (f'{label} MISSING — no start today, and the normal window '
                   f'closed at {window//60:02d}:{window%60:02d} UTC '
                   f'(p90 of last {len(past)} runs {p90//60:02d}:{p90%60:02d})')
            problems.append(msg)
            lines.append(f'  MISSING  {label:34s} window closed '
                         f'{window//60:02d}:{window%60:02d} UTC')

    if not a.quiet or problems:
        print(f'=== workflow heartbeat · {day} · now {now_m//60:02d}:{now_m%60:02d} UTC ===')
        for l in lines:
            print(l)

    if problems:
        print()
        for p in problems:
            print(f'  ⚠ {p}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
