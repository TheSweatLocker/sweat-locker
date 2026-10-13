#!/usr/bin/env python3
"""Backfill grades onto public_receipts rows whose SOURCE row is graded.

══ 2026-10-13 · WHY THIS EXISTS ══
`backfill_public_receipts.upsert_batch` writes with
    Prefer: resolution=ignore-duplicates
on_conflict=(sport,surface,game_date,source_id).

That is correct for the immutable half of a receipt — the published pick, its
line and its price must never be rewritten after the fact, which is the whole
point of the surface. But `result` and `graded_at` are the MUTABLE half: they
are NULL at publish time and arrive hours later when the source table is
graded. Ignore-duplicates means a receipt inserted before its grade exists can
never receive that grade. It is stuck at NULL forever.

Whether a receipt is affected is therefore a pure race:

    snapshot 390 (10-11 20:41) → receipt first written AFTER grading ran → LOSS
    snapshot 389 (10-11 11:15) → receipt first written BEFORE grading ran → NULL

Measured on discovery, ledger alone: 258 receipts, 232 agreeing with their
snapshot, **21 stale** (snapshot graded, receipt NULL), 0 conflicting, 0
orphaned. The stale 21 skew toward WINs, so the receipt trail under-reports the
surface rather than flattering it.

══ THE ONE RULE ══
This script only ever fills a NULL. It never overwrites a receipt that already
carries a result, even if the source now disagrees — that case is a genuine
grading dispute and must be surfaced as a CONFLICT for a human, not silently
resolved. `feedback_parse_failure_must_not_overwrite` is the same lesson from
the other direction.

Usage:
    python sync_receipt_grades.py                # dry-run, every source
    python sync_receipt_grades.py --apply
    python sync_receipt_grades.py --source ledger_snapshots --apply
"""
from __future__ import annotations

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

# Windows console is cp1252 and this script prints ⚠ on every conflict.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

SB = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_SERVICE_KEY')
       or os.environ.get('SUPABASE_KEY', ''))
if not SB or not KEY:
    sys.exit('SUPABASE_URL / key not set')

H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

# ══ 2026-10-13 · WHY AN id MATCH IS NOT ENOUGH ══
# A receipt is frozen at publish time. Its source row is MUTABLE and gets
# regenerated. So `receipt.source_id == source.id` does not mean the two
# describe the same bet, and a grade disagreement is usually drift, not a
# mis-grade. Two measured examples:
#
#   receipt 20986  Greg Dulcich OVER 2.5 receptions  LOSS
#   prop_jerry_reads 92820  Greg Dulcich over 1.5    Win
#     → same player and family, line rewritten 2.5 → 1.5. Both grades are
#       correct FOR THEIR OWN LINE.
#
#   receipt 22020  BUF ML                            WIN   (BUF won 24-16)
#   jerry_reads 4685  Over 50.5 (total)              Loss  (40 points)
#     → same id, different MARKET entirely. Both correct.
#
# Comparing grades across that join produced 375 "conflicts" on the first run
# of this script, every one of them an artefact. So each source declares an
# identity check, and a grade is only ever compared — or patched — when the
# source row still describes the receipt's bet. Where identity cannot be
# established the row is counted as DRIFTED and left alone.
#
# `ledger_snapshots` is the one source that is immutable by contract
# (snapshot_ledger.py locks legs and odds at generation time), which is exactly
# why it is the only one that showed 232 agreements and 0 conflicts.


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _ident_ledger(rec: dict, src: dict) -> bool:
    """Frozen by contract. Confirm the ticket shape anyway."""
    legs = src.get('legs') or []
    n = len(legs) if isinstance(legs, list) else None
    if n is not None and f'{n} leg' not in str(rec.get('pick_label') or ''):
        return False
    ro, so = rec.get('pick_odds'), src.get('combined_odds')
    if ro is not None and so is not None and int(ro) != int(so):
        return False
    return True


