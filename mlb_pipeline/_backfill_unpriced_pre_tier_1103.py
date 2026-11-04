"""Give the unpriced-market cap back its pre-cap tier, on UNPLAYED games only.

== WHY ====================================================================
2026-11-03 I fixed apply_unpriced_market_gate so a cap lifts when the price
arrives, by recording `_unpriced_pre_tier` at cap time. That fix only helps
rows capped AFTER it shipped: every row already flagged has
`_unpriced_pre_tier = None`, so the restore branch can never fire on them
and they stay COVERAGE forever.

== THE BACKLOG IS 3-5 ROWS, NOT 59 =======================================
I wrote "59 permanently capped" in the fix itself and carried that number
into the backlog list. Re-measured today, that figure counts rows whose
game has ALREADY BEEN PLAYED:

    flagged _unpriced_market, NHL              57   (0 in every other sport)
      game already played                      48   <- do NOT touch
      game not yet started, pre-tier in note    5
      game not yet started, no pre-tier         4   <- unknowable, skip

Re-tiering a settled game rewrites what we published after the fact. The
public record has to say what we actually showed, and a receipt frozen at
COVERAGE is the truth about that game
(feedback_fix_the_cache_not_the_frozen_receipt). The three 2026-11-03 rows
are excluded for the same reason: their games commenced 00:00 UTC, which is
19:00 ET, already underway when this ran.

== WHAT THIS WRITES ======================================================
`_unpriced_pre_tier` ONLY. It does not touch `tier`.

The pre-cap tier is recovered from the audit_note sentence the gate itself
wrote ("capped to COVERAGE from LEAN: ..."). Parsing prose is normally
exactly the wrong thing for a GATE to do
(feedback_parse_a_pick_by_shape_not_vocabulary) -- which is precisely why
the gate now stores the field instead. A one-off repair is the one place
the note is the only record there is, and the pattern is machine-written
and anchored, not free text.

Then the next NHL context rebuild runs the real gate, and IT decides: the
two rows whose price has landed restore to LEAN, the three still unpriced
stay capped until theirs does. No tier in this file.

Readback-verified per row.

    python _backfill_unpriced_pre_tier_1103.py            # dry run
    python _backfill_unpriced_pre_tier_1103.py --apply
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

APPLY = '--apply' in sys.argv
TABLE = 'nhl_game_context'
PAT = re.compile(r'capped to COVERAGE from ([A-Z]+):')
#: the gate's own vocabulary -- refuse anything else rather than write it
VALID = {'PRIME', 'STRONG', 'LEAN', 'SOLID', 'LIGHT'}


def main() -> int:
    now = datetime.now(timezone.utc)
    r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=90,
                     params={'select': 'game_id,game_date,home_team,away_team,'
                                       'commence_time,primary_play',
                             'limit': 2000})
    r.raise_for_status()
    rows = r.json() or []

    plan, skipped = [], []
    for row in rows:
        pp = row.get('primary_play')
        if not isinstance(pp, dict) or not pp.get('_unpriced_market'):
            continue
        if pp.get('_unpriced_pre_tier'):
            skipped.append((row, 'already has _unpriced_pre_tier'))
            continue
        ct = row.get('commence_time')
        started = True
        if ct:
            try:
                started = datetime.fromisoformat(
                    str(ct).replace('Z', '+00:00')) <= now
            except ValueError:
                started = True       # unparseable -> treat as played, skip
        if started:
            skipped.append((row, 'game already started/played'))
            continue
        m = PAT.search(str(pp.get('audit_note') or ''))
        if not m:
            skipped.append((row, 'no pre-cap tier recoverable from audit_note'))
            continue
        pre = m.group(1).upper()
        if pre not in VALID:
            skipped.append((row, f'pre-cap tier {pre!r} not in gate vocabulary'))
            continue
        plan.append((row, pre))

    print(f'{TABLE}: {len(rows)} rows scanned')
    print(f'  to repair : {len(plan)}')
    print(f'  skipped   : {len(skipped)}')
    by = {}
    for _, why in skipped:
        by[why] = by.get(why, 0) + 1
    for why, n in sorted(by.items(), key=lambda x: -x[1]):
        print(f'      {n:3d}  {why}')
    print()
    for row, pre in sorted(plan, key=lambda x: str(x[0]['game_date'])):
        pp = row['primary_play']
        print(f"  {row['game_date']}  {row['away_team'][:20]:20} @ "
              f"{row['home_team'][:20]:20} {str(pp.get('type')):4} "
              f"tier={pp.get('tier')} -> _unpriced_pre_tier={pre}")
    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return 0

    rc = 0
    print()
    for row, pre in plan:
        pp = dict(row['primary_play'])
        pp['_unpriced_pre_tier'] = pre
        pp['_unpriced_pre_tier_backfilled'] = {
            'at': datetime.now(timezone.utc).isoformat(),
            'from': 'audit_note',
            'note': 'the gate did not store this field before 2026-11-03',
        }
        pr = requests.patch(f'{SB}/rest/v1/{TABLE}', headers=H_W, timeout=30,
                            params={'game_id': f"eq.{row['game_id']}"},
                            data=json.dumps({'primary_play': pp}))
        if pr.status_code not in (200, 204):
            print(f"  !! {row['game_id']} PATCH {pr.status_code}: "
                  f'{pr.text[:160]}')
            rc = 1
            continue
        rb = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=30,
                          params={'select': 'primary_play',
                                  'game_id': f"eq.{row['game_id']}"})
        got = ((rb.json() or [{}])[0].get('primary_play') or {})
        if got.get('_unpriced_pre_tier') != pre:
            print(f"  !! {row['game_id']} NOT CONFIRMED -- readback says "
                  f"{got.get('_unpriced_pre_tier')!r}")
            rc = 1
        elif str(got.get('tier')) != str(row['primary_play'].get('tier')):
            print(f"  !! {row['game_id']} tier MOVED "
                  f"{row['primary_play'].get('tier')} -> {got.get('tier')} "
                  f'-- this script must never change tier')
            rc = 1
        else:
            print(f"  confirmed {row['game_id']}  pre_tier={pre}  "
                  f"tier unchanged ({got.get('tier')})")
    print('\nNow re-run the NHL context rebuild. The gate itself decides which '
          'of these restore -- this script sets no tier.')
    return rc


if __name__ == '__main__':
    sys.exit(main())
