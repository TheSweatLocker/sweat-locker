#!/usr/bin/env python3
"""Step 4 — does anything in feature_history beat the closing line?

This is the question the whole feature-history project existed to make
answerable. Before it, the honest sample was 79 NFL / 203 NCAAF rows; it is
now 5,529 / 3,930 market-fed rows with the no-leak invariant proven
(build_feature_history --leak-test, validate_feature_history).

THREE RULES THIS SCRIPT OBEYS, EACH LEARNED THE HARD WAY
--------------------------------------------------------
1. SCORE AGAINST THE RESIDUAL, NEVER THE OUTCOME.
   The target is `margin + home_line` -- what the closing line MISSED -- not
   who won. A model that predicts winners is predicting the favourite, and
   project_models_dont_beat_the_close_1005 measured 65-79% ATS that was
   entirely leak when scored that way.

2. A SLOPE IS NOT A BETTING RULE (feedback_slope_is_not_a_betting_rule).
   A CI-clearing correlation with the residual is a finding, not a play. NFL
   SOR had a slope of +0.102 with a clean CI and lost 37.7% as a rule. So
   every feature that clears gets converted to a side/threshold/price and
   THAT is graded, at -110, against the always-home baseline.

3. TRAIN AND TEST ARE SPLIT BY TIME, and only the out-of-sample number is
   reported as the result. An in-sample threshold picked from the same rows it
   is scored on is the leak that keeps being rediscovered here.

Prices: spread bets are graded at a flat -110 (52.38% breakeven), which is
close to the real market for a side. Not reconstructed from line_history, so
project_reconstructed_prices_inflate_roi_1009 does not apply.

USAGE
    python audit_feature_history_edge.py --sport NFL
    python audit_feature_history_edge.py --sport NCAAF
"""
from __future__ import annotations

import argparse
import collections
import random
import statistics
import sys

import requests

from compute_margin_strength import H, SB, clean_games, load_games

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

PRICE = -110
BREAKEVEN = 110 / 210          # 0.5238
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


def corr(xs, ys):
    n = len(xs)
    if n < 10:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return (num / (dx * dy)) if dx and dy else None


def boot_ci(vals, stat=statistics.mean, n=BOOT, lo=2.5, hi=97.5):
    if len(vals) < 20:
        return None, None
    rnd = random.Random(20261013)
    out = []
    k = len(vals)
    for _ in range(n):
        out.append(stat([vals[rnd.randrange(k)] for _ in range(k)]))
    out.sort()
    return out[int(lo / 100 * n)], out[int(hi / 100 * n)]


def roi_units(picks) -> list[float]:
    """Per-bet unit P&L at -110. WIN +0.909, LOSS -1.0, PUSH 0."""
    out = []
    for res in picks:
        if res == 'PUSH':
            out.append(0.0)
        elif res == 'WIN':
            out.append(100 / 110)
        else:
            out.append(-1.0)
    return out


def grade_rule(rows, feat, thresh, side_when_high='home'):
    """Bet a side whenever |feature| clears the threshold. -> list of results."""
    picks = []
    for r in rows:
        v = r['f'].get(feat)
        if v is None:
            continue
        if v >= thresh:
            backing_home = (side_when_high == 'home')
        elif v <= -thresh:
            backing_home = (side_when_high != 'home')
        else:
            continue
        resid = r['resid']
        if abs(resid) < 1e-9:
            picks.append('PUSH')
        else:
            home_covered = resid > 0
            picks.append('WIN' if home_covered == backing_home else 'LOSS')
    return picks


