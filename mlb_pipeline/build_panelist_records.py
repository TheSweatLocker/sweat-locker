#!/usr/bin/env python3
"""Grade every model as a PANELIST — its own picks, its own record.

══ 2026-10-14 · WHY THIS EXISTS ══
Andy's design, in his words: the models are a board of five panelists on
GameDay. Each has their own data, their own calculations, their own take, and
each makes a pick for the game. The engine then weighs those picks — by their
records — alongside money flow, situation, tendencies, rolling stats, matchup,
injuries, situational records and regression factors, against the market.
"Consensus" is simply what the panel collectively concludes.

WE DO NOT HAVE THAT. Measured 2026-10-14:

  * `model_picks`, `model_records`, `panelist_records`, `model_performance`,
    `model_scorecard`, `voter_records`, `model_grades` — all seven 404.
  * What exists is 97 registry signals of category `model`, and those are EDGE
    CONDITIONS, not panelist records. `nhl_elo_home_ml_strong` at 65.6% n=64 is
    the record of one narrow condition in which Elo's number looked strong — it
    is not Elo's record as an analyst.
  * `v3` has 4 signals and ZERO measured, so one of NFL's two real panelists
    has never been graded at all.
  * `nfl_model_consensus_*` is a hardcoded check on two specific columns, one of
    which (`v4_spread`, sd 0.29) says the same thing every week.

So three things are missing: no panelist makes a PICK, no panelist has a RECORD,
and consensus is a formula reading two cells rather than the panel's aggregate.

This script builds the first two. It takes each model's number, converts it into
the pick that number implies against the closing line, grades that pick, and
reports the panelist's record. That is the scorecard the engine needs before it
can weight panelists by how good they actually are.

══ WHAT A PICK MEANS HERE ══
A panelist's number is a prediction. The pick it implies is the side of the
closing line that number favours, and it only counts as a pick when the
disagreement clears EDGE_MIN — otherwise the panelist is effectively passing.
Grading every tiny disagreement as a pick measures rounding, not opinion.

SIGN CONVENTIONS are taken from one place. `close_spread` is the HOME handicap
in every sport EXCEPT NFL, where it is the AWAY line
(project_close_spread_sign_bug_914, verified n=7,325).

MLB AND NHL ARE GRADED ON TOTALS ONLY. Their `close_spread` / `close_puckline`
is a FIXED run/puck line (5 and 2 distinct values, sd 1.65 and 1.45), not a
margin forecast — grading a margin panelist against it measures nothing, which
is a mistake this script's author already made once today and is not repeating.

NOTHING IS WIRED. This reports records. Weighting panelists by them is the next
step and belongs to the engine, not here.

USAGE
    python build_panelist_records.py
    python build_panelist_records.py --sport NFL
    python build_panelist_records.py --market total
"""
from __future__ import annotations

import argparse
import collections
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

#: Sports whose close_spread is a REAL point spread. MLB/NHL excluded — see
#: the module docstring.
REAL_SPREAD = ('NFL', 'NCAAF', 'NBA', 'NCAAB')

#: panelist -> the context column carrying its predicted HOME MARGIN.
MARGIN_PANELISTS = {
    'NFL':   {'projected': 'projected_spread', 'v3': 'v3_spread',
              'v4': 'v4_spread', 'panel': 'panel_pred_spread',
              'mc': 'mc_expected_margin'},
    'NCAAF': {'projected': 'projected_spread', 'sp_plus': 'sp_plus_pred_spread',
              'epa': 'epa_pred_spread', 'mc': 'mc_expected_margin'},
    'NBA':   {'projected': 'projected_spread'},
    'NCAAB': {'projected': 'projected_spread'},
}
#: panelist -> the context column carrying its predicted TOTAL.
TOTAL_PANELISTS = {
    'NFL':   {'projected': 'projected_total', 'v3': 'v3_total',
              'v4': 'v4_total', 'panel': 'panel_pred_total'},
    'NCAAF': {'projected': 'projected_total', 'sp_plus': 'sp_plus_pred_total'},
    'NHL':   {'projected': 'projected_total', 'panel': 'panel_pred_total'},
    'NBA':   {'projected': 'projected_total'},
    'MLB':   {'projected': 'projected_total', 'model': 'model_pred_total',
              'jerry': 'jerry_pred_total', 'panel': 'panel_implied_total'},
    'NCAAB': {'projected': 'projected_total'},
}
#: How far a panelist must disagree with the line before it counts as a pick.
EDGE_MIN = {'NFL': 1.0, 'NCAAF': 1.5, 'NBA': 1.5, 'NCAAB': 1.5,
            'MLB': 0.3, 'NHL': 0.25}
