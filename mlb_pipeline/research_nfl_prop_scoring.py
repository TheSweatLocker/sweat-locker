"""Is there a better way to score an NFL prop than edge-as-percent-of-line?

The current scorer projects a stat, subtracts the line, divides by the line,
and tiers on that percentage. Measured over 1,331 graded props it returns
50.9% against a 52.4% breakeven, and the buckets do not order: 6-10% 46.5,
10-15% 52.6, 15-20% 55.5, 20-30% 49.4, 30-40% 49.3, 40%+ 53.0.

A non-monotonic curve usually means the quantity being bucketed is not the
quantity that matters. This script tests three candidate replacements
against the same graded history, so the comparison is apples to apples.

  1. EDGE IN STANDARD DEVIATIONS (z), not percent.
     10% above a 4.5-reception line and 10% above a 250-yard passing line
     are not the same bet. Receptions have an SD near 1.5; passing yards
     near 50. Percent-of-line makes them look identical, so one tier mixes
     near-certainties with coin flips. z = (projection - line) / SD, with SD
     taken from the player's own prior games.

  2. NONPARAMETRIC HIT RATE. Of the player's prior games, what share cleared
     this line? No distributional assumption, no projection at all.

  3. VOLUME VERSUS YARDAGE. Per-family rates suggest the edge is not spread
     evenly: pass_yds 41.8% and rush_yds 43.9% against pass_attempts 56.8%
     and rush_attempts 56.9%. Counting stats are driven by role and usage,
     which the market re-prices slowly; yardage is driven by explosive plays,
     which nobody can project. If that holds up, the family is worth more
     than the edge.

LEAK DISCIPLINE. Every prior-game value comes from signals._stat_last10,
which nfl_generate_props writes at generation time from completed games.
audit_prop_tier_ordering --leak-check confirmed the predicted game is not in
that array (value-match by position: 10.9 / 11.4 / 10.5 / 10.9 / 9.6 — flat).
The dispersion and hit-rate measures below are therefore computed from games
that had already been played when the prop was created.

WHAT THIS CANNOT DO. Every prop here already cleared the generator's 6% edge
gate, so there is no zero-edge control group and none of this is an
out-of-sample test of a new model. It ranks framings against each other on
the same population. A framing that orders outcomes here has earned a
shadow run, not a weight.

Breakeven is 52.4% at -110. Every rate prints its n; under 30 is unreadable.
"""
import os
import sys
import math
import argparse
import statistics
from collections import defaultdict

import requests


# ── probability helpers, stdlib only ────────────────────────────────────
def norm_sf(x: float) -> float:
    """P(Z > x) for standard normal, via erf. No scipy dependency."""
    return 0.5 * (1.0 - math.erf(x / math.sqrt(2.0)))


def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for Poisson(lam). Counting stats are counts, not normals."""
    if lam <= 0:
        return 0.0 if k > 0 else 1.0
    if k <= 0:
        return 1.0
    # 1 - CDF(k-1), summed directly; k here is small (receptions, TDs, attempts)
    term = math.exp(-lam)
    cdf = term
    for i in range(1, k):
        term *= lam / i
        cdf += term
        if cdf >= 1.0:
            return 0.0
    return max(0.0, 1.0 - cdf)


COUNTING_FAMILIES = {'receptions', 'pass_attempts', 'pass_completions',
                     'rush_attempts', 'pass_tds', 'rush_tds',
                     'pass_interceptions', 'anytime_td'}


def p_clear(fam: str, proj: float, line: float, sd: float,
            direction: str) -> float | None:
    """P(the side we published clears), from our own projection.

    Counting families use Poisson on the projection, because a reception
    total is a count with variance tied to its mean — a normal centred on
    4.7 with SD 1.5 puts real mass below zero. Yardage uses a normal with
    the player's own dispersion, which is the honest first approximation for
    a continuous, right-skewed total.
    """
    if fam in COUNTING_FAMILIES:
        # Lines are x.5, so "over 4.5" is "5 or more".
        k = int(math.floor(line)) + 1
        p_over = poisson_sf(k, proj)
    else:
        if sd <= 0:
            return None
        p_over = norm_sf((line - proj) / sd)
    p_over = min(max(p_over, 1e-6), 1 - 1e-6)
    return p_over if direction == 'over' else 1.0 - p_over

sys.stdout.reconfigure(encoding='utf-8')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

BREAKEVEN = 52.4
MIN_N = 30
MIN_PRIOR = 4          # games needed before a dispersion estimate means anything


def page(table: str, select: str) -> list:
    out, offset = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=200,
                         params={'select': select, 'order': 'id.asc',
                                 'limit': 1000, 'offset': offset})
        r.raise_for_status()
        b = r.json()
        out.extend(b)
        if len(b) < 1000:
            return out
        offset += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def won(r) -> bool:
    return str(r.get('result') or '').strip().upper() in ('WIN', 'W')


def is_graded(r) -> bool:
    return str(r.get('result') or '').strip().upper() in ('WIN', 'W', 'LOSS', 'L')


def prior_values(row) -> list:
    """The player's stat in games already played when this prop was made."""
    sig = row.get('signals')
    arr = sig.get('_stat_last10') if isinstance(sig, dict) else None
    if not isinstance(arr, list):
        return []
    out = []
    for e in arr:
        if isinstance(e, dict):
            v = _f(e.get('value'))
            if v is not None:
                out.append(v)
    return out


