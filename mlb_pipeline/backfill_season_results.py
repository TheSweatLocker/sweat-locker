"""backfill_season_results — recover a missing season of game results.

THE PATTERN, found three times in two days. Three sports were each missing
the most recently COMPLETED season from their results table:

    NHL     2025-26 missing   -> fixed 2026-09-21 (1,528 games recovered)
    NBA     2025-26 missing   -> this script
    NCAAB   2025-26 missing   -> this script (source TBD, see below)

It is never just a gap in a table. Everything downstream is computed from
results, so a missing season silently degrades the model rather than
failing:

  * NBA: nba_elo.save_ratings computes off_rating / def_rating / net_rating
    from nba_game_results. With the season absent, all three land NULL for
    2025-26 — measured 0/30 teams — while pace and efg_pct (from the
    four-factors puller) stay populated. A totals model running on pace
    with no efficiency ratings projects ~8 points BELOW the market on
    every game: mean -7.8, median -8.1. That is a permanent UNDER lean on
    every NBA pick. Spreads come out at 8.2 sd against the close, with the
    wrong team favoured on some games (MIL@WAS: market -6.5, model +6.3).
  * NHL: elo trained on one season instead of two, so every rating sat
    near the 1500 default and projected_home_wp was 0.585 on every game.

WHAT THIS WRITES, AND WHAT IT DELIBERATELY DOES NOT. Scores, winner,
OT flag and the scored total come from the league scoreboard and are
facts. It does NOT write close_spread / close_total: this source has no
lines, and filling a line column with whatever is nearest to hand is
exactly how nhl_game_results.close_puckline ended up holding MONEYLINE
values on 1,120 rows. Lines come from
backfill_historical_closing_odds.py, which knows it is fetching lines.

IDEMPOTENT: upserts on game_id, so a re-run repairs rather than
duplicates.

CLI
  python backfill_season_results.py --sport NBA --start 2025-10-01 --end 2026-07-01 --dry-run
  python backfill_season_results.py --sport NBA --season 2025-26
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except ImportError:
    Retry = None

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

_S = requests.Session()
if Retry is not None:
    _S.mount('https://', HTTPAdapter(
        max_retries=Retry(total=4, backoff_factor=0.5,
                          status_forcelist=(500, 502, 503, 504, 429),
                          allowed_methods=frozenset(['GET', 'POST'])),
        pool_connections=8, pool_maxsize=8))


def _nba_scoreboard(ds: str) -> list:
    from nba_data_client import get_scoreboard
    return get_scoreboard(ds)


def _nhl_scoreboard(ds: str) -> list:
    from nhl_data_client import get_scoreboard
    return get_scoreboard(ds)


SPORTS = {
    'NBA': {'results': 'nba_game_results', 'scoreboard': _nba_scoreboard,
            'scored_col': 'total_points', 'has_so': False},
    'NHL': {'results': 'nhl_game_results', 'scoreboard': _nhl_scoreboard,
            'scored_col': 'total_goals', 'has_so': True},
}


def existing_ids(table: str) -> set:
    out = set()
    for off in range(0, 40000, 1000):
        r = _S.get(f'{SB}/rest/v1/{table}', headers=H_READ, timeout=40,
                   params={'select': 'game_id', 'limit': 1000, 'offset': off})
        if r.status_code != 200:
            break
        chunk = r.json()
        if not isinstance(chunk, list):
            break
        out |= {str(x['game_id']) for x in chunk if x.get('game_id')}
        if len(chunk) < 1000:
            break
    return out


def run(sport: str, start: str, end: str, season: str | None,
        dry_run: bool) -> int:
    cfg = SPORTS[sport]
    d0 = datetime.fromisoformat(start).date()
    d1 = datetime.fromisoformat(end).date()
    have = existing_ids(cfg['results'])
    print(f'=== backfill_season_results · {sport} · {start}..{end} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')
    print(f'  {cfg["results"]} already holds {len(have)} game_ids')

    batch, stats = [], Counter()
    empty_streak = 0
    day = d0
    while day <= d1:
        ds = day.isoformat()
        try:
            games = cfg['scoreboard'](ds)
        except Exception as e:
            print(f'  ⚠ {ds}: {type(e).__name__}: {str(e)[:60]}')
            games = []
            stats['fetch_error'] += 1
        if not games:
            empty_streak += 1
            # Off-days and the off-season are normal. Only surface a long
            # run so a dead endpoint is visible instead of looking like a
            # quiet calendar.
            if empty_streak == 40:
                print(f'  … 40 consecutive empty dates ending {ds} '
                      f'(off-season is expected in this range)')
        else:
            empty_streak = 0
        for g in games:
            gid = str(g.get('game_id') or '')
            hs, as_ = g.get('home_score'), g.get('away_score')
            if not gid or hs is None or as_ is None:
                stats['incomplete'] += 1
                continue
            if gid in have:
                stats['already_present'] += 1
                continue
            row = {
                'game_id': gid,
                'game_date': ds,
                'home_team': g.get('home_team'),
                'away_team': g.get('away_team'),
                'home_score': int(hs),
                'away_score': int(as_),
                'home_win': int(hs) > int(as_),
                cfg['scored_col']: int(hs) + int(as_),
                'went_to_ot': bool(g.get('went_to_ot')),
            }
            if cfg['has_so']:
                row['went_to_so'] = bool(g.get('went_to_so'))
            if g.get('home_abbrev'):
                row['home_abbrev'] = g['home_abbrev']
                row['away_abbrev'] = g.get('away_abbrev')
            if season:
                row['season'] = season
            batch.append(row)
            stats['new'] += 1
        day += timedelta(days=1)

    print(f'  new games: {stats["new"]}  already present: '
          f'{stats["already_present"]}  incomplete: {stats["incomplete"]}'
          + (f'  fetch errors: {stats["fetch_error"]}' if stats['fetch_error'] else ''))
    if not batch:
        return 0
    dates = sorted({b['game_date'] for b in batch})
    print(f'  span: {dates[0]} .. {dates[-1]} across {len(dates)} dates')
    if dry_run:
        for b in batch[:5]:
            print(f'    [DRY] {b["game_date"]} {b["away_team"]} {b["away_score"]} '
                  f'@ {b["home_team"]} {b["home_score"]}')
        print(f'  [DRY] would upsert {len(batch)}')
        return len(batch)

    ok = 0
    for i in range(0, len(batch), 200):
        chunk = batch[i:i + 200]
        r = _S.post(f'{SB}/rest/v1/{cfg["results"]}?on_conflict=game_id',
                    headers=H_WRITE, json=chunk, timeout=60)
        if r.status_code in (200, 201, 204):
            ok += len(chunk)
        else:
            print(f'  ⚠ upsert {r.status_code}: {r.text[:200]}')
    print(f'  upserted {ok}')
    if ok < len(batch):
        print(f'  ⚠ INCOMPLETE — {len(batch) - ok} unwritten. Re-run to finish.')
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(SPORTS.keys()), required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p.add_argument('--season', help="season label to stamp, e.g. '2025-26'")
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    run(a.sport, a.start, a.end, a.season, a.dry_run)


if __name__ == '__main__':
    main()
