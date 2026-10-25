"""A recorded decision about which bet this is survives recomputation.

Covers an approved manual pick AND a recorded flip / un-flip: see
DECISION_MARKERS below for why the flip had to be added on 2026-10-24.

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


#: Every marker that records a DELIBERATE decision about which bet this is.
#: 2026-10-24: `_manual_correction` was the only one, so a recompute that ran
#: while the slate was unlocked silently reverted 5 NCAAF and 2 NFL FLIPS --
#: after The Sharp had already published the flipped side and captured
#: receipts on it. `primary_play` then disagreed with its own receipts, so
#: game detail showed the un-flipped side on games we had published flipped,
#: and Houston @ Utah was still hours from kickoff in that state.
#:
#: A flip is not the engine's opinion; it is a recorded reversal of it, with
#: the prior value stored for audit, exactly like a manual override. It
#: belongs behind the same protection. Fourth instance in one day of the flip
#: not being durable -- the other three were the two composers and the
#: jerry_reads call pin.
#: 2026-10-25 · FIFTH instance, and the first that reached the POTD.
#: `_board_aligned` (align_picks_to_board.py) was missing from this tuple, so
#: preserve() saw no decision, returned the engine's pick untouched, and a
#: board-aligned pick had exactly zero protection. Measured consequence:
#: DEN @ ARI was aligned to ARI +5 at 14:15Z, a context rebuild at 14:33Z
#: overwrote it with the engine's DEN -4.5, and the pick lock then froze the
#: REVERTED value -- while the published POTD still read ARI +5. The app would
#: have shown a badge on one side of a game and the Play of the Day on the
#: other.
#:
#: A board alignment is not the engine's opinion either. It is a deliberate,
#: recorded, user-FACING move of the number onto a quote books actually post,
#: and it only ever moves in the user's favour. Once shown it is a commitment,
#: so it belongs behind the same protection as a flip or a hand correction.
DECISION_MARKERS = ('_manual_correction', '_flipped_from', '_unflipped_from',
                    '_board_aligned')


def _mc(pp):
    """The first recorded decision on this pick, or None."""
    if not isinstance(pp, dict):
        return None
    for key in DECISION_MARKERS:
        v = pp.get(key)
        if isinstance(v, dict):
            return v
    return None


def _markers(pp):
    """Every decision marker present, so all of them survive the recompute."""
    if not isinstance(pp, dict):
        return {}
    return {k: pp[k] for k in DECISION_MARKERS
            if isinstance(pp.get(k), dict)}


def is_overridden(pp) -> bool:
    """True when this primary_play carries a recorded decision of any kind."""
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

    # Carry EVERY marker forward, not just the one that matched. A game can
    # be flipped and then hand-corrected, and dropping either one would let
    # the next recompute revert it.
    for _k, _v in _markers(old_pp).items():
        new_pp[_k] = _v
    new_pp['_engine_would_have_said'] = engine_said
    if not changed:
        return new_pp, None
    new_pp['_manual_override_held'] = changed
    kinds = ', '.join(sorted(_markers(old_pp)))
    who = mc.get('by') or kinds or 'manual'
    return new_pp, f'decision held ({who}): ' + '; '.join(changed[:4])
