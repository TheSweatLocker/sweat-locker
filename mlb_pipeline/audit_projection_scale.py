#!/usr/bin/env python3
"""Is a panelist's number on the MARKET'S SCALE? A promotion gate.

== 2026-10-14 - WHY THIS EXISTS ==
The record-weighted shadow engine ran on upcoming NFL games and disagreed with
77 of 96 published margin picks. That looks like a second opinion worth having.
It is not. Measured on the same games:

    panel took the DOG on 96 of 96 games -- 100%, in EVERY spread bucket
        |line| 0-3 : 18/18    |line| 3-7 : 58/58    |line| 7+ : 20/20

No panel with genuine opinions picks the dog 96 times out of 96. The cause is
scale, not judgement:

    close_spread      sd 4.663   range [-10.50, +13.50]
    projected_spread  sd 1.269   range [ -3.74,  +9.38]
    v4_spread         sd 0.118   (pre-retrain rows)
    mc                sd 8.737
    panel             sd 3.632

A projection with sd 1.269 against a market sd of 4.663 can essentially never
exceed a spread of 3 or more, so `projected - market` is negative on every
favourite by construction. `projected` is one of only three NFL voters and is
frequently the ONLY one present on an upcoming game (130 of 386 margin rows had
exactly one voter), so the panel inherits its bias wholesale.

This is project_margin_under_projection_926 and project_sp_plus_compression_927
arriving in NFL, and it is why the shadow engine must not be promoted yet: it
would ship a systematic underdog play dressed as a consensus.

== WHAT IS CHECKED ==
For each sport and market, each panelist's sd against the market's sd:

    ratio = sd(panelist) / sd(market)

    ratio < 0.5   COMPRESSED  -- cannot reach big lines; will always take the
                                 dog/under. Its disagreements are arithmetic.
    0.5 - 1.5     ON SCALE
    ratio > 2.0   INFLATED    -- overshoots; will always take the favourite

and the resulting DIRECTIONAL BIAS: of the games where this panelist clears
EDGE_MIN, what fraction land on the underdog (or the under)? A panelist at 100%
one way is a constant with extra steps.

A COMPRESSED panelist is not useless for display, and its record may even look
good if dogs happened to run -- but it must not be counted as an independent
voice in a consensus, because it is not expressing an opinion about THIS game.

USAGE
    python audit_projection_scale.py
    python audit_projection_scale.py --sport NFL
    python audit_projection_scale.py --upcoming
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys

import build_panelist_records as B

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

COMPRESSED_AT = 0.50
INFLATED_AT = 2.00
MIN_N = 20
#: A panelist landing this far one-sided is directionally degenerate.
BIAS_AT = 0.90


def run(sport, market, upcoming):
    tbl = (B.MARGIN_PANELISTS if market == 'margin'
           else B.TOTAL_PANELISTS).get(sport)
    if not tbl:
        return
    if market == 'margin' and sport not in B.REAL_SPREAD:
        return
    ctx = B.page(B.CONTEXT[sport], {'select': '*'})
    res = B.page(B.RESULTS[sport], {'select': '*'})
    by_id = {str(r.get('game_id')): r for r in res}
    by_key = {(str(r.get('game_date'))[:10], str(r.get('home_team')),
               str(r.get('away_team'))): r for r in res}
    rows = []
    for c in ctx:
        g = (by_id.get(str(c.get('game_id')))
             or by_key.get((str(c.get('game_date'))[:10],
                            str(c.get('home_team')), str(c.get('away_team')))))
        played = (g or {}).get('home_score') is not None
        if upcoming and played:
            continue
        if not upcoming and not played:
            continue
        src = g if g else c
        if market == 'margin':
            cs = B._f(src.get('close_spread'))
            if cs is None:
                cs = B._f(c.get('close_spread'))
            if cs is None:
                continue
            home_line = -cs if sport == 'NFL' else cs
            mkt = -home_line
        else:
            mkt = B._f(src.get('close_total')) or B._f(c.get('close_total'))
            if mkt is None:
                continue
        rows.append((c, mkt))
    if len(rows) < MIN_N:
        return
    mkts = [m for _c, m in rows]
    msd = statistics.pstdev(mkts)
    label = 'UPCOMING' if upcoming else 'GRADED'
    print(f'\n{"="*86}\n{sport} · {market.upper()} · {label}   n={len(rows)}  '
          f'market sd {msd:.3f}')
    print(f'  {"panelist":<12}{"n":>5}{"sd":>8}{"ratio":>7}'
          f'{"picks":>7}{"dog/under":>11}  verdict')
    emin = B.EDGE_MIN[sport]
    for name, acc in sorted(tbl.items()):
        vals, dogish, picks = [], 0, 0
        for c, mkt in rows:
            v = acc(c)
            if v is None:
                continue
            vals.append(v)
            d = v - mkt
            if abs(d) < emin:
                continue
            picks += 1
            # margin: the dog side is the one the market has behind. total:
            # 'under' is the low side. Both are simply d < 0.
            if d < 0:
                dogish += 1
        if len(vals) < MIN_N or msd <= 0:
            print(f'  {name:<12}{len(vals):>5}{"—":>8}{"—":>7}{"—":>7}'
                  f'{"—":>11}  too few to judge')
            continue
        sd = statistics.pstdev(vals)
        ratio = sd / msd
        bias = (dogish / picks) if picks else None
        v = ('COMPRESSED — cannot reach big lines' if ratio < COMPRESSED_AT
             else 'INFLATED — overshoots' if ratio > INFLATED_AT
             else 'on scale')
        if bias is not None and (bias >= BIAS_AT or bias <= 1 - BIAS_AT):
            v += f' · ONE-SIDED {100*bias:.0f}%'
        bs = f'{dogish}/{picks}' if picks else '—'
        print(f'  {name:<12}{len(vals):>5}{sd:>8.3f}{ratio:>7.2f}{picks:>7}'
              f'{bs:>11}  {v}')
    print(f'  (ratio = panelist sd / market sd. <{COMPRESSED_AT} cannot clear '
          f'a big line; >{INFLATED_AT} overshoots. "dog/under" = share of its '
          f'picks on the low side.)')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(B.RESULTS))
    ap.add_argument('--upcoming', action='store_true',
                    help='judge unplayed games instead of graded ones')
    a = ap.parse_args()
    print('PROJECTION SCALE AUDIT — a compressed model always takes the dog, '
          'and that is arithmetic, not an opinion.')
    for s in ([a.sport] if a.sport
              else ['NFL', 'NCAAF', 'NBA', 'NCAAB', 'MLB', 'NHL']):
        for m in ('margin', 'total'):
            try:
                run(s, m, a.upcoming)
            except Exception as e:                             # noqa: BLE001
                print(f'\n{s} {m}: FAILED {type(e).__name__}: {str(e)[:110]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
