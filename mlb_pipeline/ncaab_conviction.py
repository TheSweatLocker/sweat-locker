"""Calibrated NCAAB conviction: the probability the pick wins.

== 2026-11-13 . CONVICTION WAS A DEAD CONSTANT, AND THE REBUILD SAYS WHY ===
ANDY: "rebuild conviction model."

WHAT IT WAS. NCAAB conviction came from ensemble_scorer:3071

    base          = 50 + min(win_score * 10, 30)
    margin_boost  = min(max(margin, 0) * 20, 15)
    conviction    = clamp(base + margin_boost, 45, 97)

win_score is a weighted vote over signal_registry, and every NCAAB signal
sits at 0.000-0.153 by construction (set_ncaab_signal_baseline: 2 of 65
PROVISIONAL, the rest 0.000 or a 0.020 prior). So win_score never leaves
~0.0-0.4 and conviction never leaves ~50-54: **266 of 274 live rows, 97%,
inside 50-54, max 62**, and primary_play.tier never above LEAN. The formula
was not broken; it was honestly reporting an empty signal library. No
rescaling of that number could fix it, because it carried no information to
rescale.

WHAT A CONVICTION CAN HONESTLY MEAN. Two different questions were riding on
one number: "how likely is this pick to win" (answerable) and "do we have an
edge" (measured below: we do not). This module answers only the first and
exposes the second as its own field, so a probability can never again be
read as an edge.

== THE MEASUREMENT ========================================================
479 graded games 2026-11-01..11-12, matched on {date, frozenset(home,away)}
so neutral-site orientation cannot drop rows
([[feedback_orientation_is_not_a_fact_at_a_neutral_site]]), closing lines
screened through posted_line.is_postable
([[feedback_consensus_line_must_be_posted]]). Walk-forward throughout: every
constant fitted on 11-01..11-06, every number below scored on 11-07..11-12,
which is the only honest test ([[feedback_walk_forward_misses_feature_leak]]).

The margin estimate is RECOMPUTED here from adj_em_gap + the MC and panel
blobs, never read from ncaab_game_context.projected_spread, because that
column holds two different models: every row through 11-06 carries the old
/3 divisor and every row from 11-07 carries 0.973, and the 11-06 fix was
never backfilled (177 of 363 rows stale). Mixing them reads as a 7.19-point
model bias that does not exist -- on the current formula alone the bias is
-1.40, CI [-3.14, +0.35], which spans zero.

FITTED ON TRAIN (n=179), margin = slope*adj_em_gap + HFA*(home court):

    slope 0.8715   HFA +3.63 pts        (full-sample refit: 0.8669 / +3.31)

The HFA term is the find. compute_projections has NO home-court term at all,
so its bias is -7.56 on home games and **+0.44 on neutral games, CI
[-3.56, +4.43], which spans zero** -- a textbook missing HFA, and the fitted
+3.63 lands on ncaab_mc_simulator.HFA_POINTS = 3.5 independently.

PREDICTING P(WIN), scored on the TEST half. Brier, lower is better:

    ML picks        n=156    CONSTANT (train base rate)   0.1975
                             our model alone              0.1577
                             MARKET de-vigged price       0.1543   <- best
                             blend at the fitted weight   0.1543
                             Platt-recalibrated model     0.1549

    SPREAD picks    n=167    CONSTANT (train base rate)   0.2553
                             market baseline (0.500)      0.2500   <- best
                             our model alone              0.2619
                             blend at the fitted weight   0.2510

The blend weight was fitted by minimising TRAIN Brier, not chosen here:

    ML      w_model = 0.00   the training half says ignore our model entirely
    SPREAD  w_model = 0.26   and that weight LOSES out of sample, 0.2510 vs
                             0.2500 for the market baseline it was meant to
                             beat

So our model beats a constant on ML and is beaten by the price; on spreads it
cannot beat a constant at all ([[feedback_level_model_must_beat_a_constant]]).
Both weights are therefore 0.0 and P(win) is the de-vigged price.

AND THE MODEL'S DISAGREEMENT WITH THE PRICE IS NOT WORTH BETTING:

    our P(win) - market P(win) >= 3pp   n=73  ROI  -7.2%  CI [-23.6, +10.3]
    same at >= 6pp                      n=46  ROI  -5.4%  CI [-28.9, +18.2]
    engine ATS picks, current formula   n=167  83-84  49.7%  CI [42.2, 57.2]
    engine total picks                  n=328  167-161  50.9%  CI [45.5, 56.3]
    MC moneyline picks                  n=315  247-68  78.4%  BUT ROI -7.1%
                                        CI [-13.2, -1.3], a CONFIRMED loser --
                                        297 of 315 are the favourite, so the
                                        hit rate is the price, not an edge
                                        ([[feedback_flat_110_inflates_every_ml_figure]])

ZERO WEIGHT IS NOT DISABLED. p_model is still computed, still returned, and
still written to the pick, so the weekly refit keeps measuring it and can
promote it the moment it earns a weight -- the same contract
set_ncaab_signal_baseline ships its 0.000 signals under.

== WHAT THIS DOES NOT CLAIM ===============================================
It does not claim an edge, and `model_edge_pp` is ~0 by construction while
the weights are 0. That is the brake, not a defect: an NCAAB pick cannot be
promoted on a phantom edge the way a 6-point total offset promoted UNDERs
([[feedback_a_constant_offset_reads_like_an_edge]]). The Sweat Card's own
replay agrees -- NCAAB went 0-7 on the card over 11-09..11-11.

CONVICTION IS NOT AN ELIGIBILITY GATE. P(win) is highest on the heaviest
chalk, which is exactly the display Andy objected to on 11-06 ("super juiced
chalk plays like ohio state ml"). Anything deciding whether to PUBLISH must
read model_edge_pp and the price, never this number alone. Callers that pin
a floor against the old 50-54 distribution -- generate_sweat_card
CARD_UNPROVEN_GATE['NCAAB'].min_conviction = 51, conviction_scale.ANCHORS
['NCAAB'] = (51, 52, 54, 62) -- are calibrated to a scale this module
replaces and MUST be re-pinned in the same change that adopts it, or every
pick clears them at once.

    python ncaab_conviction.py        # self-test against the stored numbers
"""
from __future__ import annotations

