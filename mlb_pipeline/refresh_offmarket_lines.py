#!/usr/bin/env python3
"""Bring a published spread pick back to a number a book actually posts.

══ WHY ══════════════════════════════════════════════════════════════════
ANDY: "still dont undertsand that inaccuarte blurb about beathing market ahs
to stay until next build thats unsat."

He is right, and the "wait for a build" answer was incomplete. Game detail
only renders its closing-line disclosure when |our line| differs from
|close_spread| by 0.25 or more. The TEXT is client code and the sign bug in it
needs a build — but the CONDITION is data. Put our line back on the market and
the disclosure stops rendering on the CURRENT client, and the pick becomes
bettable instead of fictional.

Measured when this was written, locked spread picks off the market:
    NYG +7   vs market +10.5   card claimed "we beat the close by 3.5"
    DAL @ IND -5.5 vs +10.5    a 16-point move, the favourite flipped
    plus SF, LA, CIN, Ole Miss, Pittsburgh and 17 others

WHAT IT CHANGES, AND WHAT IT CANNOT
Both the line AND the label move together. Updating the line alone would leave
the label reading "NYG +7" over a line of 10.5, and a receipt written from that
item would carry a label and a line that disagree — worse than the problem.

public_receipts.pick_line is immutable by DB trigger, by design, so a receipt
already published keeps its old number. For NYG that means showing +10.5 while
grading +7: a HARDER line than displayed, so the record understates us rather
than inflating it. That is the safe direction, and it is the honest cost of
fixing the display without a build.

enforce_pick_lock refuses a label change on a locked row and logs it to
pick_lock_drift. The trigger exists only on ncaaf_game_context and
nfl_game_context, so NBA, NHL, NCAAB and MLB refresh cleanly; a locked football
row is reported as refused rather than forced. Nothing here force-unlocks
anything.

Games that have already started are never touched.

The per-sport home-handicap sign is MEASURED, not assumed:
    NFL    close_spread positive = home FAVOURED       -> home hcap = -cs
    NCAAF  positive = home handicap                    -> +cs
    NBA    positive = home handicap                    -> +cs
           verified 25 of 25 against the cheaper moneyline, which is the
           favourite in every sport with no convention to get wrong, and
           corr(close_spread, home margin) = -0.393
A sport without a verified sign is skipped, because guessing a convention is
indistinguishable from knowing one (feedback_edge_formula_sign_audit).

Usage:
    python refresh_offmarket_lines.py              # dry run, all sports
    python refresh_offmarket_lines.py --apply
    python refresh_offmarket_lines.py --sport NBA --apply
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
HW = dict(H, **{'Content-Type': 'application/json'})

#: Only sports whose close_spread convention has been measured.
HOME_HCAP_SIGN = {'NFL': -1.0, 'NCAAF': 1.0, 'NBA': 1.0}
CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'NBA': 'nba_game_context'}
#: Kickoff column, per sport. Every sport names it differently.
KICKOFF = {'NFL': 'kickoff_utc', 'NCAAF': 'kickoff_utc',
           'NBA': 'commence_time_utc'}
#: Below this the market has not meaningfully moved and the card already
#: renders nothing.
MIN_GAP = 0.25


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ts(v):
    if not v:
        return None
    try:
        d = dt.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def run(sport: str, apply: bool) -> tuple[int, int]:
    tbl = CTX[sport]
    sgn = HOME_HCAP_SIGN[sport]
    ko_col = KICKOFF[sport]
    now = dt.datetime.now(dt.timezone.utc)
    today = (now - dt.timedelta(hours=4)).date().isoformat()
    cols = ['game_id', 'game_date', 'home_team', 'away_team', 'close_spread',
            'primary_play', ko_col]
    if sport in ('NFL', 'NCAAF'):
        cols.append('pick_locked_at')
    r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=30,
                     params={'select': ','.join(cols),
                             'game_date': f'gte.{today}', 'limit': '400'})
    if r.status_code != 200:
        print(f'  ⚠ {sport} ctx HTTP {r.status_code}: {r.text[:160]}')
        return 0, 0
    fixed = refused = 0
    for g in (r.json() or []):
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
        ours = f(pp.get('line'))
        side = str(pp.get('side') or '').upper()
        cs = f(g.get('close_spread'))
        if ours is None or cs is None or side not in ('HOME', 'AWAY'):
            continue
        ko = ts(g.get(ko_col))
        if ko and ko <= now:
            continue                       # already started — leave history
        hh = sgn * cs
        market = hh if side == 'HOME' else -hh
        gap = market - ours
        if abs(gap) < MIN_GAP:
            continue
        # ══ DRIFT vs AN INVERTED SIGN — not the same defect ══
        # A "move" that lands on exactly minus our own number is not the
        # market moving, it is our line's sign being wrong. Six NFL rows
        # showed this on the first dry run, and the moneyline proves it:
        # SF @ ARI has SF at -425 with close_spread -8.5, so SF lays 8.5, and
        # our pick read "SF +8.5" — the biggest favourite on the board shown
        # as GETTING the points. Unbettable and ungradeable, which is the same
        # family the ensemble already documents ("11 NFL rows since 09-19
        # carry a line that disagrees with the convention, ~5 of them pure
        # sign flips of this shape").
        #
        # The repair is identical either way, but conflating them in the log
        # would hide a defect class behind the word "drift", so they are named
        # separately. NFL polarity itself is NOT the problem: measured 177 of
        # 178 rows consistent with home_hcap = -close_spread, the one outlier
        # being a -110/-110 pick'em where "favourite" means nothing.
        kind = 'SIGN' if abs(market + ours) < MIN_GAP else 'drift'
        team = str(g['home_team'] if side == 'HOME' else g['away_team'])
        new = dict(pp)
        new['line'] = market
        new['label'] = f'{team} {market:+g}'
        new['_line_refreshed'] = {
            'from': ours, 'to': market, 'at': now.isoformat(),
            'kind': kind,
            'why': ('our line carried the WRONG SIGN' if kind == 'SIGN'
                    else 'published line was off the market'),
        }
        tag = '' if not g.get('pick_locked_at') else '  [LOCKED]'
        print(f'  {"FIX  " if apply else "[DRY]"} {sport} [{kind:5s}] '
              f'{g["away_team"]} @ {g["home_team"]}: '
              f'{pp.get("label")} -> {new["label"]} ({gap:+.1f}){tag}')
        if not apply:
            fixed += 1
            continue
        up = requests.patch(f'{SB}/rest/v1/{tbl}', headers=HW, timeout=30,
                            params={'game_id': f'eq.{g["game_id"]}'},
                            data=json.dumps({'primary_play': new}))
        if up.status_code not in (200, 204):
            print(f'      ⚠ PATCH HTTP {up.status_code}: {up.text[:160]}')
            refused += 1
            continue
        # A 200 is not a write. enforce_pick_lock silently reverts a locked
        # row, so read the label back and count only what actually moved.
        back = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=30,
                            params={'select': 'primary_play',
                                    'game_id': f'eq.{g["game_id"]}',
                                    'limit': '1'})
        got = ((back.json() or [{}])[0].get('primary_play') or {}) \
            if back.status_code == 200 else {}
        if str(got.get('label')) == new['label']:
            fixed += 1
        else:
            print(f'      ⚠ REVERTED by the pick lock — still '
                  f'{got.get("label")!r}')
            refused += 1
    return fixed, refused


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CTX), action='append')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print(f'=== refresh_offmarket_lines · '
          f'{"APPLY" if a.apply else "DRY RUN"} ===')
    tf = tr = 0
    for sport in (a.sport or sorted(CTX)):
        fx, rf = run(sport, a.apply)
        tf += fx
        tr += rf
    print(f'  {"refreshed" if a.apply else "would refresh"}: {tf}'
          + (f'   refused by the pick lock: {tr}' if tr else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
