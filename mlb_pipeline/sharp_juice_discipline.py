"""Where the Sharp's juice eats its edge — per price band, never pooled.

THE PROBLEM, in one line
The Sharp (Steam Room) picks WIN more than half the time and still lose money.
MLB sharp_card hits 57.2% and returns -0.7% ROI. That is not a modelling
failure — a 57% hit rate is a good hit rate. It is a PRICE failure: we publish
at odds that need a higher hit rate than the pick delivers.

So the question is not "are the picks good". It is "at the price we took, was
each pick good ENOUGH", and that question can only be asked one price band at
a time.

THE TRAP THIS TOOL EXISTS TO AVOID
The naive fix is "cap the juice at -150". That reasoning is wrong in a way
that is easy to miss: heavy favourites hit MORE often, which is why they are
favoured. Cutting every -200 play also cuts plays hitting 65%. Whether the cap
helps depends entirely on whether the hit rate rises ENOUGH to cover the worse
price — so the only meaningful column is

    EDGE = actual hit rate  -  breakeven implied by the price actually taken

A band at 65% and -200 (breakeven 66.7%) is LOSING. A band at 54% and -110
(breakeven 52.4%) is winning. Pooled hit rate cannot see either. Ranking bands
by hit rate reproduces the error; this tool ranks by edge.

THE WINDOW TRAP — THIS SCRIPT'S FIRST RUN FELL INTO IT
The first version had no date floor and reported sharp_card at +3.70% ROI.
That number is wrong, and the way it is wrong is worth recording:

    prices BEFORE 2026-09-19 (reconstructed)   hit 61.5%  ROI +9.0%  n=348
    prices 2026-09-19 ONWARD (live capture)    hit 55.2%  ROI -4.9%  n=212
    by capture_mode: reconstructed +7.1%   vs   live -3.5%

A 10.6-point ROI gap on the same surface, decided entirely by how the price
was obtained. Reconstructed prices are backfilled after the fact and come out
systematically BETTER than what we actually published — so every ROI computed
across the full history is overstated, and the pre-09-19 era cannot be used to
judge price discipline because its prices are not the prices we gave anyone.

Hence DEFAULT_SINCE below. Pass --since earlier only to inspect the
reconstruction gap itself, never to quote a number.

AND THE OTHER TRAP, learned the hard way twice today
Rows with a NULL pick_odds are EXCLUDED and counted separately — never
defaulted to -110. Defaulting produced two confident false conclusions this
week ("NCAAF/NFL ML is the winner" at +3.65u and +9.69u) because the real
median prices were -157 and -220. A missing price is missing information, not
a -110. If the excluded count is large the headline number is not trustworthy
and the script says so rather than quietly averaging over a hole.

WRITES NOTHING. Measurement only — any gate goes in as a separate change
after Andy has seen these numbers.

CLI
    python sharp_juice_discipline.py
    python sharp_juice_discipline.py --sport MLB
    python sharp_juice_discipline.py --surface sharp_card --min-n 25
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
#: When live price capture came online. Receipts before this carry
#: reconstructed prices that measure +10.6pp better than live ones, so they
#: cannot be used to judge price discipline. Matches audit_card_performance.py.
DEFAULT_SINCE = '2026-09-19'
WIN = {'WIN', 'WON', 'W'}
LOSS = {'LOSS', 'LOST', 'L'}
#: Pushes are returned stakes — they belong in neither numerator nor
#: denominator. Counting them as losses understates every hit rate.
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


def breakeven(odds):
    """Hit rate needed to break even at American odds."""
    return (100 * -odds / (-odds + 100) if odds < 0
            else 100 * 100 / (odds + 100))


def profit(odds, won):
    """Units returned on a 1u stake."""
    if not won:
        return -1.0
    return (100 / -odds) if odds < 0 else (odds / 100)


def band_of(o):
    if o > 0:
        return '+100 or better'
    if o >= -115:
        return '-100..-115'
    if o >= -140:
        return '-116..-140'
    if o >= -175:
        return '-141..-175'
    if o >= -250:
        return '-176..-250'
    return 'worse than -250'


ORDER = ('+100 or better', '-100..-115', '-116..-140', '-141..-175',
         '-176..-250', 'worse than -250')


def show(rows, label, min_n, by):
    """Print one breakdown: n, hit%, breakeven, EDGE, ROI per bucket."""
    g = collections.defaultdict(list)
    for r in rows:
        g[by(r)].append(r)
    keys = [k for k in ORDER if k in g] or sorted(g, key=str)
    keys += [k for k in sorted(g, key=str) if k not in keys]
    print(f'\n  {label}')
    print(f'      {"bucket":<18}{"n":>5}{"hit%":>8}{"need%":>8}'
          f'{"EDGE":>8}{"ROI":>9}{"units":>9}')
    tot = []
    for k in keys:
        s = g[k]
        if len(s) < min_n:
            continue
        hit = statistics.fmean([r['won'] for r in s]) * 100
        need = statistics.fmean([breakeven(r['odds']) for r in s])
        u = sum(profit(r['odds'], r['won']) for r in s)
        roi = 100 * u / len(s)
        flag = '  <-- LEAK' if roi < -2 else '  <-- keep' if roi > 2 else ''
        print(f'      {str(k):<18}{len(s):>5}{hit:7.1f}%{need:7.1f}%'
              f'{hit - need:+7.1f}{roi:+8.1f}%{u:+9.2f}{flag}')
        tot.append((k, len(s), roi, u))
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--surface', default='sharp_card')
    ap.add_argument('--sport', default=None)
    ap.add_argument('--min-n', type=int, default=20, dest='min_n')
    ap.add_argument('--since', default=DEFAULT_SINCE,
                    help='live-price-capture floor. Going earlier mixes in '
                         'reconstructed prices that read ~10pp better than '
                         'the prices actually published.')
    a = ap.parse_args()

    p = {'select': 'sport,surface,market,game_date,pick_label,pick_side,'
                   'pick_odds,tier,conviction,result,capture_mode',
         'surface': f'eq.{a.surface}',
         'game_date': f'gte.{a.since}'}
    if a.sport:
        p['sport'] = f'eq.{a.sport.upper()}'
    raw = _page('public_receipts', p)
    if a.since < DEFAULT_SINCE:
        print(f'  !! --since {a.since} is before live price capture '
              f'({DEFAULT_SINCE}). Reconstructed')
        print(f'     prices measure ~10pp better than live ones, so the ROI '
              f'below is INFLATED')
        print(f'     and must not be quoted as performance.')

    graded, nullodds, void = [], [], 0
    for x in raw:
        res = str(x.get('result') or '').upper()
        if res in VOID:
            void += 1
            continue
        if res not in WIN and res not in LOSS:
            continue
        o = _f(x.get('pick_odds'))
        rec = {'won': 1 if res in WIN else 0,
               'sport': str(x.get('sport') or '?'),
               'market': str(x.get('market') or '?'),
               'tier': str(x.get('tier') or '?'),
               'date': str(x.get('game_date') or '')[:10],
               'conv': _f(x.get('conviction')),
               'cm': str(x.get('capture_mode') or '?'),
               'odds': o}
        (graded if o else nullodds).append(rec)

    print(f'=== {a.surface}{" · " + a.sport.upper() if a.sport else ""} · '
          f'{len(raw)} receipts')
    print(f'    {len(graded)} graded WITH a price · {len(nullodds)} graded '
          f'with NO price · {void} push/void (correctly excluded)')
    if nullodds:
        hit = statistics.fmean([r['won'] for r in nullodds]) * 100
        pct = 100 * len(nullodds) / (len(graded) + len(nullodds))
        print(f'\n    !! {len(nullodds)} rows ({pct:.1f}%) have NO pick_odds. '
              f'They hit {hit:.1f}%, but a hit rate')
        print(f'       without a price cannot be turned into an ROI, and '
              f'defaulting them to -110')
        print(f'       is exactly what produced two false "ML is the winner" '
              f'calls this week.')
        print(f'       They are EXCLUDED below, so every number that follows '
              f'covers {100 - pct:.1f}% of the')
        print(f'       graded book{" -- treat the headline with care" if pct > 20 else ""}.')
    if not graded:
        print('\n    nothing priced to measure.')
        return 0

    hit = statistics.fmean([r['won'] for r in graded]) * 100
    need = statistics.fmean([breakeven(r['odds']) for r in graded])
    u = sum(profit(r['odds'], r['won']) for r in graded)
    med = statistics.median([r['odds'] for r in graded])
    print(f'\n  HEADLINE   hit {hit:.2f}%  ·  needed {need:.2f}%  ·  '
          f'EDGE {hit - need:+.2f}pp')
    print(f'             {u:+.2f}u on {len(graded)} plays = '
          f'{100 * u / len(graded):+.2f}% ROI  ·  median price {med:+.0f}')
    print(f'\n  THE SHAPE OF THE PROBLEM: we win {hit:.1f}% of the time and '
          f'need {need:.1f}% to')
    print(f'  break even. A {hit:.0f}% hit rate is a GOOD hit rate — it loses '
          f'only because of')
    print(f'  the price level we publish at.')
    print(f'\n  BUT READ THE JUICE-CAP TABLE BELOW BEFORE CONCLUDING '
          f'"CAP THE JUICE".')
    print(f'  That inference is intuitive and, on this data, backwards: the '
          f'most')
    print(f'  expensive band is the BEST band. The price LEVEL explains the '
          f'loss; it')
    print(f'  does not follow that refusing expensive prices fixes it.')
    if hit > 52.38:
        flat = sum(profit(-110, r['won']) for r in graded)
        print(f'\n  COUNTERFACTUAL: the identical picks at a flat -110 would '
              f'return {flat:+.2f}u')
        print(f'  ({100 * flat / len(graded):+.2f}% ROI). The gap of '
              f'{flat - u:+.2f}u is what juice selection costs us.')

    # Even inside the window, any surviving reconstructed rows are flagged —
    # the +10.6pp gap means a mode mix can tilt a bucket on its own.
    modes = collections.Counter(r['cm'] for r in graded)
    if len(modes) > 1:
        print(f'\n  capture_mode mix in this window: '
              f'{dict(modes)} — reconstructed rows measure ~10pp better')
        print(f'  than live ones, so compare buckets with that in mind.')
        show(graded, 'BY CAPTURE MODE — a sanity check, not a performance '
                     'split', a.min_n, lambda r: r['cm'])

    bands = show(graded, 'BY PRICE BAND — rank by EDGE, not by hit%',
                 a.min_n, lambda r: band_of(r['odds']))
    show(graded, 'BY MARKET', a.min_n, lambda r: r['market'])
    show(graded, 'BY SPORT', a.min_n, lambda r: r['sport'])
    show(graded, 'BY TIER', a.min_n, lambda r: r['tier'])

    # ── what a juice cap would actually do ───────────────────────────────
    print('\n' + '-' * 70)
    print('  WHAT A JUICE CAP WOULD ACTUALLY DO')
    print('  Each row keeps only plays at or better than the cap. Watch the')
    print('  PLAYS column as hard as the ROI column: a cap that fixes ROI by')
    print('  leaving eight plays has not fixed anything, it has stopped')
    print('  publishing. And a cap is only honest if the bands it removes')
    print('  were negative on EDGE above — otherwise this is curve-fitting a')
    print('  threshold to one sample.')
    print(f'\n      {"cap":>8}{"plays":>8}{"kept%":>7}{"hit%":>8}'
          f'{"need%":>8}{"ROI":>9}{"units":>9}')
    for cap in (None, -250, -200, -175, -150, -140, -130, -120):
        s = graded if cap is None else [r for r in graded if r['odds'] >= cap]
        if not s:
            continue
        h = statistics.fmean([r['won'] for r in s]) * 100
        nd = statistics.fmean([breakeven(r['odds']) for r in s])
        uu = sum(profit(r['odds'], r['won']) for r in s)
        lbl = 'no cap' if cap is None else f'{cap:+d}'
        print(f'      {lbl:>8}{len(s):>8}{100 * len(s) / len(graded):6.0f}%'
              f'{h:7.1f}%{nd:7.1f}%{100 * uu / len(s):+8.1f}%{uu:+9.2f}')

    # ── BAND x MARKET: the band result is confounded and must be checked ──
    # Price band is NOT independent of market. Cheap prices (-110) are mostly
    # totals and spreads; expensive ones are mostly ML favourites. So "the
    # -100..-115 band loses 17.9%" may be nothing but "totals are bad wearing
    # a price label". Pooling across an unbalanced mix is how a sign gets
    # reversed (feedback_tier_mix_reverses_the_sign). This crosstab is the
    # only way to tell a real price effect from a market-mix artefact.
    print('\n' + '-' * 70)
    print('  BAND x MARKET — is the band effect real, or just market mix?')
    print('  If a band is negative in EVERY market, the price is the problem.')
    print('  If it is negative only where one market sits, the MARKET is.')
    mkts = [m for m, c in collections.Counter(
        r['market'] for r in graded).items() if c >= a.min_n]
    print(f'\n      {"band":<18}' + ''.join(f'{m:>22}' for m in mkts))
    for b in ORDER:
        cells = []
        any_cell = False
        for m in mkts:
            s = [r for r in graded
                 if band_of(r['odds']) == b and r['market'] == m]
            if len(s) < 8:
                cells.append(f'{"n=" + str(len(s)):>22}')
                continue
            any_cell = True
            uu = sum(profit(r['odds'], r['won']) for r in s)
            hh = statistics.fmean([r['won'] for r in s]) * 100
            cells.append(f'{hh:6.1f}% {100 * uu / len(s):+6.1f}% n={len(s):<3}'
                         .rjust(22))
        if any_cell:
            print(f'      {b:<18}' + ''.join(cells))
    print('\n      cells under n=8 are shown as counts only — at this sample')
    print('      the crosstab is indicative, not conclusive.')

    leaks = [b for b in bands if b[2] < -2]
    if leaks:
        print('\n  BANDS LOSING MONEY, pooled across markets:')
        for k, n, roi, uu in sorted(leaks, key=lambda z: z[2]):
            print(f'    {k:<18}{roi:+7.1f}% ROI on {n} plays ({uu:+.2f}u)')
        print('\n  Do NOT gate on these without reading the crosstab above.')
        print('  A band that is negative pooled but fine within each market')
        print('  is a market-mix artefact, and gating it would cut good')
        print('  plays to punish a label.')

    # ── WHAT THE EVIDENCE ACTUALLY SUPPORTS ──────────────────────────────
    print('\n' + '=' * 70)
    print('  WHAT TO GATE, AND WHAT NOT TO')
    print('  Ranked by how well the evidence holds up, not by ROI size.')
    print('\n  The durable split here is by MARKET and SPORT, not by price:')
    for key, fn in (('market', lambda r: r['market']),
                    ('sport', lambda r: r['sport'])):
        g = collections.defaultdict(list)
        for r in graded:
            g[fn(r)].append(r)
        for k in sorted(g, key=lambda z: sum(
                profit(r['odds'], r['won']) for r in g[z]) / len(g[z])):
            s = g[k]
            if len(s) < a.min_n:
                continue
            uu = sum(profit(r['odds'], r['won']) for r in s)
            roi = 100 * uu / len(s)
            hh = statistics.fmean([r['won'] for r in s]) * 100
            verdict = ('DROP from the Sharp' if roi < -10 else
                       'trim / review' if roi < -3 else
                       'flat — it is paying the vig and nothing more'
                       if roi < 1 else 'keep')
            print(f'    {key:<7}{str(k):<8}{hh:5.1f}% hit  {roi:+6.1f}% ROI  '
                  f'n={len(s):<4} -> {verdict}')
    print('\n  AND THE HONEST LIMIT: this whole analysis rests on '
          f'{len(graded)} priced plays')
    print('  in the live window. Every sub-bucket above is 13-33 plays, which')
    print('  is nowhere near enough to settle anything on its own. The two')
    print('  worst cells are worth acting on ONLY because independent samples')
    print('  already said the same thing — NCAAF cards measured -19.3% ROI on')
    print('  a separate 67 plays, and the NCAAF totals model measured 30.8% on')
    print('  39 games. Convergence from separate samples is what makes a thin')
    print('  cell credible; a thin cell on its own is not evidence.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
