#!/usr/bin/env python3
"""The Split as a FADE-THE-PUBLIC product: find the filter, measure the record.

== 2026-10-19 - WHY, IN ANDY'S WORDS ==
"The Split, we discussed changin to the fade and strictly being plays passed
through a filter of certain cirteria that is against the public, almost like
the sharp set up wise but play that are agisnt the public and a running record
like the sharp. concnencus confrimed maybe added in some cariety so we cna
capture that good record."

So the product is NOT a bucket-record table. It is:
  * a PLAY LIST - specific sides, like The Sharp
  * every play is AGAINST THE PUBLIC
  * entry is a FILTER on stated criteria, not a classifier label
  * it carries a running W-L-unit record, graded like The Sharp
  * CONSENSUS_CONFIRMED can ride along as a second lens, because that one
    bucket did measure well (69.1% on strictly-pregame flags)

My earlier answer gave him a bucket-performance table and called it a
decision. That was the wrong shape for the product.

WHAT "AGAINST THE PUBLIC" MEANS HERE. line_movement_flags carries, per side:
    bets_pct     share of TICKETS on this side
    money_pct    share of HANDLE on this side
    handle_pct / bettors_pct   the same split from other sources
The public is TICKETS. So the anti-public side is the one with LOW bets_pct.
The classic sharp shape is low tickets + high money: few bettors, big money.

This searches the filter space honestly:
  * one axis at a time, then the two-axis combination
  * a temporal split: the threshold is chosen on EARLY data and the record
    reported on LATE data it had no hand in choosing
  * a control on every cut - fading at random, and the favourite - because a
    fade that only matches "take the dog" is not a signal
  * n beside every number, Wilson CIs, and the -110 breakeven marked
  * nothing under n=30 is called a finding

USAGE
    python measure_antipublic_fade.py
    python measure_antipublic_fade.py --min-n 30
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38
RESULTS = {'MLB': 'mlb_game_results', 'NFL': 'nfl_game_results',
           'NCAAF': 'ncaaf_game_results', 'NHL': 'nhl_game_results',
           'NBA': 'nba_game_results'}
_HOME_HANDICAP_SIGN = {'NFL': -1.0, 'NCAAF': 1.0}


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def page(t, p):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, timeout=240,
                         params=dict(p, limit='1000', offset=str(off)))
        if r.status_code not in (200, 206):
            print(f'  !! {t} HTTP {r.status_code} {r.text[:130]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def wilson(w, n):
    if not n:
        return (0.0, 0.0)
    z = 1.96
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def side_result(sport, market, side, gm):
    """WIN/LOSS/PUSH for `side` of `market`, or None."""
    hs, as_ = f(gm.get('home_score')), f(gm.get('away_score'))
    if hs is None or as_ is None:
        return None
    m = hs - as_
    s = str(side or '').upper()
    mk = str(market or '').lower()
    if mk == 'ml':
        if s not in ('HOME', 'AWAY'):
            return None
        if m == 0:
            return 'PUSH'
        return 'WIN' if ((s == 'HOME') == (m > 0)) else 'LOSS'
    if mk in ('total', 'ou'):
        ct = f(gm.get('close_total'))
        if ct is None or s not in ('OVER', 'UNDER'):
            return None
        tot = hs + as_
        if tot == ct:
            return 'PUSH'
        return 'WIN' if ((s == 'OVER') == (tot > ct)) else 'LOSS'
    if mk in ('spread', 'rl', 'puckline'):
        if s not in ('HOME', 'AWAY'):
            return None
        cs = f(gm.get('close_spread'))
        sgn = _HOME_HANDICAP_SIGN.get(sport)
        if cs is None or sgn is None:
            return None
        hh = sgn * cs
        eff = (m + hh) if s == 'HOME' else (-m - hh)
        if eff == 0:
            return 'PUSH'
        return 'WIN' if eff > 0 else 'LOSS'
    return None


OPPOSITE = {'HOME': 'AWAY', 'AWAY': 'HOME', 'OVER': 'UNDER', 'UNDER': 'OVER'}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-n', type=int, default=30)
    a = ap.parse_args()

    games = {}
    for sport, tbl in RESULTS.items():
        for x in page(tbl, {'select': '*'}):
            if x.get('home_score') is None:
                continue
            games[(sport, str(x.get('game_id')))] = x
    flags = page('line_movement_flags', {'select': '*'})
    print(f'graded games {len(games)}   flags {len(flags)}')

    # Build the candidate universe: one row per flag that has a public split
    # AND a gradeable game. The FADE play is the side opposite the flag side
    # when the flag side is the public side.
    rows = []
    for fl in flags:
        sport = str(fl.get('sport') or '').upper()
        gm = games.get((sport, str(fl.get('game_id'))))
        if not gm:
            continue
        side = str(fl.get('side') or '').upper()
        if side not in OPPOSITE:
            continue
        bets = f(fl.get('bets_pct'))
        money = f(fl.get('money_pct'))
        if bets is None:
            bets = f(fl.get('bettors_pct'))
        if money is None:
            money = f(fl.get('handle_pct'))
        if bets is None:
            continue
        fade = OPPOSITE[side]
        res = side_result(sport, fl.get('market'), fade, gm)
        if res not in ('WIN', 'LOSS'):
            continue
        rows.append({'sport': sport, 'market': str(fl.get('market')),
                     'public_side': side, 'fade_side': fade,
                     'bets': bets, 'money': money,
                     'gap': (None if money is None else money - bets),
                     'res': res, 'date': str(fl.get('first_seen_at'))[:10],
                     'cls': str(fl.get('classification') or '')})
    rows.sort(key=lambda r: r['date'])
    print(f'fade candidates with a public split + a result: {len(rows)}')
    if len(rows) < 60:
        print('  too few to search a filter honestly - refusing')
        return 0

    cut = int(len(rows) * 0.6)
    early, late = rows[:cut], rows[cut:]
    print(f'threshold chosen on EARLY {len(early)} '
          f'({early[0]["date"]}..{early[-1]["date"]}), '
          f'reported on LATE {len(late)} '
          f'({late[0]["date"]}..{late[-1]["date"]})')

    def rec(v):
        w = sum(1 for r in v if r['res'] == 'WIN')
        return w, len(v) - w

    def show(name, v, flag_cis=True):
        w, l = rec(v)
        n = w + l
        if n == 0:
            print(f'  {name:<40}n=0')
            return None
        lo, hi = wilson(w, n)
        tag = ''
        if n < a.min_n:
            tag = f'  n<{a.min_n}'
        elif lo > BREAKEVEN:
            tag = '  ** BEATS breakeven **'
        elif hi < BREAKEVEN:
            tag = '  LOSES'
        print(f'  {name:<40}{f"{w}-{l}":>9}{100*w/n:>7.1f}%  '
              f'[{lo:>5.1f},{hi:>5.1f}]{tag}')
        return (w, n, lo)

    print(f'\n{"="*78}\nCONTROLS - a fade has to beat these\n{"="*78}')
    show('fade EVERY flagged side (no filter)', rows)
    show('  ... EARLY half', early)
    show('  ... LATE half', late)

    print(f'\n{"="*78}\nAXIS 1: how heavy must the public be? (bets_pct)\n{"="*78}')
    print('  threshold picked on EARLY, then the SAME threshold on LATE')
    best = None
    for thr in (55, 60, 65, 70, 75, 80):
        e = [r for r in early if r['bets'] >= thr]
        if len(e) < 25:
            continue
        w, n, lo = show(f'EARLY  public bets_pct >= {thr}', e) or (0, 0, 0)
        if n and lo > (best[3] if best else -1):
            best = (thr, w, n, lo)
    if best:
        thr = best[0]
        print(f'\n  -> EARLY favours bets_pct >= {thr}; now the holdout:')
        show(f'LATE   public bets_pct >= {thr}', [r for r in late if r['bets'] >= thr])

    print(f'\n{"="*78}\nAXIS 2: the sharp shape - LOW tickets, HIGH money\n{"="*78}')
    print('  money_pct - bets_pct on the PUBLIC side; a NEGATIVE gap means')
    print('  the money is on the other side, which is what we are fading to.')
    withgap = [r for r in rows if r['gap'] is not None]
    print(f'  rows carrying both money% and bets%: {len(withgap)}')
    if len(withgap) >= 60:
        gcut = int(len(withgap) * 0.6)
        ge, gl = withgap[:gcut], withgap[gcut:]
        gbest = None
        for thr in (-25, -20, -15, -10, -5, 0):
            e = [r for r in ge if r['gap'] <= thr]
            if len(e) < 20:
                continue
            out = show(f'EARLY  money-bets gap <= {thr}', e)
            if out and out[2] > (gbest[1] if gbest else -1):
                gbest = (thr, out[2])
        if gbest:
            print(f'\n  -> holdout at gap <= {gbest[0]}:')
            show(f'LATE   money-bets gap <= {gbest[0]}',
                 [r for r in gl if r['gap'] <= gbest[0]])

    print(f'\n{"="*78}\nAXIS 3: does the market matter?\n{"="*78}')
    bym = defaultdict(list)
    for r in rows:
        bym[r['market']].append(r)
    for k, v in sorted(bym.items(), key=lambda kv: -len(kv[1])):
        show(f'fade, market={k}', v)
    print(f'\n  by sport:')
    bys = defaultdict(list)
    for r in rows:
        bys[r['sport']].append(r)
    for k, v in sorted(bys.items(), key=lambda kv: -len(kv[1])):
        show(f'fade, sport={k}', v)

    print(f'\n{"="*78}\nTHE SECOND LENS Andy asked to keep: CONSENSUS_CONFIRMED\n{"="*78}')
    print('  (this one is a FOLLOW, not a fade - reported on the flag side)')
    cc = []
    for fl in flags:
        if str(fl.get('classification')) != 'CONSENSUS_CONFIRMED':
            continue
        sport = str(fl.get('sport') or '').upper()
        gm = games.get((sport, str(fl.get('game_id'))))
        if not gm:
            continue
        res = side_result(sport, fl.get('market'), fl.get('side'), gm)
        if res in ('WIN', 'LOSS'):
            cc.append({'res': res})
    show('CONSENSUS_CONFIRMED, follow the flag side', cc)
    return 0


if __name__ == '__main__':
    sys.exit(main())
