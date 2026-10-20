#!/usr/bin/env python3
"""The model's published record, per SPORT and per MARKET, priced. Read-only.

== 2026-10-20 · WHY ==
ANDY: "add to list audit of model record across sports".

`game_read` is the most sport-universal surface we have — Jerry's pick on a
specific game, published to the app and graded. surface_records already rolls
it up, but it rolls it up POOLED:

    ALL  792-602-17   56.8%

That single number is the thing B73a called "the big one" about externals, and
we do it to ourselves: a moneyline, a spread and a total have DIFFERENT
breakevens, so pooling them produces a hit rate that cannot be compared to
anything.

  * on a SPREAD or TOTAL both sides are about -110, so breakeven is a FLAT
    52.38%
  * on a MONEYLINE breakeven MOVES with the price. A 60% hit rate on -200
    favourites loses money; 45% on +150 dogs wins. Comparing either to
    52.38% is meaningless.

So this splits sport x market, prices every pick it can, and reports ROI
against the price actually taken rather than a hit rate against a constant.

WHAT IT REFUSES TO DO
  * no pooling across markets, ever - that is the defect it exists to expose
  * no % without an n beside it, and nothing under --min-n is called a result
  * no ROI from a reconstructed price. `capture_mode` is reported, and
    receipts written before 2026-09-19 are flagged, because reconstructed
    prices were measured to inflate ROI (project_reconstructed_prices_inflate_roi_1009)
  * a sport/market with no priced picks reports "unpriceable", not 0%

USAGE
    python audit_model_record.py
    python audit_model_record.py --surface sharp_card
    python audit_model_record.py --min-n 30 --days 30
"""
from __future__ import annotations
import argparse
import math
import os
import sys
from collections import defaultdict

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

FLAT_BREAKEVEN = 52.38          # -110 both sides
#: receipts before this date carry reconstructed prices that measured as
#: inflating ROI, so they are counted separately rather than silently mixed.
PRICE_TRUST_FROM = '2026-09-19'
MARKET_GROUP = {'ml': 'ml', 'moneyline': 'ml',
                'rl': 'spread', 'spread': 'spread', 'ats': 'spread',
                'puckline': 'spread',
                'total': 'total', 'ou': 'total',
                'prop': 'prop',
                # UFC publishes a winner pick as market='fight'. Its breakeven
                # moves with the price exactly like a moneyline, so it is
                # priced the same way - but kept as its own row rather than
                # folded into 'ml', because a fight is not a team game and
                # pooling them would be the very thing this script exists to
                # refuse.
                'fight': 'fight',
                # A pass is not a bet. It is mapped so it stops showing up as
                # "unmapped", and it carries NO_ACTION so it never reaches a
                # W-L bucket.
                'pass': 'pass'}


