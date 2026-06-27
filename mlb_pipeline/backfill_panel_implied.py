"""Backfill panel_implied_total + panel_implied_margin into mlb_game_results.

Migration 20260627_results_panel_implied.sql added the columns; this script
populates them from the matching mlb_game_context rows. Re-runs idempotent:
PATCHes only rows where panel_implied_total is currently NULL OR the
mlb_game_context projection inputs have changed since last write.

Designed to run nightly post-resolver. Standalone CLI for manual backfill.

Computation matches play_of_day.py `_panel_implied`:
  away_bp_ip   = max(0, 9 - away_pitcher_projected_outs / 3)
  home_bp_ip   = max(0, 9 - home_pitcher_projected_outs / 3)
  home_scores  = away_pitcher_projected_er + away_bullpen_era * away_bp_ip / 9
  away_scores  = home_pitcher_projected_er + home_bullpen_era * home_bp_ip / 9
  panel_implied_total  = home_scores + away_scores
  panel_implied_margin = home_scores - away_scores  (+ = home wins)
"""
import os
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}


def compute_panel(asp_er, hsp_er, asp_outs, hsp_outs, a_bp, h_bp):
    """Return (panel_implied_total, panel_implied_margin) or (None, None)."""
    if asp_er is None or hsp_er is None:
        return None, None
    try:
        asp_er = float(asp_er); hsp_er = float(hsp_er)
        asp_outs = float(asp_outs) if asp_outs is not None else 15.0
        hsp_outs = float(hsp_outs) if hsp_outs is not None else 15.0
        a_bp = float(a_bp) if a_bp is not None else 4.10
        h_bp = float(h_bp) if h_bp is not None else 4.10
        away_bp_ip = max(0, 9 - asp_outs / 3)
        home_bp_ip = max(0, 9 - hsp_outs / 3)
        home_scores = asp_er + a_bp * away_bp_ip / 9
        away_scores = hsp_er + h_bp * home_bp_ip / 9
        return round(home_scores + away_scores, 2), round(home_scores - away_scores, 2)
    except (TypeError, ValueError):
        return None, None


def fetch_results(date_filter=None, only_null=True):
    """Pull mlb_game_results rows that need panel_implied populated."""
    rows = []
    off = 0
    filt = '&panel_implied_total=is.null' if only_null else ''
    if date_filter:
        filt += f'&game_date={date_filter}'
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?home_score=not.is.null'
            f'{filt}'
            f'&select=id,game_id,game_date,home_team,away_team,'
            f'panel_implied_total,panel_implied_margin'
            f'&order=game_date.desc&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def fetch_context_for_game(game_id):
    """Pull mlb_game_context row for a given game_id."""
    r = requests.get(
        f'{SU}/rest/v1/mlb_game_context?game_id=eq.{game_id}'
        f'&select=game_id,away_pitcher_projected_er,home_pitcher_projected_er,'
        f'away_pitcher_projected_outs,home_pitcher_projected_outs,'
        f'away_bullpen_era,home_bullpen_era&limit=1',
        headers=H, timeout=15,
    )
    if r.status_code != 200: return None
    rows = r.json()
    return rows[0] if rows else None


def patch_result(row_id, payload):
    """PATCH mlb_game_results row by id."""
    r = requests.patch(
        f'{SU}/rest/v1/mlb_game_results?id=eq.{row_id}',
        headers={**H, 'Content-Type': 'application/json',
                 'Prefer': 'return=minimal'},
        json=payload, timeout=15,
    )
    return r.status_code in (200, 204)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='only backfill a single date (YYYY-MM-DD) or use gte/lte (e.g. gte.2026-06-01)')
    ap.add_argument('--all', action='store_true',
                    help='backfill all rows, even ones that already have panel values')
    ap.add_argument('--dry-run', action='store_true',
                    help='print computed values but do not PATCH')
    args = ap.parse_args()

    date_filter = None
    if args.date:
        date_filter = f'eq.{args.date}' if '.' not in args.date else args.date

    rows = fetch_results(date_filter=date_filter, only_null=not args.all)
    print(f'Found {len(rows)} mlb_game_results rows to consider')

    patched = 0
    skipped_no_inputs = 0
    failed = 0
    for r in rows:
        ctx = fetch_context_for_game(r['game_id'])
        if not ctx:
            skipped_no_inputs += 1
            continue
        total, margin = compute_panel(
            ctx.get('away_pitcher_projected_er'),
            ctx.get('home_pitcher_projected_er'),
            ctx.get('away_pitcher_projected_outs'),
            ctx.get('home_pitcher_projected_outs'),
            ctx.get('away_bullpen_era'),
            ctx.get('home_bullpen_era'),
        )
        if total is None:
            skipped_no_inputs += 1
            continue
        payload = {
            'panel_implied_total': total,
            'panel_implied_margin': margin,
            'away_pitcher_projected_er': ctx.get('away_pitcher_projected_er'),
            'home_pitcher_projected_er': ctx.get('home_pitcher_projected_er'),
            'away_pitcher_projected_outs': ctx.get('away_pitcher_projected_outs'),
            'home_pitcher_projected_outs': ctx.get('home_pitcher_projected_outs'),
        }
        if args.dry_run:
            print(f"  DRY  {r['game_date']} {r['away_team'][:14]:14s} @ {r['home_team'][:14]:14s} -> total {total}, margin {margin:+}")
            patched += 1
        else:
            ok = patch_result(r['id'], payload)
            if ok:
                patched += 1
            else:
                failed += 1
                print(f"  FAIL {r['game_date']} {r['away_team']} @ {r['home_team']}")

    print(f"\n  Patched: {patched}")
    print(f"  Skipped (no projection inputs): {skipped_no_inputs}")
    if failed: print(f"  Failed: {failed}")


if __name__ == '__main__':
    main()
