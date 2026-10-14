#!/usr/bin/env python3
"""Grade every model as a PANELIST — its own picks, its own record.

== 2026-10-14 - WHY THIS EXISTS ==
Andy's design, in his words: the models are a board of panelists on GameDay.
Each has their own data, their own calculations, their own take, and each makes
a pick for the game. The engine then weighs those picks — by their records —
alongside money flow, situation, tendencies, rolling stats, matchup, injuries,
situational records and regression factors, against the market. "Consensus" is
simply what the panel collectively concludes.

WE DO NOT HAVE THAT. Measured 2026-10-14: `model_picks`, `model_records`,
`panelist_records`, `model_performance`, `model_scorecard`, `voter_records` and
`model_grades` are all 404. What exists is 97 registry signals of category
`model`, and those are EDGE CONDITIONS, not panelist records --
`nhl_elo_home_ml_strong` at 65.6% n=64 is the record of one narrow condition in
which Elo's number looked strong, not Elo's record as an analyst.

This script builds the missing piece: it takes each model's number, converts it
into the pick that number implies against the closing line, grades that pick,
and reports the panelist's record.

== THE BUG THIS FILE'S FIRST VERSION SHIPPED, AND THE GUARD THAT REPLACES IT ==
v1 declared panelists by GUESSING column names: `mc_expected_margin` and
`panel_pred_spread` for NFL, `panel_pred_total` for NHL. None of the three
exist. `.get()` on a missing key returns None, every row was skipped, and the
report said "mc never disagrees -- a panelist who always passes" for a model
whose numbers have sd 7.686 across 102 distinct values. A read bug was
published as a finding about the model.

So panelists are now declared as ACCESSORS over verified locations, and
PANELIST_MIN_ROWS enforces the distinction v1 could not make:

    0 populated rows   -> WIRING ERROR. Loud, non-zero exit, no record printed.
    populated, no edge -> a real finding: this panelist genuinely always passes.

"Silent" is only ever claimed about a panelist we proved we can read.

== WHAT A PICK MEANS HERE ==
A panelist's number is a prediction. The pick it implies is the side of the
closing line that number favours, and it counts as a pick only when the
disagreement clears EDGE_MIN -- otherwise the panelist is passing. Grading
every tiny disagreement measures rounding, not opinion.

SIGN CONVENTION: `close_spread` is the HOME handicap in every sport EXCEPT NFL,
where it is the AWAY line (project_close_spread_sign_bug_914, verified
n=7,325).

MLB AND NHL ARE GRADED ON TOTALS ONLY. Their close_spread / close_puckline is a
FIXED run/puck line (2-3 distinct values here), not a margin forecast --
grading a margin panelist against a near-constant measures nothing.

NOTHING IS WIRED. This reports records. Weighting panelists by them is the next
step and belongs to the engine, not here.

USAGE
    python build_panelist_records.py
    python build_panelist_records.py --sport NFL --market margin
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'NHL': 'nhl_game_results', 'NBA': 'nba_game_results',
           'MLB': 'mlb_game_results', 'NCAAB': 'ncaab_game_results'}
CONTEXT = {k: v.replace('_results', '_context') for k, v in RESULTS.items()}

#: Sports whose close_spread is a REAL point spread. MLB/NHL excluded.
REAL_SPREAD = ('NFL', 'NCAAF', 'NBA', 'NCAAB')

#: A panelist must be readable on at least this many games or it is a WIRING
#: ERROR rather than a quiet panelist. One is enough to prove the path works.
PANELIST_MIN_ROWS = 1


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _blob(row, col):
    """mc_probabilities and friends are jsonb -- sometimes delivered as text."""
    m = row.get(col)
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except Exception:                                      # noqa: BLE001
            return None
    return m if isinstance(m, dict) else None


def _mc(key):
    def get(r):
        b = _blob(r, 'mc_probabilities')
        return _f(b.get(key)) if b else None
    return get


def _col(name):
    return lambda r: _f(r.get(name))


def _diff(a, b):
    def get(r):
        x, y = _f(r.get(a)), _f(r.get(b))
        return None if x is None or y is None else x - y
    return get


def _sum(a, b):
    def get(r):
        x, y = _f(r.get(a)), _f(r.get(b))
        return None if x is None or y is None else x + y
    return get


#: panelist -> accessor returning its predicted HOME MARGIN.
#: Every entry below was verified against a live `select=*` on 2026-10-14.
MARGIN_PANELISTS = {
    'NFL': {
        'projected': _col('projected_spread'),
        'v3':        _col('v3_spread'),
        'v4':        _col('v4_spread'),
        'panel':     _diff('panel_pred_home_pts', 'panel_pred_away_pts'),
        'mc':        _mc('mc_expected_margin'),
        'model':     _diff('model_pred_home_points', 'model_pred_away_points'),
    },
    'NCAAF': {
        'projected': _col('projected_spread'),
        'sp_plus':   _col('sp_plus_pred_spread'),
        'epa':       _col('epa_pred_spread'),
        'mc':        _mc('mc_expected_margin'),
        'model':     _diff('model_pred_home_points', 'model_pred_away_points'),
    },
    'NBA': {
        'projected': _col('projected_spread'),
        'mc':        _mc('mc_expected_margin'),
    },
    'NCAAB': {'projected': _col('projected_spread')},
}
#: panelist -> accessor returning its predicted TOTAL.
TOTAL_PANELISTS = {
    'NFL': {
        'projected': _col('projected_total'),
        'v3':        _col('v3_total'),
        'v4':        _col('v4_total'),
        'panel':     _col('panel_pred_total'),
        'mc':        _mc('mc_expected_total'),
        'model':     _sum('model_pred_home_points', 'model_pred_away_points'),
    },
    'NCAAF': {
        'projected': _col('projected_total'),
        'sp_plus':   _col('sp_plus_pred_total'),
        'mc':        _mc('mc_expected_total'),
        'model':     _sum('model_pred_home_points', 'model_pred_away_points'),
    },
    'NHL': {
        'projected': _col('projected_total'),
        'mc':        _mc('mc_expected_total'),
        'goals':     _sum('projected_home_goals', 'projected_away_goals'),
    },
    'NBA': {
        'projected': _col('projected_total'),
        'mc':        _mc('mc_expected_total'),
    },
    'MLB': {
        'projected': _col('projected_total'),
        'model':     _col('model_pred_total'),
        'jerry':     _col('jerry_pred_total'),
        'panel':     _col('panel_implied_total'),
        # MLB's blob names this one mc_mean_total, not mc_expected_total.
        'mc':        _mc('mc_mean_total'),
    },
    'NCAAB': {'projected': _col('projected_total')},
}
#: How far a panelist must disagree with the line before it counts as a pick.
EDGE_MIN = {'NFL': 1.0, 'NCAAF': 1.5, 'NBA': 1.5, 'NCAAB': 1.5,
            'MLB': 0.3, 'NHL': 0.25}
PRICE = -110
BREAKEVEN = 100 * abs(PRICE) / (abs(PRICE) + 100)      # 52.38% at -110

WIRING_ERRORS: list[str] = []


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = None
        for _ in range(3):
            try:
                r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                                 timeout=180)
                break
            except Exception:                                  # noqa: BLE001
                r = None
        if r is None or r.status_code not in (200, 206):
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def joined(sport: str):
    """context rows paired with their graded result."""
    ctx = page(CONTEXT[sport], {'select': '*'})
    res = page(RESULTS[sport], {'select': '*'})
    by_id, by_key = {}, {}
    for r in res:
        if r.get('game_id'):
            by_id[str(r['game_id'])] = r
        by_key[(str(r.get('game_date'))[:10], str(r.get('home_team')),
                str(r.get('away_team')))] = r
    out = []
    for c in ctx:
        g = (by_id.get(str(c.get('game_id')))
             or by_key.get((str(c.get('game_date'))[:10],
                            str(c.get('home_team')), str(c.get('away_team')))))
        if not g:
            continue
        hs, as_ = _f(g.get('home_score')), _f(g.get('away_score'))
        if hs is None or as_ is None:
            continue
        out.append((c, g, hs, as_))
    return out


def wilson(w: int, n: int):
    """95% CI on a hit rate. A record without one invites over-reading."""
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def grade(sport: str, market: str) -> None:
    table = (MARGIN_PANELISTS if market == 'margin'
             else TOTAL_PANELISTS).get(sport)
    if not table:
        return
    if market == 'margin' and sport not in REAL_SPREAD:
        print(f'\n{sport} - MARGIN — SKIPPED: close_spread is a fixed '
              f'run/puck line, not a margin forecast')
        return
    rows = joined(sport)
    emin = EDGE_MIN[sport]
    print(f'\n{"="*88}\n{sport} - {market.upper()}   {len(rows)} graded games '
          f'- a pick needs |disagreement| >= {emin}')
    if not rows:
        print('  no graded games')
        return
    print(f'  {"panelist":<11}{"read":>6}{"picks":>7}{"W-L":>9}{"hit%":>7}'
          f'{"95% CI":>16}{"units":>8}{"ROI":>8}{"edge":>7}  note')
    shown = False
    for name, acc in sorted(table.items()):
        readable = sum(1 for c, _g, _h, _a in rows if acc(c) is not None)
        if readable < PANELIST_MIN_ROWS:
            WIRING_ERRORS.append(
                f'{sport} {market}: panelist "{name}" read 0 of {len(rows)} '
                f'rows — WIRING ERROR, not a quiet panelist')
            print(f'  {name:<11}{0:>6}{"—":>7}{"—":>9}{"—":>7}{"—":>16}'
                  f'{"—":>8}{"—":>8}{"—":>7}  ** WIRING ERROR: unreadable **')
            continue
        n = w = l = p = 0
        u = 0.0
        edges = []
        for c, g, hs, as_ in rows:
            pv = acc(c)
            if pv is None:
                continue
            if market == 'margin':
                cs = _f(g.get('close_spread'))
                if cs is None:
                    continue
                home_line = -cs if sport == 'NFL' else cs
                dev = pv - (-home_line)      # vs market's implied home margin
                if abs(dev) < emin:
                    continue
                cover = (hs - as_) + home_line
                if abs(cover) < 1e-9:
                    p += 1
                    continue
                hit = (cover > 0) == (dev > 0)
            else:
                ct = _f(g.get('close_total'))
                if ct is None:
                    continue
                dev = pv - ct
                if abs(dev) < emin:
                    continue
                tot = hs + as_
                if abs(tot - ct) < 1e-9:
                    p += 1
                    continue
                hit = (tot > ct) == (dev > 0)
            n += 1
            edges.append(abs(dev))
            if hit:
                w += 1
                u += 100 / abs(PRICE)
            else:
                l += 1
                u -= 1.0
        if n == 0:
            print(f'  {name:<11}{readable:>6}{0:>7}{"—":>9}{"—":>7}{"—":>16}'
                  f'{"—":>8}{"—":>8}{"—":>7}  readable but never disagrees by '
                  f'>= {emin} — always passes')
            continue
        dec = w + l
        hitpct = 100 * w / dec if dec else 0.0
        lo, hi = wilson(w, dec)
        ci = f'[{lo:.1f},{hi:.1f}]' if lo is not None else '—'
        if lo is not None and lo > BREAKEVEN:
            note = 'BEATS breakeven, CI clears'
        elif hi is not None and hi < BREAKEVEN:
            note = 'LOSES, CI clears'
        elif hitpct >= BREAKEVEN:
            note = 'above breakeven, CI does NOT clear'
        else:
            note = 'below breakeven, CI does not clear'
        if p:
            note += f' ({p} push)'
        print(f'  {name:<11}{readable:>6}{n:>7}{f"{w}-{l}":>9}{hitpct:>6.1f}%'
              f'{ci:>16}{u:>8.2f}{100*u/n:>7.1f}%'
              f'{statistics.mean(edges):>7.2f}  {note}')
        shown = True
    if shown:
        print(f'  (-110 breakeven {BREAKEVEN:.2f}%. "read" = games the '
              f'panelist is readable on; "edge" = typical disagreement.)')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(RESULTS))
    ap.add_argument('--market', choices=('margin', 'total'))
    a = ap.parse_args()
    sports = ([a.sport] if a.sport
              else ['NFL', 'NCAAF', 'NBA', 'NCAAB', 'MLB', 'NHL'])
    markets = [a.market] if a.market else ['margin', 'total']
    print('PANELIST SCORECARD — each model graded on the picks its own number '
          'implies.')
    print('A panelist that reads 0 rows is a WIRING ERROR, never a finding '
          'about the model.')
    for s in sports:
        for m in markets:
            try:
                grade(s, m)
            except Exception as e:                             # noqa: BLE001
                print(f'\n{s} - {m}: FAILED {type(e).__name__}: {str(e)[:110]}')
    if WIRING_ERRORS:
        print(f'\n{"="*88}\n{len(WIRING_ERRORS)} WIRING ERROR(S) — these are '
              f'unreadable inputs, not quiet panelists:')
        for m in WIRING_ERRORS:
            print(f'  * {m}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
