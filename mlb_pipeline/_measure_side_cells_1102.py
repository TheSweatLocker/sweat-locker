"""Which SIDES cells earn a card slot? Same three rules the props got.

== 2026-11-02 - WHY =======================================================
ANDY: "we are getting detroyed in football ... the product outright sucks".

Item 3 of the plan: stop publishing football volume that has no measured
edge, and let card composition draw from cells with a record instead of from
`tier`. prop_cell_allowlist.py did this for props on 11-01. This is the
measurement for sides.

Football receipts since 09-01, priced and graded, ROI at each row's OWN odds:

    NFL    prop_jerry  prop   2812  50.7%   -4.2%
    NCAAF  game_read   rl      194  49.5%   -6.1%
    NCAAF  game_read   ml      149  63.8%   +1.0%
    NFL    game_read   rl       53  45.3%  -13.2%
    NFL    game_read   ml       43  67.4%  +10.8%
    NCAAF  sweat_card  rl       33  42.4%  -19.0%
    NFL    sharp_card  rl       14  35.7%  -32.2%

Spreads lose almost everywhere; moneylines are the positive side. But a
moneyline hit rate is not an edge on its own, so every cell here is judged
on ROI at real prices, never on hit rate, and never at flat -110
(feedback_flat_110_inflates_every_ml_figure: ML books average -120..-203, so
breakeven is 56-58%, not 52.38%).

THE THREE RULES, same as the prop allowlist:
  1. n >= 30 graded rows carrying a real price
  2. ROI > 0 at each row's own odds
  3. positive in BOTH date halves, >= 8 rows each half

AND A CONTROL, which the props did not need and sides absolutely do. The
engine has already been measured losing to a one-word rule on football
spreads (45.8% vs 72.9% for "take the away team",
project_engine_loses_to_a_coin_flip_1026). So for every cell that passes,
this also prints what the DUMBEST rule returns on the same games: take the
favourite, take the dog. A cell that cannot beat "take the favourite" is not
an edge, it is a restatement of the price
(feedback_signal_needs_a_direction_control).

  python _measure_side_cells_1102.py
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

for _l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}

MIN_N = 30
MIN_HALF = 8
SIDE_MARKETS = ('ml', 'rl', 'spread', 'total')


def page(table, params):
    out, off = [], 0
    while off < 200000:
        r = requests.get(SB + '/rest/v1/' + table, headers=H, timeout=180,
                         params=dict(params, limit=1000, offset=off))
        if r.status_code != 200:
            print('  ! %s HTTP %s %s' % (table, r.status_code, r.text[:200]))
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000
    return out


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def unit(res, odds):
    """Units won/lost at the row's OWN price. None = not measurable."""
    o = fl(odds)
    if o is None:
        return None
    r = str(res or '').upper()
    if r == 'WIN':
        return (o / 100.0) if o > 0 else (100.0 / abs(o))
    if r == 'LOSS':
        return -1.0
    return None                     # PUSH/VOID/NO_ACTION -> excluded


def summarise(rows):
    w = sum(1 for r in rows if str(r['result']).upper() == 'WIN')
    l = sum(1 for r in rows if str(r['result']).upper() == 'LOSS')
    u = sum(r['_u'] for r in rows)
    n = w + l
    return {'n': n, 'w': w, 'l': l,
            'hit': (100.0 * w / n if n else 0.0), 'u': u,
            'roi': (100.0 * u / n if n else 0.0)}


