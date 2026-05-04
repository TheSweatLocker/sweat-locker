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


# Park HR factors — diverge meaningfully from park run factors.
# Sources: FanGraphs / Statcast park HR factor data (3-year averages).
# Higher = HRs more frequent at this venue. League average = 100.
PARK_HR_FACTOR = {
    'Coors Field':                123,  # extreme HR park (altitude)
    'Great American Ball Park':   118,
    'Yankee Stadium':             115,  # short porch (LH bias not modeled here)
    'Citizens Bank Park':         112,
    'Wrigley Field':              108,  # wind-dependent but HR-friendly when out
    'Globe Life Field':           107,
    'Camden Yards':               104,  # post-2022 LF wall changes
    'Rogers Centre':              104,
    'Truist Park':                103,
    'Chase Field':                102,
    'Comerica Park':              102,
    'American Family Field':      102,
    'Target Field':               101,
    'Citi Field':                 100,
    'Angel Stadium':              100,
    'Nationals Park':             100,
    'Fenway Park':                 99,  # high run factor but Green Monster suppresses HR
    'PNC Park':                    98,
    'Kauffman Stadium':            98,
    'Daikin Park':                 97,  # Astros (renamed from Minute Maid)
    'Minute Maid Park':            97,
    'Progressive Field':           96,
    'Busch Stadium':               96,
    'loanDepot Park':              94,  # deep gaps + retractable roof
    'Sutter Health Park':          93,  # A's temp Sacramento home
    'T-Mobile Park':               92,  # marine layer suppresses HR
    'George M. Steinbrenner Field':92,  # Rays temp Tampa home
    'Dodger Stadium':              91,
    'Oracle Park':                 88,  # cold + deep gaps
    'Petco Park':                  88,  # spacious + marine layer
}


def park_hr_factor(venue):
    """Return park HR factor (100 = league avg). Falls back to 100."""
    if not venue:
        return 100
    return PARK_HR_FACTOR.get(venue, 100)


def strip_accents(s):
    if not s:
        return s
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def fetch_batter_stats(name):
    """Fetch season hitting stats + bat side + last-7 game pace from MLB Stats API.
    Adds slg + iso (process signals more stable than HR/PA in small samples).
    Adds bat_side (L/R/S) for platoon scoring.
    Adds last_7_hr / last_7_pa to penalize cold streaks (season totals miss recent slumps)."""
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
        person = people[0]
        pid = person['id']
        bat_side = person.get('batSide', {}).get('code')  # 'L' / 'R' / 'S'

        sr = requests.get(
            f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
            params={'stats': 'season', 'group': 'hitting', 'season': 2026, 'sportId': 1},
            timeout=10
        )
        splits = sr.json().get('stats', [{}])[0].get('splits', [])
        if not splits:
            return None
        # Filter to MLB-only splits (sportId=1 is the param but some responses still
        # include MiLB rows — defensively pick the MLB split if multiple exist).
        mlb_split = next(
            (sp for sp in splits if sp.get('sport', {}).get('id') == 1
             or sp.get('league', {}).get('sport', {}).get('id') == 1),
            splits[0]
        )
        s = mlb_split.get('stat', {})
        ba = float(s.get('avg', 0) or 0)
        slg = float(s.get('slg', 0) or 0)
        iso = max(0.0, slg - ba)

        # Last-7 games pace — gameLog stats sorted by date desc, take last 7
        last_7_hr = 0
        last_7_pa = 0
        try:
            gl = requests.get(
                f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                params={'stats': 'gameLog', 'group': 'hitting', 'season': 2026, 'sportId': 1},
                timeout=10
            )
            gsplits = gl.json().get('stats', [{}])[0].get('splits', [])
            # Filter to MLB games only — guards against MiLB rehab/option stints
            mlb_games = [g for g in gsplits
                         if g.get('sport', {}).get('id') == 1
                         or g.get('league', {}).get('sport', {}).get('id') == 1]
            # If response was already MLB-only, mlb_games may be empty due to
            # missing nested fields; fall back to all splits.
            recent = (mlb_games or gsplits)[-7:]
            for g in recent:
                gs = g.get('stat', {})
                last_7_hr += int(gs.get('homeRuns', 0) or 0)
                last_7_pa += int(gs.get('plateAppearances', 0) or 0)
        except Exception:
            pass

        return {
            'name': name,
            'pa': int(s.get('plateAppearances', 0) or 0),
            'hr': int(s.get('homeRuns', 0) or 0),
            'ba': ba,
            'slg': slg,
            'iso': round(iso, 3),
            'bat_side': bat_side,
            'last_7_hr': last_7_hr,
            'last_7_pa': last_7_pa,
        }
    except Exception as e:
        print(f'  Error fetching {name}: {e}')
        return None


def get_pitcher_contact(pitcher_name):
    """Fetch pitcher contact profile from mlb_pitcher_stats.

    Now also returns fb_pct (HR risk proxy — flyball pitchers give up
    more HRs than groundball pitchers for the same xERA) and throws
    (handedness for platoon scoring)."""
    if not pitcher_name:
        return None
    try:
        last_name = pitcher_name.split()[-1]
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_pitcher_stats'
            f'?player_name=ilike.*{requests.utils.quote(last_name)}*'
            f'&select=player_name,hard_hit_pct_allowed,barrel_pct,fb_pct,gb_pct,throws'
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


