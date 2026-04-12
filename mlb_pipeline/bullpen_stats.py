import requests
import os
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def get_team_bullpen_stats(season=2026):
    """Fetch team bullpen ERA from MLB Stats API"""
    print(f"Fetching bullpen stats for {season} season...")
    try:
        # Get all teams
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": season}
        )
        teams = r.json().get("teams", [])
        print(f"Found {len(teams)} teams")
        return teams
    except Exception as e:
        print(f"Error fetching teams: {e}")
        return []

def get_bullpen_era(team_id, team_name, season=2026):
    """Get bullpen ERA for a specific team"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats",
            params={
                "stats": "season",
                "group": "pitching",
                "season": season,
                "playerPool": "qualifier"
            }
        )
        data = r.json()
        stats = data.get("stats", [])
        if not stats or not stats[0].get("splits"):
            return None
        
        # Get team totals
        team_stats = stats[0]["splits"][0]["stat"]
        era = float(team_stats.get("era", 4.50))
        saves = int(team_stats.get("saves", 0))
        blown_saves = int(team_stats.get("blownSaves", 0))
        holds = int(team_stats.get("holds", 0))
        
        return {
            "team": team_name,
            "bullpen_era": era,
            "saves": saves,
            "blown_saves": blown_saves,
            "holds": holds,
            "save_pct": round(saves / (saves + blown_saves) * 100, 1) if (saves + blown_saves) > 0 else 0,
            "season": str(season),
            "updated_at": "now()"
        }
    except Exception as e:
        return None

def upload_bullpen(data):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_bullpen_stats?on_conflict=team,season",
        headers=headers,
        json=data
    )
    if r.status_code not in [200, 201, 204]:
        print(f"  Upload error {r.status_code}: {r.text[:200]}")
        return False
    return True

def run():
    teams = get_team_bullpen_stats(2026)
    if not teams:
        print("Trying 2025...")
        teams = get_team_bullpen_stats(2025)
    
    if not teams:
        print("No teams found")
        return

    success = 0
    errors = 0

    for team in teams:
        team_id = team.get("id")
        team_name = team.get("name")
        if not team_id or not team_name:
            continue
        
        season = 2026
        stats = get_bullpen_era(team_id, team_name, season)
        if not stats:
            season = 2025
            stats = get_bullpen_era(team_id, team_name, season)
        
        if stats:
            if upload_bullpen(stats):
                print(f"✅ {team_name} — Bullpen ERA: {stats['bullpen_era']}, Save%: {stats['save_pct']}%")
                success += 1
            else:
                print(f"❌ {team_name} — upload failed")
                errors += 1
        else:
            print(f"⚠️ {team_name} — no stats available")

    print(f"\nDone! ✅ {success} teams, ❌ {errors} errors")

if __name__ == "__main__":
    run()