def run(sport: str) -> int:
    fh = page('feature_history', {'select': '*', 'sport': f'eq.{sport}'})
    if not fh:
        print(f'{sport}: feature_history empty — run build_feature_history '
              f'--apply first')
        return 1
    games = {}
    for g in clean_games(sport, load_games(sport), quiet=True):
        if g.get('game_id'):
            games[str(g['game_id'])] = g

    rows = []
    for r in fh:
        g = games.get(str(r['game_id']))
        if not g or g.get('home_line') is None:
            continue
        if not (r['srs_fed'] and r['market_fed']):
            continue
        rows.append({'date': str(r['game_date'])[:10], 'season': str(r['season']),
                     'resid': g['margin'] + g['home_line'],
                     'f': r['features'] or {}})
    rows.sort(key=lambda x: x['date'])
    print(f'=== {sport}: {len(fh)} feature rows, '
          f'{len(rows)} fed AND priced AND joined to a result')
    if len(rows) < 200:
        print('  too few rows to say anything')
        return 1

    cut = rows[int(len(rows) * 0.6)]['date']
    train = [r for r in rows if r['date'] < cut]
    test = [r for r in rows if r['date'] >= cut]
    print(f'  temporal split at {cut}: train={len(train)} test={len(test)}')

    resid_all = [r['resid'] for r in rows]
    print(f'  residual (margin + home_line): mean {statistics.mean(resid_all):+.3f} '
          f'sd {statistics.pstdev(resid_all):.2f}')
    lo, hi = boot_ci(resid_all)
    print(f'    bootstrap CI on the mean: [{lo:+.3f}, {hi:+.3f}]'
          + ('  — straddles 0, the line is unbiased overall'
             if lo is not None and lo < 0 < hi else
             '  — EXCLUDES 0, the line carries a side bias'))

    # ── correlation of every numeric feature with the residual ───────
    feats = collections.Counter()
    for r in rows:
        for k, v in r['f'].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                feats[k] += 1
    cands = [k for k, n in feats.items() if n >= 0.8 * len(rows)]
    print(f'\n  [1] correlation with the RESIDUAL, in-sample on train '
          f'({len(cands)} features on >=80% of rows)')
    scored = []
    for k in cands:
        pairs = [(r['f'][k], r['resid']) for r in train if r['f'].get(k) is not None]
        if len(pairs) < MIN_BUCKET:
            continue
        c = corr([p[0] for p in pairs], [p[1] for p in pairs])
        if c is not None:
            scored.append((abs(c), c, k, len(pairs)))
    scored.sort(reverse=True)
    print(f"      {'feature':<28}{'corr':>8}{'n':>7}")
    for _a, c, k, n in scored[:12]:
        print(f'      {k:<28}{c:>+8.4f}{n:>7}')
    if not scored:
        print('      nothing measurable')
        return 0

    # ── out-of-sample check on the top features ──────────────────────
    print(f'\n  [2] the same features OUT OF SAMPLE (test set, n={len(test)})')
    print(f"      {'feature':<28}{'train':>9}{'test':>9}{'holds?'}")
    survivors = []
    for _a, c, k, _n in scored[:8]:
        pairs = [(r['f'][k], r['resid']) for r in test if r['f'].get(k) is not None]
        if len(pairs) < MIN_BUCKET:
            continue
        c2 = corr([p[0] for p in pairs], [p[1] for p in pairs])
        if c2 is None:
            continue
        holds = (c > 0) == (c2 > 0) and abs(c2) > 0.02
        print(f'      {k:<28}{c:>+9.4f}{c2:>+9.4f}  '
              f'{"same sign" if (c>0)==(c2>0) else "FLIPS"}')
        if holds:
            survivors.append((k, c, c2))

    # ── convert to an actual rule and grade THAT ─────────────────────
    print(f'\n  [3] rule conversion — a slope is not a bet '
          f'(threshold picked on TRAIN, graded on TEST, flat {PRICE})')
    base_home = [('PUSH' if abs(r['resid']) < 1e-9 else
                  'WIN' if r['resid'] > 0 else 'LOSS') for r in test]
    bu = roi_units(base_home)
    bw = sum(1 for x in base_home if x == 'WIN')
    bd = sum(1 for x in base_home if x != 'PUSH')
    print(f'      BASELINE always-home ATS on test: {bw}/{bd} '
          f'({100*bw/bd:.2f}%) {sum(bu):+.2f}u '
          f'ROI {100*sum(bu)/len(bu):+.2f}%  (breakeven {100*BREAKEVEN:.2f}%)')

    if not survivors:
        nseasons = len({r['season'] for r in rows})
        print(f'\n      No feature held its sign out of sample, so there is no '
              f'rule to convert. The honest answer: across {nseasons} seasons '
              f'and {len(rows)} leak-free rows, nothing we own moves the '
              f'residual in a direction that persists.')
        return 0

    for k, c_tr, c_te in survivors:
        side = 'home' if c_tr > 0 else 'away'
        vals = sorted(abs(r['f'][k]) for r in train if r['f'].get(k) is not None)
        best = None
        for q in (0.5, 0.6, 0.7, 0.8, 0.9):
            th = vals[int(q * (len(vals) - 1))]
            p = grade_rule(train, k, th, side)
            d = sum(1 for x in p if x != 'PUSH')
            if d < MIN_BUCKET:
                continue
            u = roi_units(p)
            r_ = sum(u) / len(u)
            if best is None or r_ > best[1]:
                best = (th, r_, q)
        if best is None:
            continue
        th, tr_roi, q = best
        p = grade_rule(test, k, th, side)
        d = sum(1 for x in p if x != 'PUSH')
        if d < 30:
            print(f'      {k}: only {d} test bets — not enough to grade')
            continue
        u = roi_units(p)
        w = sum(1 for x in p if x == 'WIN')
        lo2, hi2 = boot_ci(u)
        ci = f'CI[{100*lo2:+.1f}%, {100*hi2:+.1f}%]' if lo2 is not None else 'CI n/a'
        verdict = ('EDGE' if lo2 is not None and lo2 > 0 else 'no edge')
        print(f'      {k}  back {side} when |v| >= {th:.3f} (train q{q:.0%})')
        print(f'        train ROI {100*tr_roi:+.2f}%  ->  TEST {w}/{d} '
              f'({100*w/d:.2f}%) {sum(u):+.2f}u ROI '
              f'{100*sum(u)/len(u):+.2f}% {ci}  → {verdict}')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True)
    return run(ap.parse_args().sport)


if __name__ == '__main__':
    sys.exit(main())
