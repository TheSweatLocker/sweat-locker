#!/usr/bin/env python3
"""Does any sport's stored prop lookback contain the game it predicted?

══ WHY THIS EXISTS (2026-10-27) ════════════════════════════════════════════
Andy: "I feel like the entirety of this project anytime I have asked that
you have never done it correctly because it's too much work. That stops
now."

He was right about the specific failure that prompted this. I claimed the
2026-09-22 L5 leak was "still live on NFL and NBA props" on the strength of
a COLUMN-PRESENCE check: those tables carry `_stat_avg_l5` and carry no
`signals._leak_repair` stamp, so I concluded they were unrepaired. The stamp
is absent because they never needed repairing. Nothing was leaking.

Presence of a column is not evidence of contamination. The only test that
counts is whether the stored window contains the predicted game or a LATER
one — and that test needs a different key per sport, which is the part that
was "too much work" and got skipped:

    MLB  `_stat_last10` entries carry `date`     -> compare to game_date
    NFL  entries carry `season` + `week`, NO date -> compare to the game's
         (season, week), which the PROP ROW DOES NOT HAVE (week is NULL on
         all 4,568 rows) and must be joined from nfl_game_context
    NBA  no `_stat_last10`; the L5 is written by nba_prop_gate.py, which
         bounds on `dd < game_date` directly
    NHL  no lookback stored at all

VERIFIED STATE AT FIRST RUN
    MLB  repaired 2026-10-26, 13,290 rows carry a _leak_repair stamp
    NFL  4,107 of 4,107 windows checked on (season, week): 0 contaminated
    NBA  bounded at source, violation counter asserts 0
    NHL  no lookback -> nothing to leak

Run this instead of believing a claim about it. Non-zero exit means a sport
is contaminated.

    python audit_prop_lookback_leak.py
    python audit_prop_lookback_leak.py --sport NFL --examples 10
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


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
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}


def fetch_all(table, select='*', **filt):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off)}
        p.update(filt)
        u = BASE + table + '?' + urllib.parse.urlencode(p, safe='.,()*:')
        r = urllib.request.Request(u, headers=HDR)
        try:
            with urllib.request.urlopen(r, timeout=90) as resp:
                rows = json.loads(resp.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            print('    HTTP %s on %s :: %s'
                  % (e.code, table, e.read().decode('utf-8', 'replace')[:200]))
            return out
        if not isinstance(rows, list):
            return out
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def signals(row):
    s = row.get('signals')
    if isinstance(s, str):
        try:
            return json.loads(s) or {}
        except ValueError:
            return {}
    return s if isinstance(s, dict) else {}


def audit_by_date(table, label):
    """MLB-shaped: entries carry a `date`."""
    rows = fetch_all(table)
    have = [r for r in rows
            if isinstance(signals(r).get('_stat_last10'), list)
            and signals(r)['_stat_last10']]
    repaired = sum(1 for r in rows if '_leak_repair' in signals(r))
    checked = leaked = nodate = 0
    ex = []
    for r in have:
        gd = str(r.get('game_date') or '')[:10]
        if not gd:
            continue
        ds = []
        for e in signals(r)['_stat_last10']:
            if isinstance(e, dict) and e.get('date'):
                ds.append(str(e['date'])[:10])
        if not ds:
            nodate += 1
            continue
        checked += 1
        bad = [d for d in ds if d >= gd]
        if bad:
            leaked += 1
            if len(ex) < 20:
                ex.append((r.get('player_name'), gd, r.get('prop_type'),
                           sorted(bad, reverse=True)[:3],
                           signals(r).get('_stat_avg_l5')))
    return {'label': label, 'rows': len(rows), 'windows': len(have),
            'repaired': repaired, 'checked': checked, 'leaked': leaked,
            'unkeyed': nodate, 'key': 'date', 'examples': ex}


def audit_by_week(table, label):
    """NFL-shaped: entries carry season+week; the prop row does NOT."""
    ctx = {}
    for c in fetch_all('nfl_game_context',
                       select='game_id,week,season_week,season'):
        gid = str(c.get('game_id') or '')
        if gid:
            ctx[gid] = c
    rows = fetch_all(table)
    have = [r for r in rows
            if isinstance(signals(r).get('_stat_last10'), list)
            and signals(r)['_stat_last10']]
    repaired = sum(1 for r in rows if '_leak_repair' in signals(r))
    checked = leaked = unkeyed = 0
    ex = []
    for r in have:
        c = ctx.get(str(r.get('game_id') or ''))
        if not c:
            unkeyed += 1
            continue
        pw = c.get('week') if c.get('week') is not None else c.get('season_week')
        if pw is None:
            unkeyed += 1
            continue
        try:
            pw = int(pw)
            ps = int(c.get('season')) if c.get('season') is not None else 2026
        except (TypeError, ValueError):
            unkeyed += 1
            continue
        bad, keyed = [], False
        for e in signals(r)['_stat_last10']:
            if not isinstance(e, dict):
                continue
            es, ew = e.get('season'), e.get('week')
            if es is None or ew is None:
                continue
            keyed = True
            try:
                es, ew = int(es), int(ew)
            except (TypeError, ValueError):
                continue
            if es > ps or (es == ps and ew >= pw):
                bad.append((es, ew, e.get('value')))
        if not keyed:
            unkeyed += 1
            continue
        checked += 1
        if bad:
            leaked += 1
            if len(ex) < 20:
                ex.append((r.get('player_name'), '%s wk%s' % (ps, pw),
                           r.get('prop_type'), bad[:3],
                           signals(r).get('_stat_avg_l5')))
    return {'label': label, 'rows': len(rows), 'windows': len(have),
            'repaired': repaired, 'checked': checked, 'leaked': leaked,
            'unkeyed': unkeyed, 'key': '(season, week)', 'examples': ex}


def audit_bounded_at_source(table, label, note):
    """NBA/NHL-shaped: no stored window to re-check."""
    rows = fetch_all(table, select='signals')
    withl5 = sum(1 for r in rows if '_stat_avg_l5' in signals(r))
    win = sum(1 for r in rows
              if isinstance(signals(r).get('_stat_last10'), list)
              and signals(r)['_stat_last10'])
    return {'label': label, 'rows': len(rows), 'windows': win,
            'repaired': 0, 'checked': 0, 'leaked': 0, 'unkeyed': withl5,
            'key': 'n/a — ' + note, 'examples': []}


PLAN = (
    ('MLB', lambda: audit_by_date('mlb_pipeline_props', 'MLB')),
    ('NFL', lambda: audit_by_week('nfl_pipeline_props', 'NFL')),
    ('NBA', lambda: audit_bounded_at_source(
        'nba_pipeline_props', 'NBA',
        'no _stat_last10; nba_prop_gate bounds dd < game_date at source')),
    ('NHL', lambda: audit_bounded_at_source(
        'nhl_pipeline_props', 'NHL', 'no lookback stored')),
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport')
    ap.add_argument('--examples', type=int, default=4)
    a = ap.parse_args()

    print('=' * 100)
    print('PROP LOOKBACK LEAK AUDIT')
    print('  the only question: does a stored window contain the game it')
    print('  predicted, or a later one? Tested on each sport\'s OWN key.')
    print('=' * 100)

    bad_sports = []
    out = []
    for sp, fn in PLAN:
        if a.sport and sp != a.sport.upper():
            continue
        r = fn()
        out.append(r)
        if r['leaked']:
            bad_sports.append(sp)

    hdr = ('%-5s %8s %9s %9s %9s %9s  %s'
           % ('sport', 'rows', 'windows', 'checked', 'LEAKED', 'repaired',
              'key'))
    print(hdr)
    print('-' * len(hdr))
    for r in out:
        print('%-5s %8d %9d %9d %9d %9d  %s'
              % (r['label'], r['rows'], r['windows'], r['checked'],
                 r['leaked'], r['repaired'], r['key']))
    print('')
    for r in out:
        if r['unkeyed'] and r['key'].startswith('n/a'):
            print('  %s: %d rows carry _stat_avg_l5, bounded at source — '
                  'nothing stored to re-verify here, so the guarantee is the '
                  'writer\'s assert, not this audit.'
                  % (r['label'], r['unkeyed']))
        elif r['unkeyed']:
            print('  %s: %d window(s) could not be keyed and were NOT counted '
                  'as clean.' % (r['label'], r['unkeyed']))

    for r in out:
        if not r['examples']:
            continue
        print('')
        print('  %s contaminated examples:' % r['label'])
        for nm, when, pt, bad, l5 in r['examples'][:a.examples]:
            print('     %-22s %-12s %-24s offending=%s  _stat_avg_l5=%s'
                  % (nm, when, pt, bad, l5))

    print('')
    if bad_sports:
        print('  RESULT: CONTAMINATED -> %s' % ', '.join(bad_sports))
        print('  Repair before trusting any measurement built on these rows.')
        return 1
    print('  RESULT: no stored window contains its own game or a later one.')
    print('  Any prop measurement failing is failing for some OTHER reason.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
