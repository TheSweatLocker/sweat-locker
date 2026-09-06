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

# 2026-09-06 QB pass — team-code → madden27.wiki team-page slug. Used by
# scrape_starter_qbs() to hit each team's roster page and pull the
# starter QB (highest-OVR QB per team). Enables home_qb_madden_ovr /
# madden_qb_delta_home fields that top-100 alone can't cover (only ~8
# QBs in top-100; other 24 starters are on their team pages).
TEAM_CODE_TO_SLUG = {
    'BUF':'buffalo-bills','MIA':'miami-dolphins','NE':'new-england-patriots','NYJ':'ny-jets',
    'BAL':'baltimore-ravens','CIN':'cincinnati-bengals','CLE':'cleveland-browns','PIT':'pittsburgh-steelers',
    'HOU':'houston-texans','IND':'indianapolis-colts','JAX':'jacksonville-jaguars','TEN':'tennessee-titans',
    'DEN':'denver-broncos','KC':'kansas-city-chiefs','LV':'las-vegas-raiders','LAC':'los-angeles-chargers',
    'DAL':'dallas-cowboys','NYG':'ny-giants','PHI':'philadelphia-eagles','WAS':'washington-commanders',
    'CHI':'chicago-bears','DET':'detroit-lions','GB':'green-bay-packers','MIN':'minnesota-vikings',
    'ATL':'atlanta-falcons','CAR':'carolina-panthers','NO':'new-orleans-saints','TB':'tampa-bay-buccaneers',
    'ARI':'arizona-cardinals','LA':'los-angeles-rams','SF':'san-francisco-49ers','SEA':'seattle-seahawks',
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
    """Return list of {rank, player_name, position, team_code, ovr}.

    Wiki quirk (2026-09-06): the 'Rank' cell shows the tier-START rank
    for that OVR band (all 99-OVR players share rank=1, all 98-OVR
    share rank=7, etc.). Rows are ordered by OVR desc, ties broken by
    name/team. Assign our own sequential 1..N rank by row order and
    ignore the wiki's rank cell — gives us 100 unique ranks for the
    top100_snapshot table without wasting the OVR ordering.
    """
    soup = _fetch('https://madden27.wiki/ratings/top-100')
    rows = soup.select('table tbody tr')
    print(f'  top-100 rows found: {len(rows)}')
    out = []
    for i, row in enumerate(rows, start=1):
        player_cell = _cell_via_data_label(row, 'Player')
        if not player_cell: continue
        # Wiki cell has two spans: a short initials badge + the full
        # name (span[class*="playerNameLink"]). Prefer the full-name
        # span; fall back to the longest text token in the cell.
        p_span = player_cell.select_one('[class*="playerNameLink"]')
        if p_span:
            player_name = p_span.get_text(strip=True)
        else:
            parts = [t.strip() for t in player_cell.get_text('\n').splitlines() if t.strip()]
            player_name = max(parts, key=len) if parts else ''
        position = _text_via_data_label(row, 'Position')
        team_name = _text_via_data_label(row, 'Team')
        team_code = TEAM_NAME_TO_CODE.get(team_name or '')
        ovr = _int(_cell_via_data_label(row, 'OVR'))
        out.append({'rank': i, 'player_name': player_name,
                    'position': position, 'team_name': team_name,
                    'team_code': team_code, 'ovr': ovr})
    return out


def scrape_starter_qbs() -> list[dict]:
    """Fetch each of 32 team pages, extract all QBs, pick highest OVR
    per team as the starter. Returns list of {team_code, player_name, ovr}."""
    import time
    out = []
    for code, slug in TEAM_CODE_TO_SLUG.items():
        try:
            soup = _fetch(f'https://madden27.wiki/ratings/teams/{slug}')
        except Exception as e:
            print(f'  ⚠ {code} fetch failed: {e}')
            time.sleep(0.5); continue
        # Roster rows use divs (not <table>) with data-label attrs
        qbs = []
        for row in soup.select('[data-label]'):
            # skip non-row nodes
            pass
        # Simpler: find all "position" cells == "QB", then walk up to row
        for pos_cell in soup.find_all(attrs={'data-label': 'Position'}):
            pos_text = pos_cell.get_text(strip=True)
            if 'QB' not in pos_text: continue
            # walk to parent row-like container
            row = pos_cell
            for _ in range(6):
                row = row.parent
                if row is None: break
                if row.find(attrs={'data-label': 'Player'}) or row.find(attrs={'data-label': 'Name'}):
                    break
            if row is None: continue
            name_cell = row.find(attrs={'data-label': 'Player'}) or row.find(attrs={'data-label': 'Name'})
            ovr_cell = row.find(attrs={'data-label': 'Overall'}) or row.find(attrs={'data-label': 'OVR'})
            if not name_cell or not ovr_cell: continue
            # Full name extraction (same wiki quirk as top-100)
            p_span = name_cell.select_one('[class*="playerNameLink"]')
            if p_span:
                player_name = p_span.get_text(strip=True)
            else:
                parts = [t.strip() for t in name_cell.get_text('\n').splitlines() if t.strip()]
                player_name = max(parts, key=len) if parts else ''
            ovr = _int(ovr_cell)
            if player_name and ovr is not None:
                qbs.append({'name': player_name, 'ovr': ovr})
        if not qbs:
            print(f'  ⚠ {code} ({slug}): no QBs parsed')
            time.sleep(0.5); continue
        # Highest OVR = starter
        starter = max(qbs, key=lambda q: q['ovr'])
        out.append({'team_code': code, 'player_name': starter['name'], 'ovr': starter['ovr']})
        time.sleep(0.5)  # polite crawl
    return out


def upsert_starter_qbs(qbs: list[dict], season: int, week: int, dry_run: bool) -> int:
    """Upsert to nfl_madden_player_ratings so enrich_ctx_nfl_madden.load_qb_ratings
    picks them up. Same schema as upsert_players. Merge duplicates via
    (player_name, team, season, week_snapshot) unique key."""
    if not qbs: return 0
    fetched = datetime.now(timezone.utc).isoformat()
    payload = [{
        'player_name': q['player_name'], 'team': q['team_code'],
        'season': season, 'week_snapshot': week,
        'position': 'QB', 'ovr': q['ovr'],
        'source': 'madden27.wiki:team_page', 'fetched_at': fetched,
    } for q in qbs]
    if dry_run:
        print(f'  [DRY] would upsert {len(payload)} starter QB rows')
        return len(payload)
    r = requests.post(f'{SB}/rest/v1/nfl_madden_player_ratings?on_conflict=player_name,team,season,week_snapshot',
                      headers=H_WRITE, json=payload, timeout=30)
    if r.status_code not in (200, 201, 204):
        print(f'  starter QB upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(payload)


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

    # 2026-09-06 QB pass — 32 team-page fetches to backfill starter QB
    # coverage for all 32 teams (top-100 alone covers ~8 QBs, leaving
    # 24 team starters unmapped and blocking home_qb_madden_ovr).
    print('  → starter QBs (32 team-page fetches, ~20s)')
    qbs = scrape_starter_qbs()
    n_qbs = upsert_starter_qbs(qbs, args.season, args.week, args.dry_run)
    print(f'✓ wrote {n_teams} team ratings, {n_players} player ratings, {n_top100} top-100 rows, {n_qbs} starter QBs')
    return 0


if __name__ == '__main__':
    sys.exit(main())
