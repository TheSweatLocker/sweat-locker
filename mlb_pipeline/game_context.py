import requests
from datetime import datetime, date, timedelta, timezone
import time
import json
from math import radians, sin, cos, sqrt, atan2

import os
from dotenv import load_dotenv
load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")

def sanitize_xera(xera, pitcher_name=''):
    """Cap xERA at 6.5 — values above this are bad early season data"""
    if xera is None:
        return None
    try:
        val = float(xera)
        if val > 6.5:
            print(f'  ⚠️ Suspicious xERA {val} for {pitcher_name} — bad early season data, treating as None')
            return None
        return round(val, 2)
    except:
        return None

def sanitize_k_pct(k_pct, pitcher_name=''):
    """Cap K% at 40 — above this is suspect early season small sample"""
    if k_pct is None:
        return None
    try:
        val = float(k_pct)
        if val > 40.0:
            print(f'  ⚠️ Suspicious K% {val} for {pitcher_name} — capping at None')
            return None
        return round(val, 2)
    except:
        return None

def get_final_score(game_id_mlb):
    """Fetch final score from MLB Stats API by game PK"""
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/game/{game_id_mlb}/linescore',
            timeout=15
        )
        data = r.json()
        home_runs = data.get('teams', {}).get('home', {}).get('runs')
        away_runs = data.get('teams', {}).get('away', {}).get('runs')
        innings = data.get('innings', [])
        game_over = len(innings) >= 9 and home_runs is not None
        return home_runs, away_runs, game_over
    except Exception as e:
        print(f'  Error fetching final score: {e}')
        return None, None, False

def get_mlb_game_pk(home_team, away_team, game_date):
    """Find MLB Stats API game PK by team names and date"""
    try:
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={
                'sportId': 1,
                'date': game_date,
                'hydrate': 'linescore'
            },
            timeout=15
        )
        dates = r.json().get('dates', [])
        for d in dates:
            for game in d.get('games', []):
                mlb_home = game.get('teams', {}).get('home', {}).get('team', {}).get('name', '')
                mlb_away = game.get('teams', {}).get('away', {}).get('team', {}).get('name', '')
                home_match = home_team.lower() in mlb_home.lower() or mlb_home.lower() in home_team.lower()
                away_match = away_team.lower() in mlb_away.lower() or mlb_away.lower() in away_team.lower()
                if home_match and away_match:
                    status = game.get('status', {}).get('abstractGameState', '')
                    if status == 'Final':
                        return game.get('gamePk'), game
        return None, None
    except Exception as e:
        print(f'  Error finding game PK: {e}')
        return None, None

def get_probable_pitchers(game_date):
    """Fetch probable pitchers from MLB Stats API"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "date": game_date,
                "hydrate": "probablePitcher"
            }
        )
        data = r.json()
        pitchers = {}
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                game_pk = str(game.get("gamePk", ""))
                home_team = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                away_team = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                home_pitcher = game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName", None)
                away_pitcher = game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName", None)
                home_pitcher_id = game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("id", None)
                away_pitcher_id = game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("id", None)
                pitchers[home_team] = {
                    "home_pitcher": home_pitcher,
                    "away_pitcher": away_pitcher,
                    "away_team": away_team,
                    "home_pitcher_id": home_pitcher_id,
                    "away_pitcher_id": away_pitcher_id
}
        print(f"Found probable pitchers for {len(pitchers)} games")
        return pitchers
    except Exception as e:
        print(f"MLB Stats API error: {e}")
        return {}

def get_pitcher_days_rest(pitcher_id, game_date):
    """Calculate days rest for a pitcher based on last appearance"""
    if not pitcher_id:
        return None
    try:
        # Look back 15 days for last start
        end_date = game_date
        start_date = (datetime.strptime(game_date, '%Y-%m-%d') - timedelta(days=15)).strftime('%Y-%m-%d')
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "startDate": start_date,
                "endDate": end_date,
                "hydrate": f"probablePitcher",
                "fields": "dates,date,games,teams,probablePitcher,id"
            }
        )
        data = r.json()
        last_start = None
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                for side in ["home", "away"]:
                    pp = game.get("teams", {}).get(side, {}).get("probablePitcher", {})
                    if pp.get("id") == pitcher_id:
                        game_date_str = date_entry.get("date")
                        if game_date_str and game_date_str < end_date:
                            if not last_start or game_date_str > last_start:
                                last_start = game_date_str
        if last_start:
            last_dt = datetime.strptime(last_start, '%Y-%m-%d')
            today_dt = datetime.strptime(end_date, '%Y-%m-%d')
            return (today_dt - last_dt).days
        return None
    except Exception as e:
        return None

def get_umpires(game_date):
    """Fetch home plate umpires from MLB Stats API"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "date": game_date,
                "hydrate": "officials"
            }
        )
        data = r.json()
        umpires = {}
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                home_team = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                officials = game.get("officials", [])
                home_plate_ump = next(
                    (o.get("official", {}).get("fullName") 
                    for o in officials 
                    if o.get("officialType") == "Home Plate"),
                    None
                )
                if home_plate_ump:
                    umpires[home_team] = home_plate_ump
        print(f"Found umpires for {len(umpires)} games")
        return umpires
    except Exception as e:
        print(f"Umpire fetch error: {e}")
        return {}

def get_umpire_stats(ump_name):
    """Look up umpire tendencies from Supabase"""
    if not ump_name:
        return None
    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_umpires?ump_name=ilike.{requests.utils.quote('*'+ump_name+'*')}&select=*&limit=1",
            headers=headers
        )
        data = r.json()
        return data[0] if data else None
    except:
        return None

def get_team_stats(team_name, season=2026):
    """Fetch team batting stats from MLB Stats API"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": season}
        )
        teams = r.json().get("teams", [])
        team = next((t for t in teams if t["name"].lower() == team_name.lower()), None)
        if not team:
            # Try partial match
            team = next((t for t in teams if team_name.lower() in t["name"].lower() or t["name"].lower() in team_name.lower()), None)
        if not team:
            return None
        
        team_id = team["id"]
        
        # Get team batting stats
        r2 = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats",
            params={"stats": "season", "group": "hitting", "season": season}
        )
        stats = r2.json().get("stats", [])
        if not stats or not stats[0].get("splits"):
            return None
            
        batting = stats[0]["splits"][0]["stat"]
        return {
            "runs_per_game": float(batting.get("runs", 0)) / max(float(batting.get("gamesPlayed", 1)), 1),
            "avg": float(batting.get("avg", 0.250)),
            "obp": float(batting.get("obp", 0.320)),
            "slg": float(batting.get("slg", 0.400)),
            "ops": float(batting.get("ops", 0.720)),
            "home_runs": int(batting.get("homeRuns", 0)),
            "games_played": int(batting.get("gamesPlayed", 0)),
        }
    except Exception as e:
        return None

def get_team_splits(team_name, season=2026):
    """Fetch home/away splits from MLB Stats API"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": season}
        )
        teams = r.json().get("teams", [])
        team = next((t for t in teams if t["name"].lower() == team_name.lower()), None)
        if not team:
            team = next((t for t in teams if team_name.lower() in t["name"].lower() or t["name"].lower() in team_name.lower()), None)
        if not team:
            return None

        team_id = team["id"]

        r2 = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats",
            params={
                "stats": "homeAndAway",
                "group": "hitting",
                "season": season
            }
        )
        splits = r2.json().get("stats", [])
        if not splits:
            return None

        home_stats = None
        away_stats = None

        for split_group in splits:
            for split in split_group.get("splits", []):
                split_type = split.get("split", {}).get("code", "")
                stat = split.get("stat", {})
                games = int(stat.get("gamesPlayed", 1))
                if games == 0:
                    continue
                runs_per_game = float(stat.get("runs", 0)) / games
                ops = float(stat.get("ops", 0.720))
                if split_type == "H":
                    home_stats = {"runs_per_game": runs_per_game, "ops": ops, "games": games}
                elif split_type == "A":
                    away_stats = {"runs_per_game": runs_per_game, "ops": ops, "games": games}

        return {"home": home_stats, "away": away_stats}
    except Exception as e:
        return None

def get_team_strikeout_rate(team_name, season=2026):
    """Fetch team strikeout rate from MLB Stats API"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": season}
        )
        teams = r.json().get("teams", [])
        team = next((t for t in teams if t["name"].lower() == team_name.lower()), None)
        if not team:
            team = next((t for t in teams if team_name.lower() in t["name"].lower() or t["name"].lower() in team_name.lower()), None)
        if not team:
            return None

        team_id = team["id"]
        r2 = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats",
            params={"stats": "season", "group": "hitting", "season": season}
        )
        stats = r2.json().get("stats", [])
        if not stats or not stats[0].get("splits"):
            return None

        batting = stats[0]["splits"][0]["stat"]
        ab = float(batting.get("atBats", 0))
        so = float(batting.get("strikeOuts", 0))
        games = float(batting.get("gamesPlayed", 1))
        if ab == 0 or games < 5:
            return None
        k_rate = (so / ab) * 100
        return round(k_rate, 1)
    except Exception as e:
        return None

def get_team_last10(team_name, season=2026):
    """Fetch team last 10 games record from MLB Stats API standings"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/standings",
            params={
                "leagueId": "103,104",
                "season": season,
                "standingsTypes": "regularSeason",
                "hydrate": "team,streak,division,sport,league,record(overallRecords)"
            }
        )
        data = r.json()
        for record in data.get("records", []):
            for team_record in record.get("teamRecords", []):
                name = team_record.get("team", {}).get("name", "")
                if name.lower() == team_name.lower() or team_name.lower() in name.lower() or name.lower() in team_name.lower():
                    # Get last 10
                    overall = team_record.get("records", {}).get("overallRecords", [])
                    last10 = next((r for r in overall if r.get("type") == "lastTen"), None)
                    streak = team_record.get("streak", {}).get("streakCode", "")
                    wins = team_record.get("wins", 0)
                    losses = team_record.get("losses", 0)
                    return {
                        "wins": wins,
                        "losses": losses,
                        "last10": f"{last10.get('wins', 0)}-{last10.get('losses', 0)}" if last10 else None,
                        "streak": streak
                    }
        return None
    except Exception as e:
        return None

