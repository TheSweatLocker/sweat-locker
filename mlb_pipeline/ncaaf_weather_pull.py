"""NCAAF weather puller — OpenWeather-backed, keyed on home team stadium.

Closes silent-bug audit finding #14 — 4 NCAAF signals were reading
ctx.wind_speed and ctx.temp that no puller ever populated:
  ncaaf_extreme_wind_under
  ncaaf_high_wind_under
  ncaaf_freezing_cold_under
  ncaaf_prop_freezing_rush_boost

Populates temp / wind / dome / weather_source fields on
ncaaf_game_context for upcoming games (≤7 days out).

Cadence: wire into ncaaf_pipeline.yml Fri + Sat crons.

Usage:
  python ncaaf_weather_pull.py                    # all upcoming ≤7d
  python ncaaf_weather_pull.py --game-id 401756789
  python ncaaf_weather_pull.py --dry-run

Coverage: ~80 P5 + top G5 stadiums seeded. Unknown teams skipped
(safe fallback — signals stay PASS instead of firing on bad data).
"""
import argparse, os, sys
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

# ─── NCAAF stadium coord + dome table ────────────────────────────────
# Keyed on home_team as it appears in ncaaf_game_context (CFBD naming).
# ~80 programs — Power 5 + top G5 + top indies. Add rows as needed.
# dome=True: Ford Field (UMich occasional), Alamodome (Army/Navy neutral), etc.
# Otherwise assume outdoor. Neutral-site games handled by caller if flagged.
STADIUMS: dict[str, dict] = {
    # SEC
    'Alabama':        {'lat': 33.2098, 'lng': -87.5504, 'dome': False},
    'Auburn':         {'lat': 32.6023, 'lng': -85.4907, 'dome': False},
    'Arkansas':       {'lat': 36.0680, 'lng': -94.1782, 'dome': False},
    'Florida':        {'lat': 29.6499, 'lng': -82.3486, 'dome': False},
    'Georgia':        {'lat': 33.9497, 'lng': -83.3737, 'dome': False},
    'Kentucky':       {'lat': 38.0219, 'lng': -84.5057, 'dome': False},
    'LSU':            {'lat': 30.4118, 'lng': -91.1836, 'dome': False},
    'Ole Miss':       {'lat': 34.3617, 'lng': -89.5347, 'dome': False},
    'Mississippi State': {'lat': 33.4553, 'lng': -88.7936, 'dome': False},
    'Missouri':       {'lat': 38.9358, 'lng': -92.3336, 'dome': False},
    'South Carolina': {'lat': 34.0189, 'lng': -81.0197, 'dome': False},
    'Tennessee':      {'lat': 35.9549, 'lng': -83.9250, 'dome': False},
    'Texas':          {'lat': 30.2836, 'lng': -97.7325, 'dome': False},
    'Texas A&M':      {'lat': 30.6103, 'lng': -96.3406, 'dome': False},
    'Vanderbilt':     {'lat': 36.1428, 'lng': -86.8060, 'dome': False},
    'Oklahoma':       {'lat': 35.2058, 'lng': -97.4426, 'dome': False},
    # BIG TEN
    'Michigan':       {'lat': 42.2657, 'lng': -83.7487, 'dome': False},
    'Michigan State': {'lat': 42.7284, 'lng': -84.4841, 'dome': False},
    'Ohio State':     {'lat': 40.0017, 'lng': -83.0197, 'dome': False},
    'Penn State':     {'lat': 40.8121, 'lng': -77.8562, 'dome': False},
    'Wisconsin':      {'lat': 43.0700, 'lng': -89.4126, 'dome': False},
    'Iowa':           {'lat': 41.6580, 'lng': -91.5514, 'dome': False},
    'Minnesota':      {'lat': 44.9750, 'lng': -93.2586, 'dome': False},
    'Nebraska':       {'lat': 40.8207, 'lng': -96.7057, 'dome': False},
    'Illinois':       {'lat': 40.0996, 'lng': -88.2359, 'dome': False},
    'Indiana':        {'lat': 39.1808, 'lng': -86.5259, 'dome': False},
    'Northwestern':   {'lat': 42.0656, 'lng': -87.6923, 'dome': False},
    'Purdue':         {'lat': 40.4342, 'lng': -86.9182, 'dome': False},
    'Rutgers':        {'lat': 40.5133, 'lng': -74.4652, 'dome': False},
    'Maryland':       {'lat': 38.9927, 'lng': -76.9475, 'dome': False},
    'UCLA':           {'lat': 34.1611, 'lng': -118.1678, 'dome': False},  # Rose Bowl
    'USC':            {'lat': 34.0141, 'lng': -118.2879, 'dome': False},
    'Washington':     {'lat': 47.6503, 'lng': -122.3016, 'dome': False},
    'Oregon':         {'lat': 44.0582, 'lng': -123.0685, 'dome': False},
    # BIG 12
    'Baylor':         {'lat': 31.5580, 'lng': -97.1157, 'dome': False},
    'BYU':            {'lat': 40.2578, 'lng': -111.6547, 'dome': False},
    'Cincinnati':     {'lat': 39.1310, 'lng': -84.5164, 'dome': False},
    'Colorado':       {'lat': 40.0090, 'lng': -105.2670, 'dome': False},
    'Houston':        {'lat': 29.7220, 'lng': -95.3480, 'dome': False},
    'Iowa State':     {'lat': 42.0143, 'lng': -93.6357, 'dome': False},
    'Kansas':         {'lat': 38.9633, 'lng': -95.2447, 'dome': False},
    'Kansas State':   {'lat': 39.2020, 'lng': -96.5942, 'dome': False},
    'Oklahoma State': {'lat': 36.1244, 'lng': -97.0662, 'dome': False},
    'TCU':            {'lat': 32.7098, 'lng': -97.3684, 'dome': False},
    'Texas Tech':     {'lat': 33.5911, 'lng': -101.8724, 'dome': False},
    'UCF':            {'lat': 28.6079, 'lng': -81.1927, 'dome': False},
    'Utah':           {'lat': 40.7607, 'lng': -111.8489, 'dome': False},
    'West Virginia':  {'lat': 39.6486, 'lng': -79.9542, 'dome': False},
    'Arizona':        {'lat': 32.2288, 'lng': -110.9484, 'dome': False},
    'Arizona State':  {'lat': 33.4265, 'lng': -111.9327, 'dome': False},
    # ACC
    'Boston College': {'lat': 42.3355, 'lng': -71.1691, 'dome': False},
    'Clemson':        {'lat': 34.6789, 'lng': -82.8438, 'dome': False},
    'Duke':           {'lat': 36.0011, 'lng': -78.9391, 'dome': False},
    'Florida State':  {'lat': 30.4381, 'lng': -84.3049, 'dome': False},
    'Georgia Tech':   {'lat': 33.7724, 'lng': -84.3924, 'dome': False},
    'Louisville':     {'lat': 38.2065, 'lng': -85.7570, 'dome': False},
    'Miami':          {'lat': 25.9581, 'lng': -80.2389, 'dome': False},
    'NC State':       {'lat': 35.7999, 'lng': -78.7217, 'dome': False},
    'North Carolina': {'lat': 35.9070, 'lng': -79.0472, 'dome': False},
    'Pittsburgh':     {'lat': 40.4468, 'lng': -80.0158, 'dome': False},
    'Syracuse':       {'lat': 43.0362, 'lng': -76.1361, 'dome': True},  # Carrier Dome
    'Virginia':       {'lat': 38.0313, 'lng': -78.5140, 'dome': False},
    'Virginia Tech':  {'lat': 37.2200, 'lng': -80.4187, 'dome': False},
    'Wake Forest':    {'lat': 36.1287, 'lng': -80.2593, 'dome': False},
    'California':     {'lat': 37.8712, 'lng': -122.2510, 'dome': False},
    'Stanford':       {'lat': 37.4348, 'lng': -122.1611, 'dome': False},
    'SMU':            {'lat': 32.8397, 'lng': -96.7828, 'dome': False},
    # NOTRE DAME + INDEPENDENTS
    'Notre Dame':     {'lat': 41.6987, 'lng': -86.2337, 'dome': False},
    'Army':           {'lat': 41.3903, 'lng': -73.9640, 'dome': False},
    'Navy':           {'lat': 38.9856, 'lng': -76.4708, 'dome': False},
    'UConn':          {'lat': 41.8064, 'lng': -72.2536, 'dome': False},
    # TOP G5
    'Boise State':    {'lat': 43.6027, 'lng': -116.1966, 'dome': False},
    'Memphis':        {'lat': 35.1173, 'lng': -89.9789, 'dome': False},
    'Tulane':         {'lat': 29.9354, 'lng': -90.1189, 'dome': True},   # Yulman (open) but backup Superdome
    'Liberty':        {'lat': 37.3555, 'lng': -79.1620, 'dome': False},
    'Coastal Carolina': {'lat': 33.7920, 'lng': -79.0100, 'dome': False},
    'App State':      {'lat': 36.2114, 'lng': -81.6853, 'dome': False},
    'James Madison':  {'lat': 38.4275, 'lng': -78.8722, 'dome': False},
    'Fresno State':   {'lat': 36.8110, 'lng': -119.7466, 'dome': False},
    'Air Force':      {'lat': 38.9970, 'lng': -104.8438, 'dome': False},
    'San Diego State':{'lat': 32.7830, 'lng': -117.1204, 'dome': False},
    'Toledo':         {'lat': 41.6634, 'lng': -83.6134, 'dome': False},
    'Northern Illinois': {'lat': 41.9330, 'lng': -88.7734, 'dome': False},
}


