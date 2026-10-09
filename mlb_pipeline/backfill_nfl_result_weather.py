"""Backfill at-game weather onto CURRENT-SEASON nfl_game_results rows.

WHY THIS EXISTS RATHER THAN RAISING ingest_nflverse --until
nfl_game_results.wind stops at 2026-02-08. Every current-season game is
graded and scored but carries no at-game weather, which breaks two things:

  1. The wind lens cannot be GRADED going forward. NFL totals at >=11mph went
     UNDER 55.61% on n=1,444 (project_wind_under_lens_1009) — the one lens
     that measured positive this week — and every one of those games is from
     a PRIOR season because that is the only era with weather.
  2. The forecast cannot be SCORED. nfl_game_context.wind now holds a
     pre-game OpenWeather forecast (project_weather_pullers_silent_noop_1009),
     and the honest question about the lens is how much of the 55.61%
     survives using a forecast instead of the at-game reading. Answering it
     needs both numbers on the same game.

ingest_nflverse already maps `wind` correctly, and its --until bound defaults
to 2019 WITH AN EXPLICIT WARNING: "our 2026 rows use a different id scheme, so
pulling the current season would DUPLICATE the live games that feed published
records rather than update them." So raising that bound is not an option — it
would fork every live game in two, which is the defect B65 is already open on
for MLB.

THIS SCRIPT THEREFORE NEVER INSERTS. It matches nflverse rows to rows that
ALREADY EXIST in nfl_game_results on (game_date, away_team, home_team) — not
on game_id, which is precisely the field whose two schemes caused the problem
— and PATCHes weather columns only. A game with no existing row is skipped and
counted, never created.

FURTHER SAFETY
  * Only fills columns that are currently NULL. An existing value is never
    overwritten, so a hand-corrected figure survives.
  * Weather columns only (temp, wind, roof, surface, away_rest, home_rest).
    Scores, lines and results are never touched — those feed published
    records.
  * Dry-run by default; --apply writes.
  * Every PATCH is read back and verified, because a 204 is not a write.

CLI
    python backfill_nfl_result_weather.py
    python backfill_nfl_result_weather.py --apply
    python backfill_nfl_result_weather.py --season 2026 --apply
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}
GAMES_URL = ('https://raw.githubusercontent.com/nflverse/nfldata/'
             'master/data/games.csv')
#: Weather and rest only. Deliberately excludes every scoring, line and
#: result column — those are what published records are computed from.
WX_COLS = ('temp', 'wind', 'roof', 'surface', 'away_rest', 'home_rest')


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:160]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default='2026')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print(f'=== NFL result weather backfill · season {a.season}')
    r = requests.get(GAMES_URL, timeout=300)
    r.raise_for_status()
    sched = [g for g in csv.DictReader(io.StringIO(r.text))
             if str(g.get('season')) == str(a.season)]
    print(f'    nflverse games.csv: {len(sched)} rows for {a.season}')
    wx_avail = sum(1 for g in sched if _int(g.get('wind')) is not None)
    print(f'    of those, {wx_avail} carry a wind reading')

    ours = _page('nfl_game_results',
                 {'select': 'game_date,away_team,home_team,home_score,'
                            + ','.join(WX_COLS),
                  'game_date': f'gte.{a.season}-08-01'})
    print(f'    our rows since {a.season}-08-01: {len(ours)}')
    idx = {}
    for x in ours:
        idx[(str(x['game_date'])[:10], str(x['away_team']),
             str(x['home_team']))] = x

    todo, no_row, already, no_wx = [], 0, 0, 0
    for g in sched:
        key = (str(g.get('gameday') or '')[:10],
               str(g.get('away_team') or ''), str(g.get('home_team') or ''))
        mine = idx.get(key)
        if mine is None:
            # NEVER create it. A missing row means our ingest has not seen
            # this game, which is a different problem and not one to solve by
            # inserting a second copy alongside the live row.
            no_row += 1
            continue
        patch = {}
        for c in WX_COLS:
            if mine.get(c) is not None:
                continue            # never overwrite an existing value
            v = (g.get(c) or '').strip()
            if v == '':
                continue
            patch[c] = _int(v) if c in ('temp', 'wind', 'away_rest',
                                        'home_rest') else v
        if not patch:
            if any(mine.get(c) is not None for c in WX_COLS):
                already += 1
            else:
                no_wx += 1
            continue
        todo.append((key, patch))

    print(f'\n    {len(todo)} rows would gain weather · {already} already '
          f'have some · {no_wx} have none to give')
    print(f'    {no_row} nflverse games have NO matching row here '
          f'(skipped, never inserted)')
    for key, patch in todo[:8]:
        print(f'      {key[0]} {key[1]:>4} @ {key[2]:<4} <- {patch}')
    if len(todo) > 8:
        print(f'      ... +{len(todo) - 8} more')

    if not a.apply:
        print('\n[DRY] nothing written. Re-run with --apply')
        return 0

    ok = failed = 0
    for key, patch in todo:
        params = {'game_date': f'eq.{key[0]}', 'away_team': f'eq.{key[1]}',
                  'home_team': f'eq.{key[2]}'}
        p = requests.patch(f'{SB}/rest/v1/nfl_game_results', headers=H_W,
                           params=params, data=json.dumps(patch), timeout=60)
        if p.status_code not in (200, 204):
            print(f'    ! {key} {p.status_code} {p.text[:140]}')
            failed += 1
            continue
        v = requests.get(f'{SB}/rest/v1/nfl_game_results', headers=H,
                         params={**params, 'select': ','.join(WX_COLS)},
                         timeout=60)
        got = (v.json() or [{}])[0] if v.status_code == 200 else {}
        if all(got.get(c) is not None for c in patch):
            ok += 1
        else:
            print(f'    ! {key} read-back missing {patch.keys()} -> {got}')
            failed += 1
    print(f'\n  patched {ok} · failed {failed}')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
