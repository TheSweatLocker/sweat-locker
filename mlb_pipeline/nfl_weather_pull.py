"""NFL weather puller — OpenWeather-backed, keyed on stadium coords.

Populates temp/wind/precip fields on nfl_game_context for upcoming games
(≤7 days out). Domed stadiums get temp=72, wind=0, dome=True and skip the
API call. Games further than 7 days out get climatology defaults from the
stadium's average conditions.

Cadence:
  - Every 6 hours during the week (Mon-Sat)
  - Every 3 hours on gameday (Sun/Thu/Mon)

Usage:
  python nfl_weather_pull.py                     # all upcoming ≤7d
  python nfl_weather_pull.py --game-id 2026091500
  python nfl_weather_pull.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

OW_KEY = os.environ.get('OPENWEATHER_API_KEY') or os.environ.get('OPENWEATHER_KEY')
OW_ONECALL = 'https://api.openweathermap.org/data/3.0/onecall'
OW_CURRENT = 'https://api.openweathermap.org/data/2.5/weather'

# ─── Stadium coord + dome table (32 teams, home stadium only) ────────────
# Keyed on team abbrev matching nfl_team_stats.team
STADIUMS: dict[str, dict] = {
    'ARI': {'lat': 33.5276, 'lng': -112.2626, 'dome': True,  'name': 'State Farm Stadium'},
    'ATL': {'lat': 33.7554, 'lng': -84.4008,  'dome': True,  'name': 'Mercedes-Benz Stadium'},
    'BAL': {'lat': 39.2780, 'lng': -76.6227,  'dome': False, 'name': 'M&T Bank Stadium'},
    'BUF': {'lat': 42.7738, 'lng': -78.7869,  'dome': False, 'name': 'Highmark Stadium'},
    'CAR': {'lat': 35.2258, 'lng': -80.8528,  'dome': False, 'name': 'Bank of America Stadium'},
    'CHI': {'lat': 41.8623, 'lng': -87.6167,  'dome': False, 'name': 'Soldier Field'},
    'CIN': {'lat': 39.0955, 'lng': -84.5161,  'dome': False, 'name': 'Paycor Stadium'},
    'CLE': {'lat': 41.5061, 'lng': -81.6995,  'dome': False, 'name': 'Cleveland Browns Stadium'},
    'DAL': {'lat': 32.7473, 'lng': -97.0945,  'dome': True,  'name': 'AT&T Stadium'},
    'DEN': {'lat': 39.7439, 'lng': -105.0201, 'dome': False, 'name': 'Empower Field at Mile High'},
    'DET': {'lat': 42.3400, 'lng': -83.0456,  'dome': True,  'name': 'Ford Field'},
    'GB':  {'lat': 44.5013, 'lng': -88.0622,  'dome': False, 'name': 'Lambeau Field'},
    'HOU': {'lat': 29.6847, 'lng': -95.4107,  'dome': True,  'name': 'NRG Stadium'},  # retractable
    'IND': {'lat': 39.7601, 'lng': -86.1639,  'dome': True,  'name': 'Lucas Oil Stadium'},  # retractable
    'JAX': {'lat': 30.3239, 'lng': -81.6373,  'dome': False, 'name': 'EverBank Stadium'},
    'KC':  {'lat': 39.0489, 'lng': -94.4839,  'dome': False, 'name': 'GEHA Field at Arrowhead'},
    'LAC': {'lat': 33.9535, 'lng': -118.3387, 'dome': False, 'name': 'SoFi Stadium'},   # open-air roof
    'LAR': {'lat': 33.9535, 'lng': -118.3387, 'dome': False, 'name': 'SoFi Stadium'},
    'LV':  {'lat': 36.0908, 'lng': -115.1830, 'dome': True,  'name': 'Allegiant Stadium'},
    'MIA': {'lat': 25.9580, 'lng': -80.2389,  'dome': False, 'name': 'Hard Rock Stadium'},
    'MIN': {'lat': 44.9738, 'lng': -93.2578,  'dome': True,  'name': 'U.S. Bank Stadium'},
    'NE':  {'lat': 42.0909, 'lng': -71.2643,  'dome': False, 'name': 'Gillette Stadium'},
    'NO':  {'lat': 29.9509, 'lng': -90.0812,  'dome': True,  'name': 'Caesars Superdome'},
    'NYG': {'lat': 40.8135, 'lng': -74.0745,  'dome': False, 'name': 'MetLife Stadium'},
    'NYJ': {'lat': 40.8135, 'lng': -74.0745,  'dome': False, 'name': 'MetLife Stadium'},
    'PHI': {'lat': 39.9008, 'lng': -75.1675,  'dome': False, 'name': 'Lincoln Financial Field'},
    'PIT': {'lat': 40.4468, 'lng': -80.0158,  'dome': False, 'name': 'Acrisure Stadium'},
    'SEA': {'lat': 47.5952, 'lng': -122.3316, 'dome': False, 'name': 'Lumen Field'},
    'SF':  {'lat': 37.4032, 'lng': -121.9698, 'dome': False, 'name': 'Levi\'s Stadium'},
    'TB':  {'lat': 27.9759, 'lng': -82.5033,  'dome': False, 'name': 'Raymond James Stadium'},
    'TEN': {'lat': 36.1665, 'lng': -86.7713,  'dome': False, 'name': 'Nissan Stadium'},
    'WAS': {'lat': 38.9077, 'lng': -76.8645,  'dome': False, 'name': 'Northwest Stadium'},
}


def fetch_forecast(lat: float, lng: float, target_utc: datetime) -> Optional[dict]:
    """Get forecast closest to target_utc from OpenWeather onecall (hourly)."""
    if not OW_KEY:
        print('  ⚠ OPENWEATHER_API_KEY not set — skipping API pull')
        return None
    params = {'lat': lat, 'lon': lng, 'appid': OW_KEY, 'units': 'imperial',
              'exclude': 'current,minutely,daily,alerts'}
    r = requests.get(OW_ONECALL, params=params, timeout=15)
    if r.status_code != 200:
        # Fall back to current-weather endpoint if onecall 401s (free tier)
        r = requests.get(OW_CURRENT, params={'lat': lat, 'lon': lng,
                                              'appid': OW_KEY, 'units': 'imperial'}, timeout=15)
        if r.status_code != 200:
            print(f'  ⚠ OpenWeather {r.status_code}: {r.text[:200]}')
            return None
        j = r.json()
        return {'temp': j.get('main', {}).get('temp'),
                'wind_mph': j.get('wind', {}).get('speed'),
                'precip': (j.get('rain', {}) or {}).get('1h', 0) or 0,
                'source': 'current'}
    j = r.json()
    hourly = j.get('hourly') or []
    if not hourly:
        return None
    target_ts = target_utc.timestamp()
    closest = min(hourly, key=lambda h: abs((h.get('dt') or 0) - target_ts))
    return {'temp': closest.get('temp'),
            'wind_mph': (closest.get('wind_speed') or 0),
            'precip': (closest.get('rain', {}) or {}).get('1h', 0) or 0,
            'source': 'onecall'}


def fetch_upcoming_games() -> list:
    """Games in nfl_game_context within next 7 days that lack weather."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=7)
    r = requests.get(
        f'{SB}/rest/v1/nfl_game_context',
        headers=H_READ,
        params={'select': 'game_id,commence_time,home_team,away_team,temp,wind,dome',
                'commence_time': f'gte.{now.isoformat()},lt.{horizon.isoformat()}',
                'order': 'commence_time.asc', 'limit': 200},
        timeout=20,
    )
    if r.status_code != 200:
        print(f'  ⚠ context fetch {r.status_code}: {r.text[:200]}')
        return []
    return r.json()


