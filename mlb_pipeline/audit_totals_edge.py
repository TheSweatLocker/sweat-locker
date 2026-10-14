#!/usr/bin/env python3
"""Does anything we own beat the closing TOTAL?

The totals counterpart to audit_feature_history_edge.py, built on the same
leak-tested feature_history rows. Andy 2026-10-13: "lets get all totals up to
speed".

WHY A SEPARATE AUDIT RATHER THAN REUSING THE SPREAD ONE
Totals are where the current projections are weakest, and the weakness is
measurable rather than theoretical:

  NFL    projected_total mean 45.00, sd 2.31 against a MARKET sd of 5.85.
         65% of games land within +/-1 of BASE_TOTAL 44.5 and 177 of 316
         round to exactly 44, because compute_projections blends 70%
         matchup with 30% anchored to BASE_TOTAL. Correlation with the
         market is +0.134. The O/U lean that falls out is mechanical: on
         games with a market total >= 47 it leans UNDER 91 of 100, and on
         games <= 41 it leans OVER 96%. That is the LINE'S LEVEL reflected
         back, not a read.
  NCAAF  its own signal is measured ANTI_VALIDATED --
         ncaaf_projected_total 43.1% on n=181, -9.3pp.
  NHL    projected_total is (avg_gf+avg_ga home + same away)/2 and nothing
         else: no goalie, no rest, no pace, no special teams, despite the
         context table already carrying xgf_per60, pp_pct, pk_pct and
         goalie SV%/GSAA.
  NBA    projection 228.69 vs market 233.39. NOT a bug -- nba_elo resolves
         to 2025-26, whose realised average total was 229.71, so the
         projection reflects last season's actual scoring and the market
         prices ~3.7 higher. Only 5 games have market+projection+final, so
         which is right is UNKNOWN. Correcting it toward the market would be
         mimicry, so it is monitored, not "fixed".
  NCAAB  ncaab_game_context is empty. No total model at all.

SAME THREE RULES AS THE SPREAD AUDIT
  1. Score against the RESIDUAL: target is `actual_total - close_total`,
     what the closing total MISSED -- never whether the game went over.
  2. A slope is not a bet. Anything that clears gets converted to an
     OVER/UNDER threshold rule and graded at -110 against the always-OVER
     baseline.
  3. Train and test split by TIME; only the out-of-sample number counts.

A NOTE ON THE BASELINE. For spreads the honest bar is always-home. For
totals it is always-OVER, and it matters more: if the market's totals carry
a systematic under-bias, an always-OVER bet already profits and any feature
rule must beat THAT, not 52.38%.

USAGE
    python audit_totals_edge.py --sport NFL
    python audit_totals_edge.py --sport NCAAF
"""
from __future__ import annotations

import argparse
import collections
import random
import statistics
import sys

import requests

from compute_margin_strength import H, SB

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'NHL': 'nhl_game_results', 'NBA': 'nba_game_results',
           'MLB': 'mlb_game_results', 'NCAAB': 'ncaab_game_results'}
BREAKEVEN = 110 / 210
BOOT = 2000
MIN_BUCKET = 100


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code} {r.text[:160]}')
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def corr(xs, ys):
    n = len(xs)
    if n < 10:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return (num / (dx * dy)) if dx and dy else None


def boot_ci(vals, n=BOOT):
    if len(vals) < 20:
        return None, None
    rnd = random.Random(20261013)
    out, k = [], len(vals)
    for _ in range(n):
        out.append(statistics.mean([vals[rnd.randrange(k)] for _ in range(k)]))
    out.sort()
    return out[int(0.025 * n)], out[int(0.975 * n)]


def roi_units(picks):
    return [0.0 if r == 'PUSH' else (100 / 110 if r == 'WIN' else -1.0)
            for r in picks]


def grade(rows, feat, thresh, over_when_high=True):
    picks = []
    for r in rows:
        v = r['f'].get(feat)
        if v is None:
            continue
        if v >= thresh:
            take_over = over_when_high
        elif v <= -thresh:
            take_over = not over_when_high
        else:
            continue
        resid = r['resid']
        if abs(resid) < 1e-9:
            picks.append('PUSH')
        else:
            picks.append('WIN' if (resid > 0) == take_over else 'LOSS')
    return picks


