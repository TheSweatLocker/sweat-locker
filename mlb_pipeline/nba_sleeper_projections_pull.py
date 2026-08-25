"""NBA Sleeper projections puller (2026-08-17).

Mirrors nfl_sleeper_projections_pull.py. Sleeper's free NBA projections
provide season per-game averages for pts/reb/ast/stl/blk/fgm/fga/etc.

Endpoints:
  GET https://api.sleeper.app/v1/players/nba
    → All active NBA players (metadata: player_id → name, team, position)
  GET https://api.sleeper.app/v1/projections/nba/regular/{season}
    → Per-player season per-game projections

Sample projection keys observed 2026-08-17:
  pts, reb, ast, stl, blk, fgm, fga, ftm, fta, plus_minus,
  pts_reb, pts_ast, reb_ast, pts_reb_ast, pts_std, pts_std_dfs,
  adp_dynasty, adp_std

CLI:
    python nba_sleeper_projections_pull.py --season 2024
    python nba_sleeper_projections_pull.py --season 2025 --dry-run
"""
from __future__ import annotations
import argparse, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

SLEEPER_PLAYERS_URL = 'https://api.sleeper.app/v1/players/nba'
SLEEPER_PROJ_URL = 'https://api.sleeper.app/v1/projections/nba/regular/{season}'


def fetch_players() -> dict:
    """Player metadata: {sleeper_id: {full_name, team, position}}."""
    r = requests.get(SLEEPER_PLAYERS_URL, timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ players fetch: {r.status_code}')
        return {}
    return r.json()


def fetch_projections(season: str) -> dict:
    r = requests.get(SLEEPER_PROJ_URL.format(season=season), timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ projections fetch: {r.status_code}')
        return {}
    return r.json()


def _current_nba_season() -> str:
    """Current NBA season label using Sleeper's convention (start-year)."""
    now = datetime.now()
    # Sleeper uses start year (2024 = 2024-25 season)
    if now.month >= 8: return str(now.year)
    return str(now.year - 1)


def _get_threes_estimate(proj: dict, position: str | None) -> float | None:
    """Sleeper doesn't publish 3-point makes directly. Estimate from
    fgm × position-specific 3P rate. Rough — should be refined later
    if we ship 3PM prop signals in production."""
    fgm = proj.get('fgm')
    if fgm is None: return None
    # Position-based 3P share estimates (from league-wide averages)
    share = {'PG': 0.35, 'SG': 0.40, 'SF': 0.35, 'PF': 0.20, 'C': 0.10}.get(position, 0.25)
    return round(float(fgm) * share, 2)


def run(season: str | None = None, dry_run: bool = False):
    season = season or _current_nba_season()
    print(f'=== nba_sleeper_projections_pull · season {season} ===')

    print('  fetching player metadata...')
    players = fetch_players()
    print(f'  {len(players)} total players in Sleeper NBA registry')

    print('  fetching projections...')
    projections = fetch_projections(season)
    non_empty = [(pid, p) for pid, p in projections.items() if isinstance(p, dict) and p]
    print(f'  {len(non_empty)} players with real projections (of {len(projections)} total)')

    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for pid, proj in non_empty:
        pmeta = players.get(pid, {})
        full_name = pmeta.get('full_name') or pmeta.get('first_name','') + ' ' + pmeta.get('last_name','')
        full_name = full_name.strip()
        if not full_name: continue
        position = pmeta.get('position')
        team = pmeta.get('team')

        # Derive PRA-style combos + threes estimate
        pts = proj.get('pts'); reb = proj.get('reb'); ast = proj.get('ast')
        payload = {
            'sleeper_id':     pid,
            'player_name':    full_name,
            'team_abbrev':    team,
            'position':       position,
            'season':         season,
            'proj_pts':       pts,
            'proj_reb':       reb,
            'proj_ast':       ast,
            'proj_stl':       proj.get('stl'),
            'proj_blk':       proj.get('blk'),
            'proj_threes':    _get_threes_estimate(proj, position),
            'proj_fga':       proj.get('fga'),
            'proj_fgm':       proj.get('fgm'),
            'proj_fta':       proj.get('fta'),
            'proj_ftm':       proj.get('ftm'),
            'proj_turnovers': proj.get('to'),
            'proj_minutes':   proj.get('min'),
            # Combos
            'proj_pra':       proj.get('pts_reb_ast'),
            'proj_pr':        proj.get('pts_reb'),
            'proj_pa':        proj.get('pts_ast'),
            'proj_ra':        proj.get('reb_ast'),
            'updated_at':     now_iso,
        }
        payloads.append(payload)

    print(f'  built {len(payloads)} payload rows')
    if dry_run:
        for p in payloads[:5]:
            print(f'  [DRY] {p["player_name"]:<25} pts={p["proj_pts"]} reb={p["proj_reb"]} ast={p["proj_ast"]}')
        return

    written = 0
    for i in range(0, len(payloads), 100):
        chunk = payloads[i:i+100]
        pr = requests.post(f'{SB}/rest/v1/nba_player_projections?on_conflict=player_name,season',
                           headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204): written += len(chunk)
        else: print(f'  ✗ upsert: {pr.status_code} {pr.text[:200]}')

    print(f'\n  ✓ upserted {written} projections')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', help='Sleeper season (start year, e.g. 2024)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
