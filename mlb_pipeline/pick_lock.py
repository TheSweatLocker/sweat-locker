"""pick_lock — one publish lock, every sport.

Andy's directive, first given for MLB ("whatever comes out in the morning
stays") and repeated for NFL ("make sure aren't overwritten, want model /
LR / primary play ready to go and not overwritable"). Both were
implemented, separately, and nothing else was:

    MLB     game_context.pick_lock_active()          8 refs
    NFL     generate_nfl_game_reads.nfl_week_write_locked()   3 refs
    NCAAF   none
    NHL     none
    NBA     none
    NCAAB   none

So on four sports — including NCAAF, in season right now — any later cron
run silently rewrites a pick that has already been published. That is
directly at odds with showing the receipts: the receipt keeps what we
published, the card starts showing something else, and the two disagree
with no record of the change.

TWO SHAPES OF LOCK, because the sports differ:

  * DAILY sports (MLB, NHL, NBA, NCAAB) lock by time of day — once the
    morning publishing window closes, the slate is frozen.
  * WEEKLY sports (NFL, NCAAF) lock by day of week — frozen from the
    first kickoff of the week through the last game.

MLB and NFL delegate to their existing functions rather than
reimplementing the comparison. Two copies of an hour check is two chances
to drift, and a lock active in one writer but not another is worse than no
lock: it just changes which job wins the race.

WHAT A LOCK DOES AND DOESN'T DO. It preserves the PUBLISHED pick and lets
every other column refresh — lineups, odds, weather, goalies. Picks
freeze; data does not. A game with no published pick yet is still
writable, because a gap is not a change: late additions and delayed
pipelines still get one.
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

# Hour (ET) after which a daily sport's slate is frozen. Overridable per
# sport so a late-starting league can publish later without touching code.
_DAILY_LOCK_HOUR = {
    'MLB': 12,
    'NHL': 12,
    'NBA': 12,
    'NCAAB': 12,
}

# Weekly sports: (lock_start_dow, lock_start_hour_et, lock_end_dow).
# dow: Mon=0 .. Sun=6. Locked from start through end inclusive.
#
# NFL: Thu 8am ET -> Mon night. Tue/Wed are the free generation window.
# NCAAF: plays Thu/Fri/Sat, so the freeze has to begin before Thursday
# kickoff — Thu 8am ET through Sunday, leaving Mon-Wed to build the week.
_WEEKLY_LOCK = {
    'NFL':   (3, 8, 0),   # Thu 8am -> Mon
    'NCAAF': (3, 8, 6),   # Thu 8am -> Sun
}


def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def lock_active(sport: str) -> bool:
    """True when this sport's published picks must not be rewritten."""
    sport = (sport or '').upper()

    # Global escape hatch, and a per-sport one. Deliberately env vars
    # rather than a --force flag: pipelines pass --force on routine steps,
    # so honouring it would leave the lock open on exactly the runs it
    # exists to stop. Same convention the MLB and NFL locks already use.
    if os.environ.get('PICK_EMERGENCY_UNLOCK') == '1':
        return False
    if os.environ.get(f'{sport}_PICK_EMERGENCY_UNLOCK') == '1':
        return False

    # Delegate where a lock already exists, so behaviour cannot diverge.
    if sport == 'MLB':
        try:
            from game_context import pick_lock_active
            return pick_lock_active()
        except Exception:
            pass
    if sport == 'NFL':
        try:
            from generate_nfl_game_reads import nfl_week_write_locked
            return nfl_week_write_locked()
        except Exception:
            pass

    if sport in _WEEKLY_LOCK:
        start_dow, start_hour, end_dow = _WEEKLY_LOCK[sport]
        now = _et_now()
        dow = now.weekday()
        # Window wraps the week boundary when end_dow < start_dow.
        if start_dow <= end_dow:
            in_window = start_dow <= dow <= end_dow
        else:
            in_window = dow >= start_dow or dow <= end_dow
        if not in_window:
            return False
        if dow == start_dow and now.hour < start_hour:
            return False
        return True

    hour = _DAILY_LOCK_HOUR.get(sport)
    if hour is None:
        return False          # unknown sport — never claim a lock
    hour = int(os.environ.get(f'{sport}_PICK_LOCK_ET_HOUR', hour))
    return _et_now().hour >= hour


def preserve_published(sport: str, table: str, context: dict,
                       supabase_url: str, headers: dict,
                       field: str = 'primary_play',
                       also_keep: tuple = ()) -> bool:
    """Restore the already-published `field` onto `context`, if locked.

    Returns True if a published value was preserved (i.e. the rebuild's
    value was discarded), False otherwise.

    Gated at the WRITE rather than in the scorer, because that is the one
    place every code path has to pass through — MLB learned this the hard
    way: its recompute script honoured the lock while the context builder
    wrote primary_play directly, so the afternoon pipeline overwrote the
    morning's pick before the lock ever ran.
    """
    gid = context.get('game_id')
    if not gid or not lock_active(sport):
        return False
    try:
        sel = ','.join([field, *also_keep]) or field
        r = requests.get(f'{supabase_url}/rest/v1/{table}', headers=headers,
                         params={'game_id': f'eq.{gid}', 'select': sel},
                         timeout=10)
        rows = r.json() if r.status_code == 200 else []
        if not rows:
            return False
        published = rows[0].get(field)
        if not published:
            return False      # a gap is not a change — still writable
        new = context.get(field)
        old_lbl = published.get('label') if isinstance(published, dict) else published
        new_lbl = new.get('label') if isinstance(new, dict) else new
        if old_lbl != new_lbl:
            print(f"  🔒 {sport} PICK LOCKED — keeping published "
                  f"'{old_lbl}' (rebuild wanted '{new_lbl}') for {str(gid)[:14]}")
        context[field] = published
        for k in also_keep:
            if rows[0].get(k) is not None:
                context[k] = rows[0][k]
        return True
    except Exception as e:
        # Never fail the write over the lock. A missed lock costs one
        # overwritten pick; a raised exception costs the whole context row.
        print(f'  ⚠ {sport} pick-lock check failed ({type(e).__name__}) — '
              f'write proceeding unlocked')
        return False


if __name__ == '__main__':
    now = _et_now()
    print(f'ET now: {now:%Y-%m-%d %H:%M} (dow={now.weekday()})')
    for s in ('MLB', 'NFL', 'NCAAF', 'NHL', 'NBA', 'NCAAB', 'UFC'):
        print(f'  {s:<6} lock_active={lock_active(s)}')
