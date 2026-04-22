import requests
import os
import time
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

LEAGUE_WOBA = 0.310
WOBA_SCALE = 1.15

def safe_float(val, default=None):
    try:
        f = float(val)
        return round(f, 3) if f == f else default  # NaN check
    except:
        return default

def safe_int(val, default=None):
    try:
        return int(val)
    except:
        return default

def compute_woba_wrc(stat):
    """Compute wOBA + wRC+ approximation from a stat split dict"""
    pa = safe_int(stat.get('plateAppearances'), 0) or 0
    if pa == 0:
        return None, None, None, None
    bb = safe_int(stat.get('baseOnBalls'), 0) or 0
    hbp = safe_int(stat.get('hitByPitch'), 0) or 0
    hits = safe_int(stat.get('hits'), 0) or 0
    doubles = safe_int(stat.get('doubles'), 0) or 0
    triples = safe_int(stat.get('triples'), 0) or 0
    hr = safe_int(stat.get('homeRuns'), 0) or 0
    so = safe_int(stat.get('strikeOuts'), 0) or 0
    ops = safe_float(stat.get('ops'))
    singles = hits - doubles - triples - hr
    woba = round((0.69*bb + 0.72*hbp + 0.89*singles + 1.27*doubles + 1.62*triples + 2.10*hr) / pa, 3)
    wrc_plus = round((woba / LEAGUE_WOBA) * 100) if woba else 100
    k_pct = round((so / pa) * 100, 1)
    return woba, wrc_plus, k_pct, ops

def fetch_team_split(team_id, sit_code, season=2026):
    """Fetch a single split (vr, vl, h, a) for a team"""
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/teams/{team_id}/stats',
            params={
                'stats': 'statSplits',
                'group': 'hitting',
                'season': season,
                'sitCodes': sit_code,
            },
            timeout=10
        )
        data = r.json().get('stats', [])
        if not data or not data[0].get('splits'):
            return None
        stat = data[0]['splits'][0].get('stat', {})
        if not stat or safe_int(stat.get('plateAppearances'), 0) == 0:
            return None
        woba, wrc, k_pct, ops = compute_woba_wrc(stat)
        games = safe_int(stat.get('gamesPlayed'), 0) or 0
        runs = safe_int(stat.get('runs'), 0) or 0
        rpg = round(runs / games, 2) if games > 0 else None
        return {
            'woba': woba,
            'wrc_plus': wrc,
            'k_pct': k_pct,
            'ops': ops,
            'runs_per_game': rpg,
        }
    except Exception:
        return None

def get_team_stats_mlb_api():
    """Fetch team batting stats from MLB Stats API — free, never blocks"""
    print("Fetching team batting stats from MLB Stats API...")
    try:
        # Get all team IDs
        teams_resp = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=15)
        teams = teams_resp.json().get('teams', [])
        print(f"Found {len(teams)} MLB teams")

        results = []
        for team in teams:
            team_id = team['id']
            team_name = team['name']

            try:
                # Fetch team hitting stats for current season
                stats_resp = requests.get(
                    f'https://statsapi.mlb.com/api/v1/teams/{team_id}/stats',
                    params={
                        'stats': 'season',
                        'group': 'hitting',
                        'season': 2026
                    },
                    timeout=15
                )
                stats_data = stats_resp.json().get('stats', [])
                if not stats_data or not stats_data[0].get('splits'):
                    continue

                s = stats_data[0]['splits'][0]['stat']
                games = safe_int(s.get('gamesPlayed'), 0)
                if games == 0:
                    continue

                runs = safe_int(s.get('runs'), 0)
                hits = safe_int(s.get('hits'), 0)
                hr = safe_int(s.get('homeRuns'), 0)
                ab = safe_int(s.get('atBats'), 1)
                bb = safe_int(s.get('baseOnBalls'), 0)
                so = safe_int(s.get('strikeOuts'), 0)
                pa = safe_int(s.get('plateAppearances'), 1)
                doubles = safe_int(s.get('doubles'), 0)
                triples = safe_int(s.get('triples'), 0)

                avg = safe_float(s.get('avg'))
                obp = safe_float(s.get('obp'))
                slg = safe_float(s.get('slg'))
                ops = safe_float(s.get('ops'))

                # Calculate derived stats
                k_pct = round((so / pa) * 100, 1) if pa > 0 else None
                bb_pct = round((bb / pa) * 100, 1) if pa > 0 else None
                iso = round(slg - avg, 3) if slg and avg else None
                babip = round((hits - hr) / (ab - so - hr + 0.001), 3) if (ab - so - hr) > 0 else None

                # wOBA approximation using linear weights
                # wOBA = (0.69*BB + 0.72*HBP + 0.89*1B + 1.27*2B + 1.62*3B + 2.10*HR) / PA
                hbp = safe_int(s.get('hitByPitch'), 0)
                singles = hits - doubles - triples - hr
                woba = round((0.69*bb + 0.72*hbp + 0.89*singles + 1.27*doubles + 1.62*triples + 2.10*hr) / pa, 3) if pa > 0 else None

                # wRC+ approximation — normalize wOBA to league average
                # League avg wOBA ~.310, wOBA scale ~1.15
                league_woba = 0.310
                woba_scale = 1.15
                wrc_plus = round(((((woba - league_woba) / woba_scale) + (runs / pa)) / (runs / pa if runs > 0 else 0.12)) * 100) if woba and pa > 0 else None
                # Simpler wRC+ approximation: (wOBA / league_wOBA) * 100
                if wrc_plus is None or wrc_plus > 200 or wrc_plus < 50:
                    wrc_plus = round((woba / league_woba) * 100) if woba else 100

                # Fetch splits: vs RHP, vs LHP, home, away
                vs_rhp = fetch_team_split(team_id, 'vr')
                time.sleep(0.15)
                vs_lhp = fetch_team_split(team_id, 'vl')
                time.sleep(0.15)
                home_split = fetch_team_split(team_id, 'h')
                time.sleep(0.15)
                away_split = fetch_team_split(team_id, 'a')
                time.sleep(0.15)

                results.append({
                    'team_name': team_name,
                    'games': games,
                    'runs': runs,
                    'avg': avg,
                    'obp': obp,
                    'slg': slg,
                    'ops': ops,
                    'k_pct': k_pct,
                    'bb_pct': bb_pct,
                    'iso': iso,
                    'babip': babip,
                    'woba': woba,
                    'wrc_plus': wrc_plus,
                    'hr': hr,
                    'vs_rhp': vs_rhp,
                    'vs_lhp': vs_lhp,
                    'home_split': home_split,
                    'away_split': away_split,
                })

            except Exception as e:
                print(f"  Error fetching {team_name}: {e}")
                continue

        print(f"Fetched stats for {len(results)} teams")
        return results
    except Exception as e:
        print(f"MLB Stats API error: {e}")
        return None

