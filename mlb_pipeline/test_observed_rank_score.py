"""Does rescoring picks by OBSERVED per-signal rates rank, out of sample?

WHY (2026-10-09)
----------------
Andy wants NCAAF/NFL tiers that rank well enough for PRIME to be profitable.
Two things are already established:

  * the current conviction does not rank
        NCAAF r=+0.025..+0.071 · NFL r=-0.125  (higher conviction LOST)
  * coarse aggregate features do not rank either (build_rank_dataset.py).
    NCAAF's best was conviction itself at r=+0.071, a median split of
    47.7%% vs 56.4%% which is ~1.4 standard errors on n=247. NFL's apparent
    total_contrib r=+0.426 is 15 features screened on 35 rows — about one
    chance hit expected at that level.

The per-signal information is where any real ordering would live, but there
are ~229 NCAAF signal keys against 247 graded picks, so fitting a weight per
signal would overfit by construction.

THIS IS THE REGULARISED VERSION, and it fits nothing. Each signal's weight is
its OWN measured win rate on the picks it drove, shrunk toward breakeven by a
Beta prior so a signal with n=4 barely moves off 0.5. A pick's score is then

    score = sum over its signals of  contribution_i * (shrunk_rate_i - 0.5)

No coefficients are learned, so there is nothing to overfit — but the rates
themselves are estimated from data, which is precisely why this must be tested
OUT OF SAMPLE or it is circular.

THE TEST IS TIME-ORDERED, NOT RANDOM. Rates are computed on the earlier games
only and applied to the later ones. A random split would let a signal's own
test-set outcomes inform its weight, which is the leakage that made 65-79%% ATS
backtests look real here before
(project_sp_plus_backtests_are_leaky_926, project_models_dont_beat_the_close_1005).

WHAT WOULD COUNT AS SUCCESS
On the held-out half, the score must separate better than conviction does on
that SAME half. Beating a coin flip is not the bar; beating the incumbent is.
Reported with n on both sides, because a 10pp gap on 20 picks is not evidence.

WRITES NOTHING.

CLI
    python test_observed_rank_score.py --sport NCAAF
    python test_observed_rank_score.py --sport NFL --prior 10
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_rank_dataset import CTX, _jl, _f, _page   # noqa: E402

import json  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

BREAKEVEN = 0.5238


def load(sport: str) -> list[dict]:
    """One row per graded pick: date, label, conviction, and its signals."""
    reads = {str(x['game_id']): x for x in _page(
        'jerry_reads', {'select': 'game_id,result,conviction',
                        'sport': f'eq.{sport}'})}
    rows = []
    for g in _page(CTX[sport], {'select': 'game_id,game_date,primary_play',
                                'game_date': 'gte.2026-08-20'}):
        rd = reads.get(str(g['game_id']))
        if not rd:
            continue
        res = str(rd.get('result') or '').upper()
        res = {'W': 'WIN', 'L': 'LOSS'}.get(res, res)
        if res not in ('WIN', 'LOSS'):
            continue
        pp = _jl(g.get('primary_play'))
        srcs = [s for s in (pp.get('_ensemble_sources') or [])
                if isinstance(s, dict) and s.get('contribution') is not None]
        if not srcs:
            continue
        rows.append({
            'date': str(g['game_date'])[:10],
            'y': 1 if res == 'WIN' else 0,
            'conviction': _f(rd.get('conviction')) or 0.0,
            'sig': [(str(s.get('signal_key')),
                     abs(_f(s.get('contribution')) or 0.0)) for s in srcs],
        })
    rows.sort(key=lambda r: r['date'])
    return rows


def observed_rates(train: list[dict], prior: float) -> dict:
    """signal_key -> Beta-shrunk win rate on the picks it drove, in TRAIN."""
    tal = collections.defaultdict(lambda: [0, 0])
    for r in train:
        for k, _c in r['sig']:
            tal[k][0 if r['y'] else 1] += 1
    a0 = BREAKEVEN * prior
    b0 = (1 - BREAKEVEN) * prior
    return {k: (a0 + w) / (a0 + b0 + w + l) for k, (w, l) in tal.items()}


def score(row: dict, rates: dict) -> float:
    return sum(c * (rates.get(k, BREAKEVEN) - 0.5) for k, c in row['sig'])


def split_report(label: str, rows: list[dict], key) -> tuple:
    vals = [key(r) for r in rows]
    ys = [r['y'] for r in rows]
    try:
        r_ = statistics.correlation(vals, ys)
    except Exception:                                      # noqa: BLE001
        r_ = float('nan')
    med = statistics.median(vals)
    lo = [r['y'] for r in rows if key(r) <= med]
    hi = [r['y'] for r in rows if key(r) > med]
    lop = statistics.fmean(lo) * 100 if lo else 0.0
    hip = statistics.fmean(hi) * 100 if hi else 0.0
    # top quartile, which is what a PRIME tier would actually select
    cut = statistics.quantiles(vals, n=4)[2] if len(vals) >= 8 else med
    top = [r['y'] for r in rows if key(r) >= cut]
    topp = statistics.fmean(top) * 100 if top else 0.0
    print(f'    {label:<22}r={r_:+.3f}  lo {lop:5.1f}%(n={len(lo)})  '
          f'hi {hip:5.1f}%(n={len(hi)})  gap {hip - lop:+6.1f}pp  '
          f'| top-quartile {topp:5.1f}%(n={len(top)})')
    return r_, hip - lop, topp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF')
    ap.add_argument('--prior', type=float, default=20.0,
                    help='Beta prior strength; higher = more shrinkage')
    a = ap.parse_args()
    sport = a.sport.upper()
    rows = load(sport)
    if len(rows) < 40:
        print(f'=== {sport}: only {len(rows)} graded picks with sources — '
              f'too few to split honestly. Not testing.')
        return 0

    cut = len(rows) // 2
    train, test = rows[:cut], rows[cut:]
    print(f'=== {sport}: {len(rows)} graded picks with sources')
    print(f'    TRAIN {train[0]["date"]}..{train[-1]["date"]}  n={len(train)}'
          f'  win {statistics.fmean([r["y"] for r in train]) * 100:.1f}%')
    print(f'    TEST  {test[0]["date"]}..{test[-1]["date"]}  n={len(test)}'
          f'  win {statistics.fmean([r["y"] for r in test]) * 100:.1f}%')
    rates = observed_rates(train, a.prior)
    cov = statistics.fmean([
        sum(1 for k, _ in r['sig'] if k in rates) / max(len(r['sig']), 1)
        for r in test]) * 100
    print(f'    signals with a TRAIN rate: {len(rates)} · '
          f'test-pick coverage {cov:.0f}%')

    print('\n  ON THE HELD-OUT HALF (the only numbers that count):')
    s_r, s_gap, s_top = split_report('observed-rate score', test,
                                     lambda r: score(r, rates))
    c_r, c_gap, c_top = split_report('conviction (incumbent)', test,
                                     lambda r: r['conviction'])

    print('\n  IN-SAMPLE, for contrast only — expect this to look better:')
    split_report('observed-rate score', train, lambda r: score(r, rates))

    print()
    base = statistics.fmean([r['y'] for r in test]) * 100
    print(f'  test-set base rate {base:.1f}%  ·  breakeven 52.38%')
    better = (s_top > c_top) and (s_gap > c_gap)
    print(f'  score beats incumbent on BOTH gap and top-quartile: {better}')
    if not better:
        print('  => no improvement out of sample. The per-signal rates do not')
        print('     carry a usable ordering either, on this much data.')
    else:
        print('  => promising. Needs a pre-registered forward test before it')
        print('     touches a tier, and n here is small.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