import math

#: margin = slope*adj_em_gap + HFA on home court. Fitted on 11-01..11-06.
ADJ_EM_SLOPE = 0.8715
HFA_POINTS = 3.63

#: sd of (our margin estimate - actual margin) on the train half.
MARGIN_SIGMA = 12.05
#: mean of the same residual. Subtracted so the estimate is centred.
MARGIN_BIAS = -1.80

#: Weight on OUR probability vs the de-vigged price, per market. Both fitted
#: by minimising train Brier and then scored out of sample -- see the module
#: docstring. Raise one only when a measurement says to.
W_MODEL = {'ml': 0.0, 'rl': 0.0, 'spread': 0.0, 'total': 0.0}

#: == 2026-11-13 · THE FLOOR WAS 50, AND IT INVENTED EDGE ON EVERY DOG ==
#: "A pick we would publish is at worst a coin flip" is reasoning about
#: SELECTION, and conviction is not a selection number: `model_edge.edge_pp`
#: is literally `conviction - implied_prob*100`. Clamping a dog UP to 50
#: therefore manufactures a positive edge out of the clamp itself. Measured
#: on tonight's board, against the live prices:
#:
#:     Pittsburgh ML      +135   implied 42.6   conv 50  ->  edge +7.4pp
#:     Mississippi St ML  +140   implied 41.7   conv 50  ->  edge +8.3pp
#:
#: Neither number came from the model. Worse, a POSITIVE edge is the one
#: case `cap_tier_for_edge` leaves alone, so the clamp did not just print a
#: wrong figure -- it switched OFF the only guard that demotes an unbacked
#: pick (feedback_magnitude_cannot_express_a_side: a floor cannot express a
#: side, and a dog is the side below 50).
#:
#: A probability is allowed to be below a coin flip. Bounds are sanity rails
#: only -- 0 and 100 are never honest outputs of a blended estimate.
CONV_MIN, CONV_MAX = 1, 99


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def implied_prob(odds) -> float | None:
    """American price -> raw implied probability, vig included."""
    o = _f(odds)
    if o is None or -100 < o < 100:
        return None
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def devig(odds_a, odds_b) -> float | None:
    """Two-way prices -> P(a), vig removed proportionally.

    Returns None unless BOTH sides are present: a one-sided price cannot be
    de-vigged, and treating its raw implied probability as fair would hand
    every favourite a few points of free conviction.
    """
    pa, pb = implied_prob(odds_a), implied_prob(odds_b)
    if pa is None or pb is None:
        return None
    s = pa + pb
    return pa / s if s > 0 else None


