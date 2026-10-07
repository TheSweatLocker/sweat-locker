#!/usr/bin/env python3
"""DETECT prices that were copied from a read describing a DIFFERENT bet.

══ 2026-10-06 · THIS CANNOT REPAIR ANYTHING, AND THAT IS CORRECT ══
I wrote this as a repair and it is a no-op. public_receipts is immutable at
the DATABASE level (migration 20260918b_receipt_enrichment_allow): the
identity fields are frozen outright, and tier / conviction / pick_odds /
matchup / audit are SET-ONCE — NULL to a value is allowed, value to any
other value is silently reverted by trg_freeze_receipt_identity. Only
result, actual_value and graded_at stay mutable.

A PATCH therefore returns 200 WITH THE ROW and changes nothing. My first
run reported "repaired 24/174"; all 24 were false positives where the
re-derived line_history price happened to equal the stored one, so the
read-back matched while nothing had been written. The real figure is 0 of
174, and it always would have been.

That is the ledger working as designed, and it is the answer to "how do we
know what is right": a published pick cannot be rewritten after the fact.
The 174 wrong prices were written as the FIRST value by the 10-04 backfill
(NULL to value, which the trigger allows) and are now permanent.

So this file detects and reports them. Two things follow from it:
  * no NEW ones can be written — backfill_receipt_prices._jr_price now
    refuses a read whose market/side/line no longer matches the receipt
  * the existing 174 must be excluded at READ time rather than corrected,
    which is what exclude_ids() below is for.
"""
# ── Original diagnosis, kept verbatim ──────────────────────────
# Remove prices that were copied from a read describing a DIFFERENT bet.
#
# WHY (2026-10-06)
# ----------------
# backfill_receipt_prices.py filled `public_receipts.pick_odds` from
# `jerry_reads.price_american`, keyed on the receipt's `source_id`. That is only
# valid while the cited read still describes the same bet, and often it does not:
# jerry_reads has 41 writers and no row-level write protection (B41), so rows get
# rewritten with a different call after a receipt has frozen.
#
# Measured across all 1,340 receipts citing jerry_reads:
#
#     agrees with its cited read      1,091   81.4%
#     market differs                    186
#     side and/or line differs           63
#     cited row is gone                   2
#
# Of the 330 receipts that the backfill priced from jerry_reads, **174 cite a
# read that no longer matches the bet**, and 159 of those are graded. The errors
# are not marginal:
#
#     DET -9.5          priced -300   from an ML read   (a spread is ~-110)
#     Boise State ML    priced -290   from a read on the OTHER side
#     Alabama -11.5     priced -110   from a read on South Carolina +12.5
#     Over 42.5         priced -108   from a read calling Under 43.5
#
# A price belonging to a different bet is worse than a missing one: ROI is
# computed from it, so it silently moves the published record. "DET -9.5 at -300"
# makes a winning spread look like a losing proposition.
#
# WHAT THIS DOES NOT TOUCH
# ------------------------
# Nothing about the PICK or its RESULT. The grades are sound — grade_public_
# receipts resolves from the receipt's own pick_label and pick_line, never from
# the source row ("The LABEL is authoritative for the sign"). So the record of
# what we said and whether it won is unaffected.
#
# These prices were also NOT part of the original publication. Every one was
# written by a backfill on 2026-10-04 and stamped audit.price_source =
# 'jerry_reads.price_american'. This repairs that backfill's own output; it is
# not a revision of a published pick. Only rows carrying that stamp are
# considered, so a price captured at publish time can never be touched.
#
# For each mismatch it NULLs pick_odds and tries to re-derive the real price from
# line_history via pick_price.resolve, which is keyed on the receipt's OWN
# market/side/line and so cannot drift. line_history starts 2026-09-23, so
# earlier rows end up with no price — which is the honest state, not a loss.
#
#     python repair_mismatched_receipt_prices.py           # report
#     python repair_mismatched_receipt_prices.py --ids     # ids, for exclusion

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

import requests

import pick_price

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

#: only rows the backfill wrote from jerry_reads are in scope
BACKFILL_STAMP = 'jerry_reads.price_american'


def page(table, params):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q,
                         timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code}: {r.text[:200]}')
            return out
        chunk = r.json()
        if not isinstance(chunk, list):
            return out
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _audit(rec):
    a = rec.get('audit') or {}
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            a = {}
    return a if isinstance(a, dict) else {}


