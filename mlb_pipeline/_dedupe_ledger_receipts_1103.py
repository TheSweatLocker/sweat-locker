"""Neutralise duplicate GRADED Ledger receipts that double-count the record.

== WHY ====================================================================
Chasing the two 91-row POTD duplicate storms turned up a bigger question:
how many receipts are duplicated, and does any of it move a record?

Measured over all 38,435 public_receipts, keyed on
(surface, source_table, source_id, game_date):

    same-day duplicate groups                               689
    extra rows beyond the first                           9,716
      prop_jerry 7,959 . game_read 1,974 . sweat_card 224
      potd 188 . ledger 46 . sharp_card 14
    groups with CONFLICTING graded results                    0
    groups with MORE THAN ONE terminal grade                 23   <- all here

So almost all of that duplication is inert: one row per group carries the
grade and the rest are VOID / NO_ACTION / null, which is the documented
recompose behaviour ("the card really did show five picks", 2026-10-30).

(First pass keyed WITHOUT game_date and reported 943 groups and 55
conflicting results. That was wrong -- these source_ids carry no date, e.g.
'ml:Milwaukee Brewers @ Cincinnati Reds|Milwaukee Brewers ML|1.5', so the
same matchup on two different nights collided. With the date in the key the
conflicts are zero. feedback_dedupe_key_shape_must_match.)

== THE ONE REAL RECORD ERROR =============================================
All 23 double-graded groups are surface='ledger', all capture_mode=
'reconstructed', all written in one backfill (ids 3298294..3298326), game
dates 2026-09-30..2026-10-21, and every pair agrees on its grade -- 15
('WIN','WIN') and 8 ('LOSS','LOSS'). So nothing is mis-graded; the same slip
is simply counted twice.

    ledger AS STORED   136-182-1P   42.77%  n=318
    ledger DEDUPED     121-174-1P   41.02%  n=295
                       +15W +8L     +1.75pp overstated

== WHAT THIS WRITES ======================================================
Keeps the EARLIEST receipt id in each group and sets the later one's result
to 'TRACKED_ELSEWHERE' -- a value already in this column's vocabulary for
exactly this (one row used it before today). Verified it is excluded from
every record: compute_surface_records._classify returns None for anything
outside win/loss/push.

Deliberately NOT a DELETE. The row stays auditable, the change is
reversible, and an immutable ledger should not lose rows to a dedupe
decision. `audit` records why.

Nothing else is touched -- not the 9,693 inert duplicates, not the two POTD
storms (91 identical VOID rows each on 2026-07-21 and 2026-10-16, which
cost no record but do inflate POTD receipt counts and are most of the
"295 of 357 POTD receipts carry no price").

Readback-verified, and it re-measures the record afterwards.

    python _dedupe_ledger_receipts_1103.py            # dry run
    python _dedupe_ledger_receipts_1103.py --apply
"""
import collections
import json
import os
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
TERM = ('WIN', 'LOSS', 'PUSH')
NEUTRAL = 'TRACKED_ELSEWHERE'


def page(select: str, **extra) -> list:
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': 1000, 'offset': off}
        p.update(extra)
        r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                         timeout=90, params=p)
        r.raise_for_status()
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def record(rows) -> tuple:
    c = collections.Counter(str(x.get('result')) for x in rows)
    w, l, p = c.get('WIN', 0), c.get('LOSS', 0), c.get('PUSH', 0)
    n = w + l
    return w, l, p, (100.0 * w / n if n else 0.0), n


def main() -> int:
    led = page('id,surface,sport,game_date,result,source_table,source_id,'
               'capture_mode,audit', surface='eq.ledger')
    w, l, p, hr, n = record(led)
    print(f'ledger receipts: {len(led)}')
    print(f'  as stored : {w}-{l}' + (f'-{p}P' if p else '')
          + f'  {hr:.2f}%  n={n}')

    groups = collections.defaultdict(list)
    for r in led:
        groups[(str(r.get('source_table')), str(r.get('source_id')),
                str(r.get('game_date')))].append(r)
    drop = []
    for _k, g in groups.items():
        term = [r for r in g if str(r.get('result')) in TERM]
        if len(term) > 1:
            term.sort(key=lambda x: x['id'])
            drop.extend(term[1:])
    if not drop:
        print('  no double-graded duplicate groups — nothing to do')
        return 0

    kept = [r for r in led if r['id'] not in {d['id'] for d in drop}]
    w2, l2, p2, hr2, n2 = record(kept)
    print(f'  deduped   : {w2}-{l2}' + (f'-{p2}P' if p2 else '')
          + f'  {hr2:.2f}%  n={n2}')
    print(f'  overstated: +{w-w2}W +{l-l2}L  {hr-hr2:+.2f}pp')
    print()
    print(f'rows to neutralise -> {NEUTRAL}: {len(drop)}')
    bad = [r for r in drop if str(r.get('capture_mode')) != 'reconstructed']
    if bad:
        print(f'  !! {len(bad)} are NOT capture_mode=reconstructed — refusing. '
              f'A live receipt is not a backfill artefact.')
        return 1
    for r in sorted(drop, key=lambda x: str(x['game_date'])):
        print(f"  {r['game_date']}  id={r['id']}  {str(r.get('result')):5} "
              f"source={r.get('source_id')}")
    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return 0

    rc = 0
    print()
    stamp = datetime.now(timezone.utc).isoformat()
    for r in drop:
        audit = r.get('audit')
        audit = audit if isinstance(audit, dict) else {}
        audit['dedupe_1103'] = {
            'was': r.get('result'),
            'at': stamp,
            'why': ('duplicate graded ledger receipt for the same '
                    '(source_table, source_id, game_date); earliest id kept'),
        }
        pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                            timeout=30, params={'id': f"eq.{r['id']}"},
                            data=json.dumps({'result': NEUTRAL,
                                             'audit': audit}))
        if pr.status_code not in (200, 204):
            print(f"  !! {r['id']} PATCH {pr.status_code}: {pr.text[:150]}")
            rc = 1
            continue
        rb = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                          timeout=30,
                          params={'select': 'result', 'id': f"eq.{r['id']}"})
        got = (rb.json() or [{}])[0].get('result')
        if got != NEUTRAL:
            print(f"  !! {r['id']} NOT CONFIRMED — readback {got!r}")
            rc = 1
        else:
            print(f"  confirmed {r['id']}  {audit['dedupe_1103']['was']} -> "
                  f'{NEUTRAL}')

    after = page('id,result', surface='eq.ledger')
    w3, l3, p3, hr3, n3 = record(after)
    print(f'\nledger record now: {w3}-{l3}' + (f'-{p3}P' if p3 else '')
          + f'  {hr3:.2f}%  n={n3}')
    if (w3, l3) != (w2, l2):
        print(f'  !! expected {w2}-{l2} — readback disagrees')
        rc = 1
    print('\nRe-run compute_surface_records.py so the stored surface row '
          'picks this up.')
    return rc


if __name__ == '__main__':
    sys.exit(main())
