"""Is the SOR fade a real signal, or just "back the big favourite"?

ANDY 2026-10-10: "are you checking sor gap between opponents?"

Yes — the gap is home SOR minus away SOR for the two teams IN that game, and
the market-relative edge is (gap + HFA) - market home margin. Confirmed the
quantity is the one we ship: compute_margin_strength does
`rating = fit_srs(games, cfg)` and writes stat_key 'sor' from that same
`rating`, and this test reconstructs it walk-forward per date because the
stored value is a current snapshot and cannot be used historically.

WHY THIS CONTROL EXISTS, and it is the thing that nearly fooled me.
test_sor_gap_matured found that FOLLOWING a large market-relative SOR edge
loses by more than 2SE (45.60%, n=432, |edge|>=7, both teams >=8 prior
games), implying a 54.40% fade. But look at today's slate: the largest
negative edges are all blowout lines —

    Maryland @ Ohio State   SOR gap +8.0   market +33.0   edge -22.2
    Ball State @ Northwestern  +11.3            +35.5         -21.4
    Stanford @ Notre Dame      +15.6            +39.5         -21.1

SOR is a ridge-shrunk SRS fit, so it COMPRESSES large gaps exactly the way
SP+ does (project_sp_plus_compression_927). A big negative edge therefore
means "SOR underrates this favourite", and fading it means BACKING THE BIG
FAVOURITE. We already know NCAAF favourites beat dogs badly
(project_ncaaf_dog_bias_926: favs 57.9% vs dogs 24.3%). So the SOR fade could
be entirely subsumed by favourite size and carry no independent information.

A signal that is not tested against the obvious confound it resembles is not
a signal, it is a restatement. This measures whether the SOR fade survives
controls:

  1. WHAT SIDE does the fade actually take? If it is ~always the favourite,
     say so plainly.
  2. FAVOURITE-ONLY BASELINE on the same games — back the market favourite
     regardless of SOR. If the fade does not beat that, SOR adds nothing.
  3. WITHIN SPREAD BANDS — hold favourite size roughly fixed and ask whether
     SOR still separates. This is the only comparison that can show
     independent information.
  4. DOG-SIDE FADES ALONE — the subset where fading SOR means taking the
     DOG. If SOR carries real information this subset should also work; if
     it is pure favourite bias, this subset should be dead.

Walk-forward and leak-free throughout, reusing fit_srs the same way
test_sor_gap_matured does.

WRITES NOTHING.

CLI
    python test_sor_fade_vs_fav_control.py --sport NCAAF
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
    cfg = SPORT_CFG[sport]
    by_season = collections.defaultdict(list)
    for g in games:
        by_season[g['season']].append(g)
    for _season, gs in by_season.items():
        gs.sort(key=lambda z: z['date'])
        ratings, last_cut = {}, None
        for d in sorted({z['date'] for z in gs}):
            prior = [z for z in gs if z['date'] < d]
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


def rate(vals, label, floor=60, pad=44):
    if len(vals) < floor:
        print(f'    {label:<{pad}}n={len(vals)} under n>={floor}')
        return None
    hit = statistics.fmean(vals) * 100
    se2 = (0.5 / len(vals) ** 0.5) * 100 * 2
    print(f'    {label:<{pad}}{hit:6.2f}%  n={len(vals):<5} +/-{se2:4.2f}pp  '
          f'{hit - BREAKEVEN:+6.2f} vs BE')
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--floor', type=int, default=8,
                    help='per-team prior-game floor (the matured test used 8)')
    ap.add_argument('--thr', type=float, default=7.0,
                    help='|market-relative edge| threshold')
    a = ap.parse_args()
    sport = a.sport
    cfg = SPORT_CFG[sport]
    hfa = cfg['hfa']
    games = build(sport, load(sport))
    usable = [g for g in games
              if g.get('sor_home') is not None
              and g.get('sor_away') is not None
              and g['n_home'] >= a.floor and g['n_away'] >= a.floor]
    print(f'=== {sport} SOR FADE vs FAVOURITE-SIZE CONTROL')
    print(f'    both teams >= {a.floor} prior games · {len(usable)} games')
    print(f'    edge = (home SOR - away SOR + {hfa} HFA) - market home margin')
    print(f'    "the fade" = back the side the SOR edge does NOT favour')

    sub = []
    for g in usable:
        edge = (g['sor_home'] - g['sor_away'] + hfa) - g['mkt_home']
        if abs(edge) < a.thr:
            continue
        fade_home = edge < 0          # SOR likes away -> fade = take home
        home_is_fav = g['mkt_home'] > 0
        sub.append({
            'fade_hit': g['home_cover'] if fade_home else 1 - g['home_cover'],
            'fav_hit': (g['home_cover'] if home_is_fav
                        else 1 - g['home_cover']),
            'fade_takes_fav': fade_home == home_is_fav,
            'absmkt': abs(g['mkt_home']), 'edge': edge,
            'home_cover': g['home_cover'], 'home_is_fav': home_is_fav,
            'fade_home': fade_home})
    print(f'    {len(sub)} games clear |edge| >= {a.thr}')
    if len(sub) < 60:
        print('    too few to control.')
        return 0

    # ---- 1. what side is the fade actually taking? ----------------------
    tf = sum(1 for x in sub if x['fade_takes_fav'])
    print(f'\n  1. WHAT SIDE DOES THE FADE TAKE?')
    print(f'     it takes the MARKET FAVOURITE on {tf}/{len(sub)} = '
          f'{tf / len(sub) * 100:.1f}% of these games')
    if tf / len(sub) > 0.85:
        print('     -> that is essentially a favourite bet wearing a SOR')
        print('        label. The controls below decide whether SOR adds')
        print('        anything at all.')

    # ---- 2. favourite-only baseline on the SAME games -------------------
    print(f'\n  2. SAME GAMES, HEAD TO HEAD:')
    rate([x['fade_hit'] for x in sub], 'fade the SOR edge')
    rate([x['fav_hit'] for x in sub], 'just back the market favourite')
    agree = sum(1 for x in sub if x['fade_takes_fav'])
    print(f'     the two strategies pick the same side on {agree}/{len(sub)}')
    disc = [x for x in sub if not x['fade_takes_fav']]
    if len(disc) >= 30:
        print(f'\n     on the {len(disc)} games where they DISAGREE:')
        rate([x['fade_hit'] for x in disc], '  fade the SOR edge', floor=30)
        rate([x['fav_hit'] for x in disc], '  back the favourite', floor=30)
        print('     ^ this is the ONLY place SOR can show independent value.')

    # ---- 3. within spread bands ----------------------------------------
    print(f'\n  3. WITHIN SPREAD BANDS (favourite size held roughly fixed):')
    print(f'     {"band":<16}{"fade":>22}{"fav baseline":>24}')
    for lo, hi in ((0, 7), (7, 14), (14, 28), (28, 99)):
        b = [x for x in sub if lo <= x['absmkt'] < hi]
        if len(b) < 40:
            print(f'     {f"{lo}-{hi}":<16}n={len(b)} too few')
            continue
        fd = statistics.fmean(x['fade_hit'] for x in b) * 100
        fv = statistics.fmean(x['fav_hit'] for x in b) * 100
        se2 = (0.5 / len(b) ** 0.5) * 100 * 2
        print(f'     {f"{lo}-{hi}":<16}{f"{fd:.2f}% n={len(b)}":>22}'
              f'{f"{fv:.2f}%":>24}   delta {fd - fv:+6.2f}pp '
              f'(+/-{se2:.1f})')

    # ---- 4. dog-side fades alone ---------------------------------------
    dogf = [x for x in sub if not x['fade_takes_fav']]
    print(f'\n  4. THE SUBSET WHERE FADING SOR MEANS TAKING THE DOG:')
    r4 = rate([x['fade_hit'] for x in dogf],
              'fade SOR onto the underdog', floor=40)
    print('     If SOR carried real information this subset would also work.')
    print('     If the whole effect is favourite bias, this subset is dead.')

    print('\n' + '=' * 74)
    fd_all = statistics.fmean(x['fade_hit'] for x in sub) * 100
    fv_all = statistics.fmean(x['fav_hit'] for x in sub) * 100
    print(f'  VERDICT INPUTS: fade {fd_all:.2f}% vs favourite-only '
          f'{fv_all:.2f}% on the same {len(sub)} games '
          f'({fd_all - fv_all:+.2f}pp).')
    if fd_all - fv_all < 1.0:
        print('  The SOR fade does NOT beat simply backing the favourite on')
        print('  these games. On this evidence it is a RESTATEMENT of the')
        print('  known NCAAF favourite bias, not an independent SOR signal,')
        print('  and it should not be built as one.')
    else:
        print('  The fade beats the favourite-only baseline. Check whether')
        print('  that survives inside the spread bands above before building')
        print('  anything — a pooled win can be pure band mix.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
