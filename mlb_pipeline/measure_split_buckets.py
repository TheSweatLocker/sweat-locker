#!/usr/bin/env python3
"""Grade EVERY line-movement bucket across EVERY sport. Read-only.

== 2026-10-19 - WHY ==
ANDY, 10-01: "The Split needs to be reworked or changed, its just not meeting
the mark, the record on the split doesn't make any sense its not providing any
useful data for users." Today: "ensuring ... the split being changed (look at
notes of process and what it is changing to)".

The diagnosis was never a layout problem, it was three server/client drifts:

  1. aggregate_daily_records.agg_split publishes ONE bucket
     (_PUBLISHED_BUCKETS = {'triple'}) while the client queried three, so two
     rendered as permanent zeros.
  2. agg_split is MLB-ONLY by construction - line 775 is
     `if not mlb_gids: return []`. MLB has now ended, and as predicted the
     Split is BLANK: surface_records holds zero split surfaces today.
  3. split_antipublic is computed daily and never read by the client.

So "what it is changing to" needs a decision on WHICH BUCKETS ARE REAL, and
that is an empirical question nobody has answered, because grading never
reached the 3,055 non-MLB flags. line_movement_flags now holds 4,767 rows over
67 days, and since 10-12 MLB is only 24 of 1,031 - the sport the grader
depends on is the one that stopped.

This grades every classification, in every sport, against the real result, so
the bucket list can be chosen on evidence instead of on the 2026-08-31
quarantine note.

METHOD
  * a flag names a sport, game_id, market and SIDE; it is graded on whether
    that side won that market, at the stored closing number
  * MLB/NHL spreads are FIXED handicaps, so rl/puckline there is graded as
    the handicap, not as a margin forecast
  * every bucket reports n, hit%, a Wilson CI and whether the CI clears the
    -110 breakeven of 52.38%; nothing under n=30 is treated as a finding,
    per the standing sample-size rule
  * PATTERN_ONLY and NEUTRAL are reported too - they are the control, and a
    bucket that cannot beat them is not a signal

USAGE
    python measure_split_buckets.py
    python measure_split_buckets.py --min-n 30
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
#: close_spread orientation per sport, measured in line_integrity /
#: analyze_spread_signals rather than assumed.
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
            print(f'  !! {t} HTTP {r.status_code} {r.text[:140]}')
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


def grade_flag(flag, games):
    """WIN / LOSS / PUSH for the flagged side, or None when ungradeable."""
    sport = str(flag.get('sport') or '').upper()
    gm = games.get((sport, str(flag.get('game_id'))))
    if not gm:
        return None
    hs, as_ = f(gm.get('home_score')), f(gm.get('away_score'))
    if hs is None or as_ is None:
        return None
    m = hs - as_
    side = str(flag.get('side') or '').strip().upper()
    mkt = str(flag.get('market') or '').lower()
    if mkt == 'ml':
        if side not in ('HOME', 'AWAY'):
            return None
        if m == 0:
            return 'PUSH'
        return 'WIN' if ((side == 'HOME') == (m > 0)) else 'LOSS'
    if mkt in ('total', 'ou'):
        ct = f(gm.get('close_total'))
        if ct is None or side not in ('OVER', 'UNDER'):
            return None
        tot = hs + as_
        if tot == ct:
            return 'PUSH'
        return 'WIN' if ((side == 'OVER') == (tot > ct)) else 'LOSS'
    if mkt in ('spread', 'rl', 'puckline'):
        if side not in ('HOME', 'AWAY'):
            return None
        cs = f(gm.get('close_spread'))
        if cs is None:
            cs = f(gm.get('close_puckline'))
        if cs is None:
            return None
        sgn = _HOME_HANDICAP_SIGN.get(sport)
        if sgn is None:
            # MLB / NHL / NBA store a handicap directly in this schema; the
            # orientation check below keeps an unverified sport out rather
            # than guessing at it
            return None
        home_handicap = sgn * cs
        eff = (m + home_handicap) if side == 'HOME' else (-m - home_handicap)
        if eff == 0:
            return 'PUSH'
        return 'WIN' if eff > 0 else 'LOSS'
    return None


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
    print(f'graded games indexed: {len(games)}')

    flags = page('line_movement_flags', {'select': '*'})
    print(f'line_movement_flags: {len(flags)}')

    buckets = defaultdict(lambda: [0, 0, 0])      # w, l, push
    by_sport = defaultdict(lambda: [0, 0, 0])
    ungradeable = defaultdict(int)
    for fl in flags:
        v = grade_flag(fl, games)
        if v is None:
            ungradeable[str(fl.get('sport'))] += 1
            continue
        cls = str(fl.get('classification') or 'NONE')
        i = {'WIN': 0, 'LOSS': 1, 'PUSH': 2}[v]
        buckets[cls][i] += 1
        by_sport[(str(fl.get('sport')), cls)][i] += 1

    gradeable = sum(sum(v) for v in buckets.values())
    print(f'gradeable: {gradeable}   ungradeable by sport: {dict(ungradeable)}')
    print('\n  (ungradeable is mostly a missing close line or a sport whose '
          'spread orientation is not verified - reported, not hidden)')

    print(f'\n{"="*76}\nEVERY BUCKET, ALL SPORTS POOLED\n{"="*76}')
    print(f'  {"classification":<32}{"W-L-P":>12}{"hit%":>8}{"95% CI":>16}'
          f'  verdict')
    rows = []
    for cls, (w, l, p) in buckets.items():
        n = w + l
        if n == 0:
            continue
        rows.append((n, cls, w, l, p))
    for n, cls, w, l, p in sorted(rows, reverse=True):
        lo, hi = wilson(w, n)
        if n < a.min_n:
            verdict = f'n<{a.min_n}, not a finding'
        elif lo > BREAKEVEN:
            verdict = '** BEATS breakeven **'
        elif hi < BREAKEVEN:
            verdict = 'LOSES to breakeven'
        else:
            verdict = 'inconclusive'
        print(f'  {cls:<32}{f"{w}-{l}-{p}":>12}{100*w/n:>7.1f}%'
              f'  [{lo:>5.1f},{hi:>5.1f}]  {verdict}')

    print(f'\n{"="*76}\nFAMILY ROLL-UP (what a tab could actually publish)\n{"="*76}')
    fams = {'SHARP_MOVE': 'SHARP_MOVE', 'CONSENSUS': 'CONSENSUS',
            'PUBLIC_MOVE': 'PUBLIC_MOVE', 'RLM': 'RLM',
            'PATTERN_ONLY': 'PATTERN_ONLY', 'NEUTRAL': 'NEUTRAL'}
    print(f'  {"family":<20}{"W-L":>10}{"hit%":>8}{"95% CI":>16}  verdict')
    for fam in fams:
        w = l = 0
        for cls, (ww, ll, _pp) in buckets.items():
            if cls.startswith(fam):
                w += ww
                l += ll
        n = w + l
        if n == 0:
            continue
        lo, hi = wilson(w, n)
        verdict = ('** BEATS **' if lo > BREAKEVEN else
                   ('LOSES' if hi < BREAKEVEN else 'inconclusive'))
        if n < a.min_n:
            verdict = f'n<{a.min_n}'
        print(f'  {fam:<20}{f"{w}-{l}":>10}{100*w/n:>7.1f}%'
              f'  [{lo:>5.1f},{hi:>5.1f}]  {verdict}')

    print(f'\n{"="*76}\nTIER LADDER inside each family (does conviction sort?)\n{"="*76}')
    for fam in ('SHARP_MOVE', 'CONSENSUS', 'PUBLIC_MOVE'):
        print(f'  {fam}:')
        for tier in ('LEAN', 'CONFIRMED', 'TRIPLE_CONFIRMED'):
            key = f'{fam}_{tier}'
            w, l, _p = buckets.get(key, [0, 0, 0])
            n = w + l
            if n == 0:
                continue
            lo, hi = wilson(w, n)
            print(f'    {tier:<18}{f"{w}-{l}":>9}{100*w/n:>7.1f}%'
                  f'  [{lo:>5.1f},{hi:>5.1f}]  n={n}')

    print(f'\n{"="*76}\nPER SPORT (is any sport carrying or dragging it?)\n{"="*76}')
    agg = defaultdict(lambda: [0, 0])
    for (sp, cls), (w, l, _p) in by_sport.items():
        if cls in ('PATTERN_ONLY', 'NEUTRAL', 'NONE'):
            continue
        agg[sp][0] += w
        agg[sp][1] += l
    print(f'  {"sport":<8}{"W-L":>10}{"hit%":>8}{"95% CI":>16}  n')
    for sp, (w, l) in sorted(agg.items(), key=lambda kv: -(kv[1][0]+kv[1][1])):
        n = w + l
        if n == 0:
            continue
        lo, hi = wilson(w, n)
        print(f'  {sp:<8}{f"{w}-{l}":>10}{100*w/n:>7.1f}%  [{lo:>5.1f},{hi:>5.1f}]  {n}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
