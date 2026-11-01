"""Should the fade refuse a number our OWN projection does not support?

== 2026-10-31 ==
generate_the_fade's only selection gate is FADE_MIN_PUBLIC=65. The words
`projected_spread`, `mc_probabilities` and `_ensemble_sources` appear ZERO
times in that module -- it is a pure market-structure engine and never asks
whether we agree with the side we are laying.

Tonight that produced Cleveland -9.5 while our own projection had Cleveland
by 4.2: a 5.3-point disagreement against our own pick. It is the shape Andy
withdrew on 10-30 ("close 6.5, our projected_spread 5.5"), five times worse.

Before adding a gate, measure whether the gap predicts anything.

  gap = our_projected_margin + pick_line

  pick_line is already signed from OUR side in the receipt ('Memphis -8.5'
  -> -8.5, 'Pelicans +4.5' -> +4.5), so a POSITIVE gap means our projection
  expects the bet to cover, and a negative gap means our own number says it
  does not.

THE SIGN TRAP. `_HOME_HANDICAP_SIGN = {'NFL': -1.0, 'NCAAF': 1.0,
'NBA': 1.0}` already lives in generate_the_fade, so projected_spread does
NOT share one convention across sports (feedback_edge_formula_sign_audit).
Rather than trust a constant, this DERIVES the sign per sport by correlating
projected home margin against the ACTUAL home margin, and refuses any sport
whose correlation does not clearly resolve.

Reported with n on every bucket and a direction-only control -- the fade's
hit rate on the same rows with no gap filter at all
(feedback_signal_needs_a_direction_control). Split-half by date, because a
gate that only works in one half is noise (feedback_split_the_leak_window).

USAGE
  python _measure_fade_projection_gap_1031.py
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

HERE = Path(__file__).parent
_env = HERE / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'NBA': 'nba_game_context', 'NHL': 'nhl_game_context',
       'MLB': 'mlb_game_context'}
RES = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
       'NBA': 'nba_game_results', 'NHL': 'nhl_game_results',
       'MLB': 'mlb_game_results'}


def page(path, params):
    out, off = [], 0
    while off < 60000:
        p = dict(params)
        p.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, timeout=120,
                         params=p)
        if r.status_code != 200:
            print(f'  ! {path} HTTP {r.status_code} {r.text[:160]}')
            return out
        b = r.json()
        if not b:
            break
        out.extend(b)
        off += 1000
    return out


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def corr(xs, ys):
    n = len(xs)
    if n < 8:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / (sxx * syy) ** 0.5


def be(odds):
    """Break-even win rate for American odds."""
    o = fl(odds)
    if o is None:
        return 0.5238
    return (abs(o) / (abs(o) + 100.0)) if o < 0 else (100.0 / (o + 100.0))


def roi(rows):
    """Units returned per unit staked, using each row's own price."""
    stake = ret = 0.0
    for r in rows:
        res = str(r['result']).upper()
        if res == 'PUSH':
            continue
        o = fl(r.get('pick_odds'))
        if o is None:
            o = -110.0
        stake += 1.0
        if res == 'WIN':
            ret += (100.0 / abs(o)) if o < 0 else (o / 100.0)
    return (100.0 * ret / stake) if stake else 0.0, int(stake)


def rate(rows):
    w = sum(1 for r in rows if str(r['result']).upper() == 'WIN')
    l = sum(1 for r in rows if str(r['result']).upper() == 'LOSS')
    n = w + l
    return (100.0 * w / n if n else 0.0), w, l, n