def margin_estimate(ctx: dict) -> float | None:
    """Our pre-game expected margin, + = home. Mean of whichever lenses are
    present: panel, MC, and adj_em_gap carried through the fitted slope and
    the HFA term compute_projections is missing.

    Deliberately NOT ncaab_game_context.projected_spread -- that column mixes
    the pre-11-07 /3 divisor with the current 0.973 and has no HFA.
    """
    parts = []
    pn = ctx.get('panel_prediction') or {}
    mc = ctx.get('mc_probabilities') or {}
    for v in (pn.get('panel_projected_margin'), mc.get('mc_expected_margin')):
        x = _f(v)
        if x is not None:
            parts.append(x)
    gap = _f(ctx.get('adj_em_gap'))
    if gap is None:
        h, a = _f(ctx.get('home_adj_em')), _f(ctx.get('away_adj_em'))
        if h is not None and a is not None:
            gap = h - a
    if gap is not None:
        hfa = 0.0 if ctx.get('is_neutral_site') else HFA_POINTS
        parts.append(gap * ADJ_EM_SLOPE + hfa)
    if not parts:
        return None
    return sum(parts) / len(parts) - MARGIN_BIAS


def p_model_ml(ctx: dict, side: str) -> float | None:
    """Our P(side wins outright), from the margin estimate."""
    m = margin_estimate(ctx)
    if m is None:
        return None
    p_home = _norm_cdf(m / MARGIN_SIGMA)
    s = str(side or '').strip().upper()
    if s in ('HOME', 'H'):
        return p_home
    if s in ('AWAY', 'A'):
        return 1.0 - p_home
    return None


def p_model_spread(ctx: dict, side: str, line) -> float | None:
    """Our P(side covers `line`), where line is that SIDE's handicap
    (-7.5 = laying 7.5)."""
    m = margin_estimate(ctx)
    ln = _f(line)
    if m is None or ln is None:
        return None
    s = str(side or '').strip().upper()
    if s in ('HOME', 'H'):
        return 1.0 - _norm_cdf((-ln - m) / MARGIN_SIGMA)
    if s in ('AWAY', 'A'):
        return 1.0 - _norm_cdf((-ln + m) / MARGIN_SIGMA)
    return None


def p_win(ctx: dict, market: str, side: str, *, line=None,
          odds=None, opp_odds=None) -> dict:
    """The calibrated probability this pick wins, and its parts.

    Returns {'p': float|None, 'p_model': float|None, 'p_market': float|None,
             'w_model': float, 'source': str}. `p` is None when nothing is
    known -- a caller must not substitute a default
    ([[feedback_falsy_zero_invented_a_tier_and_a_confidence]]).
    """
    mk = str(market or '').strip().lower()
    if mk in ('rl', 'spread', 'ats'):
        pm = p_model_spread(ctx, side, line)
    elif mk == 'ml':
        pm = p_model_ml(ctx, side)
    else:
        pm = None
    pk = devig(odds, opp_odds)
    w = W_MODEL.get(mk, 0.0)
    out = {'p': None, 'p_model': pm, 'p_market': pk, 'w_model': w,
           'raw_delta_pp': None, 'source': 'none'}
    if pm is not None and pk is not None:
        out['raw_delta_pp'] = round(100.0 * (pm - pk), 2)
    if pk is None:
        # ══ NO TWO-WAY PRICE -> NO CONVICTION ════════════════════════════
        # Letting the model alone answer here put conviction 97 on
        # `Louisville ML` on a game carrying no moneyline at all, and on 20
        # more like it, during the replay of this module against the 11-07..
        # 11-12 board. The model is the thing measured NOT to beat the
        # price, so with the price absent there is nothing left to be
        # confident from, and an unquoted game is exactly what
        # posted_market_gate already refuses to publish. None, not a
        # number ([[feedback_any_null_finding_must_be_investigated]]).
        out['source'] = 'unpriced' if pm is not None else 'none'
        return out
    if pm is None:
        out['p'], out['source'] = pk, 'market_only'
        return out
    out['p'], out['source'] = w * pm + (1.0 - w) * pk, 'blend'
    return out


