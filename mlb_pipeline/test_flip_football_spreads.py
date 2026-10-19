#!/usr/bin/env python3
"""Would FLIPPING our football spread picks be a real edge? Read-only.

== 2026-10-19 - WHY ==
ANDY: "Why dont we flip football rl plays that would be aa solid record?"

Fair question: The Sharp's football spreads are 18-30 (37.5%, ROI -42.3%) and
the inverse of that is 62.5%. He asked the same thing about NHL sides in
October and then talked himself out of it for the right reason - "thats
saying we have a losing model so just fade us and hope for the best". So the
honest job is to find out whether the losing record is STABLE enough to
invert, not to agree or dismiss.

A flip is only a strategy if the badness persists. A 37.5% stretch from a
process that is really 50% inverts to 50% MINUS the vig, which loses. So the
test is not "is the record bad", it is "is it bad in the first half AND bad in
the second half, out of sample".

THREE THINGS THIS CONTROLS FOR, each from something already measured here:

  * LINE INTEGRITY. 48% of game_read spread picks carried a line NO book ever
    posted (audit_published_line_integrity). A flip measured on those is
    graded against fantasy numbers in both directions. So the curated
    surfaces - sweat_card, sharp_card, potd, where only 1 of 15 and 2 of 17
    were off - are reported SEPARATELY and are the ones to trust.
  * TEMPORAL SPLIT. Early half, then the late half it never saw.
  * THE PUBLIC OVERLAP. Our football spread picks are usually favourites, and
    so is the public. If flipping our pick is the SAME TRADE as fading the
    public, that is one edge with two names, not two edges - and it changes
    what we build.

USAGE
    python test_flip_football_spreads.py
"""
from __future__ import annotations

import math
import re
import sys
from collections import defaultdict

import requests

