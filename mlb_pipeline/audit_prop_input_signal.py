"""Which stored prop input predicts what the LINE missed?

ANDY 2026-10-12: "we jsut need to focus on the data eladng to the pick
better datat, more eyes on whats driving these, there has to be a way to
improve beyond what we ahve done or investigated".

He is right that this is the question, and the earlier finding is why. The
NFL prop projection is

    0.60 * L4 + 0.35 * season_avg + 0.05 * league_baseline

— the player's own recent average and nothing else. No opponent, no usage
trend, no efficiency, no game script. The line contains the player's form
PLUS all of that, so the projection is a subset of what the market already
priced, and its slope against the residual is +0.030 CI[-0.045,+0.105]
(audit_projection_slope.py). That is not a broken model so much as an
under-fed one.

But the generator already STORES much more than it uses, in the signals
blob: opp_pct, _l4_target_share, _l4_wopr, _l4_air_yards_share, _l4_racr,
_l4_pacr, _l4_rec_epa, _l4_rush_epa, _l4_targets, _stat_avg_l5/l10, and
more. None of those enter the projection. This measures whether any of them
carries information the line does not already have.

WHAT IS SCORED. Always the RESIDUAL, never the outcome:

    residual = final_value - prop_line

A stat that predicts final_value is worthless — the line predicts that too.
A stat that predicts the residual is finding something the market missed.

HOW FAMILIES ARE POOLED. target_share and pass_yds live on different
scales, so within each prop family both the input and the residual are
z-scored, then pooled across families. One test per input instead of one
per (input x family) — which both cuts the multiplicity from ~250 tests to
~25 and gains power.

THE BUILT-IN CONTROL. l4, season_avg and league_baseline are the
projection's OWN inputs, and the slope test says they carry ~nothing. They
are scored here alongside everything else. If they come out near zero while
some other input clears its interval, the method is validating itself on a
known answer. If they come out strong, the method is wrong and nothing here
should be believed.

MULTIPLICITY IS STATED, NOT HIDDEN. ~25 inputs at p<0.05 yields about one
false positive. A single input clearing its band is therefore NOT a
finding; what counts is an input clearing it with a sane magnitude AND
holding up in the per-family breakdown printed beneath the pooled table.

WRITES NOTHING.

CLI
    python audit_prop_input_signal.py
    python audit_prop_input_signal.py --since 2026-08-01 --min-n 150
"""
from __future__ import annotations

import argparse
import json
import math
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

#: The projection's own inputs — scored as a control, not as candidates.
CONTROL = {'l4', 'season_avg', 'league_baseline', 'edge_pct'}

#: Keys that are not features: text, lists, bookkeeping.
SKIP = {'label', 'opp_col', '_line', '_direction', '_stat_last10',
        '_shadow_tier', 'l10_hot', 'l5_cold', 'l5_confirm', 'l10_mixed',
        'l5_mixed', 'def_top10', 'def_bot10', 'implied_high',
        'target_share_low', 'games_used', '_stat_games_played'}


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
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def ols(xs, ys):
    n = len(xs)
    if n < 20:
        return None, None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None, None
    b = sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    s2 = sum(r * r for r in resid) / max(n - 2, 1)
    return b, (s2 / sxx) ** 0.5


