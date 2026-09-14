"""Backfill home_season_ats_* / away_season_ats_* + OU on nfl_game_context.

2026-09-14 · Andy: "for team stats and situational sections i want display
to reflect only this season from now on, blend last season in the background
for weighting."

Root cause: nfl_game_context.home_season_ats_wins / _losses / _cover_pct
(and OU + AWAY equivalents) are NULL for every 2026 Week 2 game. The
existing enrich_team_trends.py writer depends on teamrankings.com data,
which won't have 2026 aggregate stats until several weeks in. Early-season
Andy needs the fields populated FROM 2026 game_results directly.

Strategy:
  1. Pull all 2026 REG-season nfl_game_results with spread_result +
     total_result set (games with a settled outcome).
  2. Per team, aggregate:
       ats_wins   = games where THIS team covered (home_covered if home, away_covered if away)
       ats_losses = games where THIS team didn't cover
       ou_overs   = games where total went OVER
       ou_unders  = games where total went UNDER
     Push not counted in W/L.
  3. Compute cover_pct + over_pct.
  4. PATCH nfl_game_context rows for game_date >= today with the per-team
     season aggregates.

Idempotent — safe to rerun; overwrites existing values.

CLI:
  python backfill_nfl_season_records_from_results.py
  python backfill_nfl_season_records_from_results.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from collections import defaultdict
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

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_results(season: int) -> list:
    """All 2026 REG games with a settled spread + total."""
    rows: list = []
    for page in range(6):
        r = requests.get(f'{SB}/rest/v1/nfl_game_results',
                         params={'season': f'eq.{season}',
                                 'game_type': 'eq.REG',
                                 'spread_result': 'not.is.null',
                                 'total_result': 'not.is.null',
                                 'select': 'game_id,home_team,away_team,'
                                           'home_score,away_score,close_spread,close_total,'
                                           'spread_result,total_result'},
                         headers={**H_READ, 'Range-Unit': 'items',
                                  'Range': f'{page*1000}-{(page+1)*1000-1}'},
                         timeout=20)
        if r.status_code not in (200, 206): break
        page_rows = r.json() or []
        if not isinstance(page_rows, list) or not page_rows: break
        rows.extend(page_rows)
        if len(page_rows) < 1000: break
    return rows


def fetch_upcoming_ctx() -> list:
    """nfl_game_context rows for today + 8 days (the app's window)."""
    today = _et_today()
    horizon = (datetime.now(timezone.utc) + timedelta(days=8)).date().isoformat()
    r = requests.get(f'{SB}/rest/v1/nfl_game_context',
                     params={'and': f'(game_date.gte.{today},game_date.lte.{horizon})',
                             'select': 'game_id,game_date,home_team,away_team'},
                     headers=H_READ, timeout=15)
    return r.json() if r.status_code == 200 else []


def aggregate_by_team(results: list) -> dict:
    """Returns {team_abbr: {ats_wins, ats_losses, ou_overs, ou_unders, ats_pushes, ou_pushes}}."""
    agg: dict = defaultdict(lambda: {
        'ats_wins': 0, 'ats_losses': 0, 'ats_pushes': 0,
        'ou_overs': 0, 'ou_unders': 0, 'ou_pushes': 0,
    })
    for r in results:
        home = (r.get('home_team') or '').upper()
        away = (r.get('away_team') or '').upper()
        sr = str(r.get('spread_result') or '').lower()
        tr = str(r.get('total_result') or '').lower()
        # Spread: home_covered / away_covered / push
        if sr == 'home_covered':
            agg[home]['ats_wins']   += 1
            agg[away]['ats_losses'] += 1
        elif sr == 'away_covered':
            agg[away]['ats_wins']   += 1
            agg[home]['ats_losses'] += 1
        elif sr == 'push':
            agg[home]['ats_pushes'] += 1
            agg[away]['ats_pushes'] += 1
        # Total
        if tr == 'over':
            agg[home]['ou_overs']  += 1
            agg[away]['ou_overs']  += 1
        elif tr == 'under':
            agg[home]['ou_unders'] += 1
            agg[away]['ou_unders'] += 1
        elif tr == 'push':
            agg[home]['ou_pushes'] += 1
            agg[away]['ou_pushes'] += 1
    return dict(agg)


def _pct(w, l):
    denom = (w or 0) + (l or 0)
    return round(100.0 * (w or 0) / denom, 1) if denom else None


def patch_ctx(agg: dict, upcoming: list, dry_run: bool = False) -> int:
    patched = 0
    for g in upcoming:
        home = (g.get('home_team') or '').upper()
        away = (g.get('away_team') or '').upper()
        gid  = g.get('game_id')
        if not (home and away and gid): continue
        h = agg.get(home, {}); a = agg.get(away, {})
        payload = {
            'home_season_ats_wins':    h.get('ats_wins', 0),
            'home_season_ats_losses':  h.get('ats_losses', 0),
            'home_season_cover_pct':   _pct(h.get('ats_wins'), h.get('ats_losses')),
            'home_season_ou_overs':    h.get('ou_overs', 0),
            'home_season_ou_unders':   h.get('ou_unders', 0),
            'home_season_over_pct':    _pct(h.get('ou_overs'), h.get('ou_unders')),
            'away_season_ats_wins':    a.get('ats_wins', 0),
            'away_season_ats_losses':  a.get('ats_losses', 0),
            'away_season_cover_pct':   _pct(a.get('ats_wins'), a.get('ats_losses')),
            'away_season_ou_overs':    a.get('ou_overs', 0),
            'away_season_ou_unders':   a.get('ou_unders', 0),
            'away_season_over_pct':    _pct(a.get('ou_overs'), a.get('ou_unders')),
        }
        marker = 'DRY' if dry_run else 'PATCH'
        print(f'  [{marker}]  {g.get("game_date")}  {away:4s}@{home:4s}  '
              f'H {payload["home_season_ats_wins"]}-{payload["home_season_ats_losses"]} '
              f'({payload["home_season_cover_pct"] or "—"}% cover, '
              f'{payload["home_season_ou_overs"]}O-{payload["home_season_ou_unders"]}U)  '
              f'A {payload["away_season_ats_wins"]}-{payload["away_season_ats_losses"]} '
              f'({payload["away_season_cover_pct"] or "—"}% cover)')
        if dry_run: continue
        pr = requests.patch(f'{SB}/rest/v1/nfl_game_context?game_id=eq.{gid}',
                           headers=H_WRITE, json=payload, timeout=10)
        if pr.status_code in (200, 204): patched += 1
    return patched


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', type=int, default=2026)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    print(f'=== backfill_nfl_season_records_from_results · season={args.season}'
          f' · dry_run={args.dry_run} ===')
    results = fetch_results(args.season)
    print(f'  {len(results)} settled REG games in season {args.season}')
    if not results:
        print('  (nothing to aggregate — season may not have started or results not landed)')
        return
    agg = aggregate_by_team(results)
    print(f'  aggregated per-team records for {len(agg)} teams')
    upcoming = fetch_upcoming_ctx()
    print(f'  {len(upcoming)} upcoming ctx rows to patch\n')
    patched = patch_ctx(agg, upcoming, dry_run=args.dry_run)
    print(f'\n✓ patched {patched} rows')


if __name__ == '__main__':
    main()
