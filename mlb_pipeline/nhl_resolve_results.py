"""NHL results resolver (2026-08-17).

Grades completed NHL games from the free NHL API scoreboard endpoint.
For each finalized game on the target date:
  1. Pull final home_score / away_score / went_to_ot / went_to_so
  2. Compute spread_result vs close_puckline (typically -1.5/+1.5)
  3. Compute total_result vs close_total (typically 5.5 or 6.5)
  4. Compute home_win boolean
  5. Upsert to nhl_game_results

Pushes on spread when margin exactly hits line (rare on -1.5 puckline
but happens on integer alt lines). Pushes on total when total_goals
exactly equals close_total.

CLI:
  python nhl_resolve_results.py                  # yesterday ET
  python nhl_resolve_results.py --date 2024-10-08
  python nhl_resolve_results.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
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

sys.path.insert(0, str(Path(__file__).parent))
from nhl_data_client import get_scoreboard


def _yday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4, days=1)).date().isoformat()


def _grade_spread(home_score: int, away_score: int, puckline: float | None) -> str | None:
    """close_puckline is HOME perspective. NHL puckline is usually -1.5 for
    fav / +1.5 for dog. Home_covered if (home_score + puckline) > away_score
    (i.e., home covers the spread). Returns 'home_covered' | 'away_covered' |
    'push' | None (missing line)."""
    if puckline is None: return None
    margin = (home_score - away_score) + float(puckline)
    if margin > 0.001: return 'home_covered'
    if margin < -0.001: return 'away_covered'
    return 'push'


def _grade_total(home_score: int, away_score: int, close_total: float | None) -> str | None:
    if close_total is None: return None
    total = home_score + away_score
    if total > float(close_total): return 'over'
    if total < float(close_total): return 'under'
    return 'push'


def resolve(game_date: str, dry_run: bool = False) -> int:
    print(f'=== nhl_resolve_results · {game_date} ===')
    finals = get_scoreboard(game_date)
    print(f'  {len(finals)} finalized games on {game_date}')
    if not finals: return 0

    # Get any pre-recorded lines from nhl_game_context so we can copy them
    # into results (for grading if not already on results).
    game_ids = [f['game_id'] for f in finals]
    r = requests.get(f'{SB}/rest/v1/nhl_game_context',
                     headers=H_READ,
                     params={'game_id': f'in.({",".join(game_ids)})',
                             'select': 'game_id,close_puckline,close_total,home_ml_close,away_ml_close,home_team,away_team'},
                     timeout=15)
    ctx_by_gid = {c['game_id']: c for c in (r.json() if r.status_code == 200 else [])}

    now_iso = datetime.now(timezone.utc).isoformat()
    written = 0
    for f in finals:
        gid = f['game_id']
        hs = f['home_score']; as_ = f['away_score']
        if hs is None or as_ is None: continue

        ctx = ctx_by_gid.get(gid, {})
        puckline = ctx.get('close_puckline')
        total = ctx.get('close_total')

        payload = {
            'game_id':       gid,
            'game_date':     game_date,
            'home_team':     ctx.get('home_team'),
            'away_team':     ctx.get('away_team'),
            'home_score':    hs,
            'away_score':    as_,
            'total_goals':   hs + as_,
            'home_win':      hs > as_,
            'went_to_ot':    f.get('went_to_ot', False),
            'went_to_so':    f.get('went_to_so', False),
            'close_puckline': puckline,
            'close_total':    total,
            'close_home_ml':  ctx.get('home_ml_close'),
            'close_away_ml':  ctx.get('away_ml_close'),
            'spread_result':  _grade_spread(hs, as_, puckline),
            'total_result':   _grade_total(hs, as_, total),
            'resolved_at':    now_iso,
        }

        note = ''
        if payload['spread_result']: note += f' spread={payload["spread_result"]}'
        if payload['total_result']:  note += f' total={payload["total_result"]}'
        print(f'  {ctx.get("away_team","?"):<15} {as_}-{hs} {ctx.get("home_team","?"):<15} '
              f'{"OT" if f.get("went_to_ot") else "SO" if f.get("went_to_so") else "REG":<3}{note}')

        if dry_run: written += 1; continue

        pr = requests.post(f'{SB}/rest/v1/nhl_game_results?on_conflict=game_id',
                           headers=H_WRITE, json=[payload], timeout=15)
        if pr.status_code in (200, 201, 204): written += 1
        else: print(f'    ✗ upsert failed: {pr.status_code} {pr.text[:200]}')

    print(f'\n  {"[DRY] " if dry_run else ""}wrote {written}/{len(finals)} results')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    gd = args.date or _yday_et()
    resolve(gd, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
