#!/usr/bin/env python3
"""reconcile_ncaaf_duplicate_games.py - one game, one row.

== WHY ==
2026-10-22. `ncaaf_game_results.game_id` is composed from the TEAM NAMES:

    ncaaf_20260903_Akron_Wake Forest
    ncaaf_20260903_Akron_Wake Forest Demon Deacons

Those are the same game. The ids differ, so `on_conflict=game_id` cannot
collapse them, and TWO writers mint ids from two vocabularies with no
reconciliation between them:

  * ncaaf_odds_pull canonicalises through team_resolver (1,872 aliases) and
    falls back to the RAW source name inside a try/except when the resolver
    misses or throws.
  * resolve_ncaaf_results gap-fills rows using whatever game_id its SOURCE
    row (ncaaf_game_context / jerry_reads) already carries, which may have
    been built before the alias covered that school.

Measured on 2026: 21 duplicate groups, in three flavours.

    17  both twins scored     inflates a team's game count, which is the
                              denominator of every per-game rate. Virginia
                              Tech counts 10 completed games against 7
                              played, because VMI appears as both "VMI" and
                              "VMI Keydets".
     3  one twin unscored     the NULL twin reads as an ungraded game
                              forever. This is what made 8 games look
                              "unresolved" on 10-21 - they were not
                              unresolved, their scores were on the twin.
     1  HOME/AWAY INVERTED    the dangerous one.

The inverted one is Red River, a NEUTRAL-SITE game in Dallas that the two
feeds disagree about:

    ncaaf_20261010_Oklahoma_Texas    None-None   <- jerry_reads says 'Void'
    ncaaf_20261010_Texas_Oklahoma    27-24       <- played, Oklahoma by 3

and `public_receipts` carries TWO receipts on the first id with OPPOSITE
lines - 'Texas +6.5' and 'Texas -6.5' - both still ungraded.

== WHAT THIS DOES, AND WHAT IT REFUSES TO DO ==
Groups rows by (game_date, canonical team PAIR, unordered) so an inverted
home/away lands in the same group, then for each group:

  * picks a SURVIVOR - the row carrying scores; if several do, the one whose
    id matches the canonical vocabulary; ties broken by id so the choice is
    deterministic and re-runnable.
  * MERGES every non-null field from the losers into the survivor, so a
    column only one row happened to have is not lost.
  * deletes the losers.

IT REFUSES TO TOUCH A GROUP WHERE:

  * two rows carry DIFFERENT scores. That is not a duplicate, it is a
    disagreement about what happened, and picking one would be inventing a
    result.
  * the orientation is inverted AND both rows carry scores. Merging would
    have to decide which feed is right about who was home, and on a spread
    bet that decision flips the side. See
    feedback_grade_the_side_we_published - 4 of 4 UFC fades were stored
    backwards and it FLATTERED the record.
  * any surviving or deleted id is referenced by a graded receipt or read
    whose result disagrees with the survivor's score. Re-pointing a graded
    bet is exactly the kind of record change Andy has forbidden.

Those groups are PRINTED for a human. One of them is Oklahoma @ Texas.

== USAGE ==
    python reconcile_ncaaf_duplicate_games.py                 # dry run
    python reconcile_ncaaf_duplicate_games.py --season 2026
    python reconcile_ncaaf_duplicate_games.py --apply          # writes
"""
from __future__ import annotations
import argparse
import os
import sys
import unicodedata
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:                                          # noqa: BLE001
        pass

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

#: Tables whose rows point at a game_id and carry a graded outcome. A group
#: is left alone when deleting a row would orphan one of these, because a
#: receipt is a published claim and re-pointing it changes the record.
REFERRERS = (
    ('public_receipts', 'result'),
    ('jerry_reads', 'result'),
    ('ncaaf_game_context', None),
)


def _fold(s) -> str:
    n = unicodedata.normalize('NFKD', str(s or ''))
    n = ''.join(c for c in n if not unicodedata.combining(c))
    for ch in ("'", '’', '.', '-'):
        n = n.replace(ch, '')
    return ' '.join(n.lower().split())


def page(tbl, params):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H,
                         params={**params, 'limit': '1000',
                                 'offset': str(off)}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f'{tbl} HTTP {r.status_code}: {r.text[:200]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def load_canonical():
    """{folded any-spelling -> canonical} from team_resolver's alias table.

    Read straight from ncaaf_team_aliases rather than importing
    team_resolver, because that module resolves one name per call over HTTP
    and this needs the whole map. Falls back to an empty map, which only
    makes the grouping stricter - never looser.
    """
    out = {}
    try:
        rows = page('ncaaf_team_aliases',
                    {'select': 'canonical_name,full_name,location,abbrev,'
                               'alt_names'})
    except Exception as e:                                     # noqa: BLE001
        print(f'  WARNING: ncaaf_team_aliases unreadable ({e}); grouping on '
              f'folded names only, so some duplicates will be missed',
              file=sys.stderr)
        return out
    for a in rows:
        canon = a.get('canonical_name')
        if not canon:
            continue
        for k in ('canonical_name', 'full_name', 'location', 'abbrev'):
            if a.get(k):
                out.setdefault(_fold(a[k]), canon)
        for x in (a.get('alt_names') or []):
            out.setdefault(_fold(x), canon)
    return out


def key_of(name, canon_map):
    f = _fold(name)
    return _fold(canon_map.get(f, f))


def _scored(r):
    return r.get('home_score') is not None and r.get('away_score') is not None