def get_confirmed_lineups(game_date):
    """Fetch confirmed batting lineups from MLB Stats API"""
    print(f"Fetching confirmed lineups for {game_date}...")
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "date": game_date,
                "hydrate": "lineups"
            }
        )
        data = r.json()
        lineups = {}
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                home_team = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                away_team = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                home_lineup = game.get("lineups", {}).get("homePlayers", [])
                away_lineup = game.get("lineups", {}).get("awayPlayers", [])
                home_batters = [p.get("fullName", "") for p in home_lineup if p.get("primaryPosition", {}).get("abbreviation") != "P"]
                away_batters = [p.get("fullName", "") for p in away_lineup if p.get("primaryPosition", {}).get("abbreviation") != "P"]
                if home_batters or away_batters:
                    lineups[home_team] = {
                        "home_lineup": home_batters[:9],
                        "away_lineup": away_batters[:9],
                        "away_team": away_team,
                        "lineup_confirmed": True
                    }
                    print(f"  ✅ Lineup confirmed: {away_team} @ {home_team}")
                else:
                    lineups[home_team] = {
                        "home_lineup": [],
                        "away_lineup": [],
                        "away_team": away_team,
                        "lineup_confirmed": False
                    }
        return lineups
    except Exception as e:
        print(f"Lineup fetch error: {e}")
        return {}

def get_batter_handedness(player_name, season=2026):
    """Look up batter hitting hand from MLB Stats API"""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": player_name, "sportId": 1},
            timeout=10
        )
        data = r.json()
        people = data.get("people", [])
        if not people:
            return None
        person = people[0]
        bat_side = person.get("batSide", {}).get("code", None)
        return bat_side  # 'L', 'R', or 'S' (switch)
    except Exception as e:
        return None

def calc_platoon_advantage(lineup_names, pitcher_hand):
    """
    Calculate platoon advantage score for a lineup vs a pitcher.
    Returns (score, note) where score > 0 = lineup advantage, < 0 = pitcher advantage
    """
    if not lineup_names or not pitcher_hand:
        return None, None

    batters = [b.strip() for b in lineup_names.split(',') if b.strip()]
    if not batters:
        return None, None

    handedness = []
    for name in batters[:9]:  # top 9 only
        hand = get_batter_handedness(name)
        if hand:
            handedness.append(hand)
        time.sleep(0.1)  # rate limit

    if not handedness:
        return None, None

    total = len(handedness)
    # Opposite hand batters have platoon advantage
    if pitcher_hand == 'R':
        # LHB and switch hitters have advantage vs RHP
        advantage_batters = [h for h in handedness if h in ['L', 'S']]
        disadvantage_batters = [h for h in handedness if h == 'R']
    elif pitcher_hand == 'L':
        # RHB and switch hitters have advantage vs LHP
        advantage_batters = [h for h in handedness if h in ['R', 'S']]
        disadvantage_batters = [h for h in handedness if h == 'L']
    else:
        return None, None

    adv_count = len(advantage_batters)
    dis_count = len(disadvantage_batters)

    # Score: positive = lineup has platoon advantage, negative = pitcher has platoon advantage
    # Scale: each batter with platoon advantage = +1, disadvantage = -1
    score = round((adv_count - dis_count) / total * 10, 1)

    l_count = handedness.count('L')
    r_count = handedness.count('R')
    s_count = handedness.count('S')

    note = f"{l_count}L/{r_count}R/{s_count}S vs {pitcher_hand}HP — "
    if score >= 3:
        note += f"lineup has strong platoon advantage (+{score})"
    elif score >= 1:
        note += f"lineup has slight platoon advantage (+{score})"
    elif score <= -3:
        note += f"pitcher has strong platoon advantage ({score})"
    elif score <= -1:
        note += f"pitcher has slight platoon advantage ({score})"
    else:
        note += "neutral platoon matchup"

    return score, note

def get_pitcher_splits(pitcher_id, season=2026):
    """Fetch pitcher home/away splits from MLB Stats API"""
    if not pitcher_id:
        return None
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
            params={
                "stats": "homeAndAway",
                "group": "pitching",
                "season": season
            }
        )
        data = r.json()
        stats = data.get("stats", [])
        if not stats:
            return None
        home_era = None
        away_era = None
        for split_group in stats:
            for split in split_group.get("splits", []):
                split_type = split.get("split", {}).get("code", "")
                era = float(split.get("stat", {}).get("era", 0))
                if split_type == "H":
                    home_era = era
                elif split_type == "A":
                    away_era = era
        return {"home_era": home_era, "away_era": away_era}
    except Exception as e:
        return None

def get_pitcher_stats(pitcher_name):
    """Look up pitcher stats from Supabase"""
    if not pitcher_name:
        return None
    try:
        import unicodedata
        # Normalize accent characters so López matches Lopez etc
        normalized_name = unicodedata.normalize('NFKD', pitcher_name).encode('ascii', 'ignore').decode('ascii')
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        # Try normalized name first
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=ilike.{requests.utils.quote('*'+normalized_name+'*')}&select=*&limit=1",
            headers=headers
        )
        data = r.json()
        if data:
            return data[0]
        # Fall back to original name
        r2 = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=ilike.{requests.utils.quote('*'+pitcher_name+'*')}&select=*&limit=1",
            headers=headers
        )
        data2 = r2.json()
        return data2[0] if data2 else None
    except:
        return None

# Venue coordinates for weather lookup
VENUE_COORDS = {
    "Coors Field": (39.7559, -104.9942),
    "Great American Ball Park": (39.0979, -84.5082),
    "Citizens Bank Park": (39.9061, -75.1665),
    "Fenway Park": (42.3467, -71.0972),
    "Minute Maid Park": (29.7572, -95.3555),
    "Wrigley Field": (41.9484, -87.6553),
    "Globe Life Field": (32.7473, -97.0822),
    "Camden Yards": (39.2838, -76.6217),
    "Rogers Centre": (43.6414, -79.3894),
    "Truist Park": (33.8908, -84.4678),
    "Angel Stadium": (33.8003, -117.8827),
    "Target Field": (44.9817, -93.2781),
    "Progressive Field": (41.4962, -81.6852),
    "Kauffman Stadium": (39.0517, -94.4803),
    "T-Mobile Park": (47.5914, -122.3325),
    "Tropicana Field": (27.7683, -82.6534),
    "Guaranteed Rate Field": (41.8300, -87.6339),
    "loanDepot Park": (25.7781, -80.2197),
    "PNC Park": (40.4469, -80.0057),
    "Oracle Park": (37.7786, -122.3893),
    "Sutter Health Park": (38.5803, -121.5014),
    "Comerica Park": (42.3390, -83.0485),
    "Dodger Stadium": (34.0739, -118.2400),
    "Petco Park": (32.7076, -117.1570),
    "Citi Field": (40.7571, -73.8458),
    "Yankee Stadium": (40.8296, -73.9262),
    "Busch Stadium": (38.6226, -90.1928),
    "American Family Field": (43.0280, -87.9712),
    "Chase Field": (33.4453, -112.0667),
    "Nationals Park": (38.8730, -77.0074),
}

# Team to venue mapping
TEAM_VENUE = {
    "Colorado Rockies": "Coors Field",
    "Cincinnati Reds": "Great American Ball Park",
    "Philadelphia Phillies": "Citizens Bank Park",
    "Boston Red Sox": "Fenway Park",
    "Houston Astros": "Minute Maid Park",
    "Chicago Cubs": "Wrigley Field",
    "Texas Rangers": "Globe Life Field",
    "Baltimore Orioles": "Camden Yards",
    "Toronto Blue Jays": "Rogers Centre",
    "Atlanta Braves": "Truist Park",
    "Los Angeles Angels": "Angel Stadium",
    "Minnesota Twins": "Target Field",
    "Cleveland Guardians": "Progressive Field",
    "Kansas City Royals": "Kauffman Stadium",
    "Seattle Mariners": "T-Mobile Park",
    "Tampa Bay Rays": "Tropicana Field",
    "Chicago White Sox": "Guaranteed Rate Field",
    "Miami Marlins": "loanDepot Park",
    "Pittsburgh Pirates": "PNC Park",
    "San Francisco Giants": "Oracle Park",
    "Oakland Athletics": "Sutter Health Park",
    "Athletics": "Sutter Health Park",
    "Detroit Tigers": "Comerica Park",
    "Los Angeles Dodgers": "Dodger Stadium",
    "San Diego Padres": "Petco Park",
    "New York Mets": "Citi Field",
    "New York Yankees": "Yankee Stadium",
    "St. Louis Cardinals": "Busch Stadium",
    "Milwaukee Brewers": "American Family Field",
    "Arizona Diamondbacks": "Chase Field",
    "Washington Nationals": "Nationals Park",
}

# Dome stadiums — weather irrelevant
DOME_VENUES = ["Tropicana Field", "loanDepot Park", "Rogers Centre", "American Family Field", "Chase Field", "Globe Life Field"]

TEAM_TIMEZONES = {
    'New York Yankees': 'ET', 'New York Mets': 'ET', 'Boston Red Sox': 'ET',
    'Baltimore Orioles': 'ET', 'Tampa Bay Rays': 'ET', 'Toronto Blue Jays': 'ET',
    'Philadelphia Phillies': 'ET', 'Atlanta Braves': 'ET', 'Miami Marlins': 'ET',
    'Washington Nationals': 'ET', 'Pittsburgh Pirates': 'ET', 'Cleveland Guardians': 'ET',
    'Detroit Tigers': 'ET', 'Cincinnati Reds': 'ET',
    'Chicago Cubs': 'CT', 'Chicago White Sox': 'CT',
    'Milwaukee Brewers': 'CT', 'Minnesota Twins': 'CT', 'Kansas City Royals': 'CT',
    'St. Louis Cardinals': 'CT', 'Houston Astros': 'CT', 'Texas Rangers': 'CT',
    'Colorado Rockies': 'MT', 'Arizona Diamondbacks': 'MT',
    'Los Angeles Dodgers': 'PT', 'Los Angeles Angels': 'PT', 'Athletics': 'PT',
    'San Francisco Giants': 'PT', 'San Diego Padres': 'PT',
    'Seattle Mariners': 'PT', 'Oakland Athletics': 'PT',
}
TZ_OFFSET = {'ET': 0, 'CT': 1, 'MT': 2, 'PT': 3}

