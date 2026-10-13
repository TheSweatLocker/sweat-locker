"""Does a projection predict anything the LINE does not already know?

ANDY 2026-10-12: "investigate the cause first" — and earlier in the week,
"full deep dive on signals and imporvements in processes".

THE ONE TEST A PROJECTION HAS TO PASS. Regress what happened against what we
claimed, both measured from the line:

    x = projection - prop_line      our claimed deviation
    y = final_value - prop_line     what actually happened

    slope 1.0  our deviation is fully real
    slope 0.5  half of it is real — shrink the projection halfway to the line
    slope 0.0  the projection is noise; the line already knows everything

The line is the market's estimate, so measuring from it strips out everything
both parties already agree on and leaves only the part we are claiming to
know better. A projection can look excellent against the OUTCOME and still
score zero here, because predicting that a starting receiver gets ~35 yards
is not an edge — the line says that too.

MEASURED FOR NFL, 2026-10-12, since 09-01 (n=3,084 props carrying both a
projection and a final value):

    ALL POOLED        slope +0.030   SE 0.038   95% CI [-0.045, +0.105]
    reception_yds     +0.023   receptions   +0.099   rush_yds  +0.112
    pass_yds          -0.011   pass_cmp     +0.028   anytime_td +0.232

Not one family's interval excludes zero. corr(claimed_edge, realized) =
+0.002. When our number sits 8 yards above the line the result lands, on
average, 0.24 yards above it. The projection carries no information about
where the outcome falls relative to the line, which is the root cause of
props grading near breakeven-minus-vig.

WHY THIS IS A STANDING TOOL AND NOT A ONE-OFF. The same conclusion was
reached on 2026-09-24 and recorded as a note without a number, and the
projection kept driving published picks for three more weeks. A number with
a confidence interval can gate a model; a note cannot. So this is the
acceptance test: a projection may drive published picks only once its slope
is meaningfully positive with an interval excluding zero, and the shrinkage
it implies should be applied until then.

WHAT A FAILING SLOPE DOES *NOT* MEAN. It does not mean the props operation
has no edge — conviction blends other signals and is only 10% correlated
with the claimed edge (corr +0.099), while carrying a weak positive
relationship to the result (corr +0.036). It means the EDGE IS NOT COMING
FROM THE PROJECTION, so weighting the projection's deviation is spending
confidence on noise.

WRITES NOTHING.

CLI
    python audit_projection_slope.py --sport NFL
    python audit_projection_slope.py --sport NFL --since 2026-08-01
"""
from __future__ import annotations

import argparse
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

#: Only these tables store a numeric projection next to the realized value.
#: mlb_pipeline_props has final_value but NO projection column, so MLB cannot
#: be scored here — stating that beats silently returning an empty result.
TBL = {'NFL': 'nfl_pipeline_props'}


def page(table, params, cap=400000):
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


def ols(xs, ys):
    """slope, standard error, intercept — plain OLS, no library needed."""
    n = len(xs)
    if n < 10:
        return None, None, None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None, None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    s2 = sum(r * r for r in resid) / max(n - 2, 1)
    return b, (s2 / sxx) ** 0.5, a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL', choices=sorted(TBL))
    ap.add_argument('--since', default='2026-09-01')
    ap.add_argument('--min-n', type=int, default=60, dest='min_n')
    a = ap.parse_args()

    pr = page(TBL[a.sport], {
        'select': 'prop_type,prop_line,projection,final_value,direction,'
                  'conviction,game_date', 'game_date': f'gte.{a.since}'})
    fam = defaultdict(list)
    allp = []
    for p in pr:
        fv = f(p.get('final_value'))
        pj = f(p.get('projection'))
        pl = f(p.get('prop_line'))
        if None in (fv, pj, pl):
            continue
        x, y = pj - pl, fv - pl
        key = (str(p.get('prop_type')).replace('_over', '')
               .replace('_under', ''))
        fam[key].append((x, y))
        allp.append((x, y))

    print(f'=== projection slope · {a.sport} · since {a.since} ===')
    print('    x = projection - line   (what we claim to know)')
    print('    y = actual     - line   (what happened)')
    print('    slope 1.0 = fully real · 0.0 = the line already knew it\n')
    if len(allp) < a.min_n:
        print(f'  only {len(allp)} rows carry both a projection and a '
              f'final value — cannot judge')
        return 0

    print(f"{'family':<22}{'n':>6}{'slope':>9}{'SE':>7}{'95% CI':>20}"
          f"{'keep%':>8}{'intercept':>11}")
    print('-' * 84)
    rows = []
    for k, v in fam.items():
        if len(v) < a.min_n:
            continue
        b, se, i0 = ols([z[0] for z in v], [z[1] for z in v])
        if b is None:
            continue
        rows.append((len(v), k, b, se, i0))
    rows.sort(key=lambda z: -z[0])
    clears = 0
    for n, k, b, se, i0 in rows:
        lo, hi = b - 1.96 * se, b + 1.96 * se
        star = ''
        if lo > 0:
            star = ' *'
            clears += 1
        print(f'{k[:21]:<22}{n:>6}{b:>+9.3f}{se:>7.3f}  [{lo:+6.3f},{hi:+6.3f}]'
              f'{star:<2}{b * 100:>6.0f}%{i0:>11.2f}')
    b, se, i0 = ols([z[0] for z in allp], [z[1] for z in allp])
    lo, hi = b - 1.96 * se, b + 1.96 * se
    print('-' * 84)
    print(f"{'ALL POOLED':<22}{len(allp):>6}{b:>+9.3f}{se:>7.3f}  "
          f"[{lo:+6.3f},{hi:+6.3f}]{'':<2}{b * 100:>6.0f}%{i0:>11.2f}")

    print(f'\n  {clears} of {len(rows)} families have a slope whose interval '
          f'excludes zero.')
    print(f'  Pooled: keep {b * 100:.0f}% of the claimed deviation, i.e.')
    print(f'      projection_corrected = line + {b:.2f} * (projection - line)')
    if hi < 0.25:
        print('\n  VERDICT: this projection does not beat the line. It must')
        print('  not drive published picks on its own, and any conviction')
        print('  built from its deviation is confidence spent on noise.')
    elif lo > 0:
        print('\n  VERDICT: real but partial. Shrink toward the line by the')
        print('  factor above before using the deviation as an edge.')
    else:
        print('\n  VERDICT: indeterminate at this sample. Do not promote on')
        print('  it; re-run when n has grown.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
