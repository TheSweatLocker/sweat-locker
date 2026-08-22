"""NBA prop generator (2026-08-17).

Sweeps The Odds API for NBA player props, upserts into nba_pipeline_props.
Same pattern as nhl_generate_props.py.

Markets covered:
  * player_points
  * player_rebounds
  * player_assists
  * player_threes
  * player_blocks
  * player_steals
  * player_turnovers
  * player_points_rebounds_assists (PRA combo)

CLI:
  python nba_generate_props.py                  # today ET
  python nba_generate_props.py --date 2026-10-22
  python nba_generate_props.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
ODDS_KEY = os.environ.get('ODDS_API_KEY')
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

ODDS_BASE = 'https://api.the-odds-api.com/v4/sports/basketball_nba'

MARKET_MAP = {
    'player_points':                'pts',
    'player_rebounds':              'reb',
    'player_assists':               'ast',
    'player_threes':                'threes',
    'player_blocks':                'blocks',
    'player_steals':                'steals',
    'player_turnovers':             'turnovers',
    'player_points_rebounds_assists': 'pra',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_events() -> list:
    if not ODDS_KEY: return []
    from_iso = (datetime.now(timezone.utc) - timedelta(hours=8)).strftime('%Y-%m-%dT%H:%M:%SZ')
    to_iso = (datetime.now(timezone.utc) + timedelta(hours=36)).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = requests.get(f'{ODDS_BASE}/events',
                     params={'apiKey': ODDS_KEY,
                             'commenceTimeFrom': from_iso,
                             'commenceTimeTo': to_iso},
                     timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_props_for_event(event_id: str) -> dict:
    r = requests.get(
        f'{ODDS_BASE}/events/{event_id}/odds',
        params={'apiKey': ODDS_KEY, 'regions': 'us,us2',
                'markets': ','.join(MARKET_MAP.keys()),
                'oddsFormat': 'american'},
        timeout=20)
    if r.status_code != 200: return {}
    data = r.json()

    by_key = {}
    for bk in data.get('bookmakers', []):
        book = bk.get('key')
        for mkt in bk.get('markets', []):
            base = MARKET_MAP.get(mkt.get('key'))
            if not base: continue
            for out in mkt.get('outcomes', []):
                player = (out.get('description') or '').strip()
                side = (out.get('name') or '').lower()
                direction = 'over' if 'over' in side else ('under' if 'under' in side else None)
                if not direction or not player: continue
                line = out.get('point'); price = out.get('price')
                if line is None or price is None: continue
                prop_type = f'{base}_{direction}'
                key = (player.lower(), prop_type, direction, float(line))
                slot = by_key.setdefault(key, {
                    'display': player, 'line': float(line),
                    'over_odds': None, 'under_odds': None, 'book': book,
                })
                slot[f'{direction}_odds'] = int(price)
    return by_key


def upsert_prop(payload: dict, dry_run: bool = False) -> bool:
    if dry_run: return True
    try:
        pr = requests.post(
            f'{SB}/rest/v1/nba_pipeline_props?on_conflict=game_date,player_name,prop_type,direction,prop_line',
            headers=H_WRITE, json=payload, timeout=15)
        return pr.status_code in (200, 201, 204)
    except Exception as e:
        print(f'    ✗ {e}')
        return False


def run(game_date: Optional[str] = None, dry_run: bool = False):
    gd = game_date or _et_today()
    print(f'=== nba_generate_props · {gd} ===')
    if not ODDS_KEY:
        print('  ⚠ ODDS_API_KEY missing — skipping'); return

    events = fetch_events()
    print(f'  {len(events)} NBA events fetched')

    total_props = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for ev in events:
        commence = ev.get('commence_time', '')
        if not (commence.startswith(gd) or commence.startswith((date.fromisoformat(gd) + timedelta(days=1)).isoformat())):
            continue
        home = ev.get('home_team'); away = ev.get('away_team')
        matchup = f'{away} @ {home}'
        event_id = ev.get('id')
        props = fetch_props_for_event(event_id)
        if not props: continue

        for (player_lc, prop_type, direction, line), slot in props.items():
            payload = {
                'game_date':      gd,
                'game_id':        event_id,
                'player_name':    slot['display'],
                'matchup':        matchup,
                'prop_type':      prop_type,
                'direction':      direction,
                'prop_line':      line,
                'book_line':      line,
                'book_over_odds': slot.get('over_odds'),
                'book_under_odds': slot.get('under_odds'),
                'book_source':    slot.get('book'),
                'tier':           'COVERAGE',
                'conviction':     0,
                'signals':        {},
                'last_attached_at': now_iso,
            }
            if upsert_prop(payload, dry_run=dry_run):
                total_props += 1

    print(f'\n  {"[DRY] " if dry_run else ""}upserted {total_props} NBA props')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date'); p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        from season_gate import season_gate_or_exit
        season_gate_or_exit('NBA')
    except ImportError:
        pass
    main()