def haversine(coord1, coord2):
    """Calculate distance in miles between two lat/lon coordinates"""
    R = 3958.8  # Earth radius in miles
    lat1, lon1 = radians(coord1[0]), radians(coord1[1])
    lat2, lon2 = radians(coord2[0]), radians(coord2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def get_team_schedule_features(team_name, game_date):
    """Fetch last 5 games for a team and calculate run diff, consecutive road games, days since last home game"""
    try:
        # Get MLB team ID
        teams_resp = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=15)
        team_last = team_name.split(' ')[-1].lower()
        mlb_team = None
        for t in teams_resp.json().get('teams', []):
            if t.get('name', '').lower().endswith(team_last) or team_last in t.get('name', '').lower():
                mlb_team = t
                break
        if not mlb_team:
            return None, None, None, None

        team_id = mlb_team['id']
        end_date = game_date
        start_date = (datetime.strptime(game_date, '%Y-%m-%d') - timedelta(days=30)).strftime('%Y-%m-%d')

        sched_resp = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={'teamId': team_id, 'sportId': 1, 'startDate': start_date, 'endDate': end_date, 'hydrate': 'linescore', 'gameType': 'R'},
            timeout=15
        )

        all_games = []
        for d in sched_resp.json().get('dates', []):
            for g in d.get('games', []):
                if g.get('status', {}).get('detailedState') == 'Final':
                    all_games.append(g)

        # Sort by date descending
        all_games.sort(key=lambda g: g.get('gameDate', ''), reverse=True)

        # Last 5 run differential
        run_diff = 0
        for g in all_games[:5]:
            ls = g.get('linescore', {})
            home_runs = ls.get('teams', {}).get('home', {}).get('runs', 0) or 0
            away_runs = ls.get('teams', {}).get('away', {}).get('runs', 0) or 0
            is_home = g.get('teams', {}).get('home', {}).get('team', {}).get('id') == team_id
            if is_home:
                run_diff += (home_runs - away_runs)
            else:
                run_diff += (away_runs - home_runs)

        last5_run_diff = round(run_diff, 1) if len(all_games) >= 1 else None

        # Days since last home game
        days_since_home = None
        for g in all_games:
            is_home = g.get('teams', {}).get('home', {}).get('team', {}).get('id') == team_id
            if is_home:
                last_home_date = g.get('gameDate', '')[:10]
                try:
                    days_since_home = (datetime.strptime(game_date, '%Y-%m-%d') - datetime.strptime(last_home_date, '%Y-%m-%d')).days
                except:
                    pass
                break

        # Consecutive road games (counting backwards)
        consec_road = 0
        for g in all_games:
            is_home = g.get('teams', {}).get('home', {}).get('team', {}).get('id') == team_id
            if is_home:
                break
            consec_road += 1

        # Last game venue name (for travel distance)
        last_venue = None
        if all_games:
            last_venue = all_games[0].get('venue', {}).get('name')

        return last5_run_diff, days_since_home, consec_road, last_venue
    except Exception as e:
        print(f"  ⚠️ Schedule features error for {team_name}: {e}")
        return None, None, None, None

def get_weather(venue, lat, lon):
    if venue in DOME_VENUES:
        return {"temperature": 72, "wind_speed": 0, "wind_direction": "N/A", "precipitation": 0, "is_dome": True}
    try:
        r = requests.get(
            f"https://api.openweathermap.org/data/2.5/weather",
            params={"lat": lat, "lon": lon, "appid": WEATHER_API_KEY, "units": "imperial"}
        )
        data = r.json()
        main_data = data.get("main", {})
        if not main_data:
            print(f"  Weather API no 'main' data for {venue}: {data.get('message', 'unknown')}")
            return {"temperature": 70, "wind_speed": 5, "wind_direction": "N", "precipitation": 0, "is_dome": False}
        wind_data = data.get("wind", {})
        wind_deg = wind_data.get("deg", 0)
        wind_speed = round(wind_data.get("speed", 0))
        directions = ["N","NE","E","SE","S","SW","W","NW"]
        wind_dir = directions[round(wind_deg/45) % 8]
        return {
            "temperature": round(main_data.get("temp", 70)),
            "wind_speed": wind_speed,
            "wind_direction": wind_dir,
            "precipitation": data.get("rain", {}).get("1h", 0),
            "is_dome": False
        }
    except Exception as e:
        print(f"Weather error for {venue}: {e}")
        return {"temperature": 70, "wind_speed": 5, "wind_direction": "N", "precipitation": 0, "is_dome": False}
def get_mlb_games():
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "spreads,totals,totals_1st_5_innings,h2h",
                "oddsFormat": "american",
                "bookmakers": "draftkings",
                # Anchor to ET date — midnight ET = 04:00 UTC, end at 3:59am next day UTC = 11:59pm ET
                "commenceTimeFrom": f"{(datetime.now(timezone.utc) - timedelta(hours=5)).strftime('%Y-%m-%d')}T04:00:00Z",
                "commenceTimeTo": f"{(datetime.now(timezone.utc) - timedelta(hours=5) + timedelta(days=1)).strftime('%Y-%m-%d')}T03:59:59Z",
            }
        )
        return r.json()
    except Exception as e:
        print(f"Odds API error: {e}")
        return []
def get_pitcher_last_outing(pitcher_id):
    """Fetch pitcher's last game pitch count and innings from MLB Stats API"""
    if not pitcher_id:
        return None
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
            params={"stats": "gameLog", "group": "pitching", "season": 2026},
            timeout=10
        )
        splits = r.json().get("stats", [])
        if not splits or not splits[0].get("splits"):
            return None
        # Most recent game is first in the list
        last = splits[0]["splits"][0]["stat"]
        return {
            "pitches": int(last.get("numberOfPitches", 0) or 0),
            "innings": float(last.get("inningsPitched", "0").replace('.1','.33').replace('.2','.67') or "0"),
            "earned_runs": int(last.get("earnedRuns", 0) or 0),
        }
    except:
        return None

def get_pitcher_vs_team(pitcher_id, opponent_team_id):
    """Fetch pitcher's career stats vs a specific team"""
    if not pitcher_id or not opponent_team_id:
        return None
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
            params={"stats": "vsTeam", "group": "pitching", "season": 2026, "opposingTeamId": opponent_team_id},
            timeout=10
        )
        splits = r.json().get("stats", [])
        if not splits or not splits[0].get("splits"):
            # Try career stats
            r2 = requests.get(
                f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
                params={"stats": "vsTeamTotal", "group": "pitching", "opposingTeamId": opponent_team_id},
                timeout=10
            )
            splits = r2.json().get("stats", [])
            if not splits or not splits[0].get("splits"):
                return None
        s = splits[0]["splits"][0]["stat"]
        ip = float(s.get("inningsPitched", "0").replace('.1','.33').replace('.2','.67') or "0")
        if ip < 3:
            return None  # too small sample
        return {
            "era_vs_team": float(s.get("era", "0") or "0"),
            "avg_vs_team": float(s.get("avg", "0") or "0"),
            "ip_vs_team": round(ip, 1),
            "k_vs_team": int(s.get("strikeOuts", 0) or 0),
        }
    except:
        return None

def get_bullpen_usage(team_name, game_date):
    """Check how many bullpen arms were used in the last 3 days via boxscore"""
    if not team_name:
        return None
    try:
        teams_resp = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=10)
        team_last = team_name.split(' ')[-1].lower()
        mlb_team = next((t for t in teams_resp.json().get('teams', [])
            if t.get('name', '').lower().endswith(team_last) or team_last in t.get('name', '').lower()), None)
        if not mlb_team:
            return None

        team_id = mlb_team['id']
        end = game_date
        start = (datetime.strptime(game_date, '%Y-%m-%d') - timedelta(days=3)).strftime('%Y-%m-%d')

        # Get recent games
        sched = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={'teamId': team_id, 'sportId': 1, 'startDate': start, 'endDate': end, 'gameType': 'R'},
            timeout=10
        )
        total_relievers = 0
        games_played = 0
        for d in sched.json().get('dates', []):
            for g in d.get('games', []):
                if g.get('status', {}).get('detailedState') != 'Final':
                    continue
                game_pk = g.get('gamePk')
                if not game_pk:
                    continue
                # Fetch boxscore to count pitchers used
                try:
                    box = requests.get(
                        f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore',
                        timeout=10
                    )
                    box_data = box.json()
                    # Determine if team is home or away
                    is_home = g.get('teams', {}).get('home', {}).get('team', {}).get('id') == team_id
                    side = 'home' if is_home else 'away'
                    team_box = box_data.get('teams', {}).get(side, {})
                    pitcher_ids = team_box.get('pitchers', [])
                    if len(pitcher_ids) > 1:
                        total_relievers += len(pitcher_ids) - 1  # subtract starter
                    games_played += 1
                except:
                    games_played += 1
        return {
            "relievers_used_3d": total_relievers,
            "games_last_3d": games_played,
            "avg_relievers": round(total_relievers / max(games_played, 1), 1),
        }
    except:
        return None

def get_bullpen_stats(team_name):
    """Look up bullpen ERA from Supabase"""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_bullpen_stats?team=eq.{requests.utils.quote(team_name)}&select=*&limit=1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
        )
        data = r.json()
        return data[0] if data else None
    except:
        return None

def get_pitcher_first_inning(pitcher_name):
    """Fetch pitcher's first inning splits from mlb_pitcher_stats"""
    if not pitcher_name:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=ilike.*{requests.utils.quote(pitcher_name.split(' ')[-1])}*&select=first_inning_era,first_inning_whip,first_inning_avg,first_inning_ip&limit=1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
        )
        data = r.json()
        if data and len(data) > 0 and data[0].get('first_inning_era') is not None:
            return data[0]
        return None
    except:
        return None

def get_team_woba_wrc(team_name):
    """Look up team wOBA and wRC+ from Supabase mlb_team_offense table"""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_team_offense?team=eq.{requests.utils.quote(team_name)}&select=*&limit=1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
        )
        data = r.json()
        return data[0] if data else None
    except:
        return None

def calc_batting_order_weight(lineup_names):
    """
    Weight lineup quality by batting order position.
    Top 3 = high leverage, 4-6 = medium, 7-9 = low.
    Returns a score 0-10 representing top-of-order strength.
    """
    if not lineup_names:
        return None
    batters = [b.strip() for b in lineup_names.split(',') if b.strip()]
    if len(batters) < 3:
        return None
    # Weights by position: 1-3 face starter first inning
    weights = [1.0, 0.95, 0.90, 0.70, 0.65, 0.60, 0.45, 0.40, 0.35]
    # Score is just a weighted count — higher = more lineup depth
    score = sum(weights[i] for i in range(min(len(batters), 9)))
    return round(score, 2)

