"""Defensive gates — apply AFTER ensemble picks a play, BEFORE writing to DB.

2026-08-23 background: MC-dissent and OC-flip logic used to be inline blocks
in `game_context.py.upload_game_context()`. When `recompute_primary_play.py`
was written to re-run the ensemble after enrich_monte_carlo landed, the
gates got left behind on the game_context side — for weeks, recomputed picks
bypassed both defensive filters silently. Tonight's Orioles PRIME 86 (MC 40%)
and Mariners PRIME 84 (MC 35%) were the symptom that surfaced this.

This module consolidates every "after-the-ensemble picks, apply this
defensive filter" behavior in one place. Both the initial write path
(game_context.upload_game_context) and the recompute path
(recompute_primary_play.run) import + call these — so a gate can never
again live on one path but not the other.

Design rules:
- Every function mutates the passed `pp` dict in place AND returns it, so
  callers can write `pp = apply_mc_dissent_gate(pp, ctx)` (readable) OR
  just `apply_mc_dissent_gate(pp, ctx)` (mutating). Both work.
- Every function is defensive with `try/except: pass`. A gate error must
  NEVER block a pick from being written. Errors are silent by design —
  the primary_play still ships, just without that gate's protection.
- Every function checks `pp is None`, `pp.get('_engine') == 'ensemble_v2'`,
  and other preconditions internally. Callers can pass any pp/ctx blindly.
- Each mutation writes an `_XXX_gate_note` field on pp so audits can tell
  which gate touched a pick (and what the original state was).

Add new gates here as the same pattern: apply_<name>_gate(pp, ctx) -> pp.
"""
from __future__ import annotations

import os


def apply_mc_dissent_gate(pp: dict | None, ctx: dict) -> dict | None:
    """Demote ML PRIME/STRONG picks when Monte Carlo disagrees.

    2026-08-28 tightened: 8/28 lotto audit found 11 of 13 games with the
    ensemble publishing picks that MC disagreed with by 15-30pp. Old
    thresholds (52% for PRIME, 50% for STRONG) were too soft — CHC ML at
    -199 was shipping as STRONG 65 with MC only at 51.9%. Now:
        PRIME  requires MC >= 0.55  else -> LEAN  (was STRONG @ 0.52)
        STRONG requires MC >= 0.48  else -> LEAN  (was LEAN @ 0.50)
        Any tier with MC < 0.40 -> COVERAGE (blocks publish entirely)

    Preserves original tier + MC pct in pp['_mc_dissent'] audit field so
    downstream (Sharp Card, Jerry reads) can surface the demote reason.

    No-op when pp is None, non-ML, LEAN-or-lower, or MC absent.
    """
    try:
        if not (pp and isinstance(pp, dict)): return pp
        # 2026-09-27: was ensemble_v2 only, so an LR-override pick — which
        # REPLACES the ensemble one further down apply_all_defensive_gates
        # — was never checked against the simulation at all. On NHL
        # 2026-09-30 that shipped Toronto at conviction 68 and Winnipeg at
        # 75 from lr_v1 while the sim had both near 49%. Whichever engine
        # produced the pick, MC disagreeing with it means the same thing.
        if pp.get('_engine') not in ('ensemble_v2', 'lr_v1'): return pp
        ptype = str(pp.get('type', '')).lower()
        if ptype not in ('ml', 'total'): return pp
        if pp.get('tier') not in ('PRIME', 'STRONG', 'LEAN'): return pp

        mc = ctx.get('mc_probabilities') if isinstance(ctx.get('mc_probabilities'), dict) else None
        if not mc: return pp

        cur_side = str(pp.get('side', '')).upper()

        # ══ 2026-09-27 · THIS GATE HAD NEVER FIRED OUTSIDE MLB ══
        #
        # It read only mc_p_home_win / mc_p_away_win / mc_p_over /
        # mc_p_under, and MLB is the ONLY sport that writes those names.
        # Measured across the four context tables:
        #
        #     MLB    mc_p_home_win, mc_p_away_win, mc_p_over, mc_p_under
        #     NFL    mc_p_home, mc_p_away, mc_p_over_line
        #     NCAAF  mc_p_home, mc_p_away, mc_p_over_line
        #     NHL    mc_p_home, mc_p_over
        #
        # Every non-MLB sport therefore hit `pick_prob is None` and this
        # function returned the pick untouched — a silent no-op on three
        # sports, in a gate whose own docstring cites the exact failure it
        # exists to prevent ("PHI Under 8.0 shipped as STRONG 84 with -15pp
        # MC edge because gate skipped").
        #
        # Surfaced on NHL 2026-09-30, where Toronto shipped at conviction
        # 68 and Winnipeg at 75 while the sim had both at ~49% — a 25-point
        # disagreement between two models with nothing reconciling them.
        #
        # Fixed HERE rather than in four writers: one reader that accepts
        # the known spellings cannot drift, whereas four writers already
        # have. Away and under are derived from their complement when the
        # explicit key is absent, since a two-outcome market is fully
        # described by one side.
        def _mcp(*names, complement_of=None):
            for nm in names:
                v = mc.get(nm)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            if complement_of is not None:
                for nm in complement_of:
                    v = mc.get(nm)
                    if v is not None:
                        try:
                            return 1.0 - float(v)
                        except (TypeError, ValueError):
                            pass
            return None

        pick_prob = None
        if ptype == 'ml':
            if cur_side == 'HOME':
                pick_prob = _mcp('mc_p_home_win', 'mc_p_home',
                                 complement_of=('mc_p_away_win', 'mc_p_away'))
            elif cur_side == 'AWAY':
                pick_prob = _mcp('mc_p_away_win', 'mc_p_away',
                                 complement_of=('mc_p_home_win', 'mc_p_home'))
        elif ptype == 'total':
            # 2026-08-28 extended: totals were bypassing MC gate. PHI Under 8.0
            # shipped as STRONG 84 with -15pp MC edge because gate skipped.
            if cur_side == 'OVER':
                pick_prob = _mcp('mc_p_over', 'mc_p_over_line',
                                 complement_of=('mc_p_under',))
            elif cur_side == 'UNDER':
                pick_prob = _mcp('mc_p_under',
                                 complement_of=('mc_p_over', 'mc_p_over_line'))
        if pick_prob is None: return pp
        try:
            pick_prob_f = float(pick_prob)
        except (TypeError, ValueError):
            return pp

        new_tier = None
        reason = None
        # Hard block: MC has our side losing by 10pp+ implied → don't ship
        if pick_prob_f < 0.40:
            new_tier = 'COVERAGE'
            reason = f'MC hard dissent: sim has our side at {pick_prob_f*100:.1f}% (<40% blocks publish)'
        elif pp['tier'] == 'PRIME' and pick_prob_f < 0.55:
            new_tier = 'LEAN'
            reason = f'MC dissent: sim has our side at {pick_prob_f*100:.1f}% (<55% PRIME threshold)'
        elif pp['tier'] == 'STRONG' and pick_prob_f < 0.48:
            new_tier = 'LEAN'
            reason = f'MC dissent: sim has our side at {pick_prob_f*100:.1f}% (<48% STRONG threshold)'

        if new_tier:
            pp['_mc_dissent'] = {
                'orig_tier': pp['tier'],
                'mc_pick_win_pct': round(pick_prob_f * 100, 1),
                'reason': reason,
            }
            pp['tier'] = new_tier
            tier_cap = {'COVERAGE': 0, 'LEAN': 55, 'STRONG': 65}
            if isinstance(pp.get('conviction'), (int, float)):
                pp['conviction'] = min(int(pp['conviction']), tier_cap.get(new_tier, 55))
            # 2026-08-31: reset recommended_stake to 1.0 on dissent —
            # MC blocking the pick invalidates the "high-confidence bucket"
            # premise the 2u LOCK relied on.
            if pp.get('recommended_stake') and float(pp['recommended_stake']) > 1.0:
                pp['recommended_stake'] = 1.0
            # 2026-08-30: rewrite sub so user sees WHY engine passed, not
            # stale rationale. Prior version left pp['sub'] intact — a
            # PRIME "sharp money is here · H2H dominant" narrative on a
            # COVERAGE/conv=0 pick reads as a lie (Rays 8/30 canonical).
            top_signals = []
            for src in (pp.get('_ensemble_sources') or [])[:2]:
                p = src.get('prose')
                if p: top_signals.append(p)
            if new_tier == 'COVERAGE':
                header = f'⚠ Engine passed: MC sim has our side at {pick_prob_f*100:.0f}% win prob'
            else:
                header = f'⚠ Downgraded to {new_tier}: MC sim at {pick_prob_f*100:.0f}%'
            if top_signals:
                pp['sub'] = f'{header} — outweighs {" · ".join(top_signals)}'
            else:
                pp['sub'] = header
    except Exception:
        pass  # gate errors must never block the publish
    return pp


# 2026-09-01: sport-specific juice thresholds. NCAAF has wider chalk
# than MLB (50-point favorites price ML at -3000+), so the trap floor
# is looser. Per user directive: unless ML is looser than -300 for
# football, the "take" should be spread or total. MLB stays at -200
# (matches shipped feedback_heavy_fav_ml_trap_803 discipline).
_JUICE_TRAP_HEAVY_FAV_BY_SPORT = {
    'MLB':   -200,
    'NCAAF': -300,
    'NFL':   -300,
    'NCAAB': -400,   # basketball tolerates deeper chalk before rerouting
    'NBA':   -400,
}
# 2026-10-02 · Price past which a moneyline read is expressed on the spread
# instead. Distinct from _JUICE_TRAP_HEAVY_FAV_BY_SPORT above, which DEMOTES a
# tier at a looser football threshold (-300); this REROUTES the market at the
# -200 line that feedback_heavy_fav_ml_trap_803 and the queued
# "route -200+ ML to spread" decision both name. Single value, every sport:
# -200 is the documented rule and no sport-specific evidence contradicts it.
# Override with HEAVY_ML_THRESHOLD env for a shadow run at another level.
HEAVY_ML_THRESHOLD = int(os.environ.get('HEAVY_ML_THRESHOLD', '-200'))

_JUICE_TRAP_LONG_DOG_BY_SPORT = {
    'MLB':   +250,
    'NCAAF': +400,   # football underdogs regularly +400+ in mismatches
    'NFL':   +400,
    'NCAAB': +450,
    'NBA':   +450,
}


def _read_ml_price(pp: dict, ctx: dict) -> int | None:
    """Read the ML price for pp.side from ctx across sport-specific key aliases.

    MLB ctx uses `home_ml_close`; NCAAF ctx uses `close_home_ml`. This
    normalizes across both without forcing a schema change. Returns None
    when no key resolves or the value doesn't parse to int.
    """
    cur_side = str(pp.get('side', '')).upper()
    if cur_side == 'HOME':
        candidates = [
            ctx.get('home_ml_close'), ctx.get('home_ml_odds'), ctx.get('home_ml_open'),
            ctx.get('close_home_ml'), ctx.get('open_home_ml'),
        ]
    elif cur_side == 'AWAY':
        candidates = [
            ctx.get('away_ml_close'), ctx.get('away_ml_odds'), ctx.get('away_ml_open'),
            ctx.get('close_away_ml'), ctx.get('open_away_ml'),
        ]
    else:
        return None
    for p in candidates:
        if p is None: continue
        try: return int(p)
        except (TypeError, ValueError): continue
    return None


def apply_juice_trap_gate(pp: dict | None, ctx: dict, sport: str = 'MLB') -> dict | None:
    """Demote ML PRIME/STRONG picks with heavy-fav or long-dog trap prices.

    Per user memory feedback_heavy_fav_ml_trap_803 + prop juice-trap
    discipline: heavy-fav ML at -200+ is a documented trap; long-dog ML
    at +250+ is the mirror trap. 8/28 slate had NYY -173 (PRIME 92),
    ATL -229 (STRONG 79), CHC -199 (STRONG 78) all shipping through
    juice-trap prices with no gate.

    Rule (thresholds per-sport):
        odds <= heavy_fav_floor  → demote one tier (PRIME→STRONG, STRONG→LEAN)
        odds >= long_dog_ceiling → demote one tier

    NCAAF/NFL floor is -300 (user directive 2026-09-01) because football
    prices heavy favs into the -1000+ range that MLB never sees. NOTE:
    for full-market REROUTE (swap ML → spread/total when trapped), use
    `reroute_ml_if_trapped()` in tandem — this function only demotes.
    """
    try:
        if not (pp and isinstance(pp, dict)): return pp
        if str(pp.get('type', '')).lower() != 'ml': return pp
        if pp.get('tier') not in ('PRIME', 'STRONG'): return pp

        o = _read_ml_price(pp, ctx)
        if o is None: return pp

        heavy_fav_floor  = _JUICE_TRAP_HEAVY_FAV_BY_SPORT.get(sport, -200)
        long_dog_ceiling = _JUICE_TRAP_LONG_DOG_BY_SPORT.get(sport, +250)

        # Juice-trap band
        if o > heavy_fav_floor and o < long_dog_ceiling: return pp  # fair price

        old = pp['tier']
        new_tier = 'STRONG' if old == 'PRIME' else 'LEAN'
        trap_kind = f'heavy-fav trap <={heavy_fav_floor}' if o <= heavy_fav_floor \
                    else f'long-dog trap >=+{long_dog_ceiling}'
        reason = f'juice-trap demote ({sport}): side price {o} ({trap_kind})'
        pp['_juice_trap'] = {'orig_tier': old, 'side_price': o, 'reason': reason, 'sport': sport}
        pp['tier'] = new_tier
        tier_cap = {'LEAN': 55, 'STRONG': 65}
        if isinstance(pp.get('conviction'), (int, float)):
            pp['conviction'] = min(int(pp['conviction']), tier_cap.get(new_tier, 55))
    except Exception:
        pass
    return pp