def run(sport: str) -> int:
    fh = page('feature_history', {'select': '*', 'sport': f'eq.{sport}'})
    if not fh:
        print(f'{sport}: feature_history empty — run build_feature_history --apply')
        return 1
    res = {}
    for g in page(RESULTS[sport], {'select': '*'}):
        gid = str(g.get('game_id') or '')
        hs, as_ = _f(g.get('home_score')), _f(g.get('away_score'))
        ct = _f(g.get('close_total'))
        if gid and hs is not None and as_ is not None and ct is not None:
            res[gid] = (hs + as_) - ct

    rows = []
    for r in fh:
        resid = res.get(str(r['game_id']))
        if resid is None:
            continue
        # srs_fed gates the rating block; the TOTAL block has its own
        # denominator, so gate on that instead of borrowing the spread flag.
        f = r['features'] or {}
        if (f.get('home_total_games') or 0) < 4 or (f.get('away_total_games') or 0) < 4:
            continue
        rows.append({'date': str(r['game_date'])[:10],
                     'season': str(r['season']), 'resid': resid, 'f': f})
    rows.sort(key=lambda x: x['date'])
    print(f'=== {sport} TOTALS: {len(fh)} feature rows, '
          f'{len(rows)} with >=4 priced games both sides AND a graded total')
    if len(rows) < 200:
        print('  too few rows to say anything')
        return 1

    cut = rows[int(len(rows) * 0.6)]['date']
    train = [r for r in rows if r['date'] < cut]
    test = [r for r in rows if r['date'] >= cut]
    print(f'  temporal split at {cut}: train={len(train)} test={len(test)}')

    allr = [r['resid'] for r in rows]
    lo, hi = boot_ci(allr)
    print(f'\n  residual (actual_total - close_total): '
          f'mean {statistics.mean(allr):+.3f} sd {statistics.pstdev(allr):.2f}')
    print(f'    bootstrap CI [{lo:+.3f}, {hi:+.3f}]'
          + ('  — straddles 0, the market total is unbiased'
             if lo is not None and lo < 0 < hi else
             '  — EXCLUDES 0: the closing total carries a systematic bias'))
    ov = sum(1 for x in allr if x > 0)
    nd = sum(1 for x in allr if abs(x) > 1e-9)
    print(f'    OVER hit rate: {ov}/{nd} ({100*ov/nd:.2f}%)  '
          f'breakeven {100*BREAKEVEN:.2f}%')

    feats = collections.Counter()
    for r in rows:
        for k, v in r['f'].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                feats[k] += 1
    cands = [k for k, n in feats.items() if n >= 0.8 * len(rows)]
    scored = []
    for k in cands:
        pr = [(r['f'][k], r['resid']) for r in train if r['f'].get(k) is not None]
        if len(pr) < MIN_BUCKET:
            continue
        c = corr([p[0] for p in pr], [p[1] for p in pr])
        if c is not None:
            scored.append((abs(c), c, k, len(pr)))
    scored.sort(reverse=True)
    print(f'\n  [1] correlation with the TOTALS residual, train '
          f'({len(cands)} features)')
    print(f"      {'feature':<28}{'corr':>9}{'n':>7}")
    for _a, c, k, n in scored[:12]:
        print(f'      {k:<28}{c:>+9.4f}{n:>7}')
    if not scored:
        return 0

    print(f'\n  [2] out of sample (test n={len(test)})')
    survivors = []
    for _a, c, k, _n in scored[:8]:
        pr = [(r['f'][k], r['resid']) for r in test if r['f'].get(k) is not None]
        if len(pr) < MIN_BUCKET:
            continue
        c2 = corr([p[0] for p in pr], [p[1] for p in pr])
        if c2 is None:
            continue
        print(f'      {k:<28}train {c:>+8.4f}  test {c2:>+8.4f}  '
              f'{"same sign" if (c > 0) == (c2 > 0) else "FLIPS"}')
        if (c > 0) == (c2 > 0) and abs(c2) > 0.02:
            survivors.append((k, c, c2))

    print(f'\n  [3] rule conversion, graded at -110 on TEST')
    # ══ BOTH DIRECTION BASELINES, NOT JUST ONE ══
    # Reporting only always-OVER is how a window's own bias gets mistaken for
    # skill. Every rule this audit surfaced on NFL says UNDER, and always-OVER
    # lost 6.82% on the same games -- so without the always-UNDER number
    # beside it, a "+4.49% UNDER rule" cannot be distinguished from simply
    # having been on the right side of the window.
    # feedback_signal_needs_a_direction_control, applied to totals.
    for lbl, want_over in (('always-OVER ', True), ('always-UNDER', False)):
        b = ['PUSH' if abs(r['resid']) < 1e-9 else
             ('WIN' if (r['resid'] > 0) == want_over else 'LOSS') for r in test]
        u = roi_units(b)
        w = sum(1 for x in b if x == 'WIN')
        d = sum(1 for x in b if x != 'PUSH')
        print(f'      BASELINE {lbl}: {w}/{d} ({100*w/d:.2f}%) '
              f'{sum(u):+.2f}u ROI {100*sum(u)/len(u):+.2f}%')

    if not survivors:
        ns = len({r['season'] for r in rows})
        print(f'\n      No feature held its sign out of sample. Across {ns} '
              f'seasons and {len(rows)} leak-free rows, nothing we own moves '
              f'the totals residual in a direction that persists.')
        return 0

    for k, c_tr, _c_te in survivors:
        hi_over = c_tr > 0
        vals = sorted(abs(r['f'][k]) for r in train if r['f'].get(k) is not None)
        best = None
        for q in (0.5, 0.6, 0.7, 0.8, 0.9):
            th = vals[int(q * (len(vals) - 1))]
            p = grade(train, k, th, hi_over)
            if sum(1 for x in p if x != 'PUSH') < MIN_BUCKET:
                continue
            u = roi_units(p)
            r_ = sum(u) / len(u)
            if best is None or r_ > best[1]:
                best = (th, r_, q)
        if best is None:
            continue
        th, tr_roi, q = best
        p = grade(test, k, th, hi_over)
        d = sum(1 for x in p if x != 'PUSH')
        if d < 30:
            print(f'      {k}: only {d} test bets — not enough')
            continue
        u = roi_units(p)
        w = sum(1 for x in p if x == 'WIN')
        lo2, hi2 = boot_ci(u)
        ci = f'CI[{100*lo2:+.1f}%, {100*hi2:+.1f}%]' if lo2 is not None else 'CI n/a'
        # DIRECTION-ONLY CONTROL: the same side, on the same selected games,
        # is already covered by the rule itself. The control that isolates the
        # feature is that side on EVERY test game -- if the rule's ROI is not
        # meaningfully above it, the feature selected nothing.
        ctrl = ['PUSH' if abs(r['resid']) < 1e-9 else
                ('WIN' if (r['resid'] > 0) == hi_over else 'LOSS') for r in test]
        cu = roi_units(ctrl)
        c_roi = 100 * sum(cu) / len(cu)
        rule_roi = 100 * sum(u) / len(u)
        lift = rule_roi - c_roi
        print(f'      {k}  take {"OVER" if hi_over else "UNDER"} when '
              f'|v| >= {th:.3f} (train q{q:.0%})')
        print(f'        train {100*tr_roi:+.2f}%  ->  TEST {w}/{d} '
              f'({100*w/d:.2f}%) {sum(u):+.2f}u {rule_roi:+.2f}% {ci} '
              f'→ {"EDGE" if lo2 is not None and lo2 > 0 else "no edge"}')
        print(f'        direction-only control (same side, all {len(ctrl)} '
              f'games): {c_roi:+.2f}%  → feature lift {lift:+.2f}pp'
              + ('  — the feature is selecting nothing'
                 if lift <= 0.5 else ''))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(RESULTS))
    return run(ap.parse_args().sport)


if __name__ == '__main__':
    sys.exit(main())
