"""
Backfill handedness splits + pitcher last-3-starts onto historical 2026 mlb_game_context rows.
Point-in-time: team splits use byDateRange through game_date, pitcher last 3 filters gameLog before game_date.
"""
import requests
import os
import time
import sys
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

SEASON = 2026
SEASON_START = f"{SEASON}-03-27"

LEAGUE_WOBA = 0.310

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def safe_int(val, default=0):
    try:
        return int(val)
    except:
        return default

def safe_float(val, default=None):
    try:
        f = float(val)
        return round(f, 3) if f == f else default
    except:
        return default

def get_team_id_map():
    r = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=15)
    return {t['name']: t['id'] for t in r.json().get('teams', [])}

def fetch_team_split_asof(team_id, sit_code, end_date):
    """Fetch team split through end_date (exclusive) via byDateRange"""
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/teams/{team_id}/stats',
            params={
                'stats': 'byDateRange',
                'group': 'hitting',
                'season': SEASON,
                'sitCodes': sit_code,
                'startDate': SEASON_START,
                'endDate': end_date,
            },
            timeout=10
        )
        data = r.json().get('stats', [])
        if not data or not data[0].get('splits'):
            return None
        # byDateRange may return multiple splits; take the matching sitCode or first
        stat = data[0]['splits'][0].get('stat', {})
        pa = safe_int(stat.get('plateAppearances'), 0)
        if pa < 20:
            return None
        bb = safe_int(stat.get('baseOnBalls'), 0)
        hbp = safe_int(stat.get('hitByPitch'), 0)
        hits = safe_int(stat.get('hits'), 0)
        doubles = safe_int(stat.get('doubles'), 0)
        triples = safe_int(stat.get('triples'), 0)
        hr = safe_int(stat.get('homeRuns'), 0)
        so = safe_int(stat.get('strikeOuts'), 0)
        ops = safe_float(stat.get('ops'))
        singles = hits - doubles - triples - hr
        woba = round((0.69*bb + 0.72*hbp + 0.89*singles + 1.27*doubles + 1.62*triples + 2.10*hr) / pa, 3)
        wrc_plus = round((woba / LEAGUE_WOBA) * 100) if woba else 100
        k_pct = round((so / pa) * 100, 1) if pa > 0 else None
        return {'woba': woba, 'wrc_plus': wrc_plus, 'k_pct': k_pct, 'ops': ops}
    except Exception:
        return None

def fetch_pitcher_last_3_before(pitcher_name, before_date):
    """Fetch pitcher's last 3 starts strictly before before_date"""
    try:
        sr = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": pitcher_name, "sportId": 1},
            timeout=10
        )
        people = sr.json().get("people", [])
        if not people:
            return None
        pid = people[0]["id"]
        gl = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
            params={"stats": "gameLog", "group": "pitching", "season": SEASON},
            timeout=10
        )
        splits = gl.json().get("stats", [])
        if not splits or not splits[0].get("splits"):
            return None
        games = splits[0]["splits"]
        starts = [g for g in games if (g.get("stat", {}).get("gamesStarted") or 0) == 1 and g.get("date", "") < before_date]
        starts.sort(key=lambda g: g.get("date", ""), reverse=True)
        last_3 = starts[:3]
        if len(last_3) == 0:
            return None
        total_er = sum(int(g["stat"].get("earnedRuns", 0) or 0) for g in last_3)
        total_ip = 0.0
        for g in last_3:
            ip_str = str(g["stat"].get("inningsPitched", "0") or "0")
            if "." in ip_str:
                w, f = ip_str.split(".")
                total_ip += int(w) + (int(f) / 3)
            else:
                total_ip += float(ip_str)
        total_so = sum(int(g["stat"].get("strikeOuts", 0) or 0) for g in last_3)
        total_bf = sum(int(g["stat"].get("battersFaced", 0) or 0) for g in last_3)
        era = round((total_er * 9) / total_ip, 2) if total_ip > 0 else None
        k_pct = round((total_so / total_bf) * 100, 1) if total_bf > 0 else None
        return {"era": era, "k_pct": k_pct, "ip": round(total_ip, 1)}
    except Exception:
        return None

