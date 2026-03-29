import requests
import os
from dotenv import load_dotenv
import time
from datetime import datetime, timedelta

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
BDL_API_KEY = os.environ.get("BDL_API_KEY")

BDL_HEADERS = {'Authorization': BDL_API_KEY}

def get_team_advanced_stats(season=2024):
    try:
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/team_season_averages/general',
            headers=BDL_HEADERS,
            params={'season': season, 'season_type': 'regular', 'type': 'advanced', 'per_page': 100},
            timeout=30
        )
        data = r.json()
        print(f"Fetched {len(data.get('data', []))} teams (advanced)")
        return data.get('data', [])
    except Exception as e:
        print(f"Error fetching advanced stats: {e}")
        return []

def get_team_defense_stats(season=2024):
    try:
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/team_season_averages/general',
            headers=BDL_HEADERS,
            params={'season': season, 'season_type': 'regular', 'type': 'defense', 'per_page': 100},
            timeout=30
        )
        data = r.json()
        print(f"Fetched {len(data.get('data', []))} teams (defense)")
        return data.get('data', [])
    except Exception as e:
        print(f"Error fetching defense stats: {e}")
        return []

def get_team_tracking_stats(season=2024):
    try:
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/team_season_averages/tracking',
            headers=BDL_HEADERS,
            params={'season': season, 'season_type': 'regular', 'type': 'speeddistance', 'per_page': 100},
            timeout=30
        )
        data = r.json()
        print(f"Fetched {len(data.get('data', []))} teams (tracking)")
        return data.get('data', [])
    except Exception as e:
        print(f"Error fetching tracking stats: {e}")
        return []

def get_team_base_stats(season=2024):
    try:
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/team_season_averages/general',
            headers=BDL_HEADERS,
            params={'season': season, 'season_type': 'regular', 'type': 'base', 'per_page': 100},
            timeout=30
        )
        data = r.json()
        print(f"Fetched {len(data.get('data', []))} teams (base)")
        return data.get('data', [])
    except Exception as e:
        print(f"Error fetching base stats: {e}")
        return []

def get_team_standings(season=2024):
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

def get_player_injuries():
    """Fetch current NBA player injuries"""
    try:
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/player_injuries',
            headers=BDL_HEADERS,
            params={'per_page': 100},
            timeout=30
        )
        data = r.json()
        injuries = data.get('data', [])
        print(f"Fetched {len(injuries)} player injuries")
        return injuries
    except Exception as e:
        print(f"Error fetching injuries: {e}")
        return []

def get_last5_net_rating(season=2024):
    """Calculate last 5 games net rating by fetching recent game advanced stats"""
    try:
        # Get games from last 10 days
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')

        # Get recent games
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/games',
            headers=BDL_HEADERS,
            params={
                'seasons[]': season,
                'start_date': start_date,
                'end_date': end_date,
                'per_page': 100
            },
            timeout=30
        )
        games = r.json().get('data', [])
        final_games = [g for g in games if g.get('status') == 'Final']
        print(f"Fetched {len(final_games)} recent final games for last 5 calc")

        if not final_games:
            return {}

        # Get advanced stats for recent games
        game_ids = [g['id'] for g in final_games[:30]]
        team_net_ratings = {}

        for game_id in game_ids:
            try:
                r2 = requests.get(
                    f'https://api.balldontlie.io/nba/v1/games/advanced_stats/{game_id}',
                    headers=BDL_HEADERS,
                    timeout=15
                )
                if r2.status_code == 200:
                    adv = r2.json().get('data', {})
                    for side in ['home_team', 'visitor_team']:
                        team_data = adv.get(side, {})
                        team = team_data.get('team', {})
                        team_id = team.get('id')
                        net = team_data.get('net_rating')
                        if team_id and net is not None:
                            if team_id not in team_net_ratings:
                                team_net_ratings[team_id] = []
                            team_net_ratings[team_id].append(float(net))
                time.sleep(0.2)
            except:
                pass

        # Average last 5 games per team
        last5_map = {}
        for team_id, ratings in team_net_ratings.items():
            last5_map[team_id] = round(sum(ratings[-5:]) / len(ratings[-5:]), 1)

        print(f"Calculated last 5 net rating for {len(last5_map)} teams")
        return last5_map

    except Exception as e:
        print(f"Error calculating last 5 net rating: {e}")
        return {}

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

def upload_injuries(injuries):
    """Store current injuries in Supabase"""
    if not injuries:
        return
    try:
        # Clear existing injuries first
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/nba_injuries?id=neq.00000000-0000-0000-0000-000000000000",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Prefer": "return=minimal"
            }
        )
        # Insert new injuries
        records = []
        for inj in injuries:
            player = inj.get('player', {})
            team = player.get('team', {})
            records.append({
                "player_id": player.get('id'),
                "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
                "team_id": team.get('id') if team else None,
                "team_name": team.get('full_name') if team else None,
                "status": inj.get('status'),
                "description": inj.get('description'),
                "return_date": inj.get('return_date'),
                "updated_at": datetime.now().isoformat()
            })
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/nba_injuries",
            headers=headers,
            json=records
        )
        print(f"Uploaded {len(records)} injury records: status {r.status_code}")
    except Exception as e:
        print(f"Error uploading injuries: {e}")

