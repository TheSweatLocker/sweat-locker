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
        print(f"Error fetching 2026 stats: {e}")
        # Try with different pybaseball cache settings
        try:
            import pybaseball
            pybaseball.cache.enable()
            stats = pitching_stats(2026, qual=1)
            print(f"Fetched {len(stats)} pitchers from 2026 (cached)")
            return stats
        except Exception as e1b:
            print(f"Retry failed: {e1b}")
        # Fall back to Baseball Savant Statcast directly
        try:
            from pybaseball import statcast_pitcher_exitvelo_barrels
            print("Trying Baseball Savant Statcast fallback...")
            stats = statcast_pitcher_exitvelo_barrels(2026, minBBE=10)
            if stats is not None and len(stats) > 0:
                print(f"Fetched {len(stats)} pitchers from Statcast")
                return stats
        except Exception as e1c:
            print(f"Statcast fallback failed: {e1c}")
        # Last resort — 2025 data
        try:
            stats = pitching_stats(2025, qual=20)
            print(f"Fetched {len(stats)} pitchers from 2025 fallback")
            return stats
        except Exception as e2:
            print(f"All sources failed: {e2}")
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

def get_first_inning_splits(player_name):
    """Fetch pitcher's 1st inning ERA, WHIP, and batting avg allowed from MLB Stats API"""
    try:
        # Look up player ID
        search_resp = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": player_name, "sportId": 1},
            timeout=10
        )
        people = search_resp.json().get("people", [])
        if not people:
            return None
        player_id = people[0]["id"]

        # Fetch 1st inning situational stats for current season
        stats_resp = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats",
            params={
                "stats": "statSplits",
                "group": "pitching",
                "season": 2026,
                "sitCodes": "i1"  # 1st inning
            },
            timeout=10
        )
        splits = stats_resp.json().get("stats", [])
        if not splits or not splits[0].get("splits"):
            # Try previous season as fallback
            stats_resp = requests.get(
                f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats",
                params={
                    "stats": "statSplits",
                    "group": "pitching",
                    "season": 2025,
                    "sitCodes": "i1"
                },
                timeout=10
            )
            splits = stats_resp.json().get("stats", [])
            if not splits or not splits[0].get("splits"):
                return None

        split_data = splits[0]["splits"][0].get("stat", {})
        innings_pitched = float(split_data.get("inningsPitched", "0") or "0")

        # Need at least 5 first innings to trust the data
        if innings_pitched < 5:
            return None

        era = float(split_data.get("era", "0") or "0")
        whip = float(split_data.get("whip", "0") or "0")
        avg = float(split_data.get("avg", "0") or "0")
        hits = int(split_data.get("hits", 0) or 0)
        strikeouts = int(split_data.get("strikeOuts", 0) or 0)
        walks = int(split_data.get("baseOnBalls", 0) or 0)
        home_runs = int(split_data.get("homeRuns", 0) or 0)

        return {
            "first_inning_era": round(era, 2),
            "first_inning_whip": round(whip, 2),
            "first_inning_avg": round(avg, 3),
            "first_inning_k": strikeouts,
            "first_inning_bb": walks,
            "first_inning_hr": home_runs,
            "first_inning_ip": round(innings_pitched, 1),
        }
    except Exception as e:
        return None

def upload_pitcher(pitcher_data):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    # Use upsert with on_conflict
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?on_conflict=player_name,season",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"
        },
        json=pitcher_data
    )
    if response.status_code not in [200, 201, 204]:
        # Log first few failures for diagnosis
        if not hasattr(upload_pitcher, '_err_count'):
            upload_pitcher._err_count = 0
        upload_pitcher._err_count += 1
        if upload_pitcher._err_count <= 5:
            print(f"  Upload failed {response.status_code}: {response.text[:300]}")
        return False
    return True
