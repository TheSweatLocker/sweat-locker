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


#: Anchor strings written by a HUMAN decision rather than by the engine.
#: Kept in step with jerry_anchor_potd._MANUAL_ANCHORS.
MANUAL_ANCHORS = ('manual_owner_pick',)


def is_manual_potd(data) -> bool:
    """True when this payload records a human POTD decision.

    == 2026-11-02 - THE HOLE THIS CLOSES ================================
    On 2026-11-01 Andy's hand-set POTD (NFL, MIN +3.5 at -120) was
    OVERWRITTEN in daily_best_bet_history with an NBA pick: sport NBA, game
    "Boston Celtics @ Orlando Magic", odds -110, sweat_score 53. `lean` and
    `narrative` survived, so the record row argued for a Minnesota football
    play while claiming it was a Boston basketball game - and a second POTD
    receipt (id 4067290) was reconstructed from the corrupted row, sharing
    `source_id = potd:2026-11-01` with the correct one.

    This function is the reason it happened. `potd_locked` had exactly two
    tests, and a hand-set pick passed both:

      1. HARD LOCK   - requires the ET hour to be >= 12. The overwrite
                       landed in the 11:41Z run, computed here as 07:00 ET.
      2. ANCHOR LOCK - tested `anchor == 'jerry_synthesis_v1'` ONLY. A
                       manual POTD carries `anchor='manual_owner_pick'`
                       plus a `_manual` stamp, so it matched nothing.

    Verified against the live row before the fix:

        $ python potd_publish_lock.py --date 2026-11-01
        POTD 2026-11-01: unlocked
          unlocked (ET 06:00, existing lean 'MIN +3.5')

    jerry_anchor_potd.py had this same miss and it was fixed there on
    10-25 (`_is_manual_potd`, see _MANUAL_ANCHORS). The fix was applied to
    ONE writer and not to the shared lock that this module's own header
    calls "the single implementation every writer asks" - so play_of_day.py,
    which dutifully asks the lock, was told the day was open. Patching one
    writer and leaving the chokepoint is exactly
    feedback_fix_every_writer_to_the_table, and a lock that cannot see a
    hand-set decision is feedback_lock_must_see_a_manual_decision.
    """
    if not isinstance(data, dict):
        return False
    if isinstance(data.get('_manual'), dict):
        return True
    return str(data.get('anchor') or '') in MANUAL_ANCHORS


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
            # MANUAL LOCK first, and NOT overridable by `force`. A
            # hand-set play is a publication: --force may override the
            # engine republishing over itself, never a human decision.
            # Only POTD_EMERGENCY_UNLOCK=1 (handled above) clears it.
            if is_manual_potd(data):
                _who = ((data or {}).get('_manual') or {}).get('by') or '?'
                return True, (
                    f'MANUAL LOCK — best_bet_{game_date} is a hand-set POTD '
                    f'({str((data or {}).get("leanDisplay"))[:40]!r}, set by '
                    f'{_who}). --force does NOT override a hand-set play; '
                    f'clear it by hand or use POTD_EMERGENCY_UNLOCK=1.')
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


def _self_test() -> int:
    """The 11-01 hole, as a test. Pure: no DB."""
    print('potd_publish_lock self-test')
    manual = {'anchor': 'manual_owner_pick', 'leanDisplay': 'MIN +3.5',
              '_manual': {'by': 'andy'}}
    assert is_manual_potd(manual) is True
    # a manual stamp alone is enough, even if the anchor is something else
    assert is_manual_potd({'_manual': {'by': 'andy'}}) is True
    # the engine's own anchor is NOT manual -- it stays force-overridable
    assert is_manual_potd({'anchor': 'jerry_synthesis_v1'}) is False
    assert is_manual_potd({}) is False
    assert is_manual_potd(None) is False
    assert is_manual_potd('a string payload') is False
    # a _manual key of the wrong type must not be read as a decision
    assert is_manual_potd({'_manual': True}) is False
    print('  manual payload recognised, engine payload still not  ok')
    assert _is_real_pick('MIN +3.5') is True
    assert _is_real_pick('No Play (discipline pass)') is False
    assert _is_real_pick('Pending') is False
    assert _is_real_pick(None) is False
    print('  real-pick test unchanged  ok')
    print('self-test PASSED')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=(dt.datetime.now(dt.timezone.utc)
                                       - dt.timedelta(hours=4)).date()
                    .isoformat())
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    locked, why = potd_locked(a.date, force=a.force)
    print(f'POTD {a.date}: {"LOCKED" if locked else "unlocked"}')
    print(f'  {why}')
    print(f'  HARD_LOCK_ET_HOUR={HARD_LOCK_ET_HOUR}  ET now={_et_hour():02d}:00')
    return 1 if locked else 0


if __name__ == '__main__':
    sys.exit(main())
