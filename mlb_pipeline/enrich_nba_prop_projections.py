"""Enrich today's NBA props with Sleeper projected values + edge vs book line (2026-08-17).

For each nba_pipeline_props row today:
  1. Look up player in nba_player_projections
  2. Map prop_type → projected field (pts_over → proj_pts, etc.)
  3. Compute proj_vs_line_edge (projected - book_line)
  4. Patch onto prop row so signals in signal_sources can read via ctx.

Runs AFTER nba_sleeper_projections_pull.py + nba_generate_props.py in
workflow, BEFORE prop_ensemble_scorer.py.

CLI:
  python enrich_nba_prop_projections.py                 # today
  python enrich_nba_prop_projections.py --date 2026-10-25
  python enrich_nba_prop_projections.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Prop type base → projections column
PROP_TO_PROJ = {
    'pts':      'proj_pts',
    'reb':      'proj_reb',
    'ast':      'proj_ast',
    'stl':      'proj_stl',
    'blk':      'proj_blk',
    'threes':   'proj_threes',
    'turnovers': 'proj_turnovers',
    'pra':      'proj_pra',
    'pr':       'proj_pr',
    'pa':       'proj_pa',
    'ra':       'proj_ra',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _current_season() -> str:
    now = datetime.now()
    return str(now.year if now.month >= 8 else now.year - 1)


def run(game_date: str | None = None, season: str | None = None, dry_run: bool = False):
    gd = game_date or _et_today()
    season = season or _current_season()
    print(f'=== enrich_nba_prop_projections · {gd} · season {season} ===')

    # 1. Load projections into name-indexed lookup
    r = requests.get(f'{SB}/rest/v1/nba_player_projections'
                     f'?season=eq.{season}&select=*&limit=1000',
                     headers=H_READ, timeout=15)
    projs = r.json() if r.status_code == 200 else []
    if not projs:
        print(f'  no projections for season {season} — run nba_sleeper_projections_pull.py first')
        return

    proj_by_name = {p['player_name'].lower(): p for p in projs}
    print(f'  {len(proj_by_name)} player projections loaded')

    # 2. Load today's props
    r = requests.get(f'{SB}/rest/v1/nba_pipeline_props'
                     f'?game_date=eq.{gd}&select=id,player_name,prop_type,direction,prop_line,book_line',
                     headers=H_READ, timeout=15)
    props = r.json() if r.status_code == 200 else []
    if not props:
        print(f'  no props on {gd}')
        return
    print(f'  {len(props)} props on {gd}')

    now_iso = datetime.now(timezone.utc).isoformat()
    matched = 0; patched = 0
    for prop in props:
        pname_lc = (prop.get('player_name') or '').lower()
        proj = proj_by_name.get(pname_lc)
        if not proj: continue
        matched += 1
        # Map prop_type base → projection field
        base = (prop.get('prop_type') or '').split('_')[0]  # 'pts_over' → 'pts'
        proj_col = PROP_TO_PROJ.get(base)
        if not proj_col: continue
        projected_value = proj.get(proj_col)
        if projected_value is None: continue

        book_line = prop.get('prop_line')
        try: book_line = float(book_line)
        except (TypeError, ValueError): continue

        edge = round(float(projected_value) - book_line, 2)

        # 2026-08-22 FIX (silent-bug audit finding #1): _ship_nba_nhl_props.py
        # reads `p.get('projection')` but this script was only writing
        # `projected_value`. Every NBA prop projection signal
        # (nba_prop_projection_supports/_opposes/_strong) returned None
        # → zero signal contribution. Now write BOTH keys so existing readers
        # keep working AND the ensemble sees the projection.
        patch = {
            'projection':            float(projected_value),
            'projected_value':       float(projected_value),  # legacy field kept for backward compat
            'proj_vs_line_edge':     edge,
            'projections_updated_at': now_iso,
        }
        if not dry_run:
            pr = requests.patch(f'{SB}/rest/v1/nba_pipeline_props?id=eq.{prop["id"]}',
                                headers=H_WRITE, json=patch, timeout=10)
            if pr.status_code in (200, 204): patched += 1

        # Log the big edges
        if abs(edge) >= 2.0:
            direction_emoji = '🎯' if (edge > 0 and prop['direction']=='over') or (edge < 0 and prop['direction']=='under') else '🚫'
            print(f'  {direction_emoji} {prop["player_name"][:22]:<22} {prop["prop_type"]:<10} '
                  f'line={book_line:<5} proj={projected_value}  edge={edge:+g}')

    print(f'\n  {"[DRY] " if dry_run else ""}matched {matched}/{len(props)} · patched {patched}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date'); p.add_argument('--season')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
