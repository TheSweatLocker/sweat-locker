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

def fetch_team_last10(team_id, season=2026):
    """Pull team's last 10 games and compute recency offense + defense averages.

    Returns dict with last10_runs_per_game, last10_runs_allowed, last10_run_diff,
    games_sampled. None if no game logs available.

    Used to weight projection toward recent form: a team scoring 5.5 R/G
    season-long but 3.2 R/G last 10 should NOT project the same as a hot team.
    Blended into game_context.py spread/total formula at 0.35 weight.
    """
    try:
        # Hitting gameLog → runs scored
        h = requests.get(
            f'https://statsapi.mlb.com/api/v1/teams/{team_id}/stats',
            params={'stats': 'gameLog', 'group': 'hitting', 'season': season},
            timeout=15
        ).json()
        h_splits = h.get('stats', [])
        if not h_splits or not h_splits[0].get('splits'):
            return None
        h_rows = h_splits[0]['splits']
        last10_h = h_rows[-10:] if len(h_rows) >= 10 else h_rows
        if not last10_h:
            return None
        runs_scored = [int(s.get('stat', {}).get('runs', 0) or 0) for s in last10_h]

        # Pitching gameLog → runs allowed
        p = requests.get(
            f'https://statsapi.mlb.com/api/v1/teams/{team_id}/stats',
            params={'stats': 'gameLog', 'group': 'pitching', 'season': season},
            timeout=15
        ).json()
        p_splits = p.get('stats', [])
        runs_allowed = []
        if p_splits and p_splits[0].get('splits'):
            p_rows = p_splits[0]['splits']
            last10_p = p_rows[-10:] if len(p_rows) >= 10 else p_rows
            runs_allowed = [int(s.get('stat', {}).get('runs', 0) or 0) for s in last10_p]

        n = len(runs_scored)
        if n == 0:
            return None
        rpg = round(sum(runs_scored) / n, 2)
        rapg = round(sum(runs_allowed) / len(runs_allowed), 2) if runs_allowed else None
        diff = round(rpg - rapg, 2) if rapg is not None else None
        return {
            'last10_runs_per_game': rpg,
            'last10_runs_allowed': rapg,
            'last10_run_diff': diff,
            'last10_games_sampled': n,
        }
    except Exception as e:
        print(f"  last10 fetch error team {team_id}: {e}")
        return None


