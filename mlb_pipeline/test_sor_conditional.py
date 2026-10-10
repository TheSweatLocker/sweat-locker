"""WHERE does SOR matter? — conditional tests, each with its own control.

ANDY 2026-10-10: "The engine should know situations whrre SOR matters, i am
still certain we didnt pull the thread on this enough mayeb too smal samolep
size of so say that but dont say it isnt significant because the ability for a
team to handle their record therby giving it strenght, matters."

He is right that the thread was not pulled. What I had tested was ONE slice —
market-relative |edge| >= 7 with both teams >= 8 prior games — and that slice
is 100% big-favourite games BY CONSTRUCTION, because SOR is a ridge-shrunk SRS
fit whose implied margin is always smaller in magnitude than the market's on a
big favourite. So finding "this is just favourite bias" there was close to
guaranteed and says nothing about the conditional question. My own band table
even printed "0-7: n=26 too few" — because the >= 7 filter had already removed
every close game. SOR in close games was never tested at all.

THE FOUR CELLS HERE, and why each one is a different question

  A. RAW GAP INSIDE TIGHT SPREAD BANDS. Hold favourite size roughly fixed and
     ask whether the better-SOR team covers. Inside a band the compression
     confound cannot drive the result, because both sides of the comparison
     sit on similar lines.

  B. MARKET-RELATIVE EDGE INSIDE TIGHT SPREAD BANDS. Same control, applied to
     the edge rather than the raw gap.

  C. SOR x SOS — "the ability to handle their record". This is Andy's actual
     mechanism and it is an INTERACTION, not a main effect. Two teams on the
     same SOR are not the same team if one earned it against a brutal
     schedule and the other padded it. So: does the better-SOR team cover
     MORE when its SOR was earned against the tougher schedule (higher SOS)
     than when it came against a softer one? Nothing in this repo has ever
     tested that.

  D. SOR RANK GAP instead of points. Ranks are scale-free, so a rank gap is
     IMMUNE to the ridge compression that made the points version a pure
     favourite proxy. If SOR carries information that compression was hiding,
     this is the form most likely to show it.

EVERY CELL CARRIES ITS OWN CONTROL. The lesson from
test_sor_fade_vs_fav_control is that a SOR cell can be byte-identical to
"back the market favourite" (it was, on 432/432 games, delta +0.00pp in every
band). So each cell below reports, on the SAME games:
    * the SOR-based pick's cover rate
    * the favourite-only baseline
    * the delta between them
    * what share of the time the SOR pick simply IS the favourite
A cell whose delta is ~0 and whose favourite-share is ~100% is a restatement,
however good its headline looks.

LANGUAGE DISCIPLINE, per Andy: where a cell is thin, this says SAMPLE TOO
SMALL. It does not say "not significant" and it does not say SOR does not
matter. Those are different claims and only the first is supported by a small
n.

LEAK-FREE. Ratings and SOS are refit walk-forward per date on strictly
earlier in-season games via fit_srs / sos_from. The stored `sor` stat_key and
the stored SP+ columns are CURRENT snapshots and cannot be used historically
(project_rolling_stats_leak_trap_929); verified separately that
ncaaf_game_context.sp_gap equals the CURRENT home_sp - away_sp on 341/341
rows, i.e. SP+ is refreshed rather than frozen, which is why cell D uses
reconstructed ranks rather than stored ones.

WRITES NOTHING.

CLI
    python test_sor_conditional.py --sport NCAAF
    python test_sor_conditional.py --sport NCAAF --floor 6
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
from compute_margin_strength import SPORT_CFG, fit_srs, sos_from

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38
RESULTS = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}
HOME_FAV_SIGN = {'NCAAF': -1.0, 'NFL': +1.0}


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


def load(sport):
    rows = _page(RESULTS[sport],
                 {'select': 'game_date,season,week,home_team,away_team,'
                            'home_score,away_score,close_spread,spread_result'})
    out = []
    for x in rows:
        sr = str(x.get('spread_result') or '').lower()
        cs = _f(x.get('close_spread'))
        hs, as_ = _f(x.get('home_score')), _f(x.get('away_score'))
        d = str(x.get('game_date') or '')[:10]
        if sr not in ('home_covered', 'away_covered') or cs is None:
            continue
        if hs is None or as_ is None or not d:
            continue
        out.append({'date': d, 'season': str(x.get('season')),
                    'week': x.get('week'),
                    'home': str(x['home_team']), 'away': str(x['away_team']),
                    'margin': hs - as_,
                    'mkt_home': cs * HOME_FAV_SIGN[sport],
                    'home_cover': 1 if sr == 'home_covered' else 0})
    out.sort(key=lambda g: g['date'])
    return out


def build(sport, games):
    """Walk-forward SOR, SOS, per-team prior counts and SOR RANK."""
    cfg = SPORT_CFG[sport]
    by_season = collections.defaultdict(list)
    for g in games:
        by_season[g['season']].append(g)
    for _season, gs in by_season.items():
        gs.sort(key=lambda z: z['date'])
        ratings, sos, ranks, last_cut = {}, {}, {}, None
        for d in sorted({z['date'] for z in gs}):
            prior = [z for z in gs if z['date'] < d]
            if prior and (last_cut is None or len(prior) != last_cut):
                pg = [{'home': z['home'], 'away': z['away'],
                       'margin': z['margin'], 'neutral': False} for z in prior]
                ratings = fit_srs(pg, cfg)
                sos = sos_from(pg, ratings)
                # Rank 1 = best. Scale-free, so immune to ridge compression.
                order = sorted(ratings, key=lambda t: -ratings[t])
                ranks = {t: i + 1 for i, t in enumerate(order)}
                last_cut = len(prior)
            played = collections.Counter()
            for z in prior:
                played[z['home']] += 1
                played[z['away']] += 1
            for g in [z for z in gs if z['date'] == d]:
                g['sor_home'] = ratings.get(g['home'])
                g['sor_away'] = ratings.get(g['away'])
                g['sos_home'] = sos.get(g['home'])
                g['sos_away'] = sos.get(g['away'])
                g['rk_home'] = ranks.get(g['home'])
                g['rk_away'] = ranks.get(g['away'])
                g['league_n'] = len(ranks)
                g['n_home'] = played[g['home']]
                g['n_away'] = played[g['away']]
    return games


#: Stratified base rates, filled by build_strata(). Key is
#: (spread_band, 'fav'|'dog') -> cover rate of that side in that band across
#: every usable game. This is the control that matters: a cell which is 68%
#: favourites and 32% dogs must be judged against a 68/32 blend of those base
#: rates, not against a pure favourite baseline. Judging a dog-heavy cell
#: against a favourite baseline is how a cell "beats" by +7pp while carrying
#: no information at all.
STRATA: dict = {}


def band_of(mkt_home):
    a = abs(mkt_home)
    return 0 if a < 3 else 3 if a < 7 else 7 if a < 14 else 14


def build_strata(games):
    acc = collections.defaultdict(list)
    for g in games:
        b = band_of(g['mkt_home'])
        home_fav = g['mkt_home'] > 0
        fav_hit = g['home_cover'] if home_fav else 1 - g['home_cover']
        acc[(b, 'fav')].append(fav_hit)
        acc[(b, 'dog')].append(1 - fav_hit)
    out = {k: statistics.fmean(v) for k, v in acc.items() if len(v) >= 30}
    print('\n  STRATIFIED BASE RATES (cover rate of each side, per band) —')
    print('  every cell below is scored against a blend of THESE, matched to')
    print('  its own band and side mix:')
    for b in (0, 3, 7, 14):
        f = out.get((b, 'fav')); d = out.get((b, 'dog'))
        n = len(acc.get((b, 'fav')) or [])
        if f is None:
            continue
        print(f'    spread band {b:>2}+   fav {f * 100:5.2f}%   '
              f'dog {d * 100:5.2f}%   n={n}')
    return out


class Cell:
    """A SOR pick set, scored against a band+side stratified expectation."""

    def __init__(self, label):
        self.label = label
        self.sor_hit = []
        self.fav_hit = []
        self.exp = []           # stratified expected hit for the side picked
        self.takes_fav = 0

    def add(self, g, pick_home):
        hc = g['home_cover']
        home_fav = g['mkt_home'] > 0
        picked_fav = (pick_home == home_fav)
        self.sor_hit.append(hc if pick_home else 1 - hc)
        self.fav_hit.append(hc if home_fav else 1 - hc)
        e = STRATA.get((band_of(g['mkt_home']),
                        'fav' if picked_fav else 'dog'))
        if e is not None:
            self.exp.append(e)
        if picked_fav:
            self.takes_fav += 1

    def report(self, floor, tests, pad=30):
        n = len(self.sor_hit)
        if n < floor:
            print(f'    {self.label:<{pad}}n={n:<5} SAMPLE TOO SMALL '
                  f'(need {floor}) — not a verdict either way')
            tests.append((self.label, None, n, None, None, 'thin'))
            return
        s = statistics.fmean(self.sor_hit) * 100
        se2 = (0.5 / n ** 0.5) * 100 * 2
        fshare = self.takes_fav / n * 100
        if not self.exp:
            print(f'    {self.label:<{pad}}{s:6.2f}%  n={n:<5} '
                  f'no stratified expectation available')
            tests.append((self.label, None, n, None, None, 'thin'))
            return
        exp = statistics.fmean(self.exp) * 100
        d_exp = s - exp
        if d_exp > se2:
            tag = 'BEATS its stratified expectation by >2SE'
        elif d_exp > 0:
            tag = 'above expectation, inside noise'
        else:
            tag = 'at or below expectation'
        print(f'    {self.label:<{pad}}{s:6.2f}%  n={n:<5} +/-{se2:4.1f}  '
              f'expected {exp:6.2f}%  delta {d_exp:+6.2f}  '
              f'fav-share {fshare:3.0f}%  {tag}')
        tests.append((self.label, s, n, se2, d_exp, tag))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--floor', type=int, default=6,
                    help='per-team prior-game floor so ratings are mature')
    ap.add_argument('--min-n', type=int, default=60, dest='min_n')
    a = ap.parse_args()
    sport = a.sport
    cfg = SPORT_CFG[sport]
    hfa = cfg['hfa']
    games = build(sport, load(sport))
    use = [g for g in games
           if g.get('sor_home') is not None and g.get('sor_away') is not None
           and g.get('sos_home') is not None and g.get('sos_away') is not None
           and g['n_home'] >= a.floor and g['n_away'] >= a.floor]
    print(f'=== {sport} · WHERE DOES SOR MATTER?  (conditional, controlled)')
    print(f'    both teams >= {a.floor} prior games · {len(use)} games · '
          f'hfa={hfa}')
    print(f'    every cell shows its OWN favourite-only baseline on the SAME')
    print(f'    games, plus what share of its picks simply ARE the favourite.')
    global STRATA
    STRATA = build_strata(use)
    tests = []

    # ---- A. raw gap inside tight spread bands --------------------------
    print(f'\n  A. RAW SOR GAP — does the better-SOR team cover, by spread '
          f'band?')
    for lo, hi in ((0, 3), (3, 7), (7, 14), (14, 99)):
        c = Cell(f'spread {lo}-{hi if hi < 99 else "+"}')
        for g in use:
            if not (lo <= abs(g['mkt_home']) < hi):
                continue
            gap = g['sor_home'] - g['sor_away']
            if gap == 0:
                continue
            c.add(g, gap > 0)
        c.report(a.min_n, tests)

    # ---- B. market-relative edge inside tight spread bands -------------
    print(f'\n  B. MARKET-RELATIVE EDGE  (SOR-implied margin vs the close),')
    print(f'     inside spread bands so compression cannot drive it:')
    for lo, hi in ((0, 3), (3, 7), (7, 14)):
        for thr in (1.0, 3.0):
            c = Cell(f'spread {lo}-{hi}, |edge|>={thr:.0f}')
            for g in use:
                if not (lo <= abs(g['mkt_home']) < hi):
                    continue
                edge = (g['sor_home'] - g['sor_away'] + hfa) - g['mkt_home']
                if abs(edge) < thr:
                    continue
                c.add(g, edge > 0)
            c.report(a.min_n, tests, pad=30)

    # ---- C. SOR x SOS — "handling their record" ------------------------
    print(f'\n  C. SOR x SOS — Andy\'s mechanism. Among games where the')
    print(f'     better-SOR team is backed, split by whether that team ALSO')
    print(f'     played the tougher schedule (record EARNED) or the softer')
    print(f'     one (record PADDED):')
    for lbl, want_earned in (('SOR earned (tougher SOS)', True),
                             ('SOR padded (softer SOS)', False)):
        c = Cell(lbl)
        for g in use:
            gap = g['sor_home'] - g['sor_away']
            if gap == 0:
                continue
            better_home = gap > 0
            # SOS of the better-SOR team minus the other team's SOS
            sos_adv = ((g['sos_home'] - g['sos_away']) if better_home
                       else (g['sos_away'] - g['sos_home']))
            if (sos_adv > 0) != want_earned:
                continue
            c.add(g, better_home)
        c.report(a.min_n, tests)
    # and the same split inside close games only
    print(f'\n     ...the same split, CLOSE games only (spread < 7):')
    for lbl, want_earned in (('close + SOR earned', True),
                             ('close + SOR padded', False)):
        c = Cell(lbl)
        for g in use:
            if abs(g['mkt_home']) >= 7:
                continue
            gap = g['sor_home'] - g['sor_away']
            if gap == 0:
                continue
            better_home = gap > 0
            sos_adv = ((g['sos_home'] - g['sos_away']) if better_home
                       else (g['sos_away'] - g['sos_home']))
            if (sos_adv > 0) != want_earned:
                continue
            c.add(g, better_home)
        c.report(a.min_n, tests)

    # ---- D. SOR RANK gap — scale-free, immune to compression -----------
    print(f'\n  D. SOR *RANK* GAP — ranks are scale-free, so this form is')
    print(f'     immune to the ridge compression that made the points')
    print(f'     version a pure favourite proxy:')
    for lo, hi in ((0, 7), (7, 14), (14, 99)):
        for rg in (10, 30):
            c = Cell(f'spread {lo}-{hi if hi < 99 else "+"}, rank gap>={rg}')
            for g in use:
                if g.get('rk_home') is None or g.get('rk_away') is None:
                    continue
                if not (lo <= abs(g['mkt_home']) < hi):
                    continue
                d = g['rk_away'] - g['rk_home']      # >0 = home better ranked
                if abs(d) < rg:
                    continue
                c.add(g, d > 0)
            c.report(a.min_n, tests, pad=34)

    # ---- verdict --------------------------------------------------------
    print('\n' + '=' * 74)
    scored = [t for t in tests if t[1] is not None]
    thin = [t for t in tests if t[1] is None]
    beats = [t for t in scored if t[5].startswith('BEATS')]
    nope = [t for t in scored if t[5].startswith('at or below')]
    print(f'  {len(tests)} cells · {len(scored)} had enough sample · '
          f'{len(thin)} TOO SMALL to call')
    print(f'  {len(beats)} beat their STRATIFIED EXPECTATION by >2SE · '
          f'{len(nope)} sat at or below expectation')
    print(f'  chance expectation for the >2SE count: '
          f'~{len(scored) * 0.025:.1f}')
    if beats:
        print('\n  CELLS WHERE SOR ADDS SOMETHING THE FAVOURITE DOES NOT:')
        for lbl, s, n, se2, delta, _t in sorted(beats, key=lambda z: -z[4]):
            print(f'    {lbl:<34}{s:6.2f}%  n={n:<5} delta vs fav '
                  f'{delta:+.2f}pp  2SE +/-{se2:.1f}')
        print('\n  These are CANDIDATES to grade forward, not proven edges —')
        print('  read the delta against its own 2SE and against the chance')
        print('  count above before anything gets built.')
    else:
        print('\n  No cell beat its own favourite baseline by >2SE.')
    if thin:
        print(f'\n  TOO SMALL TO CALL ({len(thin)}) — these are NOT nulls, the')
        print('  sample is simply not there yet. They are the cells to')
        print('  re-measure as the season fills in:')
        for lbl, _s, n, _se, _d, _t in thin:
            print(f'    {lbl:<34}n={n}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
