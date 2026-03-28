import requests
import os
from dotenv import load_dotenv
import time

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

NBA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.nba.com/',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'x-nba-stats-origin': 'stats',
    'x-nba-stats-token': 'true',
    'Origin': 'https://www.nba.com',
    'Connection': 'keep-alive',
    'Host': 'stats.nba.com',
}

def get_team_stats(measure_type='Advanced', last_n=0, season='2025-26'):
    try:
        r = requests.get(
            'https://stats.nba.com/stats/leaguedashteamstats',
            headers=NBA_HEADERS,
            params={
                'Conference': '', 'DateFrom': '', 'DateTo': '',
                'Division': '', 'GameScope': '', 'GameSegment': '',
                'LastNGames': last_n, 'LeagueID': '00', 'Location': '',
                'MeasureType': measure_type, 'Month': 0, 'OpponentTeamID': 0,
                'Outcome': '', 'PORound': 0, 'PaceAdjust': 'N',
                'PerMode': 'PerGame', 'Period': 0, 'PlayerExperience': '',
                'PlayerPosition': '', 'PlusMinus': 'N', 'Rank': 'N',
                'Season': season, 'SeasonSegment': '', 'SeasonType': 'Regular Season',
                'ShotClockRange': '', 'StarterBench': '', 'TeamID': 0,
                'TwoWay': 0, 'VsConference': '', 'VsDivision': '',
            },
            timeout=60
        )
        data = r.json()
        headers = data['resultSets'][0]['headers']
        rows = data['resultSets'][0]['rowSet']
        print(f"Fetched {len(rows)} teams ({measure_type}, last {last_n} games)")
        return headers, rows
    except Exception as e:
        print(f"Error: {e}")
        return None, None

def upload_team(team_data):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/nba_team_stats",
        headers=headers,
        json=team_data
    )
    if r.status_code not in [200, 201, 204]:
        print(f"Upload error {r.status_code}: {r.text[:200]}")
        return False
    return True

def run():
    season = '2025-26'

    # Clear existing data
    requests.delete(
        f"{SUPABASE_URL}/rest/v1/nba_team_stats?id=neq.00000000-0000-0000-0000-000000000000",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Prefer": "return=minimal"
        }
    )
    print("Cleared existing NBA stats")

    adv_headers, adv_rows = get_team_stats('Advanced', 0, season)
    time.sleep(2)
    base_headers, base_rows = get_team_stats('Base', 0, season)
    time.sleep(2)
    l10_headers, l10_rows = get_team_stats('Advanced', 10, season)

    if not adv_headers or not adv_rows:
        print("Failed to fetch stats")
        return

    base_map = {}
    if base_headers and base_rows:
        idx = base_headers.index('TEAM_NAME')
        for row in base_rows:
            base_map[row[idx]] = dict(zip(base_headers, row))

    l10_map = {}
    if l10_headers and l10_rows:
        idx = l10_headers.index('TEAM_NAME')
        for row in l10_rows:
            l10_map[row[idx]] = dict(zip(l10_headers, row))

    success = 0
    errors = 0

    for row in adv_rows:
        try:
            adv = dict(zip(adv_headers, row))
            team_name = adv['TEAM_NAME']
            base = base_map.get(team_name, {})
            l10 = l10_map.get(team_name, {})

            team_data = {
                "team": team_name,
                "offensive_rating": float(adv.get('OFF_RATING', 110)),
                "defensive_rating": float(adv.get('DEF_RATING', 110)),
                "net_rating": float(adv.get('NET_RATING', 0)),
                "pace": float(adv.get('PACE', 98)),
                "efg_pct": float(adv.get('EFG_PCT', 0.52)) * 100,
                "ts_pct": float(adv.get('TS_PCT', 0.56)) * 100,
                "tov_pct": float(adv.get('TM_TOV_PCT', 13)),
                "oreb_pct": float(adv.get('OREB_PCT', 25)) * 100,
                "ft_rate": float(adv.get('FTA_RATE', 0.25)) * 100,
                "wins": int(base.get('W', 0)),
                "losses": int(base.get('L', 0)),
                "last_10_net_rating": float(l10.get('NET_RATING', 0)) if l10 else None,
                "season": season,
                "updated_at": "now()"
            }

            if upload_team(team_data):
                success += 1
                print(f"✅ {team_name} — Net: {team_data['net_rating']:+.1f}, Pace: {team_data['pace']:.1f}")
            else:
                errors += 1
                print(f"❌ {team_name}")

        except Exception as e:
            errors += 1
            print(f"Error on {row}: {e}")

    print(f"\nDone! ✅ {success} teams, ❌ {errors} errors")

if __name__ == "__main__":
    run()