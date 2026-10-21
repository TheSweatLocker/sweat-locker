#!/usr/bin/env python3
"""Compute real hit rates for prop signals from graded history.

    python calibrate_prop_signals.py --sport NHL
    python calibrate_prop_signals.py --sport NHL --apply

WHY
───
Every enabled prop signal carries hit_rate_pct = NULL and sample_n = NULL, so
_resolve_weight returns (None, 0, None) and edge_weight falls back to its
no-record prior of 0.15-0.20. A signal with no track record therefore gets
the same voice as a proven one. Measured consequence on 2026-10-21: the NHL
board produced 52 PRIME decisions, 51 of them resting on
nhl_prop_l10_extreme alone -- a signal that fires on 46.9% of the board and
has never been graded. Conviction numbers built on that are arbitrary.

nhl_pipeline_props holds 34,630 rows graded Win or Loss. That is enough to
replace the prior with a measurement.

HOW A SIGNAL IS GRADED, AND WHY THE SIDE MATTERS
────────────────────────────────────────────────
`result` on the prop row is the outcome of the row's OWN direction (an
`sog_over` row with result Win means the over hit). A signal does not always
agree with the row:

    side_expr == 'BACK'  -> the signal takes the row's direction
    side_expr == 'FADE'  -> the signal takes the OPPOSITE direction

So a FADE is correct exactly when the row LOST. Scoring a fade as if it rode
with the row inverts it, which is what flattered the UFC record (4 of 4 fades
stored backwards, 13-6 that was really 11-8). Hence `correct` below is
explicitly side-aware.

BREAKEVEN COMES FROM THE PRICE, NOT FROM 52.4%
──────────────────────────────────────────────
ensemble_scorer's BREAKEVEN is 0.524, the -110 breakeven. Props do not trade
at -110; the band here is -300 to +150, where breakeven runs from 75% to 40%.
Comparing a hit rate on -250 props against 52.4% would call a losing signal
validated. So breakeven is computed per row from the price actually available
on the side the signal took -- the opposite side's price for a FADE -- and
averaged over the rows where the signal fired.

"HOLDS BOTH HALVES" IS THE BAR
──────────────────────────────
A rate measured on the same rows that produced it is in-sample. The history
is split by date into an earlier and later half, and a signal is only
VALIDATED if the full-sample Wilson lower bound clears its own breakeven AND
both halves clear it. That is the standard that qualified The Fade
(project_the_fade_is_the_one_real_edge_1019) and it is what keeps a
coin-flip partition from being promoted on noise.

LEAK WINDOW
───────────
player_l5/l10_hit_count were computed WITHOUT the before_date guard until
2026-10-03, so earlier rows encode part of their own outcome -- the MLB prop
L5 leak, which there moved PRIME from -9.3% to +21.6% ROI. Rows enriched
before --guard-from are excluded by default rather than quietly averaged in.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
load_dotenv(os.path.join(HERE, '.env'))
load_dotenv(os.path.join(os.path.dirname(HERE), '.env'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_SERVICE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = dict(H, **{'Content-Type': 'application/json',
                 'Prefer': 'return=representation'})

# Reuse the SCORER's own evaluation so calibration cannot drift from live
# behaviour. A separate expression evaluator here would eventually disagree
# with the one that actually fires, and the registry would describe a signal
# nobody runs.
from prop_ensemble_scorer import (  # noqa: E402
    CTX_TABLE, PROPS_TABLE, _coerce_prop, _CtxProxy, _matches_market,
    _safe_eval,
)
from prop_ctx_resolve import (  # noqa: E402
    build_ctx_index, derive_side_fields, resolve_ctx,
)
from ensemble_scorer import SAMPLE_MIN_N  # noqa: E402

GUARD_FROM_DEFAULT = '2026-10-03'


def _paginate(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{table} {r.status_code}: {r.text[:200]}')
        b = r.json()
        if not isinstance(b, list) or not b:
            break
        out += b
        if len(b) < 1000:
            break
        off += 1000
    return out


def _implied_prob(odds) -> float | None:
    """American odds -> breakeven win probability."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return (-o) / (-o + 100.0) if o < 0 else 100.0 / (o + 100.0)


def _raw_price_for_side(row: dict, side: str):
    """The price we would actually pay on the side this signal takes.

    A BACK rides the row's own direction; a FADE takes the other one, so it
    pays the OTHER side's juice. Using the row's own price for a fade prices
    a bet nobody could have placed.
    """
    d = (row.get('direction') or '').lower()
    over, under = row.get('book_over_odds'), row.get('book_under_odds')
    if side == 'BACK':
        return over if d == 'over' else under
    return under if d == 'over' else over