def detect_opener(pitcher_id):
    """Check if pitcher is likely an opener/bullpen day — low games started vs games played"""
    if not pitcher_id:
        return False
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
            params={"stats": "season", "group": "pitching", "season": 2026},
            timeout=10
        )
        splits = r.json().get("stats", [])
        if not splits or not splits[0].get("splits"):
            return False
        s = splits[0]["splits"][0]["stat"]
        gs = int(s.get("gamesStarted", 0) or 0)
        gp = int(s.get("gamesPlayed", 0) or 0)
        ip = float(s.get("inningsPitched", "0").replace('.1','.33').replace('.2','.67') or "0")
        # Opener signals: mostly relief appearances, or very low IP per start
        if gp > 0 and gs == 0:
            return True  # pure reliever being used as opener
        if gs > 0 and ip / gs < 3.5:
            return True  # averaging less than 3.5 IP per start — likely short opener
        return False
    except:
        return False

def calc_nrfi_score(home_pitcher_stats, away_pitcher_stats, home_days_rest, away_days_rest, temperature, wind_speed, wind_direction, park_run_factor, home_wrc_plus, away_wrc_plus, home_first_inn=None, away_first_inn=None, home_is_opener=False, away_is_opener=False):
    """
    Calculate NRFI (No Run First Inning) probability score 0-100.
    Higher = stronger NRFI lean.
    """
    score = 50  # neutral baseline

    # ── HOME PITCHER ──
    if home_pitcher_stats:
        raw_xera = sanitize_xera(home_pitcher_stats.get('xera'), 'home')
        xera = float(raw_xera if raw_xera is not None else 4.5)
        gb_pct = float(home_pitcher_stats.get('gb_pct', 0.42) or 0.42)
        raw_k = home_pitcher_stats.get('k_pct', 0.20) or 0.20
        k_pct = float(raw_k) if float(raw_k) <= 0.40 else 0.20  # cap at 40% — above is suspect
        whiff = float(home_pitcher_stats.get('whiff_rate', 0.25) or 0.25)

        # xERA signal — elite starter = strong NRFI lean
        if xera <= 3.00: score += 12
        elif xera <= 3.50: score += 8
        elif xera <= 4.00: score += 4
        elif xera >= 5.00: score -= 6
        elif xera >= 4.50: score -= 3

        # GB% — ground ball pitchers limit first inning damage
        if gb_pct >= 0.50: score += 6
        elif gb_pct >= 0.45: score += 3
        elif gb_pct <= 0.35: score -= 4

        # K% — high strikeout pitchers limit traffic
        if k_pct >= 0.28: score += 6
        elif k_pct >= 0.23: score += 3
        elif k_pct <= 0.15: score -= 4

    # ── AWAY PITCHER ──
    if away_pitcher_stats:
        raw_xera = sanitize_xera(away_pitcher_stats.get('xera'), 'away')
        xera = float(raw_xera if raw_xera is not None else 4.5)
        gb_pct = float(away_pitcher_stats.get('gb_pct', 0.42) or 0.42)
        raw_k = away_pitcher_stats.get('k_pct', 0.20) or 0.20
        k_pct = float(raw_k) if float(raw_k) <= 0.40 else 0.20  # cap at 40% — above is suspect

        if xera <= 3.00: score += 12
        elif xera <= 3.50: score += 8
        elif xera <= 4.00: score += 4
        elif xera >= 5.00: score -= 6
        elif xera >= 4.50: score -= 3

        if gb_pct >= 0.50: score += 6
        elif gb_pct >= 0.45: score += 3
        elif gb_pct <= 0.35: score -= 4

        if k_pct >= 0.28: score += 6
        elif k_pct >= 0.23: score += 3
        elif k_pct <= 0.15: score -= 4

    # ── DAYS REST ──
    # Fresh pitchers = best stuff in inning 1
    home_rest = int(home_days_rest or 4)
    away_rest = int(away_days_rest or 4)
    if home_rest >= 5 and away_rest >= 5: score += 6
    elif home_rest >= 5 or away_rest >= 5: score += 3
    elif home_rest <= 3 or away_rest <= 3: score -= 4

    # ── WEATHER ──
    temp = float(temperature or 72)
    wind = float(wind_speed or 0)
    wind_dir = (wind_direction or '').upper()

    # Temperature — calibrated from 140+ game outcomes:
    # 45 or below: 79.2% NRFI (strongest signal in dataset)
    # 46-55: 43.5% NRFI (WORSE than base rate — penalty)
    # 56-70: 41.2% NRFI (worst range — penalty)
    # 71+: 59.3% NRFI (above base rate — slight bonus)
    if temp <= 45: score += 15      # cold = dominant NRFI signal
    elif temp <= 55: score -= 4     # cool but not cold = danger zone
    elif temp <= 70: score -= 3     # mild = slight penalty
    elif temp >= 85: score -= 2     # hot = slight offense boost
    else: score += 2                # 71-84 = above base rate

    if wind >= 12:
        if any(d in wind_dir for d in ['N', 'IN', 'NW', 'NE']): score += 5  # blowing in
        elif any(d in wind_dir for d in ['S', 'OUT', 'SW', 'SE']): score -= 5  # blowing out

    # ── PARK FACTOR ──
    park = float(park_run_factor or 100)
    if park <= 93: score += 6    # extreme pitcher park
    elif park <= 97: score += 3
    elif park >= 110: score -= 6  # extreme hitter park
    elif park >= 105: score -= 3

    # ── OFFENSIVE QUALITY ──
    home_wrc = float(home_wrc_plus or 100)
    away_wrc = float(away_wrc_plus or 100)
    avg_wrc = (home_wrc + away_wrc) / 2
    if avg_wrc >= 115: score -= 8   # both elite offenses
    elif avg_wrc >= 108: score -= 4
    elif avg_wrc <= 88: score += 6  # both weak offenses
    elif avg_wrc <= 95: score += 3

    # ── FIRST INNING SPLITS ──
    # Pitcher's actual 1st inning ERA is the most direct NRFI signal
    if home_first_inn and home_first_inn.get('first_inning_era') is not None:
        fi_era = float(home_first_inn['first_inning_era'])
        if fi_era <= 1.50: score += 8     # elite 1st inning pitcher
        elif fi_era <= 3.00: score += 4
        elif fi_era >= 6.00: score -= 6   # gets hit early
        elif fi_era >= 4.50: score -= 3
        # WHIP reinforces — low traffic = no runs
        fi_whip = float(home_first_inn.get('first_inning_whip', 1.3) or 1.3)
        if fi_whip <= 0.90: score += 3
        elif fi_whip >= 1.60: score -= 3

    if away_first_inn and away_first_inn.get('first_inning_era') is not None:
        fi_era = float(away_first_inn['first_inning_era'])
        if fi_era <= 1.50: score += 8
        elif fi_era <= 3.00: score += 4
        elif fi_era >= 6.00: score -= 6
        elif fi_era >= 4.50: score -= 3
        fi_whip = float(away_first_inn.get('first_inning_whip', 1.3) or 1.3)
        if fi_whip <= 0.90: score += 3
        elif fi_whip >= 1.60: score -= 3

    # ── HOME ACE BONUS ──
    # If home pitcher xERA is 1.5+ better than away pitcher, boost NRFI
    if home_pitcher_stats and away_pitcher_stats:
        home_xera = sanitize_xera(home_pitcher_stats.get('xera'), 'home')
        away_xera = sanitize_xera(away_pitcher_stats.get('xera'), 'away')
        if home_xera and away_xera:
            xera_gap = abs(float(home_xera) - float(away_xera))
            worse_xera = max(float(home_xera), float(away_xera))
            better_xera = min(float(home_xera), float(away_xera))

            # Ace bonus — both arms elite or one dominant
            if (float(away_xera) - float(home_xera)) >= 1.5:
                score += 5

            # ── DUAL ARM GATE ──
            # Single liability arm penalty — one bad pitcher kills NRFI confidence
            # If one arm is good (<3.5) but other is bad (>4.5), penalize
            if better_xera <= 3.5 and worse_xera >= 4.5:
                penalty = min(12, round((worse_xera - 4.0) * 3))
                score -= penalty
            # Both arms bad — heavy penalty
            elif better_xera >= 4.5 and worse_xera >= 4.5:
                score -= 10
            # Both arms elite — bonus
            elif better_xera <= 3.5 and worse_xera <= 3.5:
                score += 5

    # ── OPENER / BULLPEN GAME DETECTION ──
    # Relievers used as openers are volatile — unpredictable first inning
    if home_is_opener:
        score -= 8
    if away_is_opener:
        score -= 8

    return max(0, min(100, round(score)))

def get_park_factors(home_team):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_park_factors?team=eq.{requests.utils.quote(home_team)}&select=*",
            headers=headers
        )
        data = r.json()
        return data[0] if data else None
    except:
        return None

def upload_game_context(context):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?on_conflict=game_id",
        headers=headers,
        json=context
    )
    if r.status_code not in [200, 201, 204]:
        print(f"Upload failed {r.status_code}: {r.text}")
    return r.status_code in [200, 201, 204]

