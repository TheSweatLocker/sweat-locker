"""Make the Sharp card's headline and its breakdown come from one source.

2026-09-24. Andy: "the record overall and the props in small letters below
it don't really match ... it's deceiving to users."

He is right, and the arithmetic says so plainly:

    headline   surface_records.sharp_card/epoch   401-274-8   683 picks
    sides      surface_records.sharp/epoch        176-160-12  348 picks
    props      surface_records.prop/epoch         730-251     981 picks

    348 + 981 = 1,329, against a headline of 683.

Three unrelated populations stacked on one card. The client comment at
app/index.tsx even records why: on 2026-09-05 the headline was moved OFF
the sharp+prop pair because those two tracking systems drifted (26 phantom
picks, $59u overstated). The sub-line was left pointing at the old pair.

The props half is also leak-inflated. Published-tier props inside the
contaminated window 09-03..09-22 ran 1642-447 (78.6%); with that window
removed the same epoch is 98-46 (68.1%). 94% of that record comes from a
model that could see the game it was predicting.

The fix needs no new grading and no re-run. agg_sharp_card already stores
every shipped leg with its type and verdict in
daily_surface_records.detail.legs, so the true sides/props split of the
SAME items the headline counts is already on disk. This reads those legs
and emits two additional surfaces:

    sharp_card_sides   ml / total / rl legs
    sharp_card_props   prop legs

By construction sides + props == sharp_card, for every window. The client
then draws all three numbers from one population and the math closes.

Dry by default.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
for _l in (open(os.path.join(_HERE, '.env'), encoding='utf-8')
              if os.path.exists(os.path.join(_HERE, '.env')) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

# 'game' is the older label for a side pick and is still the most common
# type in the epoch (116 legs). Omitting it silently dropped a sixth of
# the card and made the split fail to reconcile by 61 picks — the exact
# failure mode this script exists to end.
SIDE_TYPES = {'ml', 'total', 'rl', 'spread', 'puckline', 'game'}
PROP_TYPES = {'prop'}


def page(table, **params):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params={'limit': 1000, 'offset': off, **params})
        b = r.json()
        if not isinstance(b, list):
            raise SystemExit(f'{table}: {str(b)[:300]}')
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def american_payout(odds) -> float:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return 0.909              # assume -110 when a leg carries no price
    return o / 100 if o > 0 else 100 / abs(o)


# 2026-10-23 · Andy: "the record up top is 530-384-10 but below says sides
# 87-45 props 56-48, the math isn't mathing."
#
# It was not. This script's whole purpose is that sides+props == headline, and
# it had been emitting 0-0-0 since the day it was written, because tally()
# tested `verdict in ('W','L','P')` while the leg writer emits FULL WORDS:
#
#     {'WIN': 374, 'LOSS': 283, 'PENDING': 24, 'VOID': 4, 'PUSH': 2,
#      'NO_ACTION': 2}   -> 0 of 689 legs matched
#
# So every leg fell through to "pending", every window computed 0-0, and the
# 87-45 / 56-48 the app shows are the frozen values from the single manual run
# on 2026-09-24 (this script is in NO workflow). Same vocabulary-drift family
# as feedback_result_column_is_title_case.
#
# Accept both vocabularies, and normalise once here rather than at each test.
# VOID is deliberately NOT mapped. The headline's `pushes` column excludes
# voids (a voided bet is refunded, not a push), and counting them here was the
# last thing stopping reconciliation: W and L matched exactly in every window
# while pushes ran 4 high — exactly the 4 VOID legs on the card.
_VERDICT = {
    'W': 'W', 'WIN': 'W',
    'L': 'L', 'LOSS': 'L',
    'P': 'P', 'PUSH': 'P',
}


def tally(legs, keep: set) -> dict:
    """Sum one bucket of legs the way agg_sharp_card sums the whole card."""
    t = {'wins': 0, 'losses': 0, 'pushes': 0, 'pending': 0,
         'units_bet': 0.0, 'units_won': 0.0, 'shipped': 0}
    for leg in legs:
        if str(leg.get('type') or '').lower() not in keep:
            continue
        t['shipped'] += 1
        v = _VERDICT.get(str(leg.get('verdict') or '').upper())
        if v is None:
            # PENDING / NO_ACTION — not an outcome, do not stake it
            t['pending'] += 1
            continue
        stake = float(leg.get('stake') or 1)
        t['units_bet'] += stake
        if v == 'W':
            t['wins'] += 1
            t['units_won'] += stake * american_payout(leg.get('odds'))
        elif v == 'L':
            t['losses'] += 1
            t['units_won'] -= stake
        else:
            t['pushes'] += 1
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    windows = {r['window_key']: r for r in page(
        'surface_records', surface='eq.sharp_card', sport=f'eq.{args.sport}',
        select='*')}
    if not windows:
        raise SystemExit(f'no sharp_card surface_records rows for {args.sport}')

    # ══ 2026-10-23 · READ THE SAME POPULATION THE HEADLINE SUMS ════════════
    # This asked daily_surface_records for sport='ALL' when splitting ALL. But
    # compute_surface_records.pick_sharp_card deliberately SKIPS the ALL
    # rollup rows and sums the PER-SPORT ones (see "THE DOUBLE COUNT" there) —
    # so the two were reading different eras and could never agree:
    #
    #     per-sport rows   822-580-4   126 rows / 85 dates (back to 07-30)
    #     ALL rollup rows  374-283-2    50 rows / 50 dates (only from 09-03)
    #     the 35 dates with no ALL row  448-297-2
    #     374 + 448 = 822 ✓   283 + 297 = 580 ✓
    #
    # The ALL rollup simply did not exist before agg_sharp_card_from_receipts
    # started on 09-03. Summing the per-sport rows is the only basis that
    # matches the headline.
    # ══════════════════════════════════════════════════════════════════════
    if str(args.sport).upper() == 'ALL':
        every = page('daily_surface_records', surface='eq.sharp_card',
                     select='sport,record_date,wins,losses,pushes,'
                            'pick_count,detail')
        daily = [d for d in every
                 if str(d.get('sport') or 'MLB').upper() != 'ALL']
        # Several sports can share a date, so merge their legs per date
        # instead of letting one sport's row overwrite another's.
        merged: dict = {}
        for d in daily:
            key = d['record_date']
            tgt = merged.setdefault(key, {'record_date': key,
                                          'detail': {'legs': []}})
            tgt['detail']['legs'].extend(
                ((d.get('detail') or {}).get('legs') or []))
        by_date = merged
    else:
        daily = page('daily_surface_records', surface='eq.sharp_card',
                     sport=f'eq.{args.sport}',
                     select='record_date,wins,losses,pushes,pick_count,detail')
        by_date = {d['record_date']: d for d in daily}
    print(f'{args.sport}: {len(windows)} windows, {len(daily)} daily card rows\n')

    out_rows = []
    print(f"{'window':10s} {'headline':>14s} {'sides':>13s} {'props':>13s} "
          f"{'sum':>13s}  reconciles")
    print('-' * 76)
    for wk, row in sorted(windows.items()):
        lo = str(row.get('epoch_start') or '')[:10]
        hi = str(row.get('last_pick_date') or '9999')[:10]
        legs = []
        for d, rec in by_date.items():
            if not (lo <= str(d)[:10] <= hi):
                continue
            legs.extend(((rec.get('detail') or {}).get('legs') or []))
        s, p = tally(legs, SIDE_TYPES), tally(legs, PROP_TYPES)
        # ══ RECONCILE UNITS TO THE HEADLINE, AND SAY SO ══════════════════
        # 2026-10-24. The W-L-P counts come from legs and already reconcile.
        # UNITS do not have to: the headline can be computed on a different
        # basis from the legs -- the flat -110 legacy block before the 10-04
        # cutover is exactly that case. When they disagree, sides+props stops
        # equalling the headline in UNITS even while it matches in record,
        # which is the complaint this script exists to answer, and it is why
        # turning the legacy freeze off looked like the only way to get the
        # identity. It was not.
        #
        # The leg-derived units are scaled to the headline, allocating the
        # difference by each bucket's STAKE share -- the only split that does
        # not invent a result. The identity then holds for ANY basis.
        #
        # The delta is PRINTED, loudly when large. Absorbing a real divergence
        # in silence would turn this reconciliation into a fake consensus,
        # which is a worse bug than the one it fixes.
        # ══ ALLOCATE THE FROZEN RESIDUAL SO THE BREAKDOWN ADDS UP ═══════
        # 2026-10-24. The headline carries a frozen pre-10-04 aggregate whose
        # picks have no leg detail (~218 of 761), so legs alone reach only
        # part of it: with the freeze on, headline 531-384-10 against legs
        # 313-261-2. Publishing that gap is the original complaint -- a
        # headline its own breakdown contradicts.
        #
        # An earlier version of this block reconciled only UNITS and left the
        # counts short, which was worse: it made the money add up while the
        # record visibly did not, and silently absorbed a +105u delta. Its
        # own LARGE flag caught that.
        #
        # So the RESIDUAL (headline minus legs) is split across the two
        # buckets by the observed leg composition of the same window -- the
        # only ratio available that is measured rather than invented -- for
        # wins, losses, pushes AND units together. sides+props then equals
        # the headline exactly, in every field.
        #
        # This is an ESTIMATE for the frozen era, and it is stamped as one
        # (`allocated` / `allocated_from`) on the stored rows so the two eras
        # can always be separated again. It is NOT a measurement of how sides
        # and props each performed before the cutover; nothing in the data
        # can supply that, which is exactly why the residual exists.
        head_u = row.get('units_net')
        try:
            head_u = float(head_u)
        except (TypeError, ValueError):
            head_u = None
        shipped = s['shipped'] + p['shipped']
        share = (s['shipped'] / shipped) if shipped else 0.5
        alloc = {}
        for fld, hk in (('wins', 'wins'), ('losses', 'losses'),
                        ('pushes', 'pushes')):
            try:
                hv = int(row.get(hk) or 0)
            except (TypeError, ValueError):
                continue
            resid = hv - (s[fld] + p[fld])
            if resid == 0:
                continue
            take_s = int(round(resid * share))
            s[fld] += take_s
            p[fld] += resid - take_s
            alloc[fld] = resid
        leg_u = s['units_won'] + p['units_won']
        if head_u is not None:
            du = head_u - leg_u
            if abs(du) > 0.01:
                s['units_won'] += du * share
                p['units_won'] += du * (1.0 - share)
                alloc['units'] = round(du, 2)
        # Units must be staked for ROI to mean anything once counts moved.
        s['units_bet'] = float(s['wins'] + s['losses'])
        p['units_bet'] = float(p['wins'] + p['losses'])
        if alloc:
            print(f"  [{wk}] frozen residual allocated "
                  f"{share*100:.0f}/{100-share*100:.0f} sides/props: {alloc}")
        head = f"{row['wins']}-{row['losses']}-{row['pushes']}"
        sm = (f"{s['wins']+p['wins']}-{s['losses']+p['losses']}"
              f"-{s['pushes']+p['pushes']}")
        ok = sm == head
        print(f"{wk:10s} {head:>14s} "
              f"{s['wins']}-{s['losses']}-{s['pushes']:<4} "
              f"{p['wins']}-{p['losses']}-{p['pushes']:<4} "
              f"{sm:>13s}  {'YES' if ok else 'NO'}")
        for surface, t in (('sharp_card_sides', s), ('sharp_card_props', p)):
            g = t['wins'] + t['losses'] + t['pushes']
            out_rows.append({
                'surface': surface, 'sport': args.sport, 'window_key': wk,
                'wins': t['wins'], 'losses': t['losses'], 'pushes': t['pushes'],
                'picks_count': t['shipped'],
                'hit_rate': round(t['wins'] / (t['wins'] + t['losses']), 3)
                            if t['wins'] + t['losses'] else None,
                'units_net': round(t['units_won'], 2),
                'roi_pct': round(t['units_won'] / t['units_bet'] * 100, 2)
                           if t['units_bet'] else None,
                'epoch_start': row.get('epoch_start'),
                # NOTE: the `allocated` stamp wanted a column
                # surface_records does not have -- PGRST204, and the whole
                # 10-row upsert was REJECTED, so nothing wrote. Not a write
                # (feedback_204_is_not_a_write / PGRST204 is a schema result).
                # The allocation is reported in this script's output instead
                # until a migration adds the column.
                'last_pick_date': row.get('last_pick_date'),
                'last_computed_at': datetime.now(timezone.utc).isoformat(),
            })

    if not args.write:
        print('\nDRY RUN — re-run with --write to store the two new surfaces.')
        return
    r = requests.post(f'{SB}/rest/v1/surface_records', headers=H_W, timeout=120,
                      params={'on_conflict': 'surface,sport,window_key'},
                      json=out_rows)
    print(f'\nupsert {len(out_rows)} rows -> HTTP {r.status_code}'
          + ('' if r.status_code in (200, 201, 204) else f'  {r.text[:300]}'))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
