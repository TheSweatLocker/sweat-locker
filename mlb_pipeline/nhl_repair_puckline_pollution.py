#!/usr/bin/env python3
"""Clear American prices that were written into nhl_game_results.close_puckline.

══ 2026-10-13 · WHAT WENT WRONG ══
close_puckline is the LINE, home-perspective, NEGATIVE = home favoured. Its
only legal values are ±1.5 (occasionally ±2.5). The PRICES have their own
columns, added 2026-10-01 because grading a ±1.5 puck line at a flat -110 is
meaningless.

backfill_historical_odds_hoops_hockey.py wrote `int(oc['price'])` into it,
under a comment that asserted the opposite convention as established fact.
Writer fixed in that file; this clears what it already stored.

TWO BUGS COMPOUNDED, which is why the damage is so lopsided:
  1. St. Louis is the one team the NHL API and the Odds API spell differently
     ('St. Louis Blues' vs 'St Louis Blues'), so the live puller never filled
     its rows — they stayed NULL.
  2. That backfill only writes where the value is NULL. It therefore found
     exactly St. Louis's holes and filled them with the wrong field.

Measured before the repair:
    rows with a non-puck-line value in close_puckline      78
    of those involving St. Louis                           75  (96%)
    St. Louis rows in 2024-25: polluted 75, NULL 8, valid   0
    rows whose spread_result was DERIVED from the bad value  3

WHY NULL RATHER THAN RECOMPUTE. The price cannot be inverted back into a line
— many lines map to one price. line_history does not reach back to 2024-25, so
there is nothing to recompute from. A NULL is an honest "we do not have the
closing puck line for this game"; leaving -258 in place lets any future grader
silently conclude the home team needed to win by 258 goals. The three derived
spread_results are cleared with it, because a grade computed from a fabricated
line is worse than no grade.

SAFETY
  * Touches ONLY values outside the legal set — a real ±1.5 is never modified.
  * Clears spread_result only where it was derived from the value being
    cleared (recomputing it from the bad line reproduces the stored grade).
  * Leaves scores, moneylines, totals and total_result alone.
  * Dry-run by default.

USAGE
    python nhl_repair_puckline_pollution.py
    python nhl_repair_puckline_pollution.py --apply
"""
from __future__ import annotations

import argparse
import collections
import sys

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

#: Every puck line a book actually posts. 0.0 is NOT here — see
#: project notes on the median artefact; a 0.0 is a separate defect
#: (bimodal ±1.5 averaged by statistics.median) and is reported, not cleared,
#: because it needs the mode fix upstream rather than a data edit.
LEGAL = {-2.5, -1.5, -0.5, 0.5, 1.5, 2.5}


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code} {r.text[:160]}')
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--include-zero', action='store_true',
                    help='also clear close_puckline 0.0 — the even-split median '
                         'artefact. A 0.0 puck line does not exist in hockey, and '
                         'any spread_result graded against it graded the '
                         'moneyline instead. The writer is fixed in '
                         'odds_pull_core._posted_consensus; this clears the rows '
                         'it already produced.')
    a = ap.parse_args()
    if not a.apply:
        print('DRY RUN — nothing is written. Re-run with --apply.\n')

    rows = page('nhl_game_results', {'select': '*'})
    present = [r for r in rows if r.get('close_puckline') is not None]
    bad, zeros = [], []
    for r in present:
        v = float(r['close_puckline'])
        if v in LEGAL:
            continue
        (zeros if v == 0.0 else bad).append(r)

    print(f'=== nhl_game_results.close_puckline audit')
    print(f'  rows={len(rows)}  with a value={len(present)}')
    print(f'  ILLEGAL (a price, not a line) = {len(bad)}')
    print(f'  0.0 (even-split median artefact) = {len(zeros)}'
          + ('  [INCLUDED]' if a.include_zero else '  [reported only — '
             'pass --include-zero to clear]'))
    for r in zeros:
        print(f"      {r.get('game_date')} {r.get('away_team')} @ "
              f"{r.get('home_team')}  spread_result={r.get('spread_result')}")
    if a.include_zero:
        bad = bad + zeros
    if not bad:
        print('  nothing to repair')
        return

    teams: collections.Counter = collections.Counter()
    for r in bad:
        teams[r.get('home_team')] += 1
        teams[r.get('away_team')] += 1
    print('  teams involved: ' + ', '.join(f'{t} ({n})' for t, n in teams.most_common(6)))
    months = collections.Counter(str(r.get('game_date'))[:7] for r in bad)
    print('  by month: ' + str(dict(sorted(months.items()))))

    derived = 0
    patches = []
    for r in bad:
        body = {'close_puckline': None}
        sr = r.get('spread_result')
        if sr and r.get('home_score') is not None:
            m = float(r['home_score']) - float(r['away_score'])
            cov = m + float(r['close_puckline'])
            repro = ('push' if abs(cov) < 1e-9
                     else 'home_covered' if cov > 0 else 'away_covered')
            if repro == sr:
                # The stored grade is reproducible from the bogus line, so it
                # WAS computed from it. Clear it with the line.
                body['spread_result'] = None
                derived += 1
        patches.append((r['game_id'], body,
                        f"{r.get('game_date')} {r.get('away_team')} @ "
                        f"{r.get('home_team')} pl={r.get('close_puckline')}"))
    print(f'  spread_result values derived from a bogus line = {derived} '
          f'(these are cleared too)')

    if not a.apply:
        print(f'\n  [dry] would clear {len(patches)} close_puckline values')
        for _g, _b, lbl in patches[:10]:
            print(f'      {lbl}')
        return

    ok = 0
    for gid, body, lbl in patches:
        pr = requests.patch(f'{SB}/rest/v1/nhl_game_results?game_id=eq.{gid}',
                            headers=H_WRITE, json=body, timeout=30)
        if pr.status_code in (200, 204):
            ok += 1
        else:
            print(f'    ⚠ patch {gid} ({lbl}) {pr.status_code}: {pr.text[:200]}')
    print(f'\n  cleared {ok}/{len(patches)}')


if __name__ == '__main__':
    main()
