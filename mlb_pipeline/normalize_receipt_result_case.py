#!/usr/bin/env python3
"""G6 · One spelling per outcome inside public_receipts.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
`public_receipts.result` carries 13 distinct values for about 5 states, and
three of them are pure case duplicates sitting in the SAME surface:

    WIN 5,950 / Win 898        LOSS 4,747 / Loss 955
    VOID 226  / Void 12 / void 28

Any comparison written inside that table misses rows. prop_jerry alone has
1,825 rows split from its own dominant spelling.

WHICH DIRECTION, and why it is NOT Title case
I nearly rewrote 11,000 rows the wrong way. The client compares
`result === 'Win'` in 40 places, which looks like Title case is canonical —
but those comparisons read `jerry_reads`, `daily_dawg` and
`daily_best_bet_history`, each of which genuinely stores Title case and is
internally consistent:

    jerry_reads             Win 896  Loss 704  Void 155  Push 20
    daily_dawg              Win 60   Loss 68   Push 4
    public_receipts         WIN 5,950 LOSS 4,747 VOID 226 PUSH 49

`public_receipts` has its own UPPERCASE convention, documented in
public_receipt.norm_result, and 5,950 rows follow it against 898 that do
not. So the table is normalised to ITSELF — 1,893 rows, not 11,000.

HOW THE STRAGGLERS GOT IN
grade_prop_jerry_reads.py copies `result` straight off the props source
table (`mlb_pipeline_props` etc., all Title case) into public_receipts
without passing it through norm_result. That is hardened in the same commit.

NOT TOUCHED, because these are DISTINCT STATES rather than spellings:
    NO_ACTION   7,901   prop did not play / could not be graded
    NO PLAY         4   a POTD discipline pass — a deliberate no-bet,
                        which is not the same thing as NO_ACTION
    UNGRADEABLE   523
    PUSH           49   tied the number
    TRACKED_ELSEWHERE 1
    NULL          287   not yet graded. NULL is how the graders FIND work
                        (`result=is.null`), so it must never be given a
                        sentinel — that bug hid 103 receipts forever.

SAFETY: `result` only, one row at a time, every write read back, dry-run by
default, and the W/L/Push totals are printed before and after so a
normalisation that changed a RECORD rather than a spelling is visible.

    python normalize_receipt_result_case.py
    python normalize_receipt_result_case.py --apply
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

# lowercase form -> the canonical spelling public_receipts already uses
CANON = {
    'win': 'WIN',
    'loss': 'LOSS',
    'void': 'VOID',
    'push': 'PUSH',
}


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
              % (e.code, path, e.read().decode('utf-8', 'replace')[:240]))
        return None, e.code


def fetch_all(table, **filt):
    out, off = [], 0
    while True:
        p = {'select': '*', 'limit': '1000', 'offset': str(off)}
        p.update(filt)
        rows, st = _req('GET', table, p)
        if not isinstance(rows, list):
            print('    read failed (status %s)' % st)
            break
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def tally(rows):
    c = collections.Counter()
    for r in rows:
        v = str(r.get('result') or '').strip().lower()
        if v in ('win', 'won'):
            c['W'] += 1
        elif v in ('loss', 'lost', 'lose'):
            c['L'] += 1
        elif v == 'push':
            c['P'] += 1
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('=' * 78)
    print('G6 · NORMALISE result CASE IN public_receipts  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('  canonical = the spelling this table already uses: %s'
          % ', '.join(sorted(set(CANON.values()))))
    print('=' * 78)

    rec = fetch_all('public_receipts')
    print('  receipts: %d' % len(rec))
    before_vals = collections.Counter(str(r.get('result')) for r in rec
                                      if r.get('result') is not None)
    print('  distinct result values now: %d' % len(before_vals))
    t0 = tally(rec)
    print('  W-L-P before: %d-%d-%d' % (t0['W'], t0['L'], t0['P']))

    todo = []
    for r in rec:
        v = r.get('result')
        if v is None:
            continue
        want = CANON.get(str(v).strip().lower())
        if want and str(v) != want:
            todo.append((r, str(v), want))

    print('')
    print('  rows whose spelling differs from canonical: %d' % len(todo))
    plan = collections.Counter((old, new) for _r, old, new in todo)
    for (old, new), n in plan.most_common():
        print('     %-10r -> %-10r  %5d rows' % (old, new, n))
    bysurf = collections.Counter(str(r.get('surface')) for r, _o, _n in todo)
    print('  by surface: %s' % dict(bysurf.most_common()))

    untouched = {v: n for v, n in before_vals.items()
                 if CANON.get(v.strip().lower()) in (None, v)}
    print('')
    print('  LEFT ALONE (distinct states, not spellings):')
    for v, n in sorted(untouched.items(), key=lambda z: -z[1]):
        print('     %-20r %6d' % (v, n))

    if not todo:
        print('')
        print('  nothing to normalise.')
        return 0
    if not a.apply:
        print('')
        print('  DRY RUN — nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    for r, _old, new in todo:
        _x, st = _req('PATCH', 'public_receipts',
                      {'id': 'eq.%s' % r.get('id')},
                      {'result': new}, {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
    print('')
    print('  rewrote %d, failed %d' % (ok, fail))

    after = fetch_all('public_receipts')
    t1 = tally(after)
    vals = collections.Counter(str(r.get('result')) for r in after
                               if r.get('result') is not None)
    left = sum(n for v, n in vals.items()
               if CANON.get(v.strip().lower()) not in (None, v))
    print('  distinct result values now: %d (was %d)'
          % (len(vals), len(before_vals)))
    print('  rows still off-canonical: %d (was %d)' % (left, len(todo)))
    print('  W-L-P after: %d-%d-%d  (before %d-%d-%d)'
          % (t1['W'], t1['L'], t1['P'], t0['W'], t0['L'], t0['P']))
    if (t1['W'], t1['L'], t1['P']) != (t0['W'], t0['L'], t0['P']):
        print('  ABORT-LEVEL: the RECORD moved. This was a spelling change '
              'only — a changed W-L-P means a result was altered.')
        return 1
    if ok and left >= len(todo):
        print('  ABORT-LEVEL: writes succeeded but nothing moved.')
        return 1
    print('  record unchanged, as it must be — spelling only.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
