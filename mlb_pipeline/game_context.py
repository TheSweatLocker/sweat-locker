import requests
from datetime import datetime, date
import json

import os
from dotenv import load_dotenv
load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")

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
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=ilike.{requests.utils.quote('*'+pitcher_name+'*')}&select=*&limit=1",
            headers=headers
        )
        data = r.json()
        return data[0] if data else None
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

def get_weather(venue, lat, lon):
    if venue in DOME_VENUES:
        return {"temperature": 72, "wind_speed": 0, "wind_direction": "N/A", "precipitation": 0, "is_dome": True}
    try:
        r = requests.get(
            f"https://api.openweathermap.org/data/2.5/weather",
            params={"lat": lat, "lon": lon, "appid": WEATHER_API_KEY, "units": "imperial"}
        )
        data = r.json()
        wind_data = data.get("wind", {})
        wind_deg = wind_data.get("deg", 0)
        wind_speed = round(wind_data.get("speed", 0))
        directions = ["N","NE","E","SE","S","SW","W","NW"]
        wind_dir = directions[round(wind_deg/45) % 8]
        return {
            "temperature": round(data["main"]["temp"]),
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
                "markets": "totals,h2h",
                "oddsFormat": "american",
                "bookmakers": "draftkings"
            }
        )
        return r.json()
    except Exception as e:
        print(f"Odds API error: {e}")
        return []
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
        "Prefer": "resolution=merge-duplicates"
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context",
        headers=headers,
        json=context
    )
    return r.status_code in [200, 201]

def run():
    print(f"Fetching MLB games for today...")
    # Clear today's games first
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "return=minimal"
    }
    today = date.today().isoformat()
    requests.delete(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{today}",
        headers=headers
    )
    games = get_mlb_games()
    
    if not games:
        print("No MLB games found")
        return
    
    today = date.today().isoformat()
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
            
            # Calculate over/under lean
            total_line = None
            for bm in game.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    if mkt["key"] == "totals":
                        total_line = mkt["outcomes"][0]["point"] if mkt["outcomes"] else None
                        break
                if total_line:
                    break
            
            # Weather adjustment
            weather_adj = 0
            if not weather.get("is_dome"):
                if weather["wind_speed"] > 15 and weather["wind_direction"] in ["S", "SW", "SE"]:
                    weather_adj = 1.5  # wind blowing out
                elif weather["wind_speed"] > 15 and weather["wind_direction"] in ["N", "NW", "NE"]:
                    weather_adj = -1.5  # wind blowing in
                if weather["temperature"] < 50:
                    weather_adj -= 1.0  # cold suppresses offense
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

            # Use home split for home team, away split for away team
            home_rpg = home_splits['home']['runs_per_game'] if home_splits and home_splits.get('home') else (home_stats['runs_per_game'] if home_stats else None)
            away_rpg = away_splits['away']['runs_per_game'] if away_splits and away_splits.get('away') else (away_stats['runs_per_game'] if away_stats else None)
            home_ops_split = home_splits['home']['ops'] if home_splits and home_splits.get('home') else (home_stats['ops'] if home_stats else None)
            away_ops_split = away_splits['away']['ops'] if away_splits and away_splits.get('away') else (away_stats['ops'] if away_stats else None)

            if home_rpg:
                print(f"  {home_team} home R/G: {home_rpg:.2f}")
            if away_rpg:
                print(f"  {away_team} away R/G: {away_rpg:.2f}")

            # Get bullpen stats
            home_bullpen = get_bullpen_stats(home_team)
            away_bullpen = get_bullpen_stats(away_team)
            # Get confirmed lineup
            lineup_info = confirmed_lineups.get(home_team, {})
            home_lineup = lineup_info.get("home_lineup", [])
            away_lineup = lineup_info.get("away_lineup", [])
            lineup_confirmed = lineup_info.get("lineup_confirmed", False)
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
            
            # Calculate projected total from team stats + park + weather
            if home_rpg and away_rpg:
                base_total = home_rpg + away_rpg
                park_multiplier = park_run_factor / 100
                projected_runs = base_total * park_multiplier
                projected_total = round(projected_runs + weather_adj, 1)
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
                kpct = home_pitcher_stats.get('k_pct', 0)
                whiff = home_pitcher_stats.get('whiff_rate', 0)
                gb = home_pitcher_stats.get('gb_pct', 0)
                fb = home_pitcher_stats.get('fb_pct', 0)
                lob = home_pitcher_stats.get('lob_pct', 0)
                throws = home_pitcher_stats.get('throws', 'R')
                pitcher_type = "GB pitcher" if gb > 50 else "FB pitcher" if fb > 40 else "neutral"
                pitcher_context += f"{home_pitcher} ({throws}HP): xERA {xera}, K% {kpct:.1f}%, whiff {whiff:.1f}%, GB% {gb:.1f}%, FB% {fb:.1f}%, LOB% {lob:.1f}% ({pitcher_type})"
            if away_pitcher_stats:
                xera = away_pitcher_stats.get('xera', 'N/A')
                kpct = away_pitcher_stats.get('k_pct', 0)
                whiff = away_pitcher_stats.get('whiff_rate', 0)
                gb = away_pitcher_stats.get('gb_pct', 0)
                fb = away_pitcher_stats.get('fb_pct', 0)
                lob = away_pitcher_stats.get('lob_pct', 0)
                throws = away_pitcher_stats.get('throws', 'R')
                pitcher_type = "GB pitcher" if gb > 50 else "FB pitcher" if fb > 40 else "neutral"
            pitcher_context += f" | {away_pitcher} ({throws}HP): xERA {xera}, K% {kpct:.1f}%, whiff {whiff:.1f}%, GB% {gb:.1f}%, FB% {fb:.1f}%, LOB% {lob:.1f}% ({pitcher_type})"
            # Build umpire note
            ump_note = ""
            if ump_stats:
                k_tendency = "K-friendly" if ump_stats.get('k_rate_above_avg', 0) > 0.5 else "hitter-friendly" if ump_stats.get('k_rate_above_avg', 0) < -0.5 else "neutral"
                over_pct = ump_stats.get('over_rate', 0.5) * 100
                ump_note = f"{ump_name} — {k_tendency} zone, {over_pct:.0f}% over rate"

            context = {
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "game_date": today,
                "venue": venue,
                "home_pitcher": home_pitcher,
                "away_pitcher": away_pitcher,
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
                "projected_total": projected_total,
                "over_lean": over_lean,
                "confidence": confidence,
                "fetched_at": datetime.now().isoformat(),
                "home_runs_per_game": home_rpg,
                "away_runs_per_game": away_rpg,
                "home_ops": home_ops_split,
                "away_ops": away_ops_split,
                "home_lineup": ", ".join(home_lineup) if home_lineup else None,
                "away_lineup": ", ".join(away_lineup) if away_lineup else None,
                "lineup_confirmed": lineup_confirmed,
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
            }
            if upload_game_context(context):
                lean = "OVER" if context["over_lean"] else "UNDER" if context["over_lean"] is False else "NEUTRAL"
                print(f"✅ {away_team} @ {home_team} — {venue} — {weather['temperature']}°F, wind {weather['wind_speed']}mph {weather['wind_direction']} — {lean}")
                processed += 1
            else:
                print(f"❌ Failed: {away_team} @ {home_team}")
                
        except Exception as e:
            print(f"❌ Error processing {game.get('home_team', 'unknown')}: {e}")
    
    print(f"\nDone! Processed {processed} games")

if __name__ == "__main__":
    run()