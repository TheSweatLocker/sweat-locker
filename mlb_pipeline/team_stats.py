import requests
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# MLB team name mapping from pybaseball to full names
TEAM_NAME_MAP = {
    'ARI': 'Arizona Diamondbacks',
    'ATL': 'Atlanta Braves',
    'BAL': 'Baltimore Orioles',
    'BOS': 'Boston Red Sox',
    'CHC': 'Chicago Cubs',
    'CHW': 'Chicago White Sox',
    'CIN': 'Cincinnati Reds',
    'CLE': 'Cleveland Guardians',
    'COL': 'Colorado Rockies',
    'DET': 'Detroit Tigers',
    'HOU': 'Houston Astros',
    'KCR': 'Kansas City Royals',
    'LAA': 'Los Angeles Angels',
    'LAD': 'Los Angeles Dodgers',
    'MIA': 'Miami Marlins',
    'MIL': 'Milwaukee Brewers',
    'MIN': 'Minnesota Twins',
    'NYM': 'New York Mets',
    'NYY': 'New York Yankees',
    'OAK': 'Athletics',
    'ATH': 'Athletics',
    'PHI': 'Philadelphia Phillies',
    'PIT': 'Pittsburgh Pirates',
    'SDP': 'San Diego Padres',
    'SEA': 'Seattle Mariners',
    'SFG': 'San Francisco Giants',
    'STL': 'St. Louis Cardinals',
    'TBR': 'Tampa Bay Rays',
    'TEX': 'Texas Rangers',
    'TOR': 'Toronto Blue Jays',
    'WSN': 'Washington Nationals',
}

def get_team_woba_wrc(season=2025):
    """Fetch team wOBA and wRC+ from pybaseball — uses 2025 as baseline until 2026 accumulates"""
    try:
        import pybaseball
        pybaseball.cache.enable()
        print(f"Fetching team batting stats for {season}...")
        df = pybaseball.team_batting(season)
        print(f"Got {len(df)} team rows from pybaseball")
        return df
    except Exception as e:
        print(f"Error fetching pybaseball data: {e}")
        return None

def upload_team_offense(record):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_team_offense",
        headers=headers,
        json=record
    )
    if r.status_code not in [200, 201, 204]:
        print(f"Upload error {r.status_code}: {r.text[:200]}")
        return False
    return True

def run():
    # Use 2025 as baseline — switch to 2026 after 15+ games played
    season = 2025

    df = get_team_woba_wrc(season)
    if df is None or len(df) == 0:
        print("No data returned from pybaseball")
        return

    print(f"Columns available: {list(df.columns)}")

    success = 0
    errors = 0

    for _, row in df.iterrows():
        try:
            team_abbr = str(row.get('Team', '')).strip()
            if not team_abbr or team_abbr == 'nan':
                continue

            full_name = TEAM_NAME_MAP.get(team_abbr)
            if not full_name:
                print(f"  Unknown team abbreviation: {team_abbr}")
                continue

            games = int(row.get('G', 0))
            runs = float(row.get('R', 0))

            record = {
                "team": full_name,
                "season": season,
                "woba": round(float(row.get('wOBA', 0)), 3) if row.get('wOBA') else None,
                "wrc_plus": int(row.get('wRC+', 100)) if row.get('wRC+') else None,
                "k_pct": round(float(str(row.get('K%', '0')).replace('%', '')) * (100 if float(str(row.get('K%', '0')).replace('%', '')) < 1 else 1), 1) if row.get('K%') else None,
                "bb_pct": round(float(str(row.get('BB%', '0')).replace('%', '')) * (100 if float(str(row.get('BB%', '0')).replace('%', '')) < 1 else 1), 1) if row.get('BB%') else None,
                "iso": round(float(row.get('ISO', 0)), 3) if row.get('ISO') else None,
                "babip": round(float(row.get('BABIP', 0)), 3) if row.get('BABIP') else None,
                "avg": round(float(row.get('AVG', 0)), 3) if row.get('AVG') else None,
                "obp": round(float(row.get('OBP', 0)), 3) if row.get('OBP') else None,
                "slg": round(float(row.get('SLG', 0)), 3) if row.get('SLG') else None,
                "ops": round(float(row.get('OPS', 0)), 3) if row.get('OPS') else None,
                "runs_per_game": round(runs / games, 2) if games > 0 else None,
                "hr_per_game": round(float(row.get('HR', 0)) / games, 3) if games > 0 else None,
                "games_played": games,
                "updated_at": datetime.now().isoformat()
            }

            if upload_team_offense(record):
                success += 1
                print(f"✅ {full_name} — wOBA: {record['woba']}, wRC+: {record['wrc_plus']}, K%: {record['k_pct']}%")
            else:
                errors += 1
                print(f"❌ {full_name}")

        except Exception as e:
            errors += 1
            print(f"Error on {row.get('Team', 'unknown')}: {e}")

    print(f"\nDone! ✅ {success} teams, ❌ {errors} errors")

if __name__ == "__main__":
    run()