def main() -> int:
    recs = page('public_receipts',
                {'select': 'id,sport,game_date,game_id,market,pick_side,'
                           'pick_line,pick_label,pick_odds,result,matchup',
                 'surface': 'eq.fade', 'market': 'in.(rl,spread)',
                 'order': 'game_date.asc'})
    graded = [x for x in recs
              if str(x.get('result') or '').upper() in ('WIN', 'LOSS', 'PUSH')
              and fl(x.get('pick_line')) is not None
              and str(x.get('pick_side') or '').upper() in ('HOME', 'AWAY')]
    print(f'=== fade spread receipts: {len(recs)} total, {len(graded)} '
          f'gradeable with a side and a line ===')
    bysport = collections.Counter(x['sport'] for x in graded)
    print(f'  by sport: {dict(bysport.most_common())}')

    # ── per sport: derive the projected_spread sign from the results ──
    print('\n=== STEP 1 - derive the projected_spread sign per sport ===')
    print('  (correlate projected home margin vs ACTUAL home margin; a '
          'constant is not trusted)')
    signs, proj = {}, {}
    for sport in sorted(bysport):
        ctx = page(CTX.get(sport, ''),
                   {'select': 'game_id,game_date,projected_spread',
                    'game_date': 'gte.2026-08-01'})
        res = page(RES.get(sport, ''),
                   {'select': 'game_id,home_score,away_score',
                    'game_date': 'gte.2026-08-01'})
        rmap = {str(r['game_id']): r for r in res}
        xs, ys = [], []
        pm = {}
        for c in ctx:
            ps = fl(c.get('projected_spread'))
            if ps is None:
                continue
            pm[str(c['game_id'])] = ps
            r = rmap.get(str(c['game_id']))
            if not r:
                continue
            hs, as_ = fl(r.get('home_score')), fl(r.get('away_score'))
            if hs is None or as_ is None:
                continue
            xs.append(ps)
            ys.append(hs - as_)
        proj[sport] = pm
        c = corr(xs, ys)
        if c is None or abs(c) < 0.15:
            signs[sport] = None
            print(f'  {sport:<6} corr(projected_spread, actual home margin) = '
                  f'{"n/a" if c is None else f"{c:+.3f}"} on n={len(xs)} '
                  f'-- UNRESOLVED, sport EXCLUDED')
        else:
            signs[sport] = 1.0 if c > 0 else -1.0
            mean = ('projected_spread IS the home margin' if c > 0
                    else 'projected_spread is a HOME HANDICAP (negated)')
            print(f'  {sport:<6} corr = {c:+.3f} on n={len(xs)} -> '
                  f'sign {signs[sport]:+.0f}  ({mean})')

    # ── build the gap per receipt ────────────────────────────────────
    rows = []
    miss = collections.Counter()
    for x in graded:
        sp = x['sport']
        s = signs.get(sp)
        if s is None:
            miss[f'{sp}: sign unresolved'] += 1
            continue
        ps = proj.get(sp, {}).get(str(x.get('game_id')))
        if ps is None:
            miss[f'{sp}: no projected_spread'] += 1
            continue
        home_margin = s * ps
        ours = home_margin if str(x['pick_side']).upper() == 'HOME' \
            else -home_margin
        gap = ours + fl(x['pick_line'])
        rows.append({**x, 'gap': gap})
    print(f'\n  usable: {len(rows)}   not usable: {dict(miss.most_common())}')
    if not rows:
        print('  nothing to measure')
        return 0

    # ── the control, then the buckets ────────────────────────────────
    r0, w0, l0, n0 = rate(rows)
    roi0, st0 = roi(rows)
    print('\n=== STEP 2 - CONTROL: every fade spread, no gap filter ===')
    print(f'  {w0}-{l0}  {r0:.2f}%  ROI {roi0:+.2f}%  n={n0}')

    print('\n=== STEP 3 - hit rate by projection gap ===')
    print('  gap = our projected margin + the line we took.')
    print('  negative = our own number says this bet does NOT cover.\n')
    EDGES = [(-99, -5), (-5, -3), (-3, -1), (-1, 0), (0, 1), (1, 3),
             (3, 5), (5, 99)]
    print(f'  {"bucket":<14}{"W-L":>9}{"hit%":>8}{"ROI%":>9}{"n":>6}')
    for lo, hi in EDGES:
        b = [r for r in rows if lo <= r['gap'] < hi]
        if not b:
            continue
        rr, w, l, n = rate(b)
        ro, _ = roi(b)
        flag = '  <- n<30' if n < 30 else ''
        print(f'  [{lo:>3},{hi:>3}){"":<4}{w:>4}-{l:<4}{rr:>7.1f}%'
              f'{ro:>8.2f}%{n:>6}{flag}')

    print('\n=== STEP 4 - candidate gates (refuse gap < X) ===')
    print(f'  {"gate":<14}{"kept W-L":>12}{"hit%":>8}{"ROI%":>9}{"n":>6}'
          f'{"  dropped: W-L / ROI%":>24}')
    best = None
    for X in (-6, -5, -4, -3, -2, -1, 0):
        keep = [r for r in rows if r['gap'] >= X]
        drop = [r for r in rows if r['gap'] < X]
        if not keep or not drop:
            continue
        rk, wk, lk, nk = rate(keep)
        rok, _ = roi(keep)
        rd, wd, ld, nd = rate(drop)
        rod, _ = roi(drop)
        print(f'  gap >= {X:<8}{wk:>5}-{lk:<5}{rk:>7.1f}%{rok:>8.2f}%{nk:>6}'
              f'      {wd}-{ld} / {rod:+.1f}% (n={nd})')
        # a gate is only worth it if what it DROPS is worse than the control
        if nd >= 20 and rod < roi0 and rok > roi0:
            if best is None or (rok - roi0) > best[1]:
                best = (X, rok - roi0, nk, nd, rod)

    print('\n=== STEP 5 - split-half on the candidate ===')
    if not best:
        print('  no gate both improves what it keeps AND drops a worse-than-'
              'control set on n>=20. DO NOT SHIP a gate on this evidence.')
    else:
        X = best[0]
        print(f'  candidate: refuse gap < {X}  '
              f'(ROI {roi0:+.2f}% -> {best[1] + roi0:+.2f}%, '
              f'drops n={best[3]} at {best[4]:+.1f}%)')
        ds = sorted({r['game_date'] for r in rows})
        mid = ds[len(ds) // 2]
        for nm, sub in (('first half ', [r for r in rows
                                         if r['game_date'] < mid]),
                        ('second half', [r for r in rows
                                         if r['game_date'] >= mid])):
            k = [r for r in sub if r['gap'] >= X]
            d = [r for r in sub if r['gap'] < X]
            rk, wk, lk, nk = rate(k)
            rd, wd, ld, nd = rate(d)
            rok, _ = roi(k)
            rod, _ = roi(d)
            print(f'  {nm}  kept {wk}-{lk} {rk:.1f}% ROI {rok:+.2f}% n={nk}'
                  f'   |  dropped {wd}-{ld} {rd:.1f}% ROI {rod:+.2f}% n={nd}')
        print('\n  SHIP ONLY IF the dropped set is worse than the kept set in '
              'BOTH halves.')

    # ── per sport, since the fade is validated per (sport, market) ───
    print('\n=== STEP 6 - per sport (the fade is gated per sport,market) ===')
    print(f'  {"sport":<7}{"control W-L":>13}{"ctl ROI":>9}'
          f'{"  gap<0 W-L":>12}{"gap<0 ROI":>11}')
    for sp in sorted({r['sport'] for r in rows}):
        sub = [r for r in rows if r['sport'] == sp]
        neg = [r for r in sub if r['gap'] < 0]
        _, w, l, n = rate(sub)
        ro, _ = roi(sub)
        _, wn, ln, nn = rate(neg)
        ron, _ = roi(neg)
        print(f'  {sp:<7}{w:>5}-{l:<7}{ro:>8.2f}%{wn:>8}-{ln:<4}'
              f'{ron:>10.2f}%  (n={nn})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