def mismatch_reason(rec, read):
    """Why this read cannot price this receipt, or None if it can."""
    if read is None:
        return 'cited read no longer exists'
    rm = str(rec.get('market') or '').strip().lower()
    jm = str(read.get('call_market') or '').strip().lower()
    if rm and jm and rm != jm:
        return f'market: receipt {rm} vs read {jm}'
    rs = str(rec.get('pick_side') or '').strip().upper()
    js = str(read.get('call_side') or '').strip().upper()
    if rs and js and rs != js:
        return f'side: receipt {rs} vs read {js}'
    rl, jl = _num(rec.get('pick_line')), _num(read.get('call_line'))
    if rl is not None and jl is not None and abs(rl - jl) > 0.01:
        return f'line: receipt {rl} vs read {jl}'
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ids', action='store_true',
                    help='print only the affected receipt ids')
    args = ap.parse_args()
    print('=== mismatched receipt prices · DETECT ONLY ===')
    print('    (public_receipts is set-once at the DB level — nothing here '
          'can be rewritten)\n')

    reads = {str(j['id']): j for j in page(
        'jerry_reads', {'select': 'id,call_market,call_side,call_line,'
                                  'call_text,price_american'})}
    print(f'jerry_reads indexed: {len(reads)}')

    recs = [r for r in page('public_receipts', {'select': '*'})
            if str(r.get('source_table')) == 'jerry_reads']
    scoped = [r for r in recs
              if _audit(r).get('price_source') == BACKFILL_STAMP
              and r.get('pick_odds') is not None]
    print(f'receipts citing jerry_reads: {len(recs)}')
    print(f'  priced by the backfill from jerry_reads: {len(scoped)}\n')

    bad = []
    for rec in scoped:
        why = mismatch_reason(rec, reads.get(str(rec.get('source_id'))))
        if why:
            bad.append((rec, why))

    print(f'=== mismatched prices: {len(bad)} of {len(scoped)} ===')
    kinds = collections.Counter(w.split(':')[0] for _r, w in bad)
    for k, v in kinds.most_common():
        print(f'    {k:<34} {v}')
    graded = sum(1 for r, _w in bad if r.get('result'))
    print(f'    graded (ROI already built on these): {graded}')

    # re-derive from line_history where it can be done honestly
    fixes = []
    for rec, why in bad:
        got = pick_price.resolve(rec.get('game_id'), rec.get('market'),
                                 rec.get('pick_side'),
                                 line=rec.get('pick_line'),
                                 as_of=rec.get('published_at'))
        fixes.append((rec, why, got.get('price'), got.get('book'),
                      got.get('reason')))
    rederived = sum(1 for _r, _w, p, _b, _x in fixes if p is not None)
    print(f'\n    re-derivable from line_history: {rederived}')
    print(f'    will end with NO price (honest blank): {len(fixes) - rederived}')

    print('\n--- sample ---')
    for rec, why, p, book, reason in fixes[:12]:
        was = rec.get('pick_odds')
        now = f'{p} [{book}]' if p is not None else f'NULL ({reason})'
        print(f'    {str(rec.get("game_date"))[:10]} '
              f'{str(rec.get("pick_label"))[:22]:22s} '
              f'was={str(was):>6} -> {now}   [{why}]')

    if args.ids:
        print('\n--- affected receipt ids (for read-time exclusion) ---')
        for rec, _w, _p, _b, _x in fixes:
            print(rec['id'])
        return 0

    print(f'\n{len(bad)} receipt(s) carry a price taken from a read that no '
          f'longer describes the bet.')
    print('They CANNOT be corrected in place: public_receipts is set-once at')
    print('the DB level (trg_freeze_receipt_identity), so a PATCH returns 200')
    print('and changes nothing — verified. The 24 my first run claimed to have')
    print('repaired were rows where the re-derived price happened to equal the')
    print('stored one; the true figure is 0 of 174.')
    print('\nSo they are excluded at READ time instead — exclude_ids() below,')
    print('used by compute_surface_records. The pick and the result stay in')
    print('the W-L record; only the unusable price leaves the ROI.')
    return 0


def exclude_ids() -> set:
    """Receipt ids whose stored price belongs to a different bet.

    Called by compute_surface_records so those receipts count toward the
    WIN/LOSS record (the pick and its result are sound — the grader reads the
    receipt's own label, never the source row) but are left out of any UNITS
    or ROI figure, exactly as they would have been had the backfill never
    written a price for them.

    Returns an empty set on any failure: a records job must not silently drop
    picks because a lookup broke.
    """
    try:
        reads = {str(j['id']): j for j in page(
            'jerry_reads', {'select': 'id,call_market,call_side,call_line'})}
        out = set()
        for rec in page('public_receipts', {'select': 'id,market,pick_side,'
                                                      'pick_line,audit,'
                                                      'source_table,source_id,'
                                                      'pick_odds'}):
            if str(rec.get('source_table')) != 'jerry_reads':
                continue
            if rec.get('pick_odds') is None:
                continue
            if _audit(rec).get('price_source') != BACKFILL_STAMP:
                continue
            if mismatch_reason(rec, reads.get(str(rec.get('source_id')))):
                out.add(rec['id'])
        return out
    except Exception as e:                              # noqa: BLE001
        print(f'  ! exclude_ids failed ({e}) — excluding nothing')
        return set()


if __name__ == '__main__':
    raise SystemExit(main())
