"""Stamp every NBA result row with its season phase, from ESPN.

Team records on every NBA game card count preseason exhibitions, the
All-Star Game and the playoffs. Measured on team_situational_records
(2025-26, ml, overall): ALL 30 of 30 teams exceed 82 games, range 84..110,
and home/road both exceed a real 41 on all 30. nba_game_results contains
THIRTY-SEVEN distinct teams - Guangzhou Loong-Lions, Melbourne United,
Hapoel Jerusalem, Team Stars/Stripes/World - and games dated to 2026-06-13.

Nothing in the data said which phase a game belonged to, so the matview
(20260901e) counted all of it. This fills that in.

╔══ WHY THIS JOIN IS SAFE ══════════════════════════════════════════════════╗
nba_game_results.game_id IS the ESPN event id ('401909101'). So this matches
ESPN's own season.type on the primary key - no team names, no dates, no
fuzzy matching. That is the entire reason NBA is being done first and alone:
ncaab_game_results.game_id is 'ncaab_20260407_Connecticut_Michigan' and
mlb_game_results.game_id is a hash, so neither can be joined to ESPN without
matching NAMES across two feeds, which is precisely the defect that cost STL
all its odds and all its tendencies for weeks
(project_nhl_stl_name_mismatch_1013).

Any row whose game_id ESPN does not return is left NULL and REPORTED. It is
never guessed from the date, because the phases overlap at their edges: an
April date holds both week-82 regular-season games and first-round playoff
games.
╚═══════════════════════════════════════════════════════════════════════════╝

ESPN's scoreboard gives season.type per event, verified against three known
dates before this was written:

    2025-10-06  type 1   preseason
    2026-01-15  type 2   regular
    2026-05-25  type 3   postseason

Date ranges are not supported (dates=YYYYMMDD-YYYYMMDD returns 400), so this
walks one date at a time over the distinct dates actually present in the
table, which is far fewer requests than one per game.

CLI
    python backfill_nba_season_type.py --dry-run
    python backfill_nba_season_type.py
    python backfill_nba_season_type.py --season 2025-26
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
import urllib.request

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                          # noqa: BLE001
        pass

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

ESPN = ('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/'
        'scoreboard')

#: ESPN season.type -> our label. 4 is the All-Star event on ESPN's scale;
#: it is stamped explicitly rather than lumped into preseason, because
#: "preseason" is a defensible thing to show a user and "the All-Star Game"
#: is never part of a team record at all.
TYPE_MAP = {1: 'preseason', 2: 'regular', 3: 'postseason', 4: 'allstar'}


def page(tbl, params, cap=200000):
    out, off = [], 0
    while off < cap:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, params=q,
                         timeout=180)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} {r.status_code} {r.text[:200]}')
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def espn_types_for_date(d: str, tries: int = 3):
    """{event_id: season_type_int} for one YYYY-MM-DD. None on failure.

    None and {} mean different things and the caller must not conflate them:
    {} is "ESPN has no games that day", None is "we could not ask". Treating
    a failed request as an empty day would silently leave rows NULL and call
    it done.
    """
    q = d.replace('-', '')
    for i in range(tries):
        try:
            req = urllib.request.Request(f'{ESPN}?dates={q}',
                                         headers={'User-Agent': 'Mozilla/5.0'})
            data = json.load(urllib.request.urlopen(req, timeout=45))
            out = {}
            for ev in data.get('events', []):
                t = (ev.get('season') or {}).get('type')
                if ev.get('id') is not None and t is not None:
                    out[str(ev['id'])] = int(t)
            return out
        except Exception:                                      # noqa: BLE001
            if i == tries - 1:
                return None
            time.sleep(1.5 * (i + 1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', dest='dry')
    ap.add_argument('--season', help='only this season, e.g. 2025-26')
    ap.add_argument('--only-null', action='store_true',
                    help='skip dates where every row already has a phase')
    a = ap.parse_args()

    params = {'select': '*'}
    if a.season:
        params['season'] = f'eq.{a.season}'
    rows = page('nba_game_results', params)
    if not rows:
        print('no nba_game_results rows')
        return 1
    if 'season_type' not in rows[0]:
        print('! nba_game_results has no season_type column - apply '
              'migration 20261022d first')
        return 1

    by_date = collections.defaultdict(list)
    for r in rows:
        d = str(r.get('game_date'))[:10]
        if d and d != 'None':
            by_date[d].append(r)
    dates = sorted(by_date)
    if a.only_null:
        dates = [d for d in dates
                 if any(r.get('season_type') is None for r in by_date[d])]
    print(f'=== backfill_nba_season_type · {len(rows)} rows over '
          f'{len(by_date)} dates · walking {len(dates)} ===')

    updates, unmatched, failed_dates = [], [], []
    for i, d in enumerate(dates, 1):
        types = espn_types_for_date(d)
        if types is None:
            failed_dates.append(d)
            continue
        for r in by_date[d]:
            t = types.get(str(r.get('game_id')))
            if t is None:
                unmatched.append((d, r.get('game_id'),
                                  r.get('away_team'), r.get('home_team')))
                continue
            label = TYPE_MAP.get(t, f'espn_type_{t}')
            if r.get('season_type') != label:
                updates.append((r.get('game_id'), label))
        if i % 50 == 0:
            print(f'    {i}/{len(dates)} dates · {len(updates)} to write')
        time.sleep(0.12)

    counts = collections.Counter(lbl for _, lbl in updates)
    print(f'\n  to write {len(updates)} · unmatched {len(unmatched)} · '
          f'ESPN unreachable on {len(failed_dates)} date(s)')
    print(f'  phase mix of the writes: {dict(counts)}')
    for d, gid, aw, hm in unmatched[:10]:
        print(f'     unmatched {d} {gid} {aw} @ {hm}')
    if failed_dates:
        print(f'     FAILED DATES (left NULL, re-run): {failed_dates[:10]}')

    if a.dry:
        print('\n  --dry-run: nothing written')
        return 0

    wrote = 0
    for gid, label in updates:
        r = requests.patch(f'{SB}/rest/v1/nba_game_results', headers=H_W,
                           params={'game_id': f'eq.{gid}'},
                           json={'season_type': label}, timeout=45)
        if r.status_code in (200, 204):
            wrote += 1
        else:
            print(f'     ! {gid} {r.status_code} {r.text[:120]}')
    print(f'\n  patched {wrote}/{len(updates)}')

    # A 204 is not a write. Read the table back and prove the shape changed.
    back = page('nba_game_results', {'select': '*'})
    mix = collections.Counter(str(x.get('season_type')) for x in back)
    print(f'  read-back phase mix over {len(back)} rows: {dict(mix)}')

    print('\n  per-season regular-season game counts (the number that was '
          'wrong):')
    per = collections.defaultdict(collections.Counter)
    for x in back:
        if x.get('season_type') != 'regular':
            continue
        for side in ('home_team', 'away_team'):
            if x.get(side):
                per[str(x.get('season'))][x[side]] += 1
    for seas in sorted(per):
        c = per[seas]
        vals = sorted(c.values())
        over = sum(1 for v in vals if v > 82)
        print(f'     {seas}: {len(c)} teams · {vals[0]}..{vals[-1]} games · '
              f'{over} over 82' + ('  <- still wrong' if over else '  OK'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
