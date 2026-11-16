#!/usr/bin/env python3
"""Backfill the Sharp's prop receipts that sharp_card_rows silently dropped.

== WHY (2026-11-16) =========================================================
Andy: "back fill find out why reciepts ar emissing for 18 days, this is unsat".

generate_sharp_card publishes to jerry_cache.sharp_card_{date} -- the payload
the app renders -- and captures a live receipt in the same breath. The record
Andy quotes for The Sharp is computed from those receipts, via
daily_surface_records. public_receipt.sharp_card_rows read the market from
`it['type']` ALONE.

The measured-cell prop path added on 2026-11-01 emits a different item shape:

    10-28 item: type='prop'  market=None   (+ matchup, reason, conviction)
    11-02 item: type=None    market='prop' (+ game, why, cell_roi, cell_n)

With no `type` key every prop leg hit a bare `continue`. It was published to
users and never receipted, so it produced no leg in daily_surface_records and
the public record counted the card's SIDES and silently dropped its PROPS:

    stated (sides only)   10-9    52.6%   +2.0% ROI   n=19   (11-08..11-15)
    true shipped card     13-15   46.4%  -10.2% ROI   n=28

Fixed forward in the builder (0aa1f9a3). This repairs the rows already lost.

== WHAT THIS DOES AND DOES NOT WRITE =======================================
13 prop legs over 6 dates: 11-02 (3), 11-05, 11-08, 11-09, 11-12, 11-15 (2 ea).

It does NOT write the two NCAAB rl legs of 11-04 (California -15.5, Butler
-24.5). Those were refused on purpose by receipt_invariants: an rl receipt
with no pick_odds is "a graded play with no price", and grading it would
price it at a guess. That pair is a different defect -- the card published
two priceless picks -- and it belongs to the card, not to the receipt table.

THE PUBLISHED LINE IS THE RECEIPT, NOT THE ROW'S CURRENT LINE. The prop
tables are mutable and two of the thirteen have moved since publish:

    60825  Swift  rush_attempts_under   shipped 17.5, row now 16.5
    71480  Likely reception_yds_over    shipped 47.5, row now 46.5

So pick_line and pick_odds come from the CARD, and the verdict is recomputed
from final_value AT THE LINE WE PUBLISHED rather than inherited from the
row's `result`, which was graded at the row's line. Both happen to agree here
(27 clears 16.5 and 17.5; 127 clears 46.5 and 47.5) but that is a fact to
check per leg, not to assume -- feedback_grade_the_side_we_published.

Identity is read from the source row by id, never parsed from the label:
grade_public_receipts needed a whole label-parsing grader
(_grade_sharp_card_props) precisely because this writer left player_name and
prop_type NULL. These rows carry them.

Everything goes through public_receipt.capture(mode='reconstructed') so it
passes the one chokepoint -- invariant screen, first-write-wins, bet-level
dedupe -- and is labelled honestly. A receipt written 14 days late is not
live evidence and must never claim to be.

Dry by default. Re-runnable: first-write-wins means a second run writes 0.

    python backfill_sharp_card_prop_receipts.py            # dry
    python backfill_sharp_card_prop_receipts.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import requests
from dotenv import load_dotenv

load_dotenv('.env')
load_dotenv('../.env')

import public_receipt as PR

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

PROP_TABLE = {'NFL': 'nfl_pipeline_props', 'MLB': 'mlb_pipeline_props',
              'NBA': 'nba_pipeline_props', 'NHL': 'nhl_pipeline_props'}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def grade_at(direction, line, final):
    """Verdict at the line WE published. None when it cannot be known."""
    ln, fv = _f(line), _f(final)
    if ln is None or fv is None:
        return None
    d = str(direction or '').strip().lower()
    if d not in ('over', 'under'):
        return None
    if abs(fv - ln) < 1e-9:
        return 'PUSH'
    if d == 'over':
        return 'WIN' if fv > ln else 'LOSS'
    return 'WIN' if fv < ln else 'LOSS'


def cards(since, until):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H, params={
            'select': 'cache_key,created_at,data',
            'cache_key': 'like.sharp_card_%',
            'order': 'cache_key.asc', 'limit': '1000', 'offset': str(off)},
            timeout=90)
        b = r.json()
        if not isinstance(b, list):
            print(f'  !! jerry_cache read failed {r.status_code}: '
                  f'{r.text[:200]}')
            sys.exit(2)
        out += b
        if len(b) < 1000:
            break
        off += 1000
    res = []
    for row in out:
        d = str(row['cache_key'])[-10:]
        if not (since <= d <= until):
            continue
        items = (row.get('data') or {}).get('items') or []
        if items:
            res.append((d, row['created_at'], items))
    return sorted(res)


def existing_source_ids(dates):
    have = set()
    for d in dates:
        r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H, params={
            'select': 'game_date,source_id', 'surface': 'eq.sharp_card',
            'game_date': f'eq.{d}', 'limit': '1000'}, timeout=60)
        if r.status_code != 200:
            print(f'  !! receipt read failed for {d}: {r.text[:160]}')
            sys.exit(2)
        if len(r.json()) >= 1000:
            # 0 rows read is a wiring error; 1000 read is a truncated one.
            print(f'  !! {d} returned the 1000-row cap - refusing to compare '
                  f'against a truncated read')
            sys.exit(2)
        for x in r.json():
            have.add((str(x['game_date']), str(x['source_id'])))
    return have


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-10-29')
    ap.add_argument('--until', default='2026-12-31')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    if not (SB and KEY):
        print('SUPABASE env missing')
        return 2

    found = cards(a.since, a.until)
    print(f'sharp_card payloads in {a.since}..{a.until}: {len(found)}')
    have = existing_source_ids([d for d, _c, _i in found])
    print(f'sharp_card receipts already recorded on those dates: {len(have)}')

    to_write, skipped = [], Counter()
    for d, created, items in found:
        rows = PR.sharp_card_rows(items, d)
        for it in items:
            if str(it.get('market') or it.get('type') or '').lower() != 'prop':
                continue
            rr = None
            for cand in rows:
                if cand.get('pick_label') == it.get('pick'):
                    rr = cand
                    break
            if rr is None:
                skipped['no_builder_row'] += 1
                continue
            if (d, rr['source_id']) in have:
                skipped['already_receipted'] += 1
                continue
            pid = it.get('id')
            tbl = it.get('source_table') or PROP_TABLE.get(str(it.get('sport')))
            if pid is None or not tbl:
                skipped['no_source_row_anchor'] += 1
                print(f"  . {d} {it.get('pick')!r}: no prop id/table - cannot "
                      f'establish identity, not written')
                continue
            q = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, params={
                'select': 'id,game_date,player_name,prop_type,prop_line,'
                          'direction,result,final_value',
                'id': f'eq.{pid}'}, timeout=60)
            src = q.json()[0] if q.status_code == 200 and q.json() else None
            if not src:
                skipped['source_row_gone'] += 1
                print(f'  . {d} {tbl}.{pid}: source row not found, not written')
                continue
            if str(src['game_date'])[:10] != d:
                skipped['source_row_wrong_date'] += 1
                print(f"  . {d} {tbl}.{pid}: row is dated "
                      f"{str(src['game_date'])[:10]} - refusing")
                continue
            # The published line and price, never the row's current ones.
            pub_line = it.get('line')
            verdict = grade_at(src['direction'], pub_line, src['final_value'])
            row_line = src['prop_line']
            moved = (_f(pub_line) is not None and _f(row_line) is not None
                     and abs(_f(pub_line) - _f(row_line)) > 1e-9)
            rr['player_name'] = src['player_name']
            rr['prop_type'] = src['prop_type']
            rr['pick_side'] = str(src['direction'] or '').upper() or None
            rr['pick_line'] = pub_line
            rr['actual_value'] = _f(src['final_value'])
            rr['result'] = verdict
            rr['published_at'] = created
            aud = dict(rr.get('audit') or {})
            aud.update({
                'backfilled_by': 'backfill_sharp_card_prop_receipts',
                'backfill_reason': 'sharp_card_rows read it["type"] only; the '
                                   'measured-cell prop path emits '
                                   'market="prop" with no type key, so this '
                                   'leg published with no receipt',
                'graded_at_published_line': pub_line,
                'source_row_line_now': row_line,
                'source_row_result': src['result'],
                'final_value': _f(src['final_value']),
                'source_ref': f'{tbl}.{pid}',
            })
            if moved:
                aud['line_moved_since_publish'] = True
            rr['audit'] = aud
            to_write.append((d, rr, src, moved))

    print(f'\nprop legs needing a receipt: {len(to_write)}')
    if skipped:
        print(f'skipped: {dict(skipped)}')
    if not to_write:
        return 0

    print(f"\n{'date':11} {'src':24} {'label':42} {'line':>6} {'odds':>6} "
          f"{'final':>7} {'verdict':8} row_result")
    agree = dis = ungraded = 0
    for d, rr, src, moved in to_write:
        rowres = str(src['result'] or '').upper()
        v = rr['result']
        if v is None:
            ungraded += 1
            flag = 'UNGRADED'
        elif rowres and v != rowres:
            dis += 1
            flag = f'!! DISAGREES ({rowres})'
        else:
            agree += 1
            flag = rowres.lower()
        print(f"  {d} {rr['audit']['source_ref']:24} "
              f"{str(rr['pick_label'])[:42]:42} "
              f"{str(rr['pick_line']):>6} {str(rr['pick_odds']):>6} "
              f"{str(src['final_value']):>7} {str(v):8} {flag}"
              + ('   [line moved since publish]' if moved else ''))
    print(f'\nverdict at published line vs row result: {agree} agree, '
          f'{dis} disagree, {ungraded} ungraded')
    if dis:
        print('  A disagreement means the row was graded at a line we did not '
              'publish. The published line wins; the row result is kept in '
              'audit for the trail.')

    tally = Counter(rr['result'] for _d, rr, _s, _m in to_write)
    print(f'backfilled verdicts: {dict(tally)}')

    payload = [rr for _d, rr, _s, _m in to_write]
    res = PR.capture(payload, surface='sharp_card',
                     dry_run=not a.apply, mode='reconstructed')
    print(f'\ncapture({"APPLY" if a.apply else "DRY"}, '
          f'mode=reconstructed) -> {res}')
    if not a.apply:
        print('\nDRY RUN - nothing written. Re-run with --apply.')
    else:
        print('\nNow re-aggregate the affected days so the record moves:')
        print('  ' + ' '.join(sorted({d for d, _r, _s, _m in to_write})))
    return 0


if __name__ == '__main__':
    sys.exit(main())