def conviction(p) -> int | None:
    """Calibrated probability -> the 0-100 number the app displays."""
    x = _f(p)
    if x is None:
        return None
    return max(CONV_MIN, min(CONV_MAX, int(round(100.0 * x))))


def credited_edge_pp(res: dict) -> float | None:
    """The edge a publish gate is allowed to act on, in percentage points.

    == WHY THIS IS NOT JUST (p_model - p_market) ==========================
    The raw delta is large and confident on exactly the market where the
    model is measured WORST. Replaying this module over the 11-07..11-12
    board, the spread picks claimed:

        Abilene Christian +1.5   raw delta +23.81pp
        Dayton -19.5             raw delta +18.09pp
        Winthrop -9.5            raw delta +11.66pp

    and NCAAB spread picks measured 49.7% (n=167, CI [42.2, 57.2]) while the
    model's own P(cover) scored a WORSE Brier than a constant. So the raw
    delta on spreads is noise with a decimal point, and anything gating on
    it would publish precisely the junk. It is credited at the market's
    MEASURED weight, which is 0.0 everywhere today, so the answer is 0.0 --
    and `raw_delta_pp` stays on the pick for the weekly refit to measure
    ([[feedback_unmeasured_signal_must_not_outweigh_measured]]).
    """
    if not res:
        return None
    raw = _f(res.get('raw_delta_pp'))
    if raw is None:
        return None
    w = _f(res.get('w_model'))
    if w is None:
        return None
    return round(raw * w, 2)