def get_pitcher_hand(pitcher_name):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=ilike.{requests.utils.quote('*'+pitcher_name+'*')}&select=throws&limit=1",
        headers=supabase_headers(),
        timeout=10
    )
    try:
        data = r.json()
        return (data[0].get('throws') if data else None) or 'R'
    except:
        return 'R'

def fetch_games_to_backfill():
    """Pull 2026 training rows from mlb_game_results where enrichment is NULL"""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_results"
        f"?game_date=gte.{SEASON}-01-01"
        f"&game_date=lt.{SEASON + 1}-01-01"
        f"&or=(home_wrc_vs_opp_hand.is.null,home_pitcher_last_3_era.is.null)"
        f"&select=game_id,game_date,home_team,away_team,home_sp_name,away_sp_name"
        f"&order=game_date.asc"
        f"&limit=1000",
        headers=supabase_headers(),
        timeout=30
    )
    return r.json() if r.status_code == 200 else []

def update_game(game_id, payload):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/mlb_game_results?game_id=eq.{requests.utils.quote(game_id)}",
        headers=supabase_headers(),
        json=payload,
        timeout=15
    )
    return r.status_code in (200, 204)

def run():
    team_ids = get_team_id_map()
    games = fetch_games_to_backfill()
    print(f"Found {len(games)} games to backfill")
    if not games:
        return

    # Cache team splits by (team, date) — many games share dates
    split_cache = {}

    def get_split(team_name, date_iso, sit_code):
        key = (team_name, date_iso, sit_code)
        if key in split_cache:
            return split_cache[key]
        tid = team_ids.get(team_name)
        if not tid:
            split_cache[key] = None
            return None
        result = fetch_team_split_asof(tid, sit_code, date_iso)
        time.sleep(0.15)
        split_cache[key] = result
        return result

    success = 0
    errors = 0
    for i, g in enumerate(games):
        try:
            gdate = g['game_date']
            home = g['home_team']
            away = g['away_team']
            home_sp = g.get('home_sp_name')
            away_sp = g.get('away_sp_name')

            if not home_sp or not away_sp:
                continue

            home_hand = get_pitcher_hand(home_sp)
            away_hand = get_pitcher_hand(away_sp)

            # Home batters face away pitcher's hand
            home_sit = 'vr' if away_hand == 'R' else 'vl'
            away_sit = 'vr' if home_hand == 'R' else 'vl'

            home_split = get_split(home, gdate, home_sit)
            away_split = get_split(away, gdate, away_sit)

            home_last_3 = fetch_pitcher_last_3_before(home_sp, gdate)
            time.sleep(0.1)
            away_last_3 = fetch_pitcher_last_3_before(away_sp, gdate)
            time.sleep(0.1)

            payload = {
                "home_wrc_vs_opp_hand": home_split['wrc_plus'] if home_split else None,
                "away_wrc_vs_opp_hand": away_split['wrc_plus'] if away_split else None,
                "home_ops_vs_opp_hand": home_split['ops'] if home_split else None,
                "away_ops_vs_opp_hand": away_split['ops'] if away_split else None,
                "home_pitcher_last_3_era": home_last_3['era'] if home_last_3 else None,
                "away_pitcher_last_3_era": away_last_3['era'] if away_last_3 else None,
                "home_pitcher_last_3_k_pct": home_last_3['k_pct'] if home_last_3 else None,
                "away_pitcher_last_3_k_pct": away_last_3['k_pct'] if away_last_3 else None,
            }

            if update_game(g['game_id'], payload):
                success += 1
                if success % 10 == 0:
                    print(f"  [{success}/{len(games)}] {gdate} {away} @ {home} — home wRC+ vs {away_hand}HP: {payload['home_wrc_vs_opp_hand']}, {home_sp} L3 ERA: {payload['home_pitcher_last_3_era']}")
            else:
                errors += 1

        except Exception as e:
            errors += 1
            print(f"  error on {g.get('game_id', '?')}: {e}")
            continue

    print(f"\nDone. ✅ {success} / ❌ {errors}")

if __name__ == "__main__":
    run()
