"""
Build HR Watch candidates server-side and store in Supabase.

Runs once at 2pm ET (after lineups confirmed) to avoid 170 API calls per app load.
App just queries mlb_hr_watch table — 1 query, instant.

Table schema (create in Supabase):
  CREATE TABLE mlb_hr_watch (
    id BIGSERIAL PRIMARY KEY,
    game_date DATE NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT NOT NULL,
    home_team TEXT NOT NULL,
    matchup TEXT,
    score INT,
    hr INT,
    pa INT,
    hr_rate NUMERIC,
    ba NUMERIC,
    opp_pitcher TEXT,
    opp_xera NUMERIC,
    venue TEXT,
    park_factor INT,
    temp INT,
    wind_speed INT,
    wind_dir TEXT,
    wind_out BOOLEAN,
    opp_hard_hit NUMERIC,
    opp_barrel NUMERIC,
    contact_score INT,
    power_score INT,
    env_score INT,
    hr_bonus INT,
    opp_score INT,
    is_fallback BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
  CREATE INDEX idx_hr_watch_date ON mlb_hr_watch(game_date DESC, score DESC);
"""
import os
import time
import unicodedata
from datetime import datetime, timedelta, timezone
import requests
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


def get_today_et():
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime('%Y-%m-%d')


def strip_accents(s):
    if not s:
        return s
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def fetch_batter_stats(name):
    """Fetch season hitting stats from MLB Stats API"""
    if not name:
        return None
    try:
        search_name = strip_accents(name)
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/people/search',
            params={'names': search_name, 'sportId': 1, 'active': True},
            timeout=10
        )
        people = r.json().get('people', [])
        if not people:
            return None
        pid = people[0]['id']

        sr = requests.get(
            f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
            params={'stats': 'season', 'group': 'hitting', 'season': 2026},
            timeout=10
        )
        splits = sr.json().get('stats', [{}])[0].get('splits', [])
        if not splits:
            return None
        s = splits[0].get('stat', {})
        return {
            'name': name,
            'pa': int(s.get('plateAppearances', 0) or 0),
            'hr': int(s.get('homeRuns', 0) or 0),
            'ba': float(s.get('avg', 0) or 0),
        }
    except Exception as e:
        print(f'  Error fetching {name}: {e}')
        return None


def get_pitcher_contact(pitcher_name):
    """Fetch pitcher contact profile from mlb_pitcher_stats"""
    if not pitcher_name:
        return None
    try:
        last_name = pitcher_name.split()[-1]
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_pitcher_stats'
            f'?player_name=ilike.*{requests.utils.quote(last_name)}*'
            f'&select=player_name,hard_hit_pct_allowed,barrel_pct'
            f'&limit=1',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        )
        data = r.json()
        if data and isinstance(data, list) and data:
            return data[0]
    except:
        pass
    return None


def get_team_fallback(team_name):
    """Fallback to cached team HR threats when lineup missing"""
    try:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_team_hr_threats'
            f'?team=eq.{requests.utils.quote(team_name)}&select=top_hitters',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        )
        data = r.json()
        if data and isinstance(data, list) and data:
            return [h['name'] for h in data[0].get('top_hitters', [])[:5]]
    except:
        pass
    return []


def score_batter(stats, opp_xera, opp_contact, park_factor, temp, wind_speed, wind_dir):
    """Apply the same scoring logic as the app-side HR Watch"""
    if not stats or stats['pa'] < 15:
        return None

    hr_rate = stats['hr'] / stats['pa'] if stats['pa'] > 0 else 0
    if hr_rate < 0.02:
        return None

    power_score = round(hr_rate * 500)
    hr_bonus = 10 if stats['hr'] >= 4 else 0
    opp_score = 15 if opp_xera and opp_xera > 4.5 else (5 if opp_xera and opp_xera > 3.5 else 0)

    contact_score = 0
    if opp_contact:
        hard_hit = float(opp_contact.get('hard_hit_pct_allowed') or 0)
        barrel = float(opp_contact.get('barrel_pct') or 0)
        if hard_hit >= 42: contact_score += 12
        elif hard_hit >= 38: contact_score += 6
        elif 0 < hard_hit <= 30: contact_score -= 5
        if barrel >= 10: contact_score += 10
        elif barrel >= 7: contact_score += 5

    env_score = 0
    if park_factor >= 108: env_score += 15
    elif park_factor >= 103: env_score += 8
    if temp >= 80: env_score += 10
    elif temp >= 70: env_score += 5
    wind_out = wind_speed > 10 and any(d in (wind_dir or '').upper() for d in ['S', 'SW', 'SE', 'OUT'])
    if wind_out: env_score += 12

    total_score = power_score + hr_bonus + opp_score + contact_score + env_score

    return {
        'hr_rate': round(hr_rate, 4),
        'score': total_score,
        'power_score': power_score,
        'hr_bonus': hr_bonus,
        'opp_score': opp_score,
        'contact_score': contact_score,
        'env_score': env_score,
        'wind_out': wind_out,
    }