def page(tbl, params, order):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, timeout=240,
                         params=dict(params, limit='1000', offset=str(off),
                                     order=order))
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} -> {r.status_code}: {(r.text or "")[:200]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def wilson(w, n):
    if not n:
        return (0.0, 0.0)
    z = 1.96
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def dec_from_american(a):
    a = f(a)
    if a is None or a == 0:
        return None
    return 1 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def report(rows, min_n, label):
    """One line per (sport, market). Never pooled across markets."""
    buckets = defaultdict(list)
    for r in rows:
        buckets[(r['sport'], r['market'])].append(r)
    print(f'\n{"="*96}')
    print(label)
    print('='*96)
    print(f'  {"sport":<7}{"market":<8}{"W-L-P":>12}{"hit%":>8}{"95% CI":>16}'
          f'{"priced":>8}{"ROI@price":>11}{"breakeven":>11}  verdict')
    for (sport, market) in sorted(buckets):
        v = buckets[(sport, market)]
        w = sum(1 for r in v if r['res'] == 'WIN')
        l = sum(1 for r in v if r['res'] == 'LOSS')
        p = sum(1 for r in v if r['res'] == 'PUSH')
        n = w + l
        if n == 0:
            continue
        lo, hi = wilson(w, n)
        priced = [r for r in v if r['dec'] and r['res'] in ('WIN', 'LOSS')
                  and r['price_trusted']]
        roi = (sum((r['dec'] - 1) if r['res'] == 'WIN' else -1
                   for r in priced) / len(priced) * 100) if priced else None
        # Breakeven: flat for spread/total, price-implied for ml and prop.
        if market in ('spread', 'total'):
            be = FLAT_BREAKEVEN
        elif priced:
            be = sum(100.0 / r['dec'] for r in priced) / len(priced)
        else:
            be = None
        if n < min_n:
            verdict = f'n<{min_n}'
        elif roi is None:
            verdict = 'UNPRICEABLE — hit rate only'
        elif be is not None and lo > be and roi > 0:
            verdict = '** BEATS the price **'
        elif roi < -5:
            verdict = 'LOSES at the price'
        else:
            verdict = 'inconclusive'
        print(f'  {sport:<7}{market:<8}{f"{w}-{l}-{p}":>12}{100*w/n:>7.1f}%'
              f'  [{lo:>5.1f},{hi:>5.1f}]{len(priced):>7}/{n:<4}'
              f'{(f"{roi:+.1f}%" if roi is not None else "—"):>10}'
              f'{(f"{be:.1f}%" if be is not None else "—"):>11}  {verdict}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--surface', default='game_read')
    ap.add_argument('--min-n', type=int, default=30)
    ap.add_argument('--days', type=int)
    a = ap.parse_args()

    params = {'select': 'sport,surface,market,game_date,result,pick_odds,'
                        'capture_mode,tier,pick_label'}
    if a.surface != 'all':
        params['surface'] = f'eq.{a.surface}'
    recs = page('public_receipts', params, 'game_date.desc')
    rows, skipped = [], defaultdict(int)
    for x in recs:
        res = str(x.get('result') or '').upper()
        if res not in ('WIN', 'LOSS', 'PUSH'):
            skipped['ungraded'] += 1
            continue
        gd = str(x.get('game_date'))[:10]
        if a.days:
            skipped['out of window'] += 1 if gd < _cut(a.days) else 0
            if gd < _cut(a.days):
                continue
        mk = MARKET_GROUP.get(str(x.get('market') or '').lower())
        if not mk:
            skipped[f"market {x.get('market')!r} unmapped"] += 1
            continue
        rows.append({
            'sport': str(x.get('sport') or '?').upper(),
            'market': mk,
            'res': res,
            'dec': dec_from_american(x.get('pick_odds')),
            'price_trusted': gd >= PRICE_TRUST_FROM,
            'date': gd,
        })
    print(f'surface={a.surface}  graded receipts in scope: {len(rows)}')
    for k, v in sorted(skipped.items(), key=lambda kv: -kv[1]):
        if v:
            print(f'  skipped · {k}: {v}')
    if not rows:
        print('  nothing graded in scope — stopping rather than reporting a clean sheet')
        return 1

    unpriced = sum(1 for r in rows if not r['dec'])
    untrusted = sum(1 for r in rows if r['dec'] and not r['price_trusted'])
    print(f'  no price at all: {unpriced}  ·  price from before '
          f'{PRICE_TRUST_FROM} (reconstructed, excluded from ROI): {untrusted}')

    report(rows, a.min_n, 'PER SPORT, PER MARKET — never pooled, because a '
                          'moneyline and a spread do not share a breakeven')

    # The pooled number, shown ONLY to demonstrate what it hides.
    print(f'\n{"="*96}')
    print('WHY THE POOLED NUMBER IS NOT A RESULT')
    print('='*96)
    byspt = defaultdict(list)
    for r in rows:
        byspt[r['sport']].append(r)
    for sport in sorted(byspt):
        v = byspt[sport]
        w = sum(1 for r in v if r['res'] == 'WIN')
        n = w + sum(1 for r in v if r['res'] == 'LOSS')
        if not n:
            continue
        mix = defaultdict(int)
        for r in v:
            mix[r['market']] += 1
        share = ' '.join(f'{k} {100*c/len(v):.0f}%' for k, c in sorted(mix.items()))
        print(f'  {sport:<7} pooled {100*w/n:>5.1f}%  n={n:<5} mix: {share}')
    print('\n  Each sport\'s pooled rate is a weighted average over markets whose')
    print('  breakevens differ, so it moves when the MIX moves even if no')
    print('  market changed. Read the table above instead.')
    return 0


def _cut(days):
    from datetime import datetime, timedelta, timezone
    return ((datetime.now(timezone.utc) - timedelta(hours=4)).date()
            - timedelta(days=days)).isoformat()


if __name__ == '__main__':
    raise SystemExit(main())
