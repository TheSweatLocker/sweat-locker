"""Did the lr_v1 override stop erasing the attribution trail?

project_lr_v1_override_erases_attribution_1027: a second engine rewrote
primary_play on 457 picks and, because both override paths build a FRESH
dict, dropped `_ensemble_sources` -- the per-signal record the weekly
attribution cadence runs on. 63% of NHL picks became unattributable.

_carry_ensemble_audit was added on 10-27 and is called at both `_pre_lr`
sites. This checks the LIVE ROWS rather than the code: of the picks the
override touched, how many still carry their signal record?

Split BEFORE vs ON-OR-AFTER 10-27 so the fix is measured against its own
baseline instead of a pooled number that hides it.

USAGE
  python _verify_lr_attribution_1031.py
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
CTX = {'NHL': 'nhl_game_context', 'NFL': 'nfl_game_context',
       'NCAAF': 'ncaaf_game_context', 'NBA': 'nba_game_context',
       'MLB': 'mlb_game_context'}
FIX_DATE = '2026-10-27'          # _carry_ensemble_audit landed


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                      # noqa: BLE001
            return None
    return v if isinstance(v, (dict, list)) else None


def main() -> int:
    print('=== lr_v1 override: is the signal record surviving? ===')
    print('  _carry_ensemble_audit landed %s; split there.' % FIX_DATE)
    rows = collections.Counter()
    examples = []
    for sport, tbl in CTX.items():
        got, off = [], 0
        while off < 8000:
            r = requests.get(SB + '/rest/v1/' + tbl, headers=H, timeout=120,
                             params={'select': 'game_id,game_date,'
                                               'primary_play',
                                     'game_date': 'gte.2026-09-15',
                                     'limit': 1000, 'offset': off,
                                     'order': 'game_date.asc'})
            if r.status_code != 200:
                print('  ! %s HTTP %s %s' % (tbl, r.status_code,
                                             r.text[:120]))
                break
            b = r.json()
            if not b:
                break
            got.extend(b)
            off += 1000
        for x in got:
            pp = jd(x.get('primary_play'))
            if not isinstance(pp, dict):
                continue
            if pp.get('_pre_lr') is None:
                continue              # the override did not touch this pick
            era = ('after ' if str(x.get('game_date')) >= FIX_DATE
                   else 'before')
            has = pp.get('_ensemble_sources') is not None
            rows[(sport, era, has)] += 1
            if era == 'after ' and not has and len(examples) < 8:
                examples.append((sport, x.get('game_date'), x.get('game_id'),
                                 pp.get('label')))

    print('\n  %-6s %-7s %8s %8s %8s' % ('sport', 'era', 'kept', 'erased',
                                         'kept%'))
    tot = collections.Counter()
    for sport in CTX:
        for era in ('before', 'after '):
            k = rows[(sport, era, True)]
            e = rows[(sport, era, False)]
            if not (k + e):
                continue
            tot[(era, 'kept')] += k
            tot[(era, 'erased')] += e
            print('  %-6s %-7s %8d %8d %7.1f%%'
                  % (sport, era, k, e, 100.0 * k / (k + e)))
    print()
    for era in ('before', 'after '):
        k, e = tot[(era, 'kept')], tot[(era, 'erased')]
        if not (k + e):
            continue
        print('  TOTAL  %-7s %8d %8d %7.1f%%  (n=%d overridden picks)'
              % (era, k, e, 100.0 * k / (k + e), k + e))
    if examples:
        print('\n  still erased since the fix (first %d):' % len(examples))
        for sport, d, gid, lbl in examples:
            print('     %-6s %s %-34s %r' % (sport, d, str(gid)[:34], lbl))
    else:
        print('\n  no overridden pick since %s is missing its signal record'
              % FIX_DATE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