def reroute_ml_if_trapped(decision, ctx: dict, sport: str = 'NCAAF'):
    """When ensemble's top pick is ML at trap-priced odds, reroute the
    `top_market` to spread or total using the ensemble's own scores.

    Motivation (2026-09-01): NCAAF Week 1 has 50-point favorites priced
    at -3000+ ML. Even when ensemble correctly scores Missouri as the
    winner, surfacing "Missouri ML -3000" is a garbage recommendation
    because no one lays that price. The correlated spread or total
    almost always carries the real edge.

    Rule:
      - If top_market == 'ml' AND ML price for pp.side violates the
        juice-trap band for this sport → find the next-best MarketDecision
        (rl or total) whose score >= LEAN floor and swap top_market to it.
      - If neither alt clears LEAN floor → leave decision.top_market
        untouched (a downstream tier demote will still catch it).

    Returns decision (mutated in place). No-op if decision is None or
    top_market isn't ML.
    """
    try:
        if decision is None: return decision
        if getattr(decision, 'top_market', None) != 'ml': return decision

        top = decision.top()
        if not top or not top.pick: return decision

        # Build a fake pp for _read_ml_price
        fake_pp = {'side': top.side, 'type': 'ml'}
        o = _read_ml_price(fake_pp, ctx)
        if o is None: return decision

        heavy_fav_floor  = _JUICE_TRAP_HEAVY_FAV_BY_SPORT.get(sport, -200)
        long_dog_ceiling = _JUICE_TRAP_LONG_DOG_BY_SPORT.get(sport, +250)
        if o > heavy_fav_floor and o < long_dog_ceiling: return decision

        # 2026-09-03 USER DIRECTIVE ("don't show -3000 ML as a take,
        # pick spread or total when juicier than -300"):
        # ALWAYS reroute juiced ML to the best-scoring alt market
        # regardless of alt's absolute score. Prior LEAN_FLOOR=0.3 guard
        # left "Rutgers ML" (at -9000) intact because both spread and
        # total scored below 0.3 on Rutgers @ UMass. User doesn't want
        # a juiced ML to survive as the take; a low-signal spread with
        # a LOW CONVICTION chip is a better UX than a garbage ML price.
        # 2026-09-29 · A REROUTE MUST NOT LAND IN A BARRED MARKET.
        # This runs AFTER score_game has already excluded barred markets
        # from the top-pick race, and it mutates top_market directly — so a
        # trapped ML was being rerouted straight into a football total.
        # Measured: 8 of 67 upcoming NCAAF rows carried a `total` pick from
        # ensemble_v2 even though score_game returns ml/rl for every one of
        # them. This loop is why, and it even tries 'total' FIRST.
        #
        # Third path today that undid the same suppression by choosing or
        # keeping a prior/alternative market: the market-selection stability
        # gate, pick_lock.preserve_published, and now this. Any code that
        # re-picks a market has to consult the same barred set.
        try:
            from ensemble_scorer import TOP_PICK_BARRED
            _barred = TOP_PICK_BARRED.get(str(sport or '').upper(), set())
        except Exception:
            _barred = set()
        candidates = []
        for alt_market in ('rl', 'total'):
            if alt_market in _barred: continue
            alt = getattr(decision, alt_market, None)
            if alt is None or not alt.pick: continue
            candidates.append((alt_market, alt))
        if not candidates:
            # Truly no non-ML alternative — leave as ML for downstream
            # juice-trap demote (COVERAGE + LOW CONVICTION chip in UI).
            return decision
        # Pick the highest-scoring alternative (any score)
        candidates.sort(key=lambda x: -float(x[1].score))
        new_market, new_top = candidates[0]

        orig_market = decision.top_market
        orig_label = top.display_label
        decision.top_market = new_market
        # Attach an audit trail on the new top so the write path can
        # stamp it into primary_play for observability.
        setattr(new_top, '_ml_reroute', {
            'orig_market': orig_market,
            'orig_label': orig_label,
            'orig_ml_price': o,
            'reason': f'ML price {o} in juice-trap band ({sport}: '
                      f'<={heavy_fav_floor} or >=+{long_dog_ceiling}); '
                      f'rerouted to {new_market} (score {new_top.score:.2f})',
        })
    except Exception:
        pass
    return decision


def apply_oc_flip_gate(pp: dict | None, ctx: dict) -> dict | None:
    """Flip ensemble picks when OddsCrowd sharp $ conviction dissents.

    Empirical from 14d audit: when ensemble picks side X on ML/RL/TOTAL and
    OC has money% >= 60 on the OPPOSITE side, our pick lost 7-30 (81%).
    Flipping to OC's side would have won 30-7 (81%). Same principle for
    RL / TOTAL — OC dissent with money conviction is a real reversal signal.

    Applied post-ensemble so the audit trail preserves the ensemble's raw
    pick alongside the flipped output. Downgrades tier one step to signal
    the flip is a defensive move (not the ensemble's original conviction).

    No-op when pp is None, non-ensemble, or OC absent.
    """
    try:
        if not (pp and isinstance(pp, dict)): return pp
        if pp.get('_engine') != 'ensemble_v2': return pp

        oc = ctx.get('oddscrowd_snapshot') if isinstance(ctx.get('oddscrowd_snapshot'), dict) else None
        if not oc: return pp

        mkt = str(pp.get('type', '')).lower()
        if mkt not in ('ml', 'rl', 'total'): return pp

        cur_side = str(pp.get('side', '')).upper()
        if not cur_side: return pp

        seg = oc.get(mkt) if isinstance(oc.get(mkt), dict) else None
        if not seg or not seg.get('pick'): return pp

        oc_pick = str(seg.get('pick', '')).upper()
        try:
            oc_money = float(seg.get('money') or 0)
        except (TypeError, ValueError):
            oc_money = 0.0

        if not oc_pick or oc_pick == cur_side:
            return pp

        # 2026-08-25 per-market threshold tightening.
        # 14d audit split by market:
        #   ML/RL dissent @ money>=60  → 81% fade edge (7-30) ✓ keep
        #   TOTAL dissent @ money>=60  → 55% fade edge only (12-10) — noise
        #   TOTAL dissent @ money>=70  → 76% fade edge (3-13) ✓ tighter
        # Totals get more market money at fair juice, so OC-alone dissent
        # doesn't clear the noise floor until money% is louder. Require
        # money>=70 on totals; keep 60 on ML/RL.
        money_threshold = 70 if mkt == 'total' else 60
        if oc_money < money_threshold:
            return pp

        # 2026-08-26 MC-DISSENT BLOCK. 8/25 audit finding: OC-flip fell
        # 1-5 that night (running 9-7 vs the 8-2 head start). The one
        # obvious loss was Over 8.5 COL/WSH — OC 90% money OVER triggered
        # a flip, but MC sim projected 6.31 total (81% UNDER probability).
        # MC was right (actual 4). Rule: if MC has HIGH probability for the
        # side we're about to flip AWAY from, respect the sim and skip the
        # flip. Thresholds set conservatively so this only blocks the
        # loudest MC dissents.
        mc = ctx.get('mc_probabilities')
        if isinstance(mc, dict):
            mc_block = False
            # 2026-08-26 threshold tightening (audit rec). Rockies UNDER
            # PRIME 97 had MC 70.3% OVER — right at the old threshold, passed
            # by rounding, flip proceeded. Lower to 60% for totals, 58% for
            # ML so meaningful MC dissent blocks the flip. Track _oc_flip_
            # blocked outcomes for 30d and revisit if fade edge holds.
            if mkt == 'total':
                # Flipping AWAY from cur_side (Under) → Over means MC's
                # mc_p_under is the "prob the original side wins."
                # Block if MC has >=60% conviction on the original side.
                mc_prob_orig = None
                if cur_side == 'UNDER':
                    mc_prob_orig = mc.get('mc_p_under')
                elif cur_side == 'OVER':
                    mc_prob_orig = mc.get('mc_p_over')
                try:
                    if mc_prob_orig is not None and float(mc_prob_orig) >= 0.60:
                        mc_block = True
                except (TypeError, ValueError):
                    pass
            elif mkt == 'ml':
                mc_prob_orig = None
                if cur_side == 'HOME':
                    mc_prob_orig = mc.get('mc_p_home_win') or mc.get('mc_home_win_prob')
                elif cur_side == 'AWAY':
                    mc_prob_orig = mc.get('mc_p_away_win') or mc.get('mc_away_win_prob')
                try:
                    if mc_prob_orig is not None and float(mc_prob_orig) >= 0.58:
                        mc_block = True
                except (TypeError, ValueError):
                    pass
            if mc_block:
                # Attach an audit note so we can see WHY flip was skipped.
                pp['_oc_flip_blocked'] = {
                    'reason': f'MC dissent block: MC has {float(mc_prob_orig)*100:.0f}% '
                              f'conviction on {cur_side} — skipping OC-flip to {oc_pick}.',
                    'oc_money_pct': oc_money,
                    'oc_pick': oc_pick,
                    'mc_prob_orig_side': float(mc_prob_orig),
                }
                return pp

        # OC dissents with money conviction — flip.
        orig_side = cur_side
        orig_label = pp.get('label')
        orig_tier = pp.get('tier')

        flip_map = {'HOME': 'AWAY', 'AWAY': 'HOME', 'OVER': 'UNDER', 'UNDER': 'OVER'}
        new_side = flip_map.get(cur_side, cur_side)

        home = ctx.get('home_team', 'HOME')
        away = ctx.get('away_team', 'AWAY')
        cs = ctx.get('close_spread')
        ct = ctx.get('close_total')
        if mkt == 'ml':
            new_label = f'{home} ML' if new_side == 'HOME' else f'{away} ML'
        elif mkt == 'rl':
            try:
                line = float(cs) if new_side == 'HOME' else -float(cs)
                new_label = f"{home if new_side == 'HOME' else away} {line:+g}"
            except (TypeError, ValueError):
                new_label = f"{home if new_side == 'HOME' else away} RL"
        elif mkt == 'total':
            try:
                new_label = f"{'Over' if new_side == 'OVER' else 'Under'} {float(ct)}"
            except (TypeError, ValueError):
                new_label = 'Over' if new_side == 'OVER' else 'Under'
        else:
            new_label = pp.get('label')

        tier_step = {'PRIME': 'STRONG', 'STRONG': 'LEAN', 'LEAN': 'LEAN'}
        new_tier = tier_step.get(orig_tier or 'LEAN', 'LEAN')

        pp['side'] = new_side
        pp['label'] = new_label
        pp['tier'] = new_tier
        pp['_oc_flipped'] = {
            'orig_side': orig_side,
            'orig_label': orig_label,
            'orig_tier': orig_tier,
            'oc_pick': oc_pick,
            'oc_money_pct': oc_money,
            'reason': (f'OC dissent flip: money% {oc_money:.0f} on {oc_pick} '
                       f'vs our {orig_side}. 14d dissent-band record 7-30 (81% fade edge).'),
        }
        pp['sub'] = (f'OC-dissent flip. Ensemble had {orig_label}; '
                     f'OC has {oc_money:.0f}% money on the other side.')
    except Exception:
        pass
    return pp


def apply_publish_gate(pp: dict | None, ctx: dict) -> dict | None:
    """Hard-block PRIME publishes that violate any of the audit's
    publish-gate rules. Demotes to STRONG (never all the way to LEAN —
    the ensemble scored it high for a reason, we're just reducing user-
    facing conviction). Attaches `_publish_gate_reason` for audit.

    Rules (any one triggers demote):
      1. fade_share > 0.30 AND MC doesn't concur with pick by >=52%
         (fade stack driving pick without live model confirmation)
      2. sum(contribs where signal_n < 25) / adjusted_total > 0.40
         (pick is >40% ramp-up-prior — no proven evidence)
      3. TOTAL pick where 3 of {jerry_pred_total, projected_total,
         mc_mean_total} exist but fewer than 2 agree with pick within
         1.0 unit
      4. ML pick where MC pick-side prob < 0.50 (already handled by
         apply_mc_dissent_gate but include as safety)

    Only fires on PRIME tier — STRONG/LEAN pass through untouched.
    """
    try:
        if not (pp and isinstance(pp, dict)): return pp
        if pp.get('tier') != 'PRIME': return pp
        if pp.get('_engine') != 'ensemble_v2': return pp

        reasons = []
        sources = pp.get('_ensemble_sources') or []
        if not sources:
            return pp

        # Compute shares
        total_contrib = sum((s.get('contribution') or 0) for s in sources)
        if total_contrib <= 0: return pp
        fade_contrib = sum((s.get('contribution') or 0) for s in sources
                           if (s.get('signal_key') or '').endswith('_fade'))
        thin_contrib = sum((s.get('contribution') or 0) for s in sources
                           if (s.get('n') or 0) < 25)
        fade_share = fade_contrib / total_contrib
        thin_share = thin_contrib / total_contrib

        # Rule 1: fade-heavy without MC concurrence
        mkt = str(pp.get('type', '')).lower()
        side = str(pp.get('side', '')).upper()
        mc = ctx.get('mc_probabilities') if isinstance(ctx.get('mc_probabilities'), dict) else None
        mc_our = None
        if mc and mkt == 'total':
            mc_our = mc.get('mc_p_over') if side == 'OVER' else mc.get('mc_p_under')
        elif mc and mkt == 'ml':
            mc_our = mc.get('mc_p_home_win') if side == 'HOME' else mc.get('mc_p_away_win')
        try:
            mc_our_f = float(mc_our) if mc_our is not None else None
        except (TypeError, ValueError):
            mc_our_f = None

        if fade_share > 0.30 and (mc_our_f is None or mc_our_f < 0.52):
            reasons.append(f'fade_share={fade_share:.2f}>0.30 without MC concurrence '
                          f'(mc_our={mc_our_f})')

        # Rule 2: >40% ramp-up-prior chip contribution
        if thin_share > 0.40:
            reasons.append(f'thin_share={thin_share:.2f}>0.40 (pick riding tiny-sample signals)')

        # Rule 3: TOTAL model concurrence check.
        # 2026-08-27 magnitude weighting. Prior version counted binary
        # agreements: "agrees within 1.0 unit" = +1, else 0. This let picks
        # pass when 2/3 models mildly agreed but 1 model VIOLENTLY dissented.
        # LAD/ATL 8/26: Jerry projected 10.84 vs line 8.5 (+2.34 OVER on an
        # UNDER pick). Panel 7.77 UNDER, MC 7.54 UNDER — 2/3 agree, but
        # Jerry's +2.34 dissent was a screaming red flag the gate ignored.
        # Actual: 11 runs. UNDER lost.
        # New: any single model dissenting by >=2.0 units against the pick
        # triggers demotion, even when other models agree. Also keep the
        # 2/3 vote floor as backup.
        SEVERE_DISSENT_UNITS = 2.0
        if mkt == 'total':
            line = ctx.get('close_total')
            model_agreements = 0
            model_checked = 0
            severe_dissenters = []
            def _check_model(name, val, line_v, pick_side):
                nonlocal model_agreements, model_checked
                if val is None or line_v is None: return
                try:
                    diff = float(val) - float(line_v)
                except (TypeError, ValueError):
                    return
                model_checked += 1
                if pick_side == 'OVER':
                    if diff > -1.0: model_agreements += 1
                    if diff <= -SEVERE_DISSENT_UNITS:
                        severe_dissenters.append(f'{name} projects {val} vs line {line_v} '
                                                 f'({diff:+.2f} units UNDER of an OVER pick)')
                elif pick_side == 'UNDER':
                    if diff < 1.0: model_agreements += 1
                    if diff >= SEVERE_DISSENT_UNITS:
                        severe_dissenters.append(f'{name} projects {val} vs line {line_v} '
                                                 f'({diff:+.2f} units OVER of an UNDER pick)')
            _check_model('jerry_pred_total', ctx.get('jerry_pred_total'), line, side)
            _check_model('projected_total', ctx.get('projected_total'), line, side)
            if mc:
                _check_model('mc_mean_total', mc.get('mc_mean_total'), line, side)
            if severe_dissenters:
                reasons.append(f'TOTAL PRIME with severe model dissent: '
                              + '; '.join(severe_dissenters))
            elif model_checked >= 2 and model_agreements < 2:
                reasons.append(f'TOTAL PRIME with only {model_agreements}/{model_checked} '
                              f'model projections agreeing within 1.0 unit')

        # Rule 4: ML MC dissent below 50% (belt-and-suspenders)
        if mkt == 'ml' and mc_our_f is not None and mc_our_f < 0.50:
            reasons.append(f'ML PRIME with MC {mc_our_f:.2%} on pick side (<50%)')

        if reasons:
            pp['_publish_gate_demoted'] = {
                'orig_tier': pp['tier'],
                'orig_conviction': pp.get('conviction'),
                'reasons': reasons,
                'fade_share': round(fade_share, 3),
                'thin_share': round(thin_share, 3),
            }
            pp['tier'] = 'STRONG'
            if isinstance(pp.get('conviction'), (int, float)):
                # STRONG conviction cap = 84 (PRIME floor is 85)
                pp['conviction'] = min(int(pp['conviction']), 84)
    except Exception:
        pass  # gate errors must never block publish
    return pp