def fetch_team_inning_buckets(team_id, season=2026):
    """Fetch team hitting inning splits and aggregate to 1-3, 4-6, 7-9 buckets.
    Returns runs_per_game, ops, k_pct, wrc_plus, hr_per_game per bucket."""
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/teams/{team_id}/stats',
            params={
                'stats': 'statSplits',
                'group': 'hitting',
                'season': season,
                'sitCodes': 'i01,i02,i03,i04,i05,i06,i07,i08,i09,ig07'
            },
            timeout=15
        )
        data = r.json().get('stats', [])
        if not data or not data[0].get('splits'):
            return None
        rows = {}
        for s in data[0]['splits']:
            code = s.get('split', {}).get('code')
            if code:
                rows[code] = s.get('stat', {})

        def aggregate(codes):
            pa = ab = runs = hits = doubles = triples = hr = bb = hbp = so = 0
            games_played_max = 0  # use the max games_played across innings as bucket denominator
            for code in codes:
                if code not in rows:
                    continue
                stat = rows[code]
                pa += safe_int(stat.get('plateAppearances'), 0) or 0
                ab += safe_int(stat.get('atBats'), 0) or 0
                runs += safe_int(stat.get('runs'), 0) or 0
                hits += safe_int(stat.get('hits'), 0) or 0
                doubles += safe_int(stat.get('doubles'), 0) or 0
                triples += safe_int(stat.get('triples'), 0) or 0
                hr += safe_int(stat.get('homeRuns'), 0) or 0
                bb += safe_int(stat.get('baseOnBalls'), 0) or 0
                hbp += safe_int(stat.get('hitByPitch'), 0) or 0
                so += safe_int(stat.get('strikeOuts'), 0) or 0
                gp = safe_int(stat.get('gamesPlayed'), 0) or 0
                if gp > games_played_max:
                    games_played_max = gp
            if pa < 10 or games_played_max == 0:
                return None
            singles = hits - doubles - triples - hr
            obp_num = hits + bb + hbp
            obp_den = ab + bb + hbp
            obp = round(obp_num / obp_den, 3) if obp_den else None
            slg = round((singles + 2*doubles + 3*triples + 4*hr) / ab, 3) if ab else None
            ops = round((obp or 0) + (slg or 0), 3) if obp is not None and slg is not None else None
            woba = round((0.69*bb + 0.72*hbp + 0.89*singles + 1.27*doubles + 1.62*triples + 2.10*hr) / pa, 3)
            wrc_plus = round((woba / LEAGUE_WOBA) * 100) if woba else 100
            k_pct = round((so / pa) * 100, 1)
            bb_pct = round((bb / pa) * 100, 1) if pa else None
            return {
                'runs_per_game': round(runs / games_played_max, 2),
                'hr_per_game': round(hr / games_played_max, 3),
                'ops': ops,
                'wrc_plus': wrc_plus,
                'k_pct': k_pct,
                'bb_pct': bb_pct,
                'pa': pa,
                'games': games_played_max,
            }

        bucket_1_3 = aggregate(['i01', 'i02', 'i03'])
        bucket_4_6 = aggregate(['i04', 'i05', 'i06'])
        # 7-9 bucket: prefer ig07 (innings 7+) — single split with more reliable PA volume
        bucket_7_9 = aggregate(['ig07']) if 'ig07' in rows else aggregate(['i07', 'i08', 'i09'])
        # Per-inning: 1st inning specifically (NRFI-relevant — leadoff hitters
        # skew the bucket avg, so 1st inning R/G alone is the sharper number)
        inning_1_only = aggregate(['i01'])
        return {
            'innings_1_3': bucket_1_3,
            'innings_4_6': bucket_4_6,
            'innings_7_9': bucket_7_9,
            'inning_1_only': inning_1_only,
        }
    except Exception as e:
        print(f"  Inning bucket fetch error for team {team_id}: {e}")
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
                inning_buckets = fetch_team_inning_buckets(team_id)
                time.sleep(0.15)
                last10 = fetch_team_last10(team_id)
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
                    'inning_buckets': inning_buckets,
                    'last10': last10,
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
            # Last-10 recency (data-collection only for now; not blended into
            # projection until we backtest the optimal weight against resolved
            # games — see project memo on L10 backtest plan)
            l10 = t.get('last10')
            if l10:
                record['last10_runs_per_game'] = l10['last10_runs_per_game']
                record['last10_runs_allowed'] = l10['last10_runs_allowed']
                record['last10_run_diff'] = l10['last10_run_diff']
                record['last10_games_sampled'] = l10['last10_games_sampled']
            # Inning bucket splits (offense in 1-3, 4-6, 7-9)
            ib = t.get('inning_buckets')
            if ib:
                for label in ('innings_1_3', 'innings_4_6', 'innings_7_9'):
                    bucket = ib.get(label)
                    if not bucket:
                        continue
                    record[f'{label}_runs_per_game'] = bucket['runs_per_game']
                    record[f'{label}_hr_per_game'] = bucket['hr_per_game']
                    record[f'{label}_ops'] = bucket['ops']
                    record[f'{label}_wrc_plus'] = bucket['wrc_plus']
                    record[f'{label}_k_pct'] = bucket['k_pct']
                    record[f'{label}_bb_pct'] = bucket['bb_pct']
                # Per-inning: 1st inning only (NRFI-relevant)
                inn1 = ib.get('inning_1_only')
                if inn1:
                    record['inning_1_runs_per_game'] = inn1['runs_per_game']
                    record['inning_1_hr_per_game'] = inn1['hr_per_game']
                    record['inning_1_ops'] = inn1['ops']
                    record['inning_1_wrc_plus'] = inn1['wrc_plus']
                    record['inning_1_k_pct'] = inn1['k_pct']
                    record['inning_1_bb_pct'] = inn1['bb_pct']

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
