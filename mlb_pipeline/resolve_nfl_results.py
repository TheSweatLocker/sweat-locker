"""NFL post-game resolver — grade nfl_game_picks + nfl_props against actual outcomes.

Runs on Tuesday morning cron (after MNF resolved) + Sunday night as sweep.

Flow:
  1. Refresh nflverse games.csv.gz for the current season (final scores land
     ~2h after each game). Upsert into nfl_game_results.
  2. For every nfl_game_picks row where result IS NULL AND game is graded:
       - spread pick: home_covers = margin > close_spread (nflverse convention)
       - total pick: over/under vs (home_score + away_score)
       - ml pick: home_win vs pick_side
       - skip pick: leave null (no grading)
  3. Same treatment for nfl_props once props start writing rows.

Idempotent: re-runs skip already-resolved rows unless --force-regrade.

USAGE:
    python resolve_nfl_results.py               # grade all ungraded picks
    python resolve_nfl_results.py --season 2025 # limit to specific season
    python resolve_nfl_results.py --force-regrade  # re-grade even resolved rows
    python resolve_nfl_results.py --dry-run
"""
import argparse
import csv
import gzip
import io
import os
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

NFLVERSE_SCHEDULES_URL = (
    'https://github.com/nflverse/nflverse-data/releases/download/'
    'schedules/games.csv.gz'
)


