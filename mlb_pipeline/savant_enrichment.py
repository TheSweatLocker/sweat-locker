"""
Savant enrichment — fetches 3 CSVs per daily cron:
1. Team defense (OAA, DRS)
2. Team expected offense (xwOBA, xSLG, barrel%, hard hit%)
3. Catcher framing runs

Upserts to mlb_team_offense + mlb_catcher_framing.
Runs after team_stats.py, before game_context.py.
"""
import io
import os
import requests
import pandas as pd
import warnings
from datetime import datetime
from dotenv import load_dotenv

warnings.filterwarnings('ignore')
load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SEASON = 2026

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}

# Savant team name → MLB team name map (Savant uses abbreviations/short names)
SAVANT_TEAM_MAP = {
    "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves", "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox", "CHC": "Chicago Cubs", "CWS": "Chicago White Sox",
    "CHW": "Chicago White Sox", "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies", "DET": "Detroit Tigers", "HOU": "Houston Astros",
    "KC":  "Kansas City Royals", "KCR": "Kansas City Royals", "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers", "MIA": "Miami Marlins", "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins", "NYM": "New York Mets", "NYY": "New York Yankees",
    "OAK": "Athletics", "ATH": "Athletics", "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates", "SD":  "San Diego Padres", "SDP": "San Diego Padres",
    "SEA": "Seattle Mariners", "SF":  "San Francisco Giants", "SFG": "San Francisco Giants",
    "STL": "St. Louis Cardinals", "TB":  "Tampa Bay Rays", "TBR": "Tampa Bay Rays",
    "TEX": "Texas Rangers", "TOR": "Toronto Blue Jays", "WSH": "Washington Nationals",
    "WAS": "Washington Nationals",
}

def safe_float(v):
    try:
        f = float(v)
        return round(f, 3) if f == f else None
    except:
        return None

def safe_int(v):
    try:
        return int(float(v))
    except:
        return None

def normalize_team(raw):
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw in SAVANT_TEAM_MAP:
        return SAVANT_TEAM_MAP[raw]
    # Already full name
    return raw

def fetch_csv(url, label):
    try:
        print(f"Fetching {label}...")
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=30)
        if r.status_code != 200:
            print(f"  ⚠️ {label} returned {r.status_code}")
            return None
        df = pd.read_csv(io.StringIO(r.text))
        print(f"  Fetched {len(df)} rows. Columns: {list(df.columns[:15])}")
        return df
    except Exception as e:
        print(f"  ⚠️ {label} fetch failed: {e}")
        return None

def fetch_team_defense():
    """Team-level OAA from Savant defensive runs leaderboard"""
    # Team OAA leaderboard — position=all returns team totals
    urls = [
        f"https://baseballsavant.mlb.com/leaderboard/outs_above_average?type=Team&startYear={SEASON}&endYear={SEASON}&split=no&team=&range=year&min=0&pos=all&roles=&viz=hide&csv=true",
        f"https://baseballsavant.mlb.com/leaderboard/outs_above_average?type=Team&startYear={SEASON}&endYear={SEASON}&pos=&csv=true",
        f"https://baseballsavant.mlb.com/leaderboard/outs_above_average?type=Team&year={SEASON}&pos=&csv=true",
    ]
    df = None
    for url in urls:
        df = fetch_csv(url, f"Team OAA {SEASON}")
        if df is not None and len(df) > 0:
            break
    if df is None or len(df) == 0:
        return {}

    team_defense = {}
    for _, row in df.iterrows():
        # Team column name varies: 'team', 'team_name', 'entity_name', 'name'
        team_raw = None
        for col in ['entity_name', 'team_name', 'team', 'name', 'display_team_name']:
            if col in row.index and pd.notna(row[col]):
                team_raw = row[col]
                break
        if not team_raw:
            continue
        team_full = normalize_team(team_raw)

        oaa = None
        for col in ['outs_above_average', 'oaa', 'outs_above_avg']:
            if col in row.index and pd.notna(row[col]):
                oaa = safe_int(row[col])
                break
        games = safe_int(row.get('games_played') or row.get('g') or row.get('gp') or 0) or 0
        oaa_pg = round(oaa / games, 3) if oaa is not None and games > 0 else None

        team_defense[team_full] = {"oaa": oaa, "oaa_per_game": oaa_pg}

    print(f"  → Parsed OAA for {len(team_defense)} teams")
    return team_defense

