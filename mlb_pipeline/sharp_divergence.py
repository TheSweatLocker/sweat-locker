"""Sharp vs public divergence detector for line movement (2026-08-07).

Computes the direction sharp books moved vs the direction public books
moved between opening and current (or closing) lines. When the two
groups moved in OPPOSITE directions, that's the strongest signal:
sharps priced true probability differently than the public was betting.

Sport-universal — takes a *_line_history table name so it works for
MLB today, NFL/NCAAF/etc. when their per-book line histories exist.

Usage (as a library):
    from sharp_divergence import compute_divergence
    sig = compute_divergence(game_id, market='total',
                             table='mlb_line_history')
    if sig['divergence_type'] == 'opposed':
        # sharp side moved opposite public side — actionable

Usage (as a CLI for one-off inspection):
    python sharp_divergence.py --game-id abc123 --market total

Return shape:
    {
      'game_id': str,
      'market': 'total' | 'spread' | 'ml',
      'sharp_open_median': float | None,
      'sharp_current_median': float | None,
      'sharp_delta': float | None,      # + means moved up (e.g. total 8.0 → 8.5)
      'sharp_direction': 'up' | 'down' | 'flat' | None,
      'sharp_n_books': int,
      'public_open_median': float | None,
      'public_current_median': float | None,
      'public_delta': float | None,
      'public_direction': 'up' | 'down' | 'flat' | None,
      'public_n_books': int,
      'divergence_type': 'aligned' | 'opposed' | 'sharp_only' |
                        'public_only' | 'no_signal' | 'insufficient_data',
      'sharp_side': 'over' | 'under' | 'home' | 'away' | None,  # sport-agnostic label of the sharp lean
      'confidence': float,   # 0-1 based on delta magnitude + book count
    }
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ.get('SUPABASE_URL', '')
KEY = os.environ.get('SUPABASE_KEY', '')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'} if KEY else {}

from book_tiers import (
    classify,
    DIVERGENCE_MIN_SHARP_BOOKS,
    DIVERGENCE_MIN_PUBLIC_BOOKS,
    DIVERGENCE_MIN_LINE_DELTA,
    DIVERGENCE_MIN_ML_DELTA_CENTS,
)


# book_lines schema uses uniform column names across markets:
# 'line' (null for ml), 'home_odds', 'away_odds', 'market' filters rows.
# Sport-universal — same table serves MLB/NFL/NCAAF/etc.
MARKET_COLUMNS = {
    'total':  {'line': 'line', 'home_odds': 'home_odds', 'away_odds': 'away_odds'},
    'spread': {'line': 'line', 'home_odds': 'home_odds', 'away_odds': 'away_odds'},
    'ml':     {'line': None,   'home_odds': 'home_odds', 'away_odds': 'away_odds'},
}


def fetch_line_history(game_id: str, sport: str = 'MLB',
                       market: str = 'total') -> list:
    """Pull all per-book rows for a game_id + market from book_lines,
    ordered by fetch time ascending. Sport-universal."""
    if not SB or not KEY: return []
    r = requests.get(
        f'{SB}/rest/v1/book_lines',
        headers=H,
        params={
            'sport': f'eq.{sport}',
            'game_id': f'eq.{game_id}',
            'market': f'eq.{market}',
            'order': 'fetched_at.asc',
            'select': 'book_key,book_title,book_tier,line,home_odds,away_odds,fetched_at',
        },
        timeout=15,
    )
    if r.status_code != 200: return []
    return r.json() or []


def _direction(delta: Optional[float], min_move: float) -> Optional[str]:
    if delta is None: return None
    if abs(delta) < min_move: return 'flat'
    return 'up' if delta > 0 else 'down'


def _median_by_book(rows: list, col: str) -> Optional[float]:
    """Median of a column across a group of book rows (dedup on book —
    if a book appears multiple times take its latest)."""
    latest = {}
    for r in rows:
        book = r.get('source_book')
        if not book: continue
        v = r.get(col)
        if v is None: continue
        latest[book] = v
    vals = [float(v) for v in latest.values()]
    if not vals: return None
    return statistics.median(vals)


def _open_and_current(rows: list, tier: str, col: str) -> tuple:
    """For a set of book_lines rows filtered to one tier, return
    (open_median, current_median, n_books) where 'open' = earliest
    stored value per book and 'current' = most recent per book.

    Because book_lines uses change-only writes, a book's first stored
    row IS its opening line (nothing written before the first change).
    """
    tier_rows = [r for r in rows if r.get('book_tier') == tier]
    if not tier_rows: return None, None, 0
    # Per-book earliest + latest
    earliest_per_book = {}
    latest_per_book = {}
    for r in tier_rows:
        book = r.get('book_key')
        v = r.get(col)
        if not book or v is None: continue
        ts = r.get('fetched_at')
        if book not in earliest_per_book or ts < earliest_per_book[book][0]:
            earliest_per_book[book] = (ts, v)
        if book not in latest_per_book or ts > latest_per_book[book][0]:
            latest_per_book[book] = (ts, v)
    valid_books = set(earliest_per_book) & set(latest_per_book)
    if not valid_books: return None, None, 0
    open_vals = [float(earliest_per_book[b][1]) for b in valid_books]
    curr_vals = [float(latest_per_book[b][1]) for b in valid_books]
    return statistics.median(open_vals), statistics.median(curr_vals), len(valid_books)


def compute_divergence(game_id: str, market: str = 'total',
                       sport: str = 'MLB') -> dict:
    """Core detector. See module docstring for return shape.

    Sport-universal: pass sport='NFL' / 'NCAAF' / etc. once those
    pipelines are writing to book_lines.
    """
    market = market.lower()
    if market not in MARKET_COLUMNS:
        return {'error': f'unknown market {market}'}
    rows = fetch_line_history(game_id, sport, market)
    result = {
        'game_id': game_id, 'market': market,
        'sharp_open_median': None, 'sharp_current_median': None,
        'sharp_delta': None, 'sharp_direction': None, 'sharp_n_books': 0,
        'public_open_median': None, 'public_current_median': None,
        'public_delta': None, 'public_direction': None, 'public_n_books': 0,
        'divergence_type': 'insufficient_data',
        'sharp_side': None, 'confidence': 0.0,
    }
    if not rows: return result

    if market == 'ml':
        # For ML, divergence is on the home_odds directly (ML has no
        # separate 'line'). A more negative home_odds = home team more
        # heavily favored = sharp money moved on home.
        col = 'home_odds'
        min_move = DIVERGENCE_MIN_ML_DELTA_CENTS
    else:
        col = MARKET_COLUMNS[market]['line']  # always 'line' now
        min_move = DIVERGENCE_MIN_LINE_DELTA

    sharp_open, sharp_curr, sharp_n = _open_and_current(rows, 'sharp', col)
    public_open, public_curr, public_n = _open_and_current(rows, 'public', col)

    result['sharp_open_median'] = sharp_open
    result['sharp_current_median'] = sharp_curr
    result['sharp_n_books'] = sharp_n
    result['public_open_median'] = public_open
    result['public_current_median'] = public_curr
    result['public_n_books'] = public_n

    if sharp_open is not None and sharp_curr is not None:
        result['sharp_delta'] = round(sharp_curr - sharp_open, 3)
        result['sharp_direction'] = _direction(result['sharp_delta'], min_move)
    if public_open is not None and public_curr is not None:
        result['public_delta'] = round(public_curr - public_open, 3)
        result['public_direction'] = _direction(result['public_delta'], min_move)

    # Classify divergence
    if sharp_n < DIVERGENCE_MIN_SHARP_BOOKS and public_n < DIVERGENCE_MIN_PUBLIC_BOOKS:
        result['divergence_type'] = 'insufficient_data'
    elif sharp_n < DIVERGENCE_MIN_SHARP_BOOKS:
        result['divergence_type'] = 'public_only'
    elif public_n < DIVERGENCE_MIN_PUBLIC_BOOKS:
        result['divergence_type'] = 'sharp_only'
    else:
        sd = result['sharp_direction']; pd = result['public_direction']
        if sd == 'flat' and pd == 'flat':
            result['divergence_type'] = 'no_signal'
        elif sd == 'flat' or pd == 'flat':
            # One side moved, other didn't — not opposed, not aligned
            result['divergence_type'] = 'partial'
        elif sd == pd:
            result['divergence_type'] = 'aligned'
        else:
            result['divergence_type'] = 'opposed'

    # Sharp-side label — which side of the market the sharps back
    sd = result['sharp_direction']
    if market == 'total' and sd in ('up', 'down'):
        # total moved UP means sharps took OVER (they bet enough over that
        # the line had to rise to attract under money)
        result['sharp_side'] = 'over' if sd == 'up' else 'under'
    elif market == 'spread' and sd in ('up', 'down'):
        # spread moved UP (e.g. -3 → -3.5 for home) means home got tougher
        # for the bettor = sharps bet home; DOWN = sharps bet away
        result['sharp_side'] = 'home' if sd == 'up' else 'away'
    elif market == 'ml' and sd in ('up', 'down'):
        # home_ml moving MORE NEGATIVE (up in absolute juice) = home
        # became stronger fav = sharps bet home
        # But careful — 'up' on -110 → -100 is public backing away from home
        # And 'up' on -110 → -120 is home getting more juiced
        # We use delta = curr - open. -110 → -120 = delta -10 (down)
        # -110 → -100 = delta +10 (up). So DOWN = home more expensive = sharps on home
        result['sharp_side'] = 'home' if sd == 'down' else 'away'

    # Confidence: 0..1 combining book count and delta magnitude
    if result['divergence_type'] in ('opposed', 'aligned') and sharp_n >= DIVERGENCE_MIN_SHARP_BOOKS:
        book_factor = min(1.0, sharp_n / 4.0)
        delta_factor = min(1.0, abs(result['sharp_delta'] or 0) / (min_move * 4))
        result['confidence'] = round(book_factor * delta_factor, 3)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--game-id', required=True)
    ap.add_argument('--market', default='total',
                    choices=list(MARKET_COLUMNS.keys()))
    ap.add_argument('--sport', default='MLB')
    args = ap.parse_args()
    result = compute_divergence(args.game_id, args.market, args.sport)
    print(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    main()
