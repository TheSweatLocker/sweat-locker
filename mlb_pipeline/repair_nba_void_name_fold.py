#!/usr/bin/env python3
"""ONE-TIME REPAIR — NBA props Voided by an asymmetric name key.

== WHY (2026-11-15) =========================================================
`nba_data_client.get_player_boxscores` keys its map with
`displayName.lower()`; `grade_props.grade_prop` looked it up with
`_norm_name()`, which ALSO strips periods, apostrophes and accents. Every
NBA player carrying one therefore missed the boxscore entirely:

    props spelling 'Kelly Oubre Jr.'  -> _norm_name -> 'kelly oubre jr'
    boxscore key                                       'kelly oubre jr.'

The miss was not a skip. It fell into grade_date's "player not found in a
FINAL boxscore" branch, which writes result='Void' -- a returned stake. So a
player who played 23 minutes and took 3 rebounds against a 3.5 line had both
props booked as no-action.

MEASURED, full table, row counts asserted against count=exact:

    population                          n      Win   Loss   Void   Void%
    name altered by _norm_name       1,976      343    352  1,202   63.4%
    name unchanged                  31,342   13,543 14,026  2,429    8.1%

    Void props whose player DID play (unique nba_player_game_logs row,
    minutes > 0): 1,791 -- 1,128 of them name-altered.

A 63.4% Void rate against 8.1% is the signature. The top names are exactly
the predicted shape: Jabari Smith Jr, Jaime Jaquez Jr, Wendell Carter Jr,
Michael Porter Jr, De-Anthony Melton, P.J. Washington, Ja-Kobe Walter,
De-Aaron Fox.

Because Void is excluded from every hit rate on purpose, this never showed up
as a wrong record -- it silently REMOVED ~1.8k real outcomes from every NBA
measurement, including the prop cell-gate counterfactual measured on
2026-11-14, whose steals_under cell carried a 13% void share.

== WHAT THIS DOES ===========================================================
The fix itself is in grade_props.fetch_nba_player_stats_for_date, which now
folds the boxscore keys with _norm_name and marks genuine collisions
ambiguous rather than guessing. This script re-grades the historical rows
through THAT path -- it imports the production
`fetch_nba_player_stats_for_date` and `grade_prop` rather than
reimplementing them, so a disagreement between repair and pipeline is
impossible by construction.

It writes ONLY rows whose result actually changes, batched by
(result, final_value) through an `id=in.(...)` filter chunked to stay well
under the URL length at which such a filter silently truncates. A row that is
still correctly Void is left untouched, resolved_at included.

    python repair_nba_void_name_fold.py            # dry run, prints the diff
    python repair_nba_void_name_fold.py --apply
"""
from __future__ import annotations

import argparse
import collections
import sys
import time
from datetime import datetime, timezone

import requests

import grade_props as G

SB, H_READ = G.SB, G.H_READ
H_WRITE = {**G.H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}
TABLE = 'nba_pipeline_props'
SELECT = ('id,game_date,player_name,prop_type,prop_line,direction,tier,'
          'conviction,result,final_value,matchup')
# 60 ids at ~7 chars is ~450 chars of filter; the documented silent-truncation
# cliff for an in.(...) filter is around 2KB of URL.
CHUNK = 60