import market_line as ML
from public_receipt import _side_for_receipt

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
BREAKEVEN = 52.38
LINE_RX = re.compile(r'([+-]\d+(?:\.\d+)?)')
CURATED = ('sweat_card', 'sharp_card', 'potd')


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
            print(f'  !! {t} HTTP {r.status_code} {r.text[:130]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def wilson(w, n):
    if not n:
        return (0.0, 0.0)
    z = 1.96
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def rep(name, v, key='flip'):
    g = [r for r in v if r[key] in ('WIN', 'LOSS')]
    if not g:
        print(f'  {name:<46}n=0')
        return
    w = sum(1 for r in g if r[key] == 'WIN')
    n = len(g)
    lo, hi = wilson(w, n)
    roi = 100 * ((w * (100 / 110.0)) - (n - w)) / n
    tag = ('  n<30' if n < 30 else
           ('  ** BEATS **' if lo > BREAKEVEN else
            ('  LOSES' if hi < BREAKEVEN else '')))
    print(f'  {name:<46}{f"{w}-{n-w}":>9}{100*w/n:>7.1f}%  '
          f'CI[{lo:>5.1f},{hi:>5.1f}]  ROI {roi:>+6.1f}%  n={n}{tag}')


def main() -> int:
    # results keyed by (sport, away, home, date): the close lives here
    res = {}
    for sport, tbl in (('NFL', 'nfl_game_results'),
                       ('NCAAF', 'ncaaf_game_results')):
        for x in page(tbl, {'select': 'game_id,game_date,away_team,home_team,'
                                      'home_score,away_score,close_spread'}):
            if x.get('home_score') is None:
                continue
            res[(sport, str(x.get('away_team')), str(x.get('home_team')),
                 str(x.get('game_date'))[:10])] = x
    ctx = {}
    for sport, tbl in (('NFL', 'nfl_game_context'),
                       ('NCAAF', 'ncaaf_game_context')):
        for c in page(tbl, {'select': 'game_id,game_date,away_team,home_team'}):
            ctx[(sport, str(c.get('game_id')))] = c
    print(f'football results {len(res)}   context hashes {len(ctx)}')

    # public split per (sport, game hash, market) - last snapshot
    pub = {}
    for s in page('line_snapshot', {'select': 'sport,game_id,market,pick_side,'
                                              'bets_pct,snapshot_ts'}):
        if str(s.get('market')) not in ('rl', 'spread'):
            continue
        k = (str(s.get('sport')), str(s.get('game_id')))
        ts = str(s.get('snapshot_ts'))
        if k not in pub or ts > pub[k]['ts']:
            pub[k] = {'ts': ts, 'side': str(s.get('pick_side') or '').upper(),
                      'bets': f(s.get('bets_pct'))}
    print(f'games with a public spread split: {len(pub)}')

    rows = []
    for x in page('public_receipts', {'select': 'sport,surface,market,game_id,'
                                                'game_date,pick_side,pick_line,'
                                                'pick_label,result,tier'}):
        if str(x.get('sport')) not in ('NFL', 'NCAAF'):
            continue
        if str(x.get('market')) not in ('rl', 'spread', 'ats'):
            continue
        sport = str(x['sport'])
        c = ctx.get((sport, str(x.get('game_id'))))
        if not c:
            continue
        gm = res.get((sport, str(c.get('away_team')), str(c.get('home_team')),
                      str(c.get('game_date'))[:10]))
        if not gm:
            continue
        ours = f(x.get('pick_line'))
        if ours is None:
            m = LINE_RX.search(str(x.get('pick_label') or ''))
            ours = float(m.group(1)) if m else None
        if ours is None:
            continue
        side = str(x.get('pick_side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            side = str(_side_for_receipt(
                {'label': x.get('pick_label'),
                 'game': f"{c.get('away_team')} @ {c.get('home_team')}"},
                'rl', None) or '').upper()
        if side not in ('HOME', 'AWAY'):
            continue
        stored = str(x.get('result') or '').upper()
        if stored not in ('WIN', 'LOSS', 'W', 'L'):
            continue
        ourwin = stored.startswith('W')
        # the FLIP is the same bet on the other side at the same number, so
        # its result is simply the complement (a push would be a push, and a
        # push is already excluded above)
        flip = 'LOSS' if ourwin else 'WIN'
        p = pub.get((sport, str(x.get('game_id'))))
        on_public = None
        if p and p['bets'] is not None and p['side'] in ('HOME', 'AWAY'):
            public_side = p['side'] if p['bets'] >= 50 else (
                'AWAY' if p['side'] == 'HOME' else 'HOME')
            on_public = (side == public_side)
        rows.append({'sport': sport, 'surface': str(x.get('surface')),
                     'ours': 'WIN' if ourwin else 'LOSS', 'flip': flip,
                     'date': str(x.get('game_date'))[:10],
                     'on_public': on_public, 'tier': str(x.get('tier') or '')})
    rows.sort(key=lambda r: r['date'])
    print(f'graded football spread picks joinable to a result: {len(rows)}')
    if len(rows) < 40:
        print('  too few to judge a flip - refusing')
        return 0

    cur = [r for r in rows if r['surface'] in CURATED]
    print(f'  of which CURATED (clean lines): {len(cur)}')

    print(f'\n{"="*80}\n1. AS PUBLISHED vs FLIPPED - all surfaces\n{"="*80}')
    rep('as published', rows, 'ours')
    rep('FLIPPED', rows, 'flip')

    print(f'\n{"="*80}\n2. CURATED SURFACES ONLY (the ones with real lines)\n{"="*80}')
    print('  48% of game_read spread lines were never posted at any book, so a')
    print('  flip measured on those is graded against a fantasy number.')
    rep('curated, as published', cur, 'ours')
    rep('curated, FLIPPED', cur, 'flip')

    print(f'\n{"="*80}\n3. DOES THE BADNESS PERSIST? (the only question that matters)\n{"="*80}')
    for label, v in (('all surfaces', rows), ('curated only', cur)):
        if len(v) < 30:
            print(f'  {label}: n={len(v)} - too thin to split')
            continue
        cut = int(len(v) * 0.5)
        e, l = v[:cut], v[cut:]
        print(f'  {label}: EARLY {e[0]["date"]}..{e[-1]["date"]}  '
              f'LATE {l[0]["date"]}..{l[-1]["date"]}')
        rep(f'  EARLY flipped', e, 'flip')
        rep(f'  LATE  flipped (holdout)', l, 'flip')

    print(f'\n{"="*80}\n4. IS THE FLIP THE SAME TRADE AS FADING THE PUBLIC?\n{"="*80}')
    known = [r for r in rows if r['on_public'] is not None]
    if not known:
        print('  no public split joined - cannot tell')
    else:
        onp = sum(1 for r in known if r['on_public'])
        print(f'  of {len(known)} picks with a known public split, '
              f'{onp} ({100*onp/len(known):.0f}%) were ON the public side')
        rep('our picks that were ON the public side, flipped',
            [r for r in known if r['on_public']], 'flip')
        rep('our picks AGAINST the public, flipped',
            [r for r in known if not r['on_public']], 'flip')
        print('\n  If the flip only works on the picks that sat WITH the public,')
        print('  then "flip our spread" and "fade the public" are ONE edge, and')
        print('  the public filter is the better way to express it - it has 331')
        print('  football rows instead of this handful.')

    print(f'\n{"="*80}\n5. BY SURFACE AND SPORT, FLIPPED\n{"="*80}')
    by = defaultdict(list)
    for r in rows:
        by[(r['sport'], r['surface'])].append(r)
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len(v) >= 10:
            rep(f'flip {k[0]} {k[1]}', v, 'flip')
    return 0


if __name__ == '__main__':
    sys.exit(main())