def _referrers_for(gids):
    """{game_id: [(table, result)]} for anything pointing at these ids."""
    out = defaultdict(list)
    for tbl, result_col in REFERRERS:
        sel = 'game_id' + (f',{result_col}' if result_col else '')
        for gid in gids:
            try:
                r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H,
                                 params={'select': sel,
                                         'game_id': f'eq.{gid}',
                                         'limit': '50'}, timeout=30)
                if r.status_code != 200:
                    continue
                for row in r.json() or []:
                    out[gid].append((tbl, row.get(result_col)
                                     if result_col else None))
            except Exception:                                  # noqa: BLE001
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=2026)
    ap.add_argument('--apply', action='store_true',
                    help='actually merge and delete (default: dry run)')
    a = ap.parse_args()

    canon_map = load_canonical()
    print(f'=== reconcile_ncaaf_duplicate_games · season {a.season} '
          f'{"APPLY" if a.apply else "[DRY RUN]"} ===')
    print(f'  alias map: {len(canon_map)} name(s) -> canonical')

    rows = page('ncaaf_game_results',
                {'select': '*', 'season': f'eq.{a.season}'})
    print(f'  {len(rows)} result row(s)')

    groups = defaultdict(list)
    for r in rows:
        h, aw = r.get('home_team'), r.get('away_team')
        if not (h and aw):
            continue
        pair = tuple(sorted((key_of(h, canon_map), key_of(aw, canon_map))))
        groups[(str(r.get('game_date'))[:10], pair)].append(r)
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    print(f'  {len(dupes)} duplicate group(s)')
    if not dupes:
        print('\nnothing to reconcile.')
        return 0

    merged = held = 0
    for (gd, pair), v in sorted(dupes.items()):
        label = f'{gd}  {" / ".join(pair)}'
        scored = [r for r in v if _scored(r)]
        homes = {key_of(r.get('home_team'), canon_map) for r in v}

        # -- refuse: two different results on the same game ---------------
        outcomes = {(r.get('home_score'), r.get('away_score'))
                    for r in scored}
        if len(outcomes) > 1:
            held += 1
            print(f'\n  HELD {label}')
            print(f'     two rows carry DIFFERENT scores {sorted(outcomes)} '
                  f'- that is a disagreement about what happened, not a '
                  f'duplicate. Not choosing one.')
            for r in v:
                print(f'       {r["game_id"]}  {r.get("home_score")}-'
                      f'{r.get("away_score")}')
            continue

        # -- refuse: inverted orientation with scores on both sides -------
        if len(homes) > 1 and len(scored) > 1:
            held += 1
            print(f'\n  HELD {label}')
            print(f'     home/away INVERTED and both rows scored. Merging '
                  f'would decide which feed is right about who was home, '
                  f'and on a spread that flips the side.')
            for r in v:
                print(f'       {r["game_id"]}  {r.get("away_team")} @ '
                      f'{r.get("home_team")}  {r.get("home_score")}-'
                      f'{r.get("away_score")}')
            continue

        # -- pick the survivor --------------------------------------------
        def _rank(r):
            canon_id = key_of(r.get('home_team'), canon_map) == _fold(
                canon_map.get(_fold(r.get('home_team')), '')) or False
            return (0 if _scored(r) else 1, 0 if canon_id else 1,
                    str(r.get('game_id')))
        ordered = sorted(v, key=_rank)
        survivor, losers = ordered[0], ordered[1:]

        # -- refuse: a loser is referenced by a GRADED receipt/read -------
        refs = _referrers_for([r['game_id'] for r in losers])
        graded = {gid: [t for t, res in hits
                        if res not in (None, '', 'NO_ACTION', 'Pending')]
                  for gid, hits in refs.items()}
        graded = {g: t for g, t in graded.items() if t}
        if graded:
            held += 1
            print(f'\n  HELD {label}')
            print(f'     a row due for deletion is referenced by a GRADED '
                  f'receipt/read; deleting it would orphan a published '
                  f'claim: {dict(graded)}')
            continue

        # -- merge non-null fields, then delete ---------------------------
        patch = {}
        for r in losers:
            for k, val in r.items():
                if k in ('game_id',):
                    continue
                if val is not None and survivor.get(k) is None:
                    patch[k] = val
        print(f'\n  MERGE {label}')
        print(f'     keep   {survivor["game_id"]}  '
              f'{survivor.get("home_score")}-{survivor.get("away_score")}')
        for r in losers:
            print(f'     delete {r["game_id"]}  {r.get("home_score")}-'
                  f'{r.get("away_score")}'
              + (f'  (refs: {[t for t, _ in refs.get(r["game_id"], [])]})'
                 if refs.get(r['game_id']) else ''))
        if patch:
            print(f'     carry over {sorted(patch)}')
        if not a.apply:
            merged += 1
            continue

        ok = True
        if patch:
            pr = requests.patch(
                f'{SB}/rest/v1/ncaaf_game_results',
                headers=H_W, params={'game_id': f'eq.{survivor["game_id"]}'},
                json=patch, timeout=30)
            if pr.status_code not in (200, 204):
                print(f'     PATCH failed {pr.status_code}: {pr.text[:120]}')
                ok = False
        if ok:
            for r in losers:
                dr = requests.delete(
                    f'{SB}/rest/v1/ncaaf_game_results', headers=H_W,
                    params={'game_id': f'eq.{r["game_id"]}'}, timeout=30)
                if dr.status_code not in (200, 204):
                    print(f'     DELETE {r["game_id"]} failed '
                          f'{dr.status_code}: {dr.text[:120]}')
                    ok = False
        if ok:
            merged += 1

    print(f'\n{"merged" if a.apply else "would merge"} {merged} group(s); '
          f'{held} held for a human')
    if held:
        print('  A HELD group is not a bug in this script - it is a question '
              'only Andy can answer. Nothing was changed for those.')
    if not a.apply and merged:
        print('  Re-run with --apply to write. This DELETES rows.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