def _self_test() -> int:
    """Assert the pieces against the numbers in the docstring."""
    bad = []

    def eq(got, want, tol, what):
        if got is None or abs(got - want) > tol:
            bad.append(f'{what}: got {got!r}, want ~{want}')

    # de-vig: -110/-110 is a coin flip; a two-way book sums to 1
    eq(devig(-110, -110), 0.5, 1e-9, 'devig(-110,-110)')
    p = devig(-800, 550)
    eq(p + devig(550, -800), 1.0, 1e-9, 'devig sums to 1')
    if not (0.85 < p < 0.92):
        bad.append(f'devig(-800,550) = {p!r}, expected ~0.88')
    # a one-sided price must NOT be treated as fair
    if devig(-800, None) is not None:
        bad.append('devig accepted a one-sided price')

    # HFA: the same matchup at a neutral site must project lower for home
    ctx = {'adj_em_gap': 10.0}
    home = margin_estimate(ctx)
    neut = margin_estimate({'adj_em_gap': 10.0, 'is_neutral_site': True})
    eq(home - neut, HFA_POINTS, 1e-6, 'HFA is applied on home court only')
    eq(home, 10.0 * ADJ_EM_SLOPE + HFA_POINTS - MARGIN_BIAS, 1e-6,
       'margin_estimate from gap')

    # a pick-em projects ~50%, and a 25-point gap well above it
    eq(p_model_ml({'adj_em_gap': 0.0, 'is_neutral_site': True}, 'HOME'),
       0.5, 0.07, 'p_model_ml on a neutral pick-em')
    big = p_model_ml({'adj_em_gap': 25.0}, 'HOME')
    if not (big and big > 0.95):
        bad.append(f'p_model_ml on a 25-point gap = {big!r}, expected >0.95')
    # the two sides of a market must sum to 1
    eq(p_model_ml(ctx, 'HOME') + p_model_ml(ctx, 'AWAY'), 1.0, 1e-9,
       'ml probabilities sum to 1')
    eq(p_model_spread(ctx, 'HOME', -7.5)
       + p_model_spread(ctx, 'AWAY', 7.5), 1.0, 1e-9,
       'spread probabilities sum to 1')

    # THE LOAD-BEARING ONE: with W_MODEL at 0, conviction is the price and
    # the claimed edge is zero. A future weight change must break this loudly.
    r = p_win({'adj_em_gap': 25.0}, 'ml', 'HOME', odds=-800, opp_odds=550)
    eq(r['p'], devig(-800, 550), 1e-9, 'p_win is the de-vigged price at w=0')
    eq(r['raw_delta_pp'], 100.0 * (r['p_model'] - r['p_market']), 0.01,
       'raw_delta_pp is model minus market')      # it rounds to 2dp
    if W_MODEL['ml'] == 0.0 and abs(r['p'] - r['p_market']) > 1e-9:
        bad.append('w_model=0 but p is not the market price')
    # An UNVALIDATED model must credit no edge, however loud its raw delta.
    if abs(r['raw_delta_pp']) < 5:
        bad.append('expected a large raw delta on a 25-point gap at -800')
    eq(credited_edge_pp(r), 0.0, 1e-9,
       'credited_edge_pp is 0 while w_model is 0')

    # An UNPRICED pick must carry NO conviction -- this is the Louisville
    # ML case, conviction 97 on a game with no moneyline.
    up = p_win({'adj_em_gap': 25.0}, 'ml', 'HOME', odds=None, opp_odds=None)
    if up['p'] is not None or conviction(up['p']) is not None:
        bad.append(f'unpriced pick produced a conviction: {up!r}')
    if up['source'] != 'unpriced':
        bad.append(f"unpriced source tag is {up['source']!r}")
    if up['p_model'] is None:
        bad.append('unpriced pick should still RECORD the model probability')
    # one-sided price is still unpriced
    if p_win({'adj_em_gap': 9.0}, 'ml', 'HOME', odds=-300)['p'] is not None:
        bad.append('a one-sided price produced a conviction')

    # == A DOG MUST BE ALLOWED BELOW A COIN FLIP ==========================
    # conviction feeds model_edge.edge_pp as a probability. A floor at 50
    # turns every underdog price into a fake positive edge, which is also
    # the one case the tier cap does not catch. Guard the real prices.
    import model_edge as _me
    for _odds, _lo, _hi in ((135, -2.0, 2.0), (140, -2.0, 2.0)):
        _p = devig(_odds, -int(_odds * 1.12))
        _c = conviction(_p)
        _e = _me.edge_pp(_c, _odds, 'ml')
        if _e is None or not (_lo <= _e <= _hi):
            bad.append(f'dog at {_odds:+d}: conviction {_c} prints edge '
                       f'{_e} -- the clamp is inventing it')
    if conviction(0.426) >= 50:
        bad.append('a 42.6% probability must not read as a coin flip')

    # conviction maps and clamps, and 0.0 is NOT falsy-defaulted
    eq(conviction(0.92), 92, 0, 'conviction(0.92)')
    eq(conviction(0.01), CONV_MIN, 0, 'conviction clamps low')
    eq(conviction(0.999), CONV_MAX, 0, 'conviction clamps high')
    if conviction(None) is not None:
        bad.append('conviction(None) must be None, not a default')
    if conviction(0.0) != CONV_MIN:
        bad.append('conviction(0.0) fell through a falsy check')

    # no data must not silently become a probability
    if p_win({}, 'ml', 'HOME')['p'] is not None:
        bad.append('p_win with no data must return p=None')

    if bad:
        print('FAIL')
        for b in bad:
            print('  -', b)
        return 1
    print('ncaab_conviction self-test OK')
    print(f'  slope {ADJ_EM_SLOPE}  HFA {HFA_POINTS:+.2f}  '
          f'sigma {MARGIN_SIGMA}  bias {MARGIN_BIAS:+.2f}')
    print(f'  W_MODEL {W_MODEL}  -> conviction is the de-vigged price, '
          f'claimed edge 0.0')
    return 0


if __name__ == '__main__':
    raise SystemExit(_self_test())
