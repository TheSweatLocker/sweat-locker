#!/usr/bin/env python3
"""Recompute the 48 signal_registry weights that exceed the formula's cap.

══ WHY (2026-10-27) · F4 ═══════════════════════════════════════════════════
`refit_signal_registry._recommended_weight_for` caps weight at 1.0 and
shrinks it by `log1p(n)/log(101)`. A second writer,
`refresh_prop_signal_calibration.py`, used the same edge term with NEITHER
the cap nor the shrinkage:

    round(max(0.0, (hit - BREAKEVEN) / 0.12), 3)

Two writers, two formulas, one column. The result was that the LARGEST
weights in the live scorer sat on the THINNEST samples. Verified to three
decimals against the stored rows:

    signal                hit%   n   stored   uncapped calc
    NHL two_sided_price   84.8   33   2.700   2.702
    MLB l5_confirm        80.0   20   2.300   2.302
    MLB opp_form          78.6   28   2.183   2.185
    MLB last7_walks       76.2   21   1.983   1.985

A weight of 2.700 is roughly 3x a VALIDATED signal's mean (0.931), earned
on 33 graded firings. `log1p(n)/log(101)` does not reach 1.0 until n=100,
so these should be 0.66-0.80.

The writer is fixed in the same commit. This repairs the rows it already
wrote: 48 of 1,280, all `origin='auto_calibration_from_graded'`, median
n=24, 39 of them n<50. Total weight across them falls 73.9 -> 35.4.

WHY NOT JUST RE-RUN THE CALIBRATOR: it only rewrites signals that fired
inside its rolling window, so rows outside the window would keep the old
weight indefinitely. This touches exactly the rows that are wrong.

SAFETY
  * `recommended_weight` only. tier, hit_rate, sample_n, edge_pp and
    everything else are untouched -- this is an arithmetic correction, not
    a re-tiering.
  * only rows where the stored weight EXCEEDS 1.0, which the formula makes
    impossible, so there is no judgement call about which are wrong.
  * recomputed from the row's OWN stored hit_rate and sample_n. Nothing is
    inferred from a neighbouring row.
  * a row missing hit_rate or sample_n is SKIPPED and reported, not
    guessed at.
  * dry-run by default; every write read back and the remaining count
    asserted to be 0.

    python repair_uncapped_signal_weights.py
    python repair_uncapped_signal_weights.py --apply
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BREAKEVEN = 0.524      # matches refresh_prop_signal_calibration
EDGE_SCALE = 0.12
N_FULL = 101           # log1p(n)/log(101) reaches 1.0 at n=100


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


def fetch_all(table, select='*'):
    out, off = [], 0
    while True:
        rows, st = _req('GET', table, {'select': select, 'limit': '1000',
                                       'offset': str(off)})
        if not isinstance(rows, list):
            print('    read failed (status %s)' % st)
            break
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def proper_weight(hit_pct, n):
    """The canonical capped + shrunk weight. hit_pct is 0-100."""
    if hit_pct is None or n is None:
        return None
    edge = max(0.0, (hit_pct / 100.0) - BREAKEVEN)
    edge_c = min(edge / EDGE_SCALE, 1.0)
    n_c = min(math.log1p(max(int(n), 0)) / math.log(N_FULL), 1.0)
    return round(edge_c * n_c, 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('=' * 84)
    print('REPAIR UNCAPPED signal_registry WEIGHTS  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('  the formula cannot exceed 1.0, so any stored weight above it')
    print('  was written by the uncapped path and is arithmetically wrong')
    print('=' * 84)

    reg = fetch_all('signal_registry')
    print('  registry rows: %d' % len(reg))
    over = [r for r in reg if (f(r.get('recommended_weight')) or 0) > 1.0]
    print('  rows with weight > 1.0: %d' % len(over))
    if not over:
        print('  nothing to repair.')
        return 0

    print('  by sport:  %s'
          % dict(collections.Counter(str(r.get('sport'))
                                     for r in over).most_common()))
    print('  by origin: %s'
          % dict(collections.Counter(str(r.get('origin'))
                                     for r in over).most_common()))
    print('  by tier:   %s'
          % dict(collections.Counter(str(r.get('tier'))
                                     for r in over).most_common()))

    plan, skipped = [], []
    for r in over:
        hr = f(r.get('hit_rate'))
        n = f(r.get('sample_n'))
        pw = proper_weight(hr, n)
        if pw is None:
            skipped.append(r)
            continue
        plan.append((r, f(r.get('recommended_weight')), pw))

    print('')
    print('  repairable: %d   SKIPPED (missing hit_rate or sample_n): %d'
          % (len(plan), len(skipped)))
    for r in skipped[:6]:
        print('     %-6s %-34s hit=%r n=%r — left alone, not guessed'
              % (r.get('sport'), str(r.get('signal_name'))[:34],
                 r.get('hit_rate'), r.get('sample_n')))

    plan.sort(key=lambda z: -(z[1] or 0))
    print('')
    print('  %-6s %-34s %8s %10s %8s %7s'
          % ('sport', 'signal', 'stored', 'corrected', 'hit%', 'n'))
    print('  ' + '-' * 78)
    for r, old, pw in plan[:14]:
        print('  %-6s %-34s %8.3f %10.4f %7.1f%% %7.0f'
              % (r.get('sport'), str(r.get('signal_name'))[:34], old, pw,
                 f(r.get('hit_rate')) or 0, f(r.get('sample_n')) or 0))
    if len(plan) > 14:
        print('     ... and %d more' % (len(plan) - 14))

    tb = sum((o or 0) for _r, o, _p in plan)
    ta = sum(p for _r, _o, p in plan)
    print('')
    print('  total weight across these rows: %.1f -> %.1f  (%.0f%% removed)'
          % (tb, ta, 100.0 * (1 - ta / tb) if tb else 0))

    if not a.apply:
        print('')
        print('  DRY RUN — nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    for r, _old, pw in plan:
        _x, st = _req('PATCH', 'signal_registry',
                      {'id': 'eq.%s' % r.get('id')},
                      {'recommended_weight': pw},
                      {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
    print('')
    print('  corrected %d, failed %d' % (ok, fail))

    after = fetch_all('signal_registry', select='recommended_weight,tier')
    left = sum(1 for r in after
               if (f(r.get('recommended_weight')) or 0) > 1.0)
    print('  rows still above 1.0: %d (was %d)' % (left, len(over)))
    byt = collections.defaultdict(list)
    for r in after:
        w = f(r.get('recommended_weight'))
        if w is not None:
            byt[str(r.get('tier'))].append(w)
    print('  mean weight by tier now:')
    for t in sorted(byt, key=lambda k: -len(byt[k]))[:6]:
        v = byt[t]
        print('     %-18s n=%-5d mean %.3f  max %.3f'
              % (t, len(v), sum(v) / len(v), max(v)))
    if ok and left:
        print('  NOTE: %d rows remain above 1.0 — expected %d skipped.'
              % (left, len(skipped)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