PRICE = -110


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
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


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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


def grade(sport: str, market: str) -> None:
    rows = joined(sport)
    table = (MARGIN_PANELISTS if market == 'margin' else TOTAL_PANELISTS).get(sport)
    if not table:
        return
    if market == 'margin' and sport not in REAL_SPREAD:
        print(f'\n{sport} · MARGIN — SKIPPED: close_spread is a fixed '
              f'run/puck line, not a margin forecast')
        return
    emin = EDGE_MIN[sport]
    print(f'\n{"="*74}\n{sport} · {market.upper()}   {len(rows)} graded games '
          f'· a pick needs |disagreement| >= {emin}')
    print(f'  {"panelist":<12}{"picks":>7}{"W-L":>10}{"hit%":>8}{"units":>9}'
          f'{"ROI":>8}{"avg edge":>10}  note')
    base_shown = False
    for name, col in sorted(table.items()):
        n = w = l = p = 0
        u = 0.0
        edges = []
        for c, g, hs, as_ in rows:
            pv = _f(c.get(col))
            if pv is None:
                continue
            if market == 'margin':
                cs = _f(g.get('close_spread'))
                if cs is None:
                    continue
                home_line = -cs if sport == 'NFL' else cs
                mkt_margin = -home_line          # market's implied home margin
                dev = pv - mkt_margin
                if abs(dev) < emin:
                    continue
                actual = hs - as_
                cover = actual + home_line       # >0 home covered
                if abs(cover) < 1e-9:
                    p += 1
                    continue
                took_home = dev > 0
                hit = (cover > 0) == took_home
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
                took_over = dev > 0
                hit = (tot > ct) == took_over
            n += 1
            edges.append(abs(dev))
            if hit:
                w += 1
                u += 100 / abs(PRICE)
            else:
                l += 1
                u -= 1.0
        if n == 0:
            print(f'  {name:<12}{0:>7}{"—":>10}{"—":>8}{"—":>9}{"—":>8}'
                  f'{"—":>10}  never disagrees by >= {emin} (a panelist who '
                  f'always passes)')
            continue
        hitpct = 100 * w / (w + l) if (w + l) else 0.0
        note = ''
        if hitpct >= 52.38:
            note = 'above -110 breakeven'
        print(f'  {name:<12}{n:>7}{f"{w}-{l}":>10}{hitpct:>7.1f}%{u:>9.2f}'
              f'{100*u/n:>7.1f}%{statistics.mean(edges):>10.2f}  {note}')
        base_shown = True
    if base_shown:
        print(f'  (-110 breakeven is 52.38%. "avg edge" is how far the panelist '
              f'typically disagrees with the line.)')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(RESULTS))
    ap.add_argument('--market', choices=('margin', 'total'))
    a = ap.parse_args()
    sports = [a.sport] if a.sport else ['NFL', 'NCAAF', 'NBA', 'NCAAB', 'MLB', 'NHL']
    markets = [a.market] if a.market else ['margin', 'total']
    print('PANELIST SCORECARD — each model graded on the picks its own number '
          'implies.')
    for s in sports:
        for m in markets:
            try:
                grade(s, m)
            except Exception as e:                             # noqa: BLE001
                print(f'\n{s} · {m}: FAILED {type(e).__name__}: {str(e)[:110]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
