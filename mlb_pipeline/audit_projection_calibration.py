"""Is a sport's projected_total actually centred on the real total?

== 2026-11-12 . NCAAB's TOTAL PROJECTION IS SIX POINTS LOW, EVERY GAME =====
ANDY: "ncaab engine and models need more work alot more work."

ncaab_game_context.projected_total vs the actual final total, on games
with a POSTABLE closing total (feedback_consensus_line_must_be_posted --
79% of stored NCAAB closing lines were averages, so an unscreened
measurement here would be measuring against numbers no book offered):

    projected_total - actual   n=319  mean -6.03  CI [-7.96, -4.11]
    close_total     - actual   n=319  mean +0.49  CI [-1.30, +2.28]

The closing line is unbiased. Our projection is low by six points, and
the bias is FLAT in mismatch size -- adj_em gap 0-10 -5.53, 10-20 -6.01,
20+ -6.52 -- so it is a constant offset, not a blowout artifact.

WHY THAT MATTERS MORE THAN IT LOOKS. A constant offset makes the model
disagree with the market on almost every game, always toward the UNDER,
and that disagreement reads exactly like an edge. 17 games carried a
>=8 point projected-vs-close gap with no pick, every one of them UNDER.
Betting the raw disagreement:

    |proj-close| >= 5   n=200  106-94  53.0%  CI [46.1, 59.9]
    |proj-close| >= 8   n=122   66-56  54.1%  CI [45.2, 63.0]
    |proj-close| >= 10  n=87    45-42  51.7%  CI [41.2, 62.2]

Every CI spans the 52.38% break-even. And walk-forward -- offset fitted
on 2026-11-01..06, tested on 11-06..11, which is the only honest way to
ask (feedback_walk_forward_misses_feature_leak):

    offset on train   n=159  +4.91  CI [+2.25, +7.57]
    same on test      n=160  +7.15  CI [+4.37, +9.93]   <- replicates
    test residual after correcting   +2.24  CI [-0.54, +5.02]  unbiased
    MAE on test:  raw 15.08  corrected 14.20  CLOSING LINE 13.03
    corrected |diff| >= 5   n=66  32-34  48.5%  CI [36.4, 60.5]

So the bias is real and repeatable, correcting it does centre the
projection -- and the corrected projection is STILL less accurate than
simply reading the closing line, with no edge at any threshold. The
uncorrected "edge" WAS the bias.

THIS SCRIPT DOES NOT APPLY A CORRECTION. Shipping a fitted +6 would
make the model unbiased and no more useful, while making its
disagreements look smaller and more credible -- the worst of both.
What the engine needs is a total projection that beats the close on
MAE; until one exists, this reports the gap every day so a
projection-derived total pick cannot quietly become a published play
again (feedback_slope_is_not_a_betting_rule: state the side, the
threshold and the price, and grade THAT).

Exit 1 when a sport's bias CI excludes zero, so a regression in any
sport's projection shows up as a failing step rather than as picks.

USAGE
    python audit_projection_calibration.py
    python audit_projection_calibration.py --sport NCAAB --verbose
    python audit_projection_calibration.py --warn-only
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _l in _env.read_text().split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

from posted_line import is_postable      # noqa: E402  the one definition

#: sport -> (context table, results table, closing-total column)
SPORTS = {
    'NCAAB': ('ncaab_game_context', 'ncaab_game_results', 'close_total'),
    'NBA':   ('nba_game_context',   'nba_game_results',   'close_total'),
    'NHL':   ('nhl_game_context',   'nhl_game_results',   'close_total'),
    'NFL':   ('nfl_game_context',   'nfl_game_results',   'close_total'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results', 'close_total'),
    'MLB':   ('mlb_game_context',   'mlb_game_results',   'close_total'),
}

#: Below this the CI is too wide to call anything (feedback_sample_size_with_pct).
MIN_N = 30


def page(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = '1000'
        p['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f'{table} {r.status_code}: {r.text[:200]}')
        rows = r.json() or []
        out.extend(rows)
        if len(rows) < 1000:
            return out
        off += 1000


def mean_ci(v: list) -> tuple:
    n = len(v)
    if n < 2:
        return (n, 0.0, 0.0, 0.0)
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))
    se = sd / math.sqrt(n)
    return (n, m, m - 1.96 * se, m + 1.96 * se)


def pairs_for(sport: str) -> list:
    ctx_t, res_t, tcol = SPORTS[sport]
    ctx = page(ctx_t, {'select': f'game_date,home_team,away_team,projected_total,{tcol}'})
    res = page(res_t, {'select': 'game_date,home_team,away_team,home_score,away_score'})
    # Match on the UNORDERED pair. Orientation is not a fact at a neutral
    # site -- 7.2% of NCAAB games -- and a TOTAL does not care who is home
    # anyway, so keying on (date, home, away) would silently drop exactly
    # the games the rest of today's work was about.
    rmap = defaultdict(list)
    for r in res:
        if r.get('home_score') is None or r.get('away_score') is None:
            continue
        rmap[(r['game_date'], frozenset((str(r['home_team']),
                                         str(r['away_team']))))].append(r)
    out = []
    for c in ctx:
        if c.get('projected_total') is None or c.get(tcol) is None:
            continue
        if not is_postable(c[tcol]):
            continue        # an averaged line is not a line
        k = (c['game_date'], frozenset((str(c['home_team']), str(c['away_team']))))
        rs = rmap.get(k)
        if not rs or len(rs) != 1:
            continue        # ambiguous match -> skip, never guess
        r = rs[0]
        actual = float(r['home_score']) + float(r['away_score'])
        out.append((float(c['projected_total']), float(c[tcol]), actual))
    return out


def run(only: str | None = None, warn_only: bool = False,
        verbose: bool = False) -> int:
    sports = [only.upper()] if only else list(SPORTS)
    print('== audit_projection_calibration ==')
    print('is projected_total centred on the REAL total, and does it beat '
          'the closing line?\n')
    print(f"{'sport':7s} {'n':>5s} {'proj-actual':>24s} {'close-actual':>24s} "
          f"{'MAE proj':>9s} {'MAE close':>10s}")
    bad = []
    for sp in sports:
        try:
            P = pairs_for(sp)
        except RuntimeError as e:
            print(f'{sp:7s} READ FAILED — {e}')
            continue
        if len(P) < MIN_N:
            print(f'{sp:7s} {len(P):5d}   n<{MIN_N}, not quoting a mean')
            continue
        n, m, lo, hi = mean_ci([p - a for p, _, a in P])
        n2, m2, lo2, hi2 = mean_ci([c - a for _, c, a in P])
        mae_p = sum(abs(p - a) for p, _, a in P) / len(P)
        mae_c = sum(abs(c - a) for _, c, a in P) / len(P)
        biased = not (lo < 0 < hi)
        worse = mae_p > mae_c
        flag = ''
        if biased:
            flag += '  <-- BIASED'
        if worse:
            flag += '  <-- WORSE THAN THE CLOSE'
        print(f'{sp:7s} {n:5d}  {m:+7.2f} [{lo:+6.2f},{hi:+6.2f}]  '
              f'{m2:+7.2f} [{lo2:+6.2f},{hi2:+6.2f}]  {mae_p:9.2f} {mae_c:10.2f}{flag}')
        if biased:
            bad.append((sp, n, m, lo, hi, mae_p, mae_c))
        if verbose:
            for t in (5, 8, 10):
                sub = [(p, c, a) for p, c, a in P if abs(p - c) >= t]
                w = l = 0
                for p, c, a in sub:
                    if a == c:
                        continue
                    if (a < c) == (p < c):
                        w += 1
                    else:
                        l += 1
                tn = w + l
                if tn >= MIN_N:
                    hr = 100.0 * w / tn
                    se = 1.96 * math.sqrt(0.25 / tn) * 100
                    print(f'           betting |proj-close|>={t}: n={tn} {w}-{l} '
                          f'{hr:.1f}% CI [{hr - se:.1f},{hr + se:.1f}] (BE 52.4%)')
                else:
                    print(f'           betting |proj-close|>={t}: n={tn} '
                          f'— under {MIN_N}, not quoted')

    if bad:
        print()
        for sp, n, m, lo, hi, mae_p, mae_c in bad:
            direction = 'LOW' if m < 0 else 'HIGH'
            print(f'  {sp}: projected_total runs {abs(m):.2f} points {direction} '
                  f'(n={n}, CI [{lo:+.2f},{hi:+.2f}] excludes 0).')
            print(f'     A constant offset makes the model disagree with the '
                  f'market on nearly every game, always the same way, and '
                  f'that reads like an edge. MAE {mae_p:.2f} vs the close '
                  f'{mae_c:.2f}.')
        print(f'\n  FAIL: {len(bad)} sport(s) have a projected_total whose bias '
              f'CI excludes zero. A total pick derived from that projection is '
              f'a bet on the offset.')
        return 0 if warn_only else 1
    print('\n  OK: every sport with n>=%d has a projected_total centred on 0' % MIN_N)
    return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--sport')
    p.add_argument('--warn-only', action='store_true')
    p.add_argument('--verbose', action='store_true')
    a = p.parse_args()
    raise SystemExit(run(only=a.sport, warn_only=a.warn_only, verbose=a.verbose))