def log_game_result(context):
    """Log pre-game data to mlb_game_results for XGBoost training"""
    try:
        record = {
            "game_id": context.get("game_id"),
            "game_date": context.get("game_date"),
            "season": 2026,
            "home_team": context.get("home_team"),
            "away_team": context.get("away_team"),
            "venue": context.get("venue"),
            "dome_game": context.get("venue") in DOME_VENUES,
            "home_sp_name": context.get("home_pitcher"),
            "home_sp_hand": context.get("home_throws"),
            "home_sp_xera": context.get("home_sp_xera"),
            "home_sp_k_pct": sanitize_k_pct(float(context["pitcher_context"].split("K% ")[1].split("%")[0]), context.get("home_pitcher", '')) if context.get("pitcher_context") and "K% " in context.get("pitcher_context", "") else None,
            "home_sp_whiff_rate": float(context["pitcher_context"].split("whiff ")[1].split("%")[0]) if context.get("pitcher_context") and "whiff " in context.get("pitcher_context", "") else None,
            "home_sp_gb_pct": float(context["pitcher_context"].split("GB% ")[1].split("%")[0]) if context.get("pitcher_context") and "GB% " in context.get("pitcher_context", "") else None,
            "home_sp_days_rest": context.get("home_days_rest"),
            "away_sp_name": context.get("away_pitcher"),
            "away_sp_hand": context.get("away_throws"),
            "away_sp_xera": context.get("away_sp_xera"),
            "away_sp_k_pct": None,
            "away_sp_whiff_rate": None,
            "away_sp_gb_pct": None,
            "away_sp_days_rest": context.get("away_days_rest"),
            "home_runs_per_game": context.get("home_runs_per_game"),
            "away_runs_per_game": context.get("away_runs_per_game"),
            "home_ops": context.get("home_ops"),
            "away_ops": context.get("away_ops"),
            "home_team_k_pct": context.get("home_team_k_pct"),
            "away_team_k_pct": context.get("away_team_k_pct"),
            "home_k_gap": context.get("home_k_gap"),
            "away_k_gap": context.get("away_k_gap"),
            "home_woba": context.get("home_woba"),
            "away_woba": context.get("away_woba"),
            "home_wrc_plus": context.get("home_wrc_plus"),
            "away_wrc_plus": context.get("away_wrc_plus"),
            "home_platoon_advantage": context.get("home_platoon_advantage"),
            "away_platoon_advantage": context.get("away_platoon_advantage"),
            "home_platoon_note": context.get("home_platoon_note"),
            "away_platoon_note": context.get("away_platoon_note"),
            "home_lineup_weight": context.get("home_lineup_weight"),
            "away_lineup_weight": context.get("away_lineup_weight"),
            "home_bullpen_era": context.get("home_bullpen_era"),
            "away_bullpen_era": context.get("away_bullpen_era"),
            "home_last_pitch_count": context.get("home_last_pitch_count"),
            "away_last_pitch_count": context.get("away_last_pitch_count"),
            "home_pitcher_vs_team_era": context.get("home_pitcher_vs_team_era"),
            "away_pitcher_vs_team_era": context.get("away_pitcher_vs_team_era"),
            "home_bp_relievers_3d": context.get("home_bp_relievers_3d"),
            "away_bp_relievers_3d": context.get("away_bp_relievers_3d"),
            "park_run_factor": context.get("park_run_factor"),
            "temperature": context.get("temperature"),
            "wind_mph": context.get("wind_speed"),
            "wind_direction": context.get("wind_direction"),
            "umpire": context.get("umpire"),
            "umpire_note": context.get("umpire_note"),
            "projected_total": context.get("projected_total"),
            "over_lean": context.get("over_lean"),
            "projected_spread": context.get("projected_spread"),
            "spread_lean": context.get("spread_lean"),
            "spread_delta": context.get("spread_delta"),
            "open_spread": context.get("open_spread"),
            "close_spread": context.get("close_spread"),
            "confidence": context.get("confidence"),
            "model_version": "v0.1",
            "wind_blowing_in": context.get("wind_blowing_in"),
            "is_dome": context.get("is_dome"),
            "timezone_change": context.get("timezone_change"),
            "home_last5_run_diff": context.get("home_last5_run_diff"),
            "away_last5_run_diff": context.get("away_last5_run_diff"),
            "days_since_last_home_game": context.get("days_since_last_home_game"),
            "away_consecutive_road_games": context.get("away_consecutive_road_games"),
            "home_travel_distance_last_game": context.get("home_travel_distance_last_game"),
            "open_total": context.get("open_total"),
            "close_total": context.get("close_total"),
            "f5_total_line": context.get("f5_total_line"),
            "nrfi_score": context.get("nrfi_score"),
            "home_first_inning_era": context.get("home_first_inning_era"),
            "away_first_inning_era": context.get("away_first_inning_era"),
            "home_first_inning_whip": context.get("home_first_inning_whip"),
            "away_first_inning_whip": context.get("away_first_inning_whip"),
            "projected_spread": context.get("projected_spread"),
            "spread_lean": context.get("spread_lean"),
            "spread_delta": context.get("spread_delta"),
            "open_spread": context.get("open_spread"),
            "close_spread": context.get("close_spread"),
        }

        # Parse away pitcher stats from pitcher_context
        pitcher_ctx = context.get("pitcher_context", "")
        if " | " in pitcher_ctx:
            away_ctx = pitcher_ctx.split(" | ")[1]
            try:
                record["away_sp_xera"] = sanitize_xera(float(away_ctx.split("xERA ")[1].split(",")[0]), context.get("away_pitcher", '')) if "xERA " in away_ctx else None
                record["away_sp_k_pct"] = sanitize_k_pct(float(away_ctx.split("K% ")[1].split("%")[0]), context.get("away_pitcher", '')) if "K% " in away_ctx else None
                record["away_sp_whiff_rate"] = float(away_ctx.split("whiff ")[1].split("%")[0]) if "whiff " in away_ctx else None
                record["away_sp_gb_pct"] = float(away_ctx.split("GB% ")[1].split("%")[0]) if "GB% " in away_ctx else None
                record["away_sp_hand"] = away_ctx.split("(")[1][0] if "(" in away_ctx else None
            except:
                pass

        # Try to fetch final score
        home_score = None
        away_score = None
        total_runs = 0
        home_win = None
        total_result = None
        run_line_result = None
        margin_of_victory = 0
        home_spread_covered = None

        home_team = context.get("home_team")
        away_team = context.get("away_team")
        game_date = context.get("game_date")

        game_pk, mlb_game = get_mlb_game_pk(home_team, away_team, game_date)
        if game_pk:
            home_score, away_score, game_over = get_final_score(game_pk)
            if home_score is not None and away_score is not None:
                total_runs = home_score + away_score
                home_win = home_score > away_score
                margin_of_victory = abs(home_score - away_score)

                # Total result vs close total (fall back to open_total if close unavailable)
                total_line = record.get('close_total') or record.get('open_total')
                if total_line:
                    total_result = 'Over' if total_runs > float(total_line) else 'Under' if total_runs < float(total_line) else 'Push'

                # Run line result (home -1.5)
                # Run line result — home covers if they win by 2+
                if (home_score - away_score) > 1.5:
                    run_line_result = 'home'
                elif (away_score - home_score) > 1.5:
                    run_line_result = 'away'
                else:
                    run_line_result = 'push'

                # Home spread covered (using close spread if available)
                close_spread = record.get('close_spread')
                if close_spread:
                    home_spread_covered = (home_score - away_score) > -float(close_spread)

                print(f'  Final score: {away_team} {away_score} @ {home_team} {home_score} | Total: {total_runs}')

        record['home_score'] = home_score
        record['away_score'] = away_score

        record['home_win'] = home_win

        record['total_result'] = total_result
        record['run_line_result'] = run_line_result
        record['home_spread_covered'] = home_spread_covered

        # Also parse close total from bookmakers for training
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"
        }
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results?on_conflict=game_id",
            headers=headers,
            json=record
        )
        if r.status_code not in [200, 201, 204]:
            print(f"  ⚠️ game_results log failed {r.status_code}: {r.text[:100]}")
        else:
            print(f"  📊 Training row logged: {context.get('away_team')} @ {context.get('home_team')}")
    except Exception as e:
        print(f"  ⚠️ game_results error: {e}")

