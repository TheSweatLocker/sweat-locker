"""Correct a published pick line that NO book is posting any more.

== 2026-10-31 . "WESTERN MICHIGAN -12.5" IS A NUMBER NOBODY CAN BET =======
Comparing each spread pick's own `primary_play.line` against a live
multi-book `line_history` capture (13:26Z, 22 minutes old, 10-11 books per
game) split today's four stale lines into two kinds:

  NO BOOK POSTS OUR NUMBER -- these are corrected
    Western Michigan -12.5   10 of 11 books -14.0, one -13.5. Our line is
                             1.5 pts BETTER than anything available, so
                             grading it as published hands our record a
                             gift it never earned.
    Ole Miss -13.5           books at -11.5 (6), -11.0 (3), -10.5 (1). We
                             are laying 2 more points than necessary, which
                             harms the user rather than the record.

  OUR NUMBER IS STILL POSTED, just not the best -- these are LEFT ALONE
    Colorado State +6.5      one book still +6.5, modal +7.5
    Iowa State +8.5          one book still +8.5, modal +9.5
                             A user can still get our number, so
                             first-publisher-wins stands and changing it
                             would be moving a live commitment for a point.

WHY CORRECTING IS NOT MOVING THE CALL. The side never changes, the market
never changes -- only the number, and only when the published number is not
obtainable anywhere. The same argument the pick lock already accepts for a
missing total ("filling in the number is not changing the pick") applies to
a number that cannot be acted on: it commits us to nothing a user could do.
Leaving it is the option that costs something, because a line no book posts
grades into our public record as if it were real.

THE REPLACEMENT IS A POSTED LINE, never a computed one. It is the MODAL line
across books in the newest capture, with the median price at that line --
not a mean and not a median across mixed numbers, because a median over an
even +/-1.5 split produces a 0.0 line and a fake line makes a fake price
(feedback_consensus_line_must_be_posted). A game whose modal line is backed
by fewer than MIN_BOOKS is skipped rather than guessed.

WHAT THIS DOES NOT FIX. `public_receipts.pick_line` is frozen by
freeze_receipt_identity (R96), so the receipt still grades at the old
number. This corrects what the app displays; the record half needs the R96
decision. That divergence is reported per row rather than hidden.

USAGE
  python refresh_unposted_pick_lines.py --date 2026-10-31 --sport NCAAF
  python refresh_unposted_pick_lines.py --date 2026-10-31 --sport NCAAF --apply
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics as st
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

TABLES = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context',
          'NBA': 'nba_game_context', 'NHL': 'nhl_game_context'}
#: A replacement must be posted by at least this many books.
MIN_BOOKS = 3
#: Ignore gaps under this; book-to-book half-point variation is normal.
MIN_GAP = 0.5


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v if isinstance(v, (dict, list)) else None


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(n: float) -> str:
    return f'{n:+g}'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--sport', default='NCAAF')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    tbl = TABLES.get(a.sport.upper())
    if not tbl:
        print(f'! unknown sport {a.sport}')
        return 2

    ctx = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_R, timeout=90,
                       params={'select': 'game_id,home_team,away_team,'
                                         'close_spread,primary_play',
                               'game_date': f'eq.{a.date}', 'limit': 300})
    if ctx.status_code != 200:
        print(f'! {tbl} HTTP {ctx.status_code} {ctx.text[:160]} '
              f'-- CANNOT VERIFY')
        return 2
    rows = ctx.json()
    gids = [str(x['game_id']) for x in rows if x.get('game_id')]
    if not gids:
        print('no games')
        return 0
    enc = ','.join(f'"{g}"' for g in gids)
    # TWO PASSES, on purpose. Paging every game's full spread history is
    # ~140k rows over 47 games and does not finish; but a single capped
    # request keeps only the newest captures, which makes the
    # "was it available when we published" test below find no history for
    # our own number, so every honest line looks unposted. That is how this
    # script first proposed overwriting Georgia -12.5, live until 82 minutes
    # ago (feedback_never_measure_from_truncated_output).
    #
    # So: pass 1 reads only the newest capture per game to find candidates,
    # pass 2 pages the FULL history of those few games only.
    newest_by_game = {}
    lh = requests.get(f'{SB}/rest/v1/line_history', headers=H_R, timeout=120,
                      params={'game_id': f'in.({enc})',
                              'sport': f'eq.{a.sport.upper()}',
                              'market': 'eq.spread',
                              'select': 'game_id,book,side,line,price,'
                                        'captured_at',
                              'order': 'captured_at.desc', 'limit': 1000})
    if lh.status_code != 200:
        print(f'! line_history HTTP {lh.status_code} {lh.text[:160]} '
              f'-- CANNOT VERIFY, nothing changed')
        return 2
    seen = collections.defaultdict(list)
    for z in lh.json():
        seen[str(z['game_id'])].append(z)
    byg = {}
    for g, v in seen.items():
        newest = max(str(z['captured_at']) for z in v)
        byg[g] = [z for z in v if str(z['captured_at']) == newest]
        newest_by_game[g] = newest
    print(f'  pass 1: newest capture for {len(byg)} game(s)')

    def full_history(gid):
        """Every spread row for ONE game, paged. Used only on candidates."""
        out, off = [], 0
        while True:
            r = requests.get(f'{SB}/rest/v1/line_history', headers=H_R,
                             timeout=120,
                             params={'game_id': f'eq.{gid}',
                                     'sport': f'eq.{a.sport.upper()}',
                                     'market': 'eq.spread',
                                     'select': 'side,line,captured_at',
                                     'order': 'captured_at.desc',
                                     'limit': 1000, 'offset': off})
            if r.status_code != 200:
                return None
            d = r.json()
            out += d
            if len(d) < 1000:
                return out
            off += 1000

    # When did each pick first reach a user-facing surface? The receipt's
    # published_at is the only record of that, and the EARLIEST one is the
    # moment our commitment was made -- which is what decides whether a
    # number that is gone now was honest when we gave it.
    earliest_publish = {}
    rc = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                      timeout=90,
                      params={'select': 'game_id,published_at',
                              'sport': f'eq.{a.sport.upper()}',
                              'game_date': f'eq.{a.date}', 'limit': 2000})
    if rc.status_code == 200:
        for r in rc.json():
            g = str(r.get('game_id') or '')
            t = str(r.get('published_at') or '')
            if not g or not t:
                continue
            if g not in earliest_publish or t < earliest_publish[g]:
                earliest_publish[g] = t
    print(f'  publish times for {len(earliest_publish)} game(s)')

    mode = 'APPLY' if a.apply else 'DRY RUN'
    print(f'=== {a.sport} {a.date} unposted pick lines [{mode}] ===')
    changed = left = failed = 0
    for x in rows:
        pp = jd(x.get('primary_play')) or {}
        if str(pp.get('type') or '').lower() not in ('rl', 'spread', 'ats'):
            continue
        side = str(pp.get('side') or '').upper()
        ours = fnum(pp.get('line'))
        if ours is None or side not in ('HOME', 'AWAY'):
            continue
        v = byg.get(str(x['game_id']))
        if not v:
            continue
        newest = newest_by_game.get(str(x['game_id']), '')
        key = side.lower()
        mine = [z for z in v
                if str(z.get('side') or '').lower() == key
                and z.get('line') is not None]
        lines = [float(z['line']) for z in mine]
        if not lines:
            continue
        cnt = collections.Counter(lines)
        # Is our published number posted by ANYONE right now?
        if ours in cnt:
            continue
        # == WAS IT AVAILABLE WHEN WE PUBLISHED IT? =====================
        # A number the market has since moved off is NOT a defect: the user
        # who acted when we published got it, and first-publisher-wins says
        # that is our commitment. Only a number that had ALREADY stopped
        # existing before we published is wrong, and the two look identical
        # in a single snapshot. Measured on today's four: Georgia -12.5 was
        # posted until 12:26 and published at 10:56, so it is honest and
        # must be left alone; Western Michigan -12.5 last existed 10-29
        # 17:45 and went out 10-30 22:06, 28h after it vanished.
        pub_at = earliest_publish.get(str(x['game_id']))
        hist = full_history(str(x['game_id']))
        if hist is None:
            print(f'\n  {x["away_team"]} @ {x["home_team"]}: full history '
                  f'unreadable -- LEFT ALONE rather than changed blind')
            left += 1
            continue
        last_seen = None
        for z in hist:
            if (str(z.get('side') or '').lower() == key
                    and z.get('line') is not None
                    and abs(float(z['line']) - ours) < 0.01):
                c = str(z.get('captured_at') or '')
                if last_seen is None or c > last_seen:
                    last_seen = c
        if pub_at and last_seen and last_seen >= pub_at:
            print(f'\n  {x["away_team"]} @ {x["home_team"]}: '
                  f'{pp.get("label")!r} is no longer posted, but it WAS '
                  f'available at publish ({pub_at[:16]}, last seen '
                  f'{last_seen[:16]}) -- honest commitment, LEFT ALONE')
            left += 1
            continue
        modal, nbooks = cnt.most_common(1)[0]
        if abs(modal - ours) < MIN_GAP:
            continue
        label = str(pp.get('label') or '')
        name = f'{x["away_team"]} @ {x["home_team"]}'
        print(f'\n  {name}')
        print(f'     published {label!r} line={fmt(ours)}')
        print(f'     books ({len(mine)}) on our side at {newest[:16]}: '
              f'{dict(cnt)}')
        if nbooks < MIN_BOOKS:
            print(f'     -> modal {fmt(modal)} has only {nbooks} book(s) '
                  f'(< {MIN_BOOKS}); LEFT ALONE rather than guessed')
            left += 1
            continue
        prices = [float(z['price']) for z in mine
                  if abs(float(z['line']) - modal) < 0.01
                  and z.get('price') is not None]
        med_price = st.median(prices) if prices else None
        better_for_us = (ours > modal) if side == 'HOME' else (ours > modal)
        why = ('our number is BETTER than any book, so grading it would '
               'inflate the record'
               if better_for_us else
               'our number is WORSE than any book, so a user following us '
               'overpays')
        print(f'     -> NO book posts {fmt(ours)}; modal {fmt(modal)} '
              f'({nbooks} books, median price '
              f'{fmt(med_price) if med_price is not None else "n/a"})')
        print(f'     -> {why}')
        # rebuild the label: keep everything before the number
        team = label
        for tok in (f'{ours:+g}', f'{ours:g}', f'{abs(ours):g}'):
            if tok and tok in team:
                team = team.split(tok)[0]
                break
        team = team.rstrip(' +-')
        new_label = f'{team} {fmt(modal)}' if team.strip() else label
        print(f'     -> label {label!r} -> {new_label!r}')
        if not a.apply:
            changed += 1
            continue
        newpp = dict(pp)
        newpp['line'] = modal
        newpp['label'] = new_label
        newpp['_line_refreshed'] = {
            'at': a.date, 'was': ours, 'now': modal, 'books': nbooks,
            'capture': newest[:19], 'median_price': med_price,
            'by': 'refresh_unposted_pick_lines',
            'reason': 'no book was posting the published number'}
        pr = requests.patch(f'{SB}/rest/v1/{tbl}', headers=H_W, timeout=60,
                            params={'game_id': f'eq.{x["game_id"]}'},
                            json={'primary_play': newpp})
        back = pr.json() if pr.status_code in (200, 201) else []
        ok = False
        if back:
            b = jd(back[0].get('primary_play')) or {}
            ok = fnum(b.get('line')) == modal
        if not ok:                       # readback, never the status code
            g = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_R, timeout=60,
                             params={'game_id': f'eq.{x["game_id"]}',
                                     'select': 'primary_play'})
            gj = g.json() if g.status_code == 200 else []
            if gj:
                b = jd(gj[0].get('primary_play')) or {}
                ok = fnum(b.get('line')) == modal
        print(f'     HTTP {pr.status_code} '
              f'{"CONFIRMED" if ok else "NOT CONFIRMED -- a trigger may refuse it"}')
        print(f'     NOTE the public_receipts row is frozen at '
              f'{fmt(ours)} and will still grade there (R96)')
        changed += 1 if ok else 0
        failed += 0 if ok else 1
    print(f'\n=== corrected={changed} left_alone={left} failed={failed} ===')
    if changed and not a.apply:
        print('Re-run with --apply to write.')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
