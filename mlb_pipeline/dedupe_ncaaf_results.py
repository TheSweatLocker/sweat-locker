#!/usr/bin/env python3
"""Collapse duplicate ncaaf_game_results rows created by the ET/UTC date split.

Andy 2026-09-30: "Need to get NCAAF grading handled."

THE DEFECT
ncaaf_game_results.game_id embeds a DATE — 'ncaaf_YYYYMMDD_Away_Home'. Different
ingest paths disagree about which date that is, because CFBD's startDate is UTC
while our game_date is ET, and any kickoff at 7pm ET or later is already past
midnight UTC. So one game ends up as two or three rows:

    ncaaf_20260918_Houston_Texas Tech   game_date 09-18   UNSCORED  <- canonical
    ncaaf_20260919_Houston_Texas Tech   game_date 09-18   26-28     <- has score
    ncaaf_20260920_Houston_Texas Tech   game_date 09-19   UNSCORED  <- phantom

Measured 2026-09-30: 15 duplicate (game_date, away, home) groups covering 30
rows, and 31 games showing as unscored across 09-12/18/19/25/26.

WHY IT MATTERS
The score lands on whichever row the resolver matched, and the row the rest of
the system joins to is the CANONICAL one — the one whose embedded date equals its
own game_date, because that is what ncaaf_game_context.results_game_id generates
(migration 20260929a). So a scored duplicate leaves the canonical row unscored,
the pick ungraded, and NCAAF's record computed on an incomplete sample. On 09-26
that included SMU -32.5 at conviction 91, our highest NCAAF call of the week.

WHAT THIS DOES
For each duplicate group, only when it is UNAMBIGUOUS:
  * exactly one canonical row (embedded date == game_date), and
  * exactly one row carrying scores
then copy the scores (and spread/total results) onto the canonical row and delete
the others. Anything ambiguous — no scores anywhere, two scored rows, no
canonical row — is REPORTED AND LEFT ALONE. A wrong merge on a results table is
far worse than a duplicate.

Deleting the non-canonical rows cannot change any published record: they are the
unscored copies, and every record path requires a score. The scored copy's
numbers are preserved by being moved, not dropped.

THIS IS A REPAIR, NOT THE ROOT FIX. resolve_ncaaf_results now matches CFBD across
the date boundary (it emits key variants for the CFBD date AND the prior day), so
scores stop going missing. But the INGEST side can still create rows under two
date spellings, so duplicates may recur until the writer agrees on ET. Safe to
re-run.

USAGE
    python dedupe_ncaaf_results.py --dry-run
    python dedupe_ncaaf_results.py --season 2026
"""
import argparse
import collections
import os
import re
import sys

import requests

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not SB or not KEY:
    print('dedupe_ncaaf_results: no Supabase credentials — skipping')
    sys.exit(0)
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json'}

ID_DATE = re.compile(r'^ncaaf_(\d{8})_')
CARRY = ('home_score', 'away_score', 'spread_result', 'total_result')


def _page(tbl, select, extra=None):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off)}
        if extra:
            p.update(extra)
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, params=p, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:200]}')
        body = r.json()
        if not isinstance(body, list) or not body:
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _embedded_date(game_id):
    m = ID_DATE.match(str(game_id or ''))
    return m.group(1) if m else None


def _fold(name):
    """Normalise a team name for grouping. Mirrors resolve_ncaaf_results._fold_name
    closely enough for duplicate detection, without needing the alias table."""
    import unicodedata as _u
    n = _u.normalize('NFKD', str(name or ''))
    n = ''.join(c for c in n if not _u.combining(c))
    n = n.lower().replace("'", '').replace('-', ' ').replace('&', ' and ')
    for _m in ('golden lions', 'golden eagles', 'golden flashes', 'fighting irish',
               'crimson tide', 'tar heels', 'blue devils', 'red raiders',
               'green wave', 'demon deacons', 'hurricanes', 'wolverines',
               'buckeyes', 'cornhuskers', 'mountaineers', 'volunteers',
               'commodores', 'razorbacks', 'gamecocks', 'seminoles', 'hokies',
               'cavaliers', 'terrapins', 'scarlet knights', 'nittany lions',
               'boilermakers', 'wildcats', 'badgers', 'gophers', 'hawkeyes',
               'cyclones', 'jayhawks', 'sooners', 'longhorns', 'aggies',
               'bulldogs', 'tigers', 'braves', 'hornets', 'bison', 'rams',
               'eagles', 'lions', 'bears', 'panthers', 'spartans', 'knights',
               'owls', 'pirates', 'cougars', 'huskies', 'ducks', 'beavers',
               'trojans', 'bruins', 'utes', 'buffaloes', 'cardinals',
               'mustangs', 'broncos', 'rebels', 'wolf pack', 'aztecs',
               'warriors', 'vandals', 'falcons', 'midshipmen', 'black knights'):
        if n.endswith(' ' + _m):
            n = n[: -(len(_m) + 1)]
            break
    # UMass / Massachusetts and friends: collapse the handful of spellings our
    # ingest has used against CFBD's canonical school name.
    _ALIAS = {'umass': 'massachusetts', 'uconn': 'connecticut',
              'app state': 'appalachian state', 'southern mississippi': 'southern miss',
              'ul monroe': 'louisiana monroe', 'fiu': 'florida international',
              'miami fl': 'miami', 'miami oh': 'miami ohio'}
    n = _ALIAS.get(n.strip(), n)
    return ''.join(ch for ch in n if ch.isalnum())


