#!/usr/bin/env python3
"""Fill nfl_game_results.div_game from the canonical division map.

══ 2026-10-14 · THE GAP ══
`div_game` is populated on 100% of every NFL season from 1999 through 2025
(285/285 in each of the last five) and on **0 of 274** rows in 2026 — 79 of
them already played.

Cause: the 2026 forward schedule is created by nfl_odds_pull via
odds_pull_core.build_rows, which writes game_id / date / teams / market
columns and nothing sport-specific. The historical rows came from the results
importer, which did set it. So the column did not break — it was never written
by the writer that now creates NFL rows.

Why it matters: div_game feeds the model and the cohort tagging
(nfl_div_home_cover is a live cohort), and a NULL reads as "not a division
game" to anything doing a truthiness test. A division game mislabelled as
non-division is a wrong feature, not a missing one.

WHY NOT DERIVE IT FROM HISTORY. Tempting, because 2026 rows all involve teams
that appear in older div_game=True rows. It is wrong: the union of a team's
historical division rivals spans REALIGNMENT. Arizona's historical rivals come
back as {DAL, NYG, PHI, WAS, SEA, SF, LA, STL} because ARI played in the NFC
East until 2002, and the map built that way claims 35 teams including STL.
Deriving from it would label ARI-DAL a division game in 2026.

So the canonical CURRENT map is the only safe source. It is imported from
nfl_game_context.NFL_DIVISION rather than copied — there are already FOUR
copies of this map in the repo (nfl_game_context, nfl_cohort_backfill,
build_cohort_tag_records, mine_systems) and a fifth is how they drift.

IDEMPOTENT: only fills rows where div_game IS NULL. Never overwrites a stored
value, including a stored False.

USAGE
    python backfill_nfl_div_game.py                # dry run
    python backfill_nfl_div_game.py --apply
"""
from __future__ import annotations

import argparse
import collections
import sys

import requests

import market_line as ML
from nfl_game_context import NFL_DIVISION

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code} {r.text[:160]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not a.apply:
        print('DRY RUN — nothing is written. Re-run with --apply.\n')

    rows = page('nfl_game_results', {'select': '*'})
    missing = [r for r in rows if r.get('div_game') is None]
    print(f'=== nfl_game_results: {len(rows)} rows, '
          f'{len(missing)} missing div_game')
    if not missing:
        print('  nothing to fill')
        return 0
    seasons = collections.Counter(str(r.get('season')) for r in missing)
    print(f'  by season: {dict(sorted(seasons.items()))}')
    print(f'  division map covers {len(NFL_DIVISION)} teams')

    patches, unmapped, tally = [], collections.Counter(), collections.Counter()
    for r in missing:
        h, aw = r.get('home_team'), r.get('away_team')
        dh, da = NFL_DIVISION.get(h), NFL_DIVISION.get(aw)
        if dh is None or da is None:
            # Loud. An unmapped team means a relocation or a rename the map
            # has not caught up with, and guessing False would be a wrong
            # feature rather than a missing one.
            unmapped[h if dh is None else aw] += 1
            continue
        val = (dh == da)
        tally['div' if val else 'non-div'] += 1
        patches.append((r['game_id'], val))

    print(f'  derived: {tally["div"]} division, {tally["non-div"]} non-division')
    if unmapped:
        print(f'  ⚠ UNMAPPED teams (left NULL): {dict(unmapped)}')
    # Sanity: roughly 3 of 17 NFL games per week are division games, so a
    # share far from ~30% means the map or the teams are wrong.
    tot = tally['div'] + tally['non-div']
    if tot:
        share = 100 * tally['div'] / tot
        print(f'  division share {share:.1f}%  '
              f'(expect ~30% — 6 of a 17-game schedule)')
        if not (18 <= share <= 45):
            print('  ✗ share is implausible — refusing to write')
            return 1

    if not a.apply:
        print(f'\n  [dry] would fill {len(patches)} rows')
        return 0

    ok = 0
    for gid, val in patches:
        pr = requests.patch(f'{SB}/rest/v1/nfl_game_results?game_id=eq.{gid}',
                            headers=H_W, json={'div_game': val}, timeout=30)
        if pr.status_code in (200, 204):
            ok += 1
        else:
            print(f'  ⚠ {gid} {pr.status_code}: {pr.text[:160]}')
    print(f'\n  filled {ok}/{len(patches)}')
    chk = [r for r in page('nfl_game_results', {'select': 'div_game'})
           if r.get('div_game') is None]
    print(f'  VERIFY by re-read: {len(chk)} rows still NULL')
    return 0 if ok == len(patches) else 1


if __name__ == '__main__':
    sys.exit(main())
