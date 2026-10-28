#!/usr/bin/env python3
"""Which enabled signals have NEVER fired? Each one is a dead weight slot.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
`nba_pace_both_slow_under` was enabled, carried weight 0.3 and had
`sample_n=0`. Its condition was `home_pace <= 96 AND away_pace <= 96`, which
fires on **0 of 147** games with pace data -- league p10 is 96.5, so the
rule was unreachable. Dead code wearing an enabled flag, for a whole season.

Four more NBA pace signals could never fire either, for a different reason:
they read a PROP-level `signals.pace` dict, and NBA props do not publish at
all (0 prop receipts against 8,760 graded props).

So there are at least two distinct failure modes, and both look identical
from the outside -- an enabled row with a weight and no evidence:

  * UNREACHABLE CONDITION  the threshold is outside the data's range
  * UNREACHABLE SUBJECT    the condition reads a surface that does not
                           publish for that sport

This finds every one of them by firing history, which is the symptom both
share. It writes nothing.

WHY FIRING HISTORY RATHER THAN EVALUATING condition_expr: the conditions are
Python expressions over a ctx object, and eval-ing 849 of them against live
rows would be both unsafe and a different test than "did it actually fire".
A signal that fires is alive regardless of how its condition reads.

    python audit_unfireable_signals.py
    python audit_unfireable_signals.py --sport NBA
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
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

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'NHL': 'nhl_game_context', 'NBA': 'nba_game_context',
       'MLB': 'mlb_game_context', 'NCAAB': 'ncaab_game_context'}


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
            print('    HTTP %s on %s' % (e.code, table))
            return out
        if not isinstance(rows, list):
            return out
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def as_dict(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, dict) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport')
    a = ap.parse_args()

    print('=' * 104)
    print('UNFIREABLE SIGNALS — enabled, weighted, never fired')
    print('=' * 104)

    src = [s for s in fetch_all('signal_sources')
           if s.get('enabled') in (True, 'true', 'True')]
    print('  enabled signal_sources: %d' % len(src))

    # firings: attribution rows + _ensemble_sources on every ctx table
    fired = collections.Counter()
    for r in fetch_all('signal_attribution', select='sport,signal_key'):
        fired[(str(r.get('sport')), str(r.get('signal_key')))] += 1
    for sp, tbl in CTX.items():
        for row in fetch_all(tbl, select='primary_play'):
            pp = as_dict(row.get('primary_play'))
            if not pp:
                continue
            for e in (pp.get('_ensemble_sources') or []):
                if isinstance(e, dict) and e.get('signal_key'):
                    fired[(sp, str(e['signal_key']))] += 1
    print('  distinct (sport, signal) firings observed: %d' % len(fired))

    # ══ 2026-10-27 · A REGISTRY sample_n IS ALSO A FIRING ════════════════
    # The first version of this audit counted only signal_attribution and
    # _ensemble_sources and reported 471 dead signals. 231 of those have
    # sample_n > 0 in the registry -- nhl_prop_l5_cold has n=17,350 -- so
    # they fired through the PROP path (prop_playbook_decisions) or the GAME
    # path (results.primary_play), neither of which this audit reads. Those
    # are alive and well evidenced. Counting them as dead was my error, and
    # it would have made 231 healthy signals look like waste.
    reg = {}
    for r in fetch_all('signal_registry',
                       select='sport,signal_name,recommended_weight,'
                              'sample_n,tier'):
        k = (str(r.get('sport')), str(r.get('signal_name')))
        try:
            w = float(r.get('recommended_weight'))
        except (TypeError, ValueError):
            w = None
        if w is not None and (k not in reg or w > reg[k][0]):
            reg[k] = (w, r.get('sample_n'), str(r.get('tier') or ''))

    sports = [a.sport.upper()] if a.sport else sorted({str(s.get('sport'))
                                                       for s in src})
    print('')
    print('  %-7s %8s %9s %10s %12s' % ('sport', 'enabled', 'FIRED',
                                        'NEVER', 'never %'))
    print('  ' + '-' * 52)
    dead_all = []
    for sp in sports:
        mine = [s for s in src if str(s.get('sport')) == sp]
        if not mine:
            continue
        def _alive(sig):
            k = (sp, str(sig.get('signal_key')))
            if fired.get(k, 0) > 0:
                return True
            ev = reg.get(k)
            try:
                return bool(ev) and float(ev[1] or 0) > 0
            except (TypeError, ValueError):
                return False
        dead = [s for s in mine if not _alive(s)]
        print('  %-7s %8d %9d %10d %11.0f%%'
              % (sp, len(mine), len(mine) - len(dead), len(dead),
                 100.0 * len(dead) / len(mine)))
        dead_all += [(sp, s) for s in dead]

    # classify the dead ones by the likely cause, from the condition text
    print('')
    print('=' * 104)
    print('THE DEAD ONES, classified by what their condition reads')
    print('=' * 104)
    cls = collections.Counter()
    buckets = collections.defaultdict(list)
    for sp, s in dead_all:
        cond = str(s.get('condition_expr') or '')
        if re.search(r"\bp\s*[\.\[]", cond) or 'prop_type' in cond:
            c = 'reads a PROP (p.*) — dead if that sport does not ship props'
        elif 'ctx.' not in cond:
            c = 'reads neither ctx nor p — cannot be evaluated'
        else:
            c = 'reads ctx — threshold may be outside the data range'
        cls[c] += 1
        buckets[c].append((sp, s))
    for c, n in cls.most_common():
        print('  %-62s %d' % (c, n))

    print('')
    print('  the dead ones that CARRY WEIGHT (a wasted weight slot):')
    weighted = []
    for sp, s in dead_all:
        k = (sp, str(s.get('signal_key')))
        w = reg.get(k)
        if w and w[0] > 0:
            weighted.append((w[0], sp, s, w[2]))
    # sort on the weight only -- tuples with equal weight fell
    # through to comparing the dicts and raised TypeError.
    weighted.sort(key=lambda z: -z[0])
    print('  %-7s %-36s %8s %-14s %s'
          % ('sport', 'signal', 'weight', 'tier', 'market'))
    for w, sp, s, tier in weighted[:20]:
        print('  %-7s %-36s %8.3f %-14s %s'
              % (sp, str(s.get('signal_key'))[:36], w, tier[:14],
                 s.get('market_scope')))
    print('  ... %d dead signals carry a non-zero weight' % len(weighted))
    print('  total wasted weight: %.2f' % sum(w for w, _s, _x, _t in weighted))

    if a.sport:
        print('')
        print('=' * 104)
        print('%s — every dead signal, with its condition' % a.sport.upper())
        print('=' * 104)
        for sp, s in dead_all:
            print('')
            print('  %-34s market=%-7s class=%s'
                  % (s.get('signal_key'), s.get('market_scope'),
                     s.get('class')))
            print('     cond: %s' % str(s.get('condition_expr'))[:160])
    print('')
    print('  Nothing was written. A dead signal is not automatically wrong —')
    print('  it may be waiting for a rare spot. But one that cannot fire by')
    print('  construction is a weight slot doing nothing, and it hides as a')
    print('  healthy enabled row.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
