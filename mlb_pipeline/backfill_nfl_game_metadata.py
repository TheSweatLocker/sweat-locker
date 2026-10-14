#!/usr/bin/env python3
"""Fill NULL nfl_game_results METADATA from nflverse. Never touches markets.

══ 2026-10-14 · THE GAP ══
`home_qb_name` is populated on 285/285 rows in each of 2023, 2024 and 2025 and
on **0 of 274** in 2026. Same shape as `div_game` was: the 2026 forward
schedule is created by nfl_odds_pull via odds_pull_core.build_rows, which
writes ids, teams and market columns and nothing sport-specific, while the
historical rows came from nfl_results_backfill, which did set it.

It matters because QB identity feeds the model. project_qb_staleness_feeds_model_1009
measured the madden delta being built from QBs who were OUT, and the starter
columns being wrong on 72% of rows was user-facing. A NULL QB cannot be
checked for staleness at all.

══ WHY NOT JUST RUN nfl_results_backfill --season 2026 ══
That is the obvious move and it would damage live data. Its build_payload
carries an explicit decision:

    # 2026-09-08: DO NOT strip None values. PostgREST batch upsert
    # requires uniform key sets across all rows in a POST array

Correct for its own batch-insert path, but the 2026 season has 72 UNPLAYED
rows, and the payload includes close_spread / close_total / close_home_ml /
close_away_ml. PATCHing those rows would write NULL over whatever the odds
pull has captured for games that have not kicked off — destroying live market
data to fill in a QB name.

So this script reuses nflverse as the authoritative source, and the parsing
already written in nfl_results_backfill, but patches ONLY an allow-list of
metadata columns, and only where the stored value is currently NULL. It cannot
touch a line, a price, a score or a grade — the allow-list is the guard, not a
convention.

IDEMPOTENT: fills NULLs only. Re-running changes nothing once clean.

USAGE
    python backfill_nfl_game_metadata.py                 # dry run, 2026
    python backfill_nfl_game_metadata.py --apply
    python backfill_nfl_game_metadata.py --season 2025 --apply
"""
from __future__ import annotations

import argparse
import collections
import sys

import requests

import market_line as ML
from nfl_results_backfill import build_payload, fetch_games

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

#: The ONLY columns this script may write. Deliberately excludes every market
#: column (open_*/close_*), every score, and every derived grade
#: (spread_result / total_result / home_win / total_points). If a column is not
#: on this list it cannot be written, regardless of what nflverse ships.
SAFE_COLS = (
    'home_qb_id', 'home_qb_name', 'away_qb_id', 'away_qb_name',
    'home_coach', 'away_coach',
    'home_rest', 'away_rest',
    'div_game', 'roof', 'surface', 'referee', 'stadium',
    'weekday', 'gametime', 'game_type', 'week',
)
#: Guard rather than comment: assert the intent so a future edit that adds a
#: market column to SAFE_COLS fails loudly here instead of silently writing it.
_FORBIDDEN = ('close_', 'open_', '_score', 'spread_result', 'total_result',
              'home_win', 'total_points')
for _c in SAFE_COLS:
    assert not any(f in _c for f in _FORBIDDEN), f'SAFE_COLS must not include {_c}'


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
    ap.add_argument('--season', default='2026')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not a.apply:
        print('DRY RUN — nothing is written. Re-run with --apply.\n')

    rows = [r for r in page('nfl_game_results', {'select': '*'})
            if str(r.get('season')) == str(a.season)]
    print(f'=== nfl_game_results season {a.season}: {len(rows)} stored rows')
    if not rows:
        print('  nothing stored for that season')
        return 0

    # Join on (game_date, home_team, away_team): nflverse ships
    # YYYY_WW_AWAY_HOME ids while our rows carry YYYYMMDD_AWAY_HOME, the id
    # mismatch nfl_results_backfill documents at its upsert_batch.
    src = {}
    for g in fetch_games():
        p = build_payload(g)
        if not p:
            continue
        k = (str(p.get('game_date')), str(p.get('home_team')),
             str(p.get('away_team')))
        src[k] = p
    print(f'  nflverse payloads indexed: {len(src)}')

    before = collections.Counter()
    for c in SAFE_COLS:
        before[c] = sum(1 for r in rows if r.get(c) is not None)

    patches, unmatched = [], 0
    filled = collections.Counter()
    for r in rows:
        k = (str(r.get('game_date')), str(r.get('home_team')),
             str(r.get('away_team')))
        p = src.get(k)
        if not p:
            unmatched += 1
            continue
        body = {}
        for c in SAFE_COLS:
            if r.get(c) is None and p.get(c) is not None:
                body[c] = p[c]
                filled[c] += 1
        if body:
            patches.append((r['game_id'], body))

    print(f'  matched to nflverse: {len(rows) - unmatched}/{len(rows)}'
          + (f'   UNMATCHED {unmatched}' if unmatched else ''))
    print(f'\n  {"column":<16}{"before":>8}{"would fill":>12}{"after":>8}')
    for c in SAFE_COLS:
        if filled[c]:
            print(f'  {c:<16}{before[c]:>8}{filled[c]:>12}'
                  f'{before[c] + filled[c]:>8}')
    print(f'\n  rows needing a patch: {len(patches)}')
    if not a.apply:
        for gid, body in patches[:4]:
            print(f'    {gid}: {sorted(body)}')
        return 0

    ok = 0
    for gid, body in patches:
        pr = requests.patch(f'{SB}/rest/v1/nfl_game_results?game_id=eq.{gid}',
                            headers=H_W, json=body, timeout=30)
        if pr.status_code in (200, 204):
            ok += 1
        else:
            print(f'  ⚠ {gid} {pr.status_code}: {pr.text[:200]}')
    print(f'  patched {ok}/{len(patches)}')

    chk = [r for r in page('nfl_game_results', {'select': '*'})
           if str(r.get('season')) == str(a.season)]
    print('\n  VERIFY by re-read:')
    for c in ('home_qb_name', 'away_qb_name', 'div_game', 'home_coach'):
        n = sum(1 for r in chk if r.get(c) is not None)
        print(f'    {c:<16}{n}/{len(chk)}')
    return 0 if ok == len(patches) else 1


if __name__ == '__main__':
    sys.exit(main())
