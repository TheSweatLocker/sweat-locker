import requests
import pandas as pd
import traceback
from pybaseball import pitching_stats, pitching_stats_range
import warnings
import os
import time
from dotenv import load_dotenv
from datetime import datetime, timedelta
warnings.filterwarnings('ignore')

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def fetch_pitcher_stats():
    print("Fetching 2026 pitcher stats from Baseball Savant...")
    try:
        stats = pitching_stats(2026, qual=1)
        print(f"Fetched {len(stats)} pitchers from 2026")
        return stats
    except Exception as e:
        print(f"Error fetching 2026 stats, trying 2025: {e}")
        try:
            stats = pitching_stats(2025, qual=20)
            print(f"Fetched {len(stats)} pitchers from 2025")
            return stats
        except Exception as e2:
            print(f"Error: {e2}")
            return None

def fetch_recent_pitcher_stats():
    print("Fetching recent pitcher stats (last 30 days)...")
    try:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        recent = pitching_stats_range(start_date, end_date)
        if recent is None or len(recent) == 0:
            print("No recent stats available yet — early season")
            return None
        print(f"Fetched recent stats for {len(recent)} pitchers")
        return recent
    except Exception as e:
        print(f"Recent stats not available yet: {e}")
        return None

def fetch_last5_era(pitcher_name, recent_stats):
    if recent_stats is None or pitcher_name is None:
        return None
    try:
        match = recent_stats[recent_stats['Name'].str.lower() == pitcher_name.lower()]
        if match.empty:
            match = recent_stats[recent_stats['Name'].str.lower().str.contains(pitcher_name.lower().split(' ')[-1])]
        if match.empty:
            return None
        return float(match.iloc[0].get('ERA', None))
    except:
        return None

def safe_float(val, default):
    try:
        f = float(val)
        return f if not pd.isna(f) else default
    except:
        return default

def get_pitcher_handedness(player_name):
    """Fetch pitcher throwing hand from MLB Stats API"""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={
                "names": player_name,
                "sportId": 1
            }
        )
        data = r.json()
        people = data.get("people", [])
        if not people:
            return None
        person = people[0]
        hand = person.get("pitchHand", {}).get("code", None)
        return hand  # "R" or "L"
    except Exception as e:
        return None

def upload_pitcher(pitcher_data):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    # Use PATCH/upsert pattern
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"
        },
        json=pitcher_data
    )
    if response.status_code == 409:
        # Record exists — update it
        update_resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=eq.{requests.utils.quote(pitcher_data['player_name'])}&season=eq.{pitcher_data['season']}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
            },
            json=pitcher_data
        )
        return update_resp.status_code in [200, 201, 204]
    return response.status_code in [200, 201, 204]
def run():
    stats = fetch_pitcher_stats()
    if stats is None:
        print("Could not fetch pitcher stats")
        return

    recent_stats = fetch_recent_pitcher_stats()
    success = 0
    errors = 0

    for _, row in stats.iterrows():
        try:
            # Small delay every 10 pitchers to avoid MLB Stats API rate limit
            if (success + errors) % 10 == 0:
                time.sleep(0.5)
            name = str(row.get('Name', ''))
            last5_era = fetch_last5_era(name, recent_stats)

            throws = get_pitcher_handedness(name)
            pitcher = {
                "player_name": name,
                "team": str(row.get('Team', '')),
                "throws": throws or 'R',
                "xera": safe_float(row.get('xERA', row.get('ERA')), 4.50),
                "gb_pct": safe_float(row.get('GB%'), 45.0),
                "fb_pct": safe_float(row.get('FB%'), 35.0),
                "lob_pct": safe_float(row.get('LOB%'), 72.0),
                "k_pct": safe_float(row.get('K%'), 20.0),
                "bb_pct": safe_float(row.get('BB%'), 8.0),
                "whiff_rate": safe_float(row.get('Whiff%', row.get('SwStr%')), 10.0),
                "hard_hit_pct": safe_float(row.get('Hard%'), 35.0),
                "barrel_pct": safe_float(row.get('Barrel%', row.get('Barrels')), 6.0),
                "avg_fastball_velo": safe_float(row.get('FBv', row.get('vFB')), 93.0),
                "last_5_era": last5_era if last5_era else safe_float(row.get('ERA'), 4.50),
                # Contact-allowed profile for batter prop evaluation
                "baa_allowed": safe_float(row.get('AVG', row.get('BA')), None),
                "xba_allowed": safe_float(row.get('xBA', row.get('xAVG')), None),
                "hard_hit_pct_allowed": safe_float(row.get('Hard%'), None),
                "season": "2026",
                "updated_at": "now()"
            }

            result = upload_pitcher(pitcher)
            if result:
                success += 1
                if success % 50 == 0:
                    print(f"✅ Uploaded {success} pitchers...")
            else:
                errors += 1

        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"Error on {name}: {e}")
            continue

    print(f"\nDone! ✅ {success} uploaded, ❌ {errors} errors")
    if recent_stats is not None:
        print(f"Recent form data available for {len(recent_stats)} pitchers")

if __name__ == "__main__":
    run()