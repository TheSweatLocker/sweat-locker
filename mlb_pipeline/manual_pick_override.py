"""An approved manual pick survives recomputation.

WHY (2026-10-06)
----------------
Andy, on the Padres/Brewers slate: "I want Padres ML being the play on detail
and sweat card." That instruction was applied — mlb_game_context.primary_play
for MIL @ SD 2026-10-06 carries

    side              HOME
    tier              STRONG   conviction 71
    label             San Diego Padres ML
    _manual_correction {"at": "2026-10-06", "by": "andy-approved",
                        "was": {"sub": "⚠ Engine passed: MC sim ..."}}

and the engine underneath it says something else entirely:

    _pre_lr            side HOME, tier COVERAGE, engine ensemble_v2
    _lr_p_home_win     0.4469          (LR has HOME at 44.7%)
    _lr_ml_shadow      suggested_side AWAY, suggested_tier STRONG
    _model_edge_pp     -45.9

**Nothing in the repo reads `_manual_correction`.** Verified by grep across
every .py: there is not one reference. recompute_primary_play builds `new_pp`
fresh from ensemble_scorer and writes it over the old row, so the next
recompute silently reverts an approved pick to COVERAGE — or to the opposite
side, since LR suggests AWAY.

That is the "side selection is not deterministic" symptom: MIL @ SD was
observed as HOME/95 at 12:58:44 and AWAY/0 at 13:00:50. Not a race in the
scorer — a manual decision with no protection, against recomputes that keep
running all day.

WHAT THIS PRESERVES, AND WHAT IT DOES NOT
An approved override fixes WHAT THE PICK IS: side, tier, label, line, type,
conviction. Everything else the recompute produces is still written —
the sub/prose, the model fields, the shadows, the audit trail — because those
are the engine's current reading and they should stay current even when we
have overridden its conclusion. The override is also re-stamped each time so
the trail says it was honoured rather than silently re-applied.

Same shape and the same boundary as receipt_pins: a human decision, like a
published pick, owns the row it was made on.
"""
from __future__ import annotations

#: fields that define WHICH BET the pick is — these are what an override owns
PICK_FIELDS = ('type', 'side', 'tier', 'label', 'line', 'conviction')


def _mc(pp):
    if not isinstance(pp, dict):
        return None
    mc = pp.get('_manual_correction')
    return mc if isinstance(mc, dict) else None


def is_overridden(pp) -> bool:
    """True when this primary_play carries an approved manual correction."""
    return _mc(pp) is not None


def preserve(old_pp, new_pp):
    """(new_pp, note) with an approved manual pick carried forward.

    Returns new_pp untouched when there is no override, so the normal path
    costs nothing. Never invents an override and never removes one.
    """
    mc = _mc(old_pp)
    if mc is None or not isinstance(new_pp, dict):
        return new_pp, None

    # What this recompute concluded, kept before we overwrite it. Without
    # this the engine's current opinion is lost every run, and the override
    # becomes unauditable — you can no longer tell whether the engine has
    # come around to the manual call or still disagrees.
    engine_said = {f: new_pp.get(f) for f in PICK_FIELDS}

    changed = []
    for f in PICK_FIELDS:
        was = old_pp.get(f)
        if was is None:
            continue
        if str(was) != str(new_pp.get(f)):
            changed.append(f'{f}: {new_pp.get(f)!r}->{was!r}')
        new_pp[f] = was

    new_pp['_manual_correction'] = mc
    new_pp['_engine_would_have_said'] = engine_said
    if not changed:
        return new_pp, None
    new_pp['_manual_override_held'] = changed
    who = mc.get('by') or 'manual'
    return new_pp, f'manual override by {who} held: ' + '; '.join(changed[:4])
