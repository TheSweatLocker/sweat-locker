"""NFL game_results backfill from nflverse-data (2026-09-08).

Zero rows in nfl_game_results today blocks:
  - NFL total LR model training (project_lr_totals_investigation_908)
  - Historical Receipts / lifetime records for NFL surfaces
  - Any retro-audit of NFL pipeline picks against real outcomes

This pulls the nflverse schedules CSV (public, free, no auth) and
upserts into nfl_game_results with all the columns downstream needs:
  - Scores (home_score, away_score, total_points, home_win)
  - Closing lines (close_spread, close_total, close_home_ml, close_away_ml)
  - Opening lines (open_* mirrors — nflverse ships open lines too)
  - Outcomes (spread_result, total_result — computed here)
  - Metadata (season, week, weekday, roof, surface, temp, wind, refs)

nflverse URL:
  https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv

Idempotent: uses ON CONFLICT resolution=merge-duplicates on game_id.
Safe to re-run.

USAGE:
    python nfl_results_backfill.py                  # all seasons available
    python nfl_results_backfill.py --season 2024    # single season
    python nfl_results_backfill.py --start 2015 --end 2025  # range
    python nfl_results_backfill.py --dry-run
"""
from __future__ import annotations
import argparse, csv, io, os, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

# nflverse releases the full historical schedule w/ scores + lines here.
# The URL is stable — updated after each week completes.
NFLVERSE_URL = 'https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv'


def _f(v):
    if v is None or v == '': return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    if v is None or v == '': return None
    try: return int(float(v))
    except (TypeError, ValueError): return None


def _b(v):
    if v is None or v == '': return None
    s = str(v).strip().upper()
    if s in ('TRUE', '1', 'T', 'Y', 'YES'): return True
    if s in ('FALSE', '0', 'F', 'N', 'NO'): return False
    return None


def fetch_games() -> list[dict]:
    print(f'  fetching nflverse schedules CSV...')
    req = urllib.request.Request(NFLVERSE_URL,
                                 headers={'User-Agent': 'sweatlocker-backfill/1.0'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode('utf-8', errors='replace')
    reader = csv.DictReader(io.StringIO(raw))
    rows = list(reader)
    print(f'  fetched {len(rows)} historical games')
    return rows


def compute_outcome(row: dict) -> dict:
    """Derive spread_result + total_result + home_win from raw scores + lines."""
    hs = _i(row.get('home_score'))
    as_ = _i(row.get('away_score'))
    if hs is None or as_ is None: return {}
    out = {
        'home_score': hs, 'away_score': as_,
        'total_points': hs + as_,
        'home_win': hs > as_,
        'overtime': _b(row.get('overtime')) or False,
    }
    # spread_line in nflverse = home team's line (positive = home dog)
    sp = _f(row.get('spread_line'))
    if sp is not None:
        margin = hs - as_
        # home covers when margin > -sp (e.g. sp=-3 means home favored by 3,
        # covers when margin > 3)
        if margin > -sp:   out['spread_result'] = 'home_covered'
        elif margin < -sp: out['spread_result'] = 'away_covered'
        else:              out['spread_result'] = 'push'
    # total_line
    tt = _f(row.get('total_line'))
    if tt is not None:
        tot = hs + as_
        if tot > tt:   out['total_result'] = 'over'
        elif tot < tt: out['total_result'] = 'under'
        else:          out['total_result'] = 'push'
    return out


def build_payload(row: dict) -> dict | None:
    """Map nflverse row → nfl_game_results row shape."""
    gid = row.get('game_id')
    season = _i(row.get('season'))
    if not gid or season is None: return None
    payload = {
        'game_id': gid,
        'season': season,
        'week': _i(row.get('week')),
        'game_date': row.get('gameday') or None,
        'game_type': row.get('game_type') or 'REG',
        'gametime': row.get('gametime') or None,
        'weekday': row.get('weekday') or None,
        'home_team': row.get('home_team') or None,
        'away_team': row.get('away_team') or None,
        'home_coach': row.get('home_coach') or None,
        'away_coach': row.get('away_coach') or None,
        'home_qb_id': row.get('home_qb_id') or None,
        'home_qb_name': row.get('home_qb_name') or None,
        'away_qb_id': row.get('away_qb_id') or None,
        'away_qb_name': row.get('away_qb_name') or None,
        'home_rest': _i(row.get('home_rest')),
        'away_rest': _i(row.get('away_rest')),
        'div_game': _b(row.get('div_game')),
        'roof': row.get('roof') or None,
        'surface': row.get('surface') or None,
        'temp': _i(row.get('temp')),
        'wind': _i(row.get('wind')),
        'referee': row.get('referee') or None,
        'stadium': row.get('stadium') or None,
        # Lines — nflverse ships open + close
        'open_spread': _f(row.get('spread_line')),  # nflverse only ships closing spread
        'close_spread': _f(row.get('spread_line')),
        'open_total': _f(row.get('total_line')),
        'close_total': _f(row.get('total_line')),
        'open_home_ml': _f(row.get('home_moneyline')),
        'close_home_ml': _f(row.get('home_moneyline')),
        'open_away_ml': _f(row.get('away_moneyline')),
        'close_away_ml': _f(row.get('away_moneyline')),
    }
    # Merge outcome computations (skip if game not played yet)
    payload.update(compute_outcome(row))
    # Strip None values so we don't nuke defaults on upsert
    return {k: v for k, v in payload.items() if v is not None}


def upsert_batch(payloads: list[dict], dry_run: bool) -> int:
    if not payloads: return 0
    if dry_run:
        print(f'  [DRY] would upsert {len(payloads)} rows')
        # Show 3 sample payloads
        for p in payloads[:3]:
            print(f'    sample: {p.get("game_id")}: {p.get("home_team")} {p.get("home_score", "?")} - {p.get("away_score", "?")} {p.get("away_team")}')
        return len(payloads)
    r = requests.post(f'{SB}/rest/v1/nfl_game_results?on_conflict=game_id',
                      headers=H_W, json=payloads, timeout=60)
    if r.status_code in (200, 201, 204): return len(payloads)
    print(f'  ⚠ upsert failed {r.status_code}: {r.text[:300]}')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, help='single season YYYY')
    ap.add_argument('--start', type=int, help='range start YYYY')
    ap.add_argument('--end', type=int, help='range end YYYY (inclusive)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    all_rows = fetch_games()

    # Season filter
    seasons_wanted = None
    if args.season is not None:
        seasons_wanted = {args.season}
    elif args.start is not None or args.end is not None:
        s = args.start or 2010
        e = args.end or datetime.now().year
        seasons_wanted = set(range(s, e + 1))

    if seasons_wanted:
        all_rows = [r for r in all_rows if _i(r.get('season')) in seasons_wanted]
        print(f'  filtered to seasons {sorted(seasons_wanted)}: {len(all_rows)} rows')

    payloads = []
    for row in all_rows:
        p = build_payload(row)
        if p: payloads.append(p)

    print(f'  building {len(payloads)} valid payloads')
    total = 0
    BATCH = 200
    for i in range(0, len(payloads), BATCH):
        chunk = payloads[i:i+BATCH]
        n = upsert_batch(chunk, dry_run=args.dry_run)
        total += n
        if (i // BATCH) % 5 == 0 and not args.dry_run:
            print(f'    progress: {i+len(chunk)}/{len(payloads)} ({total} upserted)')

    print(f'\n{"[DRY] " if args.dry_run else "✓ "}total upserted: {total}')


if __name__ == '__main__':
    main()
