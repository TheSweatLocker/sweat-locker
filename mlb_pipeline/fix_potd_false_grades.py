#!/usr/bin/env python3
"""Un-grade POTD rows whose game has not been played, and repair a stale narrative.

== 2026-10-15 - WHY ==
ANDY: "Did POTD shift today at all could of swore i saw Guardians earlier and
now i see dodgers?" ... "LAD game ahsnt pkayed yet". Both observations were
right, and they are two halves of one defect.

daily_best_bet_history row id=738 (bet_date 2026-10-15):
    game       Milwaukee Brewers @ Los Angeles Dodgers
    lean       Los Angeles Dodgers ML (resolver STRONG)
    narrative  "**Play of the Day: Tampa Bay Rays @ Cleveland Guardians ML**
                Gavin Williams' solid 3.84 xERA ..."   <-- A DIFFERENT GAME
    result     Win                                     <-- GAME NOT PLAYED

mlb_game_results for 2026-10-15: Brewers @ Dodgers is None-None, while
Rays @ Guardians finished 2-3. `resolved_at` is 21:10 UTC = 17:10 ET, stamped
BEFORE first pitch. So the grader scored the NARRATIVE's game and wrote the
result onto a row whose published pick is the Dodgers.

jerry_cache best_bet_2026-10-15 is CORRECT throughout -- Brewers @ Dodgers,
Dodgers ML, narrative about Glasnow's 2.88 xERA, result None. So the pick
itself was never in doubt; only the ledger row disagreed with itself.

Nothing here changes a PICK. `game`, `lean`, `odds_american` and `sweat_score`
are untouched. This removes a grade from a game that has not finished and
replaces a narrative that describes the wrong game.

AUDITED ACROSS ALL HISTORY, properly joined (full pagination of all six
results tables, team names normalised, bet_date allowed to differ from
game_date by a day because a night game crosses UTC midnight):

    POTD rows 161, graded 146
      graded AND the game has a final score : 144
      graded BUT the game has NO score      :   2
      could not locate a results row        :   0

Only two, and this fixes both. An earlier count of 144 was my own broken join
-- paginated at 3000 rows and matched on bet_date with no date tolerance
(feedback_never_measure_from_truncated_output).

Record effect, stated plainly: POTD goes 82-60 (57.7%) -> 81-60 (57.4%) for
10-15, and the 05-09 NRFI row likewise drops a Win. The record gets SMALLER and
slightly worse, which is the point -- it becomes true.

USAGE
    python fix_potd_false_grades.py --dry-run
    python fix_potd_false_grades.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

TABLES = ['mlb_game_results', 'nfl_game_results', 'ncaaf_game_results',
          'nhl_game_results', 'nba_game_results', 'ncaab_game_results']


def page(t, params):
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=p, timeout=180)
        if r.status_code not in (200, 206):
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def nk(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', s.lower())


def build_scored():
    scored = {}
    for t in TABLES:
        rows = page(t, {'select': 'game_date,home_team,away_team,home_score,'
                                  'away_score'})
        for g in rows:
            k = (nk(g.get('home_team')), nk(g.get('away_team')),
                 str(g.get('game_date'))[:10])
            has = (g.get('home_score') is not None
                   and g.get('away_score') is not None)
            scored[k] = has or scored.get(k, False)
    return scored


def has_score(scored, game_str, bet_date):
    if ' @ ' not in str(game_str or ''):
        return None
    away, home = [s.strip() for s in str(game_str).split(' @ ', 1)]
    try:
        base = dt.date.fromisoformat(str(bet_date)[:10])
    except Exception:                                          # noqa: BLE001
        return None
    for delta in (0, 1, -1):
        k = (nk(home), nk(away),
             (base + dt.timedelta(days=delta)).isoformat())
        if k in scored:
            return scored[k]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    scored = build_scored()
    potd = page('daily_best_bet_history', {'select': '*'})
    graded = [x for x in potd if str(x.get('result')) in ('Win', 'Loss',
                                                          'Push')]
    bad = [x for x in graded if has_score(scored, x.get('game'),
                                          x.get('bet_date')) is False]
    print(f'POTD rows {len(potd)}, graded {len(graded)}, '
          f'graded-on-unplayed {len(bad)}')
    for x in bad:
        print(f"  id={x.get('id')} {x.get('bet_date')} "
              f"{str(x.get('game'))[:44]:<44} result={x.get('result')}")

    # The narrative repair: take jerry_cache's, which is the correct one.
    narr_fix = None
    jc = page('jerry_cache', {'select': '*',
                              'cache_key': 'eq.best_bet_2026-10-15'})
    row738 = next((x for x in potd if str(x.get('bet_date'))[:10]
                   == '2026-10-15'), None)
    if jc and row738:
        good = str(jc[0].get('narrative') or '')
        cur = str(row738.get('narrative') or '')
        # Only repair when the ledger narrative names a different matchup than
        # the row's own `game`. Never overwrite a narrative that already agrees.
        gm = str(row738.get('game') or '')
        home = gm.split(' @ ')[-1].strip() if ' @ ' in gm else ''
        agrees = bool(home) and nk(home) in nk(cur)
        if good and not agrees:
            narr_fix = (row738, good, cur)
            print(f"\nNARRATIVE MISMATCH on id={row738.get('id')}")
            print(f"  row game   : {gm}")
            print(f"  ledger says: {cur[:88]!r}")
            print(f"  jerry_cache: {good[:88]!r}")
        else:
            print('\nnarrative already agrees with the row game — '
                  'leaving it alone')

    if a.dry_run:
        print('\nDRY RUN — would null result/resolved_at on the rows above'
              + (' and repair 1 narrative' if narr_fix else ''))
        return 0

    for x in bad:
        # Patch ONLY columns proven present on this row: naming a missing one
        # makes PostgREST drop the WHOLE patch (PGRST204).
        patch = {'result': None}
        if 'resolved_at' in x:
            patch['resolved_at'] = None
        w = requests.patch(f'{SB}/rest/v1/daily_best_bet_history',
                           headers=H_WRITE, params={'id': f'eq.{x["id"]}'},
                           data=json.dumps(patch), timeout=60)
        print(f"  id={x['id']} un-grade -> HTTP {w.status_code}")

    if narr_fix:
        row, good, _cur = narr_fix
        w = requests.patch(f'{SB}/rest/v1/daily_best_bet_history',
                           headers=H_WRITE, params={'id': f'eq.{row["id"]}'},
                           data=json.dumps({'narrative': good}), timeout=60)
        print(f"  id={row['id']} narrative -> HTTP {w.status_code}")

    # A 204 is not a write. Read back independently.
    print('\nREAD BACK:')
    after = page('daily_best_bet_history', {'select': '*'})
    for x in after:
        if x.get('id') in {y.get('id') for y in bad} or \
                str(x.get('bet_date'))[:10] == '2026-10-15':
            n = str(x.get('narrative') or '')[:70].replace('\n', ' ')
            print(f"  id={x.get('id')} {x.get('bet_date')} "
                  f"result={str(x.get('result')):<6} "
                  f"resolved_at={str(x.get('resolved_at'))[:19]:<19} "
                  f"narr={n!r}")
    g2 = [x for x in after if str(x.get('result')) in ('Win', 'Loss', 'Push')]
    w2 = sum(1 for x in g2 if x.get('result') == 'Win')
    l2 = sum(1 for x in g2 if x.get('result') == 'Loss')
    print(f'\nPOTD record now: {w2}-{l2} '
          f'({100*w2/(w2+l2):.1f}%) on {len(g2)} graded rows')
    still = [x for x in g2
             if has_score(scored, x.get('game'), x.get('bet_date')) is False]
    print(f'graded-on-unplayed remaining: {len(still)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