def _group_key(row):
    """2026-10-03 · GROUP ACROSS BOTH AXES THAT CREATE DUPLICATES.

    This grouped on (game_date, away_team, home_team) verbatim, which misses
    every duplicate that differs on EITHER axis -- and both happen:

      date      ncaaf_20260911_Mercyhurst_New Mexico   09-11  UNSCORED
                ncaaf_20260912_Mercyhurst_New Mexico   09-12  7-70
      spelling  ncaaf_20260903_UMass_Rutgers           09-03  UNSCORED
                ncaaf_20260903_Massachusetts_Rutgers   09-03  37-21

    Measured: 12 rows looked like missing finals and every one had a scored
    sibling -- no score was ever actually absent. They were phantoms, and the
    old key could not see them (4 groups found, 12 rows stranded).

    Keyed on folded names + an unordered pair so an orientation flip groups
    too, and the DATE IS DELIBERATELY EXCLUDED from the key -- callers bucket
    by ISO week instead, so a +/-1 day split still collapses while two real
    meetings in a season (a rematch weeks apart) stay separate.
    """
    import datetime as _dt
    d = str(row.get('game_date'))[:10]
    try:
        iso = _dt.date.fromisoformat(d).isocalendar()
        wk = (iso[0], iso[1])
    except Exception:
        wk = (d,)
    return (wk, frozenset((_fold(row.get('away_team')), _fold(row.get('home_team')))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    cols = 'game_id,game_date,away_team,home_team,' + ','.join(CARRY)
    rows = _page('ncaaf_game_results', cols, {'season': f'eq.{a.season}'})
    groups = collections.defaultdict(list)
    for x in rows:
        groups[_group_key(x)].append(x)
    dupes = {k: v for k, v in groups.items() if len(v) > 1}

    print(f'=== dedupe_ncaaf_results · season {a.season} ===')
    print(f'  rows {len(rows)} · duplicate groups {len(dupes)}')

    moved = deleted = skipped = 0
    for _gk, v in sorted(dupes.items(), key=lambda kv: str(kv[0])):
        # Representative identity for logging + canonical detection comes from
        # the SCORED row where there is one, else the first.
        _rep = next((x for x in v if x.get('home_score') is not None), v[0])
        d = str(_rep['game_date'])[:10]
        away, home = _rep['away_team'], _rep['home_team']
        ymd = d.replace('-', '')
        canon = [x for x in v if _embedded_date(x['game_id']) == ymd]
        scored = [x for x in v if x.get('home_score') is not None]
        label = f'{d} {away[:22]:22s} @ {home[:18]:18s}'

        if len(canon) != 1 or len(scored) != 1:
            skipped += 1
            why = ('no score anywhere' if not scored
                   else f'{len(canon)} canonical / {len(scored)} scored')
            print(f'  SKIP  {label}  ({why})')
            continue

        keep, src = canon[0], scored[0]
        others = [x for x in v if x['game_id'] != keep['game_id']]

        if keep['game_id'] == src['game_id']:
            print(f'  DEDUP {label}  canonical already scored, drop {len(others)}')
        else:
            print(f'  MOVE  {label}  {src["away_score"]}-{src["home_score"]} '
                  f'-> canonical, drop {len(others)}')
            if not a.dry_run:
                payload = {c: src.get(c) for c in CARRY}
                r = requests.patch(
                    f'{SB}/rest/v1/ncaaf_game_results',
                    headers=H_W, params={'game_id': f'eq.{keep["game_id"]}'},
                    json=payload, timeout=30)
                if r.status_code not in (200, 204):
                    print(f'        ! patch failed {r.status_code} {r.text[:120]}')
                    continue
                # Read back — a 204 on PostgREST is not proof the row changed.
                chk = requests.get(f'{SB}/rest/v1/ncaaf_game_results', headers=H,
                                   params={'select': 'home_score',
                                           'game_id': f'eq.{keep["game_id"]}'},
                                   timeout=20).json()
                if not (isinstance(chk, list) and chk
                        and chk[0].get('home_score') is not None):
                    print('        ! score did not persist — NOT deleting dupes')
                    continue
            moved += 1

        if not a.dry_run:
            for o in others:
                dr = requests.delete(f'{SB}/rest/v1/ncaaf_game_results',
                                     headers=H_W,
                                     params={'game_id': f'eq.{o["game_id"]}'},
                                     timeout=30)
                if dr.status_code in (200, 204):
                    deleted += 1
                else:
                    print(f'        ! delete failed {dr.status_code}')
        else:
            deleted += len(others)

    print(f'\n  scores moved: {moved} · rows {"would be " if a.dry_run else ""}'
          f'deleted: {deleted} · groups skipped: {skipped}')
    if a.dry_run:
        print('  (dry run — nothing written)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
