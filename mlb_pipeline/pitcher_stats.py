import requests
import pandas as pd
from pybaseball import pitching_stats, statcast_pitcher_arsenal_stats
import warnings
warnings.filterwarnings('ignore')

SUPABASE_URL = "https://vctzbruocrjiojtmpjlw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZjdHpicnVvY3JqaW9qdG1wamx3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM1MzYyNDgsImV4cCI6MjA4OTExMjI0OH0.tRebBZpsKS4qTmK5AwkuguVFGWMZlpjXz5Hz4rFQIw0"

def fetch_pitcher_stats():
    print("Fetching 2026 pitcher stats from Baseball Savant...")
    try:
        # Get season pitching stats
        stats = pitching_stats(2026, qual=20)
        print(f"Fetched {len(stats)} pitchers")
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

def upload_pitcher(pitcher_data):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats",
        headers=headers,
        json=pitcher_data
    )
    return response.status_code in [200, 201]

def run():
    stats = fetch_pitcher_stats()
    if stats is None:
        print("Could not fetch pitcher stats")
        return

    success = 0
    errors = 0
    
    for _, row in stats.iterrows():
        try:
            # Map pybaseball columns to our schema
            pitcher = {
                "player_name": str(row.get('Name', '')),
                "team": str(row.get('Team', '')),
                "xera": float(row.get('xERA', row.get('ERA', 4.50))),
                "k_pct": float(row.get('K%', 0)) * 100 if row.get('K%', 0) < 1 else float(row.get('K%', 0)),
                "bb_pct": float(row.get('BB%', 0)) * 100 if row.get('BB%', 0) < 1 else float(row.get('BB%', 0)),
                "whiff_rate": float(row.get('Whiff%', row.get('SwStr%', 0))) * 100 if row.get('Whiff%', row.get('SwStr%', 0)) < 1 else float(row.get('Whiff%', row.get('SwStr%', 0))),
                "hard_hit_pct": float(row.get('Hard%', 35.0)),
                "barrel_pct": float(row.get('Barrel%', row.get('Barrels', 6.0))),
                "avg_fastball_velo": float(row.get('FBv', row.get('vFB', 93.0))),
                "last_5_era": float(row.get('ERA', 4.50)),
                "season": "2026",
                "updated_at": "now()"
            }
            
            if upload_pitcher(pitcher):
                success += 1
                if success % 50 == 0:
                    print(f"✅ Uploaded {success} pitchers...")
            else:
                errors += 1
                
        except Exception as e:
            errors += 1
            continue
    
    print(f"\nDone! ✅ {success} uploaded, ❌ {errors} errors")

if __name__ == "__main__":
    run()