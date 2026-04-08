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
    print("Fetching 2026 pitcher stats...")
    # Try FanGraphs via pybaseball first (has xERA, K%, GB%, etc.)
    try:
        stats = pitching_stats(2026, qual=1)
        print(f"Fetched {len(stats)} pitchers from FanGraphs")
        return stats, 'fangraphs'
    except Exception as e:
        print(f"FanGraphs failed: {e}")

    # Fallback: MLB Stats API — free, never blocks, has K%, BB%, ERA, WHIP
    print("Falling back to MLB Stats API...")
    try:
        all_pitchers = []
        teams_resp = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=15)
        teams = teams_resp.json().get('teams', [])
        for team in teams:
            try:
                roster_resp = requests.get(
                    f'https://statsapi.mlb.com/api/v1/teams/{team["id"]}/roster',
                    params={'rosterType': 'active', 'season': 2026},
                    timeout=10
                )
                for player in roster_resp.json().get('roster', []):
                    if player.get('position', {}).get('abbreviation') != 'P':
                        continue
                    pid = player['person']['id']
                    name = player['person']['fullName']
                    try:
                        stats_resp = requests.get(
                            f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                            params={'stats': 'season', 'group': 'pitching', 'season': 2026},
                            timeout=10
                        )
                        splits = stats_resp.json().get('stats', [])
                        if not splits or not splits[0].get('splits'):
                            continue
                        s = splits[0]['splits'][0]['stat']
                        ip = float(s.get('inningsPitched', '0').replace('.1', '.33').replace('.2', '.67') or '0')
                        if ip < 3:
                            continue  # skip pitchers with very few innings
                        pa = int(s.get('battersFaced', 0) or 0)
                        so = int(s.get('strikeOuts', 0) or 0)
                        bb = int(s.get('baseOnBalls', 0) or 0)
                        gb = int(s.get('groundOuts', 0) or 0)
                        fb_outs = int(s.get('airOuts', 0) or 0)
                        total_outs = gb + fb_outs if (gb + fb_outs) > 0 else 1
                        all_pitchers.append({
                            'Name': name,
                            'Team': team.get('abbreviation', ''),
                            'ERA': float(s.get('era', '4.50') or '4.50'),
                            'xERA': None,  # MLB API doesn't have xERA — will be supplemented
                            'K%': round(so / pa, 3) if pa > 0 else 0.20,
                            'BB%': round(bb / pa, 3) if pa > 0 else 0.08,
                            'GB%': round(gb / total_outs, 3) if total_outs > 0 else 0.45,
                            'FB%': round(fb_outs / total_outs, 3) if total_outs > 0 else 0.35,
                            'WHIP': float(s.get('whip', '1.30') or '1.30'),
                            'Hard%': None,
                            'Barrel%': None,
                            'Whiff%': None,
                            'SwStr%': None,
                            'LOB%': None,
                            'FBv': None,
                            'vFB': None,
                            'AVG': float(s.get('avg', '.250') or '.250'),
                            'BA': float(s.get('avg', '.250') or '.250'),
                            'IP': ip,
                        })
                    except:
                        continue
            except:
                continue
            time.sleep(0.2)
        if all_pitchers:
            print(f"Fetched {len(all_pitchers)} pitchers from MLB Stats API")
            return pd.DataFrame(all_pitchers), 'mlb_api'
    except Exception as e:
        print(f"MLB Stats API failed: {e}")

    # Last resort — 2025 FanGraphs data
    try:
        stats = pitching_stats(2025, qual=20)
        print(f"Fetched {len(stats)} pitchers from 2025 fallback")
        return stats, 'fangraphs'
    except Exception as e2:
        print(f"All sources failed: {e2}")
        return None, None

