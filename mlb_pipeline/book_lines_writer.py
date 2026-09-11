"""Sport-universal per-book line writer (2026-08-07).

Extracts every bookmaker's line from a raw Odds API response and writes
one row per (sport, game, book, market) to public.book_lines when the
book's line has CHANGED vs its most recent stored value.

Change-only writes keep volume manageable — a book that holds steady
across 10 polls only produces 1 row.

Reused by every sport's line poller. MLB's line_poller.py calls it
alongside its existing median-aggregate flow (no disruption to
mlb_line_history consumers).

Interface:
    from book_lines_writer import write_book_lines
    write_book_lines(odds_event, sport='MLB', game_id=..., game_date=...)

Silently no-ops if the book_lines table doesn't exist yet (fresh
install where migration hasn't been applied). Prints a one-time
warning per session so it's noticed.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from book_tiers import classify

SB = os.environ.get('SUPABASE_URL', '')
KEY = os.environ.get('SUPABASE_KEY', '')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'} if KEY else {}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'} if KEY else {}

_TABLE_MISSING_WARNED = False


def _extract_market_from_bookmaker(bm: dict, home_team: str, away_team: str) -> dict:
    """From one bookmaker's markets, extract (market → {line, home_odds,
    away_odds}) for the three markets we track. Returns {} if the book
    posts no relevant markets."""
    out = {}
    for mkt in bm.get('markets', []) or []:
        key = mkt.get('key')
        if key == 'totals':
            over_o = under_o = line = None
            for o in mkt.get('outcomes', []) or []:
                if o.get('name') == 'Over':
                    over_o = o.get('price'); line = o.get('point')
                elif o.get('name') == 'Under':
                    under_o = o.get('price')
            if line is not None and over_o and under_o:
                out['total'] = {
                    'line': float(line),
                    'home_odds': int(over_o),
                    'away_odds': int(under_o),
                }
        elif key == 'spreads':
            home_pt = home_o = away_o = None
            for o in mkt.get('outcomes', []) or []:
                if o.get('name') == home_team:
                    home_pt = o.get('point'); home_o = o.get('price')
                elif o.get('name') == away_team:
                    away_o = o.get('price')
            if home_pt is not None and home_o and away_o:
                out['spread'] = {
                    'line': float(home_pt),
                    'home_odds': int(home_o),
                    'away_odds': int(away_o),
                }
        elif key == 'h2h':
            home_ml = away_ml = None
            for o in mkt.get('outcomes', []) or []:
                if o.get('name') == home_team:
                    home_ml = o.get('price')
                elif o.get('name') == away_team:
                    away_ml = o.get('price')
            if home_ml and away_ml:
                out['ml'] = {
                    'line': None,
                    'home_odds': int(home_ml),
                    'away_odds': int(away_ml),
                }
    return out


def _fetch_last_row(sport: str, game_id: str, book_key: str, market: str) -> Optional[dict]:
    """Return the most recent row for this (sport, game, book, market)
    tuple, or None. Used to detect whether the book's price changed."""
    if not SB or not KEY: return None
    r = requests.get(
        f'{SB}/rest/v1/book_lines',
        headers=H_READ,
        params={
            'sport': f'eq.{sport}',
            'game_id': f'eq.{game_id}',
            'book_key': f'eq.{book_key}',
            'market': f'eq.{market}',
            'order': 'fetched_at.desc',
            'limit': 1,
            'select': 'line,home_odds,away_odds',
        },
        timeout=8,
    )
    if r.status_code != 200: return None
    rows = r.json() or []
    return rows[0] if rows else None


def _price_changed(prev: Optional[dict], curr: dict) -> bool:
    """True if any of line/home_odds/away_odds changed. First-write case
    (prev is None) is also a change."""
    if prev is None: return True
    # Normalize None-vs-value comparison
    def _same(a, b):
        if a is None and b is None: return True
        if a is None or b is None: return False
        return abs(float(a) - float(b)) < 0.0001
    return not (_same(prev.get('line'), curr.get('line'))
                and _same(prev.get('home_odds'), curr.get('home_odds'))
                and _same(prev.get('away_odds'), curr.get('away_odds')))


def write_book_lines(odds_event: dict, sport: str, game_id: str,
                     game_date: str) -> dict:
    """Iterate bookmakers in one event, write per-book rows when prices
    change vs most recent stored. Returns summary dict.

    odds_event: single event from Odds API response (must have
                bookmakers[], home_team, away_team fields).
    sport:      sport tag ('MLB', 'NFL', ...).
    game_id:    canonical game id used across the pipeline.
    game_date:  YYYY-MM-DD.
    """
    global _TABLE_MISSING_WARNED
    home_team = odds_event.get('home_team') or ''
    away_team = odds_event.get('away_team') or ''
    stats = {'books_scanned': 0, 'rows_written': 0, 'unchanged_skipped': 0,
             'unknown_books': set(), 'errors': 0}
    payload = []
    for bm in odds_event.get('bookmakers', []) or []:
        book_key = bm.get('key') or ''
        book_title = bm.get('title') or book_key
        if not book_key: continue
        tier = classify(book_title)
        if tier == 'mid' and book_title not in ('DraftKings','FanDuel','BetMGM',
                'Caesars','BetRivers','Hard Rock Bet','Hard Rock Bet (OH)',
                'ESPN BET','Fanatics','betPARX'):
            stats['unknown_books'].add(book_title)
        markets = _extract_market_from_bookmaker(bm, home_team, away_team)
        stats['books_scanned'] += 1
        for market, prices in markets.items():
            prev = _fetch_last_row(sport, game_id, book_key, market)
            if not _price_changed(prev, prices):
                stats['unchanged_skipped'] += 1
                continue
            payload.append({
                'sport': sport,
                'game_id': game_id,
                'game_date': game_date,
                'book_key': book_key,
                'book_title': book_title,
                'book_tier': tier,
                'market': market,
                'line': prices['line'],
                'home_odds': prices['home_odds'],
                'away_odds': prices['away_odds'],
            })

    if payload:
        r = requests.post(
            f'{SB}/rest/v1/book_lines',
            headers=H_WRITE,
            data=json.dumps(payload),
            timeout=15,
        )
        if r.status_code in (200, 201, 204):
            stats['rows_written'] = len(payload)
        elif r.status_code == 404:
            if not _TABLE_MISSING_WARNED:
                print('  ⚠ book_lines table missing — apply migration '
                      'supabase/migrations/20260807_book_lines.sql then '
                      're-run. Skipping per-book writes until then.')
                _TABLE_MISSING_WARNED = True
            stats['errors'] += 1
        else:
            print(f'  ⚠ book_lines write failed {r.status_code}: {r.text[:200]}')
            stats['errors'] += 1

    return stats


