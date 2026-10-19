#!/usr/bin/env python3
"""Do our published picks BEAT THE CLOSE? The market-confirmed edge test. Read-only.

== 2026-10-18 - WHY THIS IS THE TEST THAT MATTERS ==
Everything else measured outcomes, which are noisy: a 2-8-1 Sunday and a 10-2
Sunday can come from the same process. Closing line value does not have that
problem. If we take a side at +7 and it closes +6, the market moved our way
and we were early on real information. CLV converges on true edge far faster
than win rate, which is why books grade their own customers on it.

It also answers Andy's question directly. "Game picks need to be better" has
two very different causes:
  * we pick the right side and lose anyway  -> variance, keep going
  * we pick the side the market moves AWAY from -> we are systematically on
    the wrong side, and no amount of tier tuning fixes it

CLV separates those two, and nothing else we have does.

METHOD
  * our number = pick_line on the published receipt (what the user saw)
  * the close  = last pregame capture in line_history for that game
  * CLV, in points, is signed so POSITIVE always means we got the better
    number, for either side of the bet
  * reported per sport and per surface, with the share of picks that beat the
    close, and a sign test (if we had no edge, we would beat the close about
    half the time)

A pick with no stored line, or a game with no line history, is COUNTED AND
REPORTED as missing rather than dropped silently - the share of picks we
cannot grade for CLV is itself a finding.

USAGE
    python test_closing_line_value.py
    python test_closing_line_value.py --sport NFL
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
SCRATCH = os.environ.get('SCRATCH_DIR') or os.environ.get('TMPDIR') or '.'


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def page(t, p):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, timeout=240,
                         params=dict(p, limit='1000', offset=str(off)))
        if r.status_code not in (200, 206):
            print(f'  !! {t} HTTP {r.status_code} {r.text[:170]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def binom_p(k, n):
    """Two-sided sign test against p=0.5."""
    if n == 0:
        return 1.0
    k = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    a = ap.parse_args()

    # closing lines from the cached line-movement extract (home-side handicap)
    close_by_game = {}
    for sp in ('NCAAF', 'NFL', 'NHL'):
        p = os.path.join(SCRATCH, f'linefeat_{sp}_spread.json')
        if not os.path.exists(p):
            continue
        with open(p, encoding='utf-8') as fh:
            d = json.load(fh)
        for gid, v in d.items():
            close_by_game[(sp, str(gid))] = v
    print(f'closing lines available for {len(close_by_game)} game/sport keys')
    if not close_by_game:
        print('  run extract_line_features.py first')
        return 1

    rec = page('public_receipts', {'select': 'sport,surface,market,game_id,'
                                            'game_date,pick_side,pick_line,'
                                            'pick_label,tier,result'})
    rec = [x for x in rec
           if str(x.get('market')) in ('rl', 'spread', 'ats', 'puckline')]
    if a.sport:
        rec = [x for x in rec if str(x.get('sport')) == a.sport]
    print(f'spread-market receipts: {len(rec)}')

    rows = []
    miss_line = miss_hist = 0
    for x in rec:
        sp = str(x.get('sport'))
        ours = f(x.get('pick_line'))
        if ours is None:
            import re as _re
            m = _re.search(r'([+-]\d+(?:\.\d+)?)', str(x.get('pick_label') or ''))
            ours = float(m.group(1)) if m else None
        if ours is None:
            miss_line += 1
            continue
        lh = close_by_game.get((sp, str(x.get('game_id'))))
        if not lh:
            miss_hist += 1
            continue
        close_home = f(lh.get('close_line'))
        if close_home is None:
            miss_hist += 1
            continue
        side = str(x.get('pick_side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            # ══ 2026-10-19 · DERIVE IT, THE COLUMN IS IMMUTABLE ══
            # pick_side is NULL on all 65 sharp_card and all 41 sweat_card
            # spread receipts, and public_receipts REFUSES to fill it: a PATCH
            # returns HTTP 200 with a body showing the value unchanged, then
            # re-reads as NULL. Only `result` is writable. So the history
            # cannot be repaired and the ANALYSIS has to be robust instead -
            # the label plus the matchup carry the side, and the same helper
            # the writer now uses can recover it here.
            from public_receipt import _side_for_receipt
            side = str(_side_for_receipt({'label': x.get('pick_label'),
                                          'game': x.get('matchup')},
                                         str(x.get('market')), None)
                       or '').upper()
            if side not in ('HOME', 'AWAY'):
                continue
        # our number for the side we took, in that side's own handicap terms
        close_ours = close_home if side == 'HOME' else -close_home
        # POSITIVE clv = we got a better number than the close, either side
        clv = ours - close_ours
        rows.append({'sport': sp, 'surface': str(x.get('surface')),
                     'side': side, 'ours': ours, 'close': close_ours,
                     'clv': clv, 'tier': str(x.get('tier') or ''),
                     'result': str(x.get('result') or ''),
                     'label': str(x.get('pick_label') or '')[:30]})
    print(f'  CLV computable on {len(rows)}   '
          f'no stored pick_line: {miss_line}   no line history: {miss_hist}')
    if not rows:
        print('  nothing to measure')
        return 0

    def rep(name, v):
        if len(v) < 10:
            print(f'  {name:<26}n={len(v)} - too thin to quote')
            return
        clvs = [r['clv'] for r in v]
        beat = sum(1 for c in clvs if c > 0)
        tie = sum(1 for c in clvs if c == 0)
        n_dec = len(clvs) - tie
        p = binom_p(beat, n_dec) if n_dec else 1.0
        print(f'  {name:<26}n={len(v):<5}mean CLV {st.fmean(clvs):+6.2f} pts   '
              f'median {st.median(clvs):+5.2f}   beat close {beat}/{n_dec} '
              f'({100*beat/max(n_dec,1):.0f}%)  ties {tie}  sign-test p={p:.4f}')

    print('\n== CLOSING LINE VALUE, by sport ==')
    print('   POSITIVE mean CLV = we published a better number than the close')
    by = defaultdict(list)
    for r in rows:
        by[r['sport']].append(r)
    for sp in sorted(by):
        rep(sp, by[sp])
    print('\n== by sport x surface ==')
    by2 = defaultdict(list)
    for r in rows:
        by2[(r['sport'], r['surface'])].append(r)
    for k in sorted(by2):
        rep(f'{k[0]} / {k[1]}', by2[k])
    print('\n== by side taken ==')
    by3 = defaultdict(list)
    for r in rows:
        by3[(r['sport'], r['side'])].append(r)
    for k in sorted(by3):
        rep(f'{k[0]} / {k[1]}', by3[k])

    print('\n== does CLV predict the RESULT? (the sanity link) ==')
    g = [r for r in rows if r['result'].upper() in ('WIN', 'W', 'LOSS', 'L')]
    if len(g) >= 20:
        pos = [r for r in g if r['clv'] > 0]
        neg = [r for r in g if r['clv'] < 0]
        for nm, v in (('CLV > 0 (we beat close)', pos),
                      ('CLV < 0 (close beat us)', neg)):
            if len(v) < 10:
                print(f'  {nm:<26}n={len(v)} - too thin')
                continue
            w = sum(1 for r in v if r['result'].upper() in ('WIN', 'W'))
            lo, hi = ML_wilson(w, len(v))
            print(f'  {nm:<26}{w}-{len(v)-w}  {100*w/len(v):.1f}%  '
                  f'CI[{lo:.1f},{hi:.1f}]  n={len(v)}')
    else:
        print(f'  only {len(g)} graded - too thin')
    return 0


def ML_wilson(w, n):
    if not n:
        return (0.0, 0.0)
    z = 1.96
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


if __name__ == '__main__':
    sys.exit(main())