def apply_ncaaf_large_spread_gate(pp: dict | None, ctx: dict) -> dict | None:
    """2026-09-02 NCAAF-specific gate: dogs on large spreads (>=20pt)
    get demoted when multiple context signals fade the dog.

    Discovered on Stanford +24.5 vs Miami where scorer produced STRONG
    with 6 weak signals, ignoring:
      - MC: mc_p_home = 21.5% (Stanford wins ~1-in-5)
      - AP rank: Miami #7 vs Stanford unranked
      - Talent: Stanford 705 vs Miami 886 (+180 gap)
      - SP+ gap: -24.3 (matches market spread — no line value)

    Rule: if backing a DOG on a spread with |spread| >= 20 AND >=2
    of the following context anti-signals fire → demote one tier:
      (a) mc_p_dog_side < 0.30
      (b) fav ap_rank <= 15 AND dog ap_rank is null (unranked)
      (c) talent_gap_against_dog >= 150 points
      (d) sp_gap AGAINST dog >= 20 (line is FAIR, not exploitable)

    Preserves audit trail in pp['_ncaaf_large_spread_gate'].
    Sport-scoped — only touches NCAAF picks.
    """
    try:
        if not (pp and isinstance(pp, dict)): return pp
        if pp.get('_engine') != 'ensemble_v2': return pp
        ptype = str(pp.get('type', '')).lower()
        if ptype != 'rl': return pp  # only spread picks
        tier = pp.get('tier')
        if tier not in ('PRIME', 'STRONG'): return pp

        # Must be an NCAAF context — presence of home_sp_plus is a good tell
        if ctx.get('home_sp_plus') is None and ctx.get('away_sp_plus') is None:
            return pp

        close_spread = ctx.get('close_spread')
        if close_spread is None: return pp
        try: spread_abs = abs(float(close_spread))
        except (TypeError, ValueError): return pp
        if spread_abs < 20: return pp  # only large-spread games

        # Determine which side is the DOG. In our convention close_spread
        # is home-relative (positive = home is dog per CFBD convention).
        cur_side = str(pp.get('side', '')).upper()
        pick_is_home = 'HOME' in cur_side
        dog_side = 'HOME' if float(close_spread) > 0 else 'AWAY'
        if pick_is_home and dog_side != 'HOME': return pp
        if not pick_is_home and dog_side != 'AWAY': return pp
        # We're backing the DOG on a >=20pt spread — apply anti-signal count

        anti = []

        # (a) MC dog probability < 0.30
        mc = ctx.get('mc_probabilities') if isinstance(ctx.get('mc_probabilities'), dict) else {}
        mc_dog_p = mc.get('mc_p_home') if dog_side == 'HOME' else mc.get('mc_p_away')
        try:
            if mc_dog_p is not None and float(mc_dog_p) < 0.30:
                anti.append(f'MC_dog_pct={float(mc_dog_p)*100:.0f}%')
        except (TypeError, ValueError): pass

        # (b) AP rank differential — dog unranked, fav top-15
        dog_ap = ctx.get('home_ap_rank') if dog_side == 'HOME' else ctx.get('away_ap_rank')
        fav_ap = ctx.get('away_ap_rank') if dog_side == 'HOME' else ctx.get('home_ap_rank')
        try:
            if dog_ap is None and fav_ap is not None and int(fav_ap) <= 15:
                anti.append(f'AP_gap_fav_#{int(fav_ap)}_dog_unranked')
        except (TypeError, ValueError): pass

        # (c) Talent composite gap
        dog_talent = ctx.get('home_talent') if dog_side == 'HOME' else ctx.get('away_talent')
        fav_talent = ctx.get('away_talent') if dog_side == 'HOME' else ctx.get('home_talent')
        try:
            if dog_talent is not None and fav_talent is not None:
                gap = float(fav_talent) - float(dog_talent)
                if gap >= 150:
                    anti.append(f'talent_gap_{gap:.0f}pts_against')
        except (TypeError, ValueError): pass

        # (d) SP+ gap matches market — line is FAIR, no value
        sp_gap = ctx.get('sp_gap')
        try:
            if sp_gap is not None:
                # SP+ home-relative; if we're backing home_dog and sp_gap is very
                # negative (say -20+), SP+ says line is fair; likewise for away.
                sp_gap_val = float(sp_gap)
                if (dog_side == 'HOME' and sp_gap_val <= -20) or \
                   (dog_side == 'AWAY' and sp_gap_val >= 20):
                    anti.append(f'SP+_gap_matches_line_{sp_gap_val:+.1f}')
        except (TypeError, ValueError): pass

        if len(anti) < 2: return pp  # need 2+ anti-signals to trigger

        # Demote: PRIME -> LEAN, STRONG -> LEAN. Two-tier drop from PRIME
        # because these picks are structurally weak.
        new_tier = 'LEAN'
        pp['_ncaaf_large_spread_gate'] = {
            'original_tier': tier,
            'demoted_to': new_tier,
            'reason': 'backing_large_spread_dog',
            'anti_signals': anti,
            'spread': close_spread,
            'dog_side': dog_side,
        }
        pp['tier'] = new_tier
        # Update audit note visible in downstream reads
        prev_note = pp.get('audit_note') or ''
        pp['audit_note'] = f'{prev_note} · ncaaf_large_spread_dog_gate: {tier}->{new_tier} ({len(anti)} anti-signals)'.strip(' ·')
    except Exception:
        pass  # never block the pipeline on gate error
    return pp


import json as _json
import math as _math
from pathlib import Path as _Path

# 2026-09-03 SUPERVISED LR MODELS — load once at import.
# All fall back silently if model file missing (legacy scorer wins).
def _load_lr_model(filename: str):
    try:
        p = _Path(__file__).parent / 'models' / filename
        return _json.loads(p.read_text()) if p.exists() else None
    except Exception: return None

_LR_MODEL_MLB_ML      = _load_lr_model('mlb_ml_logreg.json')
_LR_MODEL_NFL_ML      = _load_lr_model('nfl_ml_logreg.json')
_LR_MODEL_NCAAF_ML    = _load_lr_model('ncaaf_ml_logreg.json')
# 2026-09-14: NHL + NBA LR ML models. Trained on 2024-25 season market
# features backfilled via The Odds API historical (v1.0.1 #12). NHL test
# acc 64.8% (+7.5pp lift over 57.4% baseline); NBA 67.1% (+10.3pp over
# 56.8%). Both features arrays: NHL uses close_puckline (single INT =
# home puckline odds), NBA uses close_spread. See backfill_historical
# _odds_hoops_hockey.py + project_v1_0_1_client_priorities #12.
_LR_MODEL_NHL_ML      = _load_lr_model('nhl_ml_logreg.json')
_LR_MODEL_NBA_ML      = _load_lr_model('nba_ml_logreg.json')
_LR_MODEL_MLB_TOTAL   = _load_lr_model('mlb_total_logreg.json')
_LR_MODEL_NCAAF_TOTAL = _load_lr_model('ncaaf_total_logreg.json')
# 2026-09-08: NFL total LR model. Trained on 6 seasons (2020-2025 =
# 1,693 games via nflverse backfill). Test lift +0.4pp — same weak
# territory as NCAAF total (52.4%), so wired SHADOW-ONLY. Legit
# signal for flagging pipeline disagreement even though too weak to
# promote its own picks. See apply_nfl_total_lr_override below +
# project_lr_totals_investigation_908.
_LR_MODEL_NFL_TOTAL   = _load_lr_model('nfl_total_logreg.json')


_BLIND_CACHE: dict = {}


def _blind_prediction(m) -> float:
    """What this model outputs when EVERY feature is missing.

    A logistic model with all inputs imputed to their training medians
    produces one fixed number. It is a property of the model, not of any
    game, so it is computed once and cached by id().
    """
    key = id(m)
    if key in _BLIND_CACHE:
        return _BLIND_CACHE[key]
    z = m['intercept']
    for i, _f in enumerate(m['features']):
        v = m['imputer_medians'][i]
        scale = m['scaler_scale'][i]
        z += m['coefficients'][i] * ((v - m['scaler_mean'][i]) / scale if scale != 0 else 0)
    p = 1.0 / (1.0 + _math.exp(-z)) if z >= 0 else _math.exp(z) / (1.0 + _math.exp(z))
    _BLIND_CACHE[key] = p
    return p


def _is_blind(p: float, n_imputed: int, m) -> bool:
    """True when the model had nothing to go on.

    2026-09-23. `_lr_predict_*` silently replaces any missing feature
    with its training median. That is fine for an occasional gap and
    catastrophic when every feature is missing, because the model then
    emits a CONSTANT that the caller reads as a confident opinion:

        MLB   TOTAL  ->  p_over  = 0.4718   inside the [0.45,0.55]
        NFL   TOTAL  ->  p_over  = 0.4724   "coin flip" band, so the
        NCAAF TOTAL  ->  p_over  = 0.4911   pick is KILLED
        MLB   ML     ->  p_home  = 0.4284   outside the band, so a pick
        NFL   ML     ->  p_home  = 0.5599   is INVENTED out of medians
        NHL   ML     ->  p_home  = 0.6024
        NBA   ML     ->  p_home  = 0.5936

    Measured on MLB 08-01..09-23: 36 of 178 games (20%) carried exactly
    0.4718, and 34 of them were demoted to COVERAGE and published as
    "engine passed". Twenty-two had a PRIME ensemble pick underneath —
    including a conviction-100 play — thrown away because a different
    model had no features loaded. Removing just those takes the MLB
    no-play rate from 27.4% to 8.4%.

    The test is exact rather than a threshold. A fraction-of-imputed
    cutoff cannot work: NCAAF totals run 65% imputed in normal healthy
    operation, so any cutoff low enough to catch the blind case would
    silence that model permanently. Landing on the blind constant to
    within 1e-9 is what actually identifies "no information".
    """
    if n_imputed >= len(m['features']):
        return True
    return abs(p - _blind_prediction(m)) < 1e-9


def _lr_predict_ml(ctx: dict, model=None) -> dict | None:
    """Returns {p_home_win, suggested_side, suggested_tier} or None.
    Defaults to MLB model — pass model=<sport-specific> for others."""
    m = model if model is not None else _LR_MODEL_MLB_ML
    if m is None: return None
    try:
        features = m['features']
        z = m['intercept']
        n_imputed = 0
        for i, f in enumerate(features):
            v = ctx.get(f)
            try: v = float(v) if v is not None else None
            except (TypeError, ValueError): v = None
            if v is None:
                v = m['imputer_medians'][i]
                n_imputed += 1
            scale = m['scaler_scale'][i]
            scaled = (v - m['scaler_mean'][i]) / scale if scale != 0 else 0
            z += m['coefficients'][i] * scaled
        if z >= 0: p = 1.0 / (1.0 + _math.exp(-z))
        else: ez = _math.exp(z); p = ez / (1.0 + ez)
        # No data is not an opinion. Returning None leaves the ensemble's
        # own pick standing instead of inventing one from medians.
        if _is_blind(p, n_imputed, m):
            return None
        if p >= 0.65:   tier, side = 'PRIME',  'HOME'
        elif p >= 0.55: tier, side = 'STRONG', 'HOME'
        elif p >= 0.45: tier, side = None,     'NONE'   # COIN → no play
        elif p >= 0.35: tier, side = 'STRONG', 'AWAY'
        else:           tier, side = 'PRIME',  'AWAY'
        return {'p_home_win': round(p, 4), 'suggested_side': side, 'suggested_tier': tier}
    except Exception:
        return None


def apply_ml_lr_override(pp: dict | None, ctx: dict, sport: str = 'MLB') -> dict | None:
    """Generic ML LR override — pass sport to pick the right model.
    See apply_mlb_ml_lr_override for full docstring."""
    _model_map = {
        'MLB':   _LR_MODEL_MLB_ML,
        'NFL':   _LR_MODEL_NFL_ML,
        'NCAAF': _LR_MODEL_NCAAF_ML,
        'NHL':   _LR_MODEL_NHL_ML,
        'NBA':   _LR_MODEL_NBA_ML,
    }
    model = _model_map.get(sport)
    if model is None: return pp
    return _apply_ml_lr_override_impl(pp, ctx, model, sport)


