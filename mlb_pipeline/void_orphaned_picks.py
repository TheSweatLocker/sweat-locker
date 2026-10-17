#!/usr/bin/env python3
"""Void picks whose game never resolved — the postponement orphan.

== 2026-10-16 - WHY ==
ANDY: "mlb game got delayed to tomorrow so everything should be pushed right?"

NOTHING IS PUSHED. Checked at 23:51 ET:

    mlb_game_results   2026-10-16  TB @ CLE  None-None   <- still dated TODAY
    mlb_game_context   2026-10-16  TB @ CLE  pub=total:OVER PRIME
    POTD 2026-10-16    Nick Martinez Over 3.5 Hits Allowed (Jerry 82/100)
                       result=Pending
    2026-10-17         NO ROW IN EITHER TABLE

Nothing is MIS-graded, which is the good news — the POTD is Pending and all
seven of that game's props are result=None, which is correct for an unplayed
game, and yesterday's identity gate plus the scores requirement stop a false
grade being invented.

THE RISK IS AN ORPHAN, and it is specific. MLB game_id is DATE-DEPENDENT:

    2026-10-15  TB @ CLE  ->  a681025734060b...
    2026-10-16  TB @ CLE  ->  b9d2e07cf437b5...

Same teams, different date, different id. So when this game is re-dated to
10-17 the pipeline mints a NEW game_id, and the 10-16 POTD and its props can
never find their result. They sit Pending forever, quietly inflating the
ungraded backlog and holding a slot in the record that will never settle.

There is precedent in the repo: game_context.py:1108 — "2026-07-22 — added
after 7/21 postponement wiped POTD + STRONG YRFI + SKIP".

AND NOTHING CURRENTLY CLEANS IT UP:
  * grade_jerry_reads DOCUMENTS the rule — "No result row -> check
    postponement inference: game_date >4 days old with no result =
    postponed/cancelled. Void it." — and the branch then just `return None`.
    Documented, never implemented.
  * grade_potd has no postponement handling at all.
  * resolve_potd matches against mlb_game_results and leaves a missing game
    Pending.
  * reconcile_potd_grades only re-checks rows that are ALREADY graded.

WHY A DELAY IS NOT VOIDED IMMEDIATELY. A game pushed one day still plays and
should still grade, so voiding on day one would destroy a live pick. The
threshold is STALE_DAYS (default 4, matching the inference grade_jerry_reads
already describes). Tonight's game is 0 days stale and this script will
correctly leave it ALONE — it exists for when the re-dated game never comes
back under its old id.

Dry-run by default. Reports what it would void and why, per surface.

USAGE
    python void_orphaned_picks.py --dry-run
    python void_orphaned_picks.py --apply
    python void_orphaned_picks.py --dry-run --stale-days 7
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}
STALE_DAYS = int(os.environ.get('ORPHAN_STALE_DAYS', '4'))
RESULTS = {'MLB': 'mlb_game_results', 'NFL': 'nfl_game_results',
           'NCAAF': 'ncaaf_game_results', 'NHL': 'nhl_game_results',
           'NBA': 'nba_game_results', 'NCAAB': 'ncaab_game_results'}


def page(t, p):
    out, off = [], 0
    while True:
        q = dict(p, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _today_et():
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()


def _stale(date_str):
    try:
        d = dt.date.fromisoformat(str(date_str)[:10])
    except Exception:                                          # noqa: BLE001
        return None
    return (_today_et() - d).days


def build_played():
    """{(sport, game_id)} and {(sport, date, home, away)} that HAVE a score."""
    by_gid, by_key = set(), set()
    for sport, tbl in RESULTS.items():
        for r in page(tbl, {'select': 'game_id,game_date,home_team,away_team,'
                                      'home_score,away_score'}):
            if r.get('home_score') is None or r.get('away_score') is None:
                continue
            if r.get('game_id'):
                by_gid.add((sport, str(r['game_id'])))
            by_key.add((sport, str(r.get('game_date'))[:10],
                        str(r.get('home_team')), str(r.get('away_team'))))
    return by_gid, by_key


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    ap.add_argument('--stale-days', type=int, default=STALE_DAYS)
    a = ap.parse_args()
    stale_days = a.stale_days

    print(f'today ET {_today_et()}  ·  voiding picks whose game has no score '
          f'after {stale_days} days')
    by_gid, by_key = build_played()
    print(f'games with a final score: {len(by_gid)} by id, '
          f'{len(by_key)} by (date, home, away)')

    plan = defaultdict(list)

    # ---- POTD ----
    for x in page('daily_best_bet_history', {'select': '*'}):
        res = str(x.get('result') or '')
        if res not in ('', 'None', 'Pending'):
            continue
        d = str(x.get('bet_date'))[:10]
        age = _stale(d)
        if age is None or age <= stale_days:
            continue
        sport = str(x.get('sport') or 'MLB').upper()
        gm = str(x.get('game') or '')
        if ' @ ' in gm:
            away, home = [s.strip() for s in gm.split(' @ ', 1)]
            if (sport, d, home, away) in by_key:
                continue          # it DID play; this is a grading gap, not an orphan
        plan['potd'].append((x, age, gm))

    # ---- props ----
    for sport, tbl in (('MLB', 'mlb_pipeline_props'),
                       ('NFL', 'nfl_pipeline_props'),
                       ('NHL', 'nhl_pipeline_props')):
        for x in page(tbl, {'select': 'id,game_date,game_id,player_name,'
                                      'prop_type,result'}):
            if str(x.get('result') or '') not in ('', 'None'):
                continue
            age = _stale(x.get('game_date'))
            if age is None or age <= stale_days:
                continue
            if (sport, str(x.get('game_id'))) in by_gid:
                continue
            plan[f'props:{sport}:{tbl}'].append((x, age, ''))

    # ---- jerry_reads ----
    for x in page('jerry_reads', {'select': 'id,sport,game_date,game_id,'
                                            'call_market,call_side,result'}):
        if str(x.get('result') or '') not in ('', 'None'):
            continue
        age = _stale(x.get('game_date'))
        if age is None or age <= stale_days:
            continue
        sport = str(x.get('sport') or '').upper()
        if (sport, str(x.get('game_id'))) in by_gid:
            continue
        plan['jerry_reads'].append((x, age, ''))

    total = sum(len(v) for v in plan.values())
    print(f'\nORPHANS (ungraded, game never scored, older than {stale_days}d): '
          f'{total}')
    for k in sorted(plan):
        v = plan[k]
        ages = [age for _x, age, _g in v]
        print(f'  {k:<34}{len(v):>5}  oldest {max(ages)}d  newest {min(ages)}d')
        for x, age, gm in sorted(v, key=lambda t: -t[1])[:4]:
            lbl = (gm or f"{x.get('player_name') or ''} "
                        f"{x.get('prop_type') or x.get('call_market') or ''}")
            print(f'      {str(x.get("bet_date") or x.get("game_date"))[:10]}  '
                  f'{age:>3}d  {str(lbl)[:48]}')

    print(f'\nTONIGHT\'S DELAYED GAME: 2026-10-16 TB @ CLE is '
          f'{_stale("2026-10-16")}d stale — correctly NOT touched '
          f'(threshold {stale_days}d). It still plays and must still grade.')

    if a.dry_run:
        print('\nDRY RUN — nothing written')
        return 0

    wrote = 0
    for k, v in plan.items():
        if k == 'potd':
            for x, _age, _gm in v:
                r = requests.patch(f'{SB}/rest/v1/daily_best_bet_history',
                                   headers=H_WRITE,
                                   params={'id': f'eq.{x["id"]}'},
                                   data=json.dumps({'result': 'Void'}),
                                   timeout=60)
                wrote += 1 if r.status_code in (200, 204) else 0
        elif k.startswith('props:'):
            tbl = k.split(':')[2]
            for x, _age, _gm in v:
                r = requests.patch(f'{SB}/rest/v1/{tbl}', headers=H_WRITE,
                                   params={'id': f'eq.{x["id"]}'},
                                   data=json.dumps({'result': 'Void'}),
                                   timeout=60)
                wrote += 1 if r.status_code in (200, 204) else 0
        elif k == 'jerry_reads':
            for x, _age, _gm in v:
                r = requests.patch(f'{SB}/rest/v1/jerry_reads', headers=H_WRITE,
                                   params={'id': f'eq.{x["id"]}'},
                                   data=json.dumps({'result': 'Void'}),
                                   timeout=60)
                wrote += 1 if r.status_code in (200, 204) else 0
    print(f'\nvoided {wrote} of {total}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
