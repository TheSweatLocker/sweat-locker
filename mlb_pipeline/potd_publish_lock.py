#!/usr/bin/env python3
"""The POTD publish lock — ONE implementation, for every writer.

== 2026-10-16 - WHY THIS EXISTS ==
ANDY: "i am sick of seeing these issues continue to pop up when i feel like it
was addressed" ... "Guardians should of stayed as POTD and it would of won, i
want to find out why the switch happened and i dont want it happening again."

WHAT HAPPENED ON 2026-10-15, from the timestamps:

  11:01 UTC  daily_best_bet_history + jerry_cache created for the date
  11:31 ET   jerry_anchor_potd publishes Cleveland Guardians ML, conviction 70.
             public_receipts freezes a receipt from the LEDGER at 15:31 UTC
             (capture_mode=reconstructed, captured_by=jerry_anchor_potd,
             pick_odds -133, conviction 70) — so the ledger said GUARDIANS.
  later      play_of_day.py overwrites the same row via
             `daily_best_bet_history?on_conflict=bet_date` with
             "Los Angeles Dodgers ML (resolver STRONG)", sweat_score 35.
  21:28 UTC  jerry_cache refreshed to the Dodgers.

Result: a conviction-70 pick was replaced by a score-35 pick, AFTER the 12:00
ET hard lock. The Guardians won 3-2. The Dodgers lost 4-6.

WHY THE LOCK DID NOT STOP IT. jerry_anchor_potd.py:463-530 implements a real
lock, and a strict one — past POTD_HARD_LOCK_ET_HOUR (default 12 ET) a
republish is "REFUSED even with --force", emergency override only. But BOTH its
checks key on

    jerry_cache.data.anchor == 'jerry_synthesis_v1'

and both live INSIDE jerry_anchor_potd. They stop jerry_anchor_potd from
republishing over itself. They do not stop play_of_day.py, which writes the
POTD unconditionally and never consults the lock.

So the lock was never wrong. It was never ASKED. Exactly the shape of the
nfl_game_context v4 neutralisation (guarded one consumer, signal_sources had no
guard) and of the 10-08 POTD odds fix (fixed odds, left the narrative). A guard
that one writer honours is not a lock, it is a convention.

== THE RULE, STATED ONCE ==
A POTD is LOCKED for a date when either holds:

  1. HARD LOCK — it is past POTD_HARD_LOCK_ET_HOUR (default 12 ET) and a POTD
     already exists for that date with a real pick. Immutable; only
     POTD_EMERGENCY_UNLOCK=1 passes.
  2. ANCHOR LOCK — jerry_cache.data.anchor == 'jerry_synthesis_v1', i.e. Jerry
     has anchored the day. Overridable by an explicit force.

"A real pick" excludes the discipline-pass placeholders ("No Play", "No
Games"), so a rest day never blocks a later genuine publish.

Every POTD writer calls potd_locked() BEFORE writing. There is one rule and one
place to change it.

USAGE (as a library)
    from potd_publish_lock import potd_locked
    locked, why = potd_locked(game_date)
    if locked:
        print(f'POTD locked: {why}'); return

USAGE (as a check)
    python potd_publish_lock.py --date 2026-10-16
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import requests

def _env():
    """Resolve Supabase creds LAZILY.

    Read at import time this broke standalone use: the module was imported
    before any dotenv load and captured None. Callers inside the pipeline
    already have the vars exported; a CLI run needs the .env. Resolve on each
    call so both work.
    """
    sb = os.environ.get('SUPABASE_URL')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ.get('SUPABASE_KEY'))
    if sb and key:
        return sb, key
    import pathlib
    import re as _re
    envf = pathlib.Path(__file__).with_name('.env')
    if envf.exists():
        for ln in envf.read_text(encoding='utf-8', errors='replace').splitlines():
            m = _re.match(r'\s*([A-Z0-9_]+)\s*=\s*(.*)', ln)
            if m:
                os.environ.setdefault(m.group(1),
                                      m.group(2).strip().strip('"').strip("'"))
    return (os.environ.get('SUPABASE_URL'),
            os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
            or os.environ.get('SUPABASE_KEY'))

#: Past this ET hour the POTD is immutable. Matches the Sweat Card / Sharp
#: Card lock (2026-09-19: "POTD rides on the Sweat Card, so a later lock here
#: would let the headline play move after the card holding it had frozen").
HARD_LOCK_ET_HOUR = int(os.environ.get('POTD_HARD_LOCK_ET_HOUR', '12'))
#: Placeholders that are NOT a real pick, so they never block a publish.
PLACEHOLDER_LEANS = ('no play', 'no games', 'no qualifying', 'rest day',
                     'discipline pass', 'pending')


def _headers():
    sb, key = _env()
    if not sb or not key:
        raise RuntimeError('SUPABASE_URL / key not resolvable')
    return sb, {'apikey': key, 'Authorization': f'Bearer {key}'}


def _et_hour() -> int:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).hour


def _is_real_pick(lean) -> bool:
    s = str(lean or '').strip().lower()
    if not s:
        return False
    return not any(p in s for p in PLACEHOLDER_LEANS)


def potd_locked(game_date: str, force: bool = False,
                dry_run: bool = False, verbose: bool = True) -> tuple:
    """-> (locked: bool, reason: str). Fail OPEN on an unreachable DB.

    Fails open deliberately: a POTD that cannot publish because the lock
    check itself broke is a worse outcome than a republish, and the caller
    still logs the reason.
    """
    if dry_run:
        return False, 'dry_run'
    if os.environ.get('POTD_EMERGENCY_UNLOCK') == '1':
        return False, 'POTD_EMERGENCY_UNLOCK=1'
    SB, H = _headers()

    existing_lean = None
    try:
        r = requests.get(f'{SB}/rest/v1/daily_best_bet_history', headers=H,
                         params={'select': 'lean,sweat_score,result',
                                 'bet_date': f'eq.{game_date}',
                                 'limit': '1'}, timeout=10)
        if r.status_code == 200 and r.json():
            existing_lean = (r.json()[0] or {}).get('lean')
    except Exception as e:                                     # noqa: BLE001
        if verbose:
            print(f'  ⚠ POTD lock: ledger read failed ({type(e).__name__}) '
                  f'— failing OPEN')
        return False, f'ledger unreadable: {type(e).__name__}'

    # 1. HARD LOCK — past the hour, with a real pick already out there.
    hour = _et_hour()
    if hour >= HARD_LOCK_ET_HOUR and _is_real_pick(existing_lean):
        return True, (f'HARD LOCK — {hour:02d}:00 ET is past '
                      f'{HARD_LOCK_ET_HOUR:02d}:00 and a real POTD is already '
                      f'published ({str(existing_lean)[:48]!r}). Immutable '
                      f'even with --force; POTD_EMERGENCY_UNLOCK=1 only.')

    # 2. ANCHOR LOCK — Jerry has anchored the day.
    try:
        r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H,
                         params={'select': 'data,fetched_at',
                                 'cache_key': f'eq.best_bet_{game_date}'},
                         timeout=10)
        if r.status_code == 200 and r.json():
            row = r.json()[0]
            data = row.get('data') or {}
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:                              # noqa: BLE001
                    data = {}
            if (data or {}).get('anchor') == 'jerry_synthesis_v1':
                if force:
                    return False, 'anchor lock present but force given'
                return True, (f'ANCHOR LOCK — Jerry anchored this date at '
                              f'{str(row.get("fetched_at"))[:19]}. Use --force '
                              f'or POTD_ALLOW_REPUBLISH=1.')
    except Exception as e:                                     # noqa: BLE001
        if verbose:
            print(f'  ⚠ POTD lock: jerry_cache read failed '
                  f'({type(e).__name__}) — anchor check skipped')

    return False, (f'unlocked (ET {hour:02d}:00, existing lean '
                   f'{str(existing_lean)[:40]!r})')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=(dt.datetime.now(dt.timezone.utc)
                                       - dt.timedelta(hours=4)).date()
                    .isoformat())
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    locked, why = potd_locked(a.date, force=a.force)
    print(f'POTD {a.date}: {"LOCKED" if locked else "unlocked"}')
    print(f'  {why}')
    print(f'  HARD_LOCK_ET_HOUR={HARD_LOCK_ET_HOUR}  ET now={_et_hour():02d}:00')
    return 1 if locked else 0


if __name__ == '__main__':
    sys.exit(main())
