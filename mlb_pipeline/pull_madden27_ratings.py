"""Pull live Madden NFL 27 team ratings + Top 100 players from madden27.wiki.

Replaces the static launch-snapshot seed in seed_nfl_madden_launch.py
with a real weekly refresh source. Both target pages are static HTML
(no JS), so requests + BeautifulSoup is enough.

Populates:
  * nfl_madden_ratings         (team OVR/OFF/DEF for all 32 teams)
  * nfl_madden_player_ratings  (top 100 by OVR, position + team)
  * nfl_top100_snapshot        (top 100 with rank)

After this runs, enrich_ctx_nfl_madden.py joins these tables into
nfl_game_context (home_madden_ovr / away_madden_ovr /
madden_ovr_gap_home / home_top100_count / etc.).

CLI:
    python pull_madden27_ratings.py                     # current week
    python pull_madden27_ratings.py --week 1
    python pull_madden27_ratings.py --season 2026 --week 0
    python pull_madden27_ratings.py --dry-run           # print only

Idempotent: on_conflict=(team, season, week_snapshot) for team ratings;
on_conflict=(player_name, team, season, week_snapshot) for players.

Runs from nfl_pipeline.yml weekly (Tue morning after MNF grades).
"""
from __future__ import annotations
import argparse, os, sys, re, io
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '\
             '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# Full team name → our internal 2-3 letter code (matches nfl_game_context.home_team).
TEAM_NAME_TO_CODE = {
    'Arizona Cardinals': 'ARI', 'Atlanta Falcons': 'ATL', 'Baltimore Ravens': 'BAL',
    'Buffalo Bills': 'BUF', 'Carolina Panthers': 'CAR', 'Chicago Bears': 'CHI',
    'Cincinnati Bengals': 'CIN', 'Cleveland Browns': 'CLE', 'Dallas Cowboys': 'DAL',
    'Denver Broncos': 'DEN', 'Detroit Lions': 'DET', 'Green Bay Packers': 'GB',
    'Houston Texans': 'HOU', 'Indianapolis Colts': 'IND', 'Jacksonville Jaguars': 'JAX',
    'Kansas City Chiefs': 'KC', 'Las Vegas Raiders': 'LV', 'Los Angeles Chargers': 'LAC',
    'Los Angeles Rams': 'LA', 'Miami Dolphins': 'MIA', 'Minnesota Vikings': 'MIN',
    'New England Patriots': 'NE', 'New Orleans Saints': 'NO', 'New York Giants': 'NYG',
    'New York Jets': 'NYJ', 'Philadelphia Eagles': 'PHI', 'Pittsburgh Steelers': 'PIT',
    'San Francisco 49ers': 'SF', 'Seattle Seahawks': 'SEA', 'Tampa Bay Buccaneers': 'TB',
    'Tennessee Titans': 'TEN', 'Washington Commanders': 'WAS',
}


def _fetch(url: str) -> BeautifulSoup:
    r = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, 'html.parser')


def _int(cell) -> int | None:
    """Extract first integer from cell text."""
    if cell is None: return None
    m = re.search(r'\d+', cell.get_text(strip=True))
    return int(m.group(0)) if m else None


def _text_via_data_label(row, label: str) -> str | None:
    """Find cell by data-label attribute, return stripped text.
    Robust to CSS-module hash changes on the wiki."""
    cell = row.find('td', attrs={'data-label': label})
    return cell.get_text(strip=True) if cell else None


def _cell_via_data_label(row, label: str):
    return row.find('td', attrs={'data-label': label})


def scrape_team_ratings() -> list[dict]:
    """Return list of {team_code, team_name, ovr, off, def, rank}."""
    soup = _fetch('https://madden27.wiki/ratings/teams')
    rows = soup.select('table tbody tr')
    print(f'  team rows found: {len(rows)}')
    out = []
    for row in rows:
        rank = _int(_cell_via_data_label(row, 'Rank'))
        team_cell = _cell_via_data_label(row, 'Team')
        if not team_cell: continue
        team_name_span = team_cell.select_one('span')
        team_name = team_name_span.get_text(strip=True) if team_name_span else team_cell.get_text(strip=True)
        code = TEAM_NAME_TO_CODE.get(team_name)
        if not code:
            print(f'  ⚠ unmapped team name: {team_name!r} (rank {rank}) — skipping')
            continue
        ovr = _int(_cell_via_data_label(row, 'OVR'))
        off = _int(_cell_via_data_label(row, 'OFF'))
        deff = _int(_cell_via_data_label(row, 'DEF'))
        out.append({'team_code': code, 'team_name': team_name,
                    'ovr': ovr, 'off': off, 'def': deff, 'rank': rank})
    return out


