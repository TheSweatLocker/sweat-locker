#!/usr/bin/env python3
"""NULL the read prices that are impossible for the market they sit on.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
A read priced as a MONEYLINE and then regenerated as a SPREAD kept the
moneyline price. "Philadelphia 76ers -12.5" stored -669, which is the median
of that game's home moneyline (ML ran -850..-721) while line_history holds
427 quotes for spread/home/-12.5 between -110 and -118.

The writer is fixed in sync_jerry_reads_from_ctx.py, which now clears the
price fields when (call_market, call_side, call_line) changes. This repairs
the rows already written.

PER-SPORT BANDS, MEASURED -- NOT A FLAT RULE
My first pass used a flat -140..+120 and flagged 55 rows. 32 of those were
LEGITIMATE: MLB's rl is a run line and NHL's is a puckline, both fixed at
+-1.5, so their prices legitimately range far wider than a football or
basketball spread where the line moves and the price stays near -110.
Measured from line_history (p1 to p99 of actual spread quotes):

    NCAAF   -122 .. +101        NHL   -285 .. +230   (puckline)
    NFL     -121 .. +102        MLB   -235 .. +190   (run line)
    NBA     -120 .. -101
    NCAAB   -118 .. +100

With sport-aware bands the real count is 23: NBA 15, NCAAF 8, and ZERO for
NHL, NFL and MLB. Repairing against the flat band would have destroyed 32
correct prices.

THE EVIDENCE, re-tested on the corrected set:
    implausible price   regenerated AFTER pricing: 23 of 23  (100%)
    plausible [CONTROL] regenerated AFTER pricing: 148 of 361 (41%)
    and 23 of 23 implausible prices sit in moneyline range
Regen is common; an impossible price ALWAYS followed one. That separation is
why the writer fix gates on the market changing rather than on regen alone.

SAFETY
  * the six price columns only. call_market/side/line/text, conviction,
    result and every graded field are untouched.
  * only rows OUTSIDE that sport's measured band. A price inside it is left
    alone even if the read was regenerated.
  * NULL rather than a guess: price_picks.py re-prices a NULL on its next
    run, and a NULL is visibly absent where a stale number is not.
  * dry-run by default, and the remaining count is asserted afterwards.

    python repair_stale_read_prices.py
    python repair_stale_read_prices.py --apply
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

# p1..p99 of real spread quotes per sport, widened slightly for safety.
# MLB/NHL are run line / puckline at +-1.5 and genuinely price wide.
BANDS = {
    'NFL':   (-150, 130),
    'NCAAF': (-150, 130),
    'NBA':   (-150, 130),
    'NCAAB': (-150, 130),
    'MLB':   (-400, 350),
    'NHL':   (-400, 350),
}
DEFAULT_BAND = (-150, 130)
# totals are a straight -110 market in every sport
TOTAL_BAND = (-170, 150)
PRICE_COLS = ('price_american', 'price_implied', 'price_best',
              'price_best_book', 'price_books_n', 'priced_at')


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
              % (e.code, path, e.read().decode('utf-8', 'replace')[:200]))
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


def band_for(sport, market):
    m = str(market or '').lower()
    if m == 'total':
        return TOTAL_BAND
    if m in ('rl', 'spread'):
        return BANDS.get(str(sport or '').upper(), DEFAULT_BAND)
    return None          # ml and anything else: no band, never flagged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('=' * 84)
    print('REPAIR STALE READ PRICES  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('  bands are PER SPORT and measured; MLB/NHL rl is a run line /')
    print('  puckline and prices wide, so a flat rule would destroy 32 good')
    print('  prices')
    print('=' * 84)

    reads = fetch_all('jerry_reads')
    print('  jerry_reads rows: %d' % len(reads))

    bad = []
    for r in reads:
        p = f(r.get('price_american'))
        if p is None:
            continue
        b = band_for(r.get('sport'), r.get('call_market'))
        if b is None:
            continue
        if p < b[0] or p > b[1]:
            bad.append((r, p, b))

    print('  prices impossible for their market: %d' % len(bad))
    if not bad:
        print('  nothing to repair.')
        return 0
    print('  by sport:  %s'
          % dict(collections.Counter(str(r.get('sport'))
                                     for r, _p, _b in bad).most_common()))
    print('  by market: %s'
          % dict(collections.Counter(str(r.get('call_market'))
                                     for r, _p, _b in bad).most_common()))
    print('')
    print('  %-6s %-10s %-30s %9s %-16s' % ('sport', 'market', 'call',
                                            'price', 'allowed band'))
    print('  ' + '-' * 78)
    for r, p, b in sorted(bad, key=lambda z: z[1]):
        print('  %-6s %-10s %-30s %9.0f [%d, %d]'
              % (r.get('sport'), r.get('call_market'),
                 str(r.get('call_text'))[:30], p, b[0], b[1]))

    if not a.apply:
        print('')
        print('  DRY RUN — nothing written. Re-run with --apply.')
        return 0

    body = {c: None for c in PRICE_COLS}
    ok = fail = 0
    for r, _p, _b in bad:
        _x, st = _req('PATCH', 'jerry_reads', {'id': 'eq.%s' % r.get('id')},
                      body, {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
    print('')
    print('  cleared %d, failed %d' % (ok, fail))

    after = fetch_all('jerry_reads', select='sport,call_market,price_american')
    left = 0
    for r in after:
        p = f(r.get('price_american'))
        if p is None:
            continue
        b = band_for(r.get('sport'), r.get('call_market'))
        if b and (p < b[0] or p > b[1]):
            left += 1
    print('  prices still outside their band: %d (was %d)' % (left, len(bad)))
    if ok and left >= len(bad):
        print('  ABORT-LEVEL: writes succeeded but nothing moved.')
        return 1
    print('  price_picks.py will re-price these on its next run.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
