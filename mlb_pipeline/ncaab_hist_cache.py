# -*- coding: utf-8 -*-
"""Local caches for the NCAAB measurement scripts, so a re-run costs no DB.

Two pulls, both keyset-paged on a UNIQUE key. Never limit/offset on
game_date -- it is non-unique and offset paging over it fabricates
duplicates and blank pages.

    from ncaab_hist_cache import load_results, load_fanmatch
    rows = load_results()           # cached; pass refresh=True to re-pull

Cache files are the gitignored scratch convention (_*).
"""
from __future__ import annotations

import os
import pickle

import requests
from dotenv import load_dotenv

load_dotenv()

RESULTS_PKL = '_ncaab_hist.pkl'
FANMATCH_PKL = '_fanmatch.pkl'

RESULT_COLS = (
    'game_id,game_date,season,home_team,away_team,home_score,away_score,'
    'close_spread,close_total,home_ml_close,away_ml_close,open_spread,'
    'open_total,home_ml_open,away_ml_open,spread_result,total_result,'
    'home_win,home_adj_em,away_adj_em,adj_em_gap,pace_avg,projected_spread,'
    'projected_total'
)


def _conn():
    u = os.environ['SUPABASE_URL']
    k = os.environ['SUPABASE_KEY']
    return u, {'apikey': k, 'Authorization': f'Bearer {k}'}


def load_results(refresh: bool = False, quiet: bool = False) -> list:
    """Every ncaab_game_results row, keyset-paged on game_id."""
    if not refresh and os.path.exists(RESULTS_PKL):
        return pickle.load(open(RESULTS_PKL, 'rb'))
    u, h = _conn()
    rows: list = []
    last = ''
    while True:
        r = requests.get(f'{u}/rest/v1/ncaab_game_results', headers=h,
                         params={'select': RESULT_COLS, 'order': 'game_id.asc',
                                 'limit': '1000', 'game_id': f'gt.{last}'},
                         timeout=60)
        r.raise_for_status()
        b = r.json()
        if not b:
            break
        rows += b
        last = b[-1]['game_id']
        if not quiet:
            print(f'  results {len(rows)}', end='\r')
    if not quiet:
        print(f'  results {len(rows)}')
    pickle.dump(rows, open(RESULTS_PKL, 'wb'))
    return rows


def load_fanmatch(refresh: bool = False, quiet: bool = False) -> list:
    """Flatten every kenpom_fanmatch_<date> snapshot into game rows.

    Each row is tagged with the snapshot date it came from, which is the
    only thing that makes it point-in-time. KenPom's own FanMatch keeps the
    PREGAME prediction on the page for a completed day, and the leak check
    is in measure_kenpom_vs_close.py: a MAE far below the market's would
    mean the snapshot is post-hoc.
    """
    if not refresh and os.path.exists(FANMATCH_PKL):
        return pickle.load(open(FANMATCH_PKL, 'rb'))
    u, h = _conn()
    r = requests.get(f'{u}/rest/v1/kenpom_cache', headers=h,
                     params={'select': 'cache_key,data,fetched_at',
                             'cache_key': 'like.kenpom_fanmatch_%',
                             'order': 'cache_key.asc'}, timeout=60)
    r.raise_for_status()
    games: list = []
    for row in r.json():
        dat = row['data']
        if not isinstance(dat, list):
            continue
        day = row['cache_key'].replace('kenpom_fanmatch_', '')
        for g in dat:
            if not isinstance(g, dict):
                continue
            g['_snap_date'] = day
            g['_fetched'] = row['fetched_at']
            games.append(g)
    if not quiet:
        print(f'  fanmatch {len(games)} game rows')
    pickle.dump(games, open(FANMATCH_PKL, 'wb'))
    return games