def patch_game(game_id: str, patch: dict, dry_run: bool = False) -> bool:
    if dry_run:
        print(f'  [DRY] {game_id} ← {patch}')
        return True
    r = requests.patch(
        f'{SB}/rest/v1/nfl_game_context?game_id=eq.{game_id}',
        headers=H_WRITE, json=patch, timeout=15,
    )
    if r.status_code not in (200, 204):
        print(f'  ⚠ patch {game_id}: {r.status_code} — {r.text[:200]}')
        return False
    return True


def run(game_id: Optional[str] = None, dry_run: bool = False) -> None:
    print(f'== NFL weather pull ==')
    if game_id:
        r = requests.get(f'{SB}/rest/v1/nfl_game_context?game_id=eq.{game_id}',
                         headers=H_READ, timeout=15)
        games = r.json() if r.status_code == 200 else []
    else:
        games = fetch_upcoming_games()
    print(f'  {len(games)} upcoming games')

    if not games:
        return

    updated = 0
    for g in games:
        home = g.get('home_team', '?')
        stad = STADIUMS.get(home)
        if not stad:
            print(f'  ⚠ no stadium coord for {home} — skip')
            continue
        if stad['dome']:
            patch = {'temp': 72, 'wind': 0, 'dome': True, 'weather_source': 'dome_default'}
            if patch_game(g['game_id'], patch, dry_run):
                updated += 1
            continue
        # Parse commence_time → utc dt
        try:
            ct = g['commence_time']
            if ct.endswith('Z'): ct = ct[:-1] + '+00:00'
            target_utc = datetime.fromisoformat(ct)
        except Exception as e:
            print(f'  ⚠ bad commence_time {g.get("commence_time")}: {e}')
            continue
        wx = fetch_forecast(stad['lat'], stad['lng'], target_utc)
        if not wx:
            continue
        patch = {'temp': round(wx['temp']) if wx['temp'] is not None else None,
                 'wind': round(wx['wind_mph']) if wx['wind_mph'] is not None else None,
                 'dome': False,
                 'weather_source': f'openweather_{wx.get("source", "?")}'}
        if patch_game(g['game_id'], patch, dry_run):
            updated += 1

    print(f'\nSummary: {updated}/{len(games)} games patched with weather')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--game-id', help='single game_id to patch')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_id=args.game_id, dry_run=args.dry_run)
