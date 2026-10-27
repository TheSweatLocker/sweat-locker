#!/usr/bin/env python3
"""Fill season_week on signal_attribution so the weekly cadence can run.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
Andy's standard (feedback_engine_selection_standard): every signal that
feeds a pick must be recorded and **assessed week to week** to see what
drives picks and what drives favourable outcomes.

That review could not run on most of the data: `season_week` was NULL on
**1,246 of 2,165** signal_attribution rows (58%).

TWO DIFFERENT CAUSES. Treating them as one would have been wrong:

  * NCAAF is a plain bug — 908 NULL rows. `ncaaf_game_context` carries
    `week` on 650 of 660 rows but `season_week` on only 256, and
    signal_attribution_snapshot.py read the emptier column (it already
    SELECTed `week` and never used it). Fixed at the writer in the same
    commit; this backfills the rows already written.

  * MLB has 338 NULL rows and **baseball has no league weeks** —
    `mlb_game_context` has no week column at all, and nor do NHL or NBA.
    A NULL there is HONEST DATA, not a defect. Inventing a league week
    would be fabrication. Those rows get the ISO week of `game_date`,
    which is a grouping key rather than a claim about the schedule.

So: prefer the sport's own league week where one exists, else derive the
ISO week from the date. Every row ends up groupable, nothing is invented.

SAFETY
  * `season_week` only. No other column is touched, and rows that already
    have a week are never overwritten.
  * the source week is read from that sport's own context table, matched on
    game_id — never guessed from a neighbouring row.
  * dry-run by default; every write is read back and the remaining-NULL
    count is asserted to have moved.

    python backfill_attribution_week.py
    python backfill_attribution_week.py --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CTX_BY_SPORT = {
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NHL': 'nhl_game_context',
    'NBA': 'nba_game_context',
    'MLB': 'mlb_game_context',
}
# sports whose schedule has no league week — ISO week is the honest key
NO_LEAGUE_WEEK = {'MLB', 'NHL', 'NBA', 'NCAAB'}


def _load_env(path):
    out = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_HERE = os.path.dirname(os.path.abspath(__file__))
_E = _load_env(os.path.join(_HERE, '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = (_E.get('SUPABASE_SERVICE_ROLE_KEY') or _E.get('SUPABASE_KEY')
       or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
       'Content-Type': 'application/json'}


def _req(method, path, params=None, body=None, extra=None):
    u = BASE + path
    if params:
        u += '?' + urllib.parse.urlencode(params, safe='.,()*:')
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(u, data=data, method=method)
    for k, v in HDR.items():
        r.add_header(k, v)
    for k, v in (extra or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return (json.loads(raw) if raw.strip() else None), resp.status
    except urllib.error.HTTPError as e:
        print('    HTTP %s on %s :: %s'
              % (e.code, path, e.read().decode('utf-8', 'replace')[:220]))
        return None, e.code


def fetch_all(table, select='*', **filt):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off)}
        p.update(filt)
        rows, st = _req('GET', table, p)
        if not isinstance(rows, list):
            print('    read failed on %s (status %s)' % (table, st))
            break
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def iso_week(d):
    try:
        if isinstance(d, str):
            d = dt.date.fromisoformat(str(d)[:10])
        if isinstance(d, dt.datetime):
            d = d.date()
        return int(d.isocalendar()[1])
    except (TypeError, ValueError, AttributeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('=' * 86)
    print('BACKFILL signal_attribution.season_week  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 86)

    rows = fetch_all('signal_attribution')
    print('  attribution rows: %d' % len(rows))
    nulls = [r for r in rows if r.get('season_week') is None]
    print('  season_week NULL: %d (%.0f%%)'
          % (len(nulls), 100.0 * len(nulls) / len(rows) if rows else 0))
    if not nulls:
        print('  nothing to fill.')
        return 0
    bysp = collections.Counter(str(r.get('sport')) for r in nulls)
    print('  NULL by sport: %s' % dict(bysp.most_common()))

    # league week per (sport, game_id), from that sport's own context table
    league = {}
    for sp in sorted({str(r.get('sport')) for r in nulls}):
        tbl = CTX_BY_SPORT.get(sp)
        if not tbl or sp in NO_LEAGUE_WEEK:
            print('  %-6s no league week exists -> ISO week of game_date' % sp)
            continue
        ctx = fetch_all(tbl)
        got = 0
        for c in ctx:
            gid = str(c.get('game_id') or '')
            if not gid:
                continue
            for key in ('season_week', 'week'):
                v = c.get(key)
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    continue
                if iv > 0:
                    league[(sp, gid)] = iv
                    got += 1
                    break
        print('  %-6s %-22s league weeks available for %d game(s)'
              % (sp, tbl, got))

    # ══ 2026-10-27 · NEVER MIX TWO WEEK SCALES IN ONE COLUMN ═════════════
    # First pass of this script would have given 23 NCAAF rows the ISO week
    # of their date (e.g. 44) while the other 885 got league weeks (1-15).
    # Grouping NCAAF by week would then be meaningless. For a sport that HAS
    # league weeks, a missing one is derived from that sport's OWN date->week
    # map, built from the games that do have one. ISO week is used only for
    # sports with no league week at all, where it is the only honest key.
    date_week = collections.defaultdict(collections.Counter)
    for sp in sorted({str(r.get('sport')) for r in nulls}):
        if sp in NO_LEAGUE_WEEK:
            continue
        tbl = CTX_BY_SPORT.get(sp)
        if not tbl:
            continue
        for c in fetch_all(tbl, select='game_id,game_date,week,season_week'):
            d = str(c.get('game_date') or '')[:10]
            if not d:
                continue
            for key in ('season_week', 'week'):
                try:
                    iv = int(c.get(key))
                except (TypeError, ValueError):
                    continue
                if iv > 0:
                    date_week[sp][(d, iv)] += 1
                    break
    # collapse to one week per (sport, date): the most common
    dmap = collections.defaultdict(dict)
    for sp, cnt in date_week.items():
        per = collections.defaultdict(collections.Counter)
        for (d, wk), n in cnt.items():
            per[d][wk] += n
        for d, wks in per.items():
            dmap[sp][d] = wks.most_common(1)[0][0]
    for sp in sorted(dmap):
        print('  %-6s date->league-week map built for %d date(s)'
              % (sp, len(dmap[sp])))

    plan = []
    src = collections.Counter()
    unresolved = []
    for r in nulls:
        sp = str(r.get('sport'))
        gid = str(r.get('game_id') or '')
        gd = str(r.get('game_date') or '')[:10]
        wk = league.get((sp, gid))
        how = 'league week'
        if wk is None and sp not in NO_LEAGUE_WEEK:
            wk = dmap.get(sp, {}).get(gd)
            how = 'league week via date map'
        if wk is None and sp in NO_LEAGUE_WEEK:
            wk = iso_week(gd)
            how = 'ISO week (sport has no league week)'
        if wk is None:
            unresolved.append(r)
            continue
        plan.append((r, wk, how))
        src['%s / %s' % (sp, how)] += 1

    print('')
    print('  resolvable: %d   UNRESOLVABLE: %d' % (len(plan), len(unresolved)))
    for k, n in sorted(src.items()):
        print('     %-34s %d' % (k, n))
    if unresolved:
        print('  rows with neither a league week nor a usable game_date:')
        for r in unresolved[:6]:
            print('     id=%s sport=%s game_id=%s game_date=%r'
                  % (r.get('id'), r.get('sport'), r.get('game_id'),
                     r.get('game_date')))
        print('     >>> these are left NULL rather than guessed. If this'
              ' count is not 0, it is a real gap worth raising.')

    print('')
    print('  sample of the plan:')
    for r, wk, how in plan[:8]:
        print('     %-6s %s  id=%-8s -> week %-3d (%s)'
              % (r.get('sport'), str(r.get('game_date'))[:10], r.get('id'),
                 wk, how))

    if not a.apply:
        print('')
        print('  DRY RUN — nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    for r, wk, _how in plan:
        _x, st = _req('PATCH', 'signal_attribution',
                      {'id': 'eq.%s' % r.get('id')},
                      {'season_week': wk}, {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
    print('')
    print('  wrote %d, failed %d' % (ok, fail))

    after = fetch_all('signal_attribution')
    left = sum(1 for r in after if r.get('season_week') is None)
    print('  season_week NULL now: %d (was %d)' % (left, len(nulls)))
    bysp2 = collections.Counter(str(r.get('sport')) for r in after
                                if r.get('season_week') is None)
    if bysp2:
        print('  still NULL by sport: %s' % dict(bysp2.most_common()))
    if ok and left >= len(nulls):
        print('  ABORT-LEVEL: writes reported success but nothing moved.')
        return 1
    if left != len(unresolved):
        print('  NOTE: %d rows remain NULL but %d were expected unresolvable'
              ' — worth a look.' % (left, len(unresolved)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