def _f(v):
    try: return float(v) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(float(v)) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def fetch_schedules_for_season(season: Optional[int] = None) -> list:
    """Pull the full nflverse schedules CSV and filter by season if given."""
    print(f'  Fetching {NFLVERSE_SCHEDULES_URL.split("/")[-1]}...')
    req = urllib.request.Request(NFLVERSE_SCHEDULES_URL,
                                 headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = gzip.decompress(r.read())
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8')))
    rows = list(reader)
    if season is not None:
        rows = [r for r in rows if _i(r.get('season')) == season]
    # Only completed games (home_score populated)
    rows = [r for r in rows if _i(r.get('home_score')) is not None]
    print(f'  Completed games: {len(rows)}')
    return rows


def refresh_results(schedules: list, dry_run: bool = False) -> int:
    """Upsert completed games' scores into nfl_game_results.

    We don't want to overwrite ALL the fields (nfl_odds_pull writes live
    lines that shouldn't get squashed). Only PATCH the outcome columns:
    home_score, away_score, total_points, home_win, overtime, spread_result,
    total_result.
    """
    if dry_run:
        print(f'  [DRY] would refresh scores for {len(schedules)} games')
        return len(schedules)

    updated = 0
    for row in schedules:
        gid = row.get('game_id')
        if not gid: continue
        home_score = _i(row.get('home_score'))
        away_score = _i(row.get('away_score'))
        if home_score is None or away_score is None: continue
        close_spread = _f(row.get('spread_line'))
        close_total = _f(row.get('total_line'))
        margin = home_score - away_score
        total_points = home_score + away_score
        home_win = margin > 0
        overtime = str(row.get('overtime') or '').lower() in ('1', 'true', 't', 'yes')

        spread_result = None
        if close_spread is not None:
            # nflverse: spread_line positive = home fav. Home covers when
            # margin > close_spread.
            if margin > close_spread:   spread_result = 'home_covered'
            elif margin < close_spread: spread_result = 'away_covered'
            else:                       spread_result = 'push'
        total_result = None
        if close_total is not None:
            if total_points > close_total:   total_result = 'over'
            elif total_points < close_total: total_result = 'under'
            else:                            total_result = 'push'

        payload = {
            'home_score': home_score,
            'away_score': away_score,
            'total_points': total_points,
            'home_win': home_win,
            'overtime': overtime,
            'spread_result': spread_result,
            'total_result': total_result,
        }
        r = requests.patch(
            f'{SB}/rest/v1/nfl_game_results?game_id=eq.{gid}',
            headers={**H_WRITE, 'Prefer': 'return=minimal'},
            json=payload, timeout=15,
        )
        if r.status_code in (200, 201, 204):
            updated += 1
    print(f'  ✓ refreshed {updated} game outcomes')
    return updated


def fetch_ungraded_picks(force_regrade: bool = False) -> list:
    """Pull nfl_game_picks rows needing resolution."""
    filt = '' if force_regrade else '&result=is.null'
    r = requests.get(
        f'{SB}/rest/v1/nfl_game_picks?pick_type=neq.skip{filt}'
        f'&select=pick_id,game_id,pick_type,pick_side,pick_line,tier,'
        f'close_spread,close_total,home_team,away_team',
        headers=H_READ, timeout=15,
    )
    return r.json() if r.status_code == 200 else []


def fetch_result_map(game_ids: list) -> dict:
    """Batch-fetch outcome rows for the referenced game_ids."""
    if not game_ids: return {}
    # PostgREST in.() has URL length limits; chunk 100 at a time.
    out = {}
    for i in range(0, len(game_ids), 100):
        chunk = game_ids[i:i+100]
        ids_str = ','.join(f'"{g}"' for g in chunk)
        r = requests.get(
            f'{SB}/rest/v1/nfl_game_results?game_id=in.({ids_str})'
            f'&select=game_id,home_score,away_score,home_win,spread_result,total_result,close_spread,close_total,total_points',
            headers=H_READ, timeout=15,
        )
        if r.status_code == 200:
            for row in r.json():
                out[row['game_id']] = row
    return out


def grade_pick(pick: dict, result: dict) -> Optional[str]:
    """Return 'W' | 'L' | 'P' | None (if game not yet resolved)."""
    if not result or result.get('home_score') is None:
        return None

    ptype = pick.get('pick_type')
    side = (pick.get('pick_side') or '').lower()

    if ptype == 'ml':
        home_win = bool(result.get('home_win'))
        if side == 'home': return 'W' if home_win else 'L'
        if side == 'away': return 'L' if home_win else 'W'

    elif ptype == 'spread':
        # nfl_game_results already computed spread_result relative to
        # close_spread using nflverse convention (positive = home fav).
        sr = result.get('spread_result')
        if not sr: return None
        if sr == 'push': return 'P'
        if side == 'home': return 'W' if sr == 'home_covered' else 'L'
        if side == 'away': return 'W' if sr == 'away_covered' else 'L'

    elif ptype == 'total':
        tr = result.get('total_result')
        if not tr: return None
        if tr == 'push': return 'P'
        if side == 'over': return 'W' if tr == 'over' else 'L'
        if side == 'under': return 'W' if tr == 'under' else 'L'

    return None


def resolve_picks(picks: list, result_map: dict, dry_run: bool = False) -> dict:
    """Grade and patch each pick. Returns tally dict."""
    tally = {'W': 0, 'L': 0, 'P': 0, 'pending': 0}
    for p in picks:
        gid = p['game_id']
        result = result_map.get(gid)
        grade = grade_pick(p, result)
        if grade is None:
            tally['pending'] += 1
            continue
        tally[grade] += 1
        if dry_run:
            print(f'  [DRY] {p["pick_id"][:8]}...  {p["pick_type"]}:{p["pick_side"]} → {grade}')
            continue
        payload = {
            'result': grade,
            'actual_spread': (result.get('home_score') or 0) - (result.get('away_score') or 0),
            'actual_total': result.get('total_points'),
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        }
        requests.patch(
            f'{SB}/rest/v1/nfl_game_picks?pick_id=eq.{p["pick_id"]}',
            headers={**H_WRITE, 'Prefer': 'return=minimal'},
            json=payload, timeout=15,
        )
    return tally


def resolve_props(force_regrade: bool = False, dry_run: bool = False) -> dict:
    """Grade nfl_props rows against nfl_player_stats final numbers.

    v1: OVER/UNDER for pass_yds / rush_yds / reception_yds / receptions.
    v1.1 anytime_td deferred (needs snap counts + red-zone target share).
    """
    filt = '' if force_regrade else '&result=is.null'
    r = requests.get(
        f'{SB}/rest/v1/nfl_props?pick_side=in.(OVER,UNDER){filt}'
        f'&select=prop_id,game_id,player_id,player_name,team,prop_type,'
        f'pick_side,pick_line,season,week',
        headers=H_READ, timeout=15,
    )
    props = r.json() if r.status_code == 200 else []
    if not props:
        return {'W': 0, 'L': 0, 'P': 0, 'pending': 0}

    # Prop type → nfl_player_stats column
    COL_MAP = {
        'pass_yds': 'passing_yards',
        'rush_yds': 'rushing_yards',
        'reception_yds': 'receiving_yards',
        'receptions': 'receptions',
    }

    tally = {'W': 0, 'L': 0, 'P': 0, 'pending': 0}
    for p in props:
        col = COL_MAP.get(p.get('prop_type'))
        if not col:
            tally['pending'] += 1
            continue
        pid = p.get('player_id')
        season = p.get('season')
        week = p.get('week')
        if not (pid and season and week):
            tally['pending'] += 1
            continue
        rr = requests.get(
            f'{SB}/rest/v1/nfl_player_stats'
            f'?player_id=eq.{pid}&season=eq.{season}&week=eq.{week}'
            f'&select={col}&limit=1',
            headers=H_READ, timeout=10,
        )
        rows = rr.json() if rr.status_code == 200 else []
        if not rows or rows[0].get(col) is None:
            tally['pending'] += 1
            continue
        actual = float(rows[0].get(col) or 0)
        line = float(p['pick_line'])
        if actual > line:   grade = 'W' if p['pick_side'] == 'OVER' else 'L'
        elif actual < line: grade = 'W' if p['pick_side'] == 'UNDER' else 'L'
        else:               grade = 'P'
        tally[grade] += 1
        if dry_run:
            print(f'  [DRY] {p["player_name"][:20]:20} {p["prop_type"]:14} {p["pick_side"]:5} {line} actual={actual} → {grade}')
            continue
        payload = {
            'result': grade,
            'actual_value': actual,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        }
        requests.patch(
            f'{SB}/rest/v1/nfl_props?prop_id=eq.{p["prop_id"]}',
            headers={**H_WRITE, 'Prefer': 'return=minimal'},
            json=payload, timeout=15,
        )
    return tally


def run(season: Optional[int] = None, force_regrade: bool = False,
        dry_run: bool = False) -> None:
    print(f'=== NFL results resolver ===')

    # 1. Refresh scores from nflverse
    schedules = fetch_schedules_for_season(season)
    refresh_results(schedules, dry_run=dry_run)

    # 2. Grade picks
    picks = fetch_ungraded_picks(force_regrade=force_regrade)
    print(f'  ungraded picks: {len(picks)}')
    game_ids = list({p['game_id'] for p in picks})
    result_map = fetch_result_map(game_ids)
    pick_tally = resolve_picks(picks, result_map, dry_run=dry_run)
    print(f'  picks: {pick_tally}')

    # 3. Grade props
    prop_tally = resolve_props(force_regrade=force_regrade, dry_run=dry_run)
    print(f'  props: {prop_tally}')

    # Summary
    prefix = '[DRY] ' if dry_run else '✓ '
    total_w = pick_tally['W'] + prop_tally['W']
    total_l = pick_tally['L'] + prop_tally['L']
    total_p = pick_tally['W'] + pick_tally['L'] + pick_tally['P']
    prop_total = prop_tally['W'] + prop_tally['L'] + prop_tally['P']
    print(f'\n{prefix}Summary: picks {pick_tally["W"]}-{pick_tally["L"]}-{pick_tally["P"]} '
          f'(n={total_p})  |  props {prop_tally["W"]}-{prop_tally["L"]}-{prop_tally["P"]} '
          f'(n={prop_total})')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=None,
                    help='Limit to a specific season (default: all completed games in schedules feed)')
    ap.add_argument('--force-regrade', action='store_true',
                    help='Re-grade rows that already have a result')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(season=args.season, force_regrade=args.force_regrade, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
