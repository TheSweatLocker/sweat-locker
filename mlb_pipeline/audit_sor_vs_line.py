"""Does opponent-adjusted strength (SOR) beat the LINE, not just the outcome?

ANDY 2026-10-13: "I want full look at ... SOR/SOS data ... any piece of data
that helps us win needs to be analyzed."

WHY compute_margin_strength --validate IS NOT THIS. That mode walks forward
correctly and reports:

    win% differential   corr = +0.341
    raw margin diff     corr = +0.379
    SRS adjusted margin corr = +0.392      (n=4,362 NFL)

Those are correlations with the OUTCOME, and the outcome is exactly what the
closing line already predicts. A rating can correlate +0.39 with margin and
be worth nothing, because the market is at +0.7 on the same target. The only
question with money in it is whether the rating moves the RESIDUAL:

    residual = margin + home_line        (home_line = home handicap,
                                          positive means home getting points;
                                          load_games resolves the NFL sign
                                          inversion centrally)

WHY THE STORED COLUMNS CANNOT ANSWER IT. nfl_game_context.home_sor and the
NCAAF equivalents only began being written on 2026-10-10, so the GRADED
sample is NFL 14 games and NCAAF 45 — nothing can be concluded from that,
and waiting for it means waiting months. The ratings are therefore rebuilt
here walk-forward from results, which both solves the sample problem and
removes any doubt about leakage: a rating used to predict a game is fitted
only on games strictly BEFORE it.

(Checked rather than assumed: the stored columns are NOT leaked. Every
populated NFL row is October or later and the ones that differ from today's
value are the already-played games, i.e. they kept their point-in-time
figure. The limitation is sample size, not contamination.)

THE HYPOTHESIS ON RECORD, which this tests directly. From migration
20261010a: SOR's information is real but concentrated in CLOSE games and
decays monotonically with spread size — 0-3 +4.39pp, 3-7 +1.94, 7-14 +0.73,
14+ +0.23 — with the 0-3 band at n=396 against a 2SE band of 5.0, i.e.
inside noise. So the line buckets are reported, and the close band is the
one to watch.

MULTIPLICITY. Two sports x two measures x five line buckets is 20 cells;
roughly one clears p<0.05 by luck. Every cell carries its own bootstrap CI
and the count expected by chance is printed.

WRITES NOTHING.

CLI
    python audit_sor_vs_line.py --sport NFL
    python audit_sor_vs_line.py --sport NCAAF
"""
from __future__ import annotations

import argparse
import collections
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import compute_margin_strength as CMS

BUCKETS = [(0.0, 3.0, '0-3'), (3.0, 7.0, '3-7'), (7.0, 14.0, '7-14'),
           (14.0, 99.0, '14+')]


def boot_ci(vals, iters=6000, seed=53):
    rng = random.Random(seed)
    n = len(vals)
    out = []
    for _ in range(iters):
        t = 0.0
        for _i in range(n):
            t += vals[rng.randrange(n)]
        out.append(t / n)
    out.sort()
    return out[int(iters * 0.025)], out[int(iters * 0.975)]


