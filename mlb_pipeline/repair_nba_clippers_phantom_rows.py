#!/usr/bin/env python3
"""Merge the Clippers phantom result rows into their real ones, then drop them.

== WHY (2026-10-28) =========================================================
The Odds API says "Los Angeles Clippers"; nba_game_context, the ESPN schedule,
nba_elo and compute_margin_strength all say "LA Clippers". odds_pull_core's
_canonical_id normalises by stripping non-alphanumerics, so laclippers never
matched losangelesclippers, the league-id lookup missed, and the fallback
minted a composite id. Result: TWO rows per Clippers game, and the data split
clean down the middle --

  2026-10-21  401909842                            score 96-121, NO line
  2026-10-21  nba_20261021_Sacramento_Kings_...    NO score, close_spread -5.0

-- so spread_result and total_result are NULL on BOTH and the game cannot be
graded on any market. Three played games are in that state (10-21, 10-23,
10-25) plus two future ones (10-28, 10-30).

The source is fixed in odds_pull_core (name_aliases) + nba_odds_pull, and the
minted-id fallback now prints a warning instead of writing silently. This
repairs the rows already stored.

WHAT IT DOES
  * for a phantom whose real (numeric-id) sibling exists on the same date and
    team pair: copy ONLY the line columns that are NULL on the real row, then
    delete the phantom.
  * for a phantom with no sibling (a future game): delete it. nba_odds_pull
    re-pulls every line every run and will now write it under the league id,
    so nothing is lost.
  * never touches a numeric-id row's score, and never overwrites a line that
    is already there.

SAFETY
  * dry-run by default.
  * only ever deletes a row whose game_id is NOT numeric.
  * checks first that no jerry_reads / public_receipts / signal_attribution
    row cites a phantom id (measured: 0 of 5 do). Aborts if that changes.
  * re-reads afterwards and asserts the phantoms are gone.

    python repair_nba_clippers_phantom_rows.py
    python repair_nba_clippers_phantom_rows.py --apply
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

LINE_COLS = ('close_spread', 'close_total', 'close_home_ml', 'close_away_ml')


def _env(path):
    out = {}
    try:
        for line in open(path, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_E = _env(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = (_E.get('SUPABASE_SERVICE_ROLE_KEY') or _E.get('SUPABASE_KEY')
       or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
       'Content-Type': 'application/json'}


def req(method, path, params=None, body=None, extra=None):
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
        print('  HTTP %s on %s :: %s'
              % (e.code, path, e.read().decode('utf-8', 'replace')[:200]))
        return None, e.code


def results(since):
    out, off = [], 0
    while True:
        rows, st = req('GET', 'nba_game_results',
                       {'select': '*', 'game_date': 'gte.' + since,
                        'order': 'game_date.asc', 'limit': '1000',
                        'offset': str(off)})
        if not isinstance(rows, list):
            sys.exit('read failed (status %s) -- refusing to write' % st)
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def tkey(s):
    """Team key that treats 'LA Clippers' and 'Los Angeles Clippers' as one."""
    n = ''.join(c for c in str(s or '').lower() if c.isalnum())
    return n.replace('losangeles', 'la')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--merge-only', action='store_true',
                    help='PATCH the line fields onto the real rows and leave '
                         'the phantom rows in place (no DELETE). This alone '
                         'makes the games gradeable.')
    ap.add_argument('--since', default='2026-09-01')
    a = ap.parse_args()

    print('=' * 84)
    print('REPAIR NBA PHANTOM RESULT ROWS  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 84)

    rows = results(a.since)
    phantom = [r for r in rows if not str(r.get('game_id') or '').isdigit()]
    real = [r for r in rows if str(r.get('game_id') or '').isdigit()]
    print('  rows since %s: %d   numeric-id %d   minted-id %d'
          % (a.since, len(rows), len(real), len(phantom)))
    if not phantom:
        print('  no phantom rows. nothing to do.')
        return 0

    idx = {}
    for r in real:
        idx[(str(r.get('game_date'))[:10], tkey(r.get('home_team')),
             tkey(r.get('away_team')))] = r

    cited = 0
    for t in ('jerry_reads', 'public_receipts', 'signal_attribution'):
        for p in phantom:
            got, _st = req('GET', t, {'select': 'game_id', 'limit': '1',
                                      'game_id': 'eq.%s' % p['game_id']})
            if isinstance(got, list) and got:
                cited += 1
                print('  !! %s cites phantom %s' % (t, p['game_id']))
    if cited:
        print('  ABORT: %d reference(s) to a phantom id. Repoint them first.'
              % cited)
        return 1
    print('  no jerry_reads / public_receipts / signal_attribution row cites '
          'a phantom id')

    merges, orphans = [], []
    for p in phantom:
        k = (str(p.get('game_date'))[:10], tkey(p.get('home_team')),
             tkey(p.get('away_team')))
        sib = idx.get(k)
        if sib:
            fill = {c: p.get(c) for c in LINE_COLS
                    if p.get(c) is not None and sib.get(c) is None}
            merges.append((p, sib, fill))
        else:
            orphans.append(p)

    print('')
    for p, sib, fill in merges:
        print('  MERGE %s' % str(p['game_id'])[:52])
        print('     -> %s  fills %s'
              % (sib['game_id'],
                 fill if fill else '(nothing; the real row already has it)'))
    for p in orphans:
        print('  DROP  %s' % str(p['game_id'])[:52])
        print('     no league-id sibling: a future game. The fixed puller '
              'rewrites it under the league id on its next run.')

    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    if a.merge_only:
        for p, sib, fill in merges:
            if not fill:
                continue
            _x, st = req('PATCH', 'nba_game_results',
                         {'game_id': 'eq.%s' % sib['game_id']}, fill,
                         {'Prefer': 'return=minimal'})
            if st in (200, 204):
                ok += 1
            else:
                fail += 1
        print('')
        print('  merged %d, failed %d  (phantom rows LEFT IN PLACE)' % (ok, fail))
        chk = [r for r in results(a.since)
               if str(r.get('game_id') or '').isdigit()
               and r.get('home_score') is not None
               and r.get('close_spread') is not None]
        print('  numeric-id rows with BOTH a score and a close_spread: %d' % len(chk))
        print('  next: python nba_resolve_results.py --date <each played date>')
        return 0
    for p, sib, fill in merges:
        if fill:
            _x, st = req('PATCH', 'nba_game_results',
                         {'game_id': 'eq.%s' % sib['game_id']}, fill,
                         {'Prefer': 'return=minimal'})
            if st not in (200, 204):
                fail += 1
                print('  !! merge failed for %s' % sib['game_id'])
                continue
        _x, st = req('DELETE', 'nba_game_results',
                     {'game_id': 'eq.%s' % p['game_id']}, None,
                     {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
    for p in orphans:
        _x, st = req('DELETE', 'nba_game_results',
                     {'game_id': 'eq.%s' % p['game_id']}, None,
                     {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
    print('')
    print('  repaired %d, failed %d' % (ok, fail))

    after = [r for r in results(a.since)
             if not str(r.get('game_id') or '').isdigit()]
    print('  phantom rows remaining: %d (was %d)' % (len(after), len(phantom)))
    if ok and len(after) >= len(phantom):
        print('  ABORT-LEVEL: writes returned OK but nothing moved.')
        return 1
    print('  next: python nba_resolve_results.py --date <each played date>')
    return 0


if __name__ == '__main__':
    sys.exit(main())
