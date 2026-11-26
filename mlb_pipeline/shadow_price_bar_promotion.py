#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SHADOW: what the registry would look like if promotion required a price bar.

== WHY THIS EXISTS (2026-11-25) ==========================================
166 signals are promoted (VALIDATED / DISCOVERY / PROVISIONAL) in a sport
that is currently playing. **164 of them have no `price_breakeven_pct` at
all**, and of the two that do, ZERO clear 2 SE over it.

The mechanism is the fallback in `refit_signal_registry._tier_for`: when
neither a side baseline nor a price breakeven is known, it promotes on a
FLAT 55%. At n=50 that is 0.71 SE above a coin flip and **0.37 SE** above
the 52.4% a -110 bet has to clear -- so a signal can be stamped VALIDATED
for being indistinguishable from the price it was taken at. The function's
own comment already records this costing money once (`away_covers_as_dog`,
65.3% on n=98 against a 65.8% breakeven, weight 1.00, ROI -0.25%); what it
does not say is that the fallback is the NORMAL path, not the exception.

WHY THIS IS A SHADOW AND NOT A FIX. `tier` is what the ensemble's
credibility floor reads to decide a pick may reach a card, so refusing to
promote 164 signals could stop picks reaching cards across five sports at
once. A guard whose blast radius exceeds what it guards is the compileall
mistake from 11-20 ([[feedback_a_guards_blast_radius]]), and Andy's
standing instruction is that nothing gets retired. So this measures the
change and writes NOTHING.

It is also the pattern that worked before: shadow the suppression, exit on
evidence ([[feedback_suppression_gate_needs_shadow]]).

    python shadow_price_bar_promotion.py
    python shadow_price_bar_promotion.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not SB or not KEY:
    raise SystemExit('SUPABASE_URL / SUPABASE_KEY not set')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: sports with a season in progress on 2026-11-25
LIVE_SPORTS = {'NBA', 'NHL', 'NCAAB', 'NFL', 'NCAAF'}
#: tiers the ensemble treats as evidence a pick may lean on
PROMOTED = {'VALIDATED', 'DISCOVERY', 'PROVISIONAL', 'CONFIRMED',
            'PROVISIONAL_WIDE_CI', 'PROVISIONAL_ONE_HALF'}


def read_registry() -> list:
    out, last = [], None
    while True:
        p = {'select': 'id,signal_name,sport,market_scope,tier,hit_rate,'
                       'sample_n,baseline_pct,price_breakeven_pct,'
                       'pick_control_pct,edge_pp,origin,category',
             'order': 'id.asc', 'limit': '1000'}
        if last is not None:
            p['id'] = f'gt.{last}'
        r = requests.get(f'{SB}/rest/v1/signal_registry', headers=H,
                         params=p, timeout=60)
        r.raise_for_status()
        b = r.json()
        if not b:
            break
        out += b
        last = b[-1]['id']
    return out