def _ident_prop_jerry(rec: dict, src: dict) -> bool:
    if (str(rec.get('player_name') or '').strip().lower()
            != str(src.get('player_name') or '').strip().lower()):
        return False
    if (str(rec.get('prop_type') or '').strip().lower()
            != str(src.get('prop_type') or '').strip().lower()):
        return False
    return _num(rec.get('pick_line')) == _num(src.get('prop_line'))


def _ident_jerry_read(rec: dict, src: dict) -> bool:
    if (str(rec.get('market') or '').strip().lower()
            != str(src.get('call_market') or '').strip().lower()):
        return False
    rs, ss = rec.get('pick_side'), src.get('call_side')
    if rs and ss and str(rs).strip().upper() != str(ss).strip().upper():
        return False
    rl, sl = _num(rec.get('pick_line')), _num(src.get('call_line'))
    if rl is not None and sl is not None and rl != sl:
        return False
    return True


# source_table value on public_receipts → (table, key column, identity check)
#
# Only sources whose receipts carry the source row's `id` in source_id appear
# here. Two do NOT and are deliberately excluded — their source_id is a
# composite natural key, so joining them on `id` returned 100% orphan:
#     daily_best_bet_history  source_id='potd:2026-10-03'
#     mlb_pipeline_props      source_id='prop:Trevor Rogers|er_under|1.5'
# Those two embed the full identity in the key itself, so if they ever need a
# grade sync it must be keyed on that string, not on id. Neither showed a
# single ungraded-with-graded-source row, so there is nothing to fix today.
#
# daily_degen / daily_dawg have no `id` column at all, and jerry_cache.* is a
# cache blob rather than a table.
SOURCES: dict[str, tuple[str, str, object]] = {
    'ledger_snapshots': ('ledger_snapshots', 'id', _ident_ledger),
    'prop_jerry_reads': ('prop_jerry_reads', 'id', _ident_prop_jerry),
    'jerry_reads':      ('jerry_reads', 'id', _ident_jerry_read),
}

# Source tables spell the grade differently. Normalise to the receipt's
# vocabulary. NOTE: `result` on public_receipts is TITLE case nowhere — it is
# upper (WIN/LOSS/PUSH); see feedback_result_column_is_title_case for the
# surface that is NOT.
_RMAP = {
    'w': 'WIN', 'win': 'WIN', 'won': 'WIN',
    'l': 'LOSS', 'loss': 'LOSS', 'lost': 'LOSS', 'lose': 'LOSS',
    'p': 'PUSH', 'push': 'PUSH', 'tie': 'PUSH',
    'void': 'VOID', 'ungradeable': 'UNGRADEABLE',
    'no_action': 'NO_ACTION', 'no-pick': 'NO_ACTION', 'no play': 'NO_ACTION',
    'tracked_elsewhere': 'TRACKED_ELSEWHERE',
}

# A source row can say "not graded yet" in words rather than with a NULL.
# `daily_best_bet_history` holds 5 rows reading 'Pending'. Treating that as a
# grade would stamp a receipt with a non-terminal state and, because this
# script only ever fills NULLs, it could never be corrected afterwards.
_NOT_A_GRADE = {'pending', 'in_progress', 'in progress', 'live', 'tbd', 'none',
                'null', 'n/a', '-'}