def fetch_forecast(lat: float, lng: float, target_utc: datetime) -> Optional[dict]:
    """Return {temp,wind_mph,precip,source} for target UTC datetime."""
    if not OW_KEY:
        return None
    try:
        r = requests.get(OW_ONECALL, params={
            'lat': lat, 'lon': lng, 'appid': OW_KEY,
            'units': 'imperial', 'exclude': 'minutely,daily,alerts',
        }, timeout=15)
        if r.status_code != 200:
            r2 = requests.get(OW_CURRENT, params={
                'lat': lat, 'lon': lng, 'appid': OW_KEY, 'units': 'imperial',
            }, timeout=15)
            if r2.status_code != 200: return None
            j = r2.json()
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
    except Exception as e:
        print(f'  ⚠ forecast fetch failed: {e}')
        return None


def fetch_upcoming_games() -> list:
    """NCAAF games in next 7 days that lack weather."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=7)
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_context',
        headers=H_READ,
        params={'select': 'game_id,kickoff_utc,home_team,away_team,neutral_site,temp,wind,dome',
                'kickoff_utc': f'gte.{now.isoformat()},lt.{horizon.isoformat()}',
                'order': 'kickoff_utc.asc', 'limit': '200'},
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
        f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{game_id}',
        headers=H_WRITE, json=patch, timeout=15,
    )
    if r.status_code not in (200, 204):
        # Retry stripping unknown columns
        print(f'  ⚠ patch {game_id}: {r.status_code} — {r.text[:200]}')
        return False
    return True


def run(game_id: Optional[str] = None, dry_run: bool = False) -> None:
    print('== NCAAF weather pull ==')
    if not OW_KEY:
        print('  ⚠ OPENWEATHER_API_KEY missing — cannot fetch forecasts')
        return
    if game_id:
        r = requests.get(f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{game_id}',
                         headers=H_READ, timeout=15)
        games = r.json() if r.status_code == 200 else []
    else:
        games = fetch_upcoming_games()
    print(f'  {len(games)} upcoming games (7-day window)')

    if not games: return
    updated = 0; skipped_unknown = 0
    for g in games:
        home = g.get('home_team', '?')
        # Neutral site games — skip weather (can't easily know venue)
        if g.get('neutral_site'):
            skipped_unknown += 1
            continue
        stad = STADIUMS.get(home)
        if not stad:
            skipped_unknown += 1
            continue
        # Signal reads ctx.wind_speed AND ctx.wind — write both
        # to be defensive (finding #14 + #18 both listed inconsistent names).
        if stad['dome']:
            patch = {'temp': 72, 'wind': 0, 'wind_speed': 0,
                     'dome': True, 'weather_source': 'dome_default'}
            if patch_game(g['game_id'], patch, dry_run):
                updated += 1
            continue
        try:
            ct = g['kickoff_utc']
            if ct.endswith('Z'): ct = ct[:-1] + '+00:00'
            target_utc = datetime.fromisoformat(ct)
        except Exception as e:
            print(f'  ⚠ bad kickoff_utc {g.get("kickoff_utc")}: {e}')
            continue
        wx = fetch_forecast(stad['lat'], stad['lng'], target_utc)
        if not wx: continue
        w = round(wx['wind_mph']) if wx['wind_mph'] is not None else None
        patch = {
            'temp': round(wx['temp']) if wx['temp'] is not None else None,
            'wind': w, 'wind_speed': w,  # dual-write
            'dome': False,
            'weather_source': f'openweather_{wx.get("source","?")}',
        }
        if patch_game(g['game_id'], patch, dry_run):
            updated += 1

    print(f'\nSummary: {updated}/{len(games)} patched · {skipped_unknown} skipped (unknown venue / neutral)')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--game-id')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_id=args.game_id, dry_run=args.dry_run)
