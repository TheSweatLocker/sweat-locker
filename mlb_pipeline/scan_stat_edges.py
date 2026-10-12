"""Does any team-stat gap predict the part of the game the LINE missed?

ANDY 2026-10-12: "what does the relainship of totals and spreads with the
off vs def epa gap? nay patterns there or in anyother stat catgoriy in
comparison to opponents surely there ia s actionable data somewhere".

WHY MOST VERSIONS OF THIS QUESTION ANSWER THEMSELVES. "Do teams with
better EPA win more?" is trivially yes, and worth nothing — the market
prices team quality before we see it. The only question with money in it
is whether a stat gap predicts the RESIDUAL: the part of the result the
closing line did not already contain.

So nothing here is scored against the outcome. Everything is scored
against:

    ATS residual  = home_margin - home_spread_number
                    (>0 means home beat the number)
    TOTAL residual= actual_total - closing_total
                    (>0 means it went over)

A stat that correlates with those is finding something the market missed.
A stat that correlates with the outcome but NOT the residual is just
re-deriving the line, which is the trap this file exists to avoid.

HOW THE GAP IS BUILT. For a stat with an offensive and a defensive side
(off_epa_pp vs def_epa_pp) the matchup gap is

    (home_off - away_def) - (away_off - home_def)

i.e. how much better the home offence should do against this defence than
the away offence against that one. For a symmetric team stat with no
opposing side it falls back to home - away. Defensive columns where lower
is better are sign-flipped so positive always favours the home side.

MULTIPLICITY IS THE REAL RISK. Scanning ~20 stats x 2 markets x 2 sports
is ~80 tests; at p<0.05 roughly four will look significant by chance. So
every stat is printed, none are selected, each carries its own 2SE, and
the summary states how many would be expected to clear by luck alone. A
result that does not beat that bar is not a finding.

WRITES NOTHING.

CLI
    python scan_stat_edges.py --sport NCAAF
    python scan_stat_edges.py --sport NFL --min-n 60
"""
from __future__ import annotations

import argparse
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
except Exception:                                          # noqa: BLE001
    pass

import market_line as ML

#: sport -> (ctx table, results table, close_spread sign for home-favoured)
SPORTS = {'NFL': ('nfl_game_context', 'nfl_game_results', +1),
          'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results', -1)}

#: (offensive column base, defensive column base it attacks). None means
#: the stat has no opposing side and is compared home-vs-away directly.
PAIRS = [
    ('off_epa_pp', 'def_epa_pp'),
    ('pass_epa_pg', 'def_pass_epa_allowed'),
    ('rush_epa_pg', 'def_rush_epa_allowed'),
    ('pass_yds_pg', 'def_pass_ypg'),
    ('rush_yds_pg', 'def_rush_ypg'),
    ('pass_tds_pg', None),
    ('rush_tds_pg', None),
    ('third_down_pct', None),
    ('to_margin', None),
    ('turnovers_pg', None),
    ('penalties_pg', None),
    ('penalty_yds_pg', None),
    ('sacks_suffered_pg', 'def_sacks_pg'),
    ('off_explosiveness', 'def_explosiveness_allowed'),
    ('off_success_rate', 'def_success_rate_allowed'),
    ('l4_ppg', 'def_ppg'),
    ('pass_cpoe', None),
    ('rush_first_downs_pg', None),
]

#: Defensive stats where a LOWER number is better, so the gap is flipped.
LOWER_IS_BETTER = {'def_epa_pp', 'def_pass_epa_allowed', 'def_rush_epa_allowed',
                   'def_pass_ypg', 'def_rush_ypg', 'def_ppg',
                   'def_success_rate_allowed', 'def_explosiveness_allowed',
                   'turnovers_pg', 'penalties_pg', 'penalty_yds_pg',
                   'sacks_suffered_pg'}


