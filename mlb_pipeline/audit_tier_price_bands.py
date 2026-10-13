"""Is a tier's problem its ACCURACY or the PRICE it pays?

ANDY 2026-10-12: "How would you rate perfromcance thoguhout app?"

THE OBSERVATION THAT STARTED THIS. On clean receipts (floored past the
prop-L5 leak and the price-reconstruction window), every tier lands just
BELOW its own breakeven:

    PRIME     58.7% hit   60.0% breakeven   -1.3pp   ROI -3.0%   n=315
    STRONG    52.1%       53.6%             -1.5pp   ROI -2.8%   n=658
    LEAN      52.9%       54.0%             -1.1pp   ROI -1.7%   n=669
    untiered  49.4%       51.2%             -1.9pp   ROI -3.2%   n=1155

Four independent tiers, ~2,800 picks, all 1-2pp short. A system with no
signal does not land that close to its own price — and a system with real
signal does not land under it. The gap IS the vig, which says the question
is not "can we pick" but "what are we paying".

Splitting PRIME by price band suggested the answer is price:

    -200..-150   70.4% vs 63.1% be   ROI +11.7%   n=71
    -150..-120   57.4% vs 57.9%      ROI  -0.7%   n=68
    -120..-105   50.7% vs 53.0%      ROI  -4.5%   n=73
    plus money   29.7% vs 47.4%      ROI -35.9%   n=37

WHY THIS FILE EXISTS RATHER THAN A GATE. That is 4 bands x 6 tiers of
cells; something will look extreme. Today a 72.1% cell with a +5.7% ROI
died in this same codebase once it was given a bootstrap CI, a temporal
split and a drop-one test. A band gate is a deletion of real published
plays, so it gets the same three tests before it is proposed, and the
answer is allowed to be "no".

ALSO TESTED: whether a band rule would have IMPROVED the book. A rule that
raises one tier's ROI while deleting its volume can still be a bad trade,
so the counterfactual book ROI is reported next to the cell ROI.

WRITES NOTHING.

CLI
    python audit_tier_price_bands.py
    python audit_tier_price_bands.py --since 2026-09-22 --tier PRIME
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

#: 2026-09-19 is the price-reconstruction floor; 2026-09-22 also clears the
#: prop L5 leak, before which no PRIME record is valid. Default to the later.
DEFAULT_SINCE = '2026-09-22'

BANDS = [(-100000, -200, 'heavy fav  <= -200'),
         (-200, -150, 'mod fav   -200..-150'),
         (-150, -120, 'light fav -150..-120'),
         (-120, -100, 'near pick -120..-100'),
         (-100, 100000, 'plus money  >= +100')]


def page(table, params, cap=200000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=180)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                                  # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def breakeven(o):
    return ((-o) / ((-o) + 100.0) * 100) if o < 0 else (100.0 / (o + 100.0) * 100)


def unit(o, won):
    return ((o / 100.0) if o > 0 else (100.0 / -o)) if won else -1.0


def boot_ci(vals, iters=8000, seed=23):
    rng = random.Random(seed)
    n = len(vals)
    out = []
    for _ in range(iters):
        t = 0.0
        for _i in range(n):
            t += vals[rng.randrange(n)]
        out.append(t / n)
    out.sort()
    return out[int(iters * 0.025)] * 100, out[int(iters * 0.975)] * 100


def line(label, rows, pad=24):
    n = len(rows)
    if n < 25:
        print(f'    {label:<{pad}} n={n} too thin')
        return None
    w = sum(1 for r in rows if r['won'])
    hit = w / n * 100
    be = statistics.fmean([breakeven(r['odds']) for r in rows])
    us = [unit(r['odds'], r['won']) for r in rows]
    roi = statistics.fmean(us) * 100
    lo, hi = boot_ci(us)
    clears = not (lo <= 0 <= hi)
    print(f'    {label:<{pad}} {w}-{n - w}  hit {hit:5.1f}%  be {be:5.1f}%  '
          f'gap {hit - be:+5.1f}pp  ROI {roi:+6.1f}%  '
          f'CI[{lo:+6.1f},{hi:+6.1f}]{" *" if clears else "  "}  n={n}')
    return {'n': n, 'roi': roi, 'ci': (lo, hi), 'clears': clears,
            'units': sum(us)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default=DEFAULT_SINCE)
    ap.add_argument('--tier', default=None)
    a = ap.parse_args()

    rc = page('public_receipts', {
        'select': 'tier,market,surface,sport,result,pick_odds,game_date',
        'game_date': f'gte.{a.since}'})
    rows = []
    for x in rc:
        res = str(x.get('result') or '').strip().lower()
        if res not in ('win', 'won', 'loss', 'lost'):
            continue            # NO_ACTION is a PASS, never a bet; push is 0
        o = f(x.get('pick_odds'))
        if o is None or o == 0 or abs(o) > 2000:
            continue
        rows.append({'tier': str(x.get('tier')), 'odds': o,
                     'won': res in ('win', 'won'),
                     'date': str(x.get('game_date'))[:10],
                     'market': str(x.get('market')),
                     'surface': str(x.get('surface'))})

    print(f'=== tier x price band · receipts since {a.since} ===')
    print(f'    {len(rows)} graded bets carrying a usable price')
    print('    NO_ACTION excluded: it records a PASS, which was never a bet\n')

    tiers = ([a.tier] if a.tier else
             ['PRIME', 'STRONG', 'LEAN', 'SKIP', 'COVERAGE', 'None'])
    for tier in tiers:
        sub = [r for r in rows if r['tier'] == tier]
        if len(sub) < 40:
            print(f'── {tier}: n={len(sub)} too thin\n')
            continue
        print(f'── {tier}: n={len(sub)}')
        cells = {}
        for lo, hi, lbl in BANDS:
            cells[lbl] = line(lbl, [r for r in sub if lo <= r['odds'] < hi])

        # ══ TEST 2 · does the worst band hold up out of sample? ══
        worst = min((c for c in cells.items() if c[1]),
                    key=lambda kv: kv[1]['roi'], default=None)
        best = max((c for c in cells.items() if c[1]),
                   key=lambda kv: kv[1]['roi'], default=None)
        for tag, cell in (('WORST', worst), ('BEST', best)):
            if not cell:
                continue
            lbl = cell[0]
            lo, hi = next((l, h) for l, h, s in BANDS if s == lbl)
            band = [r for r in sub if lo <= r['odds'] < hi]
            ds = sorted(r['date'] for r in band)
            mid = ds[len(ds) // 2]
            print(f'      {tag} band temporal split at {mid}:')
            line('  early', [r for r in band if r['date'] < mid], pad=22)
            line('  late', [r for r in band if r['date'] >= mid], pad=22)

        # ══ TEST 3 · would a gate have improved the BOOK? ══
        # Deleting a band raises the surviving ROI by construction. The
        # honest question is whether total units improve and by how much,
        # against how much volume is destroyed.
        print('      counterfactual: DROP one band from this tier')
        tot_u = sum(unit(r['odds'], r['won']) for r in sub)
        for lo, hi, lbl in BANDS:
            keep = [r for r in sub if not (lo <= r['odds'] < hi)]
            dropped = len(sub) - len(keep)
            if dropped < 15 or not keep:
                continue
            ku = sum(unit(r['odds'], r['won']) for r in keep)
            print(f'        drop {lbl:<22} -{dropped:<4} bets  '
                  f'units {tot_u:+7.2f} -> {ku:+7.2f}  '
                  f'ROI {tot_u / len(sub) * 100:+5.1f}% -> '
                  f'{ku / len(keep) * 100:+5.1f}%')
        print()

    print('  ' + '=' * 70)
    print('  A band is only a gate candidate if its CI excludes zero, it')
    print('  holds in BOTH halves, and dropping it improves total units —')
    print('  not just the surviving percentage. Raising a rate by deleting')
    print('  volume is not the same as making money.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
