#!/usr/bin/env python3
"""Dawg of the Day, every sport, from the engine's own priced reads.

== WHY (2026-10-28) ========================================================
Andy: "Wiring all sports into Dawg of the Day and ensuring that if its plus
odds it waulifies as Dawg so if engine like a -1.5 Puckline or other spread
thats pluss odds that should qualify as dawg as well right?"

generate_dawg_of_day.py is hardcoded to `baseball_mlb`, moneyline only
(MIN_ML_PRICE/MAX_ML_PRICE), and `daily_dawg.game_date` was the PRIMARY KEY,
so a multi-sport Dawg could not be stored at all. The surface has been dark
since 2026-10-18: MLB is down to one World Series game a night with no
qualifying dog. Its all-time record is 60-68-4 and, with no price column,
has never been expressible in units.

This writer is NEW and leaves the MLB generator alone. It sources from
`jerry_reads` — the engine's own published call, already priced by
price_picks.py — so a Dawg is by construction a play we actually made.

== ANDY'S RULE, AND WHAT THE EVIDENCE SAYS ABOUT IT =======================
By price, a plus-odds spread IS a dog, so it qualifies. But the two
populations are opposites. Every plus-odds pick we have ever published:

    market       n     W    L    hit%     units    breakeven
    ML          80    42   38   52.5%   +12.44u      46.7%
    SPREAD/PL   21     6   15   28.6%    -7.94u      46.5%
    NHL spread alone  17   4-13  23.5%    -8.39u

A -1.5 puckline at +130 is not an underdog; it is a FAVOURITE at a long
price, which is a different bet with a different hit rate. So the rule is
honoured and ordered: a plus-odds ML wins the slot when one exists, and a
plus-odds spread takes it only when no ML candidate does. `market` is stored
on the row so the record splits instead of blending a 52.5% population with a
28.6% one (feedback_ml_vs_rl_conflation).

Backtested on every priced read in the band, one pick per sport per day:

    all            n=38   20W-18L   52.6%   +7.27u   median +115, BE 46.5%
      ml half      n=29   16W-13L   55.2%   +7.09u
      rl half      n= 9    4W- 5L   44.4%   +0.18u
    MLB 8-3   NBA 2-1   NFL 2-1   NHL 7-8   NCAAF 1-5

Thin (n=38, CI straddles) and stated as thin. The one-per-DAY variant --
single best candidate across all sports -- measures better at 15-11, 57.7%,
+8.57u on n=26, and is available with --one-per-day if the surface should
stay singular. NCAAF at 1-5 is the weakest sport and carries no exclusion
here: six games is not grounds for banning a sport, and the per-sport record
will say so soon enough either way.

    python generate_dawg_multisport.py                 # today, dry
    python generate_dawg_multisport.py --apply
    python generate_dawg_multisport.py --date 2026-10-27 --apply
    python generate_dawg_multisport.py --one-per-day --apply
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

#: The band the legacy MLB generator used, kept deliberately. Below +100 is
#: not a dog; above +250 the books have historically known more than the
#: model does.
MIN_PRICE = 100
MAX_PRICE = 250
PLAYABLE_MARKETS = ('ml', 'rl')


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
        return e.read().decode('utf-8', 'replace')[:240], e.code


def et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=4)).date().isoformat()


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def preflight() -> bool:
    """The new columns must exist, or every write silently drops them."""
    rows, st = req('GET', 'daily_dawg',
                   {'select': 'sport,market,price_american', 'limit': '1'})
    if st == 200:
        return True
    print('  !! daily_dawg is missing the 20261028a columns '
          '(sport / market / price_american).')
    print('     Paste supabase/migrations/20261028a_dawg_multisport_and_price'
          '.sql into the SQL editor and run it, then re-run this.')
    print('     probe: HTTP %s %s' % (st, str(rows)[:160]))
    return False


def candidates(gd: str) -> list:
    rows, st = req('GET', 'jerry_reads',
                   {'game_date': 'eq.%s' % gd,
                    'select': 'id,sport,game_id,call_text,call_market,'
                              'call_side,call_line,conviction,price_american,'
                              'short_read,input_snapshot->>matchup,'
                              # the tier the gate ACTUALLY applied, from the
                              # frozen snapshot. Present on 88% of recent
                              # reads; NULL is written when it is absent
                              # rather than minting one from conviction,
                              # which is a per-sport scale.
                              'snap_tier:input_snapshot->primary_play->>tier',
                    'order': 'conviction.desc'})
    if not isinstance(rows, list):
        print('  read failed (HTTP %s)' % st)
        return []
    out = []
    for r in rows:
        mkt = str(r.get('call_market') or '').lower()
        if mkt not in PLAYABLE_MARKETS:
            continue
        if not r.get('call_text'):
            continue
        p = _i(r.get('price_american'))
        # A NULL price is not a near miss. Dawg is a plus-odds surface and an
        # unpriced row cannot be shown to be one, so it is not a candidate.
        if p is None or p < MIN_PRICE or p > MAX_PRICE:
            continue
        out.append(r)
    return out


def rank(cands: list) -> list:
    """ML first, then conviction. See the module docstring for why."""
    return sorted(cands,
                  key=lambda z: (0 if str(z.get('call_market')).lower() == 'ml'
                                 else 1,
                                 -(_i(z.get('conviction')) or 0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--one-per-day', action='store_true',
                    help='a single Dawg across all sports (measures better: '
                         '57.7%% +8.57u on n=26 vs 52.6%% +7.27u on n=38)')
    a = ap.parse_args()
    gd = a.date or et_today()

    print('=' * 92)
    print('DAWG OF THE DAY · ALL SPORTS · %s  (%s)'
          % (gd, 'APPLY' if a.apply else 'DRY RUN'))
    print('  plus odds %+d..%+d, any market; ML preferred over spread'
          % (MIN_PRICE, MAX_PRICE))
    print('=' * 92)

    if not preflight():
        return 1

    cands = candidates(gd)
    print('  priced plus-odds reads in band: %d' % len(cands))
    if not cands:
        print('  no Dawg today. Nothing written — an empty slate is not a '
              'reason to publish a minus-odds pick.')
        return 0

    by = collections.defaultdict(list)
    for c in cands:
        by[str(c.get('sport'))].append(c)
    print('  by sport: %s' % {k: len(v) for k, v in sorted(by.items())})

    if a.one_per_day:
        best = rank(cands)[:1]
    else:
        best = [rank(v)[0] for _k, v in sorted(by.items())]

    print('')
    print('  %-7s %-30s %-6s %-6s %7s %6s' % ('sport', 'pick', 'market',
                                              'side', 'price', 'conv'))
    print('  ' + '-' * 70)
    for c in best:
        print('  %-7s %-30s %-6s %-6s %+7d %6s'
              % (c.get('sport'), str(c.get('call_text'))[:30],
                 c.get('call_market'), c.get('call_side'),
                 _i(c.get('price_american')), c.get('conviction')))
    mk = collections.Counter(str(c.get('call_market')).lower() for c in best)
    print('  markets: %s' % dict(mk))
    if mk.get('rl'):
        print('  note: %d spread Dawg(s). Plus-odds spreads are 6-15 '
              '(-7.94u) historically — recorded under market=rl so they '
              'build their own record.' % mk['rl'])

    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    wrote = failed = 0
    for c in best:
        row = {
            'sport': c.get('sport'),
            'game_date': gd,
            'game_id': c.get('game_id'),
            'matchup': c.get('matchup'),
            'team': c.get('call_text'),
            'market': str(c.get('call_market') or '').lower(),
            'pick_side': c.get('call_side'),
            'pick_line': c.get('call_line'),
            'price_american': _i(c.get('price_american')),
            'conviction': _i(c.get('conviction')),
            # ══ 2026-10-28b · NO INVENTED TIER, NO INVENTED EDGE ══
            # spread_delta and tier were NOT NULL (both are MLB-generator
            # concepts) and the first --apply failed 23502 on them. 20261028b
            # drops both NOT NULLs rather than letting this writer fabricate:
            # spread_delta 0.0 would read as "model edge 0.0" on a play
            # chosen for its PRICE, and a tier derived from conviction would
            # be a baseball scale in another sport's jersey -- conviction 70+
            # is 70.0% / +85.65u on MLB and 50.3% / -14.75u on NCAAF.
            'tier': c.get('snap_tier'),
            'spread_delta': None,
            'narrative': c.get('short_read'),
            'signals': {'source': 'jerry_reads',
                        'jerry_read_id': c.get('id'),
                        'selected_by': 'generate_dawg_multisport',
                        'rule': ('plus odds %+d..%+d, ML preferred over '
                                 'spread' % (MIN_PRICE, MAX_PRICE))},
        }
        body, st = req('POST', 'daily_dawg?on_conflict=sport,game_date',
                       None, [row],
                       {'Prefer': 'resolution=merge-duplicates,'
                                  'return=minimal'})
        if st in (200, 201, 204):
            wrote += 1
        else:
            failed += 1
            print('  !! write failed for %s (%s): %s'
                  % (c.get('sport'), st, str(body)[:160]))

    print('')
    print('  wrote %d, failed %d' % (wrote, failed))
    back, _st = req('GET', 'daily_dawg',
                    {'game_date': 'eq.%s' % gd,
                     'select': 'sport,team,market,price_american'})
    print('  read back: %s'
          % [(r.get('sport'), r.get('market'), r.get('price_american'))
             for r in (back or [])])
    if wrote and not back:
        print('  ABORT-LEVEL: writes returned OK and the rows are not there.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
