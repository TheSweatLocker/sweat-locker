#!/usr/bin/env python3
"""Reconcile public_receipts(surface=potd) against daily_best_bet_history.

== 2026-10-16 - WHY ==
Yesterday's POTD fix corrected daily_best_bet_history and MISSED
public_receipts, which is the surface the published record is read from. Found
during the morning audit:

    public_receipts 2026-10-15  "Cleveland Guardians ML"   result=WIN
    daily_best_bet  2026-10-15  "Los Angeles Dodgers ML"   result=Loss

Both wrong on the receipt side: the wrong pick AND a win on a game that was
never the published bet. The receipt was written at 21:10:15, the same instant
as the false grade, by the same partial mirror. Three independent sources agree
the published POTD was Dodgers ML -- jerry_cache (Glasnow narrative),
daily_best_bet_history, and content/2026-10-15_card.md -- and the Brewers won
6-4, so it lost.

daily_best_bet_history is treated as the source of record here, for the reason
grade_potd.py:444 already gives: "Reads daily_best_bet_history because THAT is
what the calendar renders and what the published record counts -- jerry_cache
disagreed with it on both dates they differed (08-22, 09-19) and was wrong both
times."

Checks EVERY potd receipt, not just yesterday's, because a divergence this
quiet is unlikely to be unique.

USAGE
    python fix_potd_receipt_divergence.py --dry-run
    python fix_potd_receipt_divergence.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}
#: public_receipts uses upper case on every surface except a handful of
#: prop_jerry/sweat_card rows; the record calc lowercases anyway
#: (compute_surface_records.py:165), so upper is the house style here.
TO_RECEIPT = {'Win': 'WIN', 'Loss': 'LOSS', 'Push': 'PUSH'}


def page(t, p):
    out, off = [], 0
    while True:
        q = dict(p, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def nk(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', s.lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    ap.add_argument('--only-date',
                    help='restrict writes to this bet_date. Used deliberately: '
                         'the historical divergences (09-23, 09-29, 10-05) need '
                         'a human to say which surface published what, and a '
                         'bulk overwrite of the receipts ledger is exactly the '
                         'wrong instinct.')
    a = ap.parse_args()

    hist = {str(x.get('bet_date'))[:10]: x
            for x in page('daily_best_bet_history', {'select': '*'})}
    rec = [x for x in page('public_receipts', {'select': '*'})
           if str(x.get('surface')) == 'potd']
    print(f'potd receipts {len(rec)}, ledger rows {len(hist)}')

    label_bad, result_bad = [], []
    for r in rec:
        d = str(r.get('game_date'))[:10]
        h = hist.get(d)
        if not h:
            continue
        hl, rl = str(h.get('lean') or ''), str(r.get('pick_label') or '')
        # A receipt whose label shares no team/player wording with the ledger
        # lean is describing a different bet.
        if hl and rl and nk(hl)[:14] not in nk(rl) and nk(rl)[:14] not in nk(hl):
            label_bad.append((d, r, rl, hl))
        want = TO_RECEIPT.get(str(h.get('result')))
        have = str(r.get('result') or '')
        if want and have.upper() not in ('', 'NONE') and have.upper() != want:
            result_bad.append((d, r, have, want))
        elif want and have.upper() in ('', 'NONE'):
            result_bad.append((d, r, have or 'None', want))

    print(f'\nLABEL divergence (receipt describes a different bet): '
          f'{len(label_bad)}')
    for d, _r, rl, hl in label_bad:
        print(f'  {d}  receipt={rl[:42]!r}')
        print(f'        ledger ={hl[:42]!r}')
    print(f'\nRESULT divergence: {len(result_bad)}')
    for d, _r, have, want in result_bad:
        print(f'  {d}  receipt={have:<10} ledger says {want}')

    if a.dry_run:
        print('\nDRY RUN — nothing written')
        return 0

    if a.only_date:
        label_bad = [t for t in label_bad if t[0] == a.only_date]
        result_bad = [t for t in result_bad if t[0] == a.only_date]
        print(f'SCOPED to {a.only_date}: {len(label_bad)} label, '
              f'{len(result_bad)} result')
    touched = {}
    for d, r, _rl, hl in label_bad:
        touched.setdefault(r['id'], {})['pick_label'] = hl
    for d, r, _have, want in result_bad:
        touched.setdefault(r['id'], {})['result'] = want
    for rid, patch in touched.items():
        w = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_WRITE,
                           params={'id': f'eq.{rid}'},
                           data=json.dumps(patch), timeout=60)
        print(f'  id={rid} {patch} -> HTTP {w.status_code}')

    print('\nREAD BACK (potd receipts, last 5):')
    after = [x for x in page('public_receipts', {'select': '*'})
             if str(x.get('surface')) == 'potd']
    for x in sorted(after, key=lambda y: str(y.get('game_date')))[-5:]:
        d = str(x.get('game_date'))[:10]
        led = hist.get(d, {})
        print(f"  {d}  {str(x.get('pick_label'))[:40]:<41} "
              f"receipt={str(x.get('result')):<6} ledger={led.get('result')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
