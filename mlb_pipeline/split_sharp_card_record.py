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
for _l in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
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


def tally(legs, keep: set) -> dict:
    """Sum one bucket of legs the way agg_sharp_card sums the whole card."""
    t = {'wins': 0, 'losses': 0, 'pushes': 0, 'pending': 0,
         'units_bet': 0.0, 'units_won': 0.0, 'shipped': 0}
    for leg in legs:
        if str(leg.get('type') or '').lower() not in keep:
            continue
        t['shipped'] += 1
        v = str(leg.get('verdict') or '').upper()
        if v not in ('W', 'L', 'P'):
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
