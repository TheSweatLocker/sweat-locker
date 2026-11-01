#!/usr/bin/env python3
"""Collapse the two source_ids a single card bet could have, into one.

== 2026-10-31 - THE LEDGER SAID 7-0; THE CARD SAID 5-0 ==

ANDY: "Also says 5-0 yesterday but on sweat card recap below only shows 3
plays and says 3-0" was the 10-29 version of this. Tonight the 10-30 NBA
card reads 5 picks / 5-0 in the blob the app renders, and SEVEN live WINs in
public_receipts -- the table surface_records and every trust figure are
computed from.

THE MECHANISM. public_receipt.sweat_card_rows built its anchor from
`source_key`, which is composed by whichever engine supplied the pick. The
fade's shape is three-part, 'fade:401909281:rl'. The strip removed only the
FIRST segment, leaving '401909281:rl'. The same pick arriving without a
source_key -- which is what reconcile_card_recap_to_receipts recomposes --
anchored on the bare game_id, '401909281'. Two source_ids, one bet. The
partial unique index on (sport, surface, game_date, source_id) is doing its
job; it simply cannot see that the two keys name one wager.

Orlando Magic +5.5 and Golden State Warriors +3.5 were each counted twice on
2026-10-30, both WINs. Inflation in the WIN direction, on the surface the
brand rests on.

_card_anchor now prefers game_id for every market but prop, so both writers
produce one id. This migrates the rows written before that.

WHAT IT DOES, per sweat_card receipt whose source_id ends in ':{market}':
  * compute the normalised id (drop the market tail)
  * if a LIVE peer already holds that id on the same (sport, game_date):
      - REFUSE if the two disagree on `result` -- a disagreement is a
        grading bug, not a duplicate, and must not be papered over
        (feedback_grade_each_row_not_the_date)
      - otherwise VOID the RECONSTRUCTED / later-published copy and keep the
        live one, because published_at is the evidence we showed the pick
        before the game. Void first, so the index lets the rename through.
  * rename the survivor to the normalised id
  * confirm by READBACK. A refused PATCH returns 200/204 having changed
    nothing (feedback_204_is_not_a_write), so the count is never trusted.

It never touches a graded result, never deletes, and never merges rows whose
results differ.

USAGE
  python normalize_card_receipt_anchor.py              # dry run
  python normalize_card_receipt_anchor.py --apply
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

VOIDS = ('VOID', 'VOIDED', 'NO_ACTION')
SEL = ('id,sport,surface,game_date,game_id,market,pick_label,pick_line,'
       'pick_odds,result,source_id,published_at,capture_mode,audit')


def page(path, params):
    out, off = [], 0
    while off < 60000:
        p = dict(params)
        p.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H_R, timeout=120,
                         params=p)
        if r.status_code != 200:
            print(f'  ! {path} HTTP {r.status_code} {r.text[:160]}')
            return out
        b = r.json()
        if not b:
            break
        out.extend(b)
        off += 1000
    return out


def live(x) -> bool:
    return str(x.get('result') or '').upper() not in VOIDS


def readback(rid):
    r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R, timeout=60,
                     params={'select': SEL, 'id': f'eq.{rid}'})
    j = r.json() if r.status_code == 200 else []
    return j[0] if j else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    rows = page('public_receipts', {'select': SEL, 'surface': 'eq.sweat_card',
                                    'order': 'game_date.asc'})
    print(f'=== {len(rows)} sweat_card receipts ===')

    tails = []
    for x in rows:
        sid = str(x.get('source_id') or '')
        mkt = str(x.get('market') or '')
        if mkt and sid.endswith(':' + mkt) and sid != mkt:
            tails.append(x)
    print(f'source_id carries a redundant ":{{market}}" tail: {len(tails)}')
    if not tails:
        print('  nothing to migrate')
        return 0

    by_key = collections.defaultdict(list)
    for x in rows:
        by_key[(x['sport'], x['game_date'], str(x.get('source_id')))].append(x)

    voids, renames, refusals = [], [], []
    for x in tails:
        sid = str(x['source_id'])
        norm = sid[:-(len(str(x['market'])) + 1)]
        peers = [y for y in by_key.get((x['sport'], x['game_date'], norm), [])
                 if y['id'] != x['id'] and live(y)]
        if not peers:
            renames.append((x, norm, None))
            continue
        if len(peers) > 1:
            refusals.append((x, norm, f'{len(peers)} live peers -- not a pair'))
            continue
        p = peers[0]
        rx = str(x.get('result') or '').upper()
        rp = str(p.get('result') or '').upper()
        if rx != rp:
            refusals.append((x, norm,
                             f'results DISAGREE ({rx or "null"} vs '
                             f'{rp or "null"}) -- grading bug, not a dup'))
            continue
        # keep the receipt that proves we published before the game
        def _recon(r):
            return str(r.get('capture_mode') or '') == 'reconstructed'
        if _recon(p) and not _recon(x):
            drop, keep = p, x
        elif _recon(x) and not _recon(p):
            drop, keep = x, p
        else:
            drop, keep = ((p, x)
                          if str(p.get('published_at') or '')
                          > str(x.get('published_at') or '') else (x, p))
        voids.append((drop, keep))
        if keep['id'] == x['id']:
            renames.append((x, norm, drop['id']))

    print(f'\n=== PLAN ===')
    print(f'  void (duplicate of a kept row): {len(voids)}')
    for d, k in voids:
        print(f'    void  id {d["id"]} {d["sport"]:<5} {d["game_date"]} '
              f'{str(d["pick_label"])[:26]:<26} src={d["source_id"]:<26} '
              f'mode={d.get("capture_mode")} pub={str(d.get("published_at"))[:16]}')
        print(f'      keep id {k["id"]} src={k["source_id"]:<26} '
              f'mode={k.get("capture_mode")} pub={str(k.get("published_at"))[:16]}')
    print(f'  rename to the canonical id: {len(renames)}')
    for x, norm, _ in renames:
        print(f'    {x["sport"]:<5} {x["game_date"]} '
              f'{str(x["pick_label"])[:26]:<26} {x["source_id"]} -> {norm}')
    if refusals:
        print(f'  REFUSED: {len(refusals)}')
        for x, norm, why in refusals:
            print(f'    {x["sport"]:<5} {x["game_date"]} '
                  f'{str(x["pick_label"])[:26]:<26} {why}')

    if not args.apply:
        print('\n  dry run -- rerun with --apply')
        return 0

    ok_v = ok_r = 0
    # void first: it frees the canonical id under the partial unique index
    for d, k in voids:
        aud = d.get('audit')
        aud = aud if isinstance(aud, dict) else {}
        aud['void'] = {
            'at': '2026-10-31', 'by': 'normalize_card_receipt_anchor',
            'reason': (f'duplicate identity for one bet: this row held '
                       f'{d["source_id"]!r} while receipt {k["id"]} held '
                       f'{k["source_id"]!r} for the same game, market, label '
                       f'and line. Both were live and both counted. Keeping '
                       f'{k["id"]} ({k.get("capture_mode")}, published '
                       f'{str(k.get("published_at"))[:16]}).')}
        r = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                           timeout=60, params={'id': f'eq.{d["id"]}'},
                           json={'result': 'VOID', 'audit': aud})
        back = readback(d['id'])
        if (r.status_code in (200, 204) and back
                and str(back.get('result') or '').upper() in VOIDS):
            ok_v += 1
            print(f'  voided {d["id"]} -> {back.get("result")}')
        else:
            print(f'  x void {d["id"]} HTTP {r.status_code} '
                  f'readback result={back and back.get("result")!r}')

    for x, norm, _ in renames:
        r = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                           timeout=60, params={'id': f'eq.{x["id"]}'},
                           json={'source_id': norm})
        back = readback(x['id'])
        if (r.status_code in (200, 204) and back
                and str(back.get('source_id')) == norm):
            ok_r += 1
            print(f'  renamed {x["id"]} -> {norm}')
        else:
            print(f'  x rename {x["id"]} HTTP {r.status_code} '
                  f'readback source_id={back and back.get("source_id")!r} '
                  f'(wanted {norm!r}) {r.text[:120]}')

    print(f'\n  voided {ok_v}/{len(voids)}   renamed {ok_r}/{len(renames)}')
    return 0 if (ok_v == len(voids) and ok_r == len(renames)) else 1


if __name__ == '__main__':
    raise SystemExit(main())
