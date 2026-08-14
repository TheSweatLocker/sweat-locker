"""Line history writer — per-book odds trajectory (2026-08-13).

Complements write_line_snapshot.py (OddsCrowd sharp-flow snapshots) by
capturing PER-BOOK ODDS PRICES over time. That's the data behind the
Steam Room tab's price sparklines and the steam/RLM detector.

Two distinct time-series, both feed the Steam Room:
  write_line_snapshot → line_snapshot table → money%/bets%/divergence
                        over time (sharp/public flow)
  write_line_history  → line_history table  → per-book (line, price)
                        over time (odds drift, steam-move detection)

Runs after every odds pull. Reads today's odds_cache rows, iterates the
bookmakers[] arrays, snapshots one row per (game_id, market, book, side)
into line_history.

Sport-universal — SPORT_TO_CACHE_KEY_PREFIX covers all 7 registered sports.
Snapshot cadence matches whatever cron the caller runs at; the 14-day
retention on line_history keeps table size bounded.

CLI:
    python write_line_history.py [--sport MLB] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# odds_cache stores per-sport per-day rows with the full bookmakers[] array.
SPORT_TO_CACHE_KEY_PREFIX = {
    'MLB':   'odds_games_MLB_',
    'NFL':   'odds_games_NFL_',
    'NCAAF': 'odds_games_NCAAF_',
    'NCAAB': 'odds_games_NCAAB_',
    'NBA':   'odds_games_NBA_',
    'NHL':   'odds_games_NHL_',
    'UFC':   'odds_games_UFC_',
}


def snapshot_from_odds_cache(sport: str, dry_run: bool = False) -> int:
    prefix = SPORT_TO_CACHE_KEY_PREFIX.get(sport)
    if not prefix:
        print(f'  {sport}: no cache prefix mapping — skip'); return 0

    # Pull today's cache rows — most recent 20 is generous for one sport/day
    r = requests.get(f'{SB}/rest/v1/odds_cache', headers=H_READ,
        params={'cache_key': f'like.{prefix}%',
                'select': 'cache_key,data,fetched_at',
                'order': 'fetched_at.desc', 'limit': 20},
        timeout=15)
    if r.status_code != 200:
        print(f'  odds_cache read failed: {r.status_code}'); return 0
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        print(f'  {sport}: no odds_cache rows found'); return 0

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    seen_keys: set = set()  # dedup within this run — one row per (gid,market,book,side)

    batch: list = []
    for cache_row in rows:
        games = cache_row.get('data') or []
        if not isinstance(games, list): continue
        for game in games:
            gid = game.get('id') or game.get('game_id')
            if not gid: continue
            matchup = f"{game.get('away_team','?')} @ {game.get('home_team','?')}"
            commence = game.get('commence_time')
            bookmakers = game.get('bookmakers') or []
            for bm in bookmakers:
                book = bm.get('key') or bm.get('title')
                if not book: continue
                for mkt in (bm.get('markets') or []):
                    mk = mkt.get('key','')
                    if mk == 'h2h': market = 'ml'
                    elif mk == 'spreads': market = 'spread'
                    elif mk == 'totals': market = 'total'
                    else: continue
                    for outcome in (mkt.get('outcomes') or []):
                        name = (outcome.get('name') or '').lower()
                        if market == 'total':
                            side = 'over' if 'over' in name else 'under' if 'under' in name else None
                        else:
                            home = (game.get('home_team') or '').lower()
                            away = (game.get('away_team') or '').lower()
                            if name == home: side = 'home'
                            elif name == away: side = 'away'
                            else: side = None
                        if not side: continue
                        price = outcome.get('price')
                        if price is None: continue
                        key = (gid, market, book, side)
                        if key in seen_keys: continue
                        seen_keys.add(key)
                        batch.append({
                            'sport': sport,
                            'game_id': gid,
                            'matchup': matchup,
                            'commence_time': commence,
                            'market': market,
                            'book': book,
                            'side': side,
                            'line': outcome.get('point'),
                            'price': int(price),
                            'captured_at': now,
                        })

    if not batch:
        print(f'  {sport}: no bookmaker rows to snapshot'); return 0

    if dry_run:
        print(f'  {sport}: [DRY] would write {len(batch)} snapshots (sample):')
        for row in batch[:4]:
            print(f'    {row["market"]:6} {row["book"]:15} {row["side"]:5} L{row["line"]} p{row["price"]}')
        return len(batch)

    # Chunk insert — 200 per POST keeps under REST payload limits
    for i in range(0, len(batch), 200):
        chunk = batch[i:i+200]
        pr = requests.post(f'{SB}/rest/v1/line_history',
            headers=H_WRITE, json=chunk, timeout=20)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  chunk {i}: {pr.status_code} {pr.text[:200]}')
    print(f'  {sport}: wrote {written} snapshots')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='ALL',
        help='MLB / NFL / NCAAF / NCAAB / NBA / NHL / UFC / ALL')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sports = list(SPORT_TO_CACHE_KEY_PREFIX.keys()) if args.sport == 'ALL' else [args.sport]
    total = 0
    for s in sports:
        print(f'=== line_history · {s} ===')
        total += snapshot_from_odds_cache(s, dry_run=args.dry_run)
    print(f'\nTOTAL {"would-write" if args.dry_run else "written"}: {total}')


if __name__ == '__main__':
    main()