def run():
    today = get_today_et()
    print(f'Building HR Watch for {today}')

    # Clear today's previous entries
    requests.delete(
        f'{SUPABASE_URL}/rest/v1/mlb_hr_watch?game_date=eq.{today}',
        headers=HEADERS
    )

    # Get today's game contexts
    r = requests.get(
        f'{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{today}&select=*',
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    )
    games = r.json()
    print(f'Games found: {len(games)}')

    candidates = []
    batters_seen = set()  # dedupe if same player appears in multiple games somehow

    for ctx in games:
        home_team = ctx.get('home_team')
        away_team = ctx.get('away_team')
        venue = ctx.get('venue')
        park_factor = float(ctx.get('park_run_factor') or 100)
        temp = float(ctx.get('temperature') or 70)
        wind_speed = float(ctx.get('wind_speed') or 0)
        wind_dir = ctx.get('wind_direction') or ''

        home_xera = float(ctx.get('home_sp_xera') or 4.25) if ctx.get('home_sp_xera') else None
        away_xera = float(ctx.get('away_sp_xera') or 4.25) if ctx.get('away_sp_xera') else None

        # Cache pitcher contact profiles once per game
        home_contact = get_pitcher_contact(ctx.get('home_pitcher'))
        away_contact = get_pitcher_contact(ctx.get('away_pitcher'))

        for side_name, team, opp_xera, opp_contact, opp_pitcher, lineup_str in [
            ('home', home_team, away_xera, away_contact, ctx.get('away_pitcher'), ctx.get('home_lineup')),
            ('away', away_team, home_xera, home_contact, ctx.get('home_pitcher'), ctx.get('away_lineup')),
        ]:
            batters = []
            is_fallback = False

            if lineup_str:
                batters = [b.strip() for b in lineup_str.split(',') if b.strip()][:5]
            else:
                batters = get_team_fallback(team)[:5]
                is_fallback = True

            if not batters:
                continue

            for batter_name in batters:
                if len(batter_name) < 3:
                    continue
                key = f'{team}:{batter_name}'
                if key in batters_seen:
                    continue
                batters_seen.add(key)

                stats = fetch_batter_stats(batter_name)
                if not stats:
                    continue

                scoring = score_batter(stats, opp_xera, opp_contact, park_factor, temp, wind_speed, wind_dir)
                if not scoring or scoring['score'] < 20:
                    continue

                candidates.append({
                    'game_date': today,
                    'player_name': stats['name'],
                    'team': team,
                    'home_team': home_team,
                    'matchup': f'{away_team} @ {home_team}',
                    'score': scoring['score'],
                    'hr': stats['hr'],
                    'pa': stats['pa'],
                    'hr_rate': scoring['hr_rate'],
                    'ba': stats['ba'],
                    'opp_pitcher': opp_pitcher,
                    'opp_xera': opp_xera,
                    'venue': venue,
                    'park_factor': int(park_factor),
                    'temp': int(temp),
                    'wind_speed': int(wind_speed),
                    'wind_dir': wind_dir,
                    'wind_out': scoring['wind_out'],
                    'opp_hard_hit': float(opp_contact.get('hard_hit_pct_allowed')) if opp_contact and opp_contact.get('hard_hit_pct_allowed') else None,
                    'opp_barrel': float(opp_contact.get('barrel_pct')) if opp_contact and opp_contact.get('barrel_pct') else None,
                    'contact_score': scoring['contact_score'],
                    'power_score': scoring['power_score'],
                    'env_score': scoring['env_score'],
                    'hr_bonus': scoring['hr_bonus'],
                    'opp_score': scoring['opp_score'],
                    'is_fallback': is_fallback,
                })

                time.sleep(0.1)  # be polite to MLB API

    candidates.sort(key=lambda c: -c['score'])
    top_n = candidates[:15]  # store more than displayed so app can filter/sort

    print(f'\nTop candidates: {len(top_n)}')
    for c in top_n[:5]:
        print(f'  {c["player_name"]} ({c["team"]}) — {c["score"]} | {c["hr"]} HR/{c["pa"]} PA')

    # Batch upload
    if top_n:
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/mlb_hr_watch',
            headers={**HEADERS, 'Prefer': 'return=minimal'},
            json=top_n
        )
        if r.status_code in (200, 201, 204):
            print(f'\n✅ Stored {len(top_n)} candidates')
        else:
            print(f'\n❌ Upload failed {r.status_code}: {r.text[:200]}')


if __name__ == '__main__':
    run()
