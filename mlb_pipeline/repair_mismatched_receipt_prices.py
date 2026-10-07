#!/usr/bin/env python3
"""Remove prices that were copied from a read describing a DIFFERENT bet.

WHY (2026-10-06)
----------------
backfill_receipt_prices.py filled `public_receipts.pick_odds` from
`jerry_reads.price_american`, keyed on the receipt's `source_id`. That is only
valid while the cited read still describes the same bet, and often it does not:
jerry_reads has 41 writers and no row-level write protection (B41), so rows get
rewritten with a different call after a receipt has frozen.

Measured across all 1,340 receipts citing jerry_reads:

    agrees with its cited read      1,091   81.4%
    market differs                    186
    side and/or line differs           63
    cited row is gone                   2

Of the 330 receipts that the backfill priced from jerry_reads, **174 cite a
read that no longer matches the bet**, and 159 of those are graded. The errors
are not marginal:

    DET -9.5          priced -300   from an ML read   (a spread is ~-110)
    Boise State ML    priced -290   from a read on the OTHER side
    Alabama -11.5     priced -110   from a read on South Carolina +12.5
    Over 42.5         priced -108   from a read calling Under 43.5

A price belonging to a different bet is worse than a missing one: ROI is
computed from it, so it silently moves the published record. "DET -9.5 at -300"
makes a winning spread look like a losing proposition.

WHAT THIS DOES NOT TOUCH
------------------------
Nothing about the PICK or its RESULT. The grades are sound — grade_public_
receipts resolves from the receipt's own pick_label and pick_line, never from
the source row ("The LABEL is authoritative for the sign"). So the record of
what we said and whether it won is unaffected.

These prices were also NOT part of the original publication. Every one was
written by a backfill on 2026-10-04 and stamped audit.price_source =
'jerry_reads.price_american'. This repairs that backfill's own output; it is
not a revision of a published pick. Only rows carrying that stamp are
considered, so a price captured at publish time can never be touched.

For each mismatch it NULLs pick_odds and tries to re-derive the real price from
line_history via pick_price.resolve, which is keyed on the receipt's OWN
market/side/line and so cannot drift. line_history starts 2026-09-23, so
earlier rows end up with no price — which is the honest state, not a loss.

    python repair_mismatched_receipt_prices.py           # report only
    python repair_mismatched_receipt_prices.py --apply
"""
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
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    print(f'=== repair_mismatched_receipt_prices · '
          f'{"APPLY" if args.apply else "DRY"} ===\n')

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

    if not args.apply:
        print(f'\n{len(fixes)} row(s) would change. Re-run with --apply.')
        return 0

    ok = failed = 0
    for rec, why, p, book, reason in fixes:
        body = {'pick_odds': p}
        a = _audit(rec)
        a['price_source'] = (f'line_history.price[{book}]' if p is not None
                             else None)
        a['price_basis'] = 'publish' if p is not None else None
        a['price_repaired_2026_10_06'] = why
        body['audit'] = a
        r = requests.patch(f'{SB}/rest/v1/public_receipts',
                           headers=H_W, timeout=60,
                           params={'id': f'eq.{rec["id"]}'},
                           data=json.dumps(body))
        # A 204 is not a write: require the row back and check the value.
        good = False
        if r.status_code in (200, 204):
            try:
                back = r.json() if r.text else []
            except ValueError:
                back = []
            if isinstance(back, list) and back:
                got = back[0].get('pick_odds')
                good = (got is None) if p is None else (_num(got) == _num(p))
        if good:
            ok += 1
        else:
            failed += 1
            print(f'    ! id={rec["id"]} not confirmed '
                  f'({r.status_code}): {r.text[:120]}')
    print(f'\nrepaired {ok}/{len(fixes)} (verified by read-back), {failed} failed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