def frac(v):
    """hit_rate and the bars are PERCENTS in this table, never fractions."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f / 100.0 if f > 1.0 else f


def shadow_tier(n, hit, blind, bar):
    """`_tier_for`, with the flat-55% fallback REPLACED.

    The only change: when no price breakeven is known the signal cannot be
    promoted. It is not demoted to ANTI either -- an unmeasured signal is
    unmeasured, not wrong, and calling it wrong would be the same error in
    the other direction ([[feedback_column_presence_is_not_contamination]]).
    """
    if not n or hit is None:
        return 'UNVALIDATED'
    se1 = 0.5 / (n ** 0.5)
    se2 = 2.0 * se1
    if bar is None:
        # the whole point of the shadow
        if n >= 25 and hit <= 0.47:
            return 'ANTI_VALIDATED'
        return 'NEEDS_PRICE_BAR'
    edge = hit - bar
    if n >= 25 and edge < -se2:
        return 'ANTI_VALIDATED'
    if n >= 50 and edge > se2:
        return 'VALIDATED'
    if n >= 20 and edge > se1:
        return 'DISCOVERY'
    return 'UNVALIDATED'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', help='write the full shadow to this path')
    ap.add_argument('--all-sports', action='store_true',
                    help='include sports that are out of season')
    args = ap.parse_args()

    rows = read_registry()
    print(f'registry rows: {len(rows)}')
    scope = [r for r in rows
             if args.all_sports
             or str(r.get('sport') or '').upper() in LIVE_SPORTS]
    promoted = [r for r in scope
                if str(r.get('tier') or '').upper() in PROMOTED]
    print(f'in scope: {len(scope)}   promoted: {len(promoted)}')

    bar_counts = Counter(
        'has a price bar' if r.get('price_breakeven_pct') is not None
        else 'NO price bar' for r in promoted)
    print('\n=== 1. do the PROMOTED signals even have a price bar? ===')
    for k, v in bar_counts.most_common():
        print(f'  {k:<18} {v:>4}  ({100.0 * v / max(1, len(promoted)):.1f}%)')

    print('\n=== 2. the shadow: tier now -> tier under a price-bar rule ===')
    moves = Counter()
    losers = []
    for r in promoted:
        n = r.get('sample_n') or 0
        now = str(r.get('tier') or '').upper()
        new = shadow_tier(n, frac(r.get('hit_rate')),
                          frac(r.get('baseline_pct')),
                          frac(r.get('price_breakeven_pct')))
        moves[(now, new)] += 1
        if new not in PROMOTED:
            losers.append((r, now, new))
    for (a, b), c in moves.most_common():
        flag = '' if b in PROMOTED else '   <- loses promotion'
        print(f'  {a:<22} -> {b:<17} {c:>4}{flag}')
    print(f'\n  signals that would LOSE promotion: {len(losers)} of '
          f'{len(promoted)}')

    print('\n=== 3. by sport -- the blast radius, which is the whole point ===')
    bysport = defaultdict(lambda: [0, 0])
    for r in promoted:
        bysport[str(r.get('sport') or '').upper()][0] += 1
    for r, _a, _b in losers:
        bysport[str(r.get('sport') or '').upper()][1] += 1
    print(f'  {"sport":<8} {"promoted":>9} {"would lose":>11} {"left":>6}')
    for sp in sorted(bysport):
        tot, lost = bysport[sp]
        print(f'  {sp:<8} {tot:>9} {lost:>11} {tot - lost:>6}')
    empty = [sp for sp, (t, l) in bysport.items() if t - l == 0 and t]
    if empty:
        print(f'\n  !! these sports would have ZERO promoted signals left: '
              f'{", ".join(sorted(empty))}')
        print('     That is the reason this is a shadow. An engine with no '
              'promoted signal in a live sport is not a stricter engine, '
              'it is a dark one.')

    print('\n=== 4. the ones that SURVIVE (the only measured evidence we have) ===')
    keep = [r for r in promoted if (r, str(r.get('tier') or '').upper(),
                                    None) not in ()
            and r.get('price_breakeven_pct') is not None]
    for r in sorted(keep, key=lambda x: -(x.get('sample_n') or 0)):
        hit, bar = frac(r.get('hit_rate')), frac(r.get('price_breakeven_pct'))
        n = r.get('sample_n') or 0
        se = (math.sqrt(max(hit * (1 - hit), 1e-9) / n) if n else 0) or 1e-9
        print(f'  {r["signal_name"][:40]:<40} {str(r.get("sport")):<6} '
              f'hit {100 * hit:5.1f}%  bar {100 * bar:5.1f}%  n={n:<5} '
              f'{(hit - bar) / se:+5.2f} SE  tier={r.get("tier")}')
    if not keep:
        print('  (none)')

    print('\n=== 5. ANTI_VALIDATED still carrying a promoted tier? ===')
    anti_live = [r for r in scope
                 if str(r.get('tier') or '').upper() == 'ANTI_VALIDATED']
    print(f'  ANTI_VALIDATED in scope: {len(anti_live)} '
          f'(edge_weight maps these to 0.0, so they are already inert)')

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump({'promoted': len(promoted),
                       'no_price_bar': bar_counts.get('NO price bar', 0),
                       'would_lose': [
                           {'signal': r['signal_name'], 'sport': r.get('sport'),
                            'market': r.get('market_scope'),
                            'tier_now': a, 'tier_shadow': b,
                            'hit_rate': r.get('hit_rate'),
                            'n': r.get('sample_n')}
                           for r, a, b in losers],
                       'survives': [r['signal_name'] for r in keep]},
                      f, indent=1)
        print(f'\nshadow written: {args.json}')
    print('\nNOTHING WAS WRITTEN. This is a measurement, not a change.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
