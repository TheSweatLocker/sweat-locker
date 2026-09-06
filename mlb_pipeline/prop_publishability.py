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


_UNPUBLISHABLE_PLAYBOOK_GATES = {'NO_VALIDATED_SIGNALS', 'ANTI_VALIDATED'}
_TIER_RANK = {'PRIME': 3, 'STRONG': 2, 'LEAN': 1, 'SKIP': 0}


def _coerce_signals(sig) -> dict:
    if isinstance(sig, dict): return sig
    if isinstance(sig, str):
        try: return json.loads(sig) or {}
        except Exception: return {}
    return {}


def is_publishable(prop: dict) -> tuple[bool, str]:
    """Returns (publishable, reason). False → SKIP for user surface.

    Reason string categories: 'coverage_kill', 'playbook_gate',
    'lr_tier_drift', 'ok'.
    """
    sig = _coerce_signals(prop.get('signals'))

    # Hard kill: refit override tagged this as unpublishable coverage stub
    kill = sig.get('_coverage_kill_gate')
    if kill and str(kill).lower() not in ('false', '0', 'no', ''):
        return False, f'coverage_kill={kill}'

    # Hard kill: playbook gate says no validated signals
    pg = sig.get('_playbook_prop_gate') or ''
    if pg in _UNPUBLISHABLE_PLAYBOOK_GATES:
        return False, f'playbook_gate={pg}'

    # Tier-drift check: if LR says a lower tier than the stored `tier`,
    # trust LR. Catches props whose tier didn't get demoted before a
    # composer read them (race between generate_props → composer and
    # apply_refit_verdict_override).
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
