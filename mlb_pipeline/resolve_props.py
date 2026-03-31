import requests
import os
from dotenv import load_dotenv
from datetime import datetime, date, timedelta
import time

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

def get_pending_props():
    """Get all pending prop grades from yesterday or earlier"""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/prop_grades?result=eq.Pending&created_at=lt.{yesterday}T23:59:59&select=*",
        headers=HEADERS,
        timeout=30
    )
    data = r.json()
    print(f"Found {len(data)} pending props to resolve")
    return data

def get_mlb_game_result(home_team, away_team, game_date):
    """Get MLB game final score from MLB Stats API"""
    try:
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={
                'sportId': 1,
                'date': game_date,
                'hydrate': 'linescore,boxscore'
            },
            timeout=15
        )
        dates = r.json().get('dates', [])
        for d in dates:
            for game in d.get('games', []):
                if game.get('status', {}).get('abstractGameState') != 'Final':
                    continue
                gHome = game.get('teams', {}).get('home', {}).get('team', {}).get('name', '')
                gAway = game.get('teams', {}).get('away', {}).get('team', {}).get('name', '')
                if (home_team.lower() in gHome.lower() or gHome.lower() in home_team.lower()) and \
                   (away_team.lower() in gAway.lower() or gAway.lower() in away_team.lower()):
                    return game
        return None
    except Exception as e:
        print(f"Error fetching MLB game: {e}")
        return None

def get_pitcher_strikeouts(game, pitcher_name):
    """Get pitcher strikeout total from MLB boxscore"""
    try:
        game_pk = game.get('gamePk')
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore',
            timeout=15
        )
        boxscore = r.json()
        for team_side in ['home', 'away']:
            pitchers = boxscore.get('teams', {}).get(team_side, {}).get('pitchers', [])
            players = boxscore.get('teams', {}).get(team_side, {}).get('players', {})
            for pitcher_id in pitchers:
                player_key = f'ID{pitcher_id}'
                player = players.get(player_key, {})
                full_name = player.get('person', {}).get('fullName', '')
                last_name = pitcher_name.split(' ')[-1].lower()
                if last_name in full_name.lower():
                    stats = player.get('stats', {}).get('pitching', {})
                    strikeouts = stats.get('strikeOuts', None)
                    if strikeouts is not None:
                        return int(strikeouts)
        return None
    except Exception as e:
        return None

def get_batter_hits(game, batter_name):
    """Get batter hit total from MLB boxscore"""
    try:
        game_pk = game.get('gamePk')
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore',
            timeout=15
        )
        boxscore = r.json()
        for team_side in ['home', 'away']:
            batters = boxscore.get('teams', {}).get(team_side, {}).get('batters', [])
            players = boxscore.get('teams', {}).get(team_side, {}).get('players', {})
            for batter_id in batters:
                player_key = f'ID{batter_id}'
                player = players.get(player_key, {})
                full_name = player.get('person', {}).get('fullName', '')
                last_name = batter_name.split(' ')[-1].lower()
                if last_name in full_name.lower():
                    stats = player.get('stats', {}).get('batting', {})
                    hits = stats.get('hits', None)
                    if hits is not None:
                        return int(hits)
        return None
    except Exception as e:
        return None

def get_nba_player_stats(player_name, game_date):
    """Get NBA player stats from BDL for a specific date"""
    try:
        BDL_API_KEY = os.environ.get("BDL_API_KEY")
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/stats',
            headers={'Authorization': BDL_API_KEY},
            params={
                'dates[]': game_date,
                'per_page': 100
            },
            timeout=15
        )
        stats = r.json().get('data', [])
        last_name = player_name.split(' ')[-1].lower()
        for stat in stats:
            player = stat.get('player', {})
            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".lower()
            if last_name in full_name:
                return stat
        return None
    except Exception as e:
        return None

def resolve_prop(prop):
    """Resolve a single prop grade to Win/Loss"""
    sport = prop.get('sport')
    market = prop.get('market', '').upper()
    player = prop.get('player', '')
    line = float(prop.get('line', 0) or 0)
    best_side = prop.get('best_side', 'Over')
    game = prop.get('game', '')
    created_at = prop.get('created_at', '')
    game_date = created_at[:10] if created_at else None

    if not game_date:
        return None

    actual_value = None

    if sport == 'MLB':
        # Parse teams from game string "Away @ Home"
        parts = game.split(' @ ')
        if len(parts) == 2:
            away_team = parts[0].strip()
            home_team = parts[1].strip()
            mlb_game = get_mlb_game_result(home_team, away_team, game_date)
            if mlb_game:
                if 'STRIKEOUT' in market:
                    actual_value = get_pitcher_strikeouts(mlb_game, player)
                elif 'HIT' in market or 'BATTER' in market:
                    actual_value = get_batter_hits(mlb_game, player)
                time.sleep(0.3)

    elif sport == 'NBA':
        stat = get_nba_player_stats(player, game_date)
        if stat:
            if 'POINT' in market:
                actual_value = stat.get('pts')
            elif 'REBOUND' in market:
                actual_value = stat.get('reb')
            elif 'ASSIST' in market:
                actual_value = stat.get('ast')

    if actual_value is None:
        return None

    # Determine Win/Loss
    if best_side == 'Over':
        result = 'Win' if actual_value > line else 'Loss'
    else:
        result = 'Win' if actual_value < line else 'Loss'

    print(f"  {player} {market} {best_side} {line} → actual: {actual_value} → {result}")
    return result

def update_prop_result(prop_id, result):
    """Update prop grade result in Supabase"""
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/prop_grades?id=eq.{prop_id}",
        headers=HEADERS,
        json={"result": result, "resolved_at": datetime.now().isoformat()}
    )
    return r.status_code in [200, 201, 204]

def run():
    print("Starting prop result resolution...")
    pending = get_pending_props()

    if not pending:
        print("No pending props to resolve")
        return

    resolved = 0
    failed = 0
    skipped = 0

    for prop in pending:
        try:
            sport = prop.get('sport')
            if sport not in ['MLB', 'NBA']:
                skipped += 1
                continue

            result = resolve_prop(prop)
            if result:
                if update_prop_result(prop['id'], result):
                    resolved += 1
                else:
                    failed += 1
            else:
                skipped += 1

        except Exception as e:
            print(f"Error resolving {prop.get('player')}: {e}")
            failed += 1

    print(f"\nDone! ✅ {resolved} resolved, ❌ {failed} errors, ⏭ {skipped} skipped")

if __name__ == "__main__":
    run()