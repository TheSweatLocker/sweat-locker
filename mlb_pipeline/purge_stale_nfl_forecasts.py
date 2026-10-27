#!/usr/bin/env python3
"""NULL the NFL wind/temp forecasts that were written beyond the model horizon.

══ WHY (2026-10-26) ════════════════════════════════════════════════════════
Open-Meteo forecasts 16 days out. A July seeding run wrote wind for
September games anyway -- `nfl_weather_pull.py` claimed "<=7 days out" in
its docstring and nothing enforced it (now fixed with a lead-time guard).

Those rows are not merely imprecise, they are ANTI-CORRELATED with what
happened. Measured on the 46 outdoor games carrying both a stored forecast
and an observed reading:

    lead time             n    mean |err|   corr(forecast, observed)
    0-2 days before      13       3.38          +0.531     usable
    17+ days             19       5.05          -0.317     worse than nothing

19 of 46 -- 41% of the paired sample -- were stale. Examples:

    CLE @ TB   written 8 weeks early at 20 mph, blew  4 mph
    NYJ @ TEN  written 7 weeks early at 17 mph, blew  2 mph
    CHI @ CAR  written 7 weeks early at 12 mph, blew  2 mph (96-point game)

**This is why "the wind lens does not work on forecasts" measured false.**
The lens is validated on observed wind (55.8% walk-forward over 26 seasons,
CI[52.9,58.6], control side 49.3%); evaluating its bettable version against
these inputs tested the bug, not the lens. On fresh forecasts only the
direction holds at every cutoff (57-62% UNDER on n=5-8 -- far too small to
call, but no longer disproven).

WHAT IT DOES
Sets `wind` and `temp` to NULL on `nfl_game_context` rows where
`updated_at` is more than 16 days before `game_date`. Those values cannot
have come from a real forecast, so a NULL is the honest content -- and a
NULL is visibly absent, where a stale number silently poisons every
consumer.

Deliberately conservative: `updated_at` is the row's LAST write, so it only
proves staleness when it is itself long before kickoff. A recently-updated
row could still carry an old weather value and is left alone rather than
guessed at. This is a floor on the damage, not a complete cleanup.

NOT TOUCHED: scores, lines, results, or any column feeding a published
record. Weather only. Dry-run by default, and every write is read back.

    python purge_stale_nfl_forecasts.py
    python purge_stale_nfl_forecasts.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HORIZON_DAYS = 16          # Open-Meteo's own forecast limit


def _load_env(path):
    out = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_HERE = os.path.dirname(os.path.abspath(__file__))
_E = _load_env(os.path.join(_HERE, '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = _E.get('SUPABASE_KEY') or os.environ.get('SUPABASE_KEY')
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
       'Content-Type': 'application/json'}


def _req(method, path, params=None, body=None, extra=None):
    u = BASE + path
    if params:
        u += '?' + urllib.parse.urlencode(params)
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
        print('    HTTP %s on %s %s :: %s'
              % (e.code, method, path,
                 e.read().decode('utf-8', 'replace')[:300]))
        return None, e.code


def fetch_all(table):
    out, off = [], 0
    while True:
        rows, st = _req('GET', table, {'select': '*', 'limit': '1000',
                                       'offset': str(off)})
        if not isinstance(rows, list):
            print('    read failed (status %s)' % st)
            break
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('=' * 76)
    print('PURGE STALE NFL FORECASTS  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('  rule: wind/temp -> NULL where updated_at is more than %d days'
          % HORIZON_DAYS)
    print('        before game_date (beyond Open-Meteo\'s forecast horizon)')
    print('=' * 76)

    ctx = fetch_all('nfl_game_context')
    have = [c for c in ctx if c.get('wind') is not None]
    print('  ctx rows: %d   carrying a wind value: %d' % (len(ctx), len(have)))

    stale = []
    for c in have:
        try:
            gd = dt.date.fromisoformat(str(c.get('game_date'))[:10])
            wd = dt.date.fromisoformat(str(c.get('updated_at'))[:10])
        except (ValueError, TypeError):
            continue
        lead = (gd - wd).days
        if lead > HORIZON_DAYS:
            stale.append((c, lead))

    print('  provably stale: %d' % len(stale))
    if not stale:
        print('  nothing to purge.')
        return 0
    for c, lead in sorted(stale, key=lambda z: -z[1])[:12]:
        print('     %s %-4s @ %-4s  wind %-3s temp %-4s  written %dd early'
              % (str(c.get('game_date'))[:10], c.get('away_team'),
                 c.get('home_team'), c.get('wind'), c.get('temp'), lead))
    if len(stale) > 12:
        print('     ... and %d more' % (len(stale) - 12))

    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    for c, _lead in stale:
        gid = c.get('game_id')
        if not gid:
            continue
        _x, st = _req('PATCH', 'nfl_game_context',
                      {'game_id': 'eq.%s' % gid},
                      {'wind': None, 'temp': None},
                      {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
    print('')
    print('  nulled %d, failed %d' % (ok, fail))

    # assert the OUTPUT moved -- a green step may write nothing
    after = [c for c in fetch_all('nfl_game_context')
             if c.get('wind') is not None]
    print('  rows carrying a wind value now: %d (was %d)'
          % (len(after), len(have)))
    if ok and len(after) >= len(have):
        print('  ABORT-LEVEL: writes reported success but nothing moved.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
