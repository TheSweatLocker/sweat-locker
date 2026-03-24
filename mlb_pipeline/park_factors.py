import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# 2026 MLB Park Factors
# Run factor > 100 = hitter friendly, < 100 = pitcher friendly
PARK_FACTORS = [
    {"team": "Colorado Rockies", "venue": "Coors Field", "run_factor": 118, "hr_factor": 123, "hits_factor": 112, "notes": "Extreme hitter park — mile high altitude"},
    {"team": "Cincinnati Reds", "venue": "Great American Ball Park", "run_factor": 108, "hr_factor": 114, "hits_factor": 105, "notes": "Hitter friendly, short fences"},
    {"team": "Philadelphia Phillies", "venue": "Citizens Bank Park", "run_factor": 107, "hr_factor": 112, "hits_factor": 104, "notes": "Hitter friendly"},
    {"team": "Boston Red Sox", "venue": "Fenway Park", "run_factor": 106, "hr_factor": 104, "hits_factor": 108, "notes": "Hitter friendly, Green Monster affects hits"},
    {"team": "Houston Astros", "venue": "Minute Maid Park", "run_factor": 105, "hr_factor": 108, "hits_factor": 103, "notes": "Slight hitter advantage"},
    {"team": "Chicago Cubs", "venue": "Wrigley Field", "run_factor": 104, "hr_factor": 107, "hits_factor": 103, "notes": "Wind dependent — blowing out = over lean"},
    {"team": "Texas Rangers", "venue": "Globe Life Field", "run_factor": 103, "hr_factor": 106, "hits_factor": 102, "notes": "Slight hitter advantage"},
    {"team": "Baltimore Orioles", "venue": "Camden Yards", "run_factor": 102, "hr_factor": 105, "hits_factor": 101, "notes": "Slight hitter advantage"},
    {"team": "Toronto Blue Jays", "venue": "Rogers Centre", "run_factor": 101, "hr_factor": 103, "hits_factor": 100, "notes": "Neutral to slight hitter"},
    {"team": "Atlanta Braves", "venue": "Truist Park", "run_factor": 100, "hr_factor": 101, "hits_factor": 100, "notes": "Neutral park"},
    {"team": "Los Angeles Angels", "venue": "Angel Stadium", "run_factor": 99, "hr_factor": 98, "hits_factor": 100, "notes": "Neutral to slight pitcher"},
    {"team": "Minnesota Twins", "venue": "Target Field", "run_factor": 98, "hr_factor": 97, "hits_factor": 99, "notes": "Slight pitcher advantage — cold weather early season"},
    {"team": "Cleveland Guardians", "venue": "Progressive Field", "run_factor": 97, "hr_factor": 96, "hits_factor": 98, "notes": "Pitcher friendly"},
    {"team": "Kansas City Royals", "venue": "Kauffman Stadium", "run_factor": 97, "hr_factor": 95, "hits_factor": 98, "notes": "Pitcher friendly, large outfield"},
    {"team": "Seattle Mariners", "venue": "T-Mobile Park", "run_factor": 96, "hr_factor": 94, "hits_factor": 97, "notes": "Pitcher friendly — marine layer suppresses offense"},
    {"team": "Tampa Bay Rays", "venue": "Tropicana Field", "run_factor": 96, "hr_factor": 95, "hits_factor": 97, "notes": "Pitcher friendly, dome"},
    {"team": "Chicago White Sox", "venue": "Guaranteed Rate Field", "run_factor": 96, "hr_factor": 98, "hits_factor": 96, "notes": "Pitcher friendly overall"},
    {"team": "Miami Marlins", "venue": "loanDepot Park", "run_factor": 95, "hr_factor": 93, "hits_factor": 96, "notes": "Pitcher friendly, dome"},
    {"team": "Pittsburgh Pirates", "venue": "PNC Park", "run_factor": 95, "hr_factor": 94, "hits_factor": 96, "notes": "Pitcher friendly"},
    {"team": "San Francisco Giants", "venue": "Oracle Park", "run_factor": 94, "hr_factor": 91, "hits_factor": 95, "notes": "Pitcher friendly — marine layer, cold nights"},
    {"team": "Oakland Athletics", "venue": "Sutter Health Park", "run_factor": 94, "hr_factor": 93, "hits_factor": 95, "notes": "Pitcher friendly"},
    {"team": "Detroit Tigers", "venue": "Comerica Park", "run_factor": 93, "hr_factor": 90, "hits_factor": 95, "notes": "Pitcher friendly, deep outfield"},
    {"team": "Los Angeles Dodgers", "venue": "Dodger Stadium", "run_factor": 93, "hr_factor": 92, "hits_factor": 94, "notes": "Pitcher friendly"},
    {"team": "San Diego Padres", "venue": "Petco Park", "run_factor": 92, "hr_factor": 89, "hits_factor": 93, "notes": "Pitcher friendly — marine layer"},
    {"team": "New York Mets", "venue": "Citi Field", "run_factor": 95, "hr_factor": 94, "hits_factor": 96, "notes": "Neutral to slight pitcher"},
    {"team": "New York Yankees", "venue": "Yankee Stadium", "run_factor": 102, "hr_factor": 110, "hits_factor": 100, "notes": "HR friendly, short porch in right"},
    {"team": "St. Louis Cardinals", "venue": "Busch Stadium", "run_factor": 96, "hr_factor": 95, "hits_factor": 97, "notes": "Slight pitcher advantage"},
    {"team": "Milwaukee Brewers", "venue": "American Family Field", "run_factor": 97, "hr_factor": 96, "hits_factor": 98, "notes": "Neutral, dome"},
    {"team": "Arizona Diamondbacks", "venue": "Chase Field", "run_factor": 100, "hr_factor": 100, "hits_factor": 100, "notes": "Neutral, dome"},
    {"team": "Washington Nationals", "venue": "Nationals Park", "run_factor": 98, "hr_factor": 97, "hits_factor": 99, "notes": "Slight pitcher advantage"},
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