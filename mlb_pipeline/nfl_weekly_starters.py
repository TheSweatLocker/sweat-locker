"""NFL weekly starter puller — ESPN scoreboard-backed.

For each upcoming NFL game (regular season, playoffs, or preseason), pulls
the projected starting QBs from ESPN's scoreboard API and writes to
nfl_starters. Feeds the game-detail NFL slot's Starter QB card.

Cadence:
  - Wed 6pm ET (opens the week)
  - Sat 10am ET (locks Sunday early slate)
  - Sun 12pm ET (final for Sun late + MNF)

Extends easily to RB1/WR1 when ESPN starts publishing that fully. For v1
we only pull QB — highest-value single-position surface.

Usage:
  python nfl_weekly_starters.py                     # current season/week
  python nfl_weekly_starters.py --season 2026 --week 1
  python nfl_weekly_starters.py --season-type PRE   # preseason
  python nfl_weekly_starters.py --dry-run
"""
import argparse
import os
import sys
import unicodedata
from datetime import datetime, timezone
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
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# ESPN scoreboard — regular season vs preseason use different `seasontype`
# 1 = preseason, 2 = regular, 3 = postseason
SEASON_TYPE_MAP = {'PRE': 1, 'REG': 2, 'POST': 3}
ESPN_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard'

# ESPN team abbrev → nfl_data_py convention (matches nfl_team_stats.team)
# ESPN uses 'WSH', pipeline uses 'WAS' etc. Add divergences here.
TEAM_MAP: dict[str, str] = {
    'WSH': 'WAS',   # Commanders
    'JAX': 'JAX',   # ok
    'LAR': 'LAR', 'LAC': 'LAC',
    # Rest match 1:1
}
def _map_team(esp: str) -> str:
    return TEAM_MAP.get(esp, esp)


def fetch_scoreboard(season: int, week: int, season_type: str = 'REG') -> list:
    stype = SEASON_TYPE_MAP.get(season_type.upper(), 2)
    params = {'year': season, 'seasontype': stype, 'week': week}
    r = requests.get(ESPN_SCOREBOARD, params=params, timeout=20)
    if r.status_code != 200:
        print(f'  ⚠ ESPN scoreboard {r.status_code}: {r.text[:200]}')
        return []
    return r.json().get('events') or []


ESPN_ROSTER = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team}/roster'
_ROSTER_CACHE: dict[str, Optional[dict]] = {}


def roster_qb1(espn_team: str) -> Optional[dict]:
    """Fallback for offseason / pre-week: pull top QB from ESPN roster depth
    chart. Uses team's roster endpoint, filters position=QB, picks first.
    Cached in-process to avoid re-hitting per game."""
    if espn_team in _ROSTER_CACHE:
        return _ROSTER_CACHE[espn_team]
    try:
        r = requests.get(ESPN_ROSTER.format(team=espn_team.lower()), timeout=15)
        if r.status_code != 200:
            _ROSTER_CACHE[espn_team] = None
            return None
        j = r.json()
        # Structure: athletes[]{position:'offense', items:[]}
        for group in j.get('athletes') or []:
            for ath in group.get('items') or []:
                pos = (ath.get('position') or {}).get('abbreviation')
                if pos == 'QB':
                    result = {'name': ath.get('displayName') or ath.get('fullName'),
                              'id': str(ath.get('id')) if ath.get('id') else None}
                    _ROSTER_CACHE[espn_team] = result
                    return result
    except Exception as e:
        print(f'    roster fallback err {espn_team}: {e}')
    _ROSTER_CACHE[espn_team] = None
    return None


def extract_qb(competition: dict, side_home: bool, espn_team: str) -> Optional[dict]:
    """Pull projected starter QB from a competition. ESPN puts probable
    starters under competitors[].probables (may not populate until game day)
    OR under leaders (season-to-date leader who's likely starter).
    Falls back to team's roster-endpoint QB1 when scoreboard doesn't have it.
    """
    comps = competition.get('competitors') or []
    for c in comps:
        if (side_home and c.get('homeAway') == 'home') or (not side_home and c.get('homeAway') == 'away'):
            for p in (c.get('probables') or []):
                ath = p.get('athlete') or {}
                if ath.get('position', {}).get('abbreviation') == 'QB':
                    return {'name': ath.get('displayName'), 'id': ath.get('id')}
            leaders = c.get('leaders') or []
            for lgroup in leaders:
                if lgroup.get('abbreviation') in ('passingYards', 'passingTouchdowns'):
                    for leader in lgroup.get('leaders') or []:
                        ath = leader.get('athlete') or {}
                        if ath.get('position', {}).get('abbreviation') == 'QB':
                            return {'name': ath.get('displayName'), 'id': ath.get('id')}
    return roster_qb1(espn_team)


def upsert_starter(season: int, week: int, season_type: str,
                   team: str, position: str, player_name: str,
                   player_id: Optional[str] = None,
                   dry_run: bool = False) -> bool:
    if dry_run:
        print(f'  [DRY] {season} W{week} {season_type} {team} {position} = {player_name}')
        return True
    payload = {
        'season': season, 'week': week, 'season_type': season_type,
        'team': team, 'position': position, 'player_name': player_name,
        'player_id': player_id, 'is_starter': True, 'source': 'espn_scoreboard',
    }
    r = requests.post(
        f'{SB}/rest/v1/nfl_starters?on_conflict=season,week,season_type,team,position',
        headers=H_WRITE, json=payload, timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f'    ⚠ upsert {r.status_code}: {r.text[:200]}')
        return False
    return True


def run(season: Optional[int] = None, week: Optional[int] = None,
        season_type: str = 'REG', dry_run: bool = False) -> None:
    now_utc = datetime.now(timezone.utc)
    season = season or now_utc.year
    # Auto-derive week from ESPN scoreboard (its default view = current week)
    if week is None:
        params = {'seasontype': SEASON_TYPE_MAP.get(season_type.upper(), 2)}
        r = requests.get(ESPN_SCOREBOARD, params=params, timeout=15).json()
        wk_info = r.get('week') or {}
        week = wk_info.get('number', 1)
        print(f'  auto-detected week={week}')

    print(f'== NFL starters · {season} · {season_type} · W{week} ==')
    events = fetch_scoreboard(season, week, season_type)
    print(f'  {len(events)} events on ESPN scoreboard')

    total = 0
    for ev in events:
        for c in ev.get('competitions') or []:
            for side, home_flag in (('home', True), ('away', False)):
                # Get team abbrev
                for comp in c.get('competitors') or []:
                    if (home_flag and comp.get('homeAway') != 'home') or (not home_flag and comp.get('homeAway') != 'away'):
                        continue
                    espn_abbrev = (comp.get('team') or {}).get('abbreviation', '?')
                    team = _map_team(espn_abbrev)
                    qb = extract_qb(c, home_flag, espn_abbrev)
                    if qb and qb.get('name'):
                        ok = upsert_starter(season, week, season_type, team, 'QB',
                                            qb['name'], qb.get('id'), dry_run=dry_run)
                        if ok: total += 1
                    else:
                        print(f'  ⚠ no QB found for {team}')
                    break

    print(f'\nSummary: {total} starter QB rows written for {season} W{week} {season_type}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--season', type=int)
    p.add_argument('--week', type=int)
    p.add_argument('--season-type', default='REG', choices=['REG', 'POST', 'PRE'])
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(season=args.season, week=args.week, season_type=args.season_type, dry_run=args.dry_run)