def _wilson(wins: int, n: int) -> tuple[float, float]:
    """95% Wilson interval. Normal-approx CIs put bounds outside [0,1] on the
    small, lopsided samples this table is full of."""
    if n <= 0:
        return 0.0, 1.0
    z = 1.959963985
    p = wins / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - m) / d), min(1.0, (c + m) / d)


def _tier(hit: float, n: int, be: float, lo: float, hi: float,
          first_ok: bool | None, second_ok: bool | None) -> str:
    if n < SAMPLE_MIN_N:
        return 'UNVALIDATED'
    if hi < be:
        return 'ANTI_VALIDATED'
    if lo > be and first_ok and second_ok:
        return 'VALIDATED'
    if hit > be:
        # Beats its price but either the interval still spans breakeven or one
        # half does not hold. Real enough to watch, not to weight fully.
        return 'DISCOVERY'
    return 'UNVALIDATED'


def evaluate(sport: str, guard_from: str,
             band: tuple | None = (-300, 150)) -> tuple[dict, dict]:
    table = PROPS_TABLE[sport]
    signals = _paginate('signal_sources', {
        'select': '*', 'sport': f'eq.{sport}', 'enabled': 'eq.true',
        'subject_scope': 'in.(prop,player_prop)'})
    print(f'  {len(signals)} enabled prop signals for {sport}')

    # ══ 2026-10-21 · LOAD THE GAME CONTEXT. EMPTY ctx IS NOT "NO SIGNAL" ══
    # The first version of this evaluated every signal with _CtxProxy({}),
    # an EMPTY context, so any expression reading ctx.* was false by
    # construction. It then reported 17 of 25 NFL signals as "never fired on
    # any graded row", which I nearly wrote up as 17 dead signals. Nine of
    # them read ctx -- six QB-vs-defense, two weather, one situational -- and
    # the live scorer resolves ctx for NFL at 271 of 271 props with zero
    # misses. They were not dead; the measurement was blind.
    #
    # prop_ctx_resolve's own docstring names this exact failure: "a caller
    # that cannot tell an empty context from a resolved one reports 'signal
    # did not fire' for what is really 'I never had the data'". It is also
    # the house rule that 0 rows read is a wiring error, not a finding.
    #
    # Resolved the same way the scorer does -- by game_id, then by
    # (date, team) -- and derive_side_fields is applied so own/opponent
    # orientation matches what the live path sees. How many resolved is
    # reported, because a calibration run against mostly-missing context
    # would understate every ctx signal in exactly the same way.
    ctx_table = CTX_TABLE.get(sport)
    ctx_rows = _paginate(ctx_table, {'select': '*'}) if ctx_table else []
    ctx_index = build_ctx_index(ctx_rows)
    print(f'  {len(ctx_rows)} {ctx_table} rows for context resolution')

    params = {'select': '*', 'result': 'in.(Win,Loss)'}
    if guard_from:
        params['player_lookback_updated_at'] = f'gte.{guard_from}'
    rows = _paginate(table, params)
    print(f'  {len(rows)} rows graded Win/Loss'
          + (f' with lookback computed on/after {guard_from}'
             if guard_from else ''))
    if not rows:
        return {}, {'rows': 0}

    dates = sorted({str(r.get('game_date') or '') for r in rows if
                    r.get('game_date')})
    split = dates[len(dates) // 2] if len(dates) > 1 else None
    print(f'  date range {dates[0]}..{dates[-1]}'
          + (f', split at {split}' if split else ''))

    # agg[key] = [n, wins, be_sum, n_first, w_first, n_second, w_second]
    agg = defaultdict(lambda: [0, 0, 0.0, 0, 0, 0, 0])
    unevaluable = defaultdict(int)
    out_of_band = defaultdict(int)
    # signal_registry.category is NOT NULL and mirrors signal_sources.class
    # (prop_form, prop_matchup, prop_model ...). Carried through rather than
    # defaulted, so a calibrated row keeps the same category the hand-written
    # row had and the scorer's class-confluence maths is unaffected.
    sig_class: dict = {}
    fired_any = defaultdict(int)

    ctx_how = defaultdict(int)
    for raw in rows:
        ctx_row, how = resolve_ctx(ctx_index, raw)
        ctx_how[how] += 1
        p = _coerce_prop({**derive_side_fields(ctx_row, raw), **raw})
        env = {'ctx': _CtxProxy(ctx_row), 'p': p}
        for s in signals:
            if not _matches_market(s, p):
                continue
            key = (s.get('signal_key'), s.get('market_scope') or '*')
            sig_class.setdefault(key, s.get('class'))
            try:
                if not _safe_eval(s.get('condition_expr') or '', env):
                    continue
            except Exception:
                unevaluable[key] += 1
                continue
            fired_any[key] += 1
            try:
                side = str(_safe_eval(s.get('side_expr') or '""',
                                      env) or '').upper()
            except Exception:
                unevaluable[key] += 1
                continue
            if side not in ('BACK', 'FADE'):
                continue
            px = _raw_price_for_side(raw, side)
            be = _implied_prob(px)
            if be is None:
                # No price on the side we would have taken: the bet cannot be
                # graded against a real breakeven, so it is not counted.
                unevaluable[key] += 1
                continue
            # ══ MEASURE ONLY PRICES WE WOULD ACTUALLY PUBLISH ══
            # The raw table mirrors the book, so it holds prices far outside
            # the publishable band -- a points_under at -650 (86.7%
            # breakeven) and an assists_under at -390 sit in the graded
            # history. apply_odds_band_gate enforces -300..+150 downstream,
            # so a hit rate averaged over -650 props measures a bet we would
            # SKIP, and both the rate and the breakeven it is judged against
            # come from props that never reach a card.
            if band and not (band[0] <= px <= band[1]):
                out_of_band[key] += 1
                continue
            res = raw.get('result')
            correct = (res == 'Win') if side == 'BACK' else (res == 'Loss')
            a = agg[key]
            a[0] += 1
            a[1] += 1 if correct else 0
            a[2] += be
            gd = str(raw.get('game_date') or '')
            if split and gd < split:
                a[3] += 1
                a[4] += 1 if correct else 0
            else:
                a[5] += 1
                a[6] += 1 if correct else 0

    out = {}
    for key, (n, w, be_sum, n1, w1, n2, w2) in agg.items():
        if n == 0:
            continue
        hit = w / n
        be = be_sum / n
        lo, hi = _wilson(w, n)
        h1 = (w1 / n1) if n1 else None
        h2 = (w2 / n2) if n2 else None
        first_ok = (h1 is not None and h1 > be)
        second_ok = (h2 is not None and h2 > be)
        out[key] = {
            'signal': key[0], 'market_scope': key[1],
            'n': n, 'wins': w, 'hit': hit, 'breakeven': be,
            'lo': lo, 'hi': hi, 'edge_pp': (hit - be) * 100.0,
            'n_first': n1, 'hit_first': h1,
            'n_second': n2, 'hit_second': h2,
            'tier': _tier(hit, n, be, lo, hi, first_ok, second_ok),
            'category': sig_class.get(key) or 'prop_signal',
            'unevaluable': unevaluable.get(key, 0),
            'out_of_band': out_of_band.get(key, 0),
        }
    print(f'  ctx resolved: by id={ctx_how.get("id", 0)}, '
          f'by (date,team)={ctx_how.get("team_date", 0)}, '
          f'MISS={ctx_how.get("miss", 0)}')
    meta = {'rows': len(rows), 'signals': len(signals),
            'ctx_how': dict(ctx_how),
            'band': band, 'out_of_band_total': sum(out_of_band.values()),
            'fired': len(out), 'split': split,
            'never_fired': [s.get('signal_key') for s in signals
                            if not any(k[0] == s.get('signal_key')
                                       for k in out)]}
    return out, meta


def write_registry(sport: str, results: dict) -> int:
    now = datetime.now(timezone.utc).isoformat()
    payload = []
    for r in results.values():
        payload.append({
            'signal_name': r['signal'],
            'sport': sport,
            'category': r['category'],
            'market_scope': r['market_scope'],
            'hit_rate': round(r['hit'] * 100.0, 2),
            'sample_n': r['n'],
            'tier': r['tier'],
            'edge_pp': round(r['edge_pp'], 2),
            # The price this signal actually had to beat. edge_weight uses it
            # as the bar instead of a flat -110, which is the whole point of
            # the exercise: three of these signals look like +6pp edges
            # against 52.4% and are under water against their own price.
            'baseline_pct': round(r['breakeven'] * 100.0, 2),
            'last_computed_at': now,
            'origin': 'calibrate_prop_signals',
            'notes': (f"graded from {PROPS_TABLE[sport]} in the "
                      f"publishable odds band; breakeven "
                      f"{r['breakeven']*100:.1f}% from the taken side's "
                      f"price; 95% CI [{r['lo']*100:.1f},{r['hi']*100:.1f}]; "
                      f"halves {('%.1f' % (r['hit_first']*100)) if r['hit_first'] is not None else 'na'}"
                      f"/{('%.1f' % (r['hit_second']*100)) if r['hit_second'] is not None else 'na'}"),
        })
    if not payload:
        return 0
    # on_conflict matches the table's own unique key, which includes
    # market_scope -- the collision prop_ensemble_scorer documents when it
    # looks a signal up WITHOUT scope.
    def _post(body):
        return requests.post(
            f'{SB}/rest/v1/signal_registry'
            '?on_conflict=signal_name,sport,market_scope',
            headers=dict(H_W, **{'Prefer': 'resolution=merge-duplicates,'
                                           'return=minimal'}),
            json=body, timeout=90)

    r = _post(payload)
    # PGRST204 = the column is not in PostgREST's schema cache, i.e.
    # 20261021e has not been applied. Drop baseline_pct and write the rest
    # rather than losing the whole calibration; the scorer treats a missing
    # baseline as plain BREAKEVEN, so this degrades to the old behaviour
    # instead of failing.
    if r.status_code == 400 and 'baseline_pct' in r.text:
        # ══ REFUSE. Writing without the baseline is WORSE than not writing ══
        # Stripping the column and writing anyway looks graceful and is not.
        # edge_weight would then compare these hit rates to a flat 52.4%:
        #
        #   nhl_prop_l10_extreme  hit 59.9%, own breakeven 60.4% (a LOSER)
        #     blind, as today          -> weight 0.20
        #     hit_rate, no baseline    -> edge 7.5pp -> weight 0.71
        #     hit_rate + baseline      -> edge <= 0  -> weight 0.00
        #
        # So the "degraded" path would QUADRUPLE the voice of a signal that
        # loses to its own price, which is the exact failure this whole
        # exercise exists to stop. Apply 20261021e first.
        print('  REFUSING TO WRITE: signal_registry has no baseline_pct '
              'column (apply migration 20261021e first).')
        print('  Writing hit rates without the baseline would make these '
              'signals LOUDER, not quieter: edge_weight would score '
              'nhl_prop_l10_extreme as a +7.5pp edge against a flat -110 '
              'bar when it loses to its own 60.4% breakeven.')
        return 0
    if r.status_code not in (200, 201, 204):
        print(f'  registry write {r.status_code}: {r.text[:300]}')
        return 0
    return len(payload)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(PROPS_TABLE))
    ap.add_argument('--guard-from', default=GUARD_FROM_DEFAULT,
                    help='exclude rows whose lookback predates this date '
                         '(the L5 leak guard); pass "" to include all')
    ap.add_argument('--no-band', action='store_true',
                    help='measure every price, including ones outside the '
                         'publishable -300..+150 band (not recommended: it '
                         'grades bets we would SKIP)')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not SB or not KEY:
        print('FAIL: SUPABASE_URL / service key missing')
        return 2

    print(f'=== calibrate prop signals · {a.sport} ===')
    band = None if a.no_band else (-300, 150)
    results, meta = evaluate(a.sport, a.guard_from, band)
    if not results:
        print('  no signal fired on any graded row — nothing to calibrate')
        return 1

    print(f'\n  {"signal":34} {"scope":10} {"n":>6} {"hit":>7} {"b/e":>7} '
          f'{"edge":>7} {"CI":>15} {"halves":>13}  tier')
    for r in sorted(results.values(), key=lambda x: (-x['n'], x['signal'])):
        h1 = f"{r['hit_first']*100:.0f}" if r['hit_first'] is not None else '—'
        h2 = f"{r['hit_second']*100:.0f}" if r['hit_second'] is not None else '—'
        print(f"  {r['signal'][:34]:34} {str(r['market_scope'])[:10]:10} "
              f"{r['n']:>6} {r['hit']*100:>6.1f}% {r['breakeven']*100:>6.1f}% "
              f"{r['edge_pp']:>+6.1f} "
              f"[{r['lo']*100:>5.1f},{r['hi']*100:>5.1f}] "
              f"{h1:>6}/{h2:<6} {r['tier']}")

    if meta.get('out_of_band_total'):
        print(f"\n  excluded {meta['out_of_band_total']} signal-firings "
              f"priced outside {meta['band'][0]}..+{meta['band'][1]} "
              f"(apply_odds_band_gate would SKIP those props)")
    by_tier = defaultdict(int)
    for r in results.values():
        by_tier[r['tier']] += 1
    print(f"\n  tiers: {dict(by_tier)}")
    if meta.get('never_fired'):
        print(f"  never fired on any graded row ({len(meta['never_fired'])}): "
              + ', '.join(meta['never_fired']))

    if a.apply:
        n = write_registry(a.sport, results)
        print(f'\n  wrote {n} registry row(s)')
    else:
        print('\n  DRY RUN — pass --apply to write signal_registry')
    return 0


if __name__ == '__main__':
    sys.exit(main())
