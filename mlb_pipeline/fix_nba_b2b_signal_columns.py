#!/usr/bin/env python3
"""Fix the two NBA b2b signal conditions that read a column that does not exist.

== WHY (2026-10-28) =========================================================
nba_home_b2b_fade (id 788) and nba_away_b2b_fade (id 789) are enabled,
situational, weighted, and have NEVER fired. Replaying every enabled NBA
condition against 81 live nba_game_context rows with the scorer's own
evaluator (signal_expr) shows why:

    cond: ctx.home_b2b is not None and (...) and ctx.away_b2b is not None ...

There is no `home_b2b` or `away_b2b` column. The columns are `home_is_b2b`
and `away_is_b2b`, and they ARE populated -- 81 of 81 and 80 of 81. The third
b2b signal, nba_prop_b2b_fade, uses the correct names; only these two are
wrong. These are NBA's only situational signals for a game pick, so the
entire situational class has been absent from every NBA pick since launch.

== WHAT THE FIXED SIGNALS ARE WORTH, MEASURED FIRST =========================
Back-to-backs derived from two seasons of nba_game_results (consecutive
calendar days per team, regular season only), 2,516 graded games:

    rested side wins ML            n=  618   55.5%  [51.6, 59.4]
    [CONTROL] neither tired,
              home wins            n= 1752   55.8%  [53.4, 58.1]
    [CONTROL] all games,
              home wins            n= 2516   54.8%  [52.9, 56.8]

Betting the rested team wins 55.5%; just betting HOME in a game where nobody
is tired wins 55.8%. The direction-only control kills it -- the "B2B edge" is
home-court advantage wearing a different name. It splits badly (57.9% ->
53.1%) and on ATS it is 51.2% (53.5% -> 49.0%). The asymmetry is the tell:
"away tired, bet home" is 58.3%, while "home tired, bet away" is 52.5% --
BELOW the 54.8% base home rate. And an ML on the rested favourite prices
near -140/-180, so breakeven is 58-64%, not 52.4%.

So the condition is repaired, because a condition naming a column that does
not exist is simply broken. But it must NOT enter at the UNVALIDATED 0.3
prior that every other NBA game signal is currently carrying. Its registry
row is seeded with the measured hit rate and n, so edge_weight prices it off
evidence (no edge over the control = 0.0) instead of a hopeful default.

That is the difference between switching a signal off and letting the
evidence price it: it stays enabled, it fires, it gets attributed weekly, and
if the direction ever earns weight the refit will grant it.

    python fix_nba_b2b_signal_columns.py
    python fix_nba_b2b_signal_columns.py --apply
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


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

#: the backtest above, stored so the audit and the refit quote one number
MEASURED = {'hit_rate': 55.5, 'sample_n': 618, 'baseline_pct': 55.8}
KEYS = ('nba_home_b2b_fade', 'nba_away_b2b_fade')
NOTE = ('2026-10-28 seeded from a 2-season derived-schedule backtest: '
        'rested side 55.5% n=618 against a 55.8% home-court control. '
        'No edge over the control, so no weight - not the 0.3 prior.')


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
        print('  HTTP %s :: %s'
              % (e.code, e.read().decode('utf-8', 'replace')[:200]))
        return None, e.code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=' * 84)
    print('FIX NBA b2b SIGNAL CONDITIONS  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 84)

    # The target columns must actually hold data before rewriting to them,
    # or this swaps one dead condition for another.
    ctx, st = req('GET', 'nba_game_context',
                  {'select': 'home_is_b2b,away_is_b2b',
                   'game_date': 'gte.2026-10-18',
                   'order': 'game_date.asc', 'limit': '200'})
    if not isinstance(ctx, list) or not ctx:
        sys.exit('could not read nba_game_context (status %s) -- refusing' % st)
    hn = sum(1 for r in ctx if r.get('home_is_b2b') is not None)
    an = sum(1 for r in ctx if r.get('away_is_b2b') is not None)
    print('  nba_game_context rows %d: home_is_b2b populated %d, '
          'away_is_b2b %d' % (len(ctx), hn, an))
    if hn == 0 or an == 0:
        sys.exit('  target columns are all NULL -- refusing')

    changed = 0
    for k in KEYS:
        rows, _st = req('GET', 'signal_sources',
                        {'select': 'id,signal_key,condition_expr,sport',
                         'sport': 'eq.NBA', 'signal_key': 'eq.' + k})
        if not rows:
            print('  %s: NOT FOUND' % k)
            continue
        row = rows[0]
        old = str(row.get('condition_expr') or '')
        if 'ctx.home_b2b' not in old and 'ctx.away_b2b' not in old:
            print('  %s: already fixed' % k)
            continue
        new = (old.replace('ctx.home_b2b', 'ctx.home_is_b2b')
                  .replace('ctx.away_b2b', 'ctx.away_is_b2b'))
        print('')
        print('  %s (id=%s)' % (k, row.get('id')))
        print('     OLD: %s' % old[:150])
        print('     NEW: %s' % new[:150])
        if not a.apply:
            continue
        _x, st2 = req('PATCH', 'signal_sources', {'id': 'eq.%s' % row['id']},
                      {'condition_expr': new}, {'Prefer': 'return=minimal'})
        if st2 in (200, 204):
            changed += 1
        else:
            print('     !! write failed')

    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    print('')
    print('  conditions rewritten: %d' % changed)

    for k in KEYS:
        rows, _st = req('GET', 'signal_registry',
                        {'select': 'id,signal_name', 'sport': 'eq.NBA',
                         'signal_name': 'eq.' + k})
        body = {'hit_rate': MEASURED['hit_rate'],
                'sample_n': MEASURED['sample_n'],
                'baseline_pct': MEASURED['baseline_pct'],
                'tier': 'ANTI_VALIDATED',
                'recommended_weight': 0.0,
                'notes': NOTE}
        if rows:
            _x, st2 = req('PATCH', 'signal_registry',
                          {'id': 'eq.%s' % rows[0]['id']}, body,
                          {'Prefer': 'return=minimal'})
        else:
            body.update({'sport': 'NBA', 'signal_name': k,
                         'market_scope': '*'})
            _x, st2 = req('POST', 'signal_registry', None, [body],
                          {'Prefer': 'return=minimal'})
        print('  registry seeded for %-20s status=%s' % (k, st2))

    after, _st = req('GET', 'signal_sources',
                     {'select': 'signal_key,condition_expr', 'sport': 'eq.NBA',
                      'signal_key': 'in.(%s)' % ','.join(KEYS)})
    bad = [r for r in (after or [])
           if 'ctx.home_b2b' in str(r.get('condition_expr'))
           or 'ctx.away_b2b' in str(r.get('condition_expr'))]
    print('  conditions still naming a non-existent column: %d' % len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
