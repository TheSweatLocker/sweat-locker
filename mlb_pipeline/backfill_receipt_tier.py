#!/usr/bin/env python3
"""Stamp the gate's own tier onto game_read receipts already written.

══ WHY ══════════════════════════════════════════════════════════════════
`backfill_public_receipts.py` hardcoded 'tier': None on every game_read
receipt. Tier is the gate deciding what reaches users, so B178 -- the finding
that tier is band-conditional and applied backwards on both bands -- could
only be measured by joining the MUTABLE `primary_play`, where a post-game
re-score would silently corrupt the reading.

That writer is fixed (8d55bf30), but the receipts upsert is
`Prefer: resolution=ignore-duplicates` -- FIRST WRITE WINS. Re-running it
reported "written=496" while changing nothing, because every row already
existed. Measured after: all 553 NCAAF and 148 NFL game_read receipts still
read tier NULL. Same family as the 10-13 finding that ignore-duplicates
cannot deliver a late grade. So the fix only reaches NEW receipts and the ones
already written need an explicit UPDATE. This is it.

SOURCE: input_snapshot->primary_play->>tier on jerry_reads, joined on
(sport, game_id). input_snapshot is written at read time and never changes, so
it holds what the gate ACTUALLY applied -- not what primary_play says now.
Present on 410 of 456 sampled reads with the full range (STRONG, LEAN,
COVERAGE, PASS, PRIME).

WHY THIS CANNOT MOVE A PUBLISHED RECORD, checked before writing rather than
assumed:
  * no query anywhere filters public_receipts on tier. The tier-filtered
    rollups in compute_surface_records (`_pick_prop_tier`) read the
    *_pipeline_props tables; the COVERAGE skip at line 516 is inside
    pick_ladder(), which reads ladder_rung.
  * the app's tier chip was removed from the game-analysis surface, so this
    is not user-visible either.
It annotates rows for measurement. It changes no W/L, no price, no side.

REQUEST BUDGET (B154): ids are grouped BY TIER and PATCHed in chunks via
`id=in.(...)`, so 700+ rows cost about a dozen writes instead of 700. The
whole run is ~20 requests.

Only rows whose tier IS NULL are touched, so a real stored tier can never be
overwritten, and the run is idempotent.

Usage:
    python backfill_receipt_tier.py                 # dry run
    python backfill_receipt_tier.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        if '=' in _ln and not _ln.strip().startswith('#'):
            _k, _v = _ln.strip().split('=', 1)
            os.environ.setdefault(_k, _v)

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = dict(H, **{'Content-Type': 'application/json'})

SPORTS = ('NCAAF', 'NFL', 'NHL', 'NBA', 'MLB', 'NCAAB')
VALID = ('PRIME', 'STRONG', 'LEAN', 'COVERAGE', 'PASS')
CHUNK = 120
_req = {'n': 0}


def _get(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        _req['n'] += 1
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, timeout=60,
                         params={**params, 'offset': str(off),
                                 'limit': '1000'})
        if r.status_code not in (200, 206):
            print(f'  WARN GET {path} HTTP {r.status_code}: {r.text[:160]}')
            return out
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--sport', action='append', choices=sorted(SPORTS))
    a = ap.parse_args()
    sports = a.sport or list(SPORTS)
    print(f'=== backfill_receipt_tier · {"APPLY" if a.apply else "DRY RUN"} ===')
    grand = {'matched': 0, 'no_read': 0, 'no_tier': 0, 'patched': 0}
    for sport in sports:
        # the tier the gate applied, frozen at read time
        reads = _get('jerry_reads',
                     {'select': 'game_id,'
                                'snap_tier:input_snapshot->primary_play->>tier',
                      'sport': f'eq.{sport}', 'order': 'id.asc'})
        tier_by_gid = {}
        for x in reads:
            t = str(x.get('snap_tier') or '').upper()
            if t in VALID and x.get('game_id'):
                tier_by_gid[str(x['game_id'])] = t
        # receipts still missing a tier
        recs = _get('public_receipts',
                    {'select': 'id,game_id,market', 'surface': 'eq.game_read',
                     'sport': f'eq.{sport}', 'tier': 'is.null',
                     'order': 'id.asc'})
        if not recs:
            print(f'  {sport:6s} no game_read receipts with a null tier')
            continue
        by_tier: dict[str, list[int]] = {}
        no_read = 0
        for x in recs:
            t = tier_by_gid.get(str(x.get('game_id')))
            if not t:
                no_read += 1
                continue
            by_tier.setdefault(t, []).append(int(x['id']))
        matched = sum(len(v) for v in by_tier.values())
        grand['matched'] += matched
        grand['no_read'] += no_read
        print(f'  {sport:6s} {len(recs)} null-tier receipts · '
              f'{len(tier_by_gid)} reads carry a tier · '
              f'matched {matched} · unmatched {no_read}')
        for t, ids in sorted(by_tier.items()):
            print(f'           -> {t:9s} {len(ids)}')
        if not a.apply:
            continue
        for t, ids in sorted(by_tier.items()):
            for i in range(0, len(ids), CHUNK):
                part = ids[i:i + CHUNK]
                inlist = ','.join(str(x) for x in part)
                _req['n'] += 1
                up = requests.patch(f'{SB}/rest/v1/public_receipts',
                                    headers=HW, timeout=90,
                                    params={'id': f'in.({inlist})',
                                            'tier': 'is.null'},
                                    data=json.dumps({'tier': t}))
                if up.status_code not in (200, 204):
                    print(f'      WARN PATCH {t} HTTP {up.status_code}: '
                          f'{up.text[:200]}')
                    continue
                grand['patched'] += len(part)
    # A 204 is not a write (feedback_204_is_not_a_write): count what MOVED.
    if a.apply:
        left = 0
        for sport in sports:
            left += len(_get('public_receipts',
                             {'select': 'id', 'surface': 'eq.game_read',
                              'sport': f'eq.{sport}', 'tier': 'is.null'}))
        print(f'  claimed patched {grand["patched"]}; still null across '
              f'these sports: {left} (expected {grand["no_read"]})')
        if left != grand['no_read']:
            print('  ⚠ MISMATCH — the trigger may have refused the column, '
                  'or a read/receipt join is wrong. Treat tier as unwritten.')
    print(f'  matched={grand["matched"]}  no_read_tier={grand["no_read"]}  '
          f'supabase requests used: {_req["n"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
