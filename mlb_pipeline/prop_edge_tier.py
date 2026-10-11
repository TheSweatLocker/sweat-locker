"""B75a — a prop tier built on EDGE OVER THE PRICE, not on chalk.

ANDY 2026-10-11: "we need a tier sytem that works, that if we say this is
Prime Jax -6.5 anytime we do that it's hitting more than not."

WHAT IS WRONG WITH THE LIVE LADDER. Measured on 19,258 public_receipts:
post-09-22 PRIME props hit 59.3% against a 60.9% implied — they LOSE to
their own price. PRIME is simply the chalkiest bucket (median -150). A
tier that says "this is likely" is worth nothing, because the price
already says that. A tier is only information if it says "the price is
WRONG".

WHERE THE NUMBERS BELOW COME FROM. backtest_prop_tier_clean.py re-scored
21,209 pre-09-22 MLB props with every lookback recomputed from games
STRICTLY BEFORE each prop's own game_date (the L5 leak made the stored
feature wrong on 43% of rows — [[project_prop_l5_leak_922]]). The buckets
below then survived a temporal holdout: fitted on 2026-04-23..08-27,
measured on 08-28..09-21.

    clean season    test hit   test mkt    edge        n
    s<30%             18.5%      23.6%     -5.1      4,430
    s30-45            39.1%      40.1%     -1.0      3,086
    s45-55            49.3%      50.0%     -0.7      1,893
    s55-70            62.1%      59.7%     +2.3      3,854
    s70%+             76.0%      71.9%     +4.1      3,415

    clean L10 0-2 -> -5.1,  3-4 -> -1.0,  5-6 -> +1.2,
              7-8 -> +3.8,  9-10 -> +2.8

WHAT WE DELIBERATELY DO NOT DO. We do NOT carry a fitted per-cell edge
lookup. With a properly sized fit set, corr(fit edge, test edge) is +0.364
for season, -0.235 for L10 and +0.139 for the 2-D cells — the per-cell
magnitudes are noise. An earlier lopsided split showed +0.914 and was an
artefact of a 1,395-row fit. Only the BUCKET STRUCTURE transfers, so only
the bucket structure is encoded here.

We also do NOT set p_model to the bucket's hit rate. A bucket is
heterogeneous and the PRICE carries per-prop information the bucket does
not; assigning a 76% bucket average to a prop the market has at 60% would
manufacture a 16pp edge out of nothing. Instead the measured bucket edge
is added as an INCREMENT to the market's own fair price. We start from the
market and claim only the increment we actually measured.

THE CEILING IS HONEST. +4pp is over the DE-VIGGED fair price, and the
overround on these props runs ~2.5-5pp per side, so the top tier is
breakeven-to-modest — NOT a 70% winner. "Hits more often than not" is
supportable. A large implied edge is not. Published copy must match that.

SCOPE CAVEAT, load-bearing: validated on MLB props, and MLB's season is
over. The SHAPE should transfer to NFL/NHL; the THRESHOLDS should not be
assumed to. Re-validate per sport as sample builds (B75d).

WRITES NOTHING. This is the shadow pass: it reports what the edge tier
WOULD say beside what the live tier DOES say, so the replacement is judged
on its own receipts first ([[feedback_suppression_gate_needs_shadow]]).

CLI
    python prop_edge_tier.py --sport MLB --days 30
    python prop_edge_tier.py --sport NFL --days 30
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

import market_line as ML

PROPS_TABLE = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props'}

#: The L5 leak guard landed here. Stored lookback features before this
#: date include the predicted game inside their own window.
LEAK_FIX = '2026-09-22'

#: Measured edge in probability POINTS over the de-vigged fair price, from
#: the held-out window. Season hit rate is the more robust of the two.
_SEASON_EDGE = ((0.00, 0.30, -0.051), (0.30, 0.45, -0.010),
                (0.45, 0.55, -0.007), (0.55, 0.70, +0.023),
                (0.70, 1.01, +0.041))
_L10_EDGE = ((0, 3, -0.051), (3, 5, -0.010), (5, 7, +0.012),
             (7, 9, +0.038), (9, 11, +0.028))


def _lookup(table, v):
    if v is None:
        return None
    for lo, hi, e in table:
        if lo <= v < hi:
            return e
    return None


def imp(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def devig_over(over, under):
    a, b = imp(over), imp(under)
    if a is None or b is None or (a + b) <= 0:
        return None
    return a / (a + b)


def edge_tier(season_hit_pct, l10_hit_count, p_fair):
    """The shadow verdict for one prop.

    season_hit_pct : 0..1 leak-free season hit rate for this prop's line
    l10_hit_count  : 0..10 leak-free hits in the last 10
    p_fair         : de-vigged market probability for the PICKED side

    Returns {tier, edge_pp, p_model, basis} or None when inputs are absent.
    A missing feature returns None rather than a default — a tier invented
    from a missing input is how a guessed column becomes a published claim.
    """
    se = _lookup(_SEASON_EDGE, season_hit_pct)
    le = _lookup(_L10_EDGE, l10_hit_count)
    if se is None and le is None:
        return None
    if p_fair is None:
        return None
    # Season is weighted higher: it held corr +0.364 out of sample where
    # L10 alone went NEGATIVE (-0.235). L10 contributes, it does not drive.
    if se is not None and le is not None:
        edge = 0.70 * se + 0.30 * le
        basis = 'season+l10'
    elif se is not None:
        edge, basis = se, 'season'
    else:
        edge, basis = le, 'l10'
    p_model = min(max(p_fair + edge, 0.01), 0.99)
    # Thresholds in probability points over fair. Deliberately strict: the
    # best bucket we measured is +4.1pp, so a 3pp bar already selects the
    # top of what exists rather than inventing headroom above it.
    if edge >= 0.030:
        tier = 'EDGE_PRIME'
    elif edge >= 0.015:
        tier = 'EDGE_STRONG'
    elif edge > -0.015:
        tier = 'EDGE_LEAN'
    else:
        tier = 'EDGE_PASS'
    return {'tier': tier, 'edge_pp': round(edge * 100, 2),
            'p_model': round(p_model, 4), 'basis': basis}


def page(table, params, cap=60000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=120)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def report(rows, label):
    """hit% vs the mean fair price on the SAME rows. Never vs 52.38%."""
    n = len(rows)
    if n < 30:
        print(f'    {label:<14} n={n} too thin')
        return
    w = sum(r['won'] for r in rows)
    hit = w / n * 100
    mkt = statistics.fmean(r['p'] for r in rows) * 100
    se2 = (0.5 / n ** 0.5) * 200
    d = hit - mkt
    flag = '  EDGE' if d > 2 else ('  loses' if d < -2 else '')
    print(f'    {label:<14} {w}-{n - w}  hit {hit:5.1f}%  mkt {mkt:5.1f}%  '
          f'edge {d:+5.1f}  n={n:<5} ±{se2:.1f}{flag}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB', choices=sorted(PROPS_TABLE))
    ap.add_argument('--days', type=int, default=30)
    a = ap.parse_args()
    tbl = PROPS_TABLE[a.sport]
    since = ((datetime.now(timezone.utc) - timedelta(hours=4)).date()
             - timedelta(days=a.days)).isoformat()

    print(f'=== prop_edge_tier SHADOW · {a.sport} · since {since} ===')
    print('    writes nothing · every % is against the de-vigged price')
    # ══ THE WINDOW ITSELF CAN POISON THIS ══
    # This reads the STORED player_season_hit_pct / player_l10_hit_count.
    # Before 2026-09-22 those are the LEAKED values — computed with the
    # predicted game inside its own lookback — so a window reaching back
    # past the fix scores the shadow tier on the same tautology it exists
    # to replace, and reports a inflated edge for BOTH ladders. Measured
    # on a 45-day window the live PRIME showed +21.8pp, which is the leak
    # talking, not the tier. Refuse rather than caveat.
    if since < LEAK_FIX:
        print(f'\n  REFUSED: window starts {since}, before the L5 leak fix '
              f'on {LEAK_FIX}.')
        print('    Stored lookback features before that date include the')
        print('    game being predicted, so both ladders would score against')
        print('    a tautology. Use --days <= '
              f'{(datetime.now(timezone.utc).date() - datetime.strptime(LEAK_FIX, "%Y-%m-%d").date()).days}'
              ', or score history via backtest_prop_tier_clean.py, which')
        print('    recomputes the lookback honestly.')
        return 1
    print()
    rows = page(tbl, {
        'select': 'player_name,prop_type,direction,result,tier,game_date,'
                  'book_over_odds,book_under_odds,player_season_hit_pct,'
                  'player_l10_hit_count',
        'game_date': f'gte.{since}'})
    graded = [r for r in rows
              if str(r.get('result') or '').upper() in ('WIN', 'LOSS')]
    print(f'  {len(rows)} rows · {len(graded)} graded WIN/LOSS')

    scored, skipped = [], defaultdict(int)
    for r in graded:
        d = str(r.get('direction') or '').lower()
        if d not in ('over', 'under'):
            skipped['no direction'] += 1
            continue
        p = devig_over(r.get('book_over_odds'), r.get('book_under_odds'))
        if p is None:
            skipped['no two-way price'] += 1
            continue
        if d == 'under':
            p = 1.0 - p
        sh = r.get('player_season_hit_pct')
        try:
            sh = float(sh) if sh is not None else None
            if sh is not None and sh > 1.5:
                sh /= 100.0          # stored as a percentage on some rows
        except (TypeError, ValueError):
            sh = None
        try:
            l10 = (int(r['player_l10_hit_count'])
                   if r.get('player_l10_hit_count') is not None else None)
        except (TypeError, ValueError):
            l10 = None
        v = edge_tier(sh, l10, p)
        if v is None:
            skipped['no lookback features'] += 1
            continue
        scored.append({'won': 1 if str(r['result']).upper() == 'WIN' else 0,
                       'p': p, 'live': str(r.get('tier') or '').upper(),
                       **v})
    print(f'  {len(scored)} scorable' +
          (f'  · skipped {dict(skipped)}' if skipped else ''))
    if not scored:
        print('\n  Nothing to compare. Most likely the lookback columns are '
              'empty for this window.')
        return 0

    print('\n  THE LIVE TIER:')
    for t in ('PRIME', 'STRONG', 'LEAN', 'LIGHT', 'COVERAGE', 'SKIP'):
        report([r for r in scored if r['live'] == t], t)
    print('\n  THE SHADOW EDGE TIER, same rows:')
    for t in ('EDGE_PRIME', 'EDGE_STRONG', 'EDGE_LEAN', 'EDGE_PASS'):
        report([r for r in scored if r['tier'] == t], t)

    print('\n  CROSSTAB (live tier -> shadow tier):')
    x = defaultdict(lambda: defaultdict(int))
    for r in scored:
        x[r['live']][r['tier']] += 1
    cols = ['EDGE_PRIME', 'EDGE_STRONG', 'EDGE_LEAN', 'EDGE_PASS']
    print('    ' + 'live'.ljust(11) + ''.join(c.replace('EDGE_', 'E_').rjust(10)
                                              for c in cols))
    for live in sorted(x):
        print('    ' + live.ljust(11) +
              ''.join(str(x[live][c]).rjust(10) for c in cols))

    print('\n' + '=' * 72)
    print('  Judge it on the EDGE column, not the hit column. The live PRIME')
    print('  hits a lot because it is stamped on favourites; the question is')
    print('  only whether a tier beats the price that was paid for it.')
    print('  Expected ceiling is +3-5pp over FAIR, which after ~2.5-5pp of')
    print('  overround is breakeven-to-modest. Set published copy to match.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