def fetch_team_expected_offense():
    """Team-level expected stats (xwOBA, barrel%, hard hit%)"""
    # Team-aggregated expected stats
    urls = [
        f"https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter-team&year={SEASON}&position=&team=&min=q&csv=true",
        f"https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=team&year={SEASON}&csv=true",
    ]
    df = None
    for url in urls:
        df = fetch_csv(url, f"Team expected offense {SEASON}")
        if df is not None and len(df) > 0:
            break
    if df is None or len(df) == 0:
        return {}

    team_expected = {}
    for _, row in df.iterrows():
        team_raw = None
        for col in ['team_name', 'team', 'entity_name', 'name', 'display_team_name']:
            if col in row.index and pd.notna(row[col]):
                team_raw = row[col]
                break
        if not team_raw:
            continue
        team_full = normalize_team(team_raw)

        xwoba = None
        for col in ['est_woba', 'xwoba', 'xwOBA', 'expected_woba']:
            if col in row.index and pd.notna(row[col]):
                xwoba = safe_float(row[col])
                break
        xslg = None
        for col in ['est_slg', 'xslg', 'xSLG', 'expected_slg']:
            if col in row.index and pd.notna(row[col]):
                xslg = safe_float(row[col])
                break

        team_expected[team_full] = {"xwoba": xwoba, "xslg": xslg}

    print(f"  → Parsed xwOBA/xSLG for {len(team_expected)} teams")
    return team_expected

def fetch_team_quality_of_contact():
    """Team barrel% + hard hit% from Savant exit velocity leaderboard"""
    urls = [
        f"https://baseballsavant.mlb.com/leaderboard/statcast?type=batter-team&year={SEASON}&min=q&csv=true",
        f"https://baseballsavant.mlb.com/leaderboard/statcast?year={SEASON}&type=team&csv=true",
    ]
    df = None
    for url in urls:
        df = fetch_csv(url, f"Team quality of contact {SEASON}")
        if df is not None and len(df) > 0:
            break
    if df is None or len(df) == 0:
        return {}

    team_qoc = {}
    for _, row in df.iterrows():
        team_raw = None
        for col in ['team_name', 'team', 'entity_name', 'name']:
            if col in row.index and pd.notna(row[col]):
                team_raw = row[col]
                break
        if not team_raw:
            continue
        team_full = normalize_team(team_raw)

        barrel = None
        for col in ['brl_percent', 'barrel_percent', 'barrel_pct', 'brl_pa', 'brl_pct']:
            if col in row.index and pd.notna(row[col]):
                barrel = safe_float(row[col])
                break
        hard_hit = None
        for col in ['ev95percent', 'hard_hit_percent', 'hard_hit_pct', 'ev95_pct']:
            if col in row.index and pd.notna(row[col]):
                hard_hit = safe_float(row[col])
                break

        team_qoc[team_full] = {"barrel_pct": barrel, "hard_hit_pct": hard_hit}

    print(f"  → Parsed barrel%/hard hit% for {len(team_qoc)} teams")
    return team_qoc