def zs(vals):
    if len(vals) < 3:
        return None
    m = statistics.fmean(vals)
    sd = statistics.pstdev(vals)
    if sd <= 1e-12:
        return None
    return [(v - m) / sd for v in vals]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-09-01')
    ap.add_argument('--min-n', type=int, default=120, dest='min_n')
    a = ap.parse_args()

    pr = page('nfl_pipeline_props', {
        'select': 'prop_type,prop_line,projection,final_value,direction,'
                  'signals,game_date', 'game_date': f'gte.{a.since}'})

    # family -> list of (signals dict, residual)
    fam = defaultdict(list)
    for p in pr:
        fv, pl = f(p.get('final_value')), f(p.get('prop_line'))
        if fv is None or pl is None or pl == 0:
            continue
        s = p.get('signals')
        if isinstance(s, str):
            try:
                s = json.loads(s)
            except Exception:                                  # noqa: BLE001
                s = None
        if not isinstance(s, dict):
            continue
        key = (str(p.get('prop_type')).replace('_over', '')
               .replace('_under', ''))
        fam[key].append((s, fv - pl))

    total = sum(len(v) for v in fam.values())
    print(f'=== prop input vs the RESIDUAL · NFL · since {a.since} ===')
    print(f'    {total} props with a signals blob and a graded value, '
          f'{len(fam)} families')
    print('    residual = final_value - prop_line; inputs and residual are')
    print('    z-scored WITHIN family, then pooled.\n')
    if total < a.min_n:
        print('  not enough rows')
        return 0

    # pooled standardized pairs per input
    pooled = defaultdict(list)
    perfam = defaultdict(dict)
    for key, rows in fam.items():
        if len(rows) < 40:
            continue
        resid = [r for _s, r in rows]
        zr = zs(resid)
        if zr is None:
            continue
        names = set()
        for s, _r in rows:
            names |= {k for k in s if k not in SKIP}
        for nm in names:
            xs, ys = [], []
            for (s, _r), zri in zip(rows, zr):
                v = f(s.get(nm))
                if v is None:
                    continue
                xs.append(v)
                ys.append(zri)
            if len(xs) < 40:
                continue
            zx = zs(xs)
            if zx is None:
                continue
            pooled[nm] += list(zip(zx, ys))
            b, se = ols(zx, ys)
            if b is not None:
                perfam[nm][key] = (b, se, len(xs))

    results = []
    for nm, pts in pooled.items():
        if len(pts) < a.min_n:
            continue
        b, se = ols([p[0] for p in pts], [p[1] for p in pts])
        if b is None:
            continue
        results.append((abs(b), nm, b, se, len(pts)))
    results.sort(reverse=True)

    print(f"{'input':<26}{'n':>6}{'slope':>9}{'SE':>7}{'95% CI':>20}  role")
    print('-' * 80)
    clears = []
    for _ab, nm, b, se, n in results:
        lo, hi = b - 1.96 * se, b + 1.96 * se
        star = ''
        if not (lo <= 0 <= hi):
            star = ' *'
            if nm not in CONTROL:
                clears.append(nm)
        role = 'CONTROL (in projection)' if nm in CONTROL else 'candidate'
        print(f'{nm[:25]:<26}{n:>6}{b:>+9.3f}{se:>7.3f}  '
              f'[{lo:+6.3f},{hi:+6.3f}]{star:<2}{role}')

    exp = len(results) * 0.05
    print('-' * 80)
    print(f'  {len(results)} inputs tested · {len(clears)} candidates cleared '
          f'their band · ~{exp:.1f} expected by chance')
    ctrl = [nm for _a, nm, b, se, n in results if nm in CONTROL
            and not (b - 1.96 * se <= 0 <= b + 1.96 * se)]
    print(f'  CONTROL check: {len(ctrl)} of the projection\'s own inputs '
          f'cleared. Expected ~0 — the slope test already says they carry')
    print('  nothing, so a control that lights up means the method is wrong.')

    for nm in clears:
        print(f'\n  PER-FAMILY breakdown for {nm} (a pooled result that lives '
              f'in one family is not a finding):')
        for famname, (b, se, n) in sorted(perfam.get(nm, {}).items(),
                                          key=lambda kv: -kv[1][2]):
            lo, hi = b - 1.96 * se, b + 1.96 * se
            mark = ' *' if not (lo <= 0 <= hi) else ''
            print(f'      {famname[:22]:<24}{n:>5}  {b:+6.3f}  '
                  f'[{lo:+6.3f},{hi:+6.3f}]{mark}')
    if not clears:
        print('\n  Nothing cleared. The unused inputs do not beat the line')
        print('  either, which would mean the projection is not merely')
        print('  under-fed — the whole stored feature set is priced in.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
