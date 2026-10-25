#!/usr/bin/env python3
"""Move a published pick onto the BEST number books actually post.

══ WHY THIS IS NOT refresh_offmarket_lines.py ══════════════════════════
That script targets `close_spread`, which is a stored consensus number and
can lag the live board. Run on the 10-25 NFL slate it moved FOUR picks to
WORSE numbers than were available (ARI +5 -> +4.5, TEN +2.5 -> +1.5,
LV +3.5 -> +3, IND +7 -> +6.5) and left GB and CAR untouched, because their
close_spread already matched our line while books posted a point better.

This targets the posted board instead: `line_history`, one real quote per
(book, side, line), newest per book, and takes the most favourable number our
side can actually get with 2+ books behind it. Same source and the same 2-book
floor the flip uses, so there is one definition of "a number we can bet" in
the codebase rather than two that drift.

It only ever moves a pick in the USER'S FAVOUR -- a better number for the side
already published. It never changes the side, never crosses a key number in
the wrong direction, and refuses when the board is worse than what we show
(that is the market moving away, not a stale pick, and chasing it would be
re-publishing a different bet).

Line and label move together. Updating the line alone would leave the label
reading "GB +3.5" over a line of 4.0, and a receipt written from that carries
a label and a line that disagree -- worse than the problem.

public_receipts.pick_line is immutable, so a receipt already published keeps
its old number and will grade on it. For a pick improved here that means the
record grades a HARDER line than displayed: the record understates us, which
is the only acceptable direction.

Usage:
    python align_picks_to_board.py --sport NFL
    python align_picks_to_board.py --sport NFL --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_ENV = os.path.join(_HERE, '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        if '=' in _ln and not _ln.strip().startswith('#'):
            _k, _v = _ln.strip().split('=', 1)
            os.environ.setdefault(_k, _v)

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = dict(H, **{'Content-Type': 'application/json'})

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context'}
MIN_GAIN = 0.5          # ignore sub-half-point noise


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(CTX))
    ap.add_argument('--date', default=None)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    date = a.date or (dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(hours=4)).date().isoformat()
    from flip_football_spreads import posted_quotes, best_flip_quote
    quotes, teammap = posted_quotes(a.sport, date)
    tbl = CTX[a.sport]
    r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=30,
                     params={'select': 'game_id,home_team,away_team,'
                                       'primary_play,pick_locked_at',
                             'game_date': f'eq.{date}', 'limit': '400'})
    rows = r.json() if r.status_code == 200 else []
    print(f'=== align_picks_to_board · {a.sport} · {date} · '
          f'{"APPLY" if a.apply else "DRY RUN"} ===')
    print(f'  posted-quote keys: {len(quotes)}')
    moved = 0
    for g in rows:
        pp = g.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except ValueError:
                pp = None
        if not isinstance(pp, dict):
            continue
        if str(pp.get('type') or '').lower() not in ('rl', 'spread'):
            continue
        side = str(pp.get('side') or '').upper()
        ours = f(pp.get('line'))
        if ours is None or side not in ('HOME', 'AWAY'):
            continue
        lh = teammap.get((str(g['away_team']), str(g['home_team'])))
        if lh is None:
            continue
        qt = best_flip_quote(quotes, lh, side, ours)
        if not qt:
            print(f'  SKIP  {g["away_team"]} @ {g["home_team"]}: '
                  f'{pp.get("label")} — no 2+ book quote')
            continue
        best, price, books = qt
        gain = best - ours
        if gain < MIN_GAIN:
            continue
        if g.get('pick_locked_at'):
            print(f'  SKIP  {g["away_team"]} @ {g["home_team"]}: LOCKED — '
                  f'unlock first (board {best:+g} vs ours {ours:+g})')
            continue
        team = str(g['home_team'] if side == 'HOME' else g['away_team'])
        new = dict(pp)
        new['line'] = best
        new['label'] = f'{team} {best:+g}'
        new['_board_aligned'] = {
            'from': ours, 'to': best, 'price': price, 'books': books,
            'at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'why': 'moved to the best number 2+ books post for this side',
        }
        print(f'  {"FIX  " if a.apply else "[DRY]"} '
              f'{g["away_team"]} @ {g["home_team"]}: '
              f'{pp.get("label")} -> {new["label"]} @ {price:+g} '
              f'({books}bk, +{gain:g} better)')
        if not a.apply:
            moved += 1
            continue
        up = requests.patch(f'{SB}/rest/v1/{tbl}', headers=HW, timeout=30,
                            params={'game_id': f'eq.{g["game_id"]}'},
                            data=json.dumps({'primary_play': new}))
        if up.status_code not in (200, 204):
            print(f'      PATCH HTTP {up.status_code}: {up.text[:160]}')
            continue
        # A 200 is not a write — the pick lock reverts silently.
        back = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=30,
                            params={'select': 'primary_play',
                                    'game_id': f'eq.{g["game_id"]}',
                                    'limit': '1'})
        got = ((back.json() or [{}])[0].get('primary_play') or {}) \
            if back.status_code == 200 else {}
        if str(got.get('label')) == new['label']:
            moved += 1
        else:
            print(f'      REVERTED — still {got.get("label")!r}')
    print(f'  {"moved" if a.apply else "would move"}: {moved}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