def fetch_catcher_framing():
    """Catcher framing runs per catcher"""
    urls = [
        f"https://baseballsavant.mlb.com/leaderboard/services/catcher-framing?year={SEASON}&min=q&csv=true",
        f"https://baseballsavant.mlb.com/catcher_framing?year={SEASON}&csv=true",
        f"https://baseballsavant.mlb.com/leaderboard/catcher-framing?year={SEASON}&csv=true",
    ]
    df = None
    for url in urls:
        df = fetch_csv(url, f"Catcher framing {SEASON}")
        if df is not None and len(df) > 0:
            break
    if df is None or len(df) == 0:
        return []

    catchers = []
    for _, row in df.iterrows():
        # Name columns — 'name' can be "Firstname Lastname" directly
        name = None
        if 'last_name, first_name' in row.index and pd.notna(row['last_name, first_name']):
            parts = str(row['last_name, first_name']).split(', ')
            if len(parts) == 2:
                name = f"{parts[1]} {parts[0]}".strip()
        if not name:
            for col in ['player_name', 'name', 'full_name']:
                if col in row.index and pd.notna(row[col]):
                    name = str(row[col]).strip()
                    break
        if not name:
            continue

        team_raw = None
        for col in ['team_name', 'team', 'team_abbrev']:
            if col in row.index and pd.notna(row[col]):
                team_raw = row[col]
                break
        team_full = normalize_team(team_raw) if team_raw else None

        # rv_tot = total runs value from framing, pct_tot = strike rate, pitches = called pitches
        framing = None
        for col in ['rv_tot', 'runs_extra_strikes', 'framing_runs', 'runs_extra_strikes_plus']:
            if col in row.index and pd.notna(row[col]):
                framing = safe_float(row[col])
                break
        strike_rate = None
        for col in ['pct_tot', 'strike_rate', 'strike_rate_all', 'strike_pct']:
            if col in row.index and pd.notna(row[col]):
                strike_rate = safe_float(row[col])
                break
        innings = None
        for col in ['pitches', 'n_called_pitches', 'innings_caught', 'innings', 'ip']:
            if col in row.index and pd.notna(row[col]):
                innings = safe_float(row[col])
                break

        catchers.append({
            "player_name": name,
            "team": team_full,
            "season": SEASON,
            "framing_runs": framing,
            "strike_rate": strike_rate,
            "innings_caught": innings,
            "updated_at": datetime.utcnow().isoformat(),
        })

    print(f"  → Parsed framing for {len(catchers)} catchers")
    return catchers

def upsert_team_offense_merge(team_full, payload):
    """PATCH Savant fields onto existing mlb_team_offense row — don't upsert
    the whole record because the underlying table uses on_conflict=team only
    and id/season round-trip can cause 400s."""
    patch_payload = {k: v for k, v in payload.items() if v is not None}
    patch_payload["updated_at"] = datetime.now().isoformat()
    up = requests.patch(
        f"{SUPABASE_URL}/rest/v1/mlb_team_offense?team=eq.{requests.utils.quote(team_full)}",
        headers=HEADERS,
        json=patch_payload,
        timeout=10
    )
    if up.status_code not in (200, 204):
        if not hasattr(upsert_team_offense_merge, '_err_once'):
            upsert_team_offense_merge._err_once = True
            print(f"  ⚠️ team upsert failed {up.status_code}: {up.text[:200]}")
        return False
    return True

def upsert_catcher(catcher):
    up = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_catcher_framing?on_conflict=player_name,season",
        headers=HEADERS,
        json=catcher,
        timeout=10
    )
    if up.status_code not in (200, 201, 204):
        if not hasattr(upsert_catcher, '_err_once'):
            upsert_catcher._err_once = True
            print(f"  ⚠️ catcher upsert failed {up.status_code}: {up.text[:200]}")
        return False
    return True

def run():
    print(f"=== Savant enrichment {SEASON} ===")

    defense = fetch_team_defense()
    expected = fetch_team_expected_offense()
    qoc = fetch_team_quality_of_contact()
    catchers = fetch_catcher_framing()

    # Merge team data and upsert
    all_teams = set(defense.keys()) | set(expected.keys()) | set(qoc.keys())
    team_ok, team_fail = 0, 0
    for team in all_teams:
        payload = {}
        if team in defense:
            payload.update({k: v for k, v in defense[team].items() if v is not None})
        if team in expected:
            payload.update({k: v for k, v in expected[team].items() if v is not None})
        if team in qoc:
            payload.update({k: v for k, v in qoc[team].items() if v is not None})
        if not payload:
            continue
        if upsert_team_offense_merge(team, payload):
            team_ok += 1
        else:
            team_fail += 1
    print(f"Team defense/expected: ✅ {team_ok} / ❌ {team_fail}")

    # Upsert catchers
    c_ok, c_fail = 0, 0
    for c in catchers:
        if c.get("framing_runs") is None:
            continue
        if upsert_catcher(c):
            c_ok += 1
        else:
            c_fail += 1
    print(f"Catcher framing: ✅ {c_ok} / ❌ {c_fail}")

if __name__ == "__main__":
    run()
