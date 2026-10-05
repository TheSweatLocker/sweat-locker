#!/usr/bin/env python3
"""Grade every stored NBA game that already has a score and a line.

WHY (2026-10-04)
----------------
The NBA season opened 10-03 and we serve nothing for it: 0 props, 0 Jerry
reads, 0 team ratings. Before any of that can be built honestly, the history
has to be graded — a model you cannot backtest is a guess.

nba_game_results holds 2,766 rows: 2,722 with final scores back to
2024-10-22, and 2,434 with a closing spread AND total. But only 1,198 carry
`spread_result`. The other ~1,236 were never graded, and NOT because anything
was missing — the line and the score were sitting in the same row the whole
time. `nba_resolve_results` only ever grades the single date it is run on, so
any game not resolved on its own day stayed blank forever. This is the exact
pattern already documented for NHL/NCAAB/NBA closing lines in
[[project_ncaab_nba_readiness_922]].

So this needs no API call and no new source. It is arithmetic on rows we
already own.

SIGN CONVENTION — VERIFIED, NOT ASSUMED
---------------------------------------
`close_spread` is not signed consistently across our sports (NFL stores the
AWAY line; see project_close_spread_sign_bug_914), so guessing here would
silently invert 1,236 grades. Checked against the 1,198 rows that ARE already
graded:

    close_spread as HOME line : 1198/1198 agree (100.0%)
    close_spread as AWAY line :  825/1198 agree ( 68.9%)

And directionally: close_spread < 0 -> home wins 68.9% (n=1,419);
close_spread > 0 -> home wins 33.1% (n=971). Negative favours HOME.

That matches nba_resolve_results._grade_spread, whose logic this reuses by
import rather than reimplementing — two copies of a grading rule drift.

NEVER REGRADES. Only fills rows where the result column is NULL, and verifies
by read-back.

    python nba_grade_backlog.py
    python nba_grade_backlog.py --apply
"""
from __future__ import annotations
import argparse, collections, json, os, sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

# Reuse the shipped graders rather than restating the rule.
from nba_resolve_results import _grade_spread, _grade_total


def page(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        p = dict(params)
        p.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=120, params=p)
        if r.status_code not in (200, 206):
            print(f'  ⚠ {table} {r.status_code}: {r.text[:140]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    rows = page('nba_game_results', {
        'select': 'game_id,game_date,home_team,away_team,home_score,away_score,'
                  'close_spread,close_total,spread_result,total_result,'
                  'home_win,total_points'})
    print(f'=== nba_grade_backlog · {len(rows)} rows · '
          f'{"APPLY" if args.apply else "DRY"} ===\n')

    # Guard: prove the convention still holds before writing anything with it.
    agree = tot = 0
    for x in rows:
        if not x.get('spread_result') or x.get('close_spread') is None:
            continue
        if x.get('home_score') is None:
            continue
        want = _grade_spread(x['home_score'], x['away_score'], x['close_spread'])
        tot += 1
        agree += (str(x['spread_result']).strip().lower() == want)
    if tot:
        pct = 100 * agree / tot
        print(f'convention check vs {tot} already-graded rows: {agree} agree ({pct:.1f}%)')
        if pct < 99.0:
            print('REFUSING: the stored grades do not match _grade_spread, so the '
                  'sign convention is not what this script believes. Investigate '
                  'before grading the backlog.')
            return 1
    else:
        print('REFUSING: no already-graded rows to validate the convention against.')
        return 1

    fixes, why = [], collections.Counter()
    for x in rows:
        if x.get('home_score') is None or x.get('away_score') is None:
            why['no final score'] += 1
            continue
        hs, as_ = x['home_score'], x['away_score']
        patch = {}
        if x.get('spread_result') is None:
            sp = _grade_spread(hs, as_, x.get('close_spread'))
            if sp:
                patch['spread_result'] = sp
            else:
                why['no closing spread'] += 1
        if x.get('total_result') is None:
            tr = _grade_total(hs, as_, x.get('close_total'))
            if tr:
                patch['total_result'] = tr
            else:
                why['no closing total'] += 1
        if x.get('home_win') is None:
            patch['home_win'] = hs > as_
        if x.get('total_points') is None:
            patch['total_points'] = hs + as_
        if patch:
            fixes.append((x, patch))
        else:
            why['already complete'] += 1

    print(f'\nrows to fill: {len(fixes)}')
    cf = collections.Counter()
    for _x, p in fixes:
        for k in p:
            cf[k] += 1
    for k, n in cf.most_common():
        print(f'   {k:<16} {n}')
    print('\nskipped:')
    for k, n in why.most_common():
        print(f'   {n:5d}  {k}')

    if not args.apply:
        print('\n--- sample ---')
        for x, p in fixes[:8]:
            print(f'   {x["game_date"]} {x["away_team"][:18]:18s} {x["away_score"]}-'
                  f'{x["home_score"]} {x["home_team"][:18]:18s} '
                  f'sp={x["close_spread"]} tot={x["close_total"]} -> {p}')
        print('\nre-run with --apply')
        return 0

    ok = bad = 0
    for x, patch in fixes:
        r = requests.patch(f'{SB}/rest/v1/nba_game_results', headers=H_W, timeout=60,
                           params={'game_id': f'eq.{x["game_id"]}'},
                           data=json.dumps(patch))
        body = r.json() if r.content else []
        if r.status_code not in (200, 204) or not body:
            if bad < 4:
                print(f'   x {x["game_id"]} {r.status_code} {r.text[:120]}')
            bad += 1
            continue
        # Verify the VALUES, not just that a row came back.
        got = body[0]
        if all(str(got.get(k)) == str(v) for k, v in patch.items()):
            ok += 1
        else:
            if bad < 4:
                print(f'   x REFUSED {x["game_id"]}: wanted {patch}, '
                      f'got {{k: got.get(k) for k in patch}}')
            bad += 1
    print(f'\ngraded {ok}/{len(fixes)} (verified by read-back), {bad} failed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
