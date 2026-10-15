#!/usr/bin/env python3
"""Turn each panelist's record into the weight the engine gives its vote.

== 2026-10-14 - WHY THIS EXISTS ==
ANDY's engine: the models are panelists, and the engine weighs their picks BY
THEIR RECORDS. We had the picks and the records and nothing in between.
signal_sources even carries a `weight_registry_key` column -- the hook for
exactly this -- populated on 75 of 842 rows, pointing at a registry that does
not exist.

ANDY: "we wont know until we build and monitor surely its better than what we
are doing." Right, and better than the argument against it: the engine ALREADY
weights its panelists, uniformly, and that uniform weight is currently shared
by NFL v4 at -9.6% ROI and by duplicate panelists that inflate agreement. A
weight of 0 for a duplicate and 0.8 for a loser is strictly better than 1.0 for
both. And a scheme never run cannot earn the record that would justify it.

== THE THREE RULES THIS ENFORCES ==

1. A DUPLICATE DOES NOT VOTE. Anything panelist_registry.py marks IDENTITY,
   ALIAS, DERIVATIVE, ECHO or CONSTANT gets weight 0. This is the whole point:
   NFL `model` is projected's own intermediates, NCAAF `sp_plus` is a literal
   alias, NHL `goals` and `mc` fall out of one Elo predictor. Counting them is
   how "3 of 4 agree" became false.

2. A RECORD ON SMALL n IS MOSTLY NOISE, SO IT IS SHRUNK. The edge used is

       shrunk = (hit_rate - breakeven) * n / (n + N0)

   with N0 = 200 decisions. At n=60 that keeps 23% of the measured edge; at
   n=600, 75%. NFL v3's +5.9pp over breakeven becomes +1.4pp. Nobody's 60-game
   record gets to dominate a slate, which is the failure mode of every
   "weight by recent performance" scheme.

3. NO PANELIST CAN RUN AWAY WITH IT. weight = 1 + K * shrunk, clamped to
   [WEIGHT_MIN, WEIGHT_MAX]. 1.0 is the uniform weight used today, so a run
   that produces 1.0 everywhere reproduces current behaviour exactly -- the
   change is auditable rather than a rewrite.

A panelist with NO record keeps weight 1.0 and basis='no_record'. Absence of
evidence is not evidence of badness, and defaulting an unmeasured panelist to
zero would quietly delete the panel.

== WHAT IT WILL PRODUCE TODAY, AND THE HONEST CAVEAT ==
Not one panelist has a 95% CI clearing breakeven in any sport, so this will not
find a star. What it WILL do is stop counting duplicates and lean away from
measured losers -- damage limitation, which is the honest claim. Promotion to
live weighting is a separate decision, made on the forward record that
engine_shadow_picks accumulates.

USAGE
    python compute_panelist_weights.py --dry-run
    python compute_panelist_weights.py --apply
    python compute_panelist_weights.py --apply --live   # use pregame records
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict

import requests

import build_panelist_records as B
import market_line as ML
import panelist_registry as REG

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

MODEL_VERSION = 'panelist_weights_v1.0.0'
#: Shrinkage strength, in decisions. The record keeps n/(n+N0) of its edge.
N0 = 200
#: How many weight-points one point of shrunk edge buys. 0.02 means a +5pp
#: shrunk edge -> weight 1.10, a deliberately small lever.
K = 0.02
WEIGHT_MIN, WEIGHT_MAX = 0.0, 2.0
#: Below this many decisions we do not let a record move the weight at all.
MIN_N_FOR_WEIGHT = 25
BREAKEVEN = B.BREAKEVEN          # 52.38% at -110

#: Panelists whose UNDERLYING MODEL changed, with the date. Their prior record
#: was earned by a different equation and must NOT transfer -- weighting a new
#: model by the old one's record is the same error class as grading a frozen
#: receipt against a mutated source.
#:
#: NFL v4 was retrained on 2026-10-14 (nfl_v4_train.py): 13 schedule-only
#: features -> 21 including 8 team-EPA ones, prediction sd 0.302 -> 3.845,
#: correlation with the actual margin +0.025 -> +0.342, negative on 76/269
#: games instead of 0/285. Its -9.6% ROI belongs to the feature-blind version.
#: It is a DIFFERENT PANELIST now and starts with no record.
MODEL_CHANGED = {
    ('NFL', 'v4'): '2026-10-14 retrained with team features',
}

SPORTS = ['NFL', 'NCAAF', 'NHL', 'NBA', 'MLB', 'NCAAB']


def records_from_context(sport, market):
    """Grade each registry panelist off the context columns, as the scorecard
    does. Returns {panelist: (w, l, n, hit, roi)}."""
    table = (B.MARGIN_PANELISTS if market == 'margin'
             else B.TOTAL_PANELISTS).get(sport)
    if not table:
        return {}
    if market == 'margin' and sport not in B.REAL_SPREAD:
        return {}
    rows = B.joined(sport)
    emin = B.EDGE_MIN[sport]
    out = {}
    for name, acc in table.items():
        w = l = 0
        u = 0.0
        for c, g, hs, as_ in rows:
            pv = acc(c)
            if pv is None:
                continue
            if market == 'margin':
                cs = B._f(g.get('close_spread'))
                if cs is None:
                    continue
                home_line = -cs if sport == 'NFL' else cs
                dev = pv - (-home_line)
                if abs(dev) < emin:
                    continue
                cover = (hs - as_) + home_line
                if abs(cover) < 1e-9:
                    continue
                hit = (cover > 0) == (dev > 0)
            else:
                ct = B._f(g.get('close_total'))
                if ct is None:
                    continue
                dev = pv - ct
                if abs(dev) < emin:
                    continue
                tot = hs + as_
                if abs(tot - ct) < 1e-9:
                    continue
                hit = (tot > ct) == (dev > 0)
            if hit:
                w += 1
                u += 100 / abs(B.PRICE)
            else:
                l += 1
                u -= 1.0
        n = w + l
        if n:
            out[name] = (w, l, n, 100.0 * w / n, 100.0 * u / n)
    return out


def records_from_predictions(sport, market, live):
    """Grade panelists that live in panelist_predictions (the new voters)."""
    preds = []
    off = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/panelist_predictions', headers=H,
                         params={'select': '*', 'sport': f'eq.{sport}',
                                 'market': f'eq.{market}',
                                 'pregame': f'is.{str(live).lower()}',
                                 'limit': '1000', 'offset': str(off)},
                         timeout=180)
        if r.status_code not in (200, 206):
            return {}
        b = r.json()
        if not isinstance(b, list):
            return {}
        preds += b
        if len(b) < 1000:
            break
        off += 1000
    if not preds:
        return {}
    rows = B.joined(sport)
    res_by_gid = {}
    for c, g, hs, as_ in rows:
        res_by_gid[str(c.get('game_id'))] = (g, hs, as_)
    emin = B.EDGE_MIN[sport]
    agg = defaultdict(lambda: [0, 0, 0.0])
    for p in preds:
        got = res_by_gid.get(str(p.get('game_id')))
        if not got:
            continue
        g, hs, as_ = got
        pv = B._f(p.get('prediction'))
        if pv is None:
            continue
        if market == 'margin':
            cs = B._f(g.get('close_spread'))
            if cs is None or sport not in B.REAL_SPREAD:
                continue
            home_line = -cs if sport == 'NFL' else cs
            mkt, actual = -home_line, hs - as_
        else:
            mkt = B._f(g.get('close_total'))
            actual = hs + as_
            if mkt is None:
                continue
        dev = pv - mkt
        if abs(dev) < emin or abs(actual - mkt) < 1e-9:
            continue
        a = agg[p.get('panelist')]
        if (actual > mkt) == (dev > 0):
            a[0] += 1
            a[2] += 100 / abs(B.PRICE)
        else:
            a[1] += 1
            a[2] -= 1.0
    out = {}
    for name, (w, l, u) in agg.items():
        n = w + l
        if n:
            out[name] = (w, l, n, 100.0 * w / n, 100.0 * u / n)
    return out


def weigh(sport, panelist, rec):
    """-> (weight, basis, shrunk_edge)."""
    kind = REG.kind(sport, panelist)
    # Rule 1: a duplicate does not vote.
    if kind is not None and not REG.may_vote(sport, panelist):
        # Say WHY a retrained panelist is still blocked, so the stale record is
        # not mistaken for a verdict on the new model.
        if (sport, panelist) in MODEL_CHANGED:
            return 0.0, (f'registry_block:{kind}|model_changed:'
                         f'{MODEL_CHANGED[(sport, panelist)]}'), None
        return 0.0, f'registry_block:{kind}', None
    # Rule 1b: the model changed, so the record is void. Uniform weight, and
    # it rebuilds a record from here.
    if (sport, panelist) in MODEL_CHANGED:
        return 1.0, f'record_void:{MODEL_CHANGED[(sport, panelist)]}', None
    if not rec:
        return 1.0, 'no_record', None
    _w, _l, n, hit, _roi = rec
    if n < MIN_N_FOR_WEIGHT:
        return 1.0, f'no_record:n={n}<{MIN_N_FOR_WEIGHT}', None
    # Rule 2: shrink toward breakeven.
    shrunk = (hit - BREAKEVEN) * (n / (n + N0))
    # Rule 3: small lever, hard clamp.
    wgt = max(WEIGHT_MIN, min(WEIGHT_MAX, 1.0 + K * shrunk))
    return round(wgt, 4), 'record', round(shrunk, 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    ap.add_argument('--live', action='store_true',
                    help='derive from pregame=true records only')
    a = ap.parse_args()

    print(f'PANELIST WEIGHTS {MODEL_VERSION}')
    print(f'shrinkage N0={N0} decisions · lever K={K} · clamp '
          f'[{WEIGHT_MIN}, {WEIGHT_MAX}] · breakeven {BREAKEVEN:.2f}%')
    print('weight 1.0 == the uniform weight the engine uses today.\n')

    payload = []
    for sport in SPORTS:
        for market in ('margin', 'total'):
            recs = records_from_context(sport, market)
            recs.update(records_from_predictions(sport, market, a.live))
            # every registry panelist for this sport/market, recorded or not
            tbl = (B.MARGIN_PANELISTS if market == 'margin'
                   else B.TOTAL_PANELISTS).get(sport) or {}
            names = set(tbl) | set(recs)
            names |= {p for (s, p) in REG.PANELISTS if s == sport}
            if not names:
                continue
            if market == 'margin' and sport not in B.REAL_SPREAD:
                continue
            print(f'{"="*74}\n{sport} · {market.upper()}')
            print(f'  {"panelist":<13}{"n":>5}{"hit%":>8}{"ROI":>8}'
                  f'{"shrunk":>9}{"weight":>8}  basis')
            for name in sorted(names):
                rec = recs.get(name)
                wgt, basis, shrunk = weigh(sport, name, rec)
                n = rec[2] if rec else 0
                hit = f'{rec[3]:.1f}%' if rec else '—'
                roi = f'{rec[4]:+.1f}%' if rec else '—'
                sh = f'{shrunk:+.3f}' if shrunk is not None else '—'
                print(f'  {name:<13}{n:>5}{hit:>8}{roi:>8}{sh:>9}'
                      f'{wgt:>8.3f}  {basis}')
                payload.append({
                    'sport': sport, 'panelist': name, 'market': market,
                    'weight': wgt, 'basis': basis,
                    'n': n or None,
                    'hit_rate': round(rec[3], 3) if rec else None,
                    'roi': round(rec[4], 3) if rec else None,
                    'shrunk_edge': shrunk,
                    'from_live': bool(a.live),
                    'model_version': MODEL_VERSION,
                })
            print()

    voters = sum(1 for p in payload if p['weight'] > 0)
    blocked = sum(1 for p in payload if p['weight'] == 0)
    print(f'{len(payload)} weights · {voters} vote · {blocked} blocked as '
          f'duplicates/aliases')
    if a.dry_run:
        print('DRY RUN — nothing written')
        return 0
    wrote = 0
    for i in range(0, len(payload), 500):
        chunk = payload[i:i + 500]
        r = requests.post(f'{SB}/rest/v1/panelist_weights', headers=H_WRITE,
                          params={'on_conflict': 'sport,panelist,market'},
                          data=json.dumps(chunk), timeout=180)
        if r.status_code not in (200, 201, 204):
            print(f'  WRITE FAILED {r.status_code}: {r.text[:220]}')
            continue
        wrote += len(chunk)
    print(f'wrote {wrote} rows')
    return 0


if __name__ == '__main__':
    sys.exit(main())