def get_todays_starters():
    """Fetch today's probable starters from MLB Stats API"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={'sportId': 1, 'date': today, 'hydrate': 'probablePitcher'},
            timeout=15
        )
        starters = set()
        for d in r.json().get('dates', []):
            for game in d.get('games', []):
                home_p = game.get('teams', {}).get('home', {}).get('probablePitcher', {}).get('fullName')
                away_p = game.get('teams', {}).get('away', {}).get('probablePitcher', {}).get('fullName')
                if home_p: starters.add(home_p)
                if away_p: starters.add(away_p)
        print(f"Today's probable starters: {len(starters)}")
        return starters
    except Exception as e:
        print(f"Error fetching starters: {e}")
        return set()

def build_pitcher_record(row, name, recent_stats, is_fangraphs=True, is_starter=False, is_full_refresh=False):
    """Build pitcher record from either FanGraphs or Statcast data"""
    last5_era = fetch_last5_era(name, recent_stats) if recent_stats is not None else None
    # Handedness + first inning: always on full refresh (Monday), starters-only on daily
    fetch_api = is_starter or is_full_refresh
    throws = get_pitcher_handedness(name) if fetch_api else None
    first_inn = get_first_inning_splits(name) if fetch_api else None

    if is_fangraphs:
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
            "baa_allowed": safe_float(row.get('AVG', row.get('BA')), None),
            "xba_allowed": safe_float(row.get('xBA', row.get('xAVG')), None),
            "hard_hit_pct_allowed": safe_float(row.get('Hard%'), None),
        }
    else:
        # Statcast exit velo/barrels format — different column names
        pitcher = {
            "player_name": name,
            "team": '',
            "throws": throws or 'R',
            "xera": None,  # not in Statcast exit velo data
            "gb_pct": None,
            "fb_pct": None,
            "lob_pct": None,
            "k_pct": None,
            "bb_pct": None,
            "whiff_rate": None,
            "hard_hit_pct": safe_float(row.get('hard_hit_percent', row.get('ev95percent')), None),
            "barrel_pct": safe_float(row.get('brl_percent'), None),
            "avg_fastball_velo": None,
            "last_5_era": last5_era,
            "baa_allowed": safe_float(row.get('ba'), None),
            "xba_allowed": safe_float(row.get('xba'), None),
            "hard_hit_pct_allowed": safe_float(row.get('hard_hit_percent'), None),
        }

    # First inning splits — same for both sources
    pitcher["first_inning_era"] = first_inn["first_inning_era"] if first_inn else None
    pitcher["first_inning_whip"] = first_inn["first_inning_whip"] if first_inn else None
    pitcher["first_inning_avg"] = first_inn["first_inning_avg"] if first_inn else None
    pitcher["first_inning_k"] = first_inn["first_inning_k"] if first_inn else None
    pitcher["first_inning_bb"] = first_inn["first_inning_bb"] if first_inn else None
    pitcher["first_inning_hr"] = first_inn["first_inning_hr"] if first_inn else None
    pitcher["first_inning_ip"] = first_inn["first_inning_ip"] if first_inn else None
    pitcher["season"] = "2026"
    pitcher["updated_at"] = "now()"
    return pitcher

def run():
    # Determine if full refresh or daily starters only — use ET not UTC
    from datetime import timezone
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    is_monday = et_now.weekday() == 0
    print(f"ET day: {et_now.strftime('%A %Y-%m-%d %H:%M')}, is_monday: {is_monday}")
    todays_starters = get_todays_starters()

    stats = fetch_pitcher_stats()
    if stats is None:
        print("Could not fetch pitcher stats")
        return

    # Detect if data is FanGraphs or Statcast format
    is_fangraphs = 'Name' in stats.columns
    name_col = 'Name' if is_fangraphs else 'last_name'

    if not is_fangraphs:
        # Statcast format — build full name from first_name + last_name
        if 'first_name' in stats.columns and 'last_name' in stats.columns:
            stats['full_name'] = stats['first_name'].astype(str) + ' ' + stats['last_name'].astype(str)
            name_col = 'full_name'
        else:
            name_col = 'last_name'
        print(f"Using Statcast format — columns: {list(stats.columns[:10])}")

    recent_stats = fetch_recent_pitcher_stats()
    success = 0
    errors = 0
    skipped = 0

    for _, row in stats.iterrows():
        try:
            name = str(row.get(name_col, ''))
            if not name or name == 'nan':
                continue

            # Check if this pitcher is starting today
            pitcher_last = name.split(' ')[-1].lower()
            is_starter = any(pitcher_last in s.lower() for s in todays_starters) if todays_starters else False

            # Daily runs: only update today's starters (unless Monday = full refresh)
            if not is_monday and todays_starters and not is_starter:
                skipped += 1
                continue

            # Rate limit — lighter since we're processing fewer pitchers
            if (success + errors) % 10 == 0 and (success + errors) > 0:
                time.sleep(0.3)

            pitcher = build_pitcher_record(row, name, recent_stats, is_fangraphs, is_starter, is_monday)
            result = upload_pitcher(pitcher)
            if result:
                success += 1
                if success % 20 == 0:
                    print(f"✅ Uploaded {success} pitchers...")
            else:
                errors += 1

        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"Error on {row.get(name_col, '?')}: {e}")
                traceback.print_exc()
            continue

    print(f"\nDone! ✅ {success} uploaded, ❌ {errors} errors, ⏭️ {skipped} skipped (not starting today)")
    if is_monday:
        print("📋 Full Monday refresh completed")
    else:
        print(f"📋 Daily starters update — {len(todays_starters)} starters targeted")

if __name__ == "__main__":
    run()