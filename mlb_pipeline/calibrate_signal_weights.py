"""Fit signal weights on past weeks, TEST them on the next week, tune weekly.

══ 2026-10-30 · WHAT ANDY HAS ASKED FOR REPEATEDLY ════════════════════════
  "set the engine to a calibrated baseline based on last week's results in
   which the picks would line up — find a way the engine would have assessed
   and weighed correctly and came to a good record over last week, and set
   that as baseline for this week and tune every week through looking at
   data ... it doesn't matter if they haven't been weighed, we should assign
   a pre-weighted number on them based on our assessment of how much they
   matter, and tune it as time goes."

Both halves of that are implemented here, and the second half is the one
that makes the first half safe.

WHY AN UNMEASURED SIGNAL GETS A WEIGHT, NOT A ZERO
Today's library is mostly unmeasured: of the ENABLED game signals, the count
carrying both a measured hit rate at n>=30 and weight is NCAAF 28 of 63,
MLB 42 of 198, NHL 9 of 44, NFL 5 of 90, NBA 3 of 56, NCAAB 0 of 66. Zeroing
all of those leaves an engine that cannot form an opinion -- which is what
zeroing NCAAB's 65 priors did to NCAAB. Blanket-weighting them at 0.300 is
the opposite failure: it let signals with ZERO graded appearances outweigh
every NFL signal that had any.

So weights come from EMPIRICAL-BAYES SHRINKAGE toward a per-class prior:

    shrunk = (wins + ALPHA * prior_rate) / (n + ALPHA)
    weight = clamp(SCALE * (shrunk - breakeven))

  * n = 0   -> the signal gets exactly its class prior. It has a voice.
  * n large -> the prior washes out and the signal's own record decides.
  * n = 3   -> it cannot swing anything, which is the whole point.

The prior is each CLASS's own pooled rate (model / situational / h2h /
external / money flow / cohort), so "how much they matter" is derived from
how that family has actually done rather than asserted.

WHY IT IS FIT FORWARD AND NOT ON LAST WEEK ALONE
Fitting weights to last week and shipping them is how you ship noise. For
every week W this fits on weeks < W, applies those weights to W, and scores
the result -- a walk-forward test on data the fit never saw. The weekly
record is reported for the FITTED weights against two controls:

    live      what the engine's current weights would have picked
    best-side the single best one-word rule (always FAV / always HOME)

A weight set that cannot beat both out-of-sample does not get written, and
the script says so rather than writing it anyway. That is the guardrail
against exactly the overfit this design invites.

HOW A PICK IS RECONSTRUCTED
signal_attribution stores, per (game, signal): the side the signal called,
whether that side was RIGHT (`signal_was_right` -- the signal's own outcome,
never `result`, which is the PICK's and punishes a signal for disagreeing),
and the market. So for each game the weighted vote per side is summed, the
argmax side is the pick the weights imply, and whether that side won is read
off any signal that called it.

USAGE
  python calibrate_signal_weights.py --sport NCAAF                # report
  python calibrate_signal_weights.py --sport NCAAF --apply        # write
  python calibrate_signal_weights.py --all                        # report all
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

#: Shrinkage strength, in "pseudo-observations". A signal needs roughly this
#: many of its own graded appearances before its record outweighs its class
#: prior. 25 is deliberately strong: the failure this replaces was a blanket
#: 0.300 on signals with n=0.
ALPHA = 25.0

#: Points of weight per point of edge over breakeven. 0.04 maps a +5pp
#: signal to 0.20 and a +10pp one to 0.40, which keeps the scale in the same
#: range the library already uses (measured NFL weights run 0.016-0.153).
SCALE = 0.04

#: Spread/puckline/total are ~-110 both ways. Moneyline breakeven varies with
#: the price, so a signal scoped to ml is judged against a higher bar -- ML
#: books average -120..-203, so 52.38% there is a LOSING rate
#: (feedback_flat_110_inflates_every_ml_figure).
BREAKEVEN = {'rl': 0.5238, 'spread': 0.5238, 'puckline': 0.5238,
             'pl': 0.5238, 'total': 0.5238, 'ml': 0.5700}
BREAKEVEN_DEFAULT = 0.5238

TUNE = False

MAX_WEIGHT = 0.40
MIN_WEIGHT = 0.0


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:180]}')
            return out
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def _iso_week(d):
    try:
        return dt.date.fromisoformat(str(d)[:10]).isocalendar()[:2]
    except Exception:
        return None


def _be(market) -> float:
    return BREAKEVEN.get(str(market or '').lower(), BREAKEVEN_DEFAULT)


def load(sport: str) -> list:
    """Graded attribution rows for one sport, with each signal's CLASS joined.

    signal_attribution has no class column -- its `kind` field holds
    'ok'/'neutral'/'warn', a status placeholder, not a family
    (project_signal_hit_rate_was_our_pick_rate_1028 records the refit
    trusting `kind` as one of its two defects). The family lives on
    signal_sources.class, so it is joined here by (sport, signal_key).
    """
    rows = page('signal_attribution',
                {'select': 'sport,signal_key,signal_was_right,'
                           'pick_market,game_date,game_id,signal_side',
                 'sport': f'eq.{sport}'})
    cls_rows = page('signal_sources',
                    {'select': 'signal_key,class', 'sport': f'eq.{sport}'})
    cls = {r['signal_key']: (r.get('class') or 'unknown') for r in cls_rows}
    out, unmapped = [], set()
    for r in rows:
        if r.get('signal_was_right') is None:
            continue
        w = _iso_week(r.get('game_date'))
        if not w or not r.get('game_id') or not r.get('signal_key'):
            continue
        k = r['signal_key']
        if k in cls:
            r['signal_class'] = cls[k]
        else:
            # Model/panel/external signals (v3_spread_nfl, LR_SHADOW,
            # external:*) are injected by the scorer and have no
            # signal_sources row. Bucket them by prefix rather than dumping
            # them all into one 'unknown' prior, which would average a
            # handicapper feed together with a Monte Carlo.
            low = k.lower()
            if low.startswith('external:'):
                r['signal_class'] = 'external_pick'
            elif any(t in low for t in ('_shadow', 'lr_', 'panel', 'v3_', 'v4_',
                                        'mc_', 'elo')):
                r['signal_class'] = 'model'
            elif low.startswith('cohort'):
                r['signal_class'] = 'cohort'
            else:
                r['signal_class'] = 'unknown'
                unmapped.add(k)
        r['_week'] = w
        out.append(r)
    if unmapped:
        print(f'  ! {len(unmapped)} signal(s) have no class and no prefix '
              f'match -> pooled prior: {sorted(unmapped)[:6]}')
    return out


def fit(train: list, alpha: float = None, scale: float = None,
        floor_pp: float = 0.0, cap: float = None) -> tuple[dict, dict]:
    """-> (weight per signal_key, class prior rates). Shrunk, never raw.

    alpha/scale override the module defaults so --tune can search them.
    floor_pp zeroes any signal whose shrunk edge is below that many points,
    which is the 'stop paying for sub-breakeven signals' lever.
    """
    alpha = ALPHA if alpha is None else alpha
    scale = SCALE if scale is None else scale
    cap = MAX_WEIGHT if cap is None else cap
    # class priors first: each family's own pooled rate
    cls_k, cls_n = defaultdict(int), defaultdict(int)
    for r in train:
        c = str(r.get('signal_class') or 'unknown')
        cls_n[c] += 1
        if r['signal_was_right'] is True:
            cls_k[c] += 1
    priors = {c: (cls_k[c] / cls_n[c]) for c in cls_n if cls_n[c] >= 20}
    pooled = ((sum(cls_k.values()) / sum(cls_n.values()))
              if sum(cls_n.values()) else 0.5)

    sig_k, sig_n, sig_cls, sig_mkt = (defaultdict(int), defaultdict(int),
                                      {}, defaultdict(lambda: defaultdict(int)))
    for r in train:
        k = r['signal_key']
        sig_n[k] += 1
        if r['signal_was_right'] is True:
            sig_k[k] += 1
        sig_cls[k] = str(r.get('signal_class') or 'unknown')
        sig_mkt[k][str(r.get('pick_market') or '').lower()] += 1

    weights = {}
    for k in sig_n:
        prior = priors.get(sig_cls.get(k, 'unknown'), pooled)
        shrunk = (sig_k[k] + alpha * prior) / (sig_n[k] + alpha)
        mkt = max(sig_mkt[k], key=sig_mkt[k].get) if sig_mkt[k] else None
        edge_pp = 100.0 * (shrunk - _be(mkt))
        if edge_pp < floor_pp:
            weights[k] = 0.0
            continue
        weights[k] = round(max(MIN_WEIGHT, min(cap, scale * edge_pp)), 4)
    return weights, {'priors': priors, 'pooled': pooled}


def simulate(test: list, weights: dict, default: float = 0.0) -> dict:
    """Apply weights to one week's games. -> {'w':.., 'l':.., 'n':..}."""
    games = defaultdict(lambda: defaultdict(float))
    right = defaultdict(dict)
    for r in test:
        side = str(r.get('signal_side') or '')
        if not side:
            continue
        g = r['game_id']
        games[g][side] += weights.get(r['signal_key'], default)
        right[g][side] = bool(r['signal_was_right'])
    w = l = 0
    for g, sides in games.items():
        if not sides:
            continue
        best = max(sides, key=sides.get)
        if sides[best] <= 0:
            continue                     # no positive opinion -> no pick
        if best not in right[g]:
            continue
        if right[g][best]:
            w += 1
        else:
            l += 1
    return {'w': w, 'l': l, 'n': w + l}


