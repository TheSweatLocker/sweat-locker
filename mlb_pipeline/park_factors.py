import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# 2025-calibrated MLB Park Factors (from 2,401 actual games)
# Run factor > 100 = hitter friendly, < 100 = pitcher friendly
PARK_FACTORS = [
    {"team": "Colorado Rockies", "venue": "Coors Field", "run_factor": 133, "hr_factor": 130, "hits_factor": 120, "notes": "Extreme hitter park — 11.8 R/G in 2025"},
    {"team": "Oakland Athletics", "venue": "Sutter Health Park", "run_factor": 113, "hr_factor": 105, "hits_factor": 108, "notes": "Sacramento — hitter friendly, 10.0 R/G in 2025"},
    {"team": "Los Angeles Dodgers", "venue": "Dodger Stadium", "run_factor": 111, "hr_factor": 105, "hits_factor": 106, "notes": "9.9 R/G in 2025 — not pitcher friendly anymore"},
    {"team": "Arizona Diamondbacks", "venue": "Chase Field", "run_factor": 110, "hr_factor": 108, "hits_factor": 106, "notes": "Dome, 9.8 R/G in 2025"},
    {"team": "Washington Nationals", "venue": "Nationals Park", "run_factor": 108, "hr_factor": 104, "hits_factor": 104, "notes": "9.6 R/G in 2025"},
    {"team": "Minnesota Twins", "venue": "Target Field", "run_factor": 107, "hr_factor": 103, "hits_factor": 104, "notes": "9.5 R/G in 2025 — cold early, hot summer"},
    {"team": "Philadelphia Phillies", "venue": "Citizens Bank Park", "run_factor": 107, "hr_factor": 112, "hits_factor": 104, "notes": "Hitter friendly, 9.5 R/G in 2025"},
    {"team": "Toronto Blue Jays", "venue": "Rogers Centre", "run_factor": 107, "hr_factor": 105, "hits_factor": 104, "notes": "Dome, 9.5 R/G in 2025"},
    {"team": "Los Angeles Angels", "venue": "Angel Stadium", "run_factor": 106, "hr_factor": 102, "hits_factor": 103, "notes": "9.5 R/G in 2025"},
    {"team": "Detroit Tigers", "venue": "Comerica Park", "run_factor": 105, "hr_factor": 98, "hits_factor": 103, "notes": "9.3 R/G in 2025 — higher than expected"},
    {"team": "Baltimore Orioles", "venue": "Oriole Park at Camden Yards", "run_factor": 104, "hr_factor": 106, "hits_factor": 102, "notes": "9.2 R/G in 2025"},
    {"team": "New York Mets", "venue": "Citi Field", "run_factor": 103, "hr_factor": 100, "hits_factor": 101, "notes": "9.1 R/G in 2025"},
    {"team": "New York Yankees", "venue": "Yankee Stadium", "run_factor": 101, "hr_factor": 110, "hits_factor": 100, "notes": "HR friendly, short porch in right"},
    {"team": "Atlanta Braves", "venue": "Truist Park", "run_factor": 101, "hr_factor": 101, "hits_factor": 100, "notes": "Neutral park"},
    {"team": "Boston Red Sox", "venue": "Fenway Park", "run_factor": 101, "hr_factor": 100, "hits_factor": 103, "notes": "9.0 R/G in 2025 — less hitter friendly than thought"},
    {"team": "Chicago Cubs", "venue": "Wrigley Field", "run_factor": 100, "hr_factor": 103, "hits_factor": 100, "notes": "Wind dependent, neutral overall in 2025"},
    {"team": "Tampa Bay Rays", "venue": "George M. Steinbrenner Field", "run_factor": 99, "hr_factor": 97, "hits_factor": 99, "notes": "Steinbrenner Field in 2025, 8.8 R/G"},
    {"team": "Cincinnati Reds", "venue": "Great American Ball Park", "run_factor": 98, "hr_factor": 106, "hits_factor": 99, "notes": "8.7 R/G in 2025 — less hitter friendly than reputation"},
    {"team": "Miami Marlins", "venue": "loanDepot Park", "run_factor": 96, "hr_factor": 93, "hits_factor": 96, "notes": "Pitcher friendly, dome"},
    {"team": "Chicago White Sox", "venue": "Rate Field", "run_factor": 96, "hr_factor": 98, "hits_factor": 96, "notes": "8.5 R/G in 2025"},
    {"team": "Milwaukee Brewers", "venue": "American Family Field", "run_factor": 95, "hr_factor": 96, "hits_factor": 96, "notes": "Dome, 8.5 R/G in 2025"},
    {"team": "San Francisco Giants", "venue": "Oracle Park", "run_factor": 95, "hr_factor": 91, "hits_factor": 95, "notes": "Pitcher friendly — marine layer, cold nights"},
    {"team": "St. Louis Cardinals", "venue": "Busch Stadium", "run_factor": 95, "hr_factor": 95, "hits_factor": 96, "notes": "8.4 R/G in 2025"},
    {"team": "Houston Astros", "venue": "Daikin Park", "run_factor": 92, "hr_factor": 95, "hits_factor": 94, "notes": "8.2 R/G in 2025 — renamed from Minute Maid"},
    {"team": "Seattle Mariners", "venue": "T-Mobile Park", "run_factor": 91, "hr_factor": 89, "hits_factor": 93, "notes": "Pitcher friendly — marine layer, 8.1 R/G"},
    {"team": "Cleveland Guardians", "venue": "Progressive Field", "run_factor": 90, "hr_factor": 90, "hits_factor": 92, "notes": "Pitcher friendly, 8.0 R/G in 2025"},
    {"team": "San Diego Padres", "venue": "Petco Park", "run_factor": 89, "hr_factor": 85, "hits_factor": 91, "notes": "Pitcher friendly — marine layer, 7.9 R/G"},
    {"team": "Pittsburgh Pirates", "venue": "PNC Park", "run_factor": 85, "hr_factor": 88, "hits_factor": 90, "notes": "Very pitcher friendly, 7.6 R/G in 2025"},
    {"team": "Kansas City Royals", "venue": "Kauffman Stadium", "run_factor": 84, "hr_factor": 85, "hits_factor": 88, "notes": "Pitcher friendly, 7.5 R/G in 2025"},
    {"team": "Texas Rangers", "venue": "Globe Life Field", "run_factor": 80, "hr_factor": 85, "hits_factor": 86, "notes": "Extreme pitcher park in 2025, only 7.1 R/G"},
]

def upload_park_factors():
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    
    for park in PARK_FACTORS:
        park["updated_at"] = "now()"
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/mlb_park_factors",
            headers=headers,
            json=park
        )
        if response.status_code in [200, 201]:
            print(f"✅ {park['team']} — {park['venue']}")
        else:
            print(f"❌ {park['team']} — {response.status_code}: {response.text}")

if __name__ == "__main__":
    print("Uploading MLB park factors to Supabase...")
    upload_park_factors()
    print("Done!")