#!/usr/bin/env python3
"""Every data layer the engine reads, per sport, on the games we are betting.

== WHY (2026-11-16) ========================================================
ANDY: "i want you to verify data ened to end across all sports on all games,
money flow, externals , stats, etc"

The existing audits each answer part of this -- audit_data_feeds covers
signal input fill, audit_external_source_coverage covers who is reporting --
but none of them answers it PER GAME ON THE LIVE SLATE, which is the only
version that tells you whether tonight's picks were made on real data.

READ-ONLY. Writes nothing, so it is safe to run before a slate.

A layer is reported as a share of the sport's games IN THE WINDOW, never as
a raw count, and every share carries its n
([[feedback_sample_size_with_pct]]). "absent" means the column does not
exist on that sport's context table at all, which is a different finding
from 0% fill ([[feedback_a_zero_fill_column_can_have_a_populated_twin]],
[[feedback_list_the_columns_before_declaring_data_lost]]) and is therefore
printed differently.

    python verify_end_to_end_all_sports.py
    python verify_end_to_end_all_sports.py --date 2026-11-16 --days 1
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv('.env')
load_dotenv('../.env')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

CTX = {
    'NBA':   'nba_game_context',
    'NHL':   'nhl_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
}
PROPS = {
    'NBA': 'nba_pipeline_props', 'NHL': 'nhl_pipeline_props',
    'NFL': 'nfl_pipeline_props',
}

#: layer -> the candidate columns that satisfy it, first one present wins.
#: Several are deliberately per-sport aliases: NBA/NHL store the moneyline
#: as {home,away}_ml_close while NCAAB/NFL use close_{home,away}_ml, and
#: reporting the alias as missing is how a populated twin gets called a gap.
LAYERS = [
    ('spread',     ['close_spread']),
    ('total',      ['close_total']),
    # The two football tables use the close_* PREFIX convention and the
    # other three use the *_close SUFFIX convention, so both spellings are
    # candidates for every sport and the BEST-FILLED one is reported. The
    # first version of this script took the first candidate that EXISTED
    # and so reported NFL home ML as 0% while close_home_ml sat at 199/200
    # -- it reproduced the exact bug it is meant to find.
    # [[feedback_a_zero_fill_column_can_have_a_populated_twin]]
    ('home ML',    ['home_ml_close', 'close_home_ml', 'home_ml_odds']),
    ('away ML',    ['away_ml_close', 'close_away_ml', 'away_ml_odds']),
    ('home ML op', ['home_ml_open', 'open_home_ml']),
    ('money flow', ['oddscrowd_snapshot']),
    ('team form',  ['team_form_enriched_at']),
    ('tendencies', ['team_tendencies_updated_at']),
    ('trends',     ['team_trends_updated_at']),
    ('model proj', ['projected_spread', 'model_pred_home_points',
                    'panel_prediction']),
    ('proj total', ['projected_total']),
    ('confluence', ['signal_confluence_net']),
]


def page(tbl: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=60,
                         params={**params, 'offset': str(off),
                                 'limit': '1000'})
        if r.status_code != 200:
            print(f'  ! read of {tbl} failed {r.status_code}: '
                  f'{r.text[:150]}')
            return out
        j = r.json() or []
        out += j
        if len(j) < 1000:
            return out
        off += 1000
        if off > 40000:
            return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--days', type=int, default=2,
                    help='window width forward from --date, inclusive')
    a = ap.parse_args()
    if not (SB and KEY):
        print('SUPABASE env missing')
        return 2
    lo = a.date
    hi = (dt.date.fromisoformat(a.date)
          + dt.timedelta(days=max(0, a.days - 1))).isoformat()
    print(f'=== end-to-end data verification · {lo} .. {hi} · read-only ===')

    width = max(len(n) for n, _ in LAYERS) + 1
    for sport, tbl in CTX.items():
        rows = page(tbl, {'select': '*', 'game_date': f'gte.{lo}',
                          'and': f'(game_date.lte.{hi})'})
        n = len(rows)
        print(f'\n-- {sport}: {n} game(s) on the slate  [{tbl}]')
        if not n:
            print('   no games in window')
            continue
        have = set(rows[0].keys())
        for name, cands in LAYERS:
            present = [c for c in cands if c in have]
            if not present:
                print(f'   {name:<{width}} ABSENT  (no column of '
                      f'{cands} on {tbl})')
                continue
            fills = {c: sum(1 for r in rows if r.get(c) not in (None, ''))
                     for c in present}
            col = max(fills, key=lambda c: fills[c])
            f = fills[col]
            pct = 100.0 * f / n
            bar = ('OK   ' if pct >= 90 else
                   'THIN ' if pct >= 50 else
                   'POOR ' if pct > 0 else 'DARK ')
            # An EMPTY twin beside a populated one is its own finding: any
            # code reading the empty spelling gets NULL and fails silently.
            dead = [c for c in present if c != col and fills[c] == 0]
            note = (f'  !! empty twin(s) {dead} - code reading those '
                    f'spellings sees NULL') if dead and f else ''
            print(f'   {name:<{width}} {bar} {f:3}/{n:<3} '
                  f'({pct:5.1f}%)  [{col}]{note}')

        # externals + reads + props are separate tables, same window
        ex = page('external_picks',
                  {'select': 'game_id,source,surface',
                   'sport': f'eq.{sport}', 'game_date': f'gte.{lo}',
                   'and': f'(game_date.lte.{hi})'})
        gids = {r.get('game_id') for r in rows}
        ex_games = len({e['game_id'] for e in ex if e['game_id'] in gids})
        srcs = sorted({e['source'] for e in ex})
        print(f'   {"externals":<{width}} '
              f'{"OK   " if ex_games >= 0.9 * n else "THIN " if ex_games else "DARK "}'
              f' {ex_games:3}/{n:<3} ({100.0 * ex_games / n:5.1f}%)  '
              f'{len(ex)} picks from {len(srcs)} source(s): '
              f'{",".join(srcs) or "-"}')

        rd = page('jerry_reads',
                  {'select': 'game_id,call_market,call_line,conviction',
                   'sport': f'eq.{sport}', 'game_date': f'gte.{lo}',
                   'and': f'(game_date.lte.{hi})'})
        rd_games = len({r['game_id'] for r in rd if r['game_id'] in gids})
        # a spread/total read with no number cannot be graded or bet
        bare = sum(1 for r in rd
                   if str(r.get('call_market') or '').lower()
                   in ('rl', 'spread', 'total') and r.get('call_line') is None)
        print(f'   {"reads":<{width}} '
              f'{"OK   " if rd_games >= 0.9 * n else "THIN " if rd_games else "DARK "}'
              f' {rd_games:3}/{n:<3} ({100.0 * rd_games / n:5.1f}%)  '
              f'{len(rd)} read(s)'
              + (f'  !! {bare} line-market read(s) with NO LINE' if bare
                 else ''))

        pt = PROPS.get(sport)
        if pt:
            # book_over_odds / book_under_odds, not `odds` -- the first
            # version asked for a column that does not exist, and a 400 on
            # an explicit select returns NO ROWS, which printed as
            # "props DARK 0/13". A wiring error is not a finding.
            # [[feedback_zero_rows_is_a_wiring_error]]
            pr = page(pt, {'select': 'game_id,tier,prop_line,'
                                     'book_over_odds,book_under_odds',
                           'game_date': f'gte.{lo}',
                           'and': f'(game_date.lte.{hi})'})
            pr_games = len({p['game_id'] for p in pr if p['game_id'] in gids})
            print(f'   {"props":<{width}} '
                  f'{"OK   " if pr_games else "DARK "}'
                  f' {pr_games:3}/{n:<3} ({100.0 * pr_games / n:5.1f}%)  '
                  f'{len(pr)} prop row(s)')

    print('\nOK >=90% · THIN >=50% · POOR >0% · DARK 0% · ABSENT no column')
    return 0


if __name__ == '__main__':
    sys.exit(main())
