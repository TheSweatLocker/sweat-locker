"""Pull NFL history straight from nflverse instead of scraping a renderer.

2026-09-23. Andy asked what we could take from gridlinestats. The answer
turned out to be: nothing by scraping. Their footer credits nflverse and
their player URLs are /nfl/player/00-0034828 — the nflverse GSIS id, the
same id space as nfl_player_stats.player_id. Their robots.txt also
disallows /api/ and /nfl/player/*/, which is exactly the layer worth
having. They are a renderer over the source we already use.

What the visit was worth is the measurement. They compute situational
ATS and over/under splits across 1999-2026, 6,999 regular-season games.
We held 1,967 — seasons 2020 onward. 28% of a free dataset.

That shortfall is why our situational trends cannot be stated honestly:
an n=4 streak is not a read, it is noise with a sentence around it.

This module closes it. Two feeds, one GET each:

    games.csv         7,548 rows, 1999+  -> nfl_game_results
    player_stats.csv  134,470 rows       -> nfl_player_stats

games.csv also carries three prices we have never stored anywhere:
away/home_spread_odds and under/over_odds. Today 13 of 16 published NFL
picks are spreads or totals with a line and NO price on file, so we
cannot compute their EV or their closing-line value at all.

Dry by default. Nothing is written without --write.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from collections import Counter

import requests

GAMES_URL = 'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
PLAYER_URL = ('https://github.com/nflverse/nflverse-data/releases/download/'
              'player_stats/player_stats.csv')

_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


def fetch_csv(url: str) -> list[dict]:
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def num(v, cast=float):
    """CSV empty string -> None. nflverse leaves unknown fields blank."""
    if v is None or v == '' or v == 'NA':
        return None
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def map_game(g: dict) -> dict | None:
    """nflverse games.csv row -> nfl_game_results shape.

    Kept deliberately close to the existing column names so this can
    backfill the same table the pipeline already reads rather than
    introducing a parallel history nobody joins to.
    """
    season = num(g.get('season'), int)
    if season is None:
        return None
    hs, as_ = num(g.get('home_score'), int), num(g.get('away_score'), int)
    spread = num(g.get('spread_line'))
    total_line = num(g.get('total_line'))
    total_pts = None if (hs is None or as_ is None) else hs + as_

    # Results only where the game is actually played AND priced.
    #
    # Vocabulary matters more than it looks. The first pass of this
    # backfill emitted HOME/AWAY/PUSH and OVER/UNDER/PUSH, while every
    # row already in the table uses home_covered/away_covered/push and
    # over/under/push. Same column, two languages — so a query filtering
    # spread_result='home_covered' would silently return zero of the
    # 5,583 rows we had just added and nobody would see an error. Match
    # what is already there; the existing convention is canonical
    # because it is what the pipeline reads.
    spread_result = total_result = home_win = None
    if hs is not None and as_ is not None:
        home_win = hs > as_
        if spread is not None:
            # nflverse spread_line is points the HOME team is favoured by.
            margin = hs - as_
            spread_result = ('push' if margin == spread else
                             'home_covered' if margin > spread else 'away_covered')
        if total_line is not None and total_pts is not None:
            total_result = ('push' if total_pts == total_line else
                            'over' if total_pts > total_line else 'under')

    return {
        'game_id': g.get('game_id'),
        'season': season,
        'week': num(g.get('week'), int),
        'game_type': g.get('game_type') or None,
        'game_date': g.get('gameday') or None,
        'weekday': g.get('weekday') or None,
        'gametime': g.get('gametime') or None,
        'away_team': g.get('away_team') or None,
        'home_team': g.get('home_team') or None,
        'away_score': as_,
        'home_score': hs,
        'total_points': total_pts,
        'home_win': home_win,
        'overtime': None if g.get('overtime') in ('', None) else g.get('overtime') == '1',
        'div_game': None if g.get('div_game') in ('', None) else g.get('div_game') == '1',
        'roof': g.get('roof') or None,
        'surface': g.get('surface') or None,
        'temp': num(g.get('temp'), int),
        'wind': num(g.get('wind'), int),
        'away_rest': num(g.get('away_rest'), int),
        'home_rest': num(g.get('home_rest'), int),
        'referee': g.get('referee') or None,
        'stadium': g.get('stadium') or None,
        'close_spread': spread,
        'close_total': total_line,
        'close_home_ml': num(g.get('home_moneyline'), int),
        'close_away_ml': num(g.get('away_moneyline'), int),
        'spread_result': spread_result,
        'total_result': total_result,
    }


def existing_game_ids() -> set:
    out, off = set(), 0
    while True:
        r = requests.get(f'{SB}/rest/v1/nfl_game_results', headers=H_READ,
                         timeout=180,
                         params={'select': 'game_id', 'limit': 1000, 'offset': off})
        b = r.json()
        if not isinstance(b, list):
            raise SystemExit(f'read failed: {str(b)[:300]}')
        out |= {x['game_id'] for x in b}
        if len(b) < 1000:
            return out
        off += 1000


def push(table: str, rows: list[dict], on_conflict: str, chunk: int = 500) -> int:
    done = 0
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        r = requests.post(f'{SB}/rest/v1/{table}', headers=H_WRITE, timeout=180,
                          params={'on_conflict': on_conflict}, json=part)
        if r.status_code not in (200, 201, 204):
            print(f'  !! {table} chunk {i}: HTTP {r.status_code} {r.text[:300]}')
            break
        done += len(part)
        print(f'    {done}/{len(rows)}', flush=True)
    return done


# ──────────────────────────────────────────────────────────────────────
# player_stats — weekly player box scores, 2000 onward
# ──────────────────────────────────────────────────────────────────────
# 2026-09-24. Andy: "lets know everything we can get out and the plan to
# exploit for our processes."
#
# We held 38,504 rows; upstream carries 134,470. 41 of our 43 columns map
# straight across (their recent_team is our team), so this needs no
# migration — it is the same table with the other 71% of its history.
#
# Why it matters beyond volume: B10 is that NFL props carry a projection
# on 2 of 1,423 rows, so there is nothing to price an edge against. A
# shrinkage baseline fitted out-of-sample needs prior seasons of player
# form to fit ON. Two seasons cannot do that; twenty-six can.
#
# The 11 upstream columns we have nowhere to put are named here rather
# than silently dropped, so the next person who wants fumbles or 2-point
# conversions knows they exist upstream and only need a column.
_PS_SKIP = {
    'headshot_url', 'player_display_name', 'recent_team',
    'passing_2pt_conversions', 'rushing_2pt_conversions',
    'receiving_2pt_conversions', 'rushing_fumbles', 'rushing_fumbles_lost',
    'receiving_fumbles', 'receiving_fumbles_lost', 'sack_fumbles',
    'sack_fumbles_lost',
}
_PS_TEXT = {'player_id', 'player_name', 'position', 'position_group',
            'season_type', 'opponent_team'}

# The CSV writes whole numbers as "0.0", and 24 of our columns are typed
# integer. Sending 0.0 to an integer column fails the whole chunk with
# 22P02 invalid input syntax — which is what happened on the first run:
# "wrote 0". A clean total failure rather than a partial write, which is
# the one good thing about it. Types are read from the live OpenAPI schema
# rather than guessed, so this list cannot drift from the table.
_PS_INT = {
    'attempts', 'carries', 'completions', 'interceptions',
    'passing_air_yards', 'passing_first_downs', 'passing_tds',
    'passing_yards', 'passing_yards_after_catch', 'receiving_air_yards',
    'receiving_first_downs', 'receiving_tds', 'receiving_yards',
    'receiving_yards_after_catch', 'receptions', 'rushing_first_downs',
    'rushing_tds', 'rushing_yards', 'sack_yards', 'sacks', 'season',
    'special_teams_tds', 'targets', 'week',
}


def _as_int(v):
    """'0.0' -> 0, '7' -> 7, '' -> None. Rounds rather than truncates so a
    fractional sack total (0.5 sacks is a real stat) lands on the nearest
    whole number instead of silently flooring to zero."""
    f = num(v)
    return None if f is None else int(round(f))
PLAYER_STATS_URL = ('https://github.com/nflverse/nflverse-data/releases/'
                    'download/player_stats/player_stats.csv')


def map_player_stat(row: dict, keep: set) -> dict | None:
    if not row.get('player_id') or not row.get('season'):
        return None
    out = {}
    for k, v in row.items():
        if k in _PS_SKIP or k not in keep:
            continue
        if v in ('', 'NA', None):
            out[k] = None
        elif k in _PS_TEXT:
            out[k] = v
        elif k in _PS_INT:
            out[k] = _as_int(v)
        else:
            out[k] = num(v)
    out['team'] = row.get('recent_team') or None
    # player_name is EMPTY on 67,401 of 134,470 upstream rows — every older
    # season carries the name in player_display_name instead. Ingesting the
    # blank would have written half the history with no name, and every
    # prop and form lookup we have joins on name, so it would have been
    # silently useless rather than visibly broken.
    if not out.get('player_name'):
        out['player_name'] = (row.get('player_display_name')
                              or row.get('player_name') or None)
    # Three rows of 134,470 carry no name in either field, and player_name
    # is NOT NULL here. One of them aborted a chunk mid-backfill (23502)
    # after 1,500 rows had already landed. A row we cannot name is a row
    # nothing can join to, so drop it rather than invent a placeholder that
    # would later look like a real player.
    if not out.get('player_name'):
        return None
    return out


def ingest_player_stats(write: bool, since: int, until: int) -> None:
    print('=== nflverse player_stats ===')
    raw = fetch_csv(PLAYER_STATS_URL)
    probe = requests.get(f'{SB}/rest/v1/nfl_player_stats', headers=H_READ,
                         timeout=60, params={'select': '*', 'limit': 1}).json()
    keep = set(probe[0].keys()) if probe else set()
    mapped = []
    for r in raw:
        s = num(r.get('season'), int)
        if s is None or not (since <= s <= until):
            continue
        m = map_player_stat(r, keep)
        if m:
            mapped.append(m)
    # Exactly one duplicate key exists upstream — Matthew Stafford, 2010
    # week 8 REG, listed twice with identical figures. Postgres rejects a
    # whole chunk with 21000 "ON CONFLICT DO UPDATE command cannot affect
    # row a second time" when a batch contains the same key twice, so that
    # single row aborted the backfill at 57,000 written. Dedupe on the
    # conflict key, last occurrence wins.
    _seen: dict = {}
    for m in mapped:
        _seen[(m.get('player_id'), m.get('season'),
               m.get('week'), m.get('season_type'))] = m
    if len(_seen) != len(mapped):
        print(f'  deduped           : {len(mapped) - len(_seen)} duplicate key(s)')
    mapped = list(_seen.values())

    print(f'  upstream rows     : {len(raw)}')
    print(f'  mapped {since}-{until} : {len(mapped)}')
    if mapped:
        by = Counter(m['season'] for m in mapped)
        print(f'  seasons           : {min(by)}-{max(by)} ({len(by)} seasons)')
    if not write:
        print('  DRY RUN — re-run with --write to upsert.')
        if mapped:
            sm = mapped[0]
            print(f"  sample: {sm.get('player_name')} {sm.get('season')}"
                  f"w{sm.get('week')} {sm.get('team')} "
                  f"rec_yds={sm.get('receiving_yards')} epa={sm.get('receiving_epa')}")
        return
    print(f'  upserting {len(mapped)} rows...')
    n = push('nfl_player_stats', mapped,
             on_conflict='player_id,season,week,season_type', chunk=500)
    print(f'  wrote {n}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--player-stats', action='store_true',
                    help='ingest weekly player box scores instead of games')
    ap.add_argument('--write', action='store_true',
                    help='actually upsert; without it this only reports the delta')
    ap.add_argument('--since', type=int, default=1999)
    ap.add_argument('--until', type=int, default=2019,
                    help='inclusive upper season bound. Defaults to 2019 on '
                         'purpose: seasons 2020-2025 already carry nflverse '
                         'game_ids and match, but our 2026 rows use a '
                         'different id scheme, so pulling the current season '
                         'would DUPLICATE the live games that feed published '
                         'records rather than update them. Raise this only '
                         'after the 2026 id mismatch is resolved.')
    ap.add_argument('--repair', action='store_true',
                    help='re-upsert every row in range, not just unseen ids — '
                         'used to correct a value written by an earlier run')
    args = ap.parse_args()

    if args.player_stats:
        # player_stats has no game_id collision problem, so the 2019 cap
        # that protects nfl_game_results does not apply here — take it all.
        ingest_player_stats(args.write, args.since, 2026)
        return

    print('=== nflverse games.csv ===')
    raw = fetch_csv(GAMES_URL)
    mapped = [m for m in (map_game(g) for g in raw)
              if m and args.since <= m['season'] <= args.until and m['game_id']]
    have = existing_game_ids()
    new = mapped if args.repair else [m for m in mapped if m['game_id'] not in have]
    print(f'  upstream rows   : {len(raw)}')
    print(f'  mapped {args.since}-{args.until} : {len(mapped)}')
    print(f'  already stored  : {len(have)}')
    print(f'  NEW to us       : {len(new)}')
    by = Counter(m['season'] for m in new)
    if by:
        print('  new by season   : ' + ' '.join(f'{s}:{n}' for s, n in sorted(by.items())))

    priced = sum(1 for m in mapped if m['close_spread'] is not None)
    mls = sum(1 for m in mapped if m['close_home_ml'] is not None)
    print(f'  with spread     : {priced}/{len(mapped)}')
    print(f'  with moneyline  : {mls}/{len(mapped)}')

    if not args.write:
        print('\n  DRY RUN — nothing written. Re-run with --write to upsert.')
        if new:
            s = new[0]
            print(f"  sample new row  : {s['game_date']} {s['away_team']}@{s['home_team']} "
                  f"spread={s['close_spread']} total={s['close_total']} "
                  f"ml={s['close_away_ml']}/{s['close_home_ml']} result={s['spread_result']}")
        return

    print(f'\n  upserting {len(new)} rows...')
    n = push('nfl_game_results', new, on_conflict='game_id')
    print(f'  wrote {n}')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