def _page(params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = None
        for attempt in range(5):
            r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H_READ,
                             params=q, timeout=120)
            if r.status_code in (200, 206):
                break
            time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError('read failed at offset %d: %s %s'
                               % (off, r.status_code, r.text[:200]))
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _dates() -> list:
    rows = _page({'select': 'game_date', 'order': 'game_date.asc'})
    return sorted({str(r['game_date'])[:10] for r in rows})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--date', default=None, help='one date only')
    a = ap.parse_args()

    dates = [a.date] if a.date else _dates()
    print('=' * 74)
    print('REPAIR NBA VOID NAME FOLD  (%s)  dates=%d'
          % ('APPLY' if a.apply else 'DRY RUN', len(dates)))
    print('=' * 74)

    changes: dict = collections.defaultdict(list)
    moved: collections.Counter = collections.Counter()
    flips: list = []
    n_rows = n_amb = 0
    for d in dates:
        rows = _page({'select': SELECT, 'game_date': 'eq.%s' % d,
                      'order': 'id.asc'})
        if not rows:
            continue
        stats_map, n_games = G.fetch_nba_player_stats_for_date(d)
        if not stats_map:
            print('  %s  no boxscore stats (%d finals) -- skipped, nothing '
                  'regraded' % (d, n_games))
            continue
        hit = 0
        for p in rows:
            n_rows += 1
            p['_sport'] = 'NBA'
            st = stats_map.get(G._norm_name(p.get('player_name')))
            if isinstance(st, dict) and st.get('_ambiguous'):
                n_amb += 1
                continue
            res, actual = G.grade_prop(p, stats_map)
            if res is None:
                continue
            old = str(p.get('result') or '') or None
            if old == res and p.get('final_value') == actual:
                continue
            # ══ SCOPE: Void and NULL ONLY ═══════════════════════════════════
            # The first dry run also wanted to flip 24 settled Win/Loss grades
            # -- 12 complementary over/under pairs -- and that is a different
            # question wearing this one's clothes. On 10 of those 12 pairs the
            # ESPN boxscore and nba_player_game_logs disagree by EXACTLY ONE
            # on the stat the prop is about (Durant pra 32 vs 33, Gobert
            # steals 0 vs 1, Poeltl reb 7 vs 8). The existing grades came from
            # the log-based grader run by hand; this path reads ESPN. Picking
            # ESPN here would silently overwrite 24 real outcomes on the
            # strength of which source happens to be wired into CI, and that
            # is Andy's call on a source of truth, not a repair's.
            #
            # So: fix what was never graded or was wrongly voided, report the
            # rest, change nothing that already carries a Win or a Loss. The
            # 138 rows whose result is unchanged and only final_value differs
            # are skipped for the same reason -- same disagreement, no gain.
            if old not in (None, 'Void'):
                moved['SKIPPED %s -> %s (not written)' % (old, res)] += 1
                flips.append((p, old, res, actual))
                continue
            changes[(res, actual)].append(p['id'])
            moved['%s -> %s' % (old, res)] += 1
            hit += 1
        print('  %s  rows=%5d  games=%2d  regrade=%d'
              % (d, len(rows), n_games, hit))

    print()
    print('rows examined %d   ambiguous-name rows left alone %d'
          % (n_rows, n_amb))
    print('transitions:')
    for k, v in moved.most_common():
        print('   %-22s %6d' % (k, v))
    real = [f for f in flips if f[1] != f[2]]
    fval = [f for f in flips if f[1] == f[2]]
    if fval:
        print()
        print('%d row(s) left alone: result unchanged, only final_value was '
              'unset or differs. No grade is affected.' % len(fval))
    if real:
        print()
        print('%d settled grade(s) LEFT ALONE -- the stored Win/Loss and the '
              'ESPN boxscore DISAGREE.' % len(real))
        print('These are %d complementary over/under pair(s). The stored '
              'grades came from the log-based grader; this path reads ESPN. '
              'Andy decides which source wins -- nothing here overwrites '
              'a real outcome.' % (len(real) // 2))
        for p, old, res, actual in real:
            print('   id=%-7s %s %-20s %-14s %-5s %-5s stored=%-4s '
                  'espn=%-4s (espn value %s)'
                  % (p['id'], p['game_date'], str(p['player_name'])[:20],
                     p['prop_type'], p['prop_line'], p['direction'],
                     old, res, actual))
    total = sum(len(v) for v in changes.values())
    print()
    print('rows to write: %d' % total)
    if not a.apply or not total:
        print('(dry run -- nothing written)' if not a.apply
              else '(nothing to do)')
        return 0

    written = failed = 0
    for (res, actual), ids in changes.items():
        body = {'result': res, 'final_value': actual,
                'resolved_at': datetime.now(timezone.utc).isoformat()}
        for i in range(0, len(ids), CHUNK):
            part = ids[i:i + CHUNK]
            url = ('%s/rest/v1/%s?id=in.(%s)'
                   % (SB, TABLE, ','.join(str(x) for x in part)))
            ok, r = False, None
            for attempt in range(5):
                r = requests.patch(url, headers=H_WRITE, json=body, timeout=120)
                if r.status_code in (200, 204):
                    ok = True
                    break
                time.sleep(2 * (attempt + 1))
            if not ok:
                print('  FAIL %s chunk %d: %s %s'
                      % (res, i, r.status_code, r.text[:200]))
                failed += len(part)
                continue
            # Prefer: return=representation -- confirm the server echoed every
            # row it claims to have written. A 204 is not a write.
            try:
                got = r.json()
            except ValueError:
                got = []
            if len(got) != len(part):
                print('  WARN %s chunk %d: asked %d, server returned %d'
                      % (res, i, len(part), len(got)))
                failed += len(part) - len(got)
            written += len(got)
    print('written %d   failed %d' % (written, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
