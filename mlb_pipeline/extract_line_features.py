#!/usr/bin/env python3
"""Turn line_history into per-game MARKET MOVEMENT features. Writes nothing to the DB.

== 2026-10-18 - WHY ==
ANDY: "dont fucking tellme there is no measured edge, you are not digging deep
enough ... we need to evalute relsitonship of rolling stats, moeny flow, all
data we have."

He is right that the earlier conclusion was shallow. I tested the six stored
PROJECTION columns against the close and stopped. I never touched
line_history, which holds 1.69M NCAAF + 721k NFL + 356k NHL per-book captures
and is the only money-flow record we actually own.

Two properties make it worth building on:
  * 11 books, home and away, captured repeatedly before kickoff;
  * sampled 400 rows and 400 of 400 were captured BEFORE commence_time, so
    movement features are pregame by construction, not by assumption. The
    extractor still enforces it per row rather than trusting the sample.

Only the HOME side is pulled. The away line is its mirror, and dropping it
halves ~574k rows to ~287k for NCAAF. Price is kept per side where seen.

Features per game, all in HOME-handicap terms:
    open_line, close_line, line_move (close - open)
    early_move  (first -> 24h before kickoff)
    late_move   (24h before -> close)      <- where sharp money shows up
    line_range  (max - min)
    close_book_sd (disagreement across books at the close)
    n_captures, n_books, hours_covered
    open_price, close_price, price_move

Cached to the scratchpad as JSON so the analysis never re-pulls.

USAGE
    python extract_line_features.py --sport NCAAF --market spread
    python extract_line_features.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
OUT_DIR = os.environ.get('SCRATCH_DIR') or os.environ.get('TMPDIR') or '.'
PAGE = 1000


def _dt(s):
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:                                          # noqa: BLE001
        return None


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def stream(sport: str, market: str, since: str = '2026-08-01'):
    """Page through line_history with KEYSET pagination, yielding rows.

    2026-10-18: offset paging died with "canceling statement due to statement
    timeout" (57014) at offset 21000. line_history holds 3.47M rows and
    Postgres has to walk every skipped row to honour an OFFSET, so the cost
    grows with depth. Seeking on the indexed id instead is flat per page, and
    the only thing it costs is that pages must be ordered by id - which they
    already were.
    """
    last_id = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/line_history', headers=H, timeout=240,
                         params={'select': 'id,game_id,side,line,price,book,'
                                           'captured_at,commence_time',
                                 'sport': f'eq.{sport}',
                                 'market': f'eq.{market}',
                                 'side': 'eq.home',
                                 'book': 'in.(draftkings,betmgm,fanduel)',
                                 'commence_time': f'gte.{since}',
                                 'id': f'gt.{last_id}',
                                 'order': 'id.asc',
                                 'limit': str(PAGE)})
        if r.status_code not in (200, 206):
            print(f'  !! HTTP {r.status_code} {r.text[:160]}')
            return
        b = r.json()
        if not b:
            return
        for x in b:
            yield x
        last_id = b[-1]['id']
        if len(b) < PAGE:
            return


def build(sport: str, market: str) -> dict:
    per = defaultdict(list)
    kept = dropped = 0
    for x in stream(sport, market):
        ca, ct = _dt(x.get('captured_at')), _dt(x.get('commence_time'))
        ln = _f(x.get('line'))
        if ca is None or ct is None or ln is None:
            dropped += 1
            continue
        # ENFORCED per row, not assumed from a sample: a capture at or after
        # kickoff is not a pregame feature and cannot be used.
        if ca >= ct:
            dropped += 1
            continue
        kept += 1
        per[str(x['game_id'])].append((ca, ln, _f(x.get('price')),
                                       str(x.get('book')), ct))
    print(f'  {sport}/{market}: kept {kept} pregame captures on '
          f'{len(per)} games (dropped {dropped})')

    out = {}
    for gid, caps in per.items():
        caps.sort(key=lambda t: t[0])
        ct = caps[0][4]
        lines = [c[1] for c in caps]
        first_t, last_t = caps[0][0], caps[-1][0]
        open_line, close_line = lines[0], lines[-1]
        cutoff = ct - timedelta(hours=24)
        pre24 = [c for c in caps if c[0] <= cutoff]
        post24 = [c for c in caps if c[0] > cutoff]
        mid = pre24[-1][1] if pre24 else open_line
        # book disagreement at the close: spread of the last capture per book
        last_by_book = {}
        for c in caps:
            last_by_book[c[3]] = c[1]
        bsd = (st.pstdev(list(last_by_book.values()))
               if len(last_by_book) > 1 else 0.0)
        prices = [c[2] for c in caps if c[2] is not None]
        out[gid] = {
            'open_line': open_line, 'close_line': close_line,
            'line_move': close_line - open_line,
            'early_move': mid - open_line,
            'late_move': close_line - mid,
            'line_range': max(lines) - min(lines),
            'close_book_sd': bsd,
            'n_captures': len(caps), 'n_books': len(last_by_book),
            'hours_covered': (last_t - first_t).total_seconds() / 3600.0,
            'hours_to_kick_last': (ct - last_t).total_seconds() / 3600.0,
            'open_price': prices[0] if prices else None,
            'close_price': prices[-1] if prices else None,
            'price_move': ((prices[-1] - prices[0])
                           if len(prices) > 1 else None),
            'n_post24': len(post24),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--market', default='spread')
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args()
    jobs = ([(s, m) for s in ('NCAAF', 'NFL', 'NHL')
             for m in ('spread', 'total')] if a.all
            else [(a.sport, a.market)])
    for sport, market in jobs:
        if not sport:
            continue
        print(f'\n=== {sport} / {market} ===')
        d = build(sport, market)
        p = os.path.join(OUT_DIR, f'linefeat_{sport}_{market}.json')
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(d, fh)
        print(f'  wrote {len(d)} games -> {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