def write_line_history_from_event(odds_event: dict, sport: str, game_id: str,
                                  game_date: str) -> int:
    """Emit line_history rows from one Odds API event — one row per
    (game, market, book, side). Called from the cron alongside
    write_book_lines so line_history stays fresh SERVER-SIDE and
    Steam Room's Split view stops depending on a user opening the app.

    2026-09-11: previously line_history was only populated by
    write_line_history.py which reads the client-populated odds_cache
    table. If nobody opened the MLB Games tab, odds_cache went stale,
    line_history stopped receiving fresh rows, and Split showed a
    flat "no line movement" sparkline for hours. This writer bypasses
    odds_cache entirely — it consumes the same slate response that
    the cron already pulled for book_lines / mlb_line_history.

    Unlike book_lines (change-only), line_history is ALWAYS-write
    so the app can render a real sparkline. Returns the row count
    inserted (0 on error / no bookmakers).
    """
    if not SB or not KEY: return 0
    home_team = odds_event.get('home_team') or ''
    away_team = odds_event.get('away_team') or ''
    matchup = f'{away_team} @ {home_team}'
    commence = odds_event.get('commence_time')
    now_iso = datetime.now(timezone.utc).isoformat()

    payload = []
    for bm in odds_event.get('bookmakers', []) or []:
        book_key = bm.get('key') or ''
        if not book_key: continue
        for mkt in bm.get('markets', []) or []:
            key = mkt.get('key')
            if key == 'totals':
                over_o = under_o = line_pt = None
                for o in mkt.get('outcomes', []) or []:
                    if o.get('name') == 'Over':
                        over_o = o.get('price'); line_pt = o.get('point')
                    elif o.get('name') == 'Under':
                        under_o = o.get('price')
                if line_pt is not None and over_o is not None:
                    payload.append({
                        'sport': sport, 'game_id': game_id, 'matchup': matchup,
                        'commence_time': commence, 'market': 'total', 'book': book_key,
                        'side': 'over', 'line': float(line_pt), 'price': int(over_o),
                        'captured_at': now_iso,
                    })
                if line_pt is not None and under_o is not None:
                    payload.append({
                        'sport': sport, 'game_id': game_id, 'matchup': matchup,
                        'commence_time': commence, 'market': 'total', 'book': book_key,
                        'side': 'under', 'line': float(line_pt), 'price': int(under_o),
                        'captured_at': now_iso,
                    })
            elif key == 'spreads':
                home_pt = home_o = away_o = None
                for o in mkt.get('outcomes', []) or []:
                    if o.get('name') == home_team:
                        home_pt = o.get('point'); home_o = o.get('price')
                    elif o.get('name') == away_team:
                        away_o = o.get('price')
                if home_pt is not None and home_o is not None:
                    payload.append({
                        'sport': sport, 'game_id': game_id, 'matchup': matchup,
                        'commence_time': commence, 'market': 'spread', 'book': book_key,
                        'side': 'home', 'line': float(home_pt), 'price': int(home_o),
                        'captured_at': now_iso,
                    })
                if home_pt is not None and away_o is not None:
                    payload.append({
                        'sport': sport, 'game_id': game_id, 'matchup': matchup,
                        'commence_time': commence, 'market': 'spread', 'book': book_key,
                        'side': 'away', 'line': -float(home_pt), 'price': int(away_o),
                        'captured_at': now_iso,
                    })
            elif key == 'h2h':
                home_ml = away_ml = None
                for o in mkt.get('outcomes', []) or []:
                    if o.get('name') == home_team:
                        home_ml = o.get('price')
                    elif o.get('name') == away_team:
                        away_ml = o.get('price')
                if home_ml is not None:
                    payload.append({
                        'sport': sport, 'game_id': game_id, 'matchup': matchup,
                        'commence_time': commence, 'market': 'ml', 'book': book_key,
                        'side': 'home', 'line': None, 'price': int(home_ml),
                        'captured_at': now_iso,
                    })
                if away_ml is not None:
                    payload.append({
                        'sport': sport, 'game_id': game_id, 'matchup': matchup,
                        'commence_time': commence, 'market': 'ml', 'book': book_key,
                        'side': 'away', 'line': None, 'price': int(away_ml),
                        'captured_at': now_iso,
                    })

    if not payload: return 0
    try:
        r = requests.post(f'{SB}/rest/v1/line_history',
                          headers=H_WRITE, json=payload, timeout=20)
        if r.status_code in (200, 201, 204):
            return len(payload)
        else:
            print(f'  ⚠ line_history write failed {r.status_code}: {r.text[:150]}')
            return 0
    except Exception as e:
        print(f'  ⚠ line_history exception: {e}')
        return 0