def page(table, params, cap=40000):
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
            except Exception:                              # noqa: BLE001
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=sorted(SPORTS))
    ap.add_argument('--since', default='2025-08-01')
    ap.add_argument('--min-n', type=int, default=80, dest='min_n')
    a = ap.parse_args()
    ctx_t, res_t, sign = SPORTS[a.sport]

    ctx = page(ctx_t, {'select': '*', 'game_date': f'gte.{a.since}'})
    res = page(res_t, {'select': 'game_date,home_team,away_team,home_score,'
                                 'away_score,close_spread,close_total',
                       'game_date': f'gte.{a.since}'})
    idx = {(str(r['game_date'])[:10], str(r['home_team']).lower(),
            str(r['away_team']).lower()): r for r in res}

    games = []
    for g in ctx:
        k = (str(g.get('game_date'))[:10], str(g.get('home_team')).lower(),
             str(g.get('away_team')).lower())
        r = idx.get(k)
        if not r:
            continue
        hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
        cs = f(r.get('close_spread'))
        if cs is None:
            cs = f(g.get('close_spread'))
        ct = f(r.get('close_total')) or f(g.get('close_total'))
        if hs is None or as_ is None:
            continue
        rec = dict(g)
        margin = hs - as_
        # home's own spread number, in "positive = home getting points"
        rec['_ats'] = (margin - (cs * sign)) if cs is not None else None
        rec['_tot'] = (hs + as_ - ct) if ct is not None else None
        games.append(rec)

    print(f'=== stat-edge scan · {a.sport} · since {a.since} ===')
    print(f'  {len(games)} graded games joined to context')
    print('  scored against the RESIDUAL (what the line missed), never the '
          'outcome\n')
    if len(games) < a.min_n:
        print('  not enough games')
        return 0

    def gap(g, off_base, def_base):
        ho, ao = f(g.get(f'home_{off_base}')), f(g.get(f'away_{off_base}'))
        if ho is None or ao is None:
            return None
        if def_base is None:
            d = ho - ao
            return -d if off_base in LOWER_IS_BETTER else d
        hd, ad = f(g.get(f'home_{def_base}')), f(g.get(f'away_{def_base}'))
        if hd is None or ad is None:
            return None
        # home offence vs away defence, minus away offence vs home defence
        if def_base in LOWER_IS_BETTER:
            return (ho - ad) - (ao - hd)
        return (ho + ad) - (ao + hd)

    def corr(xs, ys):
        try:
            return statistics.correlation(xs, ys)
        except Exception:                                  # noqa: BLE001
            return float('nan')

    results = []
    for off_base, def_base in PAIRS:
        for mkt, key in (('ATS', '_ats'), ('TOTAL', '_tot')):
            pts = [(gap(g, off_base, def_base), g[key]) for g in games
                   if g.get(key) is not None]
            pts = [(x, y) for x, y in pts if x is not None]
            if len(pts) < a.min_n:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            if statistics.pstdev(xs) < 1e-9:
                continue
            r = corr(xs, ys)
            n = len(pts)
            # Quintile split: does the top fifth of the gap beat the number
            # more often than the bottom fifth?
            srt = sorted(pts, key=lambda p: p[0])
            q = max(1, n // 5)
            lo, hi = srt[:q], srt[-q:]
            lo_hit = sum(1 for _x, y in lo if y > 0) / len(lo) * 100
            hi_hit = sum(1 for _x, y in hi if y > 0) / len(hi) * 100
            spread_pp = hi_hit - lo_hit
            se2 = math.sqrt(0.25 / len(lo) + 0.25 / len(hi)) * 200
            label = f'{off_base} vs {def_base or "(h-a)"}'
            results.append((abs(spread_pp), spread_pp, r, n, lo_hit, hi_hit,
                            se2, mkt, label))

    results.sort(reverse=True)
    print(f"  {'market':<7}{'stat gap':<44}{'r':>7}{'botQ':>7}{'topQ':>7}"
          f"{'spread':>8}{'2SE':>6}{'n':>6}")
    print('  ' + '-' * 92)
    clear = 0
    for _abs, sp, r, n, lo, hi, se2, mkt, label in results:
        flag = ''
        if abs(sp) > se2:
            flag = '  <=='
            clear += 1
        print(f'  {mkt:<7}{label[:43]:<44}{r:>+7.3f}{lo:>6.0f}%{hi:>6.0f}%'
              f'{sp:>+8.1f}{se2:>6.0f}{n:>6}{flag}')

    exp = len(results) * 0.05
    print('\n  ' + '=' * 70)
    print(f'  {len(results)} tests run · {clear} cleared their own 2SE · '
          f'~{exp:.1f} expected by chance alone')
    if clear <= exp:
        print('  NOTHING HERE SURVIVES MULTIPLICITY. At this many tests that')
        print('  many hits is what randomness produces. Do not build on one.')
    else:
        print('  More hits than chance predicts — the ones marked <== are')
        print('  worth a dedicated out-of-sample test, not immediate use.')
    print('\n  botQ/topQ = how often the residual was positive (home beat the')
    print('  number / the game went over) in the bottom and top fifth of the')
    print('  gap. A real relationship shows a wide, monotone spread.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
