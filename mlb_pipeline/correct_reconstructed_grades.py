#!/usr/bin/env python3
"""Correct prop receipts whose stored grade disagrees with the box score.

WHY (2026-10-04)
----------------
settle_prop_receipts can now settle from mlb_pipeline_props.final_value — our
own ingested box score — instead of the live MLB gameLog API, which answers
nothing for these dates. Running --validate over 3,368 graded prop receipts
since 09-20 produced 94.2% agreement and 182 disagreements.

Those disagreements are not settler bugs. EVERY ONE that can be checked by
arithmetic has the STORED value wrong:

    Derrick Henry  rush_yds OVER  88.5   actual  89.0  stored LOSS  truth WIN
    Deebo Samuel   rec_yds  OVER  32.5   actual  31.0  stored WIN   truth LOSS
    Drew Lock      pass_yds UNDER 206.5  actual 235.0  stored WIN   truth LOSS

Root cause: ALL 3,368 of those receipts are capture_mode='reconstructed' —
not one was captured live. The stored results are themselves post-hoc
reconstructions, so they drift from the box score.

WHAT THIS CORRECTS, AND WHAT IT REFUSES
---------------------------------------
Corrects ONLY families that have never carried a FADE verdict, where pick_side
is unambiguously the side we backed. 59 receipts:

    NO_ACTION -> WIN   22        LOSS -> WIN   7
    NO_ACTION -> LOSS  22        WIN  -> LOSS  7
    VOID      -> LOSS   1

Net +22 WIN / +23 LOSS — near-symmetric, which is what an honest correction
looks like. Two thirds are rows stamped NO_ACTION that actually resolved.

REFUSES the 123 fade-ambiguous ones (PASS|fadeable 97, FADE|fadeable 25,
PRIME|fadeable 1). On those, pick_side records the PROP's side rather than the
side we backed, and the source read that would disambiguate is deleted.
Settling them would be a coin flip on a published result.

THE UPSTREAM DEFECT, which this does not fix: the receipt writer stores the
prop's side instead of the backed side on a FADE read. Fix that and this whole
ambiguous class stops being created.

    python correct_reconstructed_grades.py --since 2026-09-20
    python correct_reconstructed_grades.py --since 2026-09-20 --apply
"""
from __future__ import annotations
import argparse, collections, json, os, sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

from settle_prop_receipts import settle, fadeable_families, source_verdicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-09-20')
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    recs, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H, timeout=120,
                         params={'select': '*', 'market': 'eq.prop',
                                 'result': 'not.is.null',
                                 'game_date': f'gte.{args.since}',
                                 'limit': 1000, 'offset': off})
        r.raise_for_status()
        b = r.json()
        recs += b
        if len(b) < 1000:
            break
        off += 1000
    print(f'=== graded prop receipts since {args.since}: {len(recs)} ===\n')

    fam = fadeable_families(args.sport, '2026-06-01')
    vmap = source_verdicts(recs)

    fixes, refused = [], collections.Counter()
    for rc in recs:
        res, actual, _w = settle(rc, fam, vmap)
        if not res:
            continue
        stored = str(rc.get('result') or '').strip().upper()
        got = str(res).strip().upper()
        if stored == got:
            continue
        pt = str(rc.get('prop_type') or '')
        v = vmap.get(str(rc.get('source_id')))
        verdict = (v[0] if v and v[0] else None)
        if pt in fam or verdict == 'FADE':
            refused[f'{verdict or "NO_VERDICT"}|fadeable'] += 1
            continue
        fixes.append((rc, stored, got, actual))

    print(f'correctable (side unambiguous): {len(fixes)}')
    flips = collections.Counter(f'{s}->{g}' for _, s, g, _ in fixes)
    for k, n in flips.most_common():
        print(f'   {k:<18} {n}')
    print(f'\nrefused (fade-ambiguous): {sum(refused.values())} {dict(refused)}')

    if not args.apply:
        print('\n--- sample ---')
        for rc, s, g, a in fixes[:8]:
            print(f'   {str(rc.get("player_name"))[:20]:20s} '
                  f'{str(rc.get("prop_type")):18s} {str(rc.get("pick_side")):6s} '
                  f'{str(rc.get("pick_line")):6s} actual={a}  {s} -> {g}')
        print(f'\n{len(fixes)} to correct — re-run with --apply')
        return 0

    ok = bad = 0
    for rc, stored, got, actual in fixes:
        payload = {'result': got}
        if actual is not None:
            payload['actual_value'] = actual
        pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W, timeout=60,
                            params={'id': f'eq.{rc["id"]}'}, data=json.dumps(payload))
        body = pr.json() if pr.content else []
        # Verify the VALUE came back, not just that a row did — this table
        # silently refuses some columns and a 200 proves nothing on its own.
        if pr.status_code not in (200, 204) or not body:
            print(f'   x write failed id={rc["id"]} {pr.status_code}')
            bad += 1
            continue
        if str(body[0].get('result') or '').strip().upper() != got:
            print(f'   x REFUSED id={rc["id"]} came back '
                  f'{body[0].get("result")!r}, wanted {got!r}')
            bad += 1
            continue
        ok += 1
    print(f'\ncorrected {ok} of {len(fixes)} (result verified by read-back), '
          f'{bad} failed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