def _ml_odds_too_juiced(ctx: dict, side: str, max_juice: int = -300) -> bool:
    """Return True if ML odds on the picked side are worse than max_juice.
    Default -300: anything at -301 or juicier (e.g., -500, -1000) fails.

    Also blocks when odds are UNKNOWN for large-spread favorite chalks —
    FCS-vs-FBS blowouts (spread magnitude > 20) typically don't have
    published moneylines because the juice would be -1000 to -100000+
    (no book wants to write it). Rather than publish a PRIME on unknown
    juice, treat as too-juiced and let the spread/total win the market.
    """
    try:
        home_ml = ctx.get('close_home_ml') or ctx.get('home_ml_odds') or ctx.get('home_ml_close')
        away_ml = ctx.get('close_away_ml') or ctx.get('away_ml_odds') or ctx.get('away_ml_close')
        odds = home_ml if side == 'HOME' else away_ml
        if odds is None:
            # Unknown juice — check spread. If picking the FAVORITE on a
            # >20pt spread, it's almost certainly deep chalk (>-1000).
            try:
                spr = float(ctx.get('close_spread') or 0)
                home_is_fav = spr < 0
                picking_fav = (side == 'HOME' and home_is_fav) or (side == 'AWAY' and not home_is_fav)
                if picking_fav and abs(spr) > 20:
                    return True  # treat unknown-odds heavy chalk as too juiced
            except (TypeError, ValueError): pass
            return False  # unknown odds on close game — don't block
        o = int(odds)
        # More-negative = juicier. -400 < -300 as ints means the odds are worse.
        return o < max_juice
    except (TypeError, ValueError):
        return False


_DG_TIER_RANK = {'PASS': 0, 'SKIP': 0, 'LIGHT': 1, 'COVERAGE': 2,
                 'LEAN': 3, 'STRONG': 4, 'PRIME': 5}


def respect_discipline_cap(new_pp, old_pp):
    """Carry a discipline cap across an LR override and re-apply it.

    2026-09-20. The LR override paths build a FRESH primary_play and copy
    only a few fields forward, so a cap written by
    nfl_ncaaf_signal_discipline was silently discarded — while its
    warning sentence, living in `sub`, survived. Result on the 09-20 NFL
    slate: 5 of 14 picks showed PRIME/STRONG above text reading "capped
    to LEAN", including GB @ NYJ promoted COVERAGE -> PRIME on a pick
    whose own note said anchored picks hit 30%.

    A gate that can be undone by the next stage is not a gate. The cap is
    now structured data (`_discipline_cap`) and is re-applied here, so
    order of operations stops deciding whether it holds.
    """
    if not isinstance(new_pp, dict) or not isinstance(old_pp, dict):
        return new_pp
    cap = old_pp.get('_discipline_cap')
    if not isinstance(cap, dict) or not cap.get('tier'):
        return new_pp
    cap_tier = str(cap['tier']).upper()
    cur_tier = str(new_pp.get('tier') or '').upper()
    new_pp['_discipline_cap'] = cap
    if _DG_TIER_RANK.get(cur_tier, 9) > _DG_TIER_RANK.get(cap_tier, 9):
        new_pp['_uncapped_tier'] = cur_tier      # keep what LR wanted
        new_pp['tier'] = cap_tier
        try:
            new_pp['conviction'] = min(int(new_pp.get('conviction') or 0),
                                       int(cap.get('max_conviction') or 0))
        except (TypeError, ValueError):
            pass
        _why = ', '.join(cap.get('reasons') or []) or 'discipline gate'
        new_pp['audit_note'] = (f"{new_pp.get('audit_note', '')} · held at "
                                f"{cap_tier} by {_why}").strip(' ·')
    return new_pp


