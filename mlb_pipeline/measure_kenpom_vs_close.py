# -*- coding: utf-8 -*-
"""KenPom, POINT-IN-TIME, against the real closing number.

== WHY (2026-11-27) ==================================================
ANDY: "we have the gold standard of college basketball data (kenpom), not
being able to do data anayltics to find a better answer is lazy and
probably flat out wrong."

Fair, and the earlier work never touched KenPom at all. Here is what it
says when you do. Two point-in-time sources, no leak in either:

(A) `ncaab_game_results.home_adj_em / away_adj_em / adj_em_gap /
    pace_avg / projected_spread` -- 962 graded rows, all 2026-27. VERIFIED
    point-in-time: 365 of 365 teams carry MULTIPLE distinct adj_em values
    across their dated rows, climbing through the season (Notre Dame
    10.83 -> 13.86 over 8 games). A re-stamped column would show one value
    everywhere. The probe deliberately avoids a saturating counter, which
    is what made an earlier freeze probe report a false leak.

(B) `kenpom_cache` daily FanMatch snapshots -- 1,136 game rows over
    2026-11-01..11-26 carrying KenPom's own `HomePred`, `VisitorPred`,
    `HomeWP`, `PredTempo` and `Location`. 1,132 join to a settled result
    (100%). `Location` matters: KenPom's "Home" is OUR away team on 40 of
    them (neutral-site tournaments), so orientation is resolved per row
    rather than assumed.

NOT USABLE, and this is the trap worth naming: `_kenpom_cache_2024-25.json`
holds a 364-team season-FINAL snapshot. A season-final rating applied to a
game from that season CONTAINS that game's result, so it would read as an
enormous edge. It is the same leak `test_trend_enrich_no_leak` guards. The
leak check here is empirical as well: a MAE far BELOW the market's would
mean a snapshot is post-hoc rather than pregame.

== THE RESULT ========================================================
**THE MARKET IS ALREADY KENPOM.** `adj_em_gap` correlates **+0.975** with
the closing spread. That single number is the whole answer to why the
gold-standard data does not beat the number: it is the most widely
consumed input in the sport and the line is built on it.

    KenPom FanMatch MAE 9.86  vs  market MAE 9.61   (n=974)
    corr(KenPom gap vs line, actual vs line) = -0.0148, t=-0.46
    OLS actual ~ 1 + line + kenpom: line +1.062 (t +6.7),
                                    kenpom -0.062 (t -0.3)
    ATS backing KenPom's side: negative at every threshold
    ML from HomeWP vs the close: 37.2% on n=379, ROI -6.98%

A coefficient near 1 on the line with ~0 on KenPom means the line contains
it. That test is immune to any constant offset or scale error in the
rating, which matters: the first version of this study used a home-made
rating carrying a +4.07 pt bias, and A CONSTANT OFFSET READS LIKE AN EDGE
-- it pointed HOME on 4,823 games and AWAY on 691.

**EXCEPT ON TOTALS.** KenPom's predicted total is the one positive:

    |gap| >= 4   n=263   147-116   55.9%   +17.64u   ROI +6.71%  (+1.14 SE)
    |gap| >= 5   n=182   104- 78   57.1%   +16.55u   ROI +9.09%  (+1.29 SE)
    controls on the same games: always OVER -2.98%, always UNDER -6.11%
    both directions positive at >=4 (OVER 56.5%, UNDER 55.2%)
    all four weeks positive: +4.00 / +4.46 / +1.13 / +1.61%

That inverts against money flow, which measures positive on SIDES and
negative on totals (`measure_money_flow_edges.py`). Two different signals
carrying two different markets.

== WHAT THIS IS NOT ==================================================
+1.14 SE. It does NOT clear the bar, the thresholds were chosen after
looking, and `|gap| >= 8` flips negative on n=36. Nothing here should
carry weight or reach a card on this sample. Re-run as the season fills.

The other limit worth recording: `open_spread` / `open_total` fill 1,038 /
1,043 rows, ALL 2026-27. So "we bet the morning number, not the close" is
not testable on history -- the close is the only price 2024-25 and 2025-26
carry. A data gap to start closing, not a result.

    python measure_kenpom_vs_close.py
    python measure_kenpom_vs_close.py --refresh
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import sys
import unicodedata

import numpy as np

from ncaab_hist_cache import load_fanmatch, load_results

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                              # noqa: BLE001
    pass

ATS_WIN = 100.0 / 110.0
ATS_BE = 110.0 / 210.0


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def rep(label, picks, be=ATS_BE, units=None, floor=30):
    w = sum(1 for o in picks if o == 'W')
    l = sum(1 for o in picks if o == 'L')
    n = w + l
    if not n:
        print(f'  {label:<38} --')
        return
    u = (w * ATS_WIN - l) if units is None else units
    hit = w / n
    z = (hit - be) / math.sqrt(be * (1 - be) / n)
    note = '' if n >= floor else f'  (n<{floor}, no verdict)'
    print(f'  {label:<38} n={n:>4}  {w:>3}-{l:<3} {100 * hit:>5.1f}%  '
          f'{u:+7.2f}u  ROI {100 * u / n:+6.2f}%  {z:+5.2f} SE{note}')


def info(label, gap, resid):
    """corr(disagreement with the line, the game's own departure from it).

    Both sides are measured as departures from the line, so a constant
    bias in the rating cancels. If this is zero no threshold can work.
    """
    gap = np.asarray(gap, float)
    resid = np.asarray(resid, float)
    n = len(gap)
    if n < 30:
        print(f'  {label:<40} n={n} too small')
        return
    r = float(np.corrcoef(gap, resid)[0, 1])
    t = r * math.sqrt(n - 2) / math.sqrt(max(1 - r * r, 1e-12))
    print(f'  {label:<40} n={n:>4}  corr {r:+.4f}  t={t:+.2f}')


def norm(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode()
    s = s.lower().replace('.', '').replace(chr(39), '').replace('-', ' ')
    s = re.sub(r'\bst\b', 'state', s)
    s = re.sub(r'\buniv(ersity)?\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def imp(o):
    o = f(o)
    if o is None or -100 < o < 100:      # not an American price at all
        return None
    return (-o) / ((-o) + 100) if o < 0 else 100 / (o + 100)


def pay(o):
    o = f(o)
    if o is None or -100 < o < 100:
        return None
    return (100 / -o) if o < 0 else o / 100


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args()
    R = load_results(refresh=a.refresh, quiet=True)
    FM = load_fanmatch(refresh=a.refresh, quiet=True)

    # ------------------------------------------------------ (A) on-table
    rows = [r for r in R if r['adj_em_gap'] is not None
            and r['close_spread'] is not None and r['home_score'] is not None
            and r['spread_result'] in ('home_covered', 'away_covered', 'push')]
    rows.sort(key=lambda r: r['game_date'])
    print('=== (A) stored point-in-time KenPom on ncaab_game_results ===')
    print(f'{len(rows)} graded rows  '
          f'({rows[0]["game_date"]} -> {rows[-1]["game_date"]})')
    mkt = np.array([-f(r['close_spread']) for r in rows])
    act = np.array([r['home_score'] - r['away_score'] for r in rows])
    eg = np.array([f(r['adj_em_gap']) for r in rows])
    ps = np.array([f(r['projected_spread'])
                   if r.get('projected_spread') is not None else np.nan
                   for r in rows])
    print(f'  market MAE {np.abs(mkt - act).mean():.2f}  '
          f'corr {np.corrcoef(mkt, act)[0, 1]:+.3f}')
    print(f'  adj_em_gap vs the MARKET LINE: corr '
          f'{np.corrcoef(eg, mkt)[0, 1]:+.3f}   <-- the whole answer')
    info('adj_em_gap vs (actual - line)', eg, act - mkt)
    m = ~np.isnan(ps)
    if m.sum() > 30:
        info('projected_spread gap vs (actual-line)',
             ps[m] - mkt[m], act[m] - mkt[m])
        print(f'  our projected_spread MAE {np.abs(ps[m] - act[m]).mean():.2f} '
              f'vs market {np.abs(mkt[m] - act[m]).mean():.2f} '
              f'on n={int(m.sum())}  -- our projection is WORSE than the line')

    # ------------------------------------------------------ (B) fanmatch
    byday = {}
    for r in R:
        if r['home_score'] is None:
            continue
        byday.setdefault(r['game_date'], []).append(r)

    def find(d, home, away):
        hn, an = norm(home), norm(away)
        base = dt.date.fromisoformat(d)
        for off in (0, 1, -1):
            day = (base + dt.timedelta(days=off)).isoformat()
            for r in byday.get(day, []):
                if {norm(r['home_team']), norm(r['away_team'])} == {hn, an}:
                    return r
        return None

    recs = []
    joined = 0
    for g in FM:
        r = find(g['_snap_date'], g['Home'], g['Visitor'])
        if r is None:
            continue
        joined += 1
        if r['close_spread'] is None or r['spread_result'] not in (
                'home_covered', 'away_covered', 'push'):
            continue
        hp, vp = f(g['HomePred']), f(g['VisitorPred'])
        if hp is None or vp is None:
            continue
        flip = norm(g['Home']) != norm(r['home_team'])
        recs.append((g, r, (vp - hp) if flip else (hp - vp), hp + vp, flip))
    print(f'\n=== (B) KenPom FanMatch snapshots: {len(FM)} rows, '
          f'{joined} joined ({100 * joined / max(len(FM), 1):.0f}%) ===')
    if len(recs) < 30:
        print('  too few priced+graded rows for a verdict')
        return 0
    nflip = sum(1 for *_, fl in recs if fl)
    print(f'usable {len(recs)}; orientation flipped on {nflip} '
          f'(KenPom Home is our away -- neutral sites)')
    mk = np.array([-f(r['close_spread']) for _, r, _, _, _ in recs])
    ac = np.array([r['home_score'] - r['away_score']
                   for _, r, _, _, _ in recs])
    kp = np.array([pm for _, _, pm, _, _ in recs])
    print(f'  KenPom MAE {np.abs(kp - ac).mean():6.2f}  '
          f'corr {np.corrcoef(kp, ac)[0, 1]:+.3f}  '
          f'bias {(kp - ac).mean():+.2f}')
    print(f'  market MAE {np.abs(mk - ac).mean():6.2f}  '
          f'corr {np.corrcoef(mk, ac)[0, 1]:+.3f}  '
          f'bias {(mk - ac).mean():+.2f}')
    print('  (a MAE far BELOW the market would mean the snapshot is '
          'post-hoc, not pregame)')
    info('KenPom gap vs (actual - line)', kp - mk, ac - mk)
    X = np.column_stack([np.ones(len(kp)), mk, kp])
    b, *_ = np.linalg.lstsq(X, ac, rcond=None)
    s2 = ((ac - X @ b) ** 2).sum() / (len(kp) - 3)
    C = s2 * np.linalg.inv(X.T @ X)
    print(f'  OLS actual ~ 1 + line + kenpom:  '
          f'line {b[1]:+.3f} (t {b[1] / math.sqrt(C[1, 1]):+.1f})   '
          f'kenpom {b[2]:+.3f} (t {b[2] / math.sqrt(C[2, 2]):+.1f})')

    gp = (kp - mk) - (kp - mk).mean()
    print('\n  ATS backing the KenPom side (offset removed):')
    for lo in (0, 1, 2, 3, 4):
        pk = []
        for i, (g, r, pm, pt, fl) in enumerate(recs):
            if abs(gp[i]) < lo or r['spread_result'] == 'push':
                continue
            side = 'H' if gp[i] > 0 else 'A'
            won = (r['spread_result'] == 'home_covered') if side == 'H' \
                else (r['spread_result'] == 'away_covered')
            pk.append('W' if won else 'L')
        rep(f'|gap| >= {lo} pts', pk)

    print('\n  ML from HomeWP vs the stored close, and the FADE priced on')
    print('  the OTHER side at ITS own number (not a sign flip):')
    for edge in (0.02, 0.05):
        bk, bu, fk, fu = [], 0.0, [], 0.0
        for g, r, pm, pt, fl in recs:
            wp = f(g['HomeWP'])
            if wp is None or r['home_win'] is None:
                continue
            wp /= 100.0
            if fl:
                wp = 1 - wp
            for side, p0 in (('H', wp), ('A', 1 - wp)):
                odds = r['home_ml_close'] if side == 'H' else r['away_ml_close']
                opp = r['away_ml_close'] if side == 'H' else r['home_ml_close']
                iv = imp(odds)
                if iv is None or p0 - iv < edge:
                    continue
                won = r['home_win'] if side == 'H' else (not r['home_win'])
                if pay(odds) is not None:
                    bk.append('W' if won else 'L')
                    bu += pay(odds) if won else -1.0
                if pay(opp) is not None:
                    fk.append('L' if won else 'W')
                    fu += (-1.0) if won else pay(opp)
        rep(f'BACK KenPom, edge >= {edge:.0%}', bk, 0.5, bu)
        rep(f'FADE KenPom, edge >= {edge:.0%}', fk, 0.5, fu)
    print('  the fade hits 62.6% and STILL loses money -- heavy favourites.')
    print('  hit rate is not the metric; units are.')

    # ----------------------------------------------------------- totals
    tr = [(g, r, pt) for g, r, pm, pt, fl in recs
          if r['close_total'] is not None and pt is not None
          and r['total_result'] in ('over', 'under', 'push')]
    print(f'\n  TOTALS -- the one positive. n={len(tr)}')
    if len(tr) < 30:
        return 0
    kt = np.array([pt for _, _, pt in tr])
    ct = np.array([f(r['close_total']) for _, r, _ in tr])
    at = np.array([r['home_score'] + r['away_score'] for _, r, _ in tr])
    print(f'  KenPom MAE {np.abs(kt - at).mean():.2f}  '
          f'market MAE {np.abs(ct - at).mean():.2f}  '
          f'bias {(kt - at).mean():+.2f}')
    info('KenPom total gap vs (actual-total)', kt - ct, at - ct)
    g2 = (kt - ct) - (kt - ct).mean()
    rep('control: always OVER',
        ['W' if r['total_result'] == 'over' else 'L' for _, r, _ in tr
         if r['total_result'] != 'push'])
    rep('control: always UNDER',
        ['W' if r['total_result'] == 'under' else 'L' for _, r, _ in tr
         if r['total_result'] != 'push'])

    def tpk(lo, want=None):
        pk = []
        for i, (g, r, pt) in enumerate(tr):
            if abs(g2[i]) < lo or r['total_result'] == 'push':
                continue
            if want is not None and (g2[i] > 0) != (want > 0):
                continue
            side = 'O' if g2[i] > 0 else 'U'
            pk.append('W' if ((r['total_result'] == 'over') == (side == 'O'))
                      else 'L')
        return pk

    for lo in (0, 2, 3, 4, 5, 6, 8):
        rep(f'|gap| >= {lo}', tpk(lo))
    print('  split by direction -- both sides positive, so it is not an')
    print('  over/under lean wearing a rating:')
    for lo in (2, 4):
        rep(f'|gap| >= {lo}, points OVER', tpk(lo, 1))
        rep(f'|gap| >= {lo}, points UNDER', tpk(lo, -1))
    print('  by week -- a single hot week would show up here:')
    wk = {}
    for i, (g, r, pt) in enumerate(tr):
        if abs(g2[i]) < 2 or r['total_result'] == 'push':
            continue
        d = dt.date.fromisoformat(g['_snap_date'])
        key = (d - dt.timedelta(days=d.weekday())).isoformat()
        side = 'O' if g2[i] > 0 else 'U'
        wk.setdefault(key, []).append(
            'W' if ((r['total_result'] == 'over') == (side == 'O')) else 'L')
    for key in sorted(wk):
        rep(f'week of {key}', wk[key])
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