def ols1(xs, ys):
    n = len(xs)
    if n < 25:
        return None, None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None, None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    s2 = sum(e * e for e in resid) / max(n - 2, 1)
    return b, math.sqrt(s2 / sxx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL',
                    choices=sorted(CMS.SPORT_CFG))
    a = ap.parse_args()
    cfg = CMS.SPORT_CFG[a.sport]

    games = CMS.clean_games(a.sport, CMS.load_games(a.sport))
    games = [g for g in games if g.get('date')]
    games.sort(key=lambda g: g['date'])
    print(f'=== SOR vs the LINE · {a.sport} ===')
    print(f'  {len(games)} graded games loaded')
    withline = [g for g in games if g.get('home_line') is not None]
    print(f'  {len(withline)} carry a closing line — only these can be '
          f'scored against the market\n')

    by_season = collections.defaultdict(list)
    for g in games:
        by_season[g.get('season') or str(g['date'])[:4]].append(g)

    rows = []
    for season, gs in sorted(by_season.items(), key=lambda kv: str(kv[0])):
        gs.sort(key=lambda g: g['date'])
        n = len(gs)
        if n < 60:
            continue
        # Walk forward: fit only on games strictly before the slice predicted.
        for cut in (int(n * 0.4), int(n * 0.6), int(n * 0.8)):
            hist, fut = gs[:cut], gs[cut:int(cut + n * 0.2)]
            if len(hist) < 40 or not fut:
                continue
            rating = CMS.fit_srs(hist, cfg)
            sos = CMS.sos_from(hist, rating)
            for g in fut:
                h, aw = g['home'], g['away']
                if h not in rating or aw not in rating:
                    continue
                if g.get('home_line') is None:
                    continue
                rows.append({
                    'sor_gap': rating[h] - rating[aw],
                    'sos_gap': (sos.get(h, 0.0) - sos.get(aw, 0.0)),
                    'line': g['home_line'],
                    'resid': g['margin'] + g['home_line'],
                    'margin': g['margin'],
                })
    print(f'  {len(rows)} walk-forward predictions with a closing line')
    if len(rows) < 100:
        print('  too few to judge')
        return 0

    # The line's own implied home margin is -home_line.
    impl = [-r['line'] for r in rows]
    print(f'  corr(SOR gap, actual margin)   = '
          f'{statistics.correlation([r["sor_gap"] for r in rows], [r["margin"] for r in rows]):+.3f}')
    print(f'  corr(SOR gap, LINE margin)     = '
          f'{statistics.correlation([r["sor_gap"] for r in rows], impl):+.3f}')
    print('  (the second being high is the point: SOR mostly re-derives the '
          'market)')

    cells = []
    for label, key in (('SOR gap', 'sor_gap'), ('SOS gap', 'sos_gap')):
        print(f'\n── {label} vs the RESIDUAL, by line size')
        print(f'    {"bucket":<10}{"n":>6}{"slope":>9}{"SE":>7}'
              f'{"95% CI":>20}{"topQ-botQ":>11}')
        print('    ' + '-' * 64)
        for lo, hi, bl in BUCKETS:
            sub = [r for r in rows if lo <= abs(r['line']) < hi]
            if len(sub) < 60:
                print(f'    {bl:<10}{len(sub):>6}  too thin')
                continue
            xs = [r[key] for r in sub]
            ys = [r['resid'] for r in sub]
            b, se = ols1(xs, ys)
            if b is None:
                continue
            # quintile spread on cover rate, a scale-free cross-check
            srt = sorted(sub, key=lambda r: r[key])
            q = max(1, len(srt) // 5)
            botq = sum(1 for r in srt[:q] if r['resid'] > 0) / q * 100
            topq = sum(1 for r in srt[-q:] if r['resid'] > 0) / q * 100
            lo_ci, hi_ci = b - 1.96 * se, b + 1.96 * se
            star = ' *' if (lo_ci > 0 or hi_ci < 0) else ''
            cells.append(star == ' *')
            print(f'    {bl:<10}{len(sub):>6}{b:>+9.3f}{se:>7.3f}'
                  f'   [{lo_ci:+6.3f},{hi_ci:+6.3f}]{star:<2}'
                  f'{topq - botq:>+10.1f}pp')
        allsub = rows
        b, se = ols1([r[key] for r in allsub], [r['resid'] for r in allsub])
        if b is not None:
            print(f'    {"ALL":<10}{len(allsub):>6}{b:>+9.3f}{se:>7.3f}'
                  f'   [{b - 1.96 * se:+6.3f},{b + 1.96 * se:+6.3f}]')

    exp = len(cells) * 0.05
    print('\n  ' + '=' * 66)
    print(f'  {len(cells)} bucket cells tested · {sum(cells)} cleared their '
          f'interval · ~{exp:.1f} expected by chance')
    print('  A slope of +0.1 means a 10-point SOR edge moves the residual one')
    print('  point. Anything near zero means the market already has it, which')
    print('  is the honest default for a public rating.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
