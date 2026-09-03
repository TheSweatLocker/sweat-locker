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
        if pp.get('_engine') != 'ensemble_v2': return pp
        ptype = str(pp.get('type', '')).lower()
        if ptype not in ('ml', 'total'): return pp
        if pp.get('tier') not in ('PRIME', 'STRONG', 'LEAN'): return pp

        mc = ctx.get('mc_probabilities') if isinstance(ctx.get('mc_probabilities'), dict) else None
        if not mc: return pp

        cur_side = str(pp.get('side', '')).upper()
        pick_prob = None
        if ptype == 'ml':
            if cur_side == 'HOME':   pick_prob = mc.get('mc_p_home_win')
            elif cur_side == 'AWAY': pick_prob = mc.get('mc_p_away_win')
        elif ptype == 'total':
            # 2026-08-28 extended: totals were bypassing MC gate. PHI Under 8.0
            # shipped as STRONG 84 with -15pp MC edge because gate skipped.
            if cur_side == 'OVER':    pick_prob = mc.get('mc_p_over')
            elif cur_side == 'UNDER': pick_prob = mc.get('mc_p_under')
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

        # ML is trap-priced. Find the best non-ML alternative.
        LEAN_FLOOR = 0.3  # matches ensemble_scorer TIER_MIN_SCORE['LEAN']
        candidates = []
        for alt_market in ('total', 'rl'):
            alt = getattr(decision, alt_market, None)
            if alt is None or not alt.pick: continue
            if float(alt.score) < LEAN_FLOOR: continue
            candidates.append((alt_market, alt))
        if not candidates:
            # No alternative worth surfacing — leave as ML, downstream
            # juice-trap demote will drop tier. User will see LEAN ML
            # with the audit note explaining the juice.
            return decision
        # Pick the highest-scoring alternative
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
    if sport == 'NCAAF':
        pp = apply_ncaaf_large_spread_gate(pp, ctx)
    pp = apply_publish_gate(pp, ctx)
    return pp
