"""Backfill home/away_ops_last7 and ops_last14 onto mlb_game_results.

For each game, computes the team's OPS over their previous 7 and 14
games (excluding the game being scored — point-in-time correct).

Uses MLB stats API team gameLog endpoint, cached per (team_id, season).
Each team has ~70-90 games of history per season we care about.

Run time estimate: ~32 teams × ~3 API calls each = ~100 API calls total
at 0.3s each = 30 seconds to fetch. Then process 3,500 game updates.
"""
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H_READ = {'apikey': SK, 'Authorization': f'Bearer {SK}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except: pass

SEASONS = [2025, 2026]


def fetch_team_id_map():
    """Get MLB team_id by team name."""
    r = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1&season=2026', timeout=15)
    data = r.json()
    name_to_id = {}
    for t in data.get('teams', []):
        name_to_id[t['name']] = t['id']
    return name_to_id


def fetch_team_gamelog(team_id, season):
    """Pull all games for a team in a season with hits/AB/walks/etc."""
    url = f'https://statsapi.mlb.com/api/v1/teams/{team_id}/stats?stats=gameLog&season={season}&group=hitting'
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return []
    data = r.json()
    games = []
    for stat in data.get('stats', []):
        for split in stat.get('splits', []):
            s = split.get('stat', {})
            games.append({
                'date': split.get('date'),
                'ops': float(s.get('ops', 0) or 0),
                'avg': float(s.get('avg', 0) or 0),
                'obp': float(s.get('obp', 0) or 0),
                'slg': float(s.get('slg', 0) or 0),
            })
    games.sort(key=lambda x: x['date'])
    return games


def compute_rolling_ops(games_sorted, target_date, window):
    """Return avg OPS over the `window` games strictly before target_date."""
    prior = [g for g in games_sorted if g['date'] < target_date]
    if len(prior) < 3:
        return None
    recent = prior[-window:]
    if not recent:
        return None
    return sum(g['ops'] for g in recent) / len(recent)


def main():
    print('=== team OPS history backfill ===')
    # Pull all games needing backfill (where ops_last7 is null)
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?or=(home_ops_last7.is.null,away_ops_last7.is.null)'
            f'&select=id,game_date,home_team,away_team,home_ops_last7,away_ops_last7,home_ops_last14,away_ops_last14'
            f'&order=game_date.desc&limit=1000&offset={offset}',
            headers=H_READ, timeout=30,
        )
        chunk = r.json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        offset += 1000
    print(f'  Games needing backfill: {len(rows)}')

    if not rows:
        print('  Nothing to do.')
        return

    print('  Fetching team_id map...')
    team_ids = fetch_team_id_map()
    print(f'  Team map: {len(team_ids)} teams')

    # Pre-fetch all team gamelogs we need
    teams_needed = set()
    for r in rows:
        teams_needed.add(r['home_team'])
        teams_needed.add(r['away_team'])

    print(f'  Fetching gamelogs for {len(teams_needed)} teams across {len(SEASONS)} seasons...')
    gamelog_cache = {}  # (team_name, season) -> list of game dicts
    for team_name in teams_needed:
        tid = team_ids.get(team_name)
        if not tid:
            print(f'    [WARN] no team_id for {team_name}')
            continue
        for season in SEASONS:
            games = fetch_team_gamelog(tid, season)
            gamelog_cache[(team_name, season)] = games
            time.sleep(0.2)
        print(f'    {team_name}: {sum(len(gamelog_cache.get((team_name, s), [])) for s in SEASONS)} games cached')

    print()
    print('  Computing rolling OPS for each game...')
    updated = 0
    skipped = 0
    for g in rows:
        game_date = g['game_date']
        season = int(game_date[:4])
        home_games = gamelog_cache.get((g['home_team'], season), [])
        away_games = gamelog_cache.get((g['away_team'], season), [])

        payload = {}
        if g.get('home_ops_last7') is None:
            o7 = compute_rolling_ops(home_games, game_date, 7)
            if o7 is not None: payload['home_ops_last7'] = round(o7, 3)
        if g.get('away_ops_last7') is None:
            o7 = compute_rolling_ops(away_games, game_date, 7)
            if o7 is not None: payload['away_ops_last7'] = round(o7, 3)
        if g.get('home_ops_last14') is None:
            o14 = compute_rolling_ops(home_games, game_date, 14)
            if o14 is not None: payload['home_ops_last14'] = round(o14, 3)
        if g.get('away_ops_last14') is None:
            o14 = compute_rolling_ops(away_games, game_date, 14)
            if o14 is not None: payload['away_ops_last14'] = round(o14, 3)

        if not payload:
            skipped += 1
            continue

        r = requests.patch(
            f'{SU}/rest/v1/mlb_game_results?id=eq.{g["id"]}',
            json=payload, headers=H_WRITE, timeout=15,
        )
        if r.status_code in (200, 204):
            updated += 1
        else:
            print(f'    [FAIL] game {g["id"]}: {r.status_code} {r.text[:100]}')
        if (updated + skipped) % 50 == 0:
            print(f'    [{updated + skipped}/{len(rows)}] {updated} updated, {skipped} skipped')

    print()
    print(f'OPS backfill complete: {updated} games updated, {skipped} skipped (no data computable)')


if __name__ == '__main__':
    main()
