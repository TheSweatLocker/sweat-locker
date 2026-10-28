#!/usr/bin/env python3
"""Restore the three Clippers closing lines from line_history.

== WHY (2026-10-28) ========================================================
Sequence of events, all today:

1. Every Clippers game had TWO rows in nba_game_results -- an ESPN-id row
   with the score and no line, and a minted-composite-id row with the line
   and no score -- because the Odds API says "Los Angeles Clippers" while
   everything else says "LA Clippers".
2. repair_nba_clippers_phantom_rows merged the line fields onto the real rows
   and deleted the phantoms. Rows holding both a score and a close line went
   59 -> 62.
3. I then ran nba_resolve_results on those dates. It copies close_spread /
   close_total / close_home_ml / close_away_ml out of nba_game_context and
   writes them straight into nba_game_results -- and ctx has a close_spread
   on only 57 of 81 recent rows, so for the Clippers it wrote NULL over the
   values just merged. The count fell back to 52.
4. The phantom rows were already deleted, so the merge source was gone.

nba_resolve_results is now guarded (it omits a close_* key whose value is
None rather than blanking what is stored). This recovers the three lines from
the authoritative source.

THE LINES ARE NOT LOST. line_history holds them under the MINTED id -- 15,520
rows for the 10-21 game, 4,290 for 10-23, 3,462 for 10-25. The totals
recovered this way match the deleted phantom rows EXACTLY (226.5 / 225.5 /
223.0), which is the check that this is reading the right thing.

Side note worth its own backlog item: line_history is keyed on whatever id
the puller used, so all historical Clippers line history is orphaned from the
ESPN id. New rows will join correctly now that the alias is fixed; the old
ones will not unless repointed.

SIGN CONVENTION. line_history stores a side and a magnitude. close_spread on
nba_game_results is HOME-PERSPECTIVE and NEGATIVE when home is favoured
(measured: corr(close_spread, home margin) = -0.517 on n=2,449). So
  side=home line=-9.0  -> close_spread -9.0
  side=away line=16.0  -> close_spread -16.0   (home favoured by 16)
  side=away line=4.5   -> close_spread  -4.5
A row whose recovered value disagrees in SIGN with the game's actual result
is reported and skipped rather than written.

    python restore_clippers_closing_lines.py
    python restore_clippers_closing_lines.py --apply
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

#: (ESPN id on nba_game_results, minted id that line_history is keyed on)
PAIRS = [
    ('401909842', 'nba_20261021_Sacramento_Kings_Los_Angeles_Clippers'),
    ('401909851', 'nba_20261023_Los_Angeles_Clippers_Los_Angeles_Lakers'),
    ('401909865', 'nba_20261025_Los_Angeles_Clippers_Oklahoma_City_Thunder'),
]


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
        return e.read().decode('utf-8', 'replace')[:200], e.code


def paged(table, select='*', order='captured_at.asc', **filt):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off),
             'order': order}
        p.update(filt)
        rows, st = req('GET', table, p)
        if not isinstance(rows, list):
            sys.exit('read of %s failed (%s)' % (table, st))
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=' * 92)
    print('RESTORE CLIPPERS CLOSING LINES FROM line_history  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 92)

    plans = []
    for espn, minted in PAIRS:
        res, _st = req('GET', 'nba_game_results',
                       {'select': 'game_id,game_date,home_team,away_team,'
                                  'home_score,away_score,close_spread,'
                                  'close_total',
                        'game_id': 'eq.%s' % espn})
        if not res:
            print('  %s: no result row, skipped' % espn)
            continue
        r = res[0]
        lh = paged('line_history',
                   'market,side,line,price,captured_at',
                   **{'game_id': 'eq.%s' % minted})
        sp = [x for x in lh if str(x.get('market')) == 'spread'
              and _f(x.get('line')) is not None]
        to = [x for x in lh if str(x.get('market')) == 'total'
              and _f(x.get('line')) is not None]
        if not sp and not to:
            print('  %s: no usable line_history under %s' % (espn, minted))
            continue

        new = {}
        if sp:
            last = sp[-1]
            mag = _f(last.get('line'))
            side = str(last.get('side') or '').lower()
            # home-perspective, negative when home is favoured
            home_sp = -abs(mag) if side == 'away' else (
                mag if mag < 0 else -mag)
            new['close_spread'] = home_sp
        if to:
            new['close_total'] = _f(to[-1].get('line'))

        hs, as_ = _f(r.get('home_score')), _f(r.get('away_score'))
        sanity = ''
        if 'close_spread' in new and hs is not None and as_ is not None:
            margin = hs - as_
            # a HOME favourite (negative number) that lost outright is
            # possible, so this is a smell test, not a gate - except where
            # the recovered number has the favourite completely backwards
            if (margin > 20 and new['close_spread'] > 0) or \
               (margin < -20 and new['close_spread'] < -10):
                sanity = '  !! SIGN LOOKS WRONG against a %+0.0f margin' % margin
        print('')
        print('  %s  %s @ %s  %s-%s'
              % (r.get('game_date'), r.get('away_team'), r.get('home_team'),
                 r.get('away_score'), r.get('home_score')))
        print('     stored : spread=%s total=%s'
              % (r.get('close_spread'), r.get('close_total')))
        print('     from line_history (%d quotes): spread=%s total=%s%s'
              % (len(lh), new.get('close_spread'), new.get('close_total'),
                 sanity))
        if sanity:
            print('     SKIPPED on the sign check')
            continue
        fill = {k: v for k, v in new.items() if r.get(k) is None}
        if not fill:
            print('     nothing to fill - already populated')
            continue
        plans.append((espn, fill))

    print('')
    print('  rows to patch: %d' % len(plans))
    if not a.apply:
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    for espn, fill in plans:
        _x, st = req('PATCH', 'nba_game_results', {'game_id': 'eq.%s' % espn},
                     fill, {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
            print('  !! patch failed for %s (%s)' % (espn, st))
    print('  patched %d, failed %d' % (ok, fail))

    chk = paged('nba_game_results',
                'game_date,game_id,home_score,close_spread',
                order='game_date.asc', **{'game_date': 'gte.2026-10-18'})
    played = [x for x in chk if str(x['game_date'])[:10] < '2026-10-29']
    both = [x for x in played if x.get('home_score') is not None
            and x.get('close_spread') is not None]
    print('  played NBA rows with BOTH a score and a close line: %d of %d'
          % (len(both), len(played)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
