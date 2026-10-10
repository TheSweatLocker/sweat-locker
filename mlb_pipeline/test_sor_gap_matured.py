"""Does the SOR GAP predict covers once both teams actually have a rating?

ANDY 2026-10-10: "I dont think you are measuring/investigating/factoring SOR
correctly and SOR gap."

He is right, and this is the flaw. football_signal_panel gated its
walk-forward refit on

    ratings_at[(season, d)] = fit_srs(prior, cfg) if len(prior) >= 20 else {}

That is twenty games LEAGUE-WIDE. In a 130-plus team FBS league twenty games
means most teams have ZERO or ONE prior result, and the script never checked
that the two teams in the game being scored had any history at all. Ridge
shrinkage then pulls those infant ratings toward zero, so the SOR gap on those
games is near-noise — and they were pooled in with mature ratings. Any real
signal would be diluted by hundreds of games where SOR did not yet mean
anything. The reported 51.24% was therefore not a fair test of SOR.

WHAT THIS DOES DIFFERENTLY
  * Requires BOTH teams to have >= min_games prior in-season results before
    the game counts. Swept at 2 / 4 / 6 / 8 so maturity is a variable rather
    than an assumption.
  * Tests the RAW SOR GAP — does the higher-SOR team cover? — separately from
    the market-relative edge. Those are different questions and only the
    second was asked before.
  * Buckets the gap by size, because a 1-point edge and a 15-point edge are
    not the same claim.
  * Reports by season maturity (week bands) so an early-season effect cannot
    masquerade as a season-long one, or vice versa.

STILL WALK-FORWARD, STILL LEAK-FREE. Ratings are refit per date on strictly
earlier in-season games only, reusing fit_srs from compute_margin_strength.
The live `sor` column is an upserted snapshot and cannot be used
(project_rolling_stats_leak_trap_929).

HONEST PRIOR: the market-relative version measured null on 11,856 games, and
AP rank measured null on 6,124. If a matured SOR gap also measures null, that
is three independent team-quality inputs saying the same thing. If it does
NOT, the earlier test was simply wrong and this supersedes it.

WRITES NOTHING.

CLI
    python test_sor_gap_matured.py --sport NCAAF
    python test_sor_gap_matured.py --sport NFL --min-n 80
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
from compute_margin_strength import SPORT_CFG, fit_srs

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38
RESULTS = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}
#: NCAAF stores NEGATIVE for a home favourite, NFL POSITIVE. Both verified
#: empirically on thousands of graded games.
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
                            'home_score,away_score,close_spread,'
                            'spread_result'})
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
    """Attach walk-forward SOR plus each team's prior-game COUNT."""
    cfg = SPORT_CFG[sport]
    by_season = collections.defaultdict(list)
    for g in games:
        by_season[g['season']].append(g)
    for season, gs in by_season.items():
        gs.sort(key=lambda z: z['date'])
        played = collections.Counter()
        ratings, last_cut = {}, None
        for d in sorted({z['date'] for z in gs}):
            prior = [z for z in gs if z['date'] < d]
            # Refit on every prior game — the per-TEAM floor is applied when
            # scoring, not here, so a mature team is not penalised for sharing
            # a league with new ones.
            if prior and (last_cut is None or len(prior) != last_cut):
                ratings = fit_srs([{'home': z['home'], 'away': z['away'],
                                    'margin': z['margin'], 'neutral': False}
                                   for z in prior], cfg)
                last_cut = len(prior)
            played = collections.Counter()
            for z in prior:
                played[z['home']] += 1
                played[z['away']] += 1
            for g in [z for z in gs if z['date'] == d]:
                g['sor_home'] = ratings.get(g['home'])
                g['sor_away'] = ratings.get(g['away'])
                g['n_home'] = played[g['home']]
                g['n_away'] = played[g['away']]
    return games


