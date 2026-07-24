"""NCAAF backfill — pull historical CFB seasons into ncaaf_game_results.

Uses CollegeFootballData API (https://api.collegefootballdata.com).
Requires CFBD_API_KEY in .env — register free at
https://collegefootballdata.com/key

Endpoints used:
  /games          — schedule + outcome per game
  /lines          — market lines (spread/total/ML) per game

Loads 2022-2025 seasons for cohort baseline. Season 2026 populates as
weeks complete via the weekly workflow.

USAGE:
    python ncaaf_backfill_results.py                       # 2025 only (default)
    python ncaaf_backfill_results.py --start 2022 --end 2025
    python ncaaf_backfill_results.py --season 2024
"""
import argparse
import os
import sys
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
CFBD_KEY = os.environ.get('CFBD_API_KEY')

H_READ = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

CFBD_BASE = 'https://api.collegefootballdata.com'


def _f(v):
    try: return float(v) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(float(v)) if v not in (None, '') else None
    except (TypeError, ValueError): return None


def cfbd_get(path: str, params: dict) -> list:
    if not CFBD_KEY:
        print(f'  ✗ CFBD_API_KEY missing — register at collegefootballdata.com/key')
        return []
    r = requests.get(
        f'{CFBD_BASE}{path}',
        headers={'Authorization': f'Bearer {CFBD_KEY}'},
        params=params, timeout=30,
    )
    if r.status_code != 200:
        print(f'  ⚠ CFBD {path} {r.status_code}: {r.text[:120]}')
        return []
    return r.json()


def fetch_games(season: int, season_type: str = 'both') -> list:
    """Pull games for a season (regular + postseason unless filtered)."""
    all_games = []
    for st in (['regular', 'postseason'] if season_type == 'both' else [season_type]):
        games = cfbd_get('/games', {'year': season, 'seasonType': st, 'division': 'fbs'})
        for g in games:
            g['_source_season_type'] = st
        all_games.extend(games)
    return all_games


def fetch_lines(season: int) -> dict:
    """Pull market lines. Returns {game_id: line_dict}."""
    lines = cfbd_get('/lines', {'year': season, 'seasonType': 'both'})
    out = {}
    for L in lines:
        gid = L.get('id') or L.get('gameId')
        if not gid: continue
        # Use consensus or median across bookmakers
        line_objs = L.get('lines') or []
        if not line_objs: continue
        # Take first bookmaker for simplicity (consensus is often first)
        book = line_objs[0]
        out[gid] = {
            'close_spread': _f(book.get('spread')),
            'close_total': _f(book.get('overUnder')),
            'close_home_ml': _i(book.get('homeMoneyline')),
            'close_away_ml': _i(book.get('awayMoneyline')),
        }
    return out


def transform(game: dict, line: Optional[dict]) -> Optional[dict]:
    """CFBD game → ncaaf_game_results row."""
    gid = game.get('id')
    if not gid: return None
    home_score = _i(game.get('home_points') or game.get('homePoints'))
    away_score = _i(game.get('away_points') or game.get('awayPoints'))
    row = {
        'game_id': f'cfbd_{gid}',
        'season': _i(game.get('season')),
        'season_type': game.get('_source_season_type') or 'regular',
        'week': _i(game.get('week')),
        'game_date': (game.get('start_date') or game.get('startDate') or '')[:10],
        'kickoff_utc': game.get('start_date') or game.get('startDate'),
        'home_team': game.get('home_team') or game.get('homeTeam'),
        'away_team': game.get('away_team') or game.get('awayTeam'),
        'neutral_site': bool(game.get('neutral_site') or game.get('neutralSite')),
        'conference_game': bool(game.get('conference_game') or game.get('conferenceGame')),
        'venue': game.get('venue'),
        'home_score': home_score,
        'away_score': away_score,
        'attendance': _i(game.get('attendance')),
    }
    # Compute outcomes when both scores present
    if home_score is not None and away_score is not None:
        row['total_points'] = home_score + away_score
        row['home_win'] = home_score > away_score
    # Merge line data (already flipped source-side? Check CFBD convention)
    # CFBD spread convention: positive = home is DOG. Same as MLB. No flip needed.
    if line:
        row.update({k: v for k, v in line.items() if v is not None})
        row.setdefault('open_spread', row.get('close_spread'))
        row.setdefault('open_total', row.get('close_total'))
        # Compute spread_result + total_result when both scores + line present
        if home_score is not None and away_score is not None:
            cs = row.get('close_spread')
            ct = row.get('close_total')
            margin = home_score - away_score
            if cs is not None:
                # positive close_spread = home dog → home covers when margin > -cs
                if margin > -float(cs):   row['spread_result'] = 'home_covered'
                elif margin < -float(cs): row['spread_result'] = 'away_covered'
                else:                     row['spread_result'] = 'push'
            if ct is not None:
                tot = home_score + away_score
                if tot > float(ct):   row['total_result'] = 'over'
                elif tot < float(ct): row['total_result'] = 'under'
                else:                 row['total_result'] = 'push'
    return row


def upsert(rows: list) -> int:
    if not rows: return 0
    # Chunk 200 to keep POST body reasonable
    total = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i+200]
        r = requests.post(
            f'{SB}/rest/v1/ncaaf_game_results?on_conflict=game_id',
            headers=H_WRITE, json=chunk, timeout=60,
        )
        if r.status_code in (200, 201, 204):
            total += len(chunk)
            print(f'  ✓ upserted batch {i//200+1}: {len(chunk)} rows ({total} total)')
        else:
            print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
    return total


def run(seasons: list) -> None:
    print(f'=== NCAAF backfill (seasons {seasons}) ===')
    if not CFBD_KEY:
        print('  ✗ CFBD_API_KEY missing — abort')
        print('     Register free at https://collegefootballdata.com/key')
        print('     Then add CFBD_API_KEY=your_key to mlb_pipeline/.env')
        return
    for season in seasons:
        print(f'\n--- Season {season} ---')
        games = fetch_games(season)
        print(f'  games fetched: {len(games)}')
        if not games:
            continue
        lines = fetch_lines(season)
        print(f'  lines fetched: {len(lines)}')
        rows = []
        for g in games:
            row = transform(g, lines.get(g.get('id')))
            if row: rows.append(row)
        print(f'  valid rows: {len(rows)}')
        upsert(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=None)
    ap.add_argument('--start', type=int, default=None)
    ap.add_argument('--end', type=int, default=None)
    args = ap.parse_args()
    if args.season:
        seasons = [args.season]
    elif args.start and args.end:
        seasons = list(range(args.start, args.end + 1))
    else:
        seasons = [2025]
    run(seasons)


if __name__ == '__main__':
    main()
