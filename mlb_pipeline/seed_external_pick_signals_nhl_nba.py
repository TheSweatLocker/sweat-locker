#!/usr/bin/env python3
"""Give NHL and NBA the external_pick signal they never had.

== WHY (2026-10-28) ========================================================
Andy's engine standard, 10-27: the scorer must weigh "external ahndicappers
and theirr record swhihc should matter and be wieghed acrodingnly".

ensemble_scorer already does exactly that. `_handler_external` reads
external_picks for the game, looks up each source's own
external_source_track_record, weights the opinion by that record, and flips a
demonstrated loser to the opposite side (hit_rate <= 0.35 at n >= 10). It is
fully sport-generic.

It just never runs for NHL or NBA, because the handler is dispatched by a
signal_sources row with class='external_pick' and neither sport has one:

    sport   external_pick rows   external_picks available   graded
    MLB              1                   9,492              9,057
    NCAAF            1                   3,319              2,984
    NFL              1                   1,635              1,442
    NCAAB            1                       0                  0
    UFC              1                      89                 --
    NHL              0                   1,246              1,147   <-
    NBA              0                     380                318   <-

So 1,147 graded NHL external picks and 318 NBA ones have been sitting unused.
This is one row per sport, not new logic.

== WHAT THE HANDLER WILL FIND, MEASURED FIRST ==============================
NHL externals by source x market, graded, n >= 25 (breakeven 52.38%):

    oddscrowd      rl     n=159   61.6% [53.9, 68.8]  FOLLOW, CI clears
    covers         rl     n=103   41.7% [32.7, 51.4]  below, CI clears low
    oddscrowd      ml     n=154   57.1% [49.2, 64.7]  no edge
    oddscrowd      total  n=156   51.3% [43.5, 59.0]  no edge
    scoresandodds  rl     n=128   51.6% [43.0, 60.0]  no edge
    sbr            ml     n= 61   57.4% [44.9, 69.0]  no edge
    dimers         ml     n= 47   57.4% [43.3, 70.5]  no edge

Both verdicts hold split-half: oddscrowd rl 63% then 60%, covers rl 43% then
40%. Everything else is noise, and the handler weights by record so noise
gets near-zero weight on its own -- no hand-picking required.

Worth noting WHERE the signal is: the puckline, which is the market our own
engine is worst at (NHL rl on the Sharp is 7-13, 35.0%, -4.93u). The
externals know something there that we do not.

NBA is thinner -- scoresandodds ml n=102 at 49.0%, oddscrowd ml n=86 at
54.7%, dimers ml n=36 at 61.1%, all CIs straddling. The row is added anyway
because the handler prices by record and the attribution loop (wired today)
will accumulate the evidence either way. That is building, not guessing.

NOTE ON covers: its 41.7% does NOT trip the handler's fade-flip, which needs
hit_rate <= 0.35. It will simply carry low weight. Flipping it would be a
separate claim needing its own measurement, so it is left to the weighting.

    python seed_external_pick_signals_nhl_nba.py
    python seed_external_pick_signals_nhl_nba.py --apply
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROWS = [
    {'sport': 'NHL', 'signal_key': 'external_handicapper_pick_nhl'},
    {'sport': 'NBA', 'signal_key': 'external_handicapper_pick_nba'},
]
#: copied field-for-field from the live UFC and NCAAB rows so the handler
#: dispatch, scope and prose all behave identically
TEMPLATE = {
    'class': 'external_pick',
    'condition_expr': '_HANDLER_EXTERNAL',
    'side_expr': '_HANDLER_EXTERNAL',
    'strength_expr': '_HANDLER_EXTERNAL',
    'market_scope': 'multi',
    'subject_scope': 'game',
    'enabled': True,
    'display_prose_template': 'external analysts are on this side',
    'origin': 'SEEDED_2026-10-28_EXTERNALS_NHL_NBA',
}


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
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return (json.loads(raw) if raw.strip() else None), resp.status
    except urllib.error.HTTPError as e:
        return e.read().decode('utf-8', 'replace')[:200], e.code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=' * 88)
    print('SEED external_pick SIGNAL FOR NHL AND NBA  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 88)

    # The handler is only useful if there are picks AND track records to
    # weight them by. Verified here rather than assumed.
    for row in ROWS:
        sp = row['sport']
        picks, _s1 = req('GET', 'external_picks',
                         {'select': 'source', 'sport': 'eq.%s' % sp,
                          'result': 'in.(W,L)', 'limit': '1000'})
        tr, _s2 = req('GET', 'external_source_track_record',
                      {'select': 'source,surface,hit_rate,n_wins,n_losses',
                       'sport': 'eq.%s' % sp, 'limit': '400'})
        usable = [t for t in (tr or []) if t.get('hit_rate') is not None]
        print('')
        print('  %-5s graded external picks %-5d   track records %-3d '
              '(with a usable rate %d)'
              % (sp, len(picks or []), len(tr or []), len(usable)))
        if not picks:
            print('     no graded picks - the handler would find nothing. '
                  'Skipping.')
            row['_skip'] = True
            continue
        if not usable:
            print('     no track record carries a usable hit_rate - every '
                  'opinion would fall to the prior. Skipping.')
            row['_skip'] = True
            continue
        exist, _s3 = req('GET', 'signal_sources',
                         {'select': 'id,signal_key,enabled',
                          'sport': 'eq.%s' % sp,
                          'class': 'eq.external_pick'})
        if exist:
            print('     already has an external_pick row (%s) - nothing to do'
                  % exist[0].get('signal_key'))
            row['_skip'] = True

    todo = [r for r in ROWS if not r.get('_skip')]
    print('')
    print('  rows to insert: %d  %s'
          % (len(todo), [r['signal_key'] for r in todo]))
    if not todo:
        return 0
    if not a.apply:
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    for row in todo:
        body = dict(TEMPLATE)
        body.update({'sport': row['sport'], 'signal_key': row['signal_key']})
        _x, st = req('POST', 'signal_sources', None, [body],
                     {'Prefer': 'return=minimal'})
        if st in (200, 201, 204):
            ok += 1
        else:
            fail += 1
            print('  !! insert failed for %s (%s): %s'
                  % (row['signal_key'], st, str(_x)[:160]))
    print('  inserted %d, failed %d' % (ok, fail))

    for row in todo:
        back, _st = req('GET', 'signal_sources',
                        {'select': 'sport,signal_key,class,enabled,'
                                   'market_scope,subject_scope',
                         'signal_key': 'eq.%s' % row['signal_key']})
        print('  read back: %s' % (back or 'MISSING'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