def family(row) -> str:
    return (row.get('prop_type') or '').rsplit('_', 1)[0]


def taken_odds(row):
    """American odds actually quoted on the side we published."""
    o = (row.get('book_over_odds') if row.get('direction') == 'over'
         else row.get('book_under_odds'))
    return _f(o)


def profit_on_win(american: float) -> float:
    """Profit per 1u staked, stake excluded."""
    return (american / 100.0) if american > 0 else (100.0 / abs(american))


def implied_prob(american: float) -> float:
    """American odds -> implied probability, vig included."""
    return (100.0 / (american + 100.0) if american > 0
            else abs(american) / (abs(american) + 100.0))


def roi(rows) -> tuple:
    """Realized units per 1u staked. Hit rate is not profit — price is.

    A 53.8% hit rate is +EV at -110 and losing at -130, so any claim that a
    segment is profitable has to be settled at the prices we actually got.

    Win pays profit_on_win and returns the stake; a loss costs the stake. The
    first version of this accumulated only the profit on wins and then still
    subtracted the full amount staked, charging every winning bet twice and
    reporting -57% ROI on a segment hitting 53.8% at plus money. An ROI that
    impossible is a bug in the metric, not a finding about the picks.
    """
    staked = 0.0
    net = 0.0
    priced = 0
    for r in rows:
        o = taken_odds(r)
        if o is None:
            continue
        priced += 1
        staked += 1.0
        net += profit_on_win(o) if won(r) else -1.0
    if not staked:
        return None, 0, None
    return net / staked * 100.0, priced, net


def avg_price(rows):
    """Mean price expressed as implied probability, then back to American.

    American odds cannot be averaged — they jump from -100 to +100 with
    nothing between, so the mean of -110 and +150 lands at +20, which is not
    a price. Averaging in probability space and converting back gives a
    number that means something.
    """
    ps = [implied_prob(taken_odds(r)) for r in rows
          if taken_odds(r) is not None]
    if not ps:
        return None, None
    p = sum(ps) / len(ps)
    american = (-100.0 * p / (1.0 - p)) if p >= 0.5 else (100.0 * (1.0 - p) / p)
    return p, american


def show(title: str, buckets: list) -> None:
    """buckets: list of (label, wins, n)."""
    print(f'\n{title}')
    for label, w, n in buckets:
        if not n:
            continue
        rate = 100.0 * w / n
        flag = '   n<30' if n < MIN_N else ('  +EV' if rate >= BREAKEVEN else '')
        print(f'  {label:26s} {w:>4d}-{n - w:<4d} {rate:5.1f}%  n={n:<5d}{flag}')
    readable = [(l, 100.0 * w / n) for l, w, n in buckets if n >= MIN_N]
    if len(readable) >= 2:
        asc = all(b[1] >= a[1] - 1e-9 for a, b in zip(readable, readable[1:]))
        desc = all(b[1] <= a[1] + 1e-9 for a, b in zip(readable, readable[1:]))
        verdict = ('monotonic increasing' if asc else
                   'monotonic decreasing' if desc else 'NOT monotonic')
        print(f'  -> {verdict} across readable buckets')