def _apply_ml_lr_override_impl(pp, ctx, model, sport):
    if model is None: return pp
    try:
        pred = _lr_predict_ml(ctx, model=model)
        if pred is None: return pp
        old_pp = pp if isinstance(pp, dict) else {}
        was_ml = str(old_pp.get('type', '')).lower() == 'ml'
        # LR sees COIN (p in [0.45, 0.55]) — no edge either side.
        # Legacy scorer's confident-but-wrong picks demoted to COVERAGE.
        # Existing total/rl picks left alone — LR-COIN only kills ML.
        if pred['suggested_side'] == 'NONE':
            if was_ml:
                # 2026-09-03 REVISED: keep original label + demote tier
                # to COVERAGE. UI LOW-CONVICTION chip communicates the
                # coin-flip verdict without erasing the ensemble's take.
                old_pp['_lr_ml_shadow'] = pred
                old_pp['_pre_lr_tier'] = old_pp.get('tier')
                old_pp['tier'] = 'COVERAGE'
                old_pp['audit_note'] = f'{sport} LR sees coin flip (p={pred["p_home_win"]:.2f}) — legacy demoted'
                return old_pp
            return pp
        # 2026-09-03 JUICE CAP (user directive): if LR wants ML but the
        # side's odds are worse than -300, skip the ML override —
        # heavy chalks have marginal EV, keep the total/spread pick
        # (if there was one) or downgrade to COVERAGE (if legacy was ML).
        if _ml_odds_too_juiced(ctx, pred['suggested_side']):
            if was_ml:
                # 2026-09-04 REROUTE: rather than keep the juiced ML label
                # + LOW CONVICTION chip (2026-09-03 version), rewrite the
                # pick label to the correlated spread on the same side.
                # User directive: "make sure no heavy juice chalk being
                # promoted" — even COVERAGE-tier "Utah ML at -10000" is
                # awful UX; a COVERAGE-tier "Utah -34.5" is at least a
                # sane market. If no spread available, fall back to
                # total-based rerouting via close_total when possible,
                # else keep ML at COVERAGE as prior behavior.
                lr_side = str(pred.get('suggested_side', '')).upper()
                team_name = (ctx.get('home_team') if lr_side == 'HOME'
                             else ctx.get('away_team') if lr_side == 'AWAY' else None)
                close_spr = ctx.get('close_spread')
                new_label = None
                new_type = None
                if close_spr is not None and team_name:
                    # 2026-09-15 BUG FIX: NFL close_spread is POSITIVE = home
                    # favorite (opposite of MLB/NCAAF). Prior code assumed
                    # negative=home-fav for both sports, so NFL home
                    # favorites got labeled with the WRONG sign — Andy hit
                    # this on CLE @ TB 9/20: TB is home favorite by 8.5
                    # (ML -410) but label rendered "TB +8.5" as if TB were
                    # the dog. Same class as project_close_spread_sign_bug_914.
                    # Fix: sport-aware sign interpretation matching
                    # ensemble_scorer._label_from_candidate line 1484-1487.
                    try:
                        spr = float(close_spr)
                        # For NFL, positive spread = home fav → home's own line is NEGATIVE spread.
                        # For MLB/NCAAF/NBA/NHL/NCAAB, negative spread = home fav (home's line = spread itself).
                        home_line = -spr if sport == 'NFL' else spr
                        team_spr = home_line if lr_side == 'HOME' else -home_line
                        sign = '' if team_spr < 0 else '+'
                        new_label = f'{team_name} {sign}{team_spr:g}'
                        new_type = 'rl'
                        # 2026-09-24 COVER CHECK. Liking a team on the
                        # moneyline says they win. It does NOT say they win
                        # by the number, and rerouting one into the other
                        # published two picks our own model contradicts:
                        #
                        #   KC @ MIA   model KC by 5.9 (25.6-19.7),
                        #              market KC -10.5, we shipped KC -10.5
                        #   SEA @ WAS  model SEA by 2.1 (25.6-23.5),
                        #              market SEA -7,   we shipped SEA -7
                        #
                        # In both the model's own margin lands on the DOG
                        # with the points, and we laid them instead. The
                        # juice problem is real — a -650 ML is unplayable —
                        # but the answer cannot be a bet the model loses.
                        #
                        # model_pred_*_points is the documented source of
                        # truth here; projected_spread comes from a
                        # different lens and is known to disagree (see
                        # generate_nfl_game_reads build_struct). If the
                        # margin does not clear the number, refuse the
                        # reroute. We do not flip to the dog automatically
                        # — that is a different decision and belongs to the
                        # scorer, not to a juice guard.
                        _hp = ctx.get('model_pred_home_points')
                        _ap = ctx.get('model_pred_away_points')
                        if _hp is not None and _ap is not None:
                            # 2026-09-24 MEASURED, not reasoned.
                            #
                            # The tempting rule — "if the favourite does not
                            # cover the number, take the dog with the
                            # points" — was tested against 30 settled 2026
                            # NFL games and went 12-18 ATS (40.0%). Worse
                            # than a coin flip, and it would have been
                            # published as an improvement.
                            #
                            # The reason is a calibration bias, not a
                            # selection problem: model_pred_*_points
                            # projects a SMALLER favourite margin than the
                            # market in 22 of 30 games (mean -1.21, median
                            # -2.15). So "the side the model supports"
                            # collapses into "the dog" almost every time,
                            # which is exactly the dog-heaviness Andy
                            # flagged on sight.
                            #
                            # Conclusion: this margin is not calibrated
                            # well enough to choose an ATS side. It is still
                            # good enough to REFUSE — declining to lay a
                            # number our own model misses costs us a pick,
                            # never a wrong side. So refuse and stop there.
                            # Picking the other side is a scorer decision
                            # that needs a model which can price margins.
                            _margin = ((float(_hp) - float(_ap)) if lr_side == 'HOME'
                                       else (float(_ap) - float(_hp)))
                            if team_spr < 0 and _margin + team_spr <= 0:
                                old_pp['_reroute_refused'] = {
                                    'label': new_label,
                                    'model_margin': round(_margin, 2),
                                    'spread_needed': abs(team_spr),
                                }
                                new_label = None
                                new_type = None
                        else:
                            # Cannot verify the cover — do not manufacture a
                            # spread pick on faith.
                            old_pp['_reroute_refused'] = {
                                'label': new_label,
                                'reason': 'model_pred_points unavailable',
                            }
                            new_label = None
                            new_type = None
                    except (TypeError, ValueError):
                        pass
                old_pp['_lr_ml_shadow'] = pred
                old_pp['_pre_lr_tier'] = old_pp.get('tier')
                old_pp['_pre_juice_reroute_label'] = old_pp.get('label')
                old_pp['tier'] = 'COVERAGE'
                if new_label:
                    old_pp['label'] = new_label
                    old_pp['type'] = new_type
                    old_pp['audit_note'] = (
                        f'{sport} LR wanted {lr_side} but ML odds too juicy '
                        f'(>-300) — rerouted to spread ({new_label})')
                else:
                    old_pp['audit_note'] = f'{sport} LR ML too juicy (>-300) — coverage'
                return old_pp
            # Legacy was total/rl — leave it (better market at high juice)
            old_pp['_lr_ml_shadow'] = pred
            old_pp['_lr_juice_cap_reason'] = f'{sport}_ml_odds_worse_than_-300'
            return old_pp

        # LR has confident pick. Build the new pp — preserve legacy for audit.
        if not was_ml and old_pp:
            # Existing pp is TOTAL/RL. Sport-aware override policy:
            # (a) LR STRONG-only → don't overwrite total/rl markets
            # (b) LR PRIME → override, WITH EXCEPTIONS below
            #
            # 2026-09-03 NCAAF Week 1-2 dog-spread PROTECTION (data-driven):
            # Historical audit of 6026 NCAAF games shows Week 1 dogs +0 to
            # +28pt cover at 56-59% (real edge). Only huge dogs +28pt+
            # lose (45-48%). Blanket LR override would kill LEGIT
            # small/moderate Week 1 dog picks along with the bad ones.
            # Preserve dog spread pick in the profitable band; let LR
            # override only in the losing +28pt+ band.
            if pred['suggested_tier'] != 'PRIME':
                old_pp['_lr_ml_shadow'] = pred
                return old_pp
            if sport == 'NCAAF' and str(old_pp.get('type','')).lower() == 'rl':
                close_spr = ctx.get('close_spread')
                cur_side = str(old_pp.get('side','')).upper()
                # Determine if legacy pick backs the dog and its spread magnitude
                try:
                    spr_val = float(close_spr) if close_spr is not None else None
                    home_is_dog = spr_val > 0 if spr_val is not None else None
                    backing_dog = None
                    if home_is_dog is not None:
                        backing_dog = (('HOME' in cur_side) == home_is_dog)
                    spr_abs = abs(spr_val) if spr_val is not None else None
                    # Early-season detection: game_date in first 2 weeks of Sept
                    gd = str(ctx.get('game_date') or '')
                    is_early = (gd[:7] in ('2026-08', '2026-09') and gd[8:10] <= '14') if len(gd)>=10 else False
                    # PROTECTIVE case: Week 1-2 + backing dog + spread in edge band
                    if backing_dog and is_early and spr_abs is not None and spr_abs < 28:
                        old_pp['_lr_ml_shadow'] = pred
                        old_pp['_lr_protection_reason'] = f'wk1-2_dog_+{spr_abs:.1f}_covers_56pct_hist'
                        return old_pp
                except (TypeError, ValueError):
                    pass
            # PRIME LR override — replaces RL/total pick with the ML edge
        # 2026-09-03 CONSENSUS-DISSENT GATE (user directive after STL/LAD
        # screenshot: LR picked Cardinals ML AWAY when 4/4 handicappers,
        # 92% money, and 4/6 models were on Dodgers HOME + Jerry Read said
        # "skip"). Single-model dissent against a 4+ source consensus is
        # almost always LR noise — market has priced in edges LR's 107
        # features don't see. Demote to COVERAGE (LOW CONVICTION chip
        # + still visible in game detail) rather than surface as the pick.
        # ══ 2026-09-26 · MODEL-CONSENSUS GATE ══
        # Andy: "how is LR able to override but in theory exposed to less
        # data?" It could, and that is the bug. LR reads market prices,
        # rest and div_game — no EPA, no defensive matchup, no team stats
        # at all. The ensemble models it was overriding read all of them.
        # A model with strictly LESS information had unconditional
        # authority over models with more.
        #
        # The existing dissent gate below only consults MONEY-FLOW
        # consensus, and on Oklahoma @ Georgia it came up one source
        # short and let the override through:
        #
        #     projected_spread (v3)  +11.81   Georgia by ~12
        #     sp_plus_pred_spread    +11.81   Georgia by ~12
        #     mc_p_home               0.751   Georgia 75%
        #     LR p_home               0.126   Georgia 12.6%
        #     money 94% on Georgia, but sources_agree = 2 (gate needs 3)
        #
        # Result: a PRIME "Oklahoma ML" on a +410 dog, on a card where
        # every other lens said Georgia. So the gate now also asks the
        # ensemble's own margin models. If they point the other way with
        # real magnitude, LR stays a shadow — which is exactly what the
        # LR SHADOW explainer already promises users it does: "when it
        # disagrees, we treat it as a heads-up, not a reason to flip."
        try:
            lr_side_mc = pred['suggested_side']
            # projected_spread / sp_plus_pred_spread are HOME-POSITIVE
            # (see nfl_game_context.compute_projections: power_diff *
            # K_PTS + HOME_FIELD). Deliberately NOT close_spread, whose
            # sign convention differs by sport and has already caused a
            # grading bug (project_close_spread_sign_bug_914).
            votes, margins = [], []
            for fld in ('projected_spread', 'sp_plus_pred_spread'):
                v = ctx.get(fld)
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                margins.append(abs(v))
                votes.append('HOME' if v > 0 else 'AWAY')
            mcp = ctx.get('mc_probabilities') or {}
            if isinstance(mcp, str):
                try:
                    import json as _j
                    mcp = _j.loads(mcp)
                except Exception:
                    mcp = {}
            if isinstance(mcp, dict) and mcp.get('mc_p_home') is not None:
                try:
                    votes.append('HOME' if float(mcp['mc_p_home']) >= 0.5 else 'AWAY')
                except (TypeError, ValueError):
                    pass
            against = [v for v in votes if v and v != lr_side_mc]
            worst = max(margins) if margins else 0.0
            # Two or more stat-based lenses on the other side, and the
            # projection is not a coin flip. 3 points is roughly a
            # field goal — below that the models are not really disagreeing.
            if len(against) >= 2 and worst >= 3.0:
                old_pp = pp if isinstance(pp, dict) else {}
                old_pp['_lr_ml_shadow'] = pred
                old_pp['_lr_model_dissent'] = {
                    'reason': (f'LR wanted {lr_side_mc} but {len(against)} of '
                               f'{len(votes)} stat-based lenses favour '
                               f'{against[0]} by up to {worst:.1f} pts — LR '
                               f'sees no team stats, so it does not outrank them'),
                    'lenses_against': len(against),
                    'lenses_total': len(votes),
                    'max_projected_margin': round(worst, 2),
                }
                print(f'  🛑 {sport} LR override blocked: {len(against)}/{len(votes)} '
                      f'stat lenses favour {against[0]} by up to {worst:.1f} pts '
                      f'(LR wanted {lr_side_mc})')
                return old_pp
        except Exception as _e:
            print(f'  ⚠ LR model-consensus gate errored ({type(_e).__name__}) — '
                  f'override allowed through')

        try:
            ss = ctx.get('splits_summary') or {}
            ml_split = ss.get('ml') if isinstance(ss, dict) else None
            if isinstance(ml_split, dict):
                lr_side = pred['suggested_side']
                opp_side = 'AWAY' if lr_side == 'HOME' else 'HOME'
                opp_bucket = ml_split.get(opp_side) or {}
                if isinstance(opp_bucket, dict):
                    money_opp = opp_bucket.get('money_pct_avg') or 0
                    sources_opp = opp_bucket.get('sources_agree') or 0
                    # Broad consensus threshold: ≥3 sources AND ≥70% money on OPPOSITE side
                    if sources_opp >= 3 and money_opp >= 70:
                        # LR is a lone dissenter — demote to COVERAGE
                        old_pp = pp if isinstance(pp, dict) else {}
                        old_pp['_lr_ml_shadow'] = pred
                        old_pp['_lr_consensus_dissent'] = {
                            'reason': f'LR wanted {lr_side} but {sources_opp} sources agree ' \
                                      f'on {opp_side} with {money_opp:.0f}% money — lone dissent demoted',
                            'money_opp': money_opp, 'sources_opp': sources_opp,
                        }
                        old_pp['_pre_lr_tier'] = old_pp.get('tier')
                        old_pp['tier'] = 'COVERAGE'
                        old_pp['audit_note'] = (
                            f'{sport} LR dissented vs consensus ' \
                            f'({sources_opp} sources / {money_opp:.0f}% money on {opp_side}) — coverage'
                        )
                        return old_pp
        except Exception:
            pass  # never let consensus check break the pipeline

        home_team = ctx.get('home_team') or 'Home'
        away_team = ctx.get('away_team') or 'Away'
        team = home_team if pred['suggested_side'] == 'HOME' else away_team
        # Convert probability to conviction 0-100 scale
        p = pred['p_home_win']
        conviction = int(round((p if pred['suggested_side'] == 'HOME' else (1 - p)) * 100))
        # 2026-09-03 USER-FRIENDLY sub-line. Prior text was
        # "LR predictor: p_home_win=0.27 · 73% confidence" — technical
        # jargon, raw probability inversion, lowercase snake_case.
        # 2026-09-06 second pass — user feedback: "Supervised model
        # backs X" reads too clinical, doesn't tell them WHY. Swapped
        # to "Model conviction on X · N%" — shorter, warmer, still honest.
        # 2026-10-02 · LAST WORD IS THE WRONG WORD FOR A COLLEGE TEAM.
        # `team.split()[-1]` is right for pro sports, where the field is
        # "City Nickname" and the nickname identifies the team ("Buffalo
        # Bills" -> "Bills"). College fields are school names whose LAST
        # token is frequently the generic half, so it threw the identity
        # away and kept the filler:
        #
        #     "Penn State"     -> "State"   (shipped on the 10-02 card as
        #                                    "State · 85% vs 59% implied")
        #     "Michigan State" -> "State"
        #     "Virginia Tech"  -> "Tech"
        #     "Texas A&M"      -> "A&M"
        #
        # Two different teams on the same slate both render as "State".
        # College names are short enough to use whole, so do.
        _COLLEGE = ('NCAAF', 'NCAAB')
        if not team:
            team_short = 'the pick'
        elif str(sport).upper() in _COLLEGE:
            team_short = team
        else:
            team_short = team.split()[-1]

        # 2026-09-27 · STATE THE EDGE, NOT JUST THE CONVICTION.
        #
        # Andy, on BAL @ DAL: "BAL at -203 implies about 67%, and the model
        # says 67%. That's a coin flip against the vig, yet it's labeled a
        # LEAN. The card should show edge (model % minus implied %), not raw
        # conviction." The row proved him exactly right — implied 67.0%,
        # model 67.0%, edge 0.0pp, published anyway.
        #
        # `conviction` here IS the model's win probability (see just above),
        # and the tier bands are absolute thresholds on it, so nothing in
        # the pipeline ever compared it to the price. Measured over 100
        # past ML picks: STRONG averaged -1.77pp of edge with 66% of them
        # underwater by >2pp. Cincinnati ML -285 shipped STRONG at
        # conviction 59 against a 74.0% implied — a -15pp pick.
        #
        # The edge is computed in model_edge.py so there is ONE definition
        # rather than a second opinion per caller. It DEMOTES, never
        # suppresses: Andy does not want an engine that passes on
        # everything, so the play still ships with an honest label.
        _ml_price = (ctx.get('close_home_ml') if pred['suggested_side'] == 'HOME'
                     else ctx.get('close_away_ml'))
        try:
            from model_edge import edge_pp, edge_phrase, cap_tier_for_edge
            _edge = edge_pp(conviction, _ml_price, 'ml')
            _phrase = edge_phrase(conviction, _ml_price, 'ml')
            _tier_after, _edge_reason = cap_tier_for_edge(
                pred['suggested_tier'], conviction, _ml_price, 'ml')
        except ImportError:
            _edge, _phrase, _edge_reason = None, '', None
            _tier_after = pred['suggested_tier']

        # ══ 2026-09-29 · lr_v1 MAY NOT ASSIGN PRIME IN NCAAF ══
        # Andy: "have we fixed the ncaaf tier issue then?" It was not fixed by
        # anything else shipped today, and this is where it actually lives.
        #
        # NCAAF's tier ladder is inverted and it is ENTIRELY these picks.
        # Measured on the 2026 season:
        #
        #     lr_v1 PRIME   MLB    60-35   63.2%  n=95   z=+2.10   works
        #                   NFL     7-5    58.3%  n=12   z=+0.41   fine
        #                   NCAAF   5-10   33.3%  n=15   z=-1.48   broken
        #
        #     NCAAF ensemble_v2 tiers are healthy by comparison:
        #       STRONG 57.1% (n=63) · LEAN 53.3% (n=92) · COVERAGE 53.7% (n=67)
        #
        # All 15 NCAAF lr_v1 PRIMEs are moneylines, 14 of them on FAVOURITES of
        # 1.5-6.5 points — coin-flip road favourites wearing the top tier. And
        # within lr_v1's own NCAAF picks, PRIME (33.3%) is worse than its STRONG
        # (66.7%, n=9), so this is a ranking failure, not just a bad stretch.
        #
        # WHY CAP RATHER THAN DISABLE, and why NCAAF only: lr_v1 PRIME is
        # significantly GOOD in MLB (z=+2.10 on n=95). Turning it off globally
        # would break the thing that works. The claim here is narrow and
        # evidence-shaped — lr_v1 has DEMONSTRATED PRIME-worthy discrimination in
        # MLB and has not in NCAAF, so it keeps the tier where it earned it and
        # loses it where it has not.
        #
        # n=15 / z=-1.48 does not clear 2 SE, and a suppression would not be
        # justified on it. A CAP is a weaker action than suppression: the play
        # still ships, with an honest label, one tier down — which is exactly
        # what the surrounding comment says this gate is for ("it DEMOTES, never
        # suppresses"). Revisit after NCAAF week 8; lift it if PRIME earns it.
        if str(sport).upper() == 'NCAAF' and _tier_after == 'PRIME':
            _tier_after = 'STRONG'
            _edge_reason = ((_edge_reason + ' · ') if _edge_reason else '') +                            'lr_v1 PRIME unproven in NCAAF (5-10, n=15) — capped'

        _sub = (f'Model conviction on {team_short} · {conviction}%'
                if not _phrase else f'{team_short} · {_phrase}')

        new_pp = {
            'type': 'ml',
            'tier': _tier_after,
            'side': pred['suggested_side'],
            'label': f'{team} ML',
            'sub': _sub,
            'conviction': conviction,
            # Stored so surfaces and graders read the same number the card
            # shows, instead of each recomputing it from a price they may
            # have fetched at a different moment.
            '_model_edge_pp': _edge,
            '_ml_price_at_pick': _ml_price,
            '_edge_cap': ({'from': pred['suggested_tier'], 'to': _tier_after,
                           'reason': _edge_reason} if _edge_reason else None),
            '_engine': 'lr_v1',
            # 2026-09-09: standardized shadow field. Was `_lr_p_home_win` raw
            # (only on PRIME-override path); every other path in this file
            # writes `_lr_ml_shadow` dict. Downstream (POTD gate, Sharp Card
            # LR check, watchdogs) could not reliably find the value. Now
            # always dict format regardless of code path. Raw kept for back-
            # compat with any pre-9/9 audit tooling.
            '_lr_ml_shadow': pred,
            '_lr_p_home_win': p,  # deprecated raw — remove after 30d verify
            '_lr_sport': sport,
            '_lr_model_version': model.get('version', ''),
            '_pre_lr': {
                'tier': old_pp.get('tier'),
                'side': old_pp.get('side'),
                'type': old_pp.get('type'),
                'engine': old_pp.get('_engine'),
                'conviction': old_pp.get('conviction'),
            } if old_pp else None,
            'audit_note': f'LR override · p={p:.2f} · was {(old_pp.get("tier") or "none")}/{(old_pp.get("type") or "none")}',
        }
        # 2026-09-09: preserve _lr_total_shadow from old_pp — same class of
        # bug as the total-override path (see apply_mlb_total_lr_override).
        # ML PRIME override builds fresh new_pp; without this the total
        # shadow is lost.
        if isinstance(old_pp, dict) and old_pp.get('_lr_total_shadow') is not None:
            new_pp['_lr_total_shadow'] = old_pp['_lr_total_shadow']
        if isinstance(old_pp, dict) and old_pp.get('_lr_p_over') is not None:
            new_pp['_lr_p_over'] = old_pp['_lr_p_over']  # legacy raw
        # 2026-09-20: a discipline cap outranks an LR promotion.
        return respect_discipline_cap(new_pp, old_pp)
    except Exception:
        return pp  # never break the pipeline


