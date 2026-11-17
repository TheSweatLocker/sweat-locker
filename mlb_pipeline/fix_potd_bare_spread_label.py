#!/usr/bin/env python3
"""Give today's published POTD spread its number back, on every surface.

== WHY (2026-11-16) ========================================================
ANDY, looking at the app: "POTD says Golden State ATS, the spread could
chnage right? So it should be an actual spread".

He is right, and on the most prominent play in the app it is not cosmetic.
A spread MOVES. "Golden State Warriors ATS" tells a user to bet whatever
number their book happens to be showing, which is not the bet we made, and
it grades against nothing -- the potd receipt went out with pick_line NULL
*and* pick_side NULL. That is the 2026-10-31 failure exactly, when twelve
NBA receipts on finished dates were a line market with no line and could
never be graded.

THE CAUSE, and it is a race, not a bad read. jerry_reads id=20840 now says
'Golden State Warriors -3.5' with call_line=-3.5 -- but only since 23:01Z,
when the alignment backfill filled the number in. The POTD published at
13:34Z, when call_line was still NULL, so jerry_anchor_potd's rl branch
rendered the bare market word. That branch now recovers the number from the
posted close (jerry_anchor_potd._recovered_spread_line, same commit), so
this script is the one-time repair of what already published.

FILLING IN THE NUMBER IS NOT CHANGING THE PICK. The side never changes, the
market never changes, the price never changes. This is the argument the pick
lock and refresh_unposted_pick_lines already accept, and the narrower of the
two cases they cover: they replace a number that no book posts, whereas here
there is no number at all, so nothing a user could have acted on is being
moved. [[feedback_a_line_refresh_must_move_the_prose]] is why the narrative
and the leanDisplay are rewritten in the same pass -- a refresh that leaves
the prose saying something else has only moved the contradiction.

THE SIGN IS THE WHOLE RISK, so it is not guessed. close_spread is +3.5 and
NBA's proven orientation (consistency_watchdog._CLOSE_HOME_POSITIVE, read
not re-typed) is home-negative, so the home line is +3.5 and the away line
is -3.5. Golden State is AWAY. That agrees with the read's own later
call_line (-3.5) AND with away_ml_close -161, which can only belong to the
favourite. Three independent agreements; the script re-derives it live and
REFUSES if they disagree.

    python fix_potd_bare_spread_label.py                  # dry
    python fix_potd_bare_spread_label.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

import requests
from dotenv import load_dotenv

load_dotenv('.env')
load_dotenv('../.env')

SB = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

#: ctx table per sport, for the close_spread lookup.
_CTX = {'NBA': 'nba_game_context', 'NFL': 'nfl_game_context',
        'NCAAF': 'ncaaf_game_context', 'NCAAB': 'ncaab_game_context',
        'MLB': 'game_context'}

#: a bare market word at the end of a label, with no number anywhere.
_BARE = re.compile(r'\b(ATS|RL|SPREAD)\b(?!.*[-+]\d)', re.I)


def _conventions() -> dict:
    """The ONE proven close_spread orientation table, read not re-typed."""
    try:
        from consistency_watchdog import _CLOSE_HOME_POSITIVE
        return dict(_CLOSE_HOME_POSITIVE)
    except Exception as e:                                     # noqa: BLE001
        print(f'  ! cannot import close_spread conventions '
              f'({type(e).__name__}) - REFUSING to guess a sign')
        return {}


def _readback(path: str, params: dict, field: str) -> object:
    g = requests.get(f'{SB}/rest/v1/{path}', headers=H_R, timeout=30,
                     params={**params, 'select': field})
    j = g.json() if g.status_code == 200 else []
    return j[0].get(field) if isinstance(j, list) and j else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not (SB and KEY):
        print('SUPABASE env missing')
        return 2

    print(f'=== POTD bare-spread repair · {a.date} · '
          f'{"APPLY" if a.apply else "DRY"} ===')

    h = requests.get(f'{SB}/rest/v1/daily_best_bet_history', headers=H_R,
                     timeout=30, params={'select': 'id,bet_date,sport,game,'
                                                   'lean,result,odds_american',
                                         'bet_date': f'eq.{a.date}'})
    rows = h.json() if h.status_code == 200 else []
    if not rows:
        print(f'  no POTD row for {a.date} - nothing to do')
        return 0
    row = rows[0]
    lean = str(row.get('lean') or '')
    sport = str(row.get('sport') or '').upper()
    print(f'  POTD id={row["id"]} {sport} {row.get("game")}')
    print(f'    lean = {lean!r}')

    if str(row.get('result') or '').lower() not in ('', 'none', 'pending'):
        print(f'  POTD already graded ({row.get("result")}) - REFUSING. A '
              f'graded pick\'s number is part of the record.')
        return 0
    if not _BARE.search(lean):
        print('  lean already carries a number (or is not a spread) - '
              'nothing to repair')
        return 0

    # ── the number, re-derived live and cross-checked ──────────────────
    hp = _conventions().get(sport)
    if hp is None:
        print(f'  sport={sport} has no PROVEN close_spread orientation - '
              f'REFUSING rather than guessing the sign')
        return 1
    tbl = _CTX.get(sport)
    gm = str(row.get('game') or '')
    away = gm.split(' @ ')[0].strip() if ' @ ' in gm else None
    if not (tbl and away):
        print(f'  cannot resolve ctx table / teams from game={gm!r}')
        return 1
    c = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_R, timeout=30,
                     params={'select': 'game_id,away_team,home_team,'
                                       'close_spread,away_ml_close,'
                                       'home_ml_close',
                             'game_date': f'eq.{a.date}',
                             'away_team': f'eq.{away}'})
    ctxs = c.json() if c.status_code == 200 else []
    if not ctxs:
        print(f'  no {tbl} row for {gm} - unactionable, left alone')
        return 1
    ctx = ctxs[0]
    try:
        cs = float(ctx.get('close_spread'))
    except (TypeError, ValueError):
        print(f'  close_spread is {ctx.get("close_spread")!r} - no posted '
              f'number exists, so there is nothing to recover')
        return 1

    # which side is the POTD on? From the label's team, against ctx.
    team = _BARE.sub('', lean)
    team = re.sub(r'\(.*?\)', '', team).strip()
    side = ('AWAY' if team == str(ctx.get('away_team'))
            else 'HOME' if team == str(ctx.get('home_team')) else None)
    if side is None:
        print(f'  label team {team!r} matches neither '
              f'{ctx.get("away_team")!r} nor {ctx.get("home_team")!r} - '
              f'REFUSING ([[feedback_a_name_check_needs_the_right_row]])')
        return 1
    home_line = -cs if hp else cs
    line = home_line if side == 'HOME' else -home_line

    # cross-check against the moneyline: the favourite's spread is negative.
    try:
        own_ml = float(ctx.get('away_ml_close') if side == 'AWAY'
                       else ctx.get('home_ml_close'))
    except (TypeError, ValueError):
        own_ml = None
    if own_ml is not None and line != 0:
        agree = (own_ml < 0) == (line < 0)
        print(f'    close_spread={cs:+g} ({sport} home-positive={hp}) '
              f'-> {side} line {line:+g}; own ML {own_ml:+g} '
              f'-> {"AGREES" if agree else "DISAGREES"}')
        if not agree:
            print('  the spread sign and the moneyline sign DISAGREE - '
                  'REFUSING. One of them is wrong and a published number '
                  'must not be a coin flip.')
            return 1
    else:
        print(f'    close_spread={cs:+g} -> {side} line {line:+g} '
              f'(no ML to cross-check)')

    new_lean = _BARE.sub(lambda m: f'{m.group(1).upper()} {line:+g}', lean, 1)
    print(f'    new lean = {new_lean!r}')
    if not a.apply:
        print('  DRY RUN - re-run with --apply to write.')
        return 0

    ok_all = True

    # ── 1. the history row the app reads ──────────────────────────────
    requests.patch(f'{SB}/rest/v1/daily_best_bet_history', headers=H_W,
                   timeout=30, params={'id': f'eq.{row["id"]}'},
                   json={'lean': new_lean})
    back = _readback('daily_best_bet_history', {'id': f'eq.{row["id"]}'},
                     'lean')
    ok = (back == new_lean)
    ok_all &= ok
    print(f'    daily_best_bet_history: '
          f'{"CONFIRMED" if ok else f"NOT CONFIRMED (still {back!r})"}')

    # ── 2. the cache the home screen renders, prose included ──────────
    ck = f'best_bet_{a.date}'
    g = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H_R, timeout=30,
                     params={'select': 'game_id,sport,narrative,data',
                             'cache_key': f'eq.{ck}'})
    cj = g.json() if g.status_code == 200 else []
    if cj:
        cr = cj[0]
        data = cr.get('data') or {}
        old_disp = str(data.get('leanDisplay') or '')
        if old_disp:
            data['leanDisplay'] = _BARE.sub(
                lambda m: f'{m.group(1).upper()} {line:+g}', old_disp, 1)
        narr = str(cr.get('narrative') or '')
        # Move the prose too: its bolded headline is the same bare label.
        new_narr = _BARE.sub(
            lambda m: f'{m.group(1).upper()} {line:+g}', narr, 1)
        if data.get('narrative'):
            data['narrative'] = _BARE.sub(
                lambda m: f'{m.group(1).upper()} {line:+g}',
                str(data['narrative']), 1)
        data['_line_recovered_from'] = 'close_spread'
        requests.patch(f'{SB}/rest/v1/jerry_cache', headers=H_W, timeout=30,
                       params={'cache_key': f'eq.{ck}'},
                       json={'narrative': new_narr, 'data': data})
        bd = _readback('jerry_cache', {'cache_key': f'eq.{ck}'}, 'data')
        ok = bool(bd) and str((bd or {}).get('leanDisplay') or '') \
            == data['leanDisplay']
        ok_all &= ok
        print(f'    jerry_cache {ck}: '
              f'{"CONFIRMED" if ok else "NOT CONFIRMED"} '
              f'-> {str((bd or {}).get("leanDisplay"))!r}')
    else:
        print(f'    jerry_cache {ck}: no row')

    # ── 3. the receipts, which are the record half ────────────────────
    # pick_label/pick_side/pick_line are frozen by freeze_receipt_identity
    # (R96/B181), so these PATCHes may be silently refused -- every one is
    # therefore confirmed by READBACK, never by the status code
    # ([[feedback_204_is_not_a_write]]), and a refusal is REPORTED rather
    # than hidden. A receipt that will not take the number is the record
    # half of this bug and needs the R96 decision, not a retry.
    rc = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                      timeout=30,
                      params={'select': 'id,surface,pick_label,pick_side,'
                                        'pick_line,result',
                              'game_date': f'eq.{a.date}',
                              'limit': '1000'})
    for r_ in (rc.json() if rc.status_code == 200 else []):
        lbl = str(r_.get('pick_label') or '')
        if not _BARE.search(lbl) or team not in lbl:
            continue
        if r_.get('result') is not None:
            print(f'    receipt {r_["id"]} ({r_["surface"]}) already graded '
                  f'{r_["result"]!r} - left alone')
            continue
        want_lbl = _BARE.sub(lambda m: f'{m.group(1).upper()} {line:+g}',
                             lbl, 1)
        body = {'pick_label': want_lbl, 'pick_line': line}
        if not r_.get('pick_side'):
            body['pick_side'] = side
        requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                       timeout=30, params={'id': f'eq.{r_["id"]}'},
                       json=body)
        gl = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                          timeout=30,
                          params={'id': f'eq.{r_["id"]}',
                                  'select': 'pick_label,pick_line,pick_side'})
        gj = gl.json() if gl.status_code == 200 else []
        got = gj[0] if gj else {}
        took_line = str(got.get('pick_line')) == str(line)
        took_lbl = str(got.get('pick_label')) == want_lbl
        print(f'    receipt {r_["id"]} ({r_["surface"]}): '
              f'line={"TOOK" if took_line else "FROZEN/REFUSED"} '
              f'label={"TOOK" if took_lbl else "FROZEN/REFUSED"} '
              f'side={got.get("pick_side")!r}')
        if not (took_line and took_lbl):
            print(f'       ^ FROZEN. This receipt still grades a line '
                  f'market with no line. That is the record half of the '
                  f'bug and needs the R96 decision, not a retry.')
            ok_all = False

    print(f'\n  {"ALL CONFIRMED" if ok_all else "SOME WRITES NOT CONFIRMED"}')
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main())