def run():
    print(f"Fetching MLB games for today...")
    today = date.today().isoformat()
    for d in range(3):
        past_date = (date.today() - timedelta(days=d)).isoformat()
        delete_resp = requests.delete(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{past_date}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
        )
        print(f"Cleared {past_date}: status {delete_resp.status_code}")
    games = get_mlb_games()

    if not games:
        print("No MLB games found")
        return

    processed = 0
    
    # Fetch probable pitchers from MLB Stats API
    probable_pitchers = get_probable_pitchers(today)
    print(f"Probable pitchers loaded for {len(probable_pitchers)} teams")
    umpire_assignments = get_umpires(today)
    print(f"Umpire assignments loaded for {len(umpire_assignments)} games")
    confirmed_lineups = get_confirmed_lineups(today)
    print(f"Confirmed lineups loaded for {len(confirmed_lineups)} games")

    for game in games:
        try:
            home_team = game["home_team"]
            away_team = game["away_team"]
            game_id = game["id"]
            # Derive game_date from commence_time in ET, not system date
            commence_time = game.get("commence_time", "")
            if commence_time:
                from datetime import timezone
                game_utc = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
                game_et = game_utc - timedelta(hours=4)  # EDT
                game_date_et = game_et.strftime('%Y-%m-%d')
            else:
                game_date_et = today
            
            # Get probable pitchers
            pitcher_info = probable_pitchers.get(home_team, {})
            home_pitcher = pitcher_info.get("home_pitcher")
            away_pitcher = pitcher_info.get("away_pitcher")
            home_pitcher_id = pitcher_info.get("home_pitcher_id")
            away_pitcher_id = pitcher_info.get("away_pitcher_id")
            
            # Calculate days rest
            home_days_rest = get_pitcher_days_rest(home_pitcher_id, today)
            away_days_rest = get_pitcher_days_rest(away_pitcher_id, today)
            if home_days_rest:
                print(f"  {home_pitcher} days rest: {home_days_rest}")
            if away_days_rest:
                print(f"  {away_pitcher} days rest: {away_days_rest}")
            
            # Get pitcher stats from Supabase
            home_pitcher_stats = get_pitcher_stats(home_pitcher) if home_pitcher else None
            away_pitcher_stats = get_pitcher_stats(away_pitcher) if away_pitcher else None

            # Get pitcher home/away splits
            home_pitcher_splits = get_pitcher_splits(home_pitcher_id) if home_pitcher_id else None
            away_pitcher_splits = get_pitcher_splits(away_pitcher_id) if away_pitcher_id else None
            if home_pitcher_splits:
                print(f"  {home_pitcher} splits — Home ERA: {home_pitcher_splits.get('home_era')}, Away ERA: {home_pitcher_splits.get('away_era')}")
            if away_pitcher_splits:
                print(f"  {away_pitcher} splits — Home ERA: {away_pitcher_splits.get('home_era')}, Away ERA: {away_pitcher_splits.get('away_era')}")
            
            if home_pitcher:
                print(f"  {home_team} starter: {home_pitcher} — xERA: {home_pitcher_stats.get('xera', 'N/A') if home_pitcher_stats else 'stats not found'}")
            if away_pitcher:
                print(f"  {away_team} starter: {away_pitcher} — xERA: {away_pitcher_stats.get('xera', 'N/A') if away_pitcher_stats else 'stats not found'}")
            
            # Get venue
            venue = TEAM_VENUE.get(home_team, "Unknown")
            coords = VENUE_COORDS.get(venue, (40.7128, -74.0060))
            
            # Get weather
            weather = get_weather(venue, coords[0], coords[1])
            
            # Get park factors
            park = get_park_factors(home_team)
            park_run_factor = park["run_factor"] if park else 100
            
            # Parse market lines from Odds API
            total_line = None
            spread_line = None
            f5_total_line = None
            for bm in game.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    if mkt["key"] == "totals" and not total_line:
                        total_line = mkt["outcomes"][0]["point"] if mkt["outcomes"] else None
                    if mkt["key"] == "totals_1st_5_innings" and not f5_total_line:
                        f5_total_line = mkt["outcomes"][0]["point"] if mkt["outcomes"] else None
                    if mkt["key"] == "spreads" and not spread_line:
                        home_outcome = next((o for o in mkt.get("outcomes", []) if o.get("name") == home_team), None)
                        if home_outcome:
                            spread_line = home_outcome.get("point")
                if total_line and spread_line is not None and f5_total_line is not None:
                    break
            if f5_total_line:
                print(f"  F5 total line: {f5_total_line}")

            # Determine if this is 8am (open) or 2pm (close) run
            current_hour = datetime.now().hour
            is_open_run = current_hour < 15  # before 3pm ET = opening line
            
            # Weather adjustment
            # Weather adjustment — calibrated from 170+ game correlation analysis:
            # Temperature (0.143 corr) is 3.5x stronger than park factor (0.041)
            # Model has -0.23 run Under bias — correct with +0.25 baseline
            weather_adj = 0.25  # Under bias correction from backtesting
            if not weather.get("is_dome"):
                temp = weather.get("temperature", 70)
                if weather["wind_speed"] > 15 and weather["wind_direction"] in ["S", "SW", "SE"]:
                    weather_adj += 1.5  # wind blowing out
                elif weather["wind_speed"] > 15 and weather["wind_direction"] in ["N", "NW", "NE"]:
                    weather_adj -= 1.5  # wind blowing in
                # Temperature effect — stronger weight based on 0.143 correlation
                if temp < 45:
                    weather_adj -= 1.5  # freezing = heavy suppression
                elif temp < 55:
                    weather_adj -= 0.8  # cold
                elif temp < 65:
                    weather_adj -= 0.3  # cool
                elif temp > 85:
                    weather_adj += 0.8  # hot = more offense
                elif temp > 75:
                    weather_adj += 0.3  # warm
                if weather["precipitation"] > 0:
                    weather_adj -= 0.5

            # Park adjustment
            park_adj = (park_run_factor - 100) / 20  # normalize to runs

            # Confidence
            confidence = "HIGH" if park and not weather.get("is_dome") else "MEDIUM" if park else "LOW"

            # Get team offensive stats + home/away splits
            home_stats = get_team_stats(home_team)
            away_stats = get_team_stats(away_team)
            home_splits = get_team_splits(home_team)
            away_splits = get_team_splits(away_team)

           # Minimum 5 games before trusting team R/G — early season samples are noise
            home_split_games = home_splits['home']['games'] if home_splits and home_splits.get('home') else 0
            away_split_games = away_splits['away']['games'] if away_splits and away_splits.get('away') else 0
            home_total_games = home_stats.get('games_played', 0) if home_stats else 0
            away_total_games = away_stats.get('games_played', 0) if away_stats else 0

            home_rpg = (home_splits['home']['runs_per_game'] if home_split_games >= 5
                else home_stats['runs_per_game'] if home_stats and home_total_games >= 5
                else None)
            away_rpg = (away_splits['away']['runs_per_game'] if away_split_games >= 5
                else away_stats['runs_per_game'] if away_stats and away_total_games >= 5
                else None)
            home_ops_split = (home_splits['home']['ops'] if home_split_games >= 5
                  else home_stats['ops'] if home_stats and home_total_games >= 5
                  else None)
            away_ops_split = (away_splits['away']['ops'] if away_split_games >= 5
                  else away_stats['ops'] if away_stats and away_total_games >= 5
                  else None)

            if home_rpg:
                print(f"  {home_team} home R/G: {home_rpg:.2f}")
            if away_rpg:
                print(f"  {away_team} away R/G: {away_rpg:.2f}")

            # Reset throws variables to avoid scope issues
            home_throws = None
            away_throws = None
            
            # Get team strikeout rates
            home_k_pct = get_team_strikeout_rate(home_team)
            away_k_pct = get_team_strikeout_rate(away_team)
            if home_k_pct:
                print(f"  {home_team} K%: {home_k_pct:.1f}%")
            if away_k_pct:
                print(f"  {away_team} K%: {away_k_pct:.1f}%")

            # Calculate K rate gap vs pitcher
            home_pitcher_k = None
            away_pitcher_k = None
            if home_pitcher_stats and home_pitcher_stats.get('k_pct') is not None:
                raw_k = float(home_pitcher_stats.get('k_pct', 0) or 0)
                home_pitcher_k = raw_k * 100 if raw_k < 1 else raw_k
            if away_pitcher_stats and away_pitcher_stats.get('k_pct') is not None:
                raw_k = float(away_pitcher_stats.get('k_pct', 0) or 0)
                away_pitcher_k = raw_k * 100 if raw_k < 1 else raw_k

            # K gap: positive = pitcher K rate exceeds lineup K rate (pitcher edge)
            # away lineup faces home pitcher, home lineup faces away pitcher
            home_k_gap = round(home_pitcher_k - away_k_pct, 1) if home_pitcher_k and away_k_pct else None
            away_k_gap = round(away_pitcher_k - home_k_pct, 1) if away_pitcher_k and home_k_pct else None
            # Cap K gap contribution — early season K% samples are volatile
            if home_k_gap is not None:
                home_k_gap = max(min(home_k_gap, 15), -15)
            if away_k_gap is not None:
                away_k_gap = max(min(away_k_gap, 15), -15)
            if home_k_gap is not None:
                print(f"  K gap — {home_pitcher} vs {away_team} lineup: {home_k_gap:+.1f}pts")
            if away_k_gap is not None:
                print(f"  K gap — {away_pitcher} vs {home_team} lineup: {away_k_gap:+.1f}pts")
            
            # Get wOBA/wRC+ team offense
            home_offense = get_team_woba_wrc(home_team)
            away_offense = get_team_woba_wrc(away_team)
            if home_offense:
                print(f"  {home_team} wOBA: {home_offense.get('woba')} wRC+: {home_offense.get('wrc_plus')} K%: {home_offense.get('k_pct')}%")
            if away_offense:
                print(f"  {away_team} wOBA: {away_offense.get('woba')} wRC+: {away_offense.get('wrc_plus')} K%: {away_offense.get('k_pct')}%")

            # Get bullpen stats
            home_bullpen = get_bullpen_stats(home_team)
            away_bullpen = get_bullpen_stats(away_team)

            # Pitcher last outing — pitch count affects fatigue
            home_last_outing = get_pitcher_last_outing(home_pitcher_id) if home_pitcher_id else None
            away_last_outing = get_pitcher_last_outing(away_pitcher_id) if away_pitcher_id else None
            if home_last_outing:
                print(f"  {home_pitcher} last outing: {home_last_outing['pitches']} pitches, {home_last_outing['innings']} IP")
            if away_last_outing:
                print(f"  {away_pitcher} last outing: {away_last_outing['pitches']} pitches, {away_last_outing['innings']} IP")

            # Pitcher vs team history
            # Get opponent team IDs
            home_team_id = None
            away_team_id = None
            try:
                teams_r = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=10)
                for t in teams_r.json().get('teams', []):
                    if home_team.lower() in t.get('name', '').lower() or t.get('name', '').lower().endswith(home_team.split(' ')[-1].lower()):
                        home_team_id = t['id']
                    if away_team.lower() in t.get('name', '').lower() or t.get('name', '').lower().endswith(away_team.split(' ')[-1].lower()):
                        away_team_id = t['id']
            except:
                pass
            home_vs_away = get_pitcher_vs_team(home_pitcher_id, away_team_id) if home_pitcher_id and away_team_id else None
            away_vs_home = get_pitcher_vs_team(away_pitcher_id, home_team_id) if away_pitcher_id and home_team_id else None
            if home_vs_away:
                print(f"  {home_pitcher} vs {away_team}: ERA {home_vs_away['era_vs_team']}, AVG {home_vs_away['avg_vs_team']}, {home_vs_away['ip_vs_team']} IP")
            if away_vs_home:
                print(f"  {away_pitcher} vs {home_team}: ERA {away_vs_home['era_vs_team']}, AVG {away_vs_home['avg_vs_team']}, {away_vs_home['ip_vs_team']} IP")

            # Bullpen usage last 3 days
            home_bp_usage = get_bullpen_usage(home_team, game_date_et)
            away_bp_usage = get_bullpen_usage(away_team, game_date_et)
            if home_bp_usage and home_bp_usage['games_last_3d'] > 0:
                print(f"  {home_team} bullpen last 3d: {home_bp_usage['relievers_used_3d']} relievers in {home_bp_usage['games_last_3d']} games")
            if away_bp_usage and away_bp_usage['games_last_3d'] > 0:
                print(f"  {away_team} bullpen last 3d: {away_bp_usage['relievers_used_3d']} relievers in {away_bp_usage['games_last_3d']} games")

            # Fetch first inning splits for NRFI model — try Supabase first, then MLB API direct
            home_first_inn = get_pitcher_first_inning(home_pitcher) if home_pitcher else None
            away_first_inn = get_pitcher_first_inning(away_pitcher) if away_pitcher else None
            # If Supabase has no first inning data, query MLB Stats API directly
            if not home_first_inn and home_pitcher:
                try:
                    from pitcher_stats import get_first_inning_splits
                    home_first_inn = get_first_inning_splits(home_pitcher)
                except:
                    pass
            if not away_first_inn and away_pitcher:
                try:
                    from pitcher_stats import get_first_inning_splits
                    away_first_inn = get_first_inning_splits(away_pitcher)
                except:
                    pass
            if home_first_inn:
                print(f"  {home_pitcher} 1st inning: ERA {home_first_inn.get('first_inning_era')}, WHIP {home_first_inn.get('first_inning_whip')}")
            else:
                print(f"  {home_pitcher} 1st inning: no data yet")
            if away_first_inn:
                print(f"  {away_pitcher} 1st inning: ERA {away_first_inn.get('first_inning_era')}, WHIP {away_first_inn.get('first_inning_whip')}")
            else:
                print(f"  {away_pitcher} 1st inning: no data yet")

            # Detect opener/bullpen games
            home_is_opener = detect_opener(home_pitcher_id) if home_pitcher_id else False
            away_is_opener = detect_opener(away_pitcher_id) if away_pitcher_id else False
            if home_is_opener:
                print(f"  ⚠️ {home_pitcher} detected as OPENER/BULLPEN — NRFI penalty applied")
            if away_is_opener:
                print(f"  ⚠️ {away_pitcher} detected as OPENER/BULLPEN — NRFI penalty applied")

            # Calculate NRFI score — use local variables not context dict
            nrfi_score = calc_nrfi_score(
                home_pitcher_stats,
                away_pitcher_stats,
                home_days_rest,
                away_days_rest,
                weather.get("temperature"),
                weather.get("wind_speed"),
                weather.get("wind_direction"),
                park_run_factor,
                home_offense.get("wrc_plus") if home_offense else None,
                away_offense.get("wrc_plus") if away_offense else None,
                home_first_inn,
                away_first_inn,
                home_is_opener,
                away_is_opener,
            )
            if nrfi_score:
                print(f"  NRFI score: {nrfi_score} ({'NRFI lean' if nrfi_score >= 60 else 'YRFI lean' if nrfi_score <= 40 else 'neutral'})")
            # Get confirmed lineup
            lineup_info = confirmed_lineups.get(home_team, {})
            home_lineup = lineup_info.get("home_lineup", [])
            away_lineup = lineup_info.get("away_lineup", [])
            lineup_confirmed = lineup_info.get("lineup_confirmed", False)
            # Calculate batting order weights
            home_lineup_str = ', '.join(home_lineup) if isinstance(home_lineup, list) else home_lineup or ''
            away_lineup_str = ', '.join(away_lineup) if isinstance(away_lineup, list) else away_lineup or ''
            home_lineup_weight = calc_batting_order_weight(home_lineup_str) if lineup_confirmed else None
            away_lineup_weight = calc_batting_order_weight(away_lineup_str) if lineup_confirmed else None
            if home_lineup_weight:
                print(f"  {home_team} lineup weight: {home_lineup_weight}")
            if away_lineup_weight:
                print(f"  {away_team} lineup weight: {away_lineup_weight}")
            # Calculate platoon advantage if lineups confirmed
            home_platoon_score, home_platoon_note = None, None
            away_platoon_score, away_platoon_note = None, None
            if lineup_confirmed and home_pitcher_stats and away_pitcher_stats:
                home_throws = home_pitcher_stats.get('throws', None)
                away_throws = away_pitcher_stats.get('throws', None)
                if home_lineup and away_throws:
                    print(f"  Calculating away lineup platoon vs {away_pitcher} ({away_throws}HP)...")
                    away_platoon_score, away_platoon_note = calc_platoon_advantage(
                        ', '.join(away_lineup) if isinstance(away_lineup, list) else away_lineup,
                        away_throws
                    )
                    if away_platoon_note:
                        print(f"  Away platoon: {away_platoon_note}")
                if away_lineup and home_throws:
                    print(f"  Calculating home lineup platoon vs {home_pitcher} ({home_throws}HP)...")
                    home_platoon_score, home_platoon_note = calc_platoon_advantage(
                        ', '.join(home_lineup) if isinstance(home_lineup, list) else home_lineup,
                        home_throws
                    )
                    if home_platoon_note:
                        print(f"  Home platoon: {home_platoon_note}")
            # Get last 10 form
            home_form = get_team_last10(home_team)
            away_form = get_team_last10(away_team)
            if home_form:
                print(f"  {home_team} record: {home_form['wins']}-{home_form['losses']}, last 10: {home_form['last10']}, streak: {home_form['streak']}")
            if away_form:
                print(f"  {away_team} record: {away_form['wins']}-{away_form['losses']}, last 10: {away_form['last10']}, streak: {away_form['streak']}")
            if home_bullpen:
                print(f"  {home_team} bullpen ERA: {home_bullpen.get('bullpen_era')} save%: {home_bullpen.get('save_pct')}%")
            if away_bullpen:
                print(f"  {away_team} bullpen ERA: {away_bullpen.get('bullpen_era')} save%: {away_bullpen.get('save_pct')}%")
            
            # ── PROJECTED TOTAL (calibrated from 170+ game backtesting) ──
            # Inputs used + their correlation with actual total:
            # projected_total: 0.212, temperature: 0.143, nrfi_score: -0.164
            # away_xera: 0.123, home_xera: 0.096, away_wrc: 0.086
            home_xera_val = sanitize_xera(home_pitcher_stats.get('xera'), home_pitcher) if home_pitcher_stats else None
            away_xera_val = sanitize_xera(away_pitcher_stats.get('xera'), away_pitcher) if away_pitcher_stats else None
            home_wrc = home_offense.get('wrc_plus') if home_offense else None
            away_wrc = away_offense.get('wrc_plus') if away_offense else None
            park_mult = park_run_factor / 100 if park_run_factor else 1.0

            if home_rpg and away_rpg:
                base_total = home_rpg + away_rpg
                projected_runs = base_total * park_mult
                projected_total = round(projected_runs + weather_adj, 1)

                # NRFI score adjustment — strong -0.164 inverse correlation with total runs
                if nrfi_score:
                    nrfi_adj = (nrfi_score - 50) * -0.08  # high NRFI = lower total
                    projected_total = round(projected_total + nrfi_adj, 1)

                # Bullpen differential — late innings matter for totals
                home_bp_era = home_bullpen.get('bullpen_era', 4.0) if home_bullpen else 4.0
                away_bp_era = away_bullpen.get('bullpen_era', 4.0) if away_bullpen else 4.0
                bp_total_adj = ((home_bp_era + away_bp_era) / 2 - 4.0) * 0.25
                projected_total = round(projected_total + bp_total_adj, 1)

                # Umpire over_rate adjustment
                if ump_stats and ump_stats.get('over_rate'):
                    ump_adj = (float(ump_stats['over_rate']) - 0.5) * 1.2  # 0.56 over_rate → +0.072
                    projected_total = round(projected_total + ump_adj, 1)

                if total_line:
                    delta = projected_total - total_line
                    over_lean = True if delta > 0.3 else False if delta < -0.3 else None
                else:
                    over_lean = True if weather_adj + park_adj > 0.5 else None
                print(f"  {home_team} avg: {home_rpg:.2f} R/G | {away_team} avg: {away_rpg:.2f} R/G | Projected: {projected_total}")
            else:
                projected_runs = None
                if total_line:
                    net_adj = weather_adj + park_adj
                    projected_total = round(total_line + net_adj, 1)
                    over_lean = True if net_adj > 0.5 else False if net_adj < -0.5 else None
                else:
                    projected_total = None
                    over_lean = None
                print(f"  Team stats not available yet — market line fallback: {projected_total}")

            # ── xERA GAP OVER BOOST ──
            # Data shows: big xERA gap (2.0+) → 59.3% Over
            if home_xera_val and away_xera_val:
                xera_gap = abs(float(home_xera_val) - float(away_xera_val))
                if xera_gap >= 2.0 and over_lean is None:
                    over_lean = True
                    print(f"  xERA gap {xera_gap:.1f} → Over lean (59.3% hit rate on 2.0+ gaps)")
                elif xera_gap >= 2.0 and over_lean == False:
                    over_lean = None
                    print(f"  xERA gap {xera_gap:.1f} conflicts with Under lean → neutral")

            # ── PROJECTED SPREAD (fixed formula — no double-counting) ──
            # Old formula double-counted offense: rpg × wrc × xera
            # New: league_avg × (wrc/100) × (4.25/opp_xera) × park — standard multiplicative approach
            projected_spread = None
            spread_lean = None
            try:
                league_avg_rpg = 4.25  # 2026 league average R/G

                if home_xera_val and away_xera_val and home_wrc and away_wrc:
                    # Clean separation: offense (wRC+) × pitching quality (xERA) × park
                    home_expected = league_avg_rpg * (float(home_wrc) / 100) * (4.25 / float(away_xera_val)) * park_mult
                    away_expected = league_avg_rpg * (float(away_wrc) / 100) * (4.25 / float(home_xera_val)) * park_mult

                    # Bullpen quality adjustment
                    bp_adj = (away_bp_era - home_bp_era) * 0.15

                    projected_spread = round(home_expected - away_expected + bp_adj, 2)
                    if projected_spread >= 0.5:
                        spread_lean = 'home'
                    elif projected_spread <= -0.5:
                        spread_lean = 'away'
                    else:
                        spread_lean = None
                    print(f"  Projected spread: {home_team} {'+' if projected_spread >= 0 else ''}{projected_spread} | Lean: {spread_lean or 'neutral'}")
                elif home_rpg and away_rpg:
                    # No pitcher data — use raw R/G + park only
                    projected_spread = round((home_rpg - away_rpg) * park_mult, 2)
                    spread_lean = 'home' if projected_spread >= 0.5 else 'away' if projected_spread <= -0.5 else None
                    print(f"  Projected spread (no pitcher data): {projected_spread}")
            except Exception as e:
                print(f"  ⚠️ Spread projection error: {e}")

            # Spread delta — projected vs posted
            spread_delta = None
            if projected_spread is not None and spread_line is not None:
                spread_delta = round(projected_spread - spread_line, 2)
                print(f"  Spread delta: model {projected_spread} vs posted {spread_line} = {'+' if spread_delta >= 0 else ''}{spread_delta}")

            # Get umpire
            ump_name = umpire_assignments.get(home_team)
            ump_stats = get_umpire_stats(ump_name) if ump_name else None
            if ump_name:
                k_rate = ump_stats.get('k_rate_above_avg', 'N/A') if ump_stats else 'not in database'
                over_pct = ump_stats.get('over_rate', 'N/A') if ump_stats else 'N/A'
                print(f"  Umpire: {ump_name} — K rate: {k_rate}, Over%: {over_pct}")

            # Build pitcher context string for Jerry
            pitcher_context = ""
            if home_pitcher_stats:
                xera = home_pitcher_stats.get('xera', 'N/A')
                kpct = float(home_pitcher_stats.get('k_pct') or 0)
                whiff = float(home_pitcher_stats.get('whiff_rate') or 0)
                gb = float(home_pitcher_stats.get('gb_pct') or 0)
                fb = float(home_pitcher_stats.get('fb_pct') or 0)
                lob = float(home_pitcher_stats.get('lob_pct') or 0)
                home_throws = home_pitcher_stats.get('throws', 'R')
                pitcher_type = "GB pitcher" if gb > 50 else "FB pitcher" if fb > 40 else "neutral"
                pitcher_context += f"{home_pitcher} ({home_throws}HP): xERA {xera}, K% {kpct*100:.1f}%, whiff {whiff*100:.1f}%, GB% {gb*100:.1f}%, FB% {fb*100:.1f}%, LOB% {lob*100:.1f}% ({pitcher_type})"
            if away_pitcher_stats:
                xera = away_pitcher_stats.get('xera', 'N/A')
                kpct = float(away_pitcher_stats.get('k_pct') or 0)
                whiff = float(away_pitcher_stats.get('whiff_rate') or 0)
                gb = float(away_pitcher_stats.get('gb_pct') or 0)
                fb = float(away_pitcher_stats.get('fb_pct') or 0)
                lob = float(away_pitcher_stats.get('lob_pct') or 0)
                away_throws = away_pitcher_stats.get('throws', 'R')
                pitcher_type = "GB pitcher" if gb > 50 else "FB pitcher" if fb > 40 else "neutral"
                pitcher_context += f" | {away_pitcher} ({away_throws}HP): xERA {xera}, K% {kpct*100:.1f}%, whiff {whiff*100:.1f}%, GB% {gb*100:.1f}%, FB% {fb*100:.1f}%, LOB% {lob*100:.1f}% ({pitcher_type})"
            # Build umpire note
            ump_note = ""
            if ump_stats:
                k_tendency = "K-friendly" if ump_stats.get('k_rate_above_avg', 0) > 0.5 else "hitter-friendly" if ump_stats.get('k_rate_above_avg', 0) < -0.5 else "neutral"
                over_pct = ump_stats.get('over_rate', 0.5) * 100
                ump_note = f"{ump_name} — {k_tendency} zone, {over_pct:.0f}% over rate"

            # ── XGBOOST FEATURES ──
            # Wind blowing in (Wrigley-specific for now, expandable later)
            # Any outdoor park with wind > 15mph blowing toward home plate
            is_dome = venue in DOME_VENUES
            wind_blowing_in = (
                not is_dome and
                (weather.get('wind_speed') or 0) > 15 and
                weather.get('wind_direction') in ['N', 'NE', 'NW']
            )

            # Timezone change
            home_tz = TEAM_TIMEZONES.get(home_team, TEAM_TIMEZONES.get(home_team.split(' ')[-1], 'ET'))
            away_tz = TEAM_TIMEZONES.get(away_team, TEAM_TIMEZONES.get(away_team.split(' ')[-1], 'ET'))
            tz_change = abs(TZ_OFFSET.get(home_tz, 0) - TZ_OFFSET.get(away_tz, 0))

            # Schedule-based features (one API call per team covers run diff, home days, road streak)
            home_run_diff, home_days_since_home, _, home_last_venue = get_team_schedule_features(home_team, game_date_et)
            time.sleep(0.3)
            away_run_diff, _, away_consec_road, _ = get_team_schedule_features(away_team, game_date_et)

            # Travel distance for home team
            home_travel_dist = None
            if home_last_venue and venue:
                # Normalize venue names — MLB API may return slightly different names
                VENUE_ALIASES = {
                    'Oriole Park at Camden Yards': 'Camden Yards',
                    'Oriole Park At Camden Yards': 'Camden Yards',
                    'Oakland Coliseum': 'Sutter Health Park',
                    'Oakland-Alameda County Coliseum': 'Sutter Health Park',
                    'RingCentral Coliseum': 'Sutter Health Park',
                    'loanDepot park': 'loanDepot Park',
                    'loandepot park': 'loanDepot Park',
                    'Tropicana Field ': 'Tropicana Field',
                    'T-Mobile Park ': 'T-Mobile Park',
                    'Rate Field': 'Guaranteed Rate Field',
                    'Guaranteed Rate  Field': 'Guaranteed Rate Field',
                }
                last_venue_norm = VENUE_ALIASES.get(home_last_venue, home_last_venue)
                venue_norm = VENUE_ALIASES.get(venue, venue)
                if last_venue_norm in VENUE_COORDS and venue_norm in VENUE_COORDS:
                    try:
                        home_travel_dist = round(haversine(VENUE_COORDS[last_venue_norm], VENUE_COORDS[venue_norm]))
                    except:
                        pass
                elif home_last_venue not in VENUE_COORDS and home_last_venue:
                    print(f"  ⚠️ Missing VENUE_COORDS for: '{home_last_venue}'")

            context = {
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "game_date": game_date_et,
                "venue": venue,
                "home_pitcher": home_pitcher,
                "away_pitcher": away_pitcher,
                "home_sp_xera": sanitize_xera(home_pitcher_stats.get("xera"), home_pitcher) if home_pitcher_stats else None,
                "away_sp_xera": sanitize_xera(away_pitcher_stats.get("xera"), away_pitcher) if away_pitcher_stats else None,
                "home_throws": home_throws,
                "away_throws": away_throws,
                "home_days_rest": home_days_rest,
                "away_days_rest": away_days_rest,
                "home_pitcher_home_era": home_pitcher_splits.get('home_era') if home_pitcher_splits else None,
                "home_pitcher_away_era": home_pitcher_splits.get('away_era') if home_pitcher_splits else None,
                "away_pitcher_home_era": away_pitcher_splits.get('home_era') if away_pitcher_splits else None,
                "away_pitcher_away_era": away_pitcher_splits.get('away_era') if away_pitcher_splits else None,
                "umpire": ump_name,
                "umpire_note": ump_note,
                "pitcher_context": pitcher_context,
                "temperature": weather["temperature"],
                "wind_speed": weather["wind_speed"],
                "wind_direction": weather["wind_direction"],
                "precipitation": weather["precipitation"],
                "park_run_factor": park_run_factor,
                "open_total": total_line if is_open_run else None,
                "close_total": total_line if not is_open_run else None,
                "f5_total_line": f5_total_line,
                "projected_total": projected_total,
                "over_lean": over_lean,
                "projected_spread": projected_spread,
                "spread_lean": spread_lean,
                "spread_delta": spread_delta,
                "open_spread": spread_line if is_open_run else None,
                "close_spread": spread_line if not is_open_run else None,
                "confidence": confidence,
                "fetched_at": datetime.now().isoformat(),
                "home_runs_per_game": home_rpg,
                "away_runs_per_game": away_rpg,
                "home_ops": home_ops_split,
                "away_ops": away_ops_split,
                "home_lineup": ", ".join(home_lineup) if home_lineup else None,
                "away_lineup": ", ".join(away_lineup) if away_lineup else None,
                "lineup_confirmed": lineup_confirmed,
                "home_lineup_weight": home_lineup_weight,
                "away_lineup_weight": away_lineup_weight,
                "nrfi_score": nrfi_score,
                "home_first_inning_era": home_first_inn.get("first_inning_era") if home_first_inn else None,
                "away_first_inning_era": away_first_inn.get("first_inning_era") if away_first_inn else None,
                "home_first_inning_whip": home_first_inn.get("first_inning_whip") if home_first_inn else None,
                "away_first_inning_whip": away_first_inn.get("first_inning_whip") if away_first_inn else None,
                "home_woba": home_offense.get('woba') if home_offense else None,
                "away_woba": away_offense.get('woba') if away_offense else None,
                "home_wrc_plus": home_offense.get('wrc_plus') if home_offense else None,
                "away_wrc_plus": away_offense.get('wrc_plus') if away_offense else None,
                "home_team_k_pct": home_k_pct,
                "away_team_k_pct": away_k_pct,
                "home_k_gap": home_k_gap,
                "away_k_gap": away_k_gap,
                "home_platoon_advantage": home_platoon_score,
                "away_platoon_advantage": away_platoon_score,
                "home_platoon_note": home_platoon_note,
                "away_platoon_note": away_platoon_note,
                "home_record": f"{home_form['wins']}-{home_form['losses']}" if home_form else None,
                "away_record": f"{away_form['wins']}-{away_form['losses']}" if away_form else None,
                "home_last10": home_form['last10'] if home_form else None,
                "away_last10": away_form['last10'] if away_form else None,
                "home_streak": home_form['streak'] if home_form else None,
                "away_streak": away_form['streak'] if away_form else None,
                "home_bullpen_era": home_bullpen['bullpen_era'] if home_bullpen else None,
                "away_bullpen_era": away_bullpen['bullpen_era'] if away_bullpen else None,
                "home_save_pct": home_bullpen['save_pct'] if home_bullpen else None,
                "away_save_pct": away_bullpen['save_pct'] if away_bullpen else None,
                "home_last_pitch_count": home_last_outing['pitches'] if home_last_outing else None,
                "away_last_pitch_count": away_last_outing['pitches'] if away_last_outing else None,
                "home_last_ip": home_last_outing['innings'] if home_last_outing else None,
                "away_last_ip": away_last_outing['innings'] if away_last_outing else None,
                "home_pitcher_vs_team_era": home_vs_away['era_vs_team'] if home_vs_away else None,
                "away_pitcher_vs_team_era": away_vs_home['era_vs_team'] if away_vs_home else None,
                "home_pitcher_vs_team_avg": home_vs_away['avg_vs_team'] if home_vs_away else None,
                "away_pitcher_vs_team_avg": away_vs_home['avg_vs_team'] if away_vs_home else None,
                "home_bp_relievers_3d": home_bp_usage['relievers_used_3d'] if home_bp_usage else None,
                "away_bp_relievers_3d": away_bp_usage['relievers_used_3d'] if away_bp_usage else None,
                "wind_blowing_in": wind_blowing_in,
                "is_dome": is_dome,
                "timezone_change": tz_change,
                "home_last5_run_diff": home_run_diff,
                "away_last5_run_diff": away_run_diff,
                "days_since_last_home_game": home_days_since_home,
                "away_consecutive_road_games": away_consec_road,
                "home_travel_distance_last_game": home_travel_dist,
            }
            if upload_game_context(context):
                lean = "OVER" if context["over_lean"] else "UNDER" if context["over_lean"] is False else "NEUTRAL"
                print(f"✅ {away_team} @ {home_team} — {venue} — {weather['temperature']}°F, wind {weather['wind_speed']}mph {weather['wind_direction']} — {lean}")
                processed += 1
                log_game_result(context)
            else:
                print(f"❌ Failed: {away_team} @ {home_team}")
                
        except Exception as e:
            print(f"❌ Error processing {game.get('home_team', 'unknown')}: {e}")
    
    print(f"\nDone! Processed {processed} games")

if __name__ == "__main__":
    run()