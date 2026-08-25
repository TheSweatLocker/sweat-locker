"""Sport state auto-flip cron (2026-08-24).

Reads sport_registry rows + flips `state` based on today's date vs
`season_start` / `season_end`:

  today < season_start           → preseason (unchanged if already set)
  season_start <= today <= season_end → in_season
  today > season_end             → off_season

Also clears `state_message` when transitioning IN to `in_season` (the
"season starts Nov 3 — probable starters land the week before" note is
stale once the season is live).

Runs daily. Idempotent — only writes when state actually changes.
Silent no-op on days where no transitions occur (>99% of runs).

Sport-universal — walks all rows in sport_registry, no per-sport
hardcoded logic.

USAGE
─────
    python auto_flip_sport_state.py                  # apply
    python auto_flip_sport_state.py --dry-run        # preview
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import date
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}


def compute_new_state(today: date, season_start: str | None,
                       season_end: str | None) -> str | None:
    """Return target state or None if we can't determine."""
    if not season_start:
        return None
    try:
        s = date.fromisoformat(season_start)
    except ValueError:
        return None
    if today < s:
        return 'preseason'
    e = None
    if season_end:
        try:
            e = date.fromisoformat(season_end)
        except ValueError:
            e = None
    if e and today > e:
        return 'off_season'
    return 'in_season'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    today = date.today()
    print(f'[start] auto_flip_sport_state · today={today}', flush=True)

    r = requests.get(f'{SB}/rest/v1/sport_registry?select=*',
                     headers=H_READ, timeout=15)
    if r.status_code != 200:
        print(f'[abort] sport_registry read failed: {r.status_code}', flush=True)
        return
    rows = r.json() or []
    if not isinstance(rows, list):
        print(f'[abort] unexpected response shape', flush=True)
        return

    changes = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        sport = row.get('sport')
        cur_state = row.get('state')
        target = compute_new_state(today,
                                    row.get('season_start'),
                                    row.get('season_end'))
        if target is None:
            print(f'  {sport}: no season_start — skip', flush=True)
            continue
        if target == cur_state:
            print(f'  {sport}: state={cur_state} (unchanged)', flush=True)
            continue

        # Transition detected
        payload = {'state': target}
        # When transitioning INTO in_season, clear the stale preseason
        # message (typically "season starts <date> — probable starters
        # land the week before"). Off-season transitions preserve the
        # message so operators can set an "off-season returns MM/DD" note.
        if target == 'in_season' and row.get('state_message'):
            payload['state_message'] = None

        note = 'DRY' if args.dry_run else 'APPLY'
        print(f'  {sport}: {cur_state} → {target} [{note}]', flush=True)

        if args.dry_run:
            changes += 1
            continue

        pr = requests.patch(
            f'{SB}/rest/v1/sport_registry?sport=eq.{sport}',
            headers=H_WRITE,
            json=payload,
            timeout=15,
        )
        if pr.status_code in (200, 204):
            changes += 1
        else:
            print(f'    ⚠ patch failed: {pr.status_code} {pr.text[:120]}',
                  flush=True)

    if changes:
        print(f'[done] {"would-transition" if args.dry_run else "transitioned"} {changes} sport(s)',
              flush=True)
    else:
        print(f'[done] no transitions today', flush=True)


if __name__ == '__main__':
    main()
