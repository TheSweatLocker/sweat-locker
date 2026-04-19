"""
Cache each team's top HR threats daily.

Pulls active roster + season stats from MLB Stats API,
ranks position players by HR rate (HR/PA), and stores top 6 per team
in Supabase `mlb_team_hr_threats` table.

Runs once daily in the pipeline. HR Watch uses this as fallback
when batting lineups aren't confirmed yet.

Table schema (create in Supabase):
  CREATE TABLE mlb_team_hr_threats (
    team TEXT PRIMARY KEY,
    top_hitters JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
  );
"""
import requests
import os
import time
import unicodedata
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates,return=minimal',
}

# MLB team ID → name mapping (using MLB Stats API IDs)
MLB_TEAMS = {
    108: 'Los Angeles Angels', 109: 'Arizona Diamondbacks', 110: 'Baltimore Orioles',
    111: 'Boston Red Sox', 112: 'Chicago Cubs', 113: 'Cincinnati Reds',
    114: 'Cleveland Guardians', 115: 'Colorado Rockies', 116: 'Detroit Tigers',
    117: 'Houston Astros', 118: 'Kansas City Royals', 119: 'Los Angeles Dodgers',
    120: 'Washington Nationals', 121: 'New York Mets', 133: 'Athletics',
    134: 'Pittsburgh Pirates', 135: 'San Diego Padres', 136: 'Seattle Mariners',
    137: 'San Francisco Giants', 138: 'St. Louis Cardinals', 139: 'Tampa Bay Rays',
    140: 'Texas Rangers', 141: 'Toronto Blue Jays', 142: 'Minnesota Twins',
    143: 'Philadelphia Phillies', 144: 'Atlanta Braves', 145: 'Chicago White Sox',
    146: 'Miami Marlins', 147: 'New York Yankees', 158: 'Milwaukee Brewers',
}


def strip_accents(s):
    if not s:
        return s
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def get_player_hitting_stats(player_id, season=2026):
    """Fetch season hitting stats for a single player"""
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/people/{player_id}/stats',
            params={'stats': 'season', 'group': 'hitting', 'season': season},
            timeout=10
        )
        splits = r.json().get('stats', [{}])[0].get('splits', [])
        if not splits:
            return None
        s = splits[0].get('stat', {})
        pa = int(s.get('plateAppearances', 0) or 0)
        hr = int(s.get('homeRuns', 0) or 0)
        ba = float(s.get('avg', 0) or 0)
        ops = float(s.get('ops', 0) or 0)
        return {'pa': pa, 'hr': hr, 'ba': ba, 'ops': ops}
    except:
        return None


def get_team_top_hitters(team_id, team_name, top_n=6):
    """Fetch team roster and return top N position players by HR rate"""
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/teams/{team_id}/roster',
            params={'season': 2026, 'rosterType': 'active'},
            timeout=15
        )
        roster = r.json().get('roster', [])
        position_players = [
            p for p in roster
            if p.get('position', {}).get('abbreviation') not in ('P', 'TWP')
        ]

        candidates = []
        for p in position_players:
            pid = p.get('person', {}).get('id')
            name = p.get('person', {}).get('fullName')
            position = p.get('position', {}).get('abbreviation')
            if not pid or not name:
                continue

            stats = get_player_hitting_stats(pid)
            if not stats or stats['pa'] < 10:
                continue

            hr_rate = stats['hr'] / stats['pa'] if stats['pa'] > 0 else 0
            candidates.append({
                'name': strip_accents(name),
                'position': position,
                'hr': stats['hr'],
                'pa': stats['pa'],
                'ba': round(stats['ba'], 3),
                'ops': round(stats['ops'], 3),
                'hr_rate': round(hr_rate, 4),
            })
            time.sleep(0.1)  # be polite to the API

        # Sort by HR rate descending, take top N
        candidates.sort(key=lambda x: -x['hr_rate'])
        return candidates[:top_n]
    except Exception as e:
        print(f'  {team_name}: error — {e}')
        return []


def upload_team_threats(team_name, hitters):
    """Upsert team's top hitters to Supabase"""
    from datetime import datetime, timezone
    payload = {
        'team': team_name,
        'top_hitters': hitters,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f'{SUPABASE_URL}/rest/v1/mlb_team_hr_threats?on_conflict=team',
        headers=HEADERS,
        json=payload
    )
    return r.status_code in (200, 201, 204)


def run():
    print('Fetching top HR threats for all 30 MLB teams...')
    success = 0
    errors = 0
    for team_id, team_name in MLB_TEAMS.items():
        hitters = get_team_top_hitters(team_id, team_name, top_n=6)
        if not hitters:
            errors += 1
            continue
        if upload_team_threats(team_name, hitters):
            success += 1
            top3 = ', '.join(f"{h['name']} ({h['hr']} HR)" for h in hitters[:3])
            print(f'  ✅ {team_name}: {top3}')
        else:
            errors += 1
            print(f'  ❌ {team_name}: upload failed')
    print(f'\nDone! ✅ {success} teams uploaded, ❌ {errors} errors')


if __name__ == '__main__':
    run()