def score_batter(stats, opp_xera, opp_contact, park_factor, hr_park, temp, wind_speed, wind_dir):
    """HR Watch scoring with stronger small-sample regression + recency, platoon, FB-rate signals.

    BAYESIAN REGRESSION on HR rate (added 2026-05-02, prior strengthened
    2026-05-04): raw HR/PA over small samples wildly inflates power_score
    for hot small-sample hitters (e.g. Rushing 7 HR / 52 PA = 13.5%).
    PRIOR_PA bumped 200 → 400 because Rushing kept ranking #1 even after
    his sample grew — at 200 prior, mid-sample hot hitters still beat
    full-season legitimate threats. 400 forces meaningful evidence.

    NEW SIGNALS (2026-05-04):
      - FB-rate score: flyball pitchers (fb_pct ≥ .40) give up more HRs
        than groundballers at the same xERA. Direct HR-risk signal.
      - L7 cold-streak penalty: 0 HR over last 25+ PA = -10. Catches
        slumps that season totals miss.
      - Platoon score: opp-handed matchup gets +5, same-handed -3.
        Switch-hitters always opp-handed (treated as +5).
    """
    # Bumped PA threshold from 15 to 40 — anything under is small-sample noise.
    if not stats or stats['pa'] < 40:
        return None

    raw_hr_rate = stats['hr'] / stats['pa'] if stats['pa'] > 0 else 0
    if raw_hr_rate < 0.02:
        return None

    # Bayesian-regress BOTH HR rate AND ISO. Stronger PRIOR_PA (400) so
    # mid-sample hot hitters can't dominate full-season legit threats.
    PRIOR_HR_RATE = 0.03
    PRIOR_ISO     = 0.155
    PRIOR_PA      = 400
    hr_rate = (stats['hr'] + PRIOR_HR_RATE * PRIOR_PA) / (stats['pa'] + PRIOR_PA)
    raw_iso = stats.get('iso', PRIOR_ISO)
    iso = (raw_iso * stats['pa'] + PRIOR_ISO * PRIOR_PA) / (stats['pa'] + PRIOR_PA)

    # Power score blends regressed HR rate + regressed ISO.
    power_from_rate = hr_rate * 250
    power_from_iso  = iso * 150
    power_score = round(power_from_rate + power_from_iso)
    hr_bonus = 10 if stats['hr'] >= 4 else 0
    opp_score = 15 if opp_xera and opp_xera > 4.5 else (5 if opp_xera and opp_xera > 3.5 else 0)

    contact_score = 0
    fb_score = 0
    pitcher_throws = None
    if opp_contact:
        hard_hit = float(opp_contact.get('hard_hit_pct_allowed') or 0)
        barrel = float(opp_contact.get('barrel_pct') or 0)
        if hard_hit >= 42: contact_score += 12
        elif hard_hit >= 38: contact_score += 6
        elif 0 < hard_hit <= 30: contact_score -= 5
        if barrel >= 10: contact_score += 10
        elif barrel >= 7: contact_score += 5

        # FB-rate HR risk — flyball pitchers allow far more HRs.
        # mlb_pitcher_stats.fb_pct is sometimes stored as fraction (0.35) and
        # sometimes as percent (35.0) depending on data source path. Normalize.
        fb_pct = opp_contact.get('fb_pct')
        if fb_pct is not None:
            try:
                fb = float(fb_pct)
                if fb > 1.0: fb = fb / 100.0  # normalize percent → fraction
                if fb >= 0.40: fb_score += 8     # extreme flyball pitcher
                elif fb >= 0.35: fb_score += 4
                elif 0 < fb <= 0.30: fb_score -= 5  # heavy GB pitcher suppresses HR
            except:
                pass

        pitcher_throws = (opp_contact.get('throws') or '').upper() or None

    env_score = 0
    # Park HR factor — replaces park_run_factor for HR-specific scoring.
    # Coors 123 / Petco 88 spans much wider than run factor.
    if hr_park >= 115: env_score += 18      # extreme HR park
    elif hr_park >= 108: env_score += 12    # plus HR park
    elif hr_park >= 103: env_score += 6
    elif hr_park <= 92:  env_score -= 8     # pitcher park penalty
    elif hr_park <= 95:  env_score -= 4
    if temp >= 80: env_score += 10
    elif temp >= 70: env_score += 5
    wind_out = wind_speed > 10 and any(d in (wind_dir or '').upper() for d in ['S', 'SW', 'SE', 'OUT'])
    if wind_out: env_score += 12

    # Platoon score — switch hitters always count as opp-handed.
    bat_side = (stats.get('bat_side') or '').upper()
    platoon_score = 0
    if bat_side and pitcher_throws:
        if bat_side == 'S':
            platoon_score = 5
        elif bat_side != pitcher_throws:
            platoon_score = 5
        else:
            platoon_score = -3

    # Recency cold-streak penalty — slumps that season totals don't show.
    # Need a meaningful recent sample (≥25 PA) to call it cold.
    last_7_pa = int(stats.get('last_7_pa') or 0)
    last_7_hr = int(stats.get('last_7_hr') or 0)
    recency_score = 0
    if last_7_pa >= 25 and last_7_hr == 0:
        recency_score = -10
    elif last_7_pa >= 20 and last_7_hr >= 2:
        recency_score = 5  # heating up

    total_score = (power_score + hr_bonus + opp_score + contact_score
                   + fb_score + env_score + platoon_score + recency_score)

    return {
        'hr_rate': round(raw_hr_rate, 4),  # display raw, scoring uses regressed
        'hr_rate_regressed': round(hr_rate, 4),
        'iso': round(raw_iso, 3),
        'iso_regressed': round(iso, 3),
        'score': total_score,
        'power_score': power_score,
        'hr_bonus': hr_bonus,
        'opp_score': opp_score,
        'contact_score': contact_score,
        'fb_score': fb_score,
        'env_score': env_score,
        'platoon_score': platoon_score,
        'recency_score': recency_score,
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
        hr_park = park_hr_factor(venue)
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

                scoring = score_batter(stats, opp_xera, opp_contact, park_factor, hr_park, temp, wind_speed, wind_dir)
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
