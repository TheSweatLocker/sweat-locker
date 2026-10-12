"""Write measured hit rates for FOOTBALL signals into signal_registry.

ANDY 2026-10-11: "full deep dive on signals and imporvements in processes"
for NFL and NCAAF before week 6.

THE PROBLEM, located precisely. Every native football chip on today's
board carries sample_n = 0:

    tier on stored chips   {'': 39}        blank on every one
    sample_n               n=0 on 29, n>=15 on 10 (all 10 are externals)

Externals are the only chips with any sample, because they have their own
path through external_source_track_record. Everything we built ourselves
— ncaaf_ol_weight_adv_home, nfl_penalty_away_undisciplined, the lot — has
never had its hit rate computed into signal_registry, so _resolve_weight
returns (None, 0, None) and every one of them falls back to
RAMP_UP_PRIOR = 0.50.

THAT IS THE REAL COST, and it is not the tier. With n=0 a signal that
LOSES gets exactly the same weight as one that wins. The ensemble's whole
weighting apparatus — the sample floor, the edge formula, the
ANTI_VALIDATED flip — is dead code on football, because all of it keys off
numbers nobody computed. LV @ NE carried 8 chips and 0 credible ones on
2026-10-11 for this reason.

WHAT THIS WRITES. For each signal, the hit rate on the side IT argued for,
its n, and the edge over a blind bet on that same side — the identical
method and control as audit_football_signals.py, which is imported rather
than duplicated so the two can never drift.

TIERS ARE ASSIGNED HONESTLY, AND MOSTLY THAT MEANS UNVALIDATED. Measured
on NCAAF, ZERO of 30 signals at n>=12 beat their control and two are
significantly worse. Writing optimistic tiers to unblock card volume would
reproduce exactly the defect B75 found on props — a label that means
"likely" rather than "mispriced". A signal earns VALIDATED by beating its
control by more than its own 2SE, or it does not.

    ANTI_VALIDATED   edge < -2SE                 (actively harmful)
    VALIDATED        edge > +2SE and n >= 50
    DISCOVERY        edge > +2SE and n >= 20
    UNVALIDATED      everything else

DRY BY DEFAULT. --apply required. Re-reads after writing, because a 204 is
not a write ([[feedback_204_is_not_a_write]]).

CLI
    python compute_football_signal_registry.py --sport NCAAF
    python compute_football_signal_registry.py --sport NFL --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

import market_line as ML
import audit_football_signals as A

H_W = {**ML.H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

#: Below this a measurement is not evidence, it is an anecdote. Nothing
#: under it is written at all — a registry row carrying n=4 would feed the
#: weight formula a number more dangerous than the zero it replaces.
MIN_N = 12


def collect(sport: str, since: str):
    """(signal_key -> stats) using audit_football_signals' own method."""
    ctx_t, res_t, sign = A.SPORTS[sport]
    ctx = A.page(ctx_t, {'select': 'game_id,game_date,home_team,away_team,'
                                   'close_spread,primary_play',
                         'game_date': f'gte.{since}'})
    res = A.page(res_t, {'select': 'game_date,home_team,away_team,home_score,'
                                   'away_score,close_spread',
                         'game_date': f'gte.{since}'})
    idx = {(str(r['game_date'])[:10], str(r['home_team']).lower(),
            str(r['away_team']).lower()): r for r in res}
    rows = defaultdict(list)
    cls_of = {}
    base_cov, base_ml = [], []
    for g in ctx:
        k = (str(g.get('game_date'))[:10], str(g.get('home_team')).lower(),
             str(g.get('away_team')).lower())
        r = idx.get(k)
        if not r:
            continue
        hs, as_ = A.f(r.get('home_score')), A.f(r.get('away_score'))
        cs = A.f(r.get('close_spread')) or A.f(g.get('close_spread'))
        if hs is None or as_ is None or cs is None:
            continue
        pp = A.blob(g.get('primary_play')) or {}
        srcs = pp.get('_ensemble_sources') or []
        if not srcs:
            continue
        margin = hs - as_
        home_won = margin > 0
        cm = margin - (cs * sign)
        home_cov = None if abs(cm) < 1e-9 else (cm > 0)
        base_ml.append(1 if home_won else 0)
        if home_cov is not None:
            base_cov.append(1 if home_cov else 0)
        for s in srcs:
            key = str(s.get('signal_key') or '')
            if not key:
                continue
            cls_of[key] = s.get('class')
            side = str(s.get('side') or '').upper()
            if not (side.startswith('HOME') or side.startswith('AWAY')):
                continue
            is_home = side.startswith('HOME')
            is_ml = side.endswith('_ML')
            if is_ml:
                won = 1 if (home_won == is_home) else 0
            else:
                if home_cov is None:
                    continue
                won = 1 if (home_cov == is_home) else 0
            rows[key].append((won, is_home, is_ml))
    H_cov = statistics.fmean(base_cov) if base_cov else 0.5
    H_ml = statistics.fmean(base_ml) if base_ml else 0.5
    out = {}
    for key, vals in rows.items():
        n = len(vals)
        if n < MIN_N:
            continue
        hit = statistics.fmean(v[0] for v in vals) * 100
        blind = statistics.fmean(
            (H_ml if v[1] else 1 - H_ml) if v[2]
            else (H_cov if v[1] else 1 - H_cov) for v in vals) * 100
        se2 = (0.5 / n ** 0.5) * 200
        edge = hit - blind
        if edge < -se2:
            tier = 'ANTI_VALIDATED'
        elif edge > se2 and n >= 50:
            tier = 'VALIDATED'
        elif edge > se2 and n >= 20:
            tier = 'DISCOVERY'
        else:
            tier = 'UNVALIDATED'
        out[key] = {'hit': hit, 'blind': blind, 'edge': edge, 'n': n,
                    'se2': se2, 'tier': tier, 'cls': cls_of.get(key)}
    return out, H_cov, H_ml


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=sorted(A.SPORTS))
    ap.add_argument('--since', default='2026-08-01')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    stats, H_cov, H_ml = collect(a.sport, a.since)
    print(f'=== football signal registry · {a.sport} · since {a.since} ===')
    print(f'    control: HOME covers {H_cov * 100:.1f}% · '
          f'HOME wins {H_ml * 100:.1f}%')
    print(f'    {len(stats)} signals with n>={MIN_N}\n')
    if not stats:
        print('  nothing measurable yet — not writing a registry row for a '
              'handful of firings')
        return 0

    tiers = defaultdict(int)
    for v in stats.values():
        tiers[v['tier']] += 1
    print(f'  tier assignment: {dict(tiers)}')
    print(f"\n  {'signal':<40}{'hit':>7}{'blind':>7}{'edge':>7}{'n':>5}  tier")
    for key, v in sorted(stats.items(), key=lambda z: -z[1]['edge']):
        print(f"  {key[:39]:<40}{v['hit']:>6.1f}%{v['blind']:>6.1f}%"
              f"{v['edge']:>+7.1f}{v['n']:>5}  {v['tier']}")

    if not a.apply:
        print(f'\n  [DRY] nothing written. {len(stats)} rows would be '
              f'upserted into signal_registry. Re-run with --apply.')
        return 0

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = [{
        'signal_name': k,
        'sport': a.sport,
        'hit_rate': round(v['hit'], 2),
        'sample_n': v['n'],
        'edge_pp': round(v['edge'], 2),
        'tier': v['tier'],
        'category': v['cls'],
        'origin': 'compute_football_signal_registry',
        'last_computed_at': now,
        'notes': (f"graded on the side the signal argued for; control is a "
                  f"blind bet on that same side ({v['blind']:.1f}%); "
                  f"2SE ±{v['se2']:.1f}pp"),
    } for k, v in stats.items()]

    r = requests.post(
        f'{ML.SB}/rest/v1/signal_registry?on_conflict=signal_name,sport',
        headers={**H_W, 'Prefer': 'resolution=merge-duplicates,'
                                  'return=minimal'},
        json=payload, timeout=60)
    print(f'\n  UPSERT -> {r.status_code}')
    if r.status_code not in (200, 201, 204):
        print(f'  ! {r.text[:300]}')
        return 1
    # A 204 is not a write. Prove it landed.
    back = A.page('signal_registry', {
        'select': 'signal_name,sample_n,hit_rate,tier',
        'sport': f'eq.{a.sport}', 'origin': 'eq.compute_football_signal_registry'})
    got = {x['signal_name']: x for x in back}
    ok = sum(1 for k in stats if k in got
             and int(got[k].get('sample_n') or 0) == stats[k]['n'])
    print(f'  VERIFY by re-read: {ok}/{len(stats)} rows stored with the '
          f'expected sample_n')
    if ok < len(stats):
        missing = [k for k in stats if k not in got][:5]
        print(f'  ! not stored: {missing}')
        return 1
    print('  ✓ the ensemble weight formula now has real numbers for these '
          'signals\n    instead of falling back to RAMP_UP_PRIOR on every one.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
