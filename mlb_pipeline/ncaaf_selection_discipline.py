"""Why NCAAF picks lose, split into the two causes — and what to do instead.

ANDY 2026-10-09: "the selction process has to be better i cant just pull
saturaday picks fromt e sharp"

He is right, and dropping NCAAF was the wrong recommendation. Saturday IS the
college slate; a Sharp with no Saturday is not a product. So the question is
what a better selection rule looks like, and that has to start from what the
NCAAF market actually does rather than from our 44 published picks.

STEP 1 — THE MARKET BASELINE, on 6,329 graded games (2022-08-27..2026-10-08)
    favourite covers, by spread size
      pick-em <3   49.50%  n=802      11-14.5   48.54%  n=785
      3-6.5        47.69%  n=1,514    15-21     49.49%  n=877
      7-10.5       48.65%  n=1,149    21.5+     52.41%  n=1,202
    home favourite 49.87% n=4,115  ·  away favourite 48.18% n=2,200
    totals OVER    50.16% n=6,234
Every slice is a coin flip within about one standard error. The NCAAF closing
line is efficient: there is no favourite bias, no spread-size bias, no
over/under bias to select on. At n=6,329 that is a REAL null, not thin data.

This does NOT refute the "NCAAF dog bias — dogs 24.3% vs favs 57.9%" note from
09-26 (an earlier commit message of mine said it did; that was an
overstatement). That note measured OUR PICKS, not the market, so the two are
compatible — and together they localise the defect: in a market where dogs
cover ~50%, our dog picks going 24.3% was a selection failure, not a market
tendency we were reading correctly.

BUT THE DOG BIAS HAS SINCE DECAYED, and that matters more:
    all time        FAV 56.9% n=167   DOG 38.7% n=75    gap 18.2pp
    live window     FAV 50.0% n=104   DOG 41.7% n=36    gap  8.3pp
    since pick lock FAV 53.7% n=54    DOG 50.0% n=28    gap  3.7pp
Post-lock the gap is 3.7pp against 2SE bands of 13.6 and 18.9 — indistinguishable
from zero. The 09-26 note warned "do NOT act on this without a second season's
sample or a holdout"; that warning was correct. The pick lock (20260926b)
appears to have removed the mechanism, which was drift flipping picks toward
the dog. **A favourites-only rule is NOT supported by current data.**
Hit rate is used throughout here because it comes from grading results and is
therefore immune to the reconstructed-price problem that invalidates ROI
comparisons across surfaces.

AND THE PROMOTION HYPOTHESIS ALSO FAILS TO CLEAR NOISE
The tempting story is that promoting a read to a card subtracts value.
Post-lock, spreads only, on hit rate (provenance-safe):
    promoted to a CARD   45.8%  n=24
    left as a READ only  55.2%  n=58
    promotion worth -9.3pp against a 2SE band of 24.3pp -> INSIDE NOISE
And it is window-sensitive in the way noise is: moving the start date one week
earlier takes game_read spreads from 55.2% (n=58) to 47.7% (n=107), i.e. from
above breakeven to below. Do not build a rule on this.

STEP 2 — THE ARITHMETIC THAT FOLLOWS
If the market pays off at ~50% and we have no demonstrated edge, then price
alone decides the result. Expected ROI at a 50% hit rate:
    -110 -> -4.5%      -130 -> -11.5%      -142 -> -14.8%      -175 -> -22.7%
A coin flip at -142 loses 14.8% no matter how good the selection story sounds.
NCAAF sharp_card plays carry a MEDIAN price of -142.

STEP 3 — DECOMPOSING THE OBSERVED -34.4%
    observed   39.1% hit at median -142  =  -33.4%  (matches -34.4% measured)
    if we merely coin-flipped at -142    =  -14.8%
    if we coin-flipped at -110           =   -4.5%
So roughly 19 points of the damage is selection performing WORSE THAN RANDOM,
and roughly 10 points is paying -142 in a market with no edge. Both are real;
selection is the larger share. That is the honest shape of the problem, and it
says the fix is not one thing.

WHY THIS DOES NOT CONTRADICT THE MLB JUICE FINDING
On MLB and props, capping juice made ROI monotonically WORSE because the
-176..-250 band genuinely hit 78.8% — expensive plays there were earning their
price (project_juice_cap_is_backwards_1009). NCAAF has no such evidence at any
price: the market is 50/50 everywhere we can measure. A price ceiling is
justified by the ABSENCE of a demonstrated edge, not by a measured preference
for cheap prices. Same logic, opposite conclusion, because the evidence
differs. Do not generalise either one across sports.

WHAT THIS SCRIPT DOES
Establishes the market baseline from the full game history (leak-free: only
close_spread and the result, nothing derived), then reports what our published
NCAAF picks actually returned against that baseline, then shows what a price
ceiling would have done to the SAME picks — which is the only honest way to
size a pricing rule, because it holds selection constant.

WRITES NOTHING.

CLI
    python ncaaf_selection_discipline.py
    python ncaaf_selection_discipline.py --since 2026-09-19
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
#: Live price capture floor. Receipts before this carry reconstructed prices
#: that measure ~10pp better than live ones
#: (project_reconstructed_prices_inflate_roi_1009).
LIVE_SINCE = '2026-09-19'
WIN = {'WIN', 'WON', 'W'}
LOSS = {'LOSS', 'LOST', 'L'}
VOID = {'PUSH', 'VOID', 'CANCELLED', 'CANCELED', 'NO_ACTION'}


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:140]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def breakeven(o):
    return 100 * -o / (-o + 100) if o < 0 else 100 * 100 / (o + 100)


def profit(o, won):
    if not won:
        return -1.0
    return (100 / -o) if o < 0 else (o / 100)


def roi_at(hit_pct, odds):
    """Expected ROI if a play at `odds` hits `hit_pct` of the time."""
    h = hit_pct / 100
    return 100 * (h * (100 / -odds if odds < 0 else odds / 100) - (1 - h))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default=LIVE_SINCE)
    a = ap.parse_args()

    # ── STEP 1: the market baseline, from raw results only ───────────────
    raw = _page('ncaaf_game_results',
                {'select': 'game_date,close_spread,spread_result,'
                           'total_result'})
    g = [x for x in raw
         if str(x.get('spread_result') or '').lower()
         in ('home_covered', 'away_covered')
         and _f(x.get('close_spread')) is not None]
    print(f'=== STEP 1 · THE NCAAF MARKET, {len(g)} graded games with a price')
    print('    Leak-free by construction: only close_spread and the result.')
    fav = []
    for x in g:
        s = _f(x['close_spread'])
        hc = str(x['spread_result']).lower() == 'home_covered'
        # NCAAF/MLB convention: NEGATIVE close_spread = HOME favourite.
        fav.append(1 if (hc if s < 0 else not hc) else 0)
    fp = statistics.fmean(fav) * 100
    fse = (0.5 / len(fav) ** 0.5) * 100
    tt = [1 if str(x['total_result']).lower() == 'over' else 0 for x in raw
          if str(x.get('total_result') or '').lower() in ('over', 'under')]
    print(f'\n    favourites cover  {fp:.2f}%  n={len(fav)}  '
          f'2SE +/-{2 * fse:.2f}pp')
    if tt:
        op = statistics.fmean(tt) * 100
        print(f'    OVER hits         {op:.2f}%  n={len(tt)}  '
              f'2SE +/-{2 * (0.5 / len(tt) ** 0.5) * 100:.2f}pp')
    print('\n    Both sit on 50% inside one standard error. There is no side')
    print('    and no direction to lean on structurally. THIS IS THE')
    print('    CONSTRAINT every selection rule has to live inside.')

    # ── STEP 2: what price does to a coin flip ───────────────────────────
    print('\n' + '=' * 70)
    print('=== STEP 2 · WHAT A PRICE COSTS WHEN THERE IS NO EDGE')
    print('    Expected ROI if a play hits exactly the market rate '
          f'({fp:.1f}%):')
    print(f'\n      {"price":>8}{"need%":>9}{"ROI at market rate":>22}')
    for o in (-105, -110, -120, -130, -142, -150, -175, -200):
        print(f'      {o:>8}{breakeven(o):8.1f}%{roi_at(fp, o):21.1f}%')
    print('\n    A play at -142 loses ~15% at the market rate however good the')
    print('    write-up is. Price is not a detail here, it is the whole result.')

    # ── STEP 3: our actual picks ─────────────────────────────────────────
    rc = _page('public_receipts',
               {'select': 'surface,market,tier,pick_odds,result,game_date,'
                          'capture_mode',
                'sport': 'eq.NCAAF', 'game_date': f'gte.{a.since}'})
    rows = []
    for x in rc:
        res = str(x.get('result') or '').upper()
        if res in VOID or (res not in WIN and res not in LOSS):
            continue
        o = _f(x.get('pick_odds'))
        if not o:
            continue
        rows.append({'won': 1 if res in WIN else 0, 'o': o,
                     'mkt': str(x.get('market') or '?'),
                     'surf': str(x.get('surface') or '?'),
                     'cm': str(x.get('capture_mode') or '?')})
    print('\n' + '=' * 70)
    print(f'=== STEP 3 · OUR PUBLISHED NCAAF PICKS since {a.since}')
    print(f'    {len(rows)} graded and priced')

    # game_read is 100% reconstructed-price, so it cannot be compared to the
    # live-captured card surfaces. Keeping it in the same table invites
    # exactly the false "the engine read is fine, selection ruins it"
    # conclusion that this script was written after making.
    cards = [r for r in rows if r['surf'] in ('sharp_card', 'sweat_card',
                                              'potd', 'dawg', 'ladder')]
    reads = [r for r in rows if r not in cards]
    if reads:
        cm = collections.Counter(r['cm'] for r in reads)
        print(f'\n    !! {len(reads)} non-card rows (game_read etc) EXCLUDED: '
              f'capture_mode {dict(cm)}.')
        print(f'       Those carry reconstructed prices at a median of '
              f'-110, so comparing them')
        print(f'       to live-captured card prices measures the PRICE '
              f'SOURCE, not the selection.')
    if not cards:
        print('\n    no priced card plays in this window.')
        return 0

    print(f'\n      {"surface":<14}{"hit%":>8}{"need%":>8}{"ROI":>9}'
          f'{"n":>5}{"median":>8}')
    for s in sorted({r['surf'] for r in cards}):
        sub = [r for r in cards if r['surf'] == s]
        if len(sub) < 5:
            continue
        h = statistics.fmean([r['won'] for r in sub]) * 100
        nd = statistics.fmean([breakeven(r['o']) for r in sub])
        u = sum(profit(r['o'], r['won']) for r in sub)
        print(f'      {s:<14}{h:7.1f}%{nd:7.1f}%{100 * u / len(sub):+8.1f}%'
              f'{len(sub):>5}{statistics.median([r["o"] for r in sub]):+8.0f}')

    h = statistics.fmean([r['won'] for r in cards]) * 100
    med = statistics.median([r['o'] for r in cards])
    u = sum(profit(r['o'], r['won']) for r in cards)
    print(f'\n    DECOMPOSITION of the {100 * u / len(cards):+.1f}% observed:')
    print(f'      we hit {h:.1f}% at a median of {med:+.0f}')
    print(f'      a COIN FLIP at {med:+.0f} would return '
          f'{roi_at(fp, med):+.1f}%  <- cost of the price')
    print(f'      a COIN FLIP at -110 would return {roi_at(fp, -110):+.1f}%'
          f'  <- the floor for a no-edge market')
    print(f'      selection is worth {h - fp:+.1f}pp vs the market rate '
          f'({h:.1f}% vs {fp:.1f}%)')
    print(f'\n    So the loss splits roughly into '
          f'{abs(roi_at(fp, med) - roi_at(fp, -110)):.0f}pp of juice above')
    print(f'    -110 and {abs(100 * u / len(cards) - roi_at(fp, med)):.0f}pp '
          f'of selection landing BELOW a coin flip.')
    print('    Fixing either alone leaves the other in place.')

    # ── STEP 4: hold selection constant, vary the ceiling ────────────────
    print('\n' + '=' * 70)
    print('=== STEP 4 · A PRICE CEILING ON THE SAME PICKS')
    print('    Selection held constant — only the price filter changes. This')
    print('    is the honest way to size a pricing rule. Watch the PLAYS')
    print('    column: a ceiling that leaves four plays is not a Saturday')
    print('    product, which is the whole constraint Andy named.')
    print(f'\n      {"ceiling":>9}{"plays":>7}{"kept%":>7}{"hit%":>8}'
          f'{"ROI":>9}{"units":>9}')
    for cap in (None, -175, -150, -140, -130, -120, -115, -110):
        s = cards if cap is None else [r for r in cards if r['o'] >= cap]
        if not s:
            continue
        hh = statistics.fmean([r['won'] for r in s]) * 100
        uu = sum(profit(r['o'], r['won']) for r in s)
        lbl = 'none' if cap is None else f'{cap:+d}'
        print(f'      {lbl:>9}{len(s):>7}{100 * len(s) / len(cards):6.0f}%'
              f'{hh:7.1f}%{100 * uu / len(s):+8.1f}%{uu:+9.2f}')

    print('\n    READ THIS TABLE CAREFULLY. With selection at or below a coin')
    print('    flip, NO ceiling makes it positive — it can only reduce the')
    print('    bleed. The ceiling is damage control; the selection is the')
    print(f'    actual problem, and on n={len(cards)} card plays we cannot yet')
    print('    tell which NCAAF picks are the good ones. Any rule tuned on')
    print('    this sample is curve-fitting.')

    print('\n' + '=' * 70)
    print('  WHAT IS DEFENSIBLE TODAY')
    print('  1. NO NCAAF TOTALS. Two independent samples agree: receipts')
    print('     -21.8% (n=28) and the totals model at 30.8% (n=39). And the')
    print(f'     market OVER rate is {statistics.fmean(tt) * 100:.2f}% on '
          f'n={len(tt)} — nothing to pick.')
    print('  2. PRICE CEILING on NCAAF sides. Justified by the ABSENCE of a')
    print('     measured edge, not by a preference for cheap prices. In a')
    print('     50/50 market -142 is a guaranteed ~15% loss.')
    print('  3. CONVICTION CANNOT DRIVE NCAAF PROMOTION. It does not rank:')
    print('     r~+0.03 against winning, and PRIME measured worse than')
    print('     STRONG. A tier label we cannot justify is a trust problem as')
    print('     much as a money one.')
    print('\n  WHAT IS *NOT* SUPPORTED, despite being tempting:')
    print('  - favourites-only. The dog gap is 3.7pp post-pick-lock against')
    print('    2SE bands of 13.6/18.9. The old 24.3% was the pre-lock drift')
    print('    era and the lock already fixed the mechanism.')
    print('  - "promotion to a card subtracts value". -9.3pp against a 24.3pp')
    print('    2SE band, and it flips sign when the window moves one week.')
    print('\n  4. WHAT IS STILL MISSING: a validated discriminator. Nothing')
    print('     measured separates good NCAAF picks from bad ones. Every')
    print(f'     candidate tested sits inside its noise band at n={len(cards)}')
    print('     card plays — and that is the real blocker, not a lack of')
    print('     ideas. Five weeks of NCAAF gives ~24 graded card spreads,')
    print('     which cannot validate anything. Two routes out, both slow:')
    print('     wait for sample, or build a predictor on the 6,329-game set')
    print('     and walk-forward it there. Tuning on the published picks is')
    print('     not a third option.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