def scrape_top100() -> list[dict]:
    """Return list of {rank, player_name, position, team_code, ovr}."""
    soup = _fetch('https://madden27.wiki/ratings/top-100')
    rows = soup.select('table tbody tr')
    print(f'  top-100 rows found: {len(rows)}')
    out = []
    for row in rows:
        rank = _int(_cell_via_data_label(row, 'Rank'))
        player_cell = _cell_via_data_label(row, 'Player')
        if not player_cell: continue
        p_span = player_cell.select_one('span')
        player_name = p_span.get_text(strip=True) if p_span else player_cell.get_text(strip=True)
        position = _text_via_data_label(row, 'Position')
        team_name = _text_via_data_label(row, 'Team')
        team_code = TEAM_NAME_TO_CODE.get(team_name or '')
        ovr = _int(_cell_via_data_label(row, 'OVR'))
        out.append({'rank': rank, 'player_name': player_name,
                    'position': position, 'team_name': team_name,
                    'team_code': team_code, 'ovr': ovr})
    return out


def upsert_team_ratings(teams: list[dict], season: int, week: int, dry_run: bool) -> int:
    if not teams: return 0
    fetched = datetime.now(timezone.utc).isoformat()
    payload = [{'team': t['team_code'], 'season': season, 'week_snapshot': week,
                'ovr': t['ovr'], 'off_rating': t['off'], 'def_rating': t['def'],
                'ovr_rank': t['rank'], 'source': 'madden27.wiki', 'fetched_at': fetched}
               for t in teams if t.get('team_code') and t.get('ovr') is not None]
    if dry_run:
        print(f'  [DRY] would upsert {len(payload)} team-rating rows')
        return len(payload)
    r = requests.post(f'{SB}/rest/v1/nfl_madden_ratings?on_conflict=team,season,week_snapshot',
                      headers=H_WRITE, json=payload, timeout=30)
    if r.status_code not in (200, 201, 204):
        print(f'  team upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(payload)


def upsert_players(players: list[dict], season: int, week: int, dry_run: bool) -> int:
    """Upsert into nfl_madden_player_ratings for the top-100 subset."""
    if not players: return 0
    fetched = datetime.now(timezone.utc).isoformat()
    payload = []
    for p in players:
        if not p.get('team_code') or p.get('ovr') is None: continue
        payload.append({
            'player_name': p['player_name'], 'team': p['team_code'],
            'season': season, 'week_snapshot': week,
            'position': p.get('position'),
            'ovr': p['ovr'],
            'source': 'madden27.wiki', 'fetched_at': fetched,
        })
    if dry_run:
        print(f'  [DRY] would upsert {len(payload)} player-rating rows')
        return len(payload)
    r = requests.post(f'{SB}/rest/v1/nfl_madden_player_ratings?on_conflict=player_name,team,season,week_snapshot',
                      headers=H_WRITE, json=payload, timeout=30)
    if r.status_code not in (200, 201, 204):
        print(f'  player upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(payload)


def upsert_top100(players: list[dict], season: int, week: int, dry_run: bool) -> int:
    """Upsert into nfl_top100_snapshot (rank + team + player).
    Table columns learned by probe; schema is minimal since
    enrich_ctx_nfl_madden mostly counts per-team."""
    if not players: return 0
    fetched = datetime.now(timezone.utc).isoformat()
    # Schema: (player_name, team, season, rank, position, fetched_at).
    # No ovr / week_snapshot / source columns per probed schema.
    payload = [{
        'rank': p['rank'], 'player_name': p['player_name'],
        'team': p.get('team_code'), 'position': p.get('position'),
        'season': season, 'fetched_at': fetched,
    } for p in players if p.get('team_code') and p.get('rank') is not None]
    if dry_run:
        print(f'  [DRY] would upsert {len(payload)} top-100 snapshot rows')
        return len(payload)
    # No unique constraint known — clear this season's rows first,
    # then insert fresh. Cheap because it's 100 rows.
    requests.delete(f'{SB}/rest/v1/nfl_top100_snapshot?season=eq.{season}',
                    headers=H_READ, timeout=15)
    r = requests.post(f'{SB}/rest/v1/nfl_top100_snapshot',
                      headers=H_WRITE, json=payload, timeout=30)
    if r.status_code not in (200, 201, 204):
        print(f'  top100 insert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(payload)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int,
                    default=datetime.now(timezone.utc).year)
    ap.add_argument('--week', type=int, default=0,
                    help='0 = launch snapshot; increment weekly after EA refresh')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    print(f'=== Madden27.wiki pull · season {args.season} · week {args.week} ===')

    print('  → team ratings')
    teams = scrape_team_ratings()
    print(f'  parsed {len(teams)} teams')

    print('  → top 100')
    top100 = scrape_top100()
    print(f'  parsed {len(top100)} players')

    if not teams and not top100:
        print('  ⛔ nothing parsed — wiki layout may have changed'); return 2

    n_teams = upsert_team_ratings(teams, args.season, args.week, args.dry_run)
    n_players = upsert_players(top100, args.season, args.week, args.dry_run)
    n_top100 = upsert_top100(top100, args.season, args.week, args.dry_run)
    print(f'✓ wrote {n_teams} team ratings, {n_players} player ratings, {n_top100} top-100 rows')
    return 0


if __name__ == '__main__':
    sys.exit(main())