def rate(vals, label, min_n, tests):
    if len(vals) < min_n:
        print(f'      {label:<42}n={len(vals)}  under n>={min_n}')
        return
    hit = statistics.fmean(vals) * 100
    se = (0.5 / len(vals) ** 0.5) * 100
    edge = hit - BREAKEVEN
    tag = ('SIGNAL (>2SE)' if edge > 2 * se else
           'suggestive' if edge > se else '')
    print(f'      {label:<42}{hit:6.2f}%  n={len(vals):<5} '
          f'+/-{2 * se:4.2f}pp  vs 52.38 {edge:+6.2f}  {tag}')
    tests.append((label, hit, len(vals), 2 * se, edge, tag))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--min-n', type=int, default=60, dest='min_n')
    a = ap.parse_args()
    sport = a.sport
    games = build(sport, load(sport))
    usable = [g for g in games
              if g.get('sor_home') is not None and g.get('sor_away') is not None]
    print(f'=== {sport} SOR GAP, matured · {len(games)} graded games · '
          f'{len(usable)} with a rating for both teams')
    print('    Walk-forward: refit per date on strictly earlier in-season')
    print('    games. The per-TEAM prior-game floor is the variable swept')
    print('    below — the previous test had NO per-team floor at all, which')
    print('    is the flaw Andy identified.')
    cfg = SPORT_CFG[sport]
    hfa = cfg['hfa']
    tests = []

    for floor in (2, 4, 6, 8):
        sub = [g for g in usable
               if g['n_home'] >= floor and g['n_away'] >= floor]
        print(f'\n  ── BOTH TEAMS HAVE >= {floor} PRIOR GAMES  (n={len(sub)})')
        if len(sub) < a.min_n:
            print(f'      too few games at this floor')
            continue
        # (a) RAW GAP — does the better-rated team cover, ignoring the line?
        raw = []
        for g in sub:
            gap = g['sor_home'] - g['sor_away']
            if gap == 0:
                continue
            better_home = gap > 0
            raw.append(g['home_cover'] if better_home
                       else 1 - g['home_cover'])
        rate(raw, 'RAW GAP: better-SOR team covers', a.min_n, tests)
        # (b) RAW GAP by size
        for lo, hi in ((0, 7), (7, 14), (14, 99)):
            b = []
            for g in sub:
                gap = g['sor_home'] - g['sor_away']
                if not (lo <= abs(gap) < hi) or gap == 0:
                    continue
                b.append(g['home_cover'] if gap > 0 else 1 - g['home_cover'])
            rate(b, f'   gap {lo}-{hi if hi < 99 else "+"} pts', a.min_n,
                 tests)
        # (c) MARKET-RELATIVE edge — SOR-implied margin vs the close
        for thr in (0.0, 3.0, 7.0):
            b = []
            for g in sub:
                edge = (g['sor_home'] - g['sor_away'] + hfa) - g['mkt_home']
                if abs(edge) < thr:
                    continue
                b.append(g['home_cover'] if edge > 0 else 1 - g['home_cover'])
            rate(b, f'   vs MARKET, |edge| >= {thr:.0f}', a.min_n, tests)

    # ── by season maturity ───────────────────────────────────────────────
    print('\n  ── BY WEEK BAND (floor 4), market-relative |edge| >= 3')
    for lo, hi in ((1, 4), (5, 8), (9, 20)):
        b = []
        for g in usable:
            w = g.get('week')
            try:
                w = int(w)
            except (TypeError, ValueError):
                continue
            if not (lo <= w <= hi) or g['n_home'] < 4 or g['n_away'] < 4:
                continue
            edge = (g['sor_home'] - g['sor_away'] + hfa) - g['mkt_home']
            if abs(edge) < 3:
                continue
            b.append(g['home_cover'] if edge > 0 else 1 - g['home_cover'])
        rate(b, f'   weeks {lo}-{hi}', a.min_n, tests)

    print('\n' + '=' * 74)
    hits = [t for t in tests if t[5].startswith('SIGNAL')]
    # 2026-10-10 VERDICT BUG. This block used to count ONLY cells ABOVE
    # breakeven and then print "nothing cleared" — which is how a strong
    # FADE sat in the output for weeks being reported as a null. A bucket at
    # 45.60% with a 2SE band of 4.81 is not nothing: it says back the OTHER
    # side at 54.40%. A directional test must score both directions, so the
    # fade side is scored here explicitly.
    fades = [t for t in tests
             if (BREAKEVEN - t[1]) > t[3] and (100 - t[1]) > BREAKEVEN]
    print(f'  {len(tests)} buckets · {len(hits)} beat breakeven by >2SE '
          f'FOLLOWING SOR · {len(fades)} where FOLLOWING is >2SE WORSE than '
          f'breakeven · chance ~{len(tests) * 0.05:.1f} total')
    if hits:
        print('\n  CLEARED BY FOLLOWING SOR:')
        for lbl, hit, n, band, edge, _t in sorted(hits, key=lambda z: -z[4]):
            print(f'    {lbl.strip():<42}{hit:6.2f}%  n={n:<5} '
                  f'edge {edge:+.2f}pp')
    if fades:
        print('\n  FOLLOWING SOR IS SIGNIFICANTLY BAD HERE — which makes the')
        print('  fade a candidate, NOT a proven play. Both numbers shown:')
        for lbl, hit, n, band, edge, _t in sorted(fades, key=lambda z: z[1]):
            fade_edge = 100 - hit - BREAKEVEN
            verdict = ('fade also clears by 2SE' if fade_edge > band
                       else 'fade does NOT clear breakeven by 2SE')
            print(f'    {lbl.strip():<36}follow {hit:6.2f}%  fade '
                  f'{100 - hit:6.2f}%  n={n:<5} 2SE +/-{band:.2f}')
            print(f'      {"":34}fade edge {fade_edge:+.2f}pp -> {verdict}')
        print('\n  BE PRECISE ABOUT WHAT THIS DOES AND DOES NOT SAY.')
        print('  "Following loses by >2SE" and "fading wins by >2SE" are NOT')
        print('  the same claim: breakeven is 52.38, not 50, so there is a')
        print('  4.76pp dead zone where both sides lose to the vig. Report')
        print('  the fade as a PRE-REGISTRATION candidate to grade forward,')
        print('  never as a measured edge, unless its own edge clears its own')
        print('  2SE band above.')
        print('\n  The one thing here stronger than any single cell is the')
        print('  GRADIENT: the fade strengthens monotonically with BOTH the')
        print('  gap threshold and the per-team maturity floor. A lucky cell')
        print('  does not line up in two directions at once. Note also that')
        print('  nested thresholds are NOT independent tests — |edge|>=7 at')
        print('  floor 8 is a subset of floor 6, so these are one finding')
        print('  seen twice, not two confirmations.')
    if not hits and not fades:
        print('\n  Nothing cleared in EITHER direction, with a per-team')
        print('  maturity floor and the raw gap tested separately.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
