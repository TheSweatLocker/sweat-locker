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

    Rule (empirical from Padres 8/22 audit — ensemble PRIME 83 SD ML while
    MC had SD at 44.9% win prob):
        PRIME on ML market + MC pick_side_prob < 0.52 -> STRONG (conv cap 65)
        STRONG on ML market + MC pick_side_prob < 0.50 -> LEAN (conv cap 55)

    Preserves original tier + MC pct in pp['_mc_dissent'] audit field so
    downstream (Sharp Card, Jerry reads) can surface the demote reason.

    No-op when pp is None, non-ML, LEAN-or-lower, or MC absent.
    """
    try:
        if not (pp and isinstance(pp, dict)): return pp
        if pp.get('_engine') != 'ensemble_v2': return pp
        if str(pp.get('type', '')).lower() != 'ml': return pp
        if pp.get('tier') not in ('PRIME', 'STRONG'): return pp

        mc = ctx.get('mc_probabilities') if isinstance(ctx.get('mc_probabilities'), dict) else None
        if not mc: return pp

        cur_side = str(pp.get('side', '')).upper()
        if cur_side == 'HOME':
            pick_prob = mc.get('mc_p_home_win')
        elif cur_side == 'AWAY':
            pick_prob = mc.get('mc_p_away_win')
        else:
            return pp
        if pick_prob is None: return pp
        try:
            pick_prob_f = float(pick_prob)
        except (TypeError, ValueError):
            return pp

        # PRIME requires MC >= 0.52 (5pp edge over MLB breakeven 47.6%).
        # STRONG requires MC >= 0.50 (edge over pure toss-up).
        new_tier = None
        reason = None
        if pp['tier'] == 'PRIME' and pick_prob_f < 0.52:
            new_tier = 'STRONG'
            reason = f'MC dissent: sim has our side at {pick_prob_f*100:.1f}% (<52% PRIME threshold)'
        elif pp['tier'] == 'STRONG' and pick_prob_f < 0.50:
            new_tier = 'LEAN'
            reason = f'MC dissent: sim has our side at {pick_prob_f*100:.1f}% (<50% STRONG threshold)'

        if new_tier:
            pp['_mc_dissent'] = {
                'orig_tier': pp['tier'],
                'mc_pick_win_pct': round(pick_prob_f * 100, 1),
                'reason': reason,
            }
            pp['tier'] = new_tier
            tier_cap = {'LEAN': 55, 'STRONG': 65}
            if isinstance(pp.get('conviction'), (int, float)):
                pp['conviction'] = min(int(pp['conviction']), tier_cap.get(new_tier, 55))
    except Exception:
        pass  # gate errors must never block the publish
    return pp


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

        # Rule 3: TOTAL model concurrence check
        if mkt == 'total':
            line = ctx.get('close_total')
            model_agreements = 0
            model_checked = 0
            for key in ('jerry_pred_total', 'projected_total'):
                v = ctx.get(key)
                if v is not None and line is not None:
                    try:
                        diff = float(v) - float(line)
                        model_checked += 1
                        if side == 'OVER' and diff > -1.0:
                            model_agreements += 1
                        elif side == 'UNDER' and diff < 1.0:
                            model_agreements += 1
                    except (TypeError, ValueError):
                        pass
            if mc and mc.get('mc_mean_total') is not None and line is not None:
                try:
                    diff = float(mc['mc_mean_total']) - float(line)
                    model_checked += 1
                    if side == 'OVER' and diff > -1.0: model_agreements += 1
                    elif side == 'UNDER' and diff < 1.0: model_agreements += 1
                except (TypeError, ValueError): pass
            if model_checked >= 2 and model_agreements < 2:
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


def apply_all_defensive_gates(pp: dict | None, ctx: dict) -> dict | None:
    """Apply all defensive gates in the canonical order:
    OC flip → MC dissent → publish gate. Callers that want everything
    can use this instead of chaining apply_*_gate calls.

    Order matters:
      - OC-flip FIRST because it may change pp.side (MC-dissent reads pp.side)
      - MC-dissent SECOND to demote pick tier when MC disagrees
      - Publish gate LAST — reads the final pp state after both flip + MC
        dissent and hard-caps PRIMEs that don't earn PRIME rigor
    """
    pp = apply_oc_flip_gate(pp, ctx)
    pp = apply_mc_dissent_gate(pp, ctx)
    pp = apply_publish_gate(pp, ctx)
    return pp
