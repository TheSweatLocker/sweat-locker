import requests
import os
from dotenv import load_dotenv
import time

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
BDL_API_KEY = os.environ.get("BDL_API_KEY")

BDL_HEADERS = {'Authorization': BDL_API_KEY}

def get_team_advanced_stats(season=2024):
    """Fetch team advanced stats from BDL"""
    try:
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/team_season_averages/general',
            headers=BDL_HEADERS,
            params={
                'season': season,
                'season_type': 'regular',
                'type': 'advanced',
                'per_page': 100
            },
            timeout=30
        )
        data = r.json()
        print(f"Fetched {len(data.get('data', []))} teams (advanced)")
        return data.get('data', [])
    except Exception as e:
        print(f"Error fetching advanced stats: {e}")
        return []

def get_team_base_stats(season=2024):
    """Fetch team base stats from BDL"""
    try:
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/team_season_averages/general',
            headers=BDL_HEADERS,
            params={
                'season': season,
                'season_type': 'regular',
                'type': 'base',
                'per_page': 100
            },
            timeout=30
        )
        data = r.json()
        print(f"Fetched {len(data.get('data', []))} teams (base)")
        return data.get('data', [])
    except Exception as e:
        print(f"Error fetching base stats: {e}")
        return []

def get_team_standings(season=2024):
    """Fetch team standings from BDL"""
    try:
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/standings',
            headers=BDL_HEADERS,
            params={'season': season},
            timeout=30
        )
        data = r.json()
        print(f"Fetched {len(data.get('data', []))} teams (standings)")
        return data.get('data', [])
    except Exception as e:
        print(f"Error fetching standings: {e}")
        return []

def get_last10_stats(season=2024):
    """Fetch last 10 games advanced stats from BDL"""
    try:
        # BDL doesn't have last N games filter directly
        # Use last 10 games via date range approach — get recent games
        # For now use full season and note this is a Tier 2 enhancement
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/team_season_averages/general',
            headers=BDL_HEADERS,
            params={
                'season': season,
                'season_type': 'regular',
                'type': 'advanced',
                'per_page': 100
            },
            timeout=30
        )
        data = r.json()
        return data.get('data', [])
    except Exception as e:
        print(f"Error fetching last 10 stats: {e}")
        return []

def upload_team(team_data):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/nba_team_stats",
        headers=headers,
        json=team_data
    )
    if r.status_code not in [200, 201, 204]:
        print(f"Upload error {r.status_code}: {r.text[:200]}")
        return False
    return True

def run():
    season = 2024
    season_str = '2025-26'

    # Clear existing data
    requests.delete(
        f"{SUPABASE_URL}/rest/v1/nba_team_stats?id=neq.00000000-0000-0000-0000-000000000000",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Prefer": "return=minimal"
        }
    )
    print("Cleared existing NBA stats")

    # Fetch all data sources
    adv_data = get_team_advanced_stats(season)
    time.sleep(1)
    base_data = get_team_base_stats(season)
    time.sleep(1)
    standings_data = get_team_standings(season)

    if not adv_data:
        print("Failed to fetch advanced stats")
        return

    # Build lookup maps by team id
    base_map = {d['team']['id']: d['stats'] for d in base_data}
    standings_map = {d['team']['id']: d for d in standings_data}

    success = 0
    errors = 0

    for item in adv_data:
        try:
            team = item['team']
            team_id = team['id']
            adv = item['stats']
            base = base_map.get(team_id, {})
            standing = standings_map.get(team_id, {})

            # Parse home/away records from standings
            home_record = standing.get('home_record', '0-0')
            away_record = standing.get('road_record', '0-0')
            home_wins = int(home_record.split('-')[0]) if home_record else 0
            home_losses = int(home_record.split('-')[1]) if home_record else 0
            away_wins = int(away_record.split('-')[0]) if away_record else 0
            away_losses = int(away_record.split('-')[1]) if away_record else 0

            team_data = {
                "team": team['full_name'],
                "abbreviation": team['abbreviation'],
                "conference": team['conference'],
                "division": team['division'],
                # Core efficiency metrics
                "offensive_rating": float(adv.get('off_rating', 110)),
                "defensive_rating": float(adv.get('def_rating', 110)),
                "net_rating": float(adv.get('net_rating', 0)),
                "pace": float(adv.get('pace', 98)),
                "efg_pct": float(adv.get('efg_pct', 0.52)) * 100,
                "ts_pct": float(adv.get('ts_pct', 0.56)) * 100,
                "tov_pct": float(adv.get('tm_tov_pct', 0.13)) * 100,
                "oreb_pct": float(adv.get('oreb_pct', 0.25)) * 100,
                # Win/loss from standings
                "wins": int(standing.get('wins', adv.get('w', 0))),
                "losses": int(standing.get('losses', adv.get('l', 0))),
                # Home/away splits from standings
                "home_wins": home_wins,
                "home_losses": home_losses,
                "away_wins": away_wins,
                "away_losses": away_losses,
                "home_record": home_record,
                "away_record": away_record,
                # Last 10 net rating — using full season for now
                "last_10_net_rating": float(adv.get('net_rating', 0)),
                "season": season_str,
                "updated_at": "now()"
            }

            if upload_team(team_data):
                success += 1
                print(f"✅ {team['full_name']} — Net: {team_data['net_rating']:+.1f}, Pace: {team_data['pace']:.1f}, Home: {home_record}, Away: {away_record}")
            else:
                errors += 1
                print(f"❌ {team['full_name']}")

        except Exception as e:
            errors += 1
            print(f"Error on {item}: {e}")

    print(f"\nDone! ✅ {success} teams, ❌ {errors} errors")

if __name__ == "__main__":
    run()