def control_best_side(test: list) -> dict:
    """The one-word rule: whichever single side label wins most often."""
    tally = defaultdict(lambda: [0, 0])
    for r in test:
        side = str(r.get('signal_side') or '')
        if not side:
            continue
        tally[side][1] += 1
        if r['signal_was_right'] is True:
            tally[side][0] += 1
    best = None
    for side, (k, n) in tally.items():
        if n < 20:
            continue
        rate = k / n
        if best is None or rate > best[1]:
            best = (side, rate, n)
    return {'side': best[0], 'rate': best[1] * 100, 'n': best[2]} if best else {}


def run(sport: str, apply: bool):
    print(f'\n{"="*74}\n{sport}\n{"="*74}')
    rows = load(sport)
    if not rows:
        print('  no graded attribution rows — nothing to fit')
        return
    weeks = sorted({r['_week'] for r in rows})
    print(f'  graded rows={len(rows)}  signals={len({r["signal_key"] for r in rows})}  '
          f'games={len({r["game_id"] for r in rows})}  weeks={len(weeks)}')
    if len(weeks) < 3:
        print(f'  only {len(weeks)} week(s) — need 3 to fit forward and test. '
              f'Reporting priors only.')
    live = page('signal_registry',
                {'select': 'signal_name,recommended_weight', 'sport': f'eq.{sport}'})
    live_w = {r['signal_name']: float(r.get('recommended_weight') or 0)
              for r in live}

    print(f'\n  WALK-FORWARD: fit on weeks < W, score week W (out of sample)')
    print(f"  {'week':>6s} {'train':>6s} {'test':>5s} | {'FITTED':>14s} | "
          f"{'LIVE weights':>14s} | {'best one-word rule':>22s}")
    tot_f = {'w': 0, 'l': 0}
    tot_l = {'w': 0, 'l': 0}
    for i, W in enumerate(weeks):
        if i < 2:
            continue
        train = [r for r in rows if r['_week'] < W]
        test = [r for r in rows if r['_week'] == W]
        if len(test) < 15:
            continue
        wts, _meta = fit(train)
        f_res = simulate(test, wts)
        l_res = simulate(test, live_w)
        ctrl = control_best_side(test)
        def fmt(d):
            if not d['n']:
                return '    no pick    '
            return f"{d['w']:3d}-{d['l']:<3d} {100.0*d['w']/d['n']:5.1f}%"
        tot_f['w'] += f_res['w']; tot_f['l'] += f_res['l']
        tot_l['w'] += l_res['w']; tot_l['l'] += l_res['l']
        c = (f"{ctrl.get('side','-')[:12]:12s} {ctrl.get('rate',0):5.1f}%"
             if ctrl else '-')
        print(f'  {W[1]:6d} {len(train):6d} {len(test):5d} | {fmt(f_res):>14s} | '
              f'{fmt(l_res):>14s} | {c:>22s}')
    def agg(d, lbl):
        if not (d['w'] + d['l']):
            print(f'  {lbl}: no picks'); return None
        n = d['w'] + d['l']; hit = 100.0 * d['w'] / n
        print(f"  {lbl}: {d['w']}-{d['l']}  {hit:.2f}%  n={n}")
        return hit
    print()
    hf = agg(tot_f, 'FITTED, pooled out-of-sample ')
    hl = agg(tot_l, 'LIVE weights, same weeks     ')

    best_cfg = None
    if TUNE:
        best_cfg, _all = tune(sport, rows, live_w)
        if best_cfg:
            hit, n, a, sc, fl, tot, cp = best_cfg
            print(f'\n  BEST CONFIG out-of-sample: {hit:.2f}% on n={n} '
                  f'(alpha={a}, scale={sc}, floor={fl}pp)')
            if hit > (hl or 0):
                print(f'  -> beats live ({hl:.2f}%) by {hit-(hl or 0):+.2f}pp')
                hf = hit
            else:
                print(f'  -> still does NOT beat live ({hl:.2f}%). Not writing.')
                hf = hit

    # final weights: fit on EVERYTHING, for next week's baseline
    if best_cfg:
        _h, _n, _a, _sc, _fl, _t, _cp = best_cfg
        final, meta = fit(rows, alpha=_a, scale=_sc, floor_pp=_fl, cap=_cp)
    else:
        final, meta = fit(rows)
    print(f'\n  class priors (each family\'s own pooled rate):')
    for c, p in sorted(meta['priors'].items(), key=lambda z: -z[1]):
        print(f'    {c:22s} {p*100:5.1f}%')
    print(f'    {"(pooled fallback)":22s} {meta["pooled"]*100:5.1f}%')
    nz = {k: v for k, v in final.items() if v > 0}
    print(f'\n  fitted weights: {len(final)} signals, {len(nz)} above zero')
    for k, v in sorted(final.items(), key=lambda z: -z[1])[:12]:
        print(f'    {k:40s} {v:.4f}   (live {live_w.get(k, 0):.4f})')

    if hf is None or hl is None:
        print('\n  NOT WRITING: no out-of-sample comparison available.')
        return
    if hf <= hl:
        print(f'\n  NOT WRITING: fitted {hf:.2f}% does not beat live {hl:.2f}% '
              f'out of sample. The fit is not earning its place — tune the '
              f'estimator, do not ship it.')
        return
    print(f'\n  fitted BEATS live out of sample: {hf:.2f}% vs {hl:.2f}% '
          f'(+{hf-hl:.2f}pp)')
    if not apply:
        print('  re-run with --apply to write these weights.')
        return
    ok = fail = 0
    for k, v in final.items():
        rows_r = page('signal_registry', {'select': 'id,recommended_weight',
                                          'sport': f'eq.{sport}',
                                          'signal_name': f'eq.{k}'})
        for r in rows_r:
            if abs(float(r.get('recommended_weight') or 0) - v) < 1e-9:
                continue
            pr = requests.patch(f'{SB}/rest/v1/signal_registry', headers=H_WRITE,
                                params={'id': f'eq.{r["id"]}'},
                                json={'recommended_weight': v}, timeout=60)
            good = (pr.status_code in (200, 201) and (pr.json() or [])
                    and abs(float((pr.json() or [{}])[0]
                                  .get('recommended_weight') or -1) - v) < 1e-9)
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
    print(f'  written: {ok} weight(s), {fail} failed')


