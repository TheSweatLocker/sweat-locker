#!/usr/bin/env python3
"""Restore `generator_verdict` on reads the cell gate demoted.

== 2026-11-14 . THE SNAPSHOT RECORDED THE OVERRIDE, NOT THE DECISION =======

Migration 20260925a added `prop_jerry_reads.generator_verdict` for one
purpose: an immutable record of what the generator said BEFORE any override
touched it, so Andy's question -- "does the refit override even work or does
it just constantly cause confusion" -- is answerable at all.

On 2026-11-02, commit 5342fe03 placed `_apply_cell_gate` at the top of
`upsert_read`. That ordering is correct for `call_verdict` and for the
receipt captured off the row -- both must ship the gated decision. It is
wrong for the two snapshot columns, read ~40 lines further down, which
therefore recorded the POST-gate value.

MEASURED 2026-11-14, before the repair:

    reads carrying a cell-gate demotion     2,671   (NBA 2,147, NFL 524)
    of those with generator_verdict='PASS'  2,671   -- 100%

    demoted_from     LEAN   2,381
                     STRONG   238
                     PRIME     52

So "the model said PRIME" was unanswerable on 52 rows, and
`audit_override_impact` -- which detects an override as
`generator_verdict != call_verdict` -- saw ZERO overrides from the single
largest demoter in the repo. The tool built to judge the gate could not see
it fire. Same shape as feedback_receipt_audit_column_is_frozen_too: one
write set the shipped value and dropped its own provenance stamp.

WHY THE DATA IS RECOVERABLE. `_apply_cell_gate` always stamped
`input_snapshot.cell_gate.demoted_from` with the pre-gate verdict. The fact
was never lost, only misfiled -- so this is a restore from an existing
record and not a guess. Rows whose snapshot carries no `cell_gate` are not
touched: a PASS there is a PASS the generator reached on its own.

`generator_conviction` needs no repair. The gate rewrites `call_verdict`
only; verified 0 of 2,671 disagree with `input_snapshot.conviction`.

The generator-side fix is in generate_prop_jerry_synthesis.upsert_read,
which now snapshots before gating. This script exists for the rows written
between 2026-11-02 and that fix.

    python backfill_generator_verdict_from_cell_gate.py            # dry run
    python backfill_generator_verdict_from_cell_gate.py --apply
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

VALID = {'PRIME', 'STRONG', 'LEAN', 'FADE', 'BACK', 'PASS'}


def _page(params: str, cap: int = 40000) -> list:
    """Read every matching row.

    Raises on a bad status -- an empty read here would make a contaminated
    table look already-clean (feedback_zero_rows_is_a_wiring_error), and a
    silent truncation would under-report the damage
    (feedback_never_measure_from_truncated_output).
    """
    out: list = []
    for off in range(0, cap, 1000):
        r = requests.get(
            f'{SB}/rest/v1/prop_jerry_reads?{params}&order=id'
            f'&limit=1000&offset={off}', headers=H_READ, timeout=60)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'read failed {r.status_code}: {r.text[:300]}')
        batch = r.json()
        out += batch
        if len(batch) < 1000:
            return out
    raise RuntimeError(f'hit the {cap}-row cap -- raise it rather than '
                       f'measuring from a truncated read')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    rows = _page('input_snapshot->>cell_gate=not.is.null'
                 '&select=id,sport,game_date,call_verdict,generator_verdict,'
                 'input_snapshot')
    print(f'reads carrying a cell_gate stamp: {len(rows)}')

    todo, already, odd = [], 0, []
    for row in rows:
        gate = (row.get('input_snapshot') or {}).get('cell_gate') or {}
        orig = str(gate.get('demoted_from') or '').upper()
        if orig not in VALID or orig == 'PASS':
            odd.append((row['id'], gate.get('demoted_from')))
            continue
        if str(row.get('generator_verdict') or '').upper() == orig:
            already += 1                       # written after the fix
            continue
        todo.append((row['id'], orig, row.get('generator_verdict'),
                     row.get('sport')))

    by = collections.Counter((s, o) for _i, o, _g, s in todo)
    print(f'  already correct : {already}')
    print(f'  to restore      : {len(todo)}')
    for (sport, orig), n in sorted(by.items()):
        print(f'      {sport:5s} -> {orig:7s} {n}')
    if odd:
        print(f'  ! {len(odd)} rows have an unusable demoted_from, left '
              f'alone: {odd[:5]}')
    if not todo:
        print('nothing to do')
        return 0
    if not args.apply:
        print('\nDRY RUN -- rerun with --apply')
        return 0

    ok = fail = 0
    confirmed = collections.Counter()
    for rid, orig, _was, _sport in todo:
        r = requests.patch(
            f'{SB}/rest/v1/prop_jerry_reads?id=eq.{rid}',
            headers=H_WRITE, json={'generator_verdict': orig}, timeout=30)
        # return=representation, so a 200 carrying zero rows is a refused
        # write and not a success (feedback_204_is_not_a_write), and the
        # value is read back off the response rather than assumed
        # (feedback_receipt_audit_column_is_frozen_too).
        body = r.json() if r.status_code in (200, 201) else None
        if r.status_code in (200, 201) and isinstance(body, list) and body \
                and str(body[0].get('generator_verdict') or '').upper() == orig:
            ok += 1
            confirmed[orig] += 1
        else:
            fail += 1
            if fail <= 5:
                print(f'  !! id={rid} -> {orig}: {r.status_code} '
                      f'{str(r.text)[:160]}')
    print(f'\nrestored (confirmed in the response body): {ok}   failed: {fail}')
    for k, v in sorted(confirmed.items()):
        print(f'    {k:7s} {v}')

    # Independent re-read, because a per-row body only proves that row.
    after = _page('input_snapshot->>cell_gate=not.is.null'
                  '&generator_verdict=eq.PASS'
                  '&select=id,input_snapshot')
    still = [a['id'] for a in after
             if str((((a.get('input_snapshot') or {}).get('cell_gate') or {})
                     .get('demoted_from')) or '').upper() not in ('', 'PASS')]
    print(f're-read: rows still mis-stamped = {len(still)} {still[:5]}')
    return 0 if (fail == 0 and not still) else 1


if __name__ == '__main__':
    sys.exit(main())
