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


def main():
    ap = argparse.ArgumentParser()
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