def bucketize(rows, keyfn, edges, labels):
    agg = defaultdict(lambda: [0, 0])
    for r in rows:
        v = keyfn(r)
        if v is None:
            continue
        idx = None
        for i, (lo, hi) in enumerate(edges):
            if lo <= v < hi:
                idx = i
                break
        if idx is None:
            continue
        agg[idx][1] += 1
        if won(r):
            agg[idx][0] += 1
    return [(labels[i], agg[i][0], agg[i][1]) for i in range(len(labels))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-prior', type=int, default=MIN_PRIOR)
    args = ap.parse_args()

    rows = [r for r in page(
        'nfl_pipeline_props',
        'id,game_date,player_name,prop_type,prop_line,direction,projection,'
        'result,final_value,tier,conviction,signals,book_over_odds,'
        'book_under_odds') if is_graded(r)]
    print(f'graded NFL props: {len(rows)}')
    base_w = sum(1 for r in rows if won(r))
    print(f'baseline: {base_w}-{len(rows) - base_w} '
          f'{100.0 * base_w / len(rows):.1f}%   breakeven {BREAKEVEN}%')

    # ── enrich each row with dispersion-based measures ──────────────────
    usable = []
    for r in rows:
        line = _f(r.get('prop_line'))
        proj = _f(r.get('projection'))
        vals = prior_values(r)
        if line is None or proj is None or len(vals) < args.min_prior:
            continue
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        if sd <= 0:
            continue
        sign = 1.0 if r['direction'] == 'over' else -1.0
        r['_z'] = (proj - line) / sd * sign
        r['_edge_pct'] = (proj - line) / line * sign if line else None
        cleared = sum(1 for v in vals if (v > line if sign > 0 else v < line))
        r['_emp_rate'] = cleared / len(vals)
        r['_sd'] = sd
        r['_cv'] = sd / line if line else None
        usable.append(r)
    print(f'with >= {args.min_prior} prior games and non-zero dispersion: '
          f'{len(usable)}')

    # ── 1. the incumbent, for reference on this exact subset ────────────
    show('EDGE AS PERCENT OF LINE  (what we score on today)',
         bucketize(usable, lambda r: r.get('_edge_pct'),
                   [(0.06, 0.10), (0.10, 0.15), (0.15, 0.20),
                    (0.20, 0.30), (0.30, 0.40), (0.40, 99)],
                   ['6-10%', '10-15%', '15-20%', '20-30%', '30-40%', '40%+']))

    # ── 2. edge in standard deviations ──────────────────────────────────
    show('EDGE IN STANDARD DEVIATIONS  (same edge, variance-adjusted)',
         bucketize(usable, lambda r: r.get('_z'),
                   [(-99, 0.0), (0.0, 0.15), (0.15, 0.30), (0.30, 0.50),
                    (0.50, 0.80), (0.80, 99)],
                   ['z < 0', 'z 0.00-0.15', 'z 0.15-0.30',
                    'z 0.30-0.50', 'z 0.50-0.80', 'z 0.80+']))

    # ── 3. nonparametric: the player's own prior hit rate vs this line ──
    show("PLAYER'S PRIOR GAMES CLEARING THIS LINE  (no projection at all)",
         bucketize(usable, lambda r: r.get('_emp_rate'),
                   [(0.0, 0.30), (0.30, 0.45), (0.45, 0.55),
                    (0.55, 0.70), (0.70, 0.90), (0.90, 1.01)],
                   ['<30% of prior games', '30-45%', '45-55%',
                    '55-70%', '70-90%', '90-100%']))

    # ── 4. relative dispersion of the stat itself ──────────────────────
    show('DISPERSION OF THE STAT  (SD as a share of the line)',
         bucketize(usable, lambda r: r.get('_cv'),
                   [(0.0, 0.25), (0.25, 0.40), (0.40, 0.60),
                    (0.60, 0.90), (0.90, 99)],
                   ['CV < 0.25 (stable)', 'CV 0.25-0.40', 'CV 0.40-0.60',
                    'CV 0.60-0.90', 'CV 0.90+ (noisy)']))

    # ── 5. per family, counting stats vs yardage ────────────────────────
    fam = defaultdict(lambda: [0, 0])
    for r in rows:
        fam[family(r)][1] += 1
        if won(r):
            fam[family(r)][0] += 1
    COUNTING = {'receptions', 'pass_attempts', 'pass_completions',
                'rush_attempts', 'pass_tds', 'rush_tds',
                'pass_interceptions', 'anytime_td'}
    YARDAGE = {'pass_yds', 'rush_yds', 'reception_yds'}
    print('\nPER FAMILY')
    for k in sorted(fam, key=lambda z: -fam[z][1]):
        w, n = fam[k]
        kind = ('counting' if k in COUNTING else
                'YARDAGE' if k in YARDAGE else '?')
        rate = 100.0 * w / n
        flag = '   n<30' if n < MIN_N else ('  +EV' if rate >= BREAKEVEN else '')
        print(f'  {k:22s} {kind:9s} {w:>4d}-{n - w:<4d} {rate:5.1f}%  '
              f'n={n:<5d}{flag}')
    groups = [('counting stats', COUNTING), ('yardage stats', YARDAGE)]
    out = []
    for label, keys in groups:
        w = sum(fam[k][0] for k in keys if k in fam)
        n = sum(fam[k][1] for k in keys if k in fam)
        out.append((label, w, n))
    show('COUNTING VERSUS YARDAGE', out)

    # ── the number that actually decides it: realized ROI at our prices ──
    print('\nREALIZED ROI AT THE PRICES WE PUBLISHED')
    print('  A hit rate above 52.4% only pays if the odds were near -110.')
    segments = [
        ('everything', rows),
        ('counting stats', [r for r in rows if family(r) in COUNTING]),
        ('yardage stats', [r for r in rows if family(r) in YARDAGE]),
    ]
    for label, sub in segments:
        r_pct, n_priced, units = roi(sub)
        if r_pct is None:
            print(f'  {label:34s} no priced rows')
            continue
        w = sum(1 for x in sub if won(x))
        p, am = avg_price(sub)
        # Break-even hit rate at the average price actually paid.
        be = p * 100.0 if p else None
        print(f'  {label:34s} {100.0*w/len(sub):5.1f}%  n={len(sub):<5d} '
              f'avg price {am:+7.1f} (needs {be:4.1f}%)  '
              f'ROI {r_pct:+6.2f}%  ({units:+.1f}u on {n_priced})')

    # ── can our numbers become calibrated probabilities? ────────────────
    #
    # This is the one test that matters for moving to probability-space
    # scoring, and it needs no both-side odds, so it can run on history.
    # If we say 60% and 60% of those land, the projection can be turned into
    # a probability and compared against a fair price. If it cannot, then
    # de-vigging will just compare a good market number to a bad one of ours.
    print('\nCALIBRATION OF OUR OWN PROBABILITY  (say 60%, hit 60%?)')
    print('  Counting families use Poisson on the projection; yardage uses a')
    print('  normal with the player\'s own dispersion. Predicted is for the')
    print('  side we actually published.')
    for r in usable:
        r['_p'] = p_clear(family(r), _f(r['projection']), _f(r['prop_line']),
                          r['_sd'], r['direction'])
    cal = [r for r in usable if r.get('_p') is not None]
    P_BANDS = [(0.0, 0.40), (0.40, 0.50), (0.50, 0.55), (0.55, 0.60),
               (0.60, 0.70), (0.70, 0.85), (0.85, 1.01)]
    P_LABELS = ['<40%', '40-50%', '50-55%', '55-60%', '60-70%', '70-85%',
                '85%+']
    print(f"  {'predicted':12s} {'actual':>18s} {'gap':>8s}  n")
    rows_out = []
    for (lo, hi), label in zip(P_BANDS, P_LABELS):
        sub = [r for r in cal if lo <= r['_p'] < hi]
        if not sub:
            continue
        w = sum(1 for r in sub if won(r))
        actual = 100.0 * w / len(sub)
        pred = 100.0 * statistics.mean([r['_p'] for r in sub])
        flag = '   n<30' if len(sub) < MIN_N else ''
        print(f'  {label:12s} said {pred:5.1f}% got {actual:5.1f}% '
              f'{actual - pred:+7.1f}pp  n={len(sub)}{flag}')
        rows_out.append((label, pred, actual, len(sub)))
    big = [x for x in rows_out if x[3] >= MIN_N]
    if big:
        mean_gap = statistics.mean([a - p for _, p, a, _ in big])
        worst = max(big, key=lambda x: abs(x[2] - x[1]))
        print(f'  mean signed gap across readable bands: {mean_gap:+.1f}pp')
        print(f'  worst band: {worst[0]} said {worst[1]:.1f}% got '
              f'{worst[2]:.1f}% (n={worst[3]})')
        print('  A projection that is confident and wrong is worse than no')
        print('  projection: it would size up exactly where it is least right.')

    # ── does calibrating it out-of-sample produce something usable? ─────
    #
    # The raw probability is overconfident and the error grows with
    # confidence, but the actual hit rate still rises across bands — the
    # ranking carries some information and only the scale is wrong. That is
    # the textbook case for a calibration map: learn the monotonic mapping
    # from raw score to observed frequency on early games, then apply it to
    # later ones and check it holds. Fitting and testing on the same rows
    # would prove nothing, so this is split by date.
    print('\nOUT-OF-SAMPLE CALIBRATION  (fit on early dates, test on later)')
    dated = sorted([r for r in cal if r.get('game_date')],
                   key=lambda r: str(r['game_date']))
    if len(dated) < 200:
        print('  not enough dated rows')
    else:
        cut_idx = int(len(dated) * 0.55)
        cut_date = str(dated[cut_idx]['game_date'])[:10]
        fit = [r for r in dated if str(r['game_date'])[:10] < cut_date]
        test = [r for r in dated if str(r['game_date'])[:10] >= cut_date]
        print(f'  split at {cut_date}:  fit n={len(fit)}   test n={len(test)}')
        if not fit or not test:
            print('  split produced an empty side')
        else:
            # Monotonic map by equal-count bins on the raw score.
            BINS = 5
            fit_sorted = sorted(fit, key=lambda r: r['_p'])
            step = max(1, len(fit_sorted) // BINS)
            table = []
            for i in range(0, len(fit_sorted), step):
                chunk = fit_sorted[i:i + step]
                if len(chunk) < 20:
                    if table:
                        break
                lo = chunk[0]['_p']
                w = sum(1 for r in chunk if won(r))
                table.append((lo, w / len(chunk), len(chunk)))
            # Enforce monotonicity by pooling adjacent violations.
            changed = True
            while changed and len(table) > 1:
                changed = False
                for i in range(len(table) - 1):
                    if table[i][1] > table[i + 1][1]:
                        lo = table[i][0]
                        n = table[i][2] + table[i + 1][2]
                        rate = ((table[i][1] * table[i][2]
                                 + table[i + 1][1] * table[i + 1][2]) / n)
                        table[i:i + 2] = [(lo, rate, n)]
                        changed = True
                        break
            print('  fitted map (raw score floor -> observed rate, fit rows):')
            for lo, rate, n in table:
                print(f'      raw >= {lo:.3f}  ->  {rate*100:5.1f}%   (n={n})')

            def calibrated(p):
                out = table[0][1]
                for lo, rate, _ in table:
                    if p >= lo:
                        out = rate
                return out

            print('  applied to the held-out later dates:')
            agg = defaultdict(lambda: [0, 0, 0.0])
            for r in test:
                c = calibrated(r['_p'])
                agg[round(c, 3)][1] += 1
                agg[round(c, 3)][2] += c
                if won(r):
                    agg[round(c, 3)][0] += 1
            for c in sorted(agg):
                w, n, _ = agg[c]
                flag = '   n<30' if n < MIN_N else ''
                print(f'      said {c*100:5.1f}%  got {100.0*w/n:5.1f}%  '
                      f'n={n}{flag}')
            big = [(c, v) for c, v in agg.items() if v[1] >= MIN_N]
            if big:
                gaps = [100.0 * v[0] / v[1] - c * 100 for c, v in big]
                print(f'      mean signed gap OOS: '
                      f'{statistics.mean(gaps):+.1f}pp  '
                      f'(raw model was -14.9pp)')
            # Does a calibrated number find +EV against the price we paid?
            print('  betting only where calibrated P beats the price we paid:')
            for margin in (0.0, 0.02, 0.04):
                sel = []
                for r in test:
                    o = taken_odds(r)
                    if o is None:
                        continue
                    if calibrated(r['_p']) > implied_prob(o) + margin:
                        sel.append(r)
                if not sel:
                    print(f'      margin {margin:.0%}: no qualifying plays')
                    continue
                w = sum(1 for r in sel if won(r))
                r_pct, _, units = roi(sel)
                flag = '   n<30' if len(sel) < MIN_N else ''
                print(f'      margin {margin:.0%}: {w}-{len(sel)-w} '
                      f'{100.0*w/len(sel):5.1f}%  n={len(sel):<4d} '
                      f'ROI {r_pct:+6.2f}%  {units:+6.1f}u{flag}')

    # ── price is a selection variable we currently ignore ───────────────
    print('\nROI BY THE PRICE WE PAID  (the band is -300..+150 today)')
    print('  Break-even rises with juice, so a flat hit rate loses more as the')
    print('  price worsens. If ROI degrades with price, the cheapest edge')
    print('  available is refusing the expensive half.')
    PRICE_BANDS = [(-1000, -200), (-200, -150), (-150, -130), (-130, -115),
                   (-115, -101), (-101, 1000)]
    PRICE_LABELS = ['worse than -200', '-200 to -150', '-150 to -130',
                    '-130 to -115', '-115 to -101', 'plus money']
    for (lo, hi), label in zip(PRICE_BANDS, PRICE_LABELS):
        sub = [r for r in rows
               if taken_odds(r) is not None and lo <= taken_odds(r) < hi]
        if not sub:
            continue
        w = sum(1 for x in sub if won(x))
        r_pct, _, units = roi(sub)
        p, _ = avg_price(sub)
        flag = '   n<30' if len(sub) < MIN_N else ''
        print(f'  {label:18s} {w:>4d}-{len(sub)-w:<4d} {100.0*w/len(sub):5.1f}% '
              f'(needs {p*100:4.1f}%)  n={len(sub):<5d} '
              f'ROI {r_pct:+6.2f}%  {units:+7.1f}u{flag}')

    # ── does our own conviction predict profit at all? ──────────────────
    print('\nROI BY OUR CONVICTION  (does the number we publish mean anything)')
    CONV = [(0, 60), (60, 70), (70, 80), (80, 101)]
    for lo, hi in CONV:
        sub = [r for r in rows
               if _f(r.get('conviction')) is not None
               and lo <= _f(r['conviction']) < hi]
        if not sub:
            continue
        w = sum(1 for x in sub if won(x))
        r_pct, _, units = roi(sub)
        flag = '   n<30' if len(sub) < MIN_N else ''
        print(f'  conviction {lo}-{hi - 1:<3d} {w:>4d}-{len(sub)-w:<4d} '
              f'{100.0*w/len(sub):5.1f}%  n={len(sub):<5d} '
              f'ROI {r_pct:+6.2f}%  {units:+7.1f}u{flag}')

    # ── does stacking the three findings compound, or is it noise? ──────
    print('\nSTACKED FILTER  (counting stat AND z >= 0.15 AND not already hot)')
    print('  Selected on this same data, so treat as a hypothesis to shadow,')
    print('  not a validated edge. Reported because the size matters.')
    steps = [
        ('all usable (>=4 prior games)', usable),
        ('+ counting stats only',
         [r for r in usable if family(r) in COUNTING]),
        ('+ z >= 0.15',
         [r for r in usable if family(r) in COUNTING and r['_z'] >= 0.15]),
        ('+ prior hit rate < 0.70',
         [r for r in usable if family(r) in COUNTING and r['_z'] >= 0.15
          and r['_emp_rate'] < 0.70]),
    ]
    for label, sub in steps:
        if not sub:
            print(f'  {label:34s} empty')
            continue
        w = sum(1 for x in sub if won(x))
        r_pct, n_priced, units = roi(sub)
        roi_s = f'ROI {r_pct:+6.2f}%' if r_pct is not None else 'ROI n/a'
        flag = '   n<30' if len(sub) < MIN_N else ''
        print(f'  {label:34s} {w:>4d}-{len(sub)-w:<4d} '
              f'{100.0*w/len(sub):5.1f}%  n={len(sub):<5d} {roi_s}{flag}')

    # ── direction check: is the counting edge really a one-sided artifact? ──
    print('\nCOUNTING EDGE BY DIRECTION  (a one-sided edge is usually an artifact)')
    for label, keys in groups:
        for d in ('over', 'under'):
            sub = [r for r in rows if family(r) in keys and r['direction'] == d]
            if not sub:
                continue
            w = sum(1 for x in sub if won(x))
            r_pct, _, _ = roi(sub)
            roi_s = f'ROI {r_pct:+6.2f}%' if r_pct is not None else 'ROI n/a'
            flag = '   n<30' if len(sub) < MIN_N else ''
            print(f'  {label:16s} {d:6s} {w:>4d}-{len(sub)-w:<4d} '
                  f'{100.0*w/len(sub):5.1f}%  n={len(sub):<5d} {roi_s}{flag}')


if __name__ == '__main__':
    main()