def fetch_savant_xera():
    """Fetch xERA and expected stats directly from Baseball Savant CSV endpoint"""
    try:
        print("Fetching xERA from Baseball Savant...")
        url = "https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year=2026&position=&team=&min=1&csv=true"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=30)
        if r.status_code != 200:
            print(f"  Savant returned {r.status_code}")
            return {}

        import io
        df = pd.read_csv(io.StringIO(r.text))
        print(f"  Fetched {len(df)} pitchers from Baseball Savant")
        print(f"  Savant columns: {list(df.columns[:20])}")
        if len(df) > 0:
            print(f"  Sample row: {dict(df.iloc[0])}")

        # Build lookup — try multiple column name patterns
        xera_map = {}
        for _, row in df.iterrows():
            # Name columns vary: 'last_name', 'player_name', 'last_name, first_name', etc.
            first = str(row.get('first_name', '') or row.get('name_first', '') or '')
            last = str(row.get('last_name', '') or row.get('name_last', '') or '')
            player_name = str(row.get('player_name', '') or '')

            if first and last:
                full_name = f"{first} {last}".strip()
            elif player_name:
                full_name = player_name.strip()
            elif 'last_name, first_name' in row.index:
                combo = str(row.get('last_name, first_name', ''))
                parts = combo.split(', ')
                full_name = f"{parts[1]} {parts[0]}".strip() if len(parts) == 2 else combo
            else:
                continue

            last_name = last.strip().lower() if last else full_name.split(' ')[-1].lower()

            # xERA column varies: 'est_era', 'xera', 'xERA', 'expected_era'
            xera = None
            for col in ['est_era', 'xera', 'xERA', 'expected_era']:
                val = row.get(col)
                if val is not None and str(val) != 'nan' and str(val) != '':
                    try:
                        xera = float(val)
                        break
                    except:
                        pass

            xba = None
            for col in ['est_ba', 'xba', 'xBA', 'expected_ba']:
                val = row.get(col)
                if val is not None and str(val) != 'nan' and str(val) != '':
                    try:
                        xba = float(val)
                        break
                    except:
                        pass

            xwoba = None
            for col in ['est_woba', 'xwoba', 'xwOBA', 'expected_woba']:
                val = row.get(col)
                if val is not None and str(val) != 'nan' and str(val) != '':
                    try:
                        xwoba = float(val)
                        break
                    except:
                        pass

            if full_name and xera is not None:
                try:
                    xera_map[full_name.lower()] = {
                        'xERA': round(float(xera), 2),
                        'xBA': round(float(xba), 3) if xba is not None else None,
                        'xwOBA': round(float(xwoba), 3) if xwoba is not None else None,
                    }
                    # Also key by last name for fuzzy matching
                    if last_name:
                        xera_map[last_name] = xera_map[full_name.lower()]
                except:
                    pass

        print(f"  Built xERA lookup for {len([k for k in xera_map if ' ' in k])} pitchers")
        return xera_map
    except Exception as e:
        print(f"  Baseball Savant xERA fetch failed: {e}")
        return {}

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

    stats, source = fetch_pitcher_stats()
    if stats is None:
        print("Could not fetch pitcher stats")
        return

    # Fetch xERA from Baseball Savant — supplement MLB API data which lacks xERA
    xera_map = {}
    if source == 'mlb_api':
        xera_map = fetch_savant_xera()

    # Detect data format based on source
    is_fangraphs = source == 'fangraphs' or (hasattr(stats, 'columns') and 'Name' in stats.columns)
    name_col = 'Name' if is_fangraphs else 'last_name'

    if source == 'mlb_api':
        # MLB Stats API returns a DataFrame with 'Name' column
        is_fangraphs = True  # same column format as FanGraphs
        name_col = 'Name'
        print(f"Using MLB Stats API format — {len(stats)} pitchers")

    if not is_fangraphs:
        # Statcast format — column names vary by endpoint
        print(f"Statcast columns: {list(stats.columns[:15])}")
        if 'first_name' in stats.columns and 'last_name' in stats.columns:
            stats['full_name'] = stats['first_name'].astype(str) + ' ' + stats['last_name'].astype(str)
            name_col = 'full_name'
        elif 'last_name, first_name' in stats.columns:
            # Combined "Last, First" column — split and reverse
            stats['full_name'] = stats['last_name, first_name'].apply(
                lambda x: ' '.join(reversed(str(x).split(', '))) if ', ' in str(x) else str(x)
            )
            name_col = 'full_name'
        elif 'player_name' in stats.columns:
            name_col = 'player_name'
        else:
            # Last resort — find any column with names
            for col in stats.columns:
                if 'name' in col.lower():
                    name_col = col
                    break
        print(f"Using Statcast format — name column: '{name_col}', sample: {stats[name_col].iloc[0] if name_col in stats.columns else 'NOT FOUND'}")

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

            # Supplement with Baseball Savant xERA if MLB API source
            if xera_map and source == 'mlb_api':
                savant = xera_map.get(name.lower()) or xera_map.get(name.split(' ')[-1].lower())
                if savant and savant.get('xERA'):
                    pitcher['xera'] = savant['xERA']
                    if savant.get('xBA'):
                        pitcher['xba_allowed'] = savant['xBA']

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