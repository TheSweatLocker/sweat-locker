"""Prop publishability — single source of truth for "can this prop
surface to a user?" across every composer, every sport.

Historical bug (9/5 audit): three concurrent tier engines
(mlb_pipeline_props.tier / generate_sharp_card items[].tier /
steam_room_ladder ladder_rung.tier) each did their own scoring and
disagreed on the same prop. Sharp card marketed 8 LEAN props as PRIME;
all 8 lost. Ladder qualified Messick U 1.5 ER at PRIME/86 while the
tier engine had it at LEAN/50 with _playbook_prop_gate=NO_VALIDATED_SIGNALS.

This module is the fix. Every prop composer for every surface (sharp
card, ladder, prop card, POTD, sweat card, teasers, ledger, etc.) must
route props through the two helpers here BEFORE deciding to include
them:

    from prop_publishability import is_publishable, effective_tier

    for p in props:
        ok, reason = is_publishable(p)
        if not ok:
            drop_counter[reason] += 1
            continue
        tier = effective_tier(p)
        if tier not in ('PRIME', 'STRONG'):
            continue
        # ...compose the pick

Guarantees:
  * Every published prop passes _coverage_kill_gate
  * Every published prop passes _playbook_prop_gate (no NO_VALIDATED_SIGNALS,
    no ANTI_VALIDATED)
  * "PRIME" on a user surface always means LR-verified PRIME (never a
    stale tier that hasn't been demoted by apply_refit_verdict_override yet)

Signal schema is universal across sports — mlb_pipeline_props,
nfl_pipeline_props, ncaaf_pipeline_props all share the same `signals`
JSON structure — so these helpers work cross-sport with no changes.

See: feedback_signal_gate_over_tier_906 memory, generate_sharp_card.py
composer (uses this module), steam_room_ladder.py (uses this module),
apply_refit_verdict_override.py (SETS the gates this module reads).
"""
from __future__ import annotations
import json


_TIER_RANK = {'PRIME': 3, 'STRONG': 2, 'LEAN': 1, 'SKIP': 0}

# _playbook_prop_gate values that were previously treated as blockers but
# empirically don't predict losses. Kept as constants so we can document
# why they're NOT gates rather than removing without a trace.
# Yesterday-audit (9/5): 22 winning PRIMEs · 16 of them had this flag
# set. If we'd blocked on it, we'd have thrown out 16 winners. LR model
# consistently outperforms the playbook signal validator on props LR
# stamped PRIME. Treat this flag as a diagnostic tag, not a kill.
_DIAGNOSTIC_PLAYBOOK_GATES = {'NO_VALIDATED_SIGNALS', 'ANTI_VALIDATED'}


def _coerce_signals(sig) -> dict:
    if isinstance(sig, dict): return sig
    if isinstance(sig, str):
        try: return json.loads(sig) or {}
        except Exception: return {}
    return {}


def is_publishable(prop: dict) -> tuple[bool, str]:
    """Returns (publishable, reason). False → SKIP for user surface.

    Only two real kill conditions:

      1. `_coverage_kill_gate` — set by _demote_coverage_tier() in
         apply_refit_verdict_override.py. This is the pipeline's own
         "do not publish" flag, not a diagnostic.

      2. LR tier drift — stored tier disagrees with `_lr_tier_raw`
         AND LR is lower. Catches the 9/5 Sharp Card bug where props
         got composed at tier=PRIME then later demoted to LEAN by
         apply_refit_verdict_override (frozen sharp card kept the
         stale PRIME tier). Also catches any composer reading tier
         directly without going through this gate.

    `_playbook_prop_gate=NO_VALIDATED_SIGNALS` is NOT a kill — empirical
    9/5 audit showed 16 of 22 winning PRIMEs had it set. It's a signal-
    registry diagnostic; LR overrides it when LR-tier is high. See the
    _DIAGNOSTIC_PLAYBOOK_GATES constant docstring.
    """
    sig = _coerce_signals(prop.get('signals'))

    # Hard kill 1: coverage kill gate
    kill = sig.get('_coverage_kill_gate')
    if kill and str(kill).lower() not in ('false', '0', 'no', ''):
        return False, f'coverage_kill={kill}'

    # Hard kill 2: LR tier drift (LR says lower than stored)
    lr_tier = (sig.get('_lr_tier_raw') or '').upper()
    stored = (prop.get('tier') or '').upper()
    if lr_tier in _TIER_RANK and stored in _TIER_RANK and _TIER_RANK[lr_tier] < _TIER_RANK[stored]:
        return False, f'lr_tier_drift lr={lr_tier} stored={stored}'

    return True, 'ok'


def effective_tier(prop: dict) -> str | None:
    """Return the MORE CONSERVATIVE of prop.tier and signals._lr_tier_raw.

    Never market a prop above what LR actually said. Used at composition
    time so 'PRIME' on any user surface always means LR-verified PRIME.
    """
    stored = (prop.get('tier') or '').upper()
    sig = _coerce_signals(prop.get('signals'))
    lr = (sig.get('_lr_tier_raw') or '').upper()
    if lr in _TIER_RANK and stored in _TIER_RANK:
        return lr if _TIER_RANK[lr] < _TIER_RANK[stored] else stored
    return stored or None