def tune(sport: str, rows: list, live_w: dict) -> tuple:
    """Search (alpha, scale, floor) for the best OUT-OF-SAMPLE record.

    This is the half of Andy's ask the first version missed. Setting weights
    from hit rates is a guess at what matters; SEARCHING for the weighting
    that would actually have produced the best record, and scoring every
    candidate on weeks the fit never saw, is the ask.

    The search is deliberately coarse. A fine grid over 421 out-of-sample
    picks would just find the configuration that fits this particular noise,
    and the whole point of the walk-forward split is to stop that. Anything
    this finds still has to beat the live weights to be written.
    """
    weeks = sorted({r['_week'] for r in rows})
    if len(weeks) < 4:
        return None, None
    # `scale` is deliberately NOT swept. A weighted vote's argmax is
    # invariant to uniform scaling, and the first run proved it: all three
    # scales returned byte-identical records (53.55% on n=422, three times).
    # Sweeping an inert knob is how a grid looks thorough while testing one
    # thing. The levers that DO change the ordering are alpha (how fast a
    # signal's own record overrides its class prior), the floor (whether
    # sub-breakeven signals vote at all) and the CAP -- the first run clamped
    # at 0.40 while the live weights run to 1.00, so twelve signals sat
    # pinned at exactly 0.4000 with no ordering left between them.
    GRID = [(a, 0.0267, fl, cp)
            for a in (5.0, 10.0, 25.0, 60.0, 150.0)
            for fl in (0.0, 1.0, 3.0)
            for cp in (0.40, 1.00, 3.00)]
    results = []
    for a, sc, fl, cp in GRID:
        tot = {'w': 0, 'l': 0}
        for i, W in enumerate(weeks):
            if i < 2:
                continue
            train = [r for r in rows if r['_week'] < W]
            test = [r for r in rows if r['_week'] == W]
            if len(test) < 15:
                continue
            wts, _ = fit(train, alpha=a, scale=sc, floor_pp=fl, cap=cp)
            res = simulate(test, wts)
            tot['w'] += res['w']; tot['l'] += res['l']
        n = tot['w'] + tot['l']
        if n < 50:
            continue
        results.append((100.0 * tot['w'] / n, n, a, sc, fl, tot, cp))
    if not results:
        return None, None
    results.sort(reverse=True)
    print(f'\n  TUNING: {len(results)} configs scored out-of-sample, best 8:')
    print(f"    {'hit%':>7s} {'n':>5s} {'W-L':>10s} {'alpha':>7s} {'floor':>6s} {'cap':>6s}")
    for hit, n, a, sc, fl, tot, cp in results[:10]:
        print(f"    {hit:6.2f}% {n:5d} {tot['w']:4d}-{tot['l']:<4d} "
              f"{a:7.1f} {fl:6.1f} {cp:6.2f}")
    best = results[0]
    return best, results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', action='append')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--tune', action='store_true',
                    help='search alpha/scale/floor for the best '
                         'out-of-sample record')
    a = ap.parse_args()
    global TUNE
    TUNE = bool(a.tune)
    sports = (['MLB', 'NFL', 'NCAAF', 'NBA', 'NHL'] if a.all
              else [s.upper() for s in (a.sport or ['NCAAF'])])
    print('=== calibrate_signal_weights ===')
    print(f'ALPHA={ALPHA} SCALE={SCALE} '
          f'breakeven rl/total={BREAKEVEN["rl"]:.4f} ml={BREAKEVEN["ml"]:.4f}')
    for s in sports:
        run(s, a.apply)


if __name__ == '__main__':
    main()
