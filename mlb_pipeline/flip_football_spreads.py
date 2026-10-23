#!/usr/bin/env python3
"""Flip our own SHORT NCAAF spread pick at the SOURCE, before it is published.

══ WHY ══════════════════════════════════════════════════════════════════
Our short football spread pick is the worst thing we publish. Measured
2026-10-23 on the surfaces' own receipts:

    Sharp, football spreads |line| <= 7, AS PUBLISHED   4-18-1   18.2%  -65.3% ROI
    the identical 22 picks, FLIPPED                    18-4-1   81.8%  +56.2% ROI
    Jerry (game_read) NCAAF short, FLIPPED             35-18    66.0%  +26.1% ROI  n=53
      split-half: early 61.8% (n=34) · late 61.7% (n=47)  <- stable, not a streak
    Jerry NCAAF short, CI[52.6, 77.3]                  clears the 52.38% breakeven

Scope is deliberately narrow, and each exclusion is a measurement:

    NCAAF long (>7)  flipped 49.8% on n=227  -> a coin flip on most of the
                     volume. Excluded. Flipping it would dilute the signal.
    NFL short        flipped 53.6% on n=28, CI[35.8,70.5] -> spans breakeven
                     both ways. Excluded until it does not.
    totals / ML      not measured here at all. Untouched.

══ WHY AT THE SOURCE ════════════════════════════════════════════════════
Flipping inside the Sharp/Sweat Card composers instead would leave Jerry's
read saying "Texas -6.5" while the card said "Ole Miss +3.5" on the same
screen. Andy called that exact class of contradiction a smoking gun on
2026-10-23 (the Giants CLV card). Flipping primary_play once means the read,
the Sharp, the Sweat Card and the receipt all agree, and the grader records
the side we actually published -- feedback_grade_the_side_we_published.

══ THE TWO SAFETY GATES ═════════════════════════════════════════════════
1. A GAME THAT ALREADY HAS A RECEIPT IS NEVER TOUCHED.
   public_receipts.pick_line is immutable by DB trigger, by design, because
   it is what makes the record trustworthy. If a receipt already names the
   original side, flipping primary_play would show one number and grade
   another. So this script refuses those games outright and names them.
   On the 2026-10-24 slate that protected 4 of 11 (LSU, Wisconsin, Texas,
   Utah) -- and Texas's receipt already said -8.5 against a primary_play of
   -6.5, so that row was divergent before anything here ran.

2. A FLIP NEEDS A POSTED PRICE.
   The flipped side is priced from line_history, which stores one row per
   (book, side, line) with a real American price. 2+ books must post the
   exact flipped number or the pick is left alone. Quoting our own stale line
   on the other side is what project_jerry_published_fake_lines_1019 found on
   48% of game_read spreads, and publishing a number nobody can bet is the
   defect being fixed this same day.

POTD is excluded separately, in jerry_anchor_potd._load_primary_play_candidates,
on Andy's instruction that POTD is not to change. A flipped pick carries
`_flipped_from`, and that is what POTD skips on.

Usage:
    python flip_football_spreads.py                      # dry run
    python flip_football_spreads.py --apply
    python flip_football_spreads.py --date 2026-10-24 --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import requests

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        if '=' in _ln and not _ln.strip().startswith('#'):
            _k, _v = _ln.strip().split('=', 1)
            os.environ.setdefault(_k, _v)

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = dict(H, **{'Content-Type': 'application/json',
                'Prefer': 'return=representation'})

#: Only the band that was measured. See the docstring.
MAX_MAGNITUDE = 7.0
#: Books that must post the exact flipped number.
MIN_BOOKS = 2
#: Sports this is validated on. NFL short is 53.6% on n=28 — not yet.
SPORTS = {'NCAAF': 'ncaaf_game_context'}
OPP = {'HOME': 'AWAY', 'AWAY': 'HOME'}


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)) \
        .date().isoformat()


def _get(path, params):
    r = requests.get(f'{SB}/rest/v1/{path}', params=params, headers=H,
                     timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ GET {path} HTTP {r.status_code}: {r.text[:200]}')
        return None
    return r.json()


def amer_prob(o):
    o = float(o)
    return (-o / (-o + 100.0)) if o < 0 else (100.0 / (o + 100.0))


def posted_quotes(sport: str, date: str) -> dict:
    """(game_id, side, line) -> (median posted price, n books).

    Keyed on line_history's own game_id, which for NCAAF is built from team
    names; the caller maps to it by team codes rather than assuming the two
    tables agree (project_ncaaf_five_team_name_mismatch_1021).
    """
    rows = []
    off = 0
    while off < 12000:
        b = _get('line_history',
                 {'select': 'game_id,side,line,price,book,captured_at',
                  'sport': f'eq.{sport}', 'market': 'eq.spread',
                  'commence_time': f'gte.{date}',
                  'order': 'captured_at.desc',
                  'limit': '1000', 'offset': str(off)})
        if not b:
            break
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    seen, pool, teams = set(), {}, {}
    for r in rows:
        gid = str(r.get('game_id') or '')
        ln, pr = f(r.get('line')), f(r.get('price'))
        if ln is None or pr is None:
            continue
        k = (gid, str(r.get('book')), str(r.get('side')).lower(), ln)
        if k in seen:           # ordered desc, first seen is newest
            continue
        seen.add(k)
        pool.setdefault((gid, str(r.get('side')).lower(), ln), []).append(pr)
        parts = gid.split('_')
        if len(parts) >= 3:
            teams[(parts[-2], parts[-1])] = gid
    out = {}
    for k, prices in pool.items():
        if len(prices) < MIN_BOOKS:
            continue
        ranked = sorted(prices, key=amer_prob)
        # a REAL quote from one book, never an average of two
        out[k] = (ranked[len(ranked) // 2], len(prices))
    return out, teams


#: How many points worse than the flip number we will still accept.
#: The thesis was measured at OUR number, so a board materially short of it is
#: a different bet; a board AHEAD of it is the same bet on better terms.
FLIP_LINE_TOLERANCE = 0.5


def best_flip_quote(quotes: dict, lh_gid, side: str, flip_line: float):
    """Best POSTED quote for the flipped side, or None.

    ══ 2026-10-23 · AN EXACT-LINE MATCH REJECTED A BETTER NUMBER ══
    The first version required the board to post our flipped number exactly.
    That threw away Georgia Tech -5.5 -> Boston College, where the flip number
    was +5.5 and the board posts +6.5 -- a full point MORE in our favour, on
    the same side, refused for not matching. Andy: "If we knwo thes epicks are
    btter i want to flip them to users, youre saying only flip one?" He was
    right; refusing a better price is indefensible.

    So: take the most favourable posted line for the side we want, and accept
    it when it is no more than FLIP_LINE_TOLERANCE worse than the number the
    thesis was measured at. Higher is always better for the holder, so the
    comparison is a plain signed one and needs no per-sport convention.

    Still refuses a board that has moved materially AGAINST the flip -- Texas
    -6.5 flips to Ole Miss +6.5 while the board posts only +3.5, three points
    short, which is a different bet from the one that was measured.
    """
    if not lh_gid:
        return None
    opts = [(ln, v) for (gid, sd, ln), v in quotes.items()
            if gid == lh_gid and sd == side.lower()]
    if not opts:
        return None
    ln, (price, books) = max(opts, key=lambda z: z[0])   # most points for us
    if ln < flip_line - FLIP_LINE_TOLERANCE:
        return None
    return ln, price, books


def receipted(sport: str, date: str) -> set:
    """game_ids that already have a published spread receipt. Never touched."""
    rows = _get('public_receipts',
                {'select': 'game_id,market', 'sport': f'eq.{sport}',
                 'game_date': f'eq.{date}', 'limit': '500'}) or []
    return {str(r['game_id']) for r in rows
            if str(r.get('market') or '').lower() in ('rl', 'spread')}


def run(sport: str, date: str, apply: bool) -> int:
    tbl = SPORTS[sport]
    games = _get(tbl, {'select': 'game_id,game_date,home_team,away_team,'
                                 'close_spread,primary_play,pick_locked_at',
                       'game_date': f'eq.{date}', 'limit': '300'}) or []
    quotes, teammap = posted_quotes(sport, date)
    has_receipt = receipted(sport, date)
    print(f'=== flip_football_spreads · {sport} · {date} · '
          f'{"APPLY" if apply else "DRY RUN"} ===')
    print(f'  games={len(games)}  posted-quote keys={len(quotes)}  '
          f'games already receipted={len(has_receipt)}')
    flipped = 0
    skip = {}

    def no(reason):
        skip[reason] = skip.get(reason, 0) + 1

    for g in games:
        pp = g.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except ValueError:
                pp = None
        if not isinstance(pp, dict):
            continue
        if str(pp.get('type') or '').lower() not in ('rl', 'spread'):
            no('not a spread pick')
            continue
        ours = f(pp.get('line'))
        side = str(pp.get('side') or '').upper()
        if ours is None or side not in OPP:
            no('no usable line/side')
            continue
        if abs(ours) > MAX_MAGNITUDE:
            no(f'long spread (>{MAX_MAGNITUDE:g}) — 49.8% on n=227, no edge')
            continue
        # ══ FLIP A CALL, NEVER MANUFACTURE ONE ══
        # A PASS tier is the engine saying "no play" and COVERAGE is
        # information rather than a conviction call. Inverting those would not
        # be fading our own read, it would be inventing a bet where we told
        # the user there wasn't one -- a different and much worse thing than
        # the 66% measurement supports. On the 10-24 slate this held back
        # Arkansas State +2.5, Kansas +2.5 and Navy +2.5, all PASS.
        tier = str(pp.get('tier') or '').upper()
        if tier not in ('PRIME', 'STRONG', 'LEAN'):
            no(f'tier {tier or "none"} is not a call we made — nothing to flip')
            continue
        gid = str(g['game_id'])
        if gid in has_receipt:
            no('RECEIPT ALREADY PUBLISHED — would show one number, grade '
               'another')
            continue
        if pp.get('_flipped_from'):
            no('already flipped')
            continue
        if g.get('pick_locked_at'):
            no('pick is LOCKED — run lock_football_slate.py --unlock first')
            continue
        opp = OPP[side]
        new_line = -ours
        team = str(g['home_team'] if opp == 'HOME' else g['away_team'])
        lh = teammap.get((str(g['away_team']), str(g['home_team'])))
        q = best_flip_quote(quotes, lh, opp, new_line)
        if not q:
            no(f'no {MIN_BOOKS}+ books post a line within '
               f'{FLIP_LINE_TOLERANCE:g} of the flipped number')
            continue
        new_line, price, nbooks = q
        new = dict(pp)
        new['side'] = opp
        new['line'] = new_line
        new['label'] = f'{team} {new_line:+g}'
        new['_flipped_from'] = {
            'side': side, 'line': ours, 'label': pp.get('label'),
            'tier': pp.get('tier'), 'sub': pp.get('sub'),
            'at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'basis': ('short NCAAF spreads flipped: 66.0%, ROI +26.1%, n=53, '
                      'CI[52.6,77.3], split-half 61.8%/61.7%'),
        }
        new['_flip_price'] = price
        new['_flip_books'] = nbooks
        new['sub'] = (f'fades our own {pp.get("label")} read — our short '
                      f'NCAAF spread calls hit 34% straight and 66% inverted '
                      f'(n=53)')
        print(f'  {"FLIP " if apply else "[DRY]"} {g["away_team"]} @ '
              f'{g["home_team"]}: {pp.get("label")} -> {new["label"]} '
              f'@ {price:+g} ({nbooks}bk)')
        if apply:
            r = requests.patch(f'{SB}/rest/v1/{tbl}',
                               params={'game_id': f'eq.{gid}'},
                               headers=HW,
                               data=json.dumps({'primary_play': new}),
                               timeout=30)
            if r.status_code not in (200, 204):
                print(f'      ⚠ PATCH HTTP {r.status_code}: {r.text[:200]}')
                continue
            # A 200 is not a write. Read it back and confirm the label moved,
            # because enforce_pick_lock silently reverts a locked row.
            back = _get(tbl, {'select': 'primary_play',
                              'game_id': f'eq.{gid}', 'limit': '1'})
            got = (back or [{}])[0].get('primary_play') or {}
            if str(got.get('label')) != new['label']:
                print(f'      ⚠ REVERTED — label is still '
                      f'{got.get("label")!r}; the lock trigger refused it')
                no('refused by lock trigger')
                continue
        flipped += 1
    print(f'  flipped: {flipped}')
    if skip:
        for k, v in sorted(skip.items(), key=lambda z: -z[1]):
            print(f'    skipped · {k}: {v}')
    return flipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(SPORTS), action='append')
    ap.add_argument('--date', default=None)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    date = a.date or et_today()
    total = 0
    for sport in (a.sport or sorted(SPORTS)):
        total += run(sport, date, a.apply)
    return 0


if __name__ == '__main__':
    sys.exit(main())
