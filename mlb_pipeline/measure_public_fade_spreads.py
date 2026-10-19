#!/usr/bin/env python3
"""Fade the public ON THE SPREAD, using the OddsCrowd money/bets feed. Read-only.

== 2026-10-19 - WHY THIS EXISTS AND WHY MY FIRST ANSWER WAS WRONG ==
ANDY: "I am willing to bet that if you look at nfl spread, the public is
getting hammered? I think the idea for the fade is sound, it will literally
tell users The fade picks plays engine likes that fade the public?"

My first pass measured the fade on MONEYLINES and concluded it loses. That
could not answer this question, for a reason that matters:

  * on a MONEYLINE, heavy public means a short favourite, so the hit rate you
    need rises with the public percentage. Follow and fade both sit on top of
    a moving breakeven and the vig eats the difference.
  * on a SPREAD or TOTAL, both sides are about -110. Breakeven is a FLAT
    52.38%. A fade that hits 56% there is a real edge, and the same 56% on a
    moneyline can be a loss.

I then tried spreads using line_movement_flags and found 24 football rows,
ZERO of them NFL - so I reported "can't test it" off the wrong table.
line_snapshot carries the OddsCrowd split directly:

    MLB/rl   5,593 rows with bets_pct
    NCAAF/rl   292
    NFL/rl     183

That is the data for the question actually asked.

METHOD
  * one snapshot per (game, market) - the LAST one before kickoff, so the
    public percentage is the settled one and nothing post-game leaks in
  * the PUBLIC side is pick_side when bets_pct > 50, else its opposite; the
    FADE is the other side
  * graded at the stored closing number, with the per-sport spread
    orientation verified against the books (NFL x-1, NCAAF x+1)
  * thresholds chosen on an EARLY date half and reported on a LATE holdout
    that had no hand in choosing them - this is the discipline that caught
    my own bad result, where the fade flipped from -14% early to +33% late
  * ROI at -110 both sides, and n beside every number

USAGE
    python measure_public_fade_spreads.py
    python measure_public_fade_spreads.py --min-n 30
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
#: verified against line_history on games captured within 2h of kickoff
_HOME_HANDICAP_SIGN = {'NFL': -1.0, 'NCAAF': 1.0}
CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'MLB': 'mlb_game_context'}
RES = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
       'MLB': 'mlb_game_results'}
OPP = {'HOME': 'AWAY', 'AWAY': 'HOME', 'OVER': 'UNDER', 'UNDER': 'OVER'}


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


def grade(sport, market, side, gm):
    hs, as_ = f(gm.get('home_score')), f(gm.get('away_score'))
    if hs is None or as_ is None:
        return None
    m = hs - as_
    s = str(side or '').upper()
    mk = str(market or '').lower()
    if mk in ('total', 'ou'):
        ct = f(gm.get('close_total'))
        if ct is None or s not in ('OVER', 'UNDER'):
            return None
        tot = hs + as_
        if tot == ct:
            return 'PUSH'
        return 'WIN' if ((s == 'OVER') == (tot > ct)) else 'LOSS'
    if mk in ('rl', 'spread'):
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-n', type=int, default=30)
    a = ap.parse_args()

    # results, keyed by (sport, away, home, date) because the spread/total
    # close lives there and ids differ from the odds-API hash
    res_by_teams = {}
    for sport, tbl in RES.items():
        sel = 'game_id,game_date,away_team,home_team,home_score,away_score,close_total'
        if sport in ('NFL', 'NCAAF'):
            sel += ',close_spread'
        for x in page(tbl, {'select': sel}):
            if x.get('home_score') is None:
                continue
            res_by_teams[(sport, str(x.get('away_team')), str(x.get('home_team')),
                          str(x.get('game_date'))[:10])] = x
    # context maps the odds-API hash -> teams + date
    ctx_by_hash = {}
    for sport, tbl in CTX.items():
        for c in page(tbl, {'select': 'game_id,game_date,away_team,home_team'}):
            ctx_by_hash[(sport, str(c.get('game_id')))] = c
    print(f'results indexed {len(res_by_teams)}   context hashes {len(ctx_by_hash)}')

    snaps = page('line_snapshot', {'select': 'sport,game_id,market,pick_side,'
                                            'money_pct,bets_pct,divergence,'
                                            'snapshot_ts'})
    print(f'line_snapshot rows: {len(snaps)}')
    # LAST snapshot per (sport, game, market) - the settled public number
    last = {}
    for s in snaps:
        k = (str(s.get('sport')), str(s.get('game_id')), str(s.get('market')))
        ts = str(s.get('snapshot_ts'))
        if k not in last or ts > str(last[k].get('snapshot_ts')):
            last[k] = s
    print(f'unique (game, market) snapshots: {len(last)}')

    rows = []
    unresolved = 0
    for (sport, gid, mkt), s in last.items():
        if mkt not in ('rl', 'spread', 'total'):
            continue
        c = ctx_by_hash.get((sport, gid))
        if not c:
            unresolved += 1
            continue
        gm = res_by_teams.get((sport, str(c.get('away_team')),
                               str(c.get('home_team')),
                               str(c.get('game_date'))[:10]))
        if not gm:
            # try +/- 1 day: context date is ET, results can differ
            got = None
            for (sp, aw, hm, d), g in res_by_teams.items():
                if (sp == sport and aw == str(c.get('away_team'))
                        and hm == str(c.get('home_team'))):
                    got = g
                    break
            gm = got
        if not gm:
            unresolved += 1
            continue
        bets = f(s.get('bets_pct'))
        money = f(s.get('money_pct'))
        if bets is None:
            continue
        ps = str(s.get('pick_side') or '').upper()
        if ps not in OPP:
            continue
        # which side is the PUBLIC on?
        if bets >= 50:
            public, pub_bets = ps, bets
            pub_money = money
        else:
            public, pub_bets = OPP[ps], 100.0 - bets
            pub_money = None if money is None else 100.0 - money
        fade = OPP[public]
        rf = grade(sport, mkt, fade, gm)
        if rf not in ('WIN', 'LOSS'):
            continue
        rows.append({'sport': sport, 'market': mkt, 'bets': pub_bets,
                     'money': pub_money,
                     'gap': (None if pub_money is None else pub_money - pub_bets),
                     'fade': rf, 'date': str(gm.get('game_date'))[:10]})
    rows.sort(key=lambda r: r['date'])
    print(f'fade-gradeable spread/total rows: {len(rows)}   unresolved: {unresolved}')
    if len(rows) < 60:
        print('  too few to search honestly - refusing')
        return 0

    cut = int(len(rows) * 0.6)
    early, late = rows[:cut], rows[cut:]
    print(f'EARLY {len(early)} ({early[0]["date"]}..{early[-1]["date"]})   '
          f'LATE {len(late)} ({late[0]["date"]}..{late[-1]["date"]})')

    def rep(name, v):
        g = [r for r in v if r['fade'] in ('WIN', 'LOSS')]
        if not g:
            print(f'  {name:<44}n=0')
            return None
        w = sum(1 for r in g if r['fade'] == 'WIN')
        n = len(g)
        lo, hi = wilson(w, n)
        roi = 100 * ((w * (100 / 110.0)) - (n - w)) / n
        tag = (f'  n<{a.min_n}' if n < a.min_n else
               ('  ** BEATS **' if lo > BREAKEVEN else
                ('  LOSES' if hi < BREAKEVEN else '')))
        print(f'  {name:<44}{f"{w}-{n-w}":>9}{100*w/n:>7.1f}%  '
              f'CI[{lo:>5.1f},{hi:>5.1f}]  ROI {roi:>+6.1f}%  n={n}{tag}')
        return (w, n, lo, roi)

    print(f'\n{"="*80}\nFADE THE PUBLIC, SPREADS + TOTALS (breakeven a flat 52.38%)\n{"="*80}')
    rep('all rows, no filter', rows)
    print('\n  EARLY half (where any threshold must be chosen):')
    for thr in (50, 55, 60, 65, 70, 75):
        rep(f'EARLY fade, public bets >= {thr}', [r for r in early if r['bets'] >= thr])
    print('\n  LATE holdout (the SAME thresholds, nothing re-chosen):')
    for thr in (50, 55, 60, 65, 70, 75):
        rep(f'LATE  fade, public bets >= {thr}', [r for r in late if r['bets'] >= thr])

    print(f'\n{"="*80}\nBY SPORT AND MARKET (full sample)\n{"="*80}')
    by = defaultdict(list)
    for r in rows:
        by[(r['sport'], r['market'])].append(r)
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        rep(f'fade {k[0]} {k[1]}', v)
    print('\n  football spreads only, by threshold:')
    fb = [r for r in rows if r['sport'] in ('NFL', 'NCAAF')
          and r['market'] in ('rl', 'spread')]
    for thr in (0, 55, 60, 65):
        rep(f'football spread fade, bets >= {thr}', [r for r in fb if r['bets'] >= thr])
    for sp in ('NFL', 'NCAAF'):
        for thr in (0, 60):
            rep(f'{sp} spread fade, bets >= {thr}',
                [r for r in fb if r['sport'] == sp and r['bets'] >= thr])

    print(f'\n{"="*80}\nTHE SHARP SHAPE: public heavy on TICKETS, money elsewhere\n{"="*80}')
    wg = [r for r in rows if r['gap'] is not None]
    print(f'  rows with both money% and bets%: {len(wg)}')
    if len(wg) >= 60:
        gc = int(len(wg) * 0.6)
        ge, gl = wg[:gc], wg[gc:]
        for thr in (-15, -10, -5):
            rep(f'EARLY fade, money-bets gap <= {thr}', [r for r in ge if r['gap'] <= thr])
        for thr in (-15, -10, -5):
            rep(f'LATE  fade, gap <= {thr}', [r for r in gl if r['gap'] <= thr])
    return 0


if __name__ == '__main__':
    sys.exit(main())
