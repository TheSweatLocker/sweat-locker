import requests
from datetime import datetime, date
import json

import os
SUPABASE_URL = os.environ.get("SUPABASE_URL", "your_supabase_url")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "your_supabase_anon_key")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "your_odds_api_key")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "your_openweathermap_key")

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
                pitchers[home_team] = {
                    "home_pitcher": home_pitcher,
                    "away_pitcher": away_pitcher,
                    "away_team": away_team
                }
        print(f"Found probable pitchers for {len(pitchers)} games")
        return pitchers
    except Exception as e:
        print(f"MLB Stats API error: {e}")
        return {}

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

    for game in games:
        try:
            home_team = game["home_team"]
            away_team = game["away_team"]
            game_id = game["id"]
            
            # Get probable pitchers
            pitcher_info = probable_pitchers.get(home_team, {})
            home_pitcher = pitcher_info.get("home_pitcher")
            away_pitcher = pitcher_info.get("away_pitcher")
            
            # Get pitcher stats from Supabase
            home_pitcher_stats = get_pitcher_stats(home_pitcher) if home_pitcher else None
            away_pitcher_stats = get_pitcher_stats(away_pitcher) if away_pitcher else None
            
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
                pitcher_context += f"{home_pitcher}: xERA {xera}, K% {kpct:.1f}%, whiff {whiff:.1f}%"
            if away_pitcher_stats:
                xera = away_pitcher_stats.get('xera', 'N/A')
                kpct = away_pitcher_stats.get('k_pct', 0)
                whiff = away_pitcher_stats.get('whiff_rate', 0)
                pitcher_context += f" | {away_pitcher}: xERA {xera}, K% {kpct:.1f}%, whiff {whiff:.1f}%"

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
                "umpire": ump_name,
                "umpire_note": ump_note,
                "pitcher_context": pitcher_context,
                "temperature": weather["temperature"],
                "wind_speed": weather["wind_speed"],
                "wind_direction": weather["wind_direction"],
                "precipitation": weather["precipitation"],
                "park_run_factor": park_run_factor,
                "projected_total": round(total_line + weather_adj + park_adj, 1) if total_line else None,
                "over_lean": (weather_adj + park_adj) > 0.5 if total_line else None,
                "confidence": confidence,
                "fetched_at": datetime.now().isoformat()
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