def apply_ncaaf_high_conviction_dog_cap(pp: dict | None, ctx: dict) -> dict | None:
    """Cap the tier on an NCAAF DOG pick carrying high conviction.

    Andy 2026-09-30: "we cant just leave them inverted."

    MEASURED, leak-free, on 256 graded NCAAF sides (ctx rows whose updated_at is
    strictly before kickoff_utc, joined via results_game_id, pushes excluded).
    This became measurable only after the 09-30 grading repair — the UTC/ET date
    boundary had left ~31 games unscored, disproportionately the night games.

    The dominant effect is NOT the conviction curve, it is which side is backed:

        FAV  112-69   61.9%   n=181   z=+3.20
        DOG   35-40   46.7%   n=75    z=-0.58

    A 15.2pp gap, and conviction is not a proxy for it (dog-rate by conviction
    band runs 35 / 26 / 22 / 31 — no pattern). The damage is in the INTERACTION:

        DOG + conv >= 70   12-19   38.7%   n=31   <- below any breakeven
        FAV + conv >= 70   52-35   59.8%   n=87
        FAV + conv <  70   60-34   63.8%   n=94   z=+2.68

    So high conviction is fine on a favourite and poison on a dog. That is
    exactly what project_sp_plus_compression_927 predicts: the model
    under-projects favourites, so model-vs-market disagreement points at the dog
    almost by construction, and the biggest disagreements — which earn the
    highest conviction — are the most compressed, least real.

    WHY A CAP AND NOT A SUPPRESSION. The parent DOG/FAV split is solid
    (z=+3.20, n=256) but the specific cell is n=31 at z=-1.26, which does not
    clear 2 SE. A cap demotes and keeps the play visible with an honest label;
    suppression would be a stronger claim than the cell supports. Same reasoning
    as the lr_v1 NCAAF PRIME cap above.

    NOT A PERMANENT FIX. K_PTS_SP moved 0.85 -> 0.94 on 09-29 to attack the
    compression at source, and that has NOT been measured yet — it needs a ctx
    rebuild plus new games. If de-compression works, the high-conviction dog
    population shrinks on its own and this cap stops firing. Re-measure after
    NCAAF week 8 and lift it if dogs earn the tier back.

    Sport-scoped, sides only. Preserves an audit trail.
    """
    try:
        if not (pp and isinstance(pp, dict)):
            return pp
        if str(pp.get('type', '')).lower() not in ('ml', 'rl', 'spread'):
            return pp
        conv = pp.get('conviction')
        tier = str(pp.get('tier') or '').upper()
        if conv is None or tier not in ('PRIME', 'STRONG'):
            return pp
        try:
            conv = float(conv)
        except (TypeError, ValueError):
            return pp
        if conv < 70:
            return pp
        # Is the backed side the underdog? NCAAF close_spread is stored with
        # POSITIVE = home is the DOG, so home margin = -close_spread.
        try:
            cs = float(ctx.get('close_spread'))
        except (TypeError, ValueError):
            return pp          # no line, no judgement
        mkt_home_margin = -cs
        side = str(pp.get('side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            return pp
        is_dog = ((side == 'HOME' and mkt_home_margin < 0)
                  or (side == 'AWAY' and mkt_home_margin > 0))
        if not is_dog:
            return pp
        before = tier
        pp['tier'] = 'STRONG' if tier == 'PRIME' else 'LEAN'
        pp['_ncaaf_hi_conv_dog_cap'] = {
            'tier_before': before, 'tier_after': pp['tier'],
            'conviction': conv, 'close_spread': cs,
            'basis': 'NCAAF dog + conv>=70 measured 12-19 (38.7%, n=31); '
                     'FAV 61.9% vs DOG 46.7% on n=256, z=+3.20',
        }
        _r = pp.get('edge_reason') or ''
        pp['edge_reason'] = ((_r + ' · ') if _r else '') +             'high-conviction NCAAF dog capped (dogs 46.7% vs favs 61.9%, n=256)'
    except Exception:
        return pp
    return pp


def apply_ncaaf_prime_ml_cap(pp: dict | None, ctx: dict) -> dict | None:
    """Cap NCAAF moneyline picks out of PRIME.

    Andy 2026-09-30, approving this specific cap before the Wednesday lock.

    MEASURED on 248 leak-free graded NCAAF sides (ctx updated_at strictly before
    kickoff_utc, joined via results_game_id, pushes excluded), priced at the
    CLOSE — close_home_ml / close_away_ml for moneylines, flat -110 for
    spread/RL. Price matters here more than anywhere else in the engine:

        SPREAD / RL   109-84   56.5%   n=193   +15.09u   ROI  +7.8%
        MONEYLINE      30-25   54.5%   n=55    -12.73u   ROI -23.1%  (z=-2.37)
        COMBINED      139-109  56.0%   n=248    +2.36u   ROI  +1.0%

    The moneyline book eats essentially the whole spread book. Note the hit
    rates are nearly identical (56.5% vs 54.5%) — this is invisible to any
    hit-rate report and only shows up in units.

    The loss concentrates in PRIME, which is the only losing tier:

        PRIME      2-11  15.4%  n=13   ROI -74.7%   z=-2.50
        STRONG    47-31  60.3%  n=78   ROI  +8.2%
        LEAN      51-41  55.4%  n=92   ROI  +3.4%
        COVERAGE  26-17  60.5%  n=43   ROI  -0.6%
        PASS      13-9   59.1%  n=22   ROI +12.8%

    and 12 of those 13 PRIMEs are moneylines: PRIME ML alone is 2-10, ROI
    -72.6%, z=-2.31, median price -146. So one cell — NCAAF PRIME ML — is
    carrying the sport's entire loss, at the position of maximum user
    visibility and 2u Sharp Card stake sizing.

    WHY NOT REROUTE TO THE SPREAD. That was the obvious fix and it is wrong.
    Taking the SAME SIDE on the spread instead of the ML in those games:

        25-38   39.7%   n=63   ROI -24.2%

    Equally bad. The engine is not mispricing a market, it is picking the wrong
    TEAM in these games, and both markets lose on them. This retires the queued
    project_jerry_spread_preference_917 item for NCAAF — its premise was that
    the side was sound and only the price was wrong.

    MECHANISM, consistent with project_sp_plus_compression_927: the ML gets
    chosen as top market when the model's margin edge is large relative to the
    spread, i.e. exactly when the model most disagrees with the market. Those
    large disagreements are where a compressed margin model is least reliable.
    Same root cause as the dog bias, surfacing through market choice instead.

    THE CAP TARGET IS LEAN, NOT STRONG, AND THAT IS THE WHOLE POINT.
    The first version of this gate demoted PRIME -> STRONG. Measured against the
    Sharp Card stake ladder (PRIME/STRONG 2u, LEAN/COVERAGE 1u, PASS 0u), that
    is a LITERAL NO-OP — PRIME and STRONG carry the same 2u, so the staked book
    is identical to -0.01u:

        as published today                   -3.76u   on 317u staked   -1.2%
        cap PRIME ml -> STRONG               -3.76u   on 317u staked   -1.2%   <- no-op
        cap PRIME ml -> LEAN                 +4.96u   on 305u staked   +1.6%
        cap PRIME ml -> PASS  (0u)          +13.67u   on 293u staked   +4.7%
        suppress all NCAAF ml entirely      +17.91u   on 236u staked   +7.6%

    Worse than useless: demoting to STRONG moves 12 losing picks INTO the one
    tier with a defensible edge, dragging STRONG from +8.2% to -2.6% ROI and
    making the engine's best label look broken. A tier demotion only does
    something if it crosses a STAKE boundary. PRIME -> STRONG does not.

    So this caps to LEAN, which is the real cap: the pick stays published and
    stays in the record (('PRIME','STRONG','LEAN') is the publishable set in
    compute_surface_records, generate_ledger, aggregate_daily_records), the
    receipts stay honest, and the stake halves.

    NOT PASS, and not full suppression, even though both score better above.
    COVERAGE and PASS are OUTSIDE the publishable set, so routing there is
    suppression wearing a tier label — the pick vanishes from the card and from
    the record. That is the stronger claim, it needs the shadow week per
    feedback_suppression_gate_needs_shadow, and full ML suppression would pull
    55 of 248 picks (22% of the board). Shadow it, do not ship it blind.

    DO NOT judge this gate by hit rate afterwards. NCAAF hit rates rank
    COVERAGE (60.5%) above STRONG, but COVERAGE is where the price gate dumps
    unbettable chalk — MLs at -3200 to -10000, whose conv-100 rows carry
    _pre_lr_tier PRIME plus _reroute_refused — and at closing prices COVERAGE
    is -0.6%. Capping a tier that cannot be bet looks like an improvement on
    every hit-rate report and changes nothing real.

    MUST RUN AFTER apply_ml_lr_override. That override REPLACES the pick and
    can hand back a fresh PRIME ML, so a cap placed with the other NCAAF gates
    would be silently undone. Same reasoning as the 09-27 MC re-check above it.

    WHAT LOOKS LIKE A HOLE HERE AND IS NOT — DO NOT "FIX" THIS.
    Verified on the real board 2026-09-30: 8 of this weekend's 27 ML picks
    leave the chain at STRONG carrying `_edge_cap: {from: PRIME, reason:
    "lr_v1 PRIME unproven in NCAAF (5-10, n=15) — capped"}`. They were PRIME,
    something demoted them one step, and STRONG carries the SAME 2u stake — so
    they appear to walk straight through this gate with full exposure. The
    obvious move is to catch them the way the `_ncaaf_hi_conv_dog_cap` branch
    above is caught. Measured, that would be wrong:

        ALL ml                            30-25  54.5%  n=55  ROI -23.1%
        STRONG ml, LR-capped FROM PRIME    3-0  100.0%  n=3   ROI +45.3%
        STRONG ml, not LR-capped           7-4   63.6%  n=11  ROI -14.4%

    The LR-capped cell is the BEST-performing moneyline cell we have, not a
    losing one. n=3 is far too thin to promote on, and equally too thin to
    suppress on — the point is only that there is no evidence of harm, so
    capping it would be acting against what little data exists.

    The asymmetry with the dog cap is deliberate and evidence-based: a
    dog-capped ex-PRIME came out of a cell measured 12-19 (38.7%, n=31), so it
    belongs to the poisoned population this gate exists for. An LR-capped
    ex-PRIME does not. Re-measure after week 8; if that cell turns negative
    with real sample, add `_edge_cap` to the was_prime test then.

    Re-measure after NCAAF week 8, alongside the K_PTS_SP 0.85 -> 0.94
    de-compression, which attacks this at source and has not been measured yet.
    If the margin model stops over-disagreeing, this cap stops firing on its own.
    """
    try:
        if not (pp and isinstance(pp, dict)):
            return pp
        if str(pp.get('type', '')).lower() != 'ml':
            return pp
        tier = str(pp.get('tier') or '').upper()
        # Fire on PRIME, and also on a pick some EARLIER cap already walked down
        # from PRIME to STRONG — otherwise a high-conviction dog ML escapes this
        # gate entirely by having been demoted once already, and lands in STRONG
        # at the same 2u stake it would have had at PRIME. apply_ncaaf_high_
        # conviction_dog_cap does exactly that, so the hole is real, not
        # hypothetical.
        was_prime = tier == 'PRIME' or (
            tier == 'STRONG'
            and str((pp.get('_ncaaf_hi_conv_dog_cap') or {}).get('tier_before',
                                                                 '')).upper()
            == 'PRIME')
        if not was_prime:
            return pp
        pp['tier'] = 'LEAN'
        pp['_ncaaf_prime_ml_cap'] = {
            'tier_before': tier, 'tier_after': 'LEAN',
            'conviction': pp.get('conviction'),
            'basis': 'NCAAF PRIME ML 2-10 (ROI -72.6%, z=-2.31); sport ML book '
                     'ROI -23.1% n=55 z=-2.37 vs spread/RL +7.8% n=193; '
                     'same-side spread reroute also loses (39.7%, n=63). '
                     'LEAN not STRONG: PRIME->STRONG is stake-neutral (both '
                     '2u) and measured as an exact no-op on the staked book',
        }
        _r = pp.get('edge_reason') or ''
        pp['edge_reason'] = ((_r + ' · ') if _r else '') + (
            'NCAAF moneyline capped to LEAN '
            '(ML book -23.1% ROI vs spread +7.8%, n=248)')
        _n = pp.get('audit_note') or ''
        pp['audit_note'] = ((_n + ' · ') if _n else '') + \
            f'ncaaf_prime_ml_cap: {tier}->LEAN'
    except Exception:
        return pp
    return pp


def apply_ncaaf_total_suppression(pp: dict | None, ctx: dict) -> dict | None:
    """Stop publishing NCAAF total picks. The model is worse than the close.

    Andy 2026-09-30, approving this before the Wednesday lock.

    THE RECORD, leak-free (ctx updated_at strictly before kickoff_utc), graded
    off ncaaf_game_results.total_result, pushes excluded:

        STRONG     2-3   40.0%  n=5    ROI  -23.6%
        LEAN       3-7   30.0%  n=10   ROI  -42.7%
        COVERAGE   7-14  33.3%  n=21   ROI  -36.4%
        PASS       0-3    0.0%  n=3    ROI -100.0%
        ALL       12-27  30.8%  n=39   ROI  -41.3%   z=-2.40

    -16.09u on 39 picks — a bigger loss than the entire NCAAF moneyline book
    (-12.73u on 55) at a third the volume. It loses at EVERY tier, so unlike
    apply_ncaaf_prime_ml_cap there is no concentrated cell to cap: no tier
    demotion helps when the whole population is the problem.

    NOT A DIRECTIONAL BIAS RIDING THE SEASON. That was the obvious confound and
    it is ruled out. The engine loses on BOTH sides:

        picks OVER    4-14   22.2%   n=18   z=-2.36
        picks UNDER   8-13   38.1%   n=21   z=-1.09

    against a 2026 NCAAF base rate of OVER 161-141 (53.3%, n=302) — i.e. a mild
    OVER season. If this were just "UNDER is good in 2026", its UNDER picks
    would win. They lose too. Wrong in both directions.

    THE MECHANISM, which is why this is a suppression and not a shadow item:
    projected_total is measurably worse than the closing line.

        MAE vs actual score:   model 12.81   market 11.82   (n=199)
        bias vs close:         mean +0.95, median +0.96
        projects OVER the close 58.3% of the time

    A model with a larger error than the number it is betting against has no
    information to sell, and its +0.95 high bias is exactly why it says OVER too
    often and why OVER is its worst side. That is a structural reason not to
    publish, independent of the 39-game record — which is what separates this
    from the ML case, where I deliberately capped instead of suppressing because
    the evidence there was a record without a mechanism.

    Nothing else was gating these. apply_ncaaf_total_lr_override went
    SHADOW-ONLY on 2026-09-15 (project_ncaaf_lr_total_dead_914: p_over spans
    only [0.4578, 0.5500] across a whole slate, the model is inert), so from
    09-15 to today NCAAF totals published with no gate in front of them at all.

    ROUTES TO PASS, NOT COVERAGE. COVERAGE is not actually outside the record:
    aggregate_daily_records.py:847 rolls up ('PRIME','STRONG','LEAN',
    'COVERAGE') and jerry_pre_publish_audit reads it too. PASS is rank 0 in
    _DG_TIER_RANK beside SKIP and is excluded from every publishable set, so it
    is the only unambiguous "visible in game detail, absent from the card and
    the record" target.

    WHY NOT FADE, given feedback_fade_not_suppress_803 says <45% buckets should
    fade the other side. Fading all 48 would have gone 27-12 (+12.55u), which is
    tempting and not what I would ship yet. Per side the fade evidence is thin —
    fading its UNDER picks is 13-8 (61.9%, z=+1.09), which does not clear 2 SE —
    and the mechanism argues against durability: a model whose MAE is worse than
    the market is NOISE, and noise regresses to ~50% rather than staying
    invertible. Publishing a fade also means telling subscribers to bet against
    our own engine, which deserves its own shadow record first. So: suppress
    now, and ncaaf_shadow_total_fade.py records what the fade would have done.

    Re-measure after NCAAF week 8. If projected_total's MAE ever beats the
    market's, revisit — that is the condition that would earn these picks back.
    """
    try:
        if not (pp and isinstance(pp, dict)):
            return pp
        if str(pp.get('type', '')).lower() != 'total':
            return pp
        before = str(pp.get('tier') or '').upper()
        if before == 'PASS':
            return pp
        pp['tier'] = 'PASS'
        pp['_ncaaf_total_suppressed'] = {
            'tier_before': before, 'tier_after': 'PASS',
            'side': pp.get('side'), 'conviction': pp.get('conviction'),
            'basis': 'NCAAF totals 12-27 (30.8%, n=39, z=-2.40, ROI -41.3%); '
                     'loses on BOTH sides (OVER 22.2% n=18 / UNDER 38.1% n=21) '
                     'vs a 53.3% OVER base rate; projected_total MAE 12.81 '
                     'beaten by market 11.82',
        }
        _r = pp.get('edge_reason') or ''
        pp['edge_reason'] = ((_r + ' · ') if _r else '') + (
            'NCAAF total unpublished — model MAE 12.81 vs market 11.82, '
            'picks 30.8% (n=39)')
        _n = pp.get('audit_note') or ''
        pp['audit_note'] = ((_n + ' · ') if _n else '') +             f'ncaaf_total_suppression: {before}->PASS'
    except Exception:
        return pp
    return pp


def apply_unpriced_market_gate(pp: dict | None, ctx: dict,
                               sport: str = 'MLB') -> dict | None:
    """Cap a pick to COVERAGE when ITS OWN market carries no price.

    ══ 2026-10-02 · YOU CANNOT BEAT A LINE THAT DOES NOT EXIST ══
    Measured across every sport's game_context:

        NHL    27 of 65 regular-season picks (42%) on an unpriced market
        NCAAF   9 of 433 (2%)
        NBA     2 of 11  (18%)
        NFL     2 of 281 (1%)

    NHL is the outlier because books post NHL lines only 1-3 days out while the
    pick horizon runs a week. Tonight's St. Louis @ Dallas carried
    tier=STRONG conviction=77 with NO moneyline stored, and five more STRONG
    picks sit on 10-08 games with no price. A tier asserts an edge against the
    market; with no market there is nothing to have an edge over, so the number
    is model-only and must not be published as STRONG.

    NON-DESTRUCTIVE BY DESIGN. Most of these are FUTURE games whose odds simply
    have not landed yet. Capping is re-evaluated on every context rebuild, so
    the tier restores itself as soon as a price arrives. It withholds a claim
    we cannot support today rather than deleting a pick.

    COLUMN NAMING VARIES BY SPORT and that is exactly how this stays broken:
        NHL, NFL   both close_home_ml AND home_ml_close
        NCAAF      close_home_ml only
        MLB, NBA   home_ml_close only
        NCAAB      NO price columns at all
    So both spellings are checked. A sport carrying none of the relevant
    columns FAILS OPEN — never demote on an absence we cannot even measure.
    """
    if not isinstance(pp, dict):
        return pp
    ptype = str(pp.get('type') or '').lower()
    if not ptype:
        return pp

    def _any(*names):
        """True when at least one column EXISTS and is non-null."""
        present = [n for n in names if n in ctx]
        if not present:
            return None          # cannot measure → caller fails open
        return any(ctx.get(n) is not None for n in present)

    if ptype == 'ml':
        has = _any('close_home_ml', 'home_ml_close',
                   'close_away_ml', 'away_ml_close')
    elif ptype == 'total':
        has = _any('close_total')
    elif ptype in ('rl', 'spread', 'puckline'):
        has = _any('close_puckline', 'close_spread')
    else:
        return pp                # prop/other — priced on the prop row, not here

    if has is None or has:
        return pp                # unmeasurable, or genuinely priced

    tier = str(pp.get('tier') or '').upper()
    if tier in ('', 'COVERAGE', 'PASS', 'SKIP'):
        return pp                # already unpublished

    out = dict(pp)
    out['tier'] = 'COVERAGE'
    out['_unpriced_market'] = True
    note = (f'capped to COVERAGE from {tier}: no {ptype.upper()} price stored '
            f'for this game, so the tier cannot assert an edge')
    out['audit_note'] = ((pp.get('audit_note') or '') + ' · ' + note).strip(' ·')
    return out


def apply_heavy_ml_spread_reroute(pp: dict | None, ctx: dict,
                                  sport: str = 'MLB') -> dict | None:
    """Express a heavily-juiced ML pick on the spread instead.

    ══ 2026-10-02 · THE JUICE GATE RUNS BEFORE THE THING THAT MAKES JUICE ══
    apply_all_defensive_gates applies apply_juice_trap_gate THIRD and
    apply_ml_lr_override SIXTH. The override builds a brand-new ML pick after
    the juice gate has already run, so override moneylines were never price-
    checked at all. Measured on live picks, 2026-10-02 forward:

        NFL    44 of 73 ML picks worse than -200  (42 of them lr_v1)
        NCAAF  16 of 34
        NHL     2 of 48

    including two PRIMEs (DET ML -225, PHI ML -238) and LSU ML -375,
    Texas State ML -315, BUF ML -305, Liberty ML -275 at STRONG.

    This implements a rule the project already holds and the engine was
    violating: the -200+ heavy-favourite ML trap, the -250 POTD juice gate,
    and the queued "route -200+ ML to spread" decision. It is independently
    supported by two standing measurements — NCAAF ML -23.1% vs spread +7.8%,
    and NFL favourite-ML 65.2% win rate at -4.9% ROI. A 65% winner at -200 is
    a losing bet; the same read on the spread is not.

    WHY THE SIGN CONVENTION CANNOT BREAK THIS. close_spread is HOME-relative
    in NCAAF and AWAY-relative in NFL (verified this session: 99.7% n=331 and
    100.0% n=284 against spread_result). Rather than branch on that minefield,
    the line is rebuilt from MAGNITUDE: a team priced at -200 or worse IS the
    favourite, so it is laying points and its line is -abs(close_spread).
    True regardless of which side the stored column is written from.

    TIER IS CAPPED AT STRONG. The conviction was earned as a probability of
    WINNING THE GAME; it is not a cover probability. Carrying it onto a spread
    at PRIME would assert rigor the number does not supply. The pick still
    ships — this reroutes and demotes, it never suppresses.
    """
    if not isinstance(pp, dict):
        return pp
    if str(pp.get('type') or '').lower() != 'ml':
        return pp
    side = str(pp.get('side') or '').upper()
    if side not in ('HOME', 'AWAY'):
        return pp

    # The pick's OWN price. Both column spellings — they differ by sport and
    # that is exactly how this class of bug survives (see
    # apply_unpriced_market_gate).
    names = (('close_home_ml', 'home_ml_close') if side == 'HOME'
             else ('close_away_ml', 'away_ml_close'))
    price = None
    for n in names:
        if ctx.get(n) is not None:
            price = ctx.get(n)
            break
    try:
        price = int(price)
    except (TypeError, ValueError):
        return pp                      # unpriced → unpriced-market gate's job
    if price > HEAVY_ML_THRESHOLD:
        return pp                      # inside discipline, leave alone

    # Need a spread to reroute ONTO. No line → nothing to do here; the pick
    # keeps its ML and the juice gate below still demotes it.
    line_mag = None
    for n in ('close_spread', 'close_puckline'):
        if ctx.get(n) is not None:
            try:
                line_mag = abs(float(ctx.get(n)))
            except (TypeError, ValueError):
                line_mag = None
            if line_mag is not None:
                break
    if not line_mag:
        return pp

    team = ctx.get('home_team') if side == 'HOME' else ctx.get('away_team')
    tier_before = str(pp.get('tier') or '').upper()
    out = dict(pp)
    out['type'] = 'rl'
    out['line'] = -line_mag
    out['label'] = f'{team} -{line_mag:g}'
    if tier_before == 'PRIME':
        out['tier'] = 'STRONG'
    out['_heavy_ml_reroute'] = {
        'from_market': 'ml',
        'ml_price': price,
        'threshold': HEAVY_ML_THRESHOLD,
        'to_line': -line_mag,
        'tier_before': tier_before,
        'tier_after': out.get('tier'),
    }
    # The conviction travels, so say what it is a probability OF.
    out['_conviction_basis'] = 'ml_win_probability'
    note = (f'rerouted ML -> spread: {team} ML at {price:+d} is past the '
            f'{HEAVY_ML_THRESHOLD:+d} heavy-favourite trap, so the same read '
            f'is expressed as {out["label"]}; conviction is a win probability, '
            f'not a cover probability')
    out['audit_note'] = ((pp.get('audit_note') or '') + ' · ' + note).strip(' ·')
    return out


def apply_all_defensive_gates(pp: dict | None, ctx: dict, sport: str = 'MLB') -> dict | None:
    """Apply all defensive gates in the canonical order:
    OC flip → MC dissent → juice-trap → NCAAF large-spread → publish gate.

    Order matters:
      - OC-flip FIRST because it may change pp.side (downstream gates read pp.side)
      - MC-dissent SECOND to demote/block when MC disagrees
      - Juice-trap THIRD to demote on side-price traps (sport-specific
        thresholds — MLB -200, NCAAF/NFL -300, NCAAB/NBA -400)
      - NCAAF large-spread FOURTH — sport-scoped, catches "Stanford +24.5"
        pattern where scorer stacked weak signals on a fadeable dog
      - Publish gate LAST — reads final pp state after all demotes and
        hard-caps PRIMEs that don't earn PRIME rigor
    """
    pp = apply_oc_flip_gate(pp, ctx)
    pp = apply_mc_dissent_gate(pp, ctx)
    pp = apply_juice_trap_gate(pp, ctx, sport=sport)
    # 2026-10-02: runs with the other demote gates, BEFORE the publish gate,
    # so a pick with no market for its own type cannot reach a published tier.
    pp = apply_unpriced_market_gate(pp, ctx, sport=sport)
    if sport == 'NCAAF':
        pp = apply_ncaaf_large_spread_gate(pp, ctx)
        # 2026-09-30: measured dog/fav split on 256 graded sides — see the
        # gate's docstring. Runs after large-spread so both can demote.
        pp = apply_ncaaf_high_conviction_dog_cap(pp, ctx)
    # 2026-09-03 LR ML OVERRIDE — supervised models replace legacy ML
    # picks per sport. Runs AFTER other gates so juice-trap/MC-dissent
    # output still gets a chance; LR fires as a final override for ML.
    # 2026-09-14: NHL + NBA added post-backfill (see project_v1_0_1 #12).
    if sport in ('MLB', 'NFL', 'NCAAF', 'NHL', 'NBA'):
        pp = apply_ml_lr_override(pp, ctx, sport=sport)
    # 2026-10-02 · MUST SIT AFTER THE OVERRIDE, WHICH IS THE WHOLE POINT.
    # apply_juice_trap_gate above runs THIRD, but apply_ml_lr_override builds a
    # fresh ML pick here at SIXTH — so override moneylines were never price-
    # checked. 44 of 73 live NFL ML picks sat past -200, two of them PRIME.
    # Rerouting here catches both the legacy and the override paths, because
    # every ML pick that survives to this line passes through it.
    pp = apply_heavy_ml_spread_reroute(pp, ctx, sport=sport)
    # Re-run the juice trap on the post-override pick: a reroute may not have
    # been possible (no spread stored), and in that case the ML must still be
    # demoted rather than ship untouched at a trap price.
    pp = apply_juice_trap_gate(pp, ctx, sport=sport)
    # 2026-09-03 MLB TOTAL LR OVERRIDE — supervised total model.
    # Test acc 59.8%, PRIME_OVER 69% n=71, PRIME_UNDER 62% n=111.
    if sport == 'MLB':
        pp = apply_mlb_total_lr_override(pp, ctx)
    # 2026-09-03 NCAAF TOTAL LR OVERRIDE — weak +3.5pp lift baseline
    # model. Runs in demote-only mode (STRONG cap) so it can't manufacture
    # a PRIME from a weak signal, but CAN kill obviously-wrong legacy
    # totals via the coin-flip path.
    if sport == 'NCAAF':
        pp = apply_ncaaf_total_lr_override(pp, ctx)
    # 2026-09-08 NFL TOTAL LR OVERRIDE — weak lift baseline model trained
    # on 6 seasons (2020-2025 nflverse). Shadow-only for now; downstream
    # Sharp Card LR-shadow-conflict gate uses `_lr_total_shadow` to drop
    # picks where LR disagrees w/ pipeline. See apply_nfl_total_lr_override
    # + project_lr_totals_investigation_908.
    if sport == 'NFL':
        pp = apply_nfl_total_lr_override(pp, ctx)

    # 2026-09-09: BACKFILL LR SHADOWS unconditionally. Root fix for the
    # "3/15 games missing LR shadow" bug — LR was only writing shadow
    # when it overrode a market. Games where LR *would* fire but wasn't
    # triggered (because the ensemble already picked the market LR
    # wanted, or because LR was on a different market than the pick)
    # ended up with no shadow at all → downstream gates (POTD LR check,
    # Sharp Card LR-conflict, watchdogs) silently no-op'd.
    #
    # 2026-09-10 UPGRADE — was `if pp.get('_lr_ml_shadow') is None`, which
    # only filled gaps and never refreshed. Audit found 3/5 MLB games
    # tonight had stale shadows (identical p_home=0.4076 fallback from
    # an earlier compute pass, before close_lines dropped). Fresh compute
    # gives distinct real values (0.5973, 0.9624, 0.1614, etc). Fix:
    # ALWAYS recompute at this stage — this runs after all override paths
    # + late-updated market data, so latest ctx should be the source of
    # truth for the shadow. Only skip when the model file itself is
    # missing or the compute raises. Cheap (single linear pass over
    # ~100 features), no reason to cache.
    if isinstance(pp, dict):
        try:
            if sport in ('MLB', 'NFL', 'NCAAF', 'NHL', 'NBA'):
                _map = {'MLB': _LR_MODEL_MLB_ML, 'NFL': _LR_MODEL_NFL_ML,
                        'NCAAF': _LR_MODEL_NCAAF_ML,
                        'NHL': _LR_MODEL_NHL_ML, 'NBA': _LR_MODEL_NBA_ML}
                _model = _map.get(sport)
                if _model is not None:
                    _pred = _lr_predict_ml(ctx, model=_model)
                    if _pred is not None:
                        pp['_lr_ml_shadow'] = _pred
        except Exception:
            pass
        try:
            if sport == 'MLB' and _LR_MODEL_MLB_TOTAL is not None:
                _pred = _lr_predict_total(ctx, model=_LR_MODEL_MLB_TOTAL)
                if _pred is not None:
                    pp['_lr_total_shadow'] = _pred
        except Exception:
            pass

    # ══ 2026-09-27 · RE-CHECK MC AFTER THE LR OVERRIDE ══
    #
    # apply_mc_dissent_gate runs near the top, but apply_ml_lr_override
    # REPLACES the pick further down — by design, "LR fires as a final
    # override for ML". So the pick that actually ships had never been
    # compared to the simulation, and nothing downstream did it either.
    #
    # NHL 2026-09-30 is the demonstration: Toronto shipped at conviction 68
    # and Winnipeg at 75, both from lr_v1, while MC had them at 49.1% and
    # 49.7%. Two models 25 points apart, no reconciliation, and the card
    # showed both numbers side by side.
    #
    # Running the same gate a second time is deliberately cheap: it is
    # idempotent (a pick already demoted fails the tier check and returns
    # untouched) and it means whatever engine wins the override still has
    # to survive the sim. This is the "engine output soundness" check —
    # the final pick, not an intermediate one, is what gets tested.
    pp = apply_mc_dissent_gate(pp, ctx)

    # ══ 2026-09-30 · NCAAF PRIME MONEYLINE CAP ══
    # Placed HERE, not with the other NCAAF gates above, for the same reason the
    # MC re-check is here: apply_ml_lr_override replaces the pick further down
    # the chain and can hand back a fresh PRIME ML, which would silently undo a
    # cap applied earlier. NCAAF PRIME ML is 2-10 at ROI -72.6% and carries the
    # sport's entire loss — see the gate's docstring for the 248-game breakdown.
    # Caps to LEAN, not STRONG: PRIME and STRONG share the 2u stake, so a
    # PRIME->STRONG demotion measured as an exact no-op (-3.76u either way) and
    # only served to drag STRONG from +8.2% to -2.6% ROI.
    if sport == 'NCAAF':
        pp = apply_ncaaf_prime_ml_cap(pp, ctx)

    # ══ 2026-09-30 · NCAAF TOTALS UNPUBLISHED ══
    # 12-27 (30.8%, n=39, z=-2.40, -16.09u) and losing on BOTH sides, while
    # projected_total's MAE (12.81) is WORSE than the closing line's (11.82).
    # No tier concentration to cap — it loses at every tier — and no gate has
    # stood in front of these since apply_ncaaf_total_lr_override went
    # shadow-only on 09-15. Routes to PASS, the only tier truly outside the
    # record. See the gate's docstring.
    if sport == 'NCAAF':
        pp = apply_ncaaf_total_suppression(pp, ctx)

    # 2026-09-03 BADGE-CONFLICT GATES (badge audit fixes #1 + #2):
    # Silent contradictions between chips on the same game card.
    # These gates catch pipeline-side contradictions BEFORE they render.
    pp = apply_vault_fade_dissent_gate(pp, ctx)
    pp = apply_trap_game_gate(pp, ctx, sport=sport)
    pp = apply_publish_gate(pp, ctx)
    return pp


def apply_vault_fade_dissent_gate(pp, ctx):
    """Badge-audit fix #1 (2026-09-03): when a Vault matched-pattern is
    FADE on the same side as primary_play, the LIST renders both
    'PRIME · Team ML' AND '⚠️ FADE-HOME' side-by-side. Direct
    contradiction. Fix: demote pp tier to COVERAGE (LOW CONVICTION chip
    + still visible in game detail) when Vault says fade our own side.

    Vault matched_patterns are attached by attach_vault_matches.py to
    ctx.matched_patterns[]. Each item has direction (BACK|FADE) + side."""
    try:
        if not (pp and isinstance(pp, dict)): return pp
        if pp.get('tier') not in ('PRIME', 'STRONG'): return pp
        mp = ctx.get('matched_patterns') or []
        if not isinstance(mp, list) or not mp: return pp
        top = mp[0] if isinstance(mp[0], dict) else {}
        direction = str(top.get('direction') or '').upper()
        vault_side = str(top.get('side') or '').upper()
        pp_side = str(pp.get('side') or '').upper()
        # Only demote when Vault FADES the side pp is backing
        if direction == 'FADE' and pp_side and vault_side and \
                (pp_side == vault_side or pp_side in vault_side or vault_side in pp_side):
            pp['_pre_vault_tier'] = pp.get('tier')
            pp['tier'] = 'COVERAGE'
            pp['_vault_dissent'] = {
                'pattern_key': top.get('key') or top.get('pattern_key'),
                'reason': f'Vault pattern FADES same side as pick ({vault_side}) — demoted',
            }
            pp['audit_note'] = 'Vault FADE dissent on same side — coverage'
    except Exception:
        pass
    return pp


def apply_trap_game_gate(pp, ctx, sport='NCAAF'):
    """Badge-audit fix #2 (2026-09-03): NCAAF trap-game rule (top-10
    favorite laying ≥17 vs unranked) fires as a fade-the-fav badge on
    the LIST. But if pp.tier is PRIME/STRONG on the favorite, LIST
    renders 'PRIME · Alabama -18.5' next to '⚠️ TRAP GAME' — direct
    contradiction (one says back, one says fade). Fix: when trap gate
    fires on our pp side, demote to COVERAGE."""
    if sport != 'NCAAF': return pp
    try:
        if not (pp and isinstance(pp, dict)): return pp
        if pp.get('tier') not in ('PRIME', 'STRONG'): return pp
        home_rank = ctx.get('home_ap_rank')
        away_rank = ctx.get('away_ap_rank')
        spread = ctx.get('close_spread')
        # Home is top-10 favorite (spread <= -17) with unranked away
        try: spread = float(spread) if spread is not None else None
        except (TypeError, ValueError): spread = None
        try: hr = int(home_rank) if home_rank is not None else None
        except (TypeError, ValueError): hr = None
        try: ar = int(away_rank) if away_rank is not None else None
        except (TypeError, ValueError): ar = None
        home_is_trap = (hr is not None and hr <= 10 and (ar is None or ar > 25)
                        and spread is not None and spread <= -17)
        # Away is top-10 favorite laying 17+ (positive close_spread from home perspective)
        away_is_trap = (ar is not None and ar <= 10 and (hr is None or hr > 25)
                        and spread is not None and spread >= 17)
        pp_side = str(pp.get('side') or '').upper()
        pp_type = str(pp.get('type') or '').lower()
        # Only demote when pp backs the trap-favorite side
        if home_is_trap and pp_side == 'HOME' and pp_type in ('ml', 'rl'):
            pp['_pre_trap_tier'] = pp.get('tier')
            pp['tier'] = 'COVERAGE'
            pp['_trap_dissent'] = f'NCAAF top-10 home fav laying {abs(spread):.1f} vs unranked — trap fade'
            pp['audit_note'] = 'NCAAF TRAP GAME on home fav — coverage'
        elif away_is_trap and pp_side == 'AWAY' and pp_type in ('ml', 'rl'):
            pp['_pre_trap_tier'] = pp.get('tier')
            pp['tier'] = 'COVERAGE'
            pp['_trap_dissent'] = f'NCAAF top-10 away fav laying {spread:.1f} vs unranked — trap fade'
            pp['audit_note'] = 'NCAAF TRAP GAME on away fav — coverage'
    except Exception:
        pass
    return pp


def apply_ncaaf_total_lr_override(pp, ctx):
    """NCAAF total LR — SHADOW-ONLY mode (2026-09-15).

    Prior code demoted pipeline total picks to COVERAGE whenever LR said
    'coin flip'. But per project_ncaaf_lr_total_dead_914 (verified on
    82/82 NCAAF Wk3 slate): the model is inert — trained on 6 market-line
    features only, no team EPA / pace / weather / SP+. p_over ranges
    only [0.4578, 0.5500] across the entire slate. Test lift is 1.47pp
    over baseline 50.30% (test 51.77%).

    Effect of prior behavior: EVERY NCAAF total pick that flowed through
    this gate got demoted to COVERAGE because LR always said 'NONE side'.
    Pipeline totals were being silently killed by a useless model.

    Fix: skip the demote entirely. Keep the shadow write for observability
    (still populates primary_play._lr_total_shadow so downstream Sharp Card
    LR-shadow-conflict gate can read it — same as NFL total mode). Once
    the v1.1 retrain adds real team features, the shadow will start
    carrying signal and we can reinstate the demote.
    """
    if _LR_MODEL_NCAAF_TOTAL is None: return pp
    try:
        pred = _lr_predict_total(ctx, model=_LR_MODEL_NCAAF_TOTAL)
        if pred is None: return pp
        old_pp = pp if isinstance(pp, dict) else {}
        # Shadow-only: write the prediction, never mutate tier
        old_pp['_lr_total_shadow'] = pred
        return old_pp
    except Exception:
        return pp


def apply_nfl_total_lr_override(pp, ctx):
    """NFL total LR override — SHADOW-ONLY mode.

    Trained 2026-09-08 on nflverse 2020-2025 backfill (1,693 games).
    Test acc ~50% vs 49% baseline — model too weak to promote picks,
    same territory as NCAAF total. Wired as shadow-only so the signal
    still flows to Sharp Card LR-shadow-conflict gate (see
    _lr_shadow_conflict in generate_sharp_card.py).

    Legit protective use: when pipeline picks NFL total X and LR shadow
    says STRONG Y (opposite), Sharp Card drops the pick. Doesn't
    generate its own picks from this weak model.
    """
    if _LR_MODEL_NFL_TOTAL is None: return pp
    try:
        pred = _lr_predict_total(ctx, model=_LR_MODEL_NFL_TOTAL)
        if pred is None: return pp
        old_pp = pp if isinstance(pp, dict) else {}
        was_total = str(old_pp.get('type','')).lower() == 'total'
        if pred['suggested_side'] == 'NONE':
            if was_total:
                # Coin-flip zone + pipeline picked total → demote to
                # coverage. Same rule as NCAAF.
                old_pp['_lr_total_shadow'] = pred
                old_pp['_pre_lr_tier']  = old_pp.get('tier')
                old_pp['tier']  = 'COVERAGE'
                old_pp['audit_note'] = f'NFL total LR coin flip (p_over={pred["p_over"]:.2f}) — legacy demoted'
                return old_pp
            old_pp['_lr_total_shadow'] = pred
            return old_pp
        # LR has a lean — shadow-only until model gets stronger
        old_pp['_lr_total_shadow'] = pred
        return old_pp
    except Exception:
        return pp


def _lr_predict_total(ctx: dict, model=None) -> dict | None:
    """Returns {p_over, suggested_side, suggested_tier}. Defaults to MLB model —
    pass model=<sport-specific> for others (e.g. _LR_MODEL_NCAAF_TOTAL)."""
    m = model if model is not None else _LR_MODEL_MLB_TOTAL
    if m is None: return None
    try:
        z = m['intercept']
        n_imputed = 0
        for i, f in enumerate(m['features']):
            v = ctx.get(f)
            try: v = float(v) if v is not None else None
            except (TypeError, ValueError): v = None
            if v is None:
                v = m['imputer_medians'][i]
                n_imputed += 1
            scale = m['scaler_scale'][i]
            scaled = (v - m['scaler_mean'][i]) / scale if scale != 0 else 0
            z += m['coefficients'][i] * scaled
        if z >= 0: p = 1.0 / (1.0 + _math.exp(-z))
        else: ez = _math.exp(z); p = ez / (1.0 + ez)
        # Blind model -> no opinion. Without this the constant 0.4718
        # lands in the coin-flip band and KILLS the ensemble's pick.
        if _is_blind(p, n_imputed, m):
            return None
        if p >= 0.65:   tier, side = 'PRIME',  'OVER'
        elif p >= 0.55: tier, side = 'STRONG', 'OVER'
        elif p >= 0.45: tier, side = None,     'NONE'
        elif p >= 0.35: tier, side = 'STRONG', 'UNDER'
        else:           tier, side = 'PRIME',  'UNDER'
        return {'p_over': round(p, 4), 'suggested_side': side, 'suggested_tier': tier}
    except Exception:
        return None


def apply_mlb_total_lr_override(pp, ctx):
    """MLB total LR override. Same pattern as ML: if LR confident,
    replace with LR tier/side. If LR coin flip, demote legacy total to
    COVERAGE. If legacy is ML/RL not total, only override if LR is PRIME."""
    if _LR_MODEL_MLB_TOTAL is None: return pp
    try:
        pred = _lr_predict_total(ctx)
        if pred is None: return pp
        old_pp = pp if isinstance(pp, dict) else {}
        was_total = str(old_pp.get('type','')).lower() == 'total'
        if pred['suggested_side'] == 'NONE':
            if was_total:
                old_pp['_lr_total_shadow'] = pred
                old_pp['_pre_lr_tier'] = old_pp.get('tier')
                old_pp['tier'] = 'COVERAGE'
                old_pp['audit_note'] = f'MLB total LR sees coin flip (p_over={pred["p_over"]:.2f}) — legacy demoted'
                return old_pp
            return pp
        if not was_total and old_pp:
            # Existing pp is ML/RL — only override if LR total is PRIME
            if pred['suggested_tier'] != 'PRIME':
                old_pp['_lr_total_shadow'] = pred
                return old_pp
        # Build new pp
        close_total = ctx.get('close_total')
        label = f'{pred["suggested_side"].title()} {close_total}' if close_total else pred['suggested_side'].title()
        p = pred['p_over']
        conviction = int(round((p if pred['suggested_side'] == 'OVER' else (1 - p)) * 100))
        new_pp = {
            'type': 'total',
            'tier': pred['suggested_tier'],
            'side': pred['suggested_side'],
            'label': label,
            'sub': f'Supervised total model backs {pred["suggested_side"].title()} · {conviction}% confidence',
            'conviction': conviction,
            'line': close_total,
            '_engine': 'lr_v1',
            # 2026-09-09: standardized shadow field (parity w/ ML LR override).
            # Was `_lr_p_over` raw only. Downstream reads `_lr_total_shadow`
            # dict elsewhere — the PRIME override path was writing a different
            # field so POTD gate / Sharp Card LR check silently missed it.
            '_lr_total_shadow': pred,
            '_lr_p_over': p,  # deprecated raw — remove after 30d verify
            '_lr_model_version': _LR_MODEL_MLB_TOTAL.get('version', ''),
            '_pre_lr': {
                'tier': old_pp.get('tier'), 'side': old_pp.get('side'),
                'type': old_pp.get('type'), 'engine': old_pp.get('_engine'),
                'conviction': old_pp.get('conviction'),
            } if old_pp else None,
            'audit_note': f'LR TOTAL override · p_over={p:.2f} · was {(old_pp.get("tier") or "none")}/{(old_pp.get("type") or "none")}',
        }
        # 2026-09-09: preserve _lr_ml_shadow if ML override left it on old_pp.
        # ML LR override runs FIRST in apply_all_defensive_gates; if it wrote
        # `_lr_ml_shadow` (non-PRIME path), then TOTAL LR override built a
        # fresh new_pp here and DROPPED that field. Downstream code that
        # reads ML LR (POTD ML picks) then finds nothing. Copy it forward.
        if isinstance(old_pp, dict) and old_pp.get('_lr_ml_shadow') is not None:
            new_pp['_lr_ml_shadow'] = old_pp['_lr_ml_shadow']
        if isinstance(old_pp, dict) and old_pp.get('_lr_p_home_win') is not None:
            new_pp['_lr_p_home_win'] = old_pp['_lr_p_home_win']  # legacy raw
        # 2026-09-20: a discipline cap outranks an LR promotion.
        return respect_discipline_cap(new_pp, old_pp)
    except Exception:
        return pp


# Backwards-compat alias — existing MLB callers still work
def apply_mlb_ml_lr_override(pp, ctx):
    return apply_ml_lr_override(pp, ctx, sport='MLB')
