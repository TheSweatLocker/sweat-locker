#!/usr/bin/env python3
"""Assert a sport's game context carries the fields its CLIENT CARDS read.

    python verify_sport_launch_ready.py --sport NCAAB
    python verify_sport_launch_ready.py --sport ALL

WHY THIS EXISTS
───────────────
The same defect has now shipped three times, once per sport, and each time it
was found by a user rather than by us:

  2026-09-28  NHL   ctx chain fell through to null -> every NHL card dead
  2026-10-20  NBA   five ctx fields the select never asked for -> two whole
                    cards never rendered ("why dont i see nba badges")
  2026-10-21  NCAAB ctx chain still ended at NBA, so NCAAB would have
                    resolved null and taken all four of its cards down on
                    opening night

The shape is always identical: the DATA EXISTS in the table, and the client
either never asks for the column or never resolves the row, so each card
takes its own "no data" path and renders nothing. Nothing fails. Nothing
logs. It just looks like a quiet sport.

NCAAB is the reason this is a script and not a one-off check. Its season
opens 2026-11-03, `basketball_ncaab` is NOT yet in the Odds API's active
sports list (verified: only basketball_ncaab_championship_winner is), so
ncaab_game_context is EMPTY and there is nothing to verify against. Combined
with having no OTA path (B5), a wiring mistake found after launch cannot be
fixed without another App Store review. So the check has to be automatic and
has to fire the moment rows appear.

WHAT IT CHECKS
──────────────
For each sport, the ctx fields its client cards actually read:
  1. does the COLUMN exist on the context table
  2. is it POPULATED on the forward slate
Column-exists catches the 400-blanks-the-sport failure. Populated catches the
subtler one, where the column is there and empty so the card still shows
nothing.

Exit 1 on a missing column (a real wiring break), 0 with a warning when the
table is simply empty — preseason is not a failure.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, '.env'))
load_dotenv(os.path.join(os.path.dirname(HERE), '.env'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_SERVICE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

CTX_TABLE = {
    'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context', 'NHL': 'nhl_game_context',
    'NBA': 'nba_game_context', 'NCAAB': 'ncaab_game_context',
}

# The ctx fields each sport's client cards read. Extracted from the card
# bodies in app/components/GameDetailV2.tsx and the games-tab chip block in
# app/index.tsx -- not guessed, and worth re-extracting when cards change.
#
# `required` fails the run when the column is missing. `badge` additionally
# drives a games-tab chip, so an empty one means a visibly bare card.
CARD_FIELDS = {
    'NCAAB': {
        'required': [
            # efficiency + pace cards
            'home_adj_em', 'away_adj_em', 'home_adj_oe', 'away_adj_oe',
            'home_adj_de', 'away_adj_de', 'home_tempo', 'away_tempo',
            'pace_avg',
            # four factors
            'home_efg_o', 'away_efg_o', 'home_to_o', 'away_to_o',
            'home_or_o', 'away_or_o', 'home_ftr_o', 'away_ftr_o',
            # form + rest
            'home_record', 'away_record', 'home_l10', 'away_l10',
            'home_days_rest', 'away_days_rest',
            # shared
            'projected_spread', 'projected_total', 'close_spread',
            'close_total', 'primary_play',
        ],
        'badge': ['home_tempo', 'away_tempo', 'home_adj_em', 'away_adj_em'],
    },
    'NBA': {
        'required': [
            'home_abbrev', 'away_abbrev', 'home_starters_out',
            'away_starters_out', 'home_injury_impact', 'away_injury_impact',
            'home_pace', 'away_pace', 'home_rest_days', 'away_rest_days',
            'home_is_b2b', 'away_is_b2b', 'primary_play',
        ],
        'badge': ['home_is_b2b', 'away_is_b2b', 'home_pace', 'away_pace',
                  'home_rest_days', 'away_rest_days'],
    },
    'NHL': {
        'required': [
            'home_goalie', 'away_goalie', 'home_goalie_confirmed',
            'away_goalie_confirmed', 'home_goalie_sv_pct',
            'away_goalie_sv_pct', 'home_pk_pct', 'away_pk_pct',
            'home_pp_pct', 'away_pp_pct', 'primary_play',
        ],
        'badge': ['home_goalie_confirmed', 'away_goalie_confirmed',
                  'home_goalie_sv_pct', 'away_goalie_sv_pct',
                  'home_pk_pct', 'away_pk_pct'],
    },
    'NFL': {
        'required': ['div_game', 'kickoff_utc', 'temp', 'wind',
                     'primary_play'],
        'badge': ['div_game', 'kickoff_utc'],
    },
    'NCAAF': {
        'required': ['temp', 'wind', 'dome', 'primary_play'],
        'badge': ['temp', 'wind'],
    },
    'MLB': {'required': ['primary_play'], 'badge': ['primary_play']},
}


def column_exists(table: str, col: str) -> bool:
    """A select naming a missing column 400s even on an EMPTY table, which is
    what makes this usable before a season starts."""
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                     params={'select': col, 'limit': '1'}, timeout=30)
    return r.status_code == 200


def check(sport: str, days: int) -> tuple[int, int]:
    table = CTX_TABLE.get(sport)
    spec = CARD_FIELDS.get(sport)
    if not table or not spec:
        print(f'  {sport}: not registered here — skip')
        return 0, 0
    fields = spec['required']
    badges = set(spec.get('badge') or [])

    # 1. columns. Checked individually so the report names the offender, then
    #    as ONE request, because that is how the app sends it and one bad
    #    column takes the whole select down.
    missing = [c for c in fields if not column_exists(table, c)]
    if missing:
        print(f'  {sport}: FAIL — {len(missing)} column(s) absent from '
              f'{table}: {", ".join(missing)}')
        print(f'    The client select naming these returns 400, which this '
              f'app reads as "no context" — it would blank the sport.')
        return 1, 0
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                     params={'select': ','.join(fields), 'limit': '1'},
                     timeout=40)
    if r.status_code != 200:
        print(f'  {sport}: FAIL — the full select returns '
              f'{r.status_code}: {r.text[:160]}')
        return 1, 0

    # 2. population on the forward slate.
    today = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    rows = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                        params={'select': ','.join(fields),
                                'game_date': [f'gte.{today}', f'lte.{end}'],
                                'limit': '300'}, timeout=60).json()
    if not isinstance(rows, list) or not rows:
        print(f'  {sport}: columns all present. No forward games in '
              f'{today}..{end} — nothing to measure yet (preseason or an '
              f'off-week). NOT a failure.')
        return 0, 1

    print(f'  {sport}: {len(rows)} forward games, all {len(fields)} columns '
          f'present')
    starved = []
    for c in fields:
        fed = sum(1 for x in rows if x.get(c) not in (None, '', [], {}))
        pct = 100.0 * fed / len(rows)
        tag = ''
        if fed == 0:
            tag = '  <-- ALWAYS NULL'
            starved.append(c)
        elif pct < 50:
            tag = '  <-- thin'
        mark = '*' if c in badges else ' '
        print(f'     {mark}{c:28} {fed:>4}/{len(rows)} ({pct:5.1f}%){tag}')
    if starved:
        print(f'    WARNING: {len(starved)} column(s) present but never '
              f'populated — the card renders nothing and nothing logs: '
              f'{", ".join(starved)}')
        return 0, len(starved)
    return 0, 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAB',
                    help='one sport or ALL')
    ap.add_argument('--days', type=int, default=7)
    a = ap.parse_args()
    if not SB or not KEY:
        print('FAIL: SUPABASE_URL / service key missing')
        return 2
    sports = sorted(CTX_TABLE) if a.sport.upper() == 'ALL' else [
        a.sport.upper()]
    print(f'=== sport launch readiness · {", ".join(sports)} ===')
    fails = warns = 0
    for s in sports:
        f, w = check(s, a.days)
        fails += f
        warns += w
        print()
    if fails:
        print(f'FAIL: {fails} sport(s) have a column the client reads that '
              f'does not exist.')
        return 1
    print(f'PASS: every client-read column exists on every checked sport'
          + (f' ({warns} population warning(s))' if warns else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