def upload_team_offense(record):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_team_offense?on_conflict=team",
        headers=headers,
        json=record
    )
    if r.status_code not in [200, 201, 204]:
        print(f"Upload error {r.status_code}: {r.text[:200]}")
        return False
    return True

def run():
    teams = get_team_stats_mlb_api()
    if not teams:
        print("No team stats available")
        return

    success = 0
    errors = 0

    for t in teams:
        try:
            record = {
                "team": t['team_name'],
                "season": 2026,
                "woba": t['woba'],
                "wrc_plus": t['wrc_plus'],
                "k_pct": t['k_pct'],
                "bb_pct": t['bb_pct'],
                "iso": t['iso'],
                "babip": t['babip'],
                "avg": t['avg'],
                "obp": t['obp'],
                "slg": t['slg'],
                "ops": t['ops'],
                "runs_per_game": round(t['runs'] / t['games'], 2) if t['games'] > 0 else None,
                "hr_per_game": round(t['hr'] / t['games'], 3) if t['games'] > 0 else None,
                "games_played": t['games'],
                "updated_at": datetime.now().isoformat()
            }
            # Splits
            if t.get('vs_rhp'):
                record['woba_vs_rhp'] = t['vs_rhp']['woba']
                record['wrc_plus_vs_rhp'] = t['vs_rhp']['wrc_plus']
                record['k_pct_vs_rhp'] = t['vs_rhp']['k_pct']
                record['ops_vs_rhp'] = t['vs_rhp']['ops']
            if t.get('vs_lhp'):
                record['woba_vs_lhp'] = t['vs_lhp']['woba']
                record['wrc_plus_vs_lhp'] = t['vs_lhp']['wrc_plus']
                record['k_pct_vs_lhp'] = t['vs_lhp']['k_pct']
                record['ops_vs_lhp'] = t['vs_lhp']['ops']
            if t.get('home_split'):
                record['ops_home'] = t['home_split']['ops']
                record['runs_per_game_home'] = t['home_split']['runs_per_game']
            if t.get('away_split'):
                record['ops_away'] = t['away_split']['ops']
                record['runs_per_game_away'] = t['away_split']['runs_per_game']

            if upload_team_offense(record):
                success += 1
                print(f"✅ {t['team_name']} — wOBA: {t['woba']}, wRC+: {t['wrc_plus']}, K%: {t['k_pct']}%")
            else:
                errors += 1

        except Exception as e:
            errors += 1
            print(f"Error on {t.get('team_name', '?')}: {e}")

    print(f"\nDone! ✅ {success} teams, ❌ {errors} errors")

if __name__ == "__main__":
    run()