def normalise(raw) -> str | None:
    """Source grade → the receipt vocabulary, or None if it isn't a grade yet.

    Returns None for 'Pending'-style values so they are counted as ungraded
    rather than patched in.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in _NOT_A_GRADE:
        return None
    return _RMAP.get(s.lower(), s.upper())


def paged(table: str, params: dict) -> list[dict]:
    """Full table read. 20+ scripts in this repo have been caught measuring
    from a silently truncated 1000-row page."""
    out: list[dict] = []
    off = 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ,
                         params=p, timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ⚠ read {table} {r.status_code}: {r.text[:200]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def sync_source(recv_source: str, apply: bool) -> dict:
    table, keycol, ident = SOURCES[recv_source]
    # select='*' deliberately. An explicit SELECT naming a column this table
    # does not have returns 400, which every caller in this repo has at some
    # point read as "no rows".
    src = {str(x.get(keycol)): x for x in paged(table, {'select': '*'})}
    recs = paged('public_receipts', {'select': '*',
                                     'source_table': f'eq.{recv_source}'})

    stale, conflict = [], []
    agree = both_null = orphan = drifted = 0
    for r in recs:
        s = src.get(str(r.get('source_id')))
        if s is None:
            orphan += 1
            continue
        if not ident(r, s):
            # The source row no longer describes this bet, so its grade says
            # nothing about this receipt in either direction.
            drifted += 1
            continue
        # Normalise BOTH sides. The first cut of this script normalised only
        # the source and compared it to the receipt's raw value, which turned
        # every title-cased 'Win' receipt into a fake CONFLICT — 571 of them on
        # prop_jerry_reads alone. public_receipts.result is not consistently
        # cased across writers (see feedback_result_column_is_title_case), so a
        # case-sensitive comparison here measures the casing, not the grade.
        exp = normalise(s.get('result'))
        got = normalise(r.get('result'))
        if exp is None and got is None:
            both_null += 1
        elif got is None:
            stale.append((r, s, exp))
        elif exp is not None and exp != got:
            conflict.append((r, exp, got))
        else:
            agree += 1

    print(f'\n=== {recv_source}')
    print(f'  receipts={len(recs)}  source rows={len(src)}')
    print(f'  agree={agree}  both ungraded={both_null}  orphan={orphan}')
    print(f'  DRIFTED (source row no longer the same bet) = {drifted}')
    print(f'  STALE (source graded, receipt NULL) = {len(stale)}')
    print(f'  CONFLICT (same bet, both graded, disagree)  = {len(conflict)}')
    for r, exp, got in conflict[:25]:
        print(f'    ⚠ CONFLICT receipt {r["id"]} {r["game_date"]} '
              f'src={r["source_id"]} {str(r.get("pick_label"))[:34]!r} '
              f'receipt={got} source={exp} — NOT patched, needs a human')

    patched = 0
    for r, s, exp in stale:
        body = {'result': exp}
        if s.get('graded_at'):
            body['graded_at'] = s['graded_at']
        if not apply:
            continue
        # PATCH by the receipt's own primary key. A filter on the composite
        # natural key needs URL-encoding and has eaten writes here before.
        pr = requests.patch(f'{SB}/rest/v1/public_receipts?id=eq.{r["id"]}',
                            headers=H_WRITE, json=body, timeout=30)
        if pr.status_code in (200, 204):
            patched += 1
        else:
            # A PATCH naming a column the table lacks drops the WHOLE patch
            # with PGRST204 — so a failure here is loud, never swallowed.
            print(f'    ⚠ patch receipt {r["id"]} {pr.status_code}: '
                  f'{pr.text[:200]}')
    if apply:
        print(f'  patched {patched}/{len(stale)}')
    elif stale:
        print(f'  would patch {len(stale)} (dry-run)')
        for r, s, exp in sorted(stale, key=lambda t: str(t[0]['game_date']))[-10:]:
            print(f'    {r["game_date"]} receipt={r["id"]} '
                  f'src={r["source_id"]} NULL -> {exp}')
    return {'stale': len(stale), 'conflict': len(conflict), 'patched': patched}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', choices=list(SOURCES) + ['all'], default='all')
    ap.add_argument('--apply', action='store_true',
                    help='without this the script only measures')
    a = ap.parse_args()

    targets = list(SOURCES) if a.source == 'all' else [a.source]
    if not a.apply:
        print('DRY RUN — nothing is written. Re-run with --apply.')
    tot = {'stale': 0, 'conflict': 0, 'patched': 0}
    for t in targets:
        r = sync_source(t, a.apply)
        for k in tot:
            tot[k] += r[k]
    print(f'\nTOTAL  stale={tot["stale"]}  conflict={tot["conflict"]}  '
          f'patched={tot["patched"]}')
    if tot['conflict']:
        print('A CONFLICT means the receipt and its source carry DIFFERENT '
              'grades. Nothing was patched for those — resolve them by hand.')


if __name__ == '__main__':
    main()