def main() -> int:
    rows = page('public_receipts',
                {'select': 'sport,surface,market,tier,conviction,result,'
                           'pick_odds,pick_line,pick_side,game_date,'
                           'pick_label,game_id',
                 'game_date': 'gte.2026-08-01',
                 'market': 'in.(%s)' % ','.join(SIDE_MARKETS)})
    good = []
    for r in rows:
        u = unit(r.get('result'), r.get('pick_odds'))
        if u is None:
            continue
        r['_u'] = u
        good.append(r)
    print('side receipts: %d fetched, %d graded WITH a real price'
          % (len(rows), len(good)))
    if not good:
        print('  0 rows is a WIRING ERROR, not a finding')
        return 1
    dates = sorted({str(r['game_date']) for r in good})
    mid = dates[len(dates) // 2]
    print('dates %s .. %s, split at %s' % (dates[0], dates[-1], mid))
    print()

    # ── the control: blanket price-following on the same population ────
    print('== CONTROL: what the price alone returns, per sport+market ====')
    print('%-6s %-7s %6s %8s %8s %9s   %s'
          % ('sport', 'market', 'n', 'W-L', 'hit', 'ROI', 'note'))
    ctrl = {}
    by_sm = collections.defaultdict(list)
    for r in good:
        by_sm[(r['sport'], r['market'])].append(r)
    for k, rs in sorted(by_sm.items()):
        favs = [r for r in rs if (fl(r.get('pick_odds')) or 0) < 0]
        dogs = [r for r in rs if (fl(r.get('pick_odds')) or 0) > 0]
        sf, sd = summarise(favs), summarise(dogs)
        ctrl[k] = {'fav': sf, 'dog': sd}
        if sf['n'] >= MIN_N:
            print('%-6s %-7s %6d %8s %7.1f%% %+8.1f%%   priced as FAVOURITE'
                  % (k[0], k[1], sf['n'], '%d-%d' % (sf['w'], sf['l']),
                     sf['hit'], sf['roi']))
        if sd['n'] >= MIN_N:
            print('%-6s %-7s %6d %8s %7.1f%% %+8.1f%%   priced as DOG'
                  % (k[0], k[1], sd['n'], '%d-%d' % (sd['w'], sd['l']),
                     sd['hit'], sd['roi']))
    print()

    # ── the cells ──────────────────────────────────────────────────────
    cells = collections.defaultdict(list)
    for r in good:
        cells[(r['sport'], r['market'], str(r.get('tier') or 'NONE').upper())
              ].append(r)

    # ── SPLIT PER CELL, BY ROW COUNT, NOT BY THE GLOBAL MEDIAN DATE ──
    # The first version of this split every cell at the median DISTINCT
    # DATE across all receipts (2026-09-17). Receipts are concentrated in
    # October, so that left the early half EMPTY for most cells and they
    # were rejected as "fails split-half" with `early n=0` -- an artifact
    # of the split, not a property of the cell. A balanced split puts half
    # the cell's own rows on each side, which is what the test means.
    def halves(rs):
        rs = sorted(rs, key=lambda r: str(r['game_date']))
        if len(rs) < 2 * MIN_HALF:
            return None, None
        cut = len(rs) // 2
        # do not cut inside a date: both halves must be time-separable
        d = str(rs[cut]['game_date'])
        while cut < len(rs) and str(rs[cut]['game_date']) == d:
            cut += 1
        if cut >= len(rs) or cut < MIN_HALF:
            return None, None
        return rs[:cut], rs[cut:]

    passed, failed = [], []
    for key, rs in cells.items():
        s = summarise(rs)
        if s['n'] < MIN_N:
            continue
        eh, lh = halves(rs)
        if eh is None:
            e = l = {'n': 0, 'w': 0, 'l': 0, 'hit': 0.0, 'u': 0.0,
                     'roi': 0.0}
            both = False
        else:
            e, l = summarise(eh), summarise(lh)
            both = (e['roi'] > 0 and l['roi'] > 0
                    and e['n'] >= MIN_HALF and l['n'] >= MIN_HALF)
        rec = (key, s, e, l, both)
        (passed if (s['roi'] > 0 and both) else failed).append(rec)

    passed.sort(key=lambda z: -z[1]['roi'])
    failed.sort(key=lambda z: -z[1]['roi'])

    print('== CELLS FIT TO PUBLISH (n>=%d, ROI>0, positive BOTH halves) ==='
          % MIN_N)
    print('%-6s %-7s %-9s %6s %8s %8s %9s %11s %11s'
          % ('sport', 'market', 'tier', 'n', 'W-L', 'hit', 'ROI',
             'early', 'late'))
    if not passed:
        print('  NONE')
    for key, s, e, l, _b in passed:
        print('%-6s %-7s %-9s %6d %8s %7.1f%% %+8.1f%% %+6.1f%% n=%-3d %+6.1f%% n=%-3d'
              % (key[0], key[1], key[2], s['n'], '%d-%d' % (s['w'], s['l']),
                 s['hit'], s['roi'], e['roi'], e['n'], l['roi'], l['n']))
        c = ctrl.get((key[0], key[1]), {})
        for lab in ('fav', 'dog'):
            cc = c.get(lab) or {}
            if cc.get('n', 0) >= MIN_N:
                verdict = ('BEATS it' if s['roi'] > cc['roi']
                           else 'LOSES to it')
                print('          vs blanket %-10s %+7.1f%% n=%-4d -> cell %s'
                      % (lab.upper(), cc['roi'], cc['n'], verdict))

    print()
    print('== REJECTED (n>=%d) ==========================================='
          % MIN_N)
    print('%-6s %-7s %-9s %6s %8s %9s %-18s %11s %11s'
          % ('sport', 'market', 'tier', 'n', 'hit', 'ROI', 'why',
             'early', 'late'))
    for key, s, e, l, _b in failed:
        why = ('ROI<=0' if s['roi'] <= 0
               else ('not time-separable' if e['n'] == 0
                     else 'fails split-half'))
        print('%-6s %-7s %-9s %6d %7.1f%% %+8.1f%% %-18s %+6.1f%% n=%-3d %+6.1f%% n=%-3d'
              % (key[0], key[1], key[2], s['n'], s['hit'], s['roi'], why,
                 e['roi'], e['n'], l['roi'], l['n']))

    print()
    print('== TIER ROLL-UP per sport+market (does tier rank anything?) ====')
    for (sp, mk), rs in sorted(by_sm.items()):
        if len(rs) < MIN_N:
            continue
        line = []
        for t in ('PRIME', 'STRONG', 'LEAN', 'LIGHT', 'COVERAGE'):
            ts = summarise([r for r in rs
                            if str(r.get('tier') or '').upper() == t])
            if ts['n']:
                line.append('%s %+.1f%% n=%d' % (t[:4], ts['roi'], ts['n']))
        if line:
            print('  %-6s %-7s %s' % (sp, mk, '  '.join(line)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
