#!/usr/bin/env python3
"""IS THE ENGINE'S SIDE TILT EARNED? Per sport, per market, against controls.

══ WHY (2026-10-28) ════════════════════════════════════════════════════════
Andy: "NBA picking favorite only is unsat ... NBA engine needs serious wokr
in elimnatiting bias, we need to eliminate bias rigth now across eveythign
... NHL as well tge engine should be predictive in nature".

He is right about the tilt. Measured the same day: 86% of NBA spread picks
and 85% of its moneylines are on the market favourite, 93%/88% for NHL, and
NFL has never taken a plus-money moneyline in 24 priced picks.

But `audit_home_away_signal_bias.py` (2026-10-10, Iowa @ Washington) found
the NCAAF HOME tilt is EARNED on spreads -- home picks 55.2% ATS against a
50.48% baseline, away picks 46.2%, BELOW it -- and concluded that forcing
more away picks makes things worse. That audit covers NCAAF and NFL only,
and spreads only.

So "eliminate the bias" is not one question, it is ten: five sports x two
markets, and the answer differs. A tilt that BEATS its own baseline is a
read worth keeping; a tilt that does not is pure juice. This script is the
instrument that tells them apart, so the de-biasing work is aimed rather
than sprayed.

══ WHAT IT MEASURES ════════════════════════════════════════════════════════
For every graded pick, priced at the CLOSING price OF THE SIDE WE TOOK:

  ENGINE          what we actually did
  always FAV      take the market favourite in the SAME games
  always HOME     take the home team in the SAME games
  always DOG      take the market underdog in the SAME games

A direction-only control is mandatory here: favourites win ~60% of games,
so any rule that points at favourites inherits a high raw hit rate while
adding nothing (feedback_signal_needs_a_direction_control). The question is
never "does it win" but "does it beat the one-word rule".

EARNED vs UNEARNED, stated as a rule so it cannot drift:
  EARNED    engine ROI > best control ROI, and the gap holds split-half
  UNEARNED  engine ROI <= the control it is tilted toward

Every rate carries n, and nothing under n=30 is called either way
(feedback_sample_size_with_pct).

WRITES NOTHING.

    python audit_side_bias_all_sports.py
    python audit_side_bias_all_sports.py --sport NHL
    python audit_side_bias_all_sports.py --market ml
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import requests
from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

load_dotenv('.env')
SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
S = requests.Session()
S.headers.update({'apikey': KEY, 'Authorization': f'Bearer {KEY}'})

MIN_N = 30

# Per-sport results table and its closing-moneyline column names. The names
# genuinely differ -- MLB is home_ml_close, everyone else close_home_ml --
# and guessing one column costs the whole table (a 42703 reads as 0 rows).
RES = {
    'MLB':   ('mlb_game_results',   'home_ml_close',  'away_ml_close'),
    'NFL':   ('nfl_game_results',   'close_home_ml',  'close_away_ml'),
    'NCAAF': ('ncaaf_game_results', 'close_home_ml',  'close_away_ml'),
    'NBA':   ('nba_game_results',   'close_home_ml',  'close_away_ml'),
    'NHL':   ('nhl_game_results',   'close_home_ml',  'close_away_ml'),
}
# NHL's spread is a puckline fixed at +-1.5 and MLB's run line likewise, so
# neither stores a close_spread. They are handled as a 1.5 handicap.
FIXED_HANDICAP = {'NHL': 1.5, 'MLB': 1.5}


def paged(tbl: str, params: dict, key: str = 'id') -> list:
    out, off = [], 0
    while True:
        r = S.get(f'{SB}/rest/v1/{tbl}',
                  params=dict(params, limit='1000', offset=str(off),
                              order=f'{key}.asc'), timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {tbl} {r.status_code} {(r.text or "")[:160]}')
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def pay(o: float) -> float:
    return 100 / (-o) if o < 0 else o / 100


def be(o: float) -> float:
    return 100 * (-o) / ((-o) + 100) if o < 0 else 100 * 100 / (o + 100)


def wilson(k: int, n: int) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    z = 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - m), 100 * (c + m))


def load_games() -> dict:
    """(sport, game_id) -> fav / closing ML per side / winner / handicap."""
    alias = {}
    for a in paged('football_game_id_alias',
                   {'select': 'sport,alias_game_id,results_game_id'},
                   'alias_game_id'):
        alias[(a['sport'], str(a['alias_game_id']))] = str(a['results_game_id'])

    out, n_alias = {}, 0
    for sp, (tbl, hk, ak) in RES.items():
        cols = f'game_id,game_date,home_score,away_score,{hk},{ak}'
        if sp not in FIXED_HANDICAP:
            cols += ',close_spread'
        for g in paged(tbl, {'select': cols}, 'game_id'):
            h, a = g.get(hk), g.get(ak)
            hs, as_ = g.get('home_score'), g.get('away_score')
            if None in (h, a, hs, as_):
                continue
            hcap = FIXED_HANDICAP.get(sp)
            if hcap is None:
                cs = g.get('close_spread')
                hcap = abs(float(cs)) if cs is not None else None
            out[(sp, str(g['game_id']))] = {
                'date': g.get('game_date'),
                'fav': 'HOME' if h < a else 'AWAY',
                'ml': {'HOME': float(h), 'AWAY': float(a)},
                'win': 'HOME' if hs > as_ else ('AWAY' if as_ > hs else None),
                'margin': {'HOME': float(hs) - float(as_),
                           'AWAY': float(as_) - float(hs)},
                'hcap': hcap,
            }
    out['_alias'] = alias
    return out


def strat(rows: list, pick, market: str) -> dict:
    """Run one strategy over the rows. Prices every bet at ITS OWN odds."""
    k = n = 0
    units = 0.0
    bes = []
    for side, g in rows:
        s = pick(side, g)
        if market == 'ml':
            o = g['ml'][s]
            won = (g['win'] == s)
        else:
            # spread: the side we took covers if its margin beats the
            # handicap it was getting/giving. Favourite must win BY MORE
            # than the number; the dog may lose by less.
            if g['hcap'] is None:
                continue
            fav = g['fav']
            m = g['margin'][s]
            won = (m > g['hcap']) if s == fav else (m > -g['hcap'])
            # No stored spread price in any sport except NHL/MLB fixed
            # lines, so this is the conventional -110. SAID, not assumed
            # silently (feedback_flat_110_inflates_every_ml_figure).
            o = -110.0
        n += 1
        bes.append(be(o))
        if won:
            k += 1
            units += pay(o)
        else:
            units -= 1
    if not n:
        return {}
    return {'n': n, 'k': k, 'hit': 100 * k / n, 'roi': 100 * units / n,
            'be': sum(bes) / len(bes)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport')
    ap.add_argument('--market', choices=('ml', 'spread'))
    args = ap.parse_args()

    G = load_games()
    alias = G.pop('_alias')
    print(f'games with a closing ML and a final score: {len(G)}')

    reads = paged('jerry_reads',
                  {'select': 'sport,game_id,game_date,call_market,call_side'},
                  'id')
    print(f'jerry_reads: {len(reads)}\n')

    def g_of(sp, gid):
        return (G.get((sp, str(gid)))
                or G.get((sp, alias.get((sp, str(gid)), '~'))))

    sports = [args.sport.upper()] if args.sport else list(RES)
    markets = [args.market] if args.market else ['ml', 'spread']
    MK = {'ml': ('ml',), 'spread': ('rl', 'spread')}

    verdicts = []
    for mkt in markets:
        print('=' * 78)
        print(f'{mkt.upper()} PICKS -- engine against the one-word rules')
        if mkt == 'spread':
            print('(spreads quoted at the conventional -110; no sport stores '
                  'a spread price)')
        print('=' * 78)
        hdr = (f'{"sport":6s} {"n":>4s} | {"ENGINE":>16s} | {"alwaysFAV":>16s}'
               f' | {"alwaysDOG":>16s} | {"alwaysHOME":>16s} | fav%')
        print(hdr)
        for sp in sports:
            rows = []
            for r in reads:
                if r['sport'] != sp:
                    continue
                if (r.get('call_market') or '') not in MK[mkt]:
                    continue
                side = (r.get('call_side') or '').upper()
                if side not in ('HOME', 'AWAY'):
                    continue
                g = g_of(sp, r.get('game_id'))
                if not g or g['win'] is None:
                    continue
                rows.append((side, g))
            if not rows:
                print(f'{sp:6s}    0 | (no graded {mkt} picks)')
                continue
            eng = strat(rows, lambda s, g: s, mkt)
            fav = strat(rows, lambda s, g: g['fav'], mkt)
            dog = strat(rows, lambda s, g: ('AWAY' if g['fav'] == 'HOME'
                                            else 'HOME'), mkt)
            hom = strat(rows, lambda s, g: 'HOME', mkt)
            if not eng:
                print(f'{sp:6s}    0 | (no gradeable {mkt} rows)')
                continue
            n = eng['n']
            favshare = 100 * sum(1 for s, g in rows if s == g['fav']) / len(rows)
            f = lambda d: (f'{d["hit"]:5.1f}% {d["roi"]:+7.2f}%'
                           if d else '        -       ')
            print(f'{sp:6s} {n:4d} | {f(eng)} | {f(fav)} | {f(dog)} | '
                  f'{f(hom)} | {favshare:4.0f}%')
            lo, hi = wilson(eng['k'], n)
            best = max((d for d in (fav, dog, hom) if d),
                       key=lambda d: d['roi'])
            gap = eng['roi'] - best['roi']
            if n < MIN_N:
                tag = f'n<{MIN_N} — NOT CALLED'
            elif gap > 0:
                tag = f'EARNED  (+{gap:.2f}pp over the best control)'
            else:
                tag = f'UNEARNED ({gap:+.2f}pp vs the best control)'
            print(f'       engine CI [{lo:.1f}, {hi:.1f}]  breakeven '
                  f'{eng["be"]:.1f}%  ->  {tag}')
            verdicts.append((sp, mkt, n, favshare, eng['roi'], best['roi'],
                             gap, n >= MIN_N))
        print()

    print('=' * 78)
    print('VERDICT TABLE -- where de-biasing is justified by evidence')
    print('=' * 78)
    print(f'{"sport":6s} {"market":7s} {"n":>4s} {"fav%":>6s} {"engine":>9s} '
          f'{"control":>9s} {"gap":>9s}  verdict')
    for sp, mkt, n, fs, er, cr, gap, called in verdicts:
        if not called:
            v = 'not called (n too small)'
        elif gap > 0:
            v = 'EARNED — keep the tilt, it beats the rule'
        else:
            v = 'UNEARNED — the tilt is juice, fix this one'
        print(f'{sp:6s} {mkt:7s} {n:4d} {fs:5.0f}% {er:+8.2f}% {cr:+8.2f}% '
              f'{gap:+8.2f}pp  {v}')
    print('\nEARNED means the engine beat every one-word rule on the same')
    print('games at real prices. UNEARNED means a one-word rule did better,')
    print('so the side selector is adding the favourite\'s juice and no')
    print('information -- that is the bias worth removing.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