def run():
    season = 2024
    season_str = '2025-26'

    # Clear existing team stats
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
    defense_data = get_team_defense_stats(season)
    time.sleep(1)
    tracking_data = get_team_tracking_stats(season)
    time.sleep(1)
    standings_data = get_team_standings(season)
    time.sleep(1)
    injuries = get_player_injuries()
    time.sleep(1)
    last5_map = get_last5_net_rating(season)

    if not adv_data:
        print("Failed to fetch advanced stats")
        return

    # Build lookup maps
    base_map = {d['team']['id']: d['stats'] for d in base_data}
    defense_map = {d['team']['id']: d['stats'] for d in defense_data}
    tracking_map = {d['team']['id']: d['stats'] for d in tracking_data}
    standings_map = {d['team']['id']: d for d in standings_data}

    # Upload injuries to separate table
    upload_injuries(injuries)

    # Build injury lookup by team for quick reference
    injury_map = {}
    for inj in injuries:
        player = inj.get('player', {})
        team = player.get('team', {})
        team_id = team.get('id') if team else None
        if team_id:
            if team_id not in injury_map:
                injury_map[team_id] = []
            status = inj.get('status', '').lower()
            if status in ['out', 'questionable', 'doubtful']:
                injury_map[team_id].append({
                    'name': f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
                    'status': inj.get('status')
                })

    success = 0
    errors = 0

    for item in adv_data:
        try:
            team = item['team']
            team_id = team['id']
            adv = item['stats']
            base = base_map.get(team_id, {})
            defense = defense_map.get(team_id, {})
            tracking = tracking_map.get(team_id, {})
            standing = standings_map.get(team_id, {})
            team_injuries = injury_map.get(team_id, [])

            # Parse home/away records
            home_record = standing.get('home_record', '0-0')
            away_record = standing.get('road_record', '0-0')
            home_wins = int(home_record.split('-')[0]) if home_record else 0
            home_losses = int(home_record.split('-')[1]) if home_record else 0
            away_wins = int(away_record.split('-')[0]) if away_record else 0
            away_losses = int(away_record.split('-')[1]) if away_record else 0

            # Injury summary string
            out_players = [p['name'] for p in team_injuries if p['status'].lower() == 'out']
            questionable_players = [p['name'] for p in team_injuries if p['status'].lower() in ['questionable', 'doubtful']]
            injury_note = ''
            if out_players:
                injury_note += f"OUT: {', '.join(out_players[:3])}"
            if questionable_players:
                if injury_note:
                    injury_note += f" | Q: {', '.join(questionable_players[:3])}"
                else:
                    injury_note += f"Q: {', '.join(questionable_players[:3])}"

            team_data = {
                "team": team['full_name'],
                "abbreviation": team['abbreviation'],
                "conference": team['conference'],
                "division": team['division'],
                # Core efficiency
                "offensive_rating": float(adv.get('off_rating', 110)),
                "defensive_rating": float(adv.get('def_rating', 110)),
                "net_rating": float(adv.get('net_rating', 0)),
                "pace": float(adv.get('pace', 98)),
                "efg_pct": float(adv.get('efg_pct', 0.52)) * 100,
                "ts_pct": float(adv.get('ts_pct', 0.56)) * 100,
                "tov_pct": float(adv.get('tm_tov_pct', 0.13)) * 100,
                "oreb_pct": float(adv.get('oreb_pct', 0.25)) * 100,
                # Defense stats
                "opp_efg_pct": float(defense.get('opp_efg_pct', 0.52)) * 100 if defense.get('opp_efg_pct') else None,
                "opp_pts_paint": float(defense.get('opp_pts_paint', 0)) if defense.get('opp_pts_paint') else None,
                "opp_pts_fb": float(defense.get('opp_pts_fb', 0)) if defense.get('opp_pts_fb') else None,
                # Tracking
                "avg_speed": float(tracking.get('avg_speed', 0)) if tracking.get('avg_speed') else None,
                "avg_speed_off": float(tracking.get('avg_speed_off', 0)) if tracking.get('avg_speed_off') else None,
                # Win/loss
                "wins": int(standing.get('wins', adv.get('w', 0))),
                "losses": int(standing.get('losses', adv.get('l', 0))),
                # Home/away splits
                "home_wins": home_wins,
                "home_losses": home_losses,
                "away_wins": away_wins,
                "away_losses": away_losses,
                "home_record": home_record,
                "away_record": away_record,
                # Last 5 net rating
                "last_10_net_rating": last5_map.get(team_id, float(adv.get('net_rating', 0))),
                # Injury note
                "injury_note": injury_note or None,
                "season": season_str,
                "updated_at": "now()"
            }

            if upload_team(team_data):
                success += 1
                inj_str = f" | 🚑 {injury_note}" if injury_note else ""
                print(f"✅ {team['full_name']} — Net: {team_data['net_rating']:+.1f}, DefRtg: {team_data['defensive_rating']:.1f}, Home: {home_record}, Away: {away_record}{inj_str}")
            else:
                errors += 1
                print(f"❌ {team['full_name']}")

        except Exception as e:
            errors += 1
            print(f"Error on {item}: {e}")

    print(f"\nDone! ✅ {success} teams, ❌ {errors} errors")

if __name__ == "__main__":
    run()