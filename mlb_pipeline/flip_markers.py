"""One reader for every way the engine records "we flipped this pick".

══ 2026-11-14 · TWO WRITERS, TWO WORDS FOR "THIS PICK WAS FLIPPED" ═════

Andy asked for one thing about flipped picks: "if were flipping we need ot
make sure jerry read matches". `generate_ncaaf_game_reads` implements it
properly — it injects a FADE framing into the prompt so the read says
plainly that we are fading our own earlier number. That reconciler keys on
`primary_play['_flipped_from']`.

But TWO independent gates flip a side, and they chose different keys:

    flip_football_spreads.py:480   ->  pp['_flipped_from']
    defensive_gates.py:706         ->  pp['_oc_flipped']    (oddscrowd fade)

and EVERY reader keys on `_flipped_from` alone:

    generate_ncaaf_game_reads.py:766   the prose FADE framing
    generate_ncaaf_game_reads.py:933   --flipped-only (the repair mode)
    generate_nfl_game_reads.py:2009    same framing, NFL
    generate_nfl_game_reads.py:2661    same selection, NFL
    generate_sharp_card.py:1695/2231   the double-flip guard
    flip_football_spreads.py:50        "that is what POTD skips on"

So an oddscrowd flip is invisible to the prose reconciler, to the repair
mode built to fix exactly this, to the Sharp's double-flip guard and to
the POTD skip. Measured 2026-11-14: 15 NCAAF games carry `_oc_flipped`
since 10-01 and 9 of them reached a PUBLISHABLE tier. Three were live that
morning:

    North Texas @ UTSA        UTSA ML          was North Texas ML
    Louisville @ N Carolina   North Carolina ML was Louisville ML
    Alabama @ Vanderbilt      Alabama -14      was Vanderbilt +17.5

The second was the Dog of the Day at Sweat Card rank 2, printing
"a 12.4-point efficiency gap that favors the away team decisively"
directly under a bet ON THE HOME TEAM. Two of the three were independently
flagged by consistency_watchdog's card_self_contradiction, which is how
one root cause showed up as several unrelated-looking alerts.

This is the same class as the three manual-POTD markers
(feedback_three_writers_three_manual_markers): the defect is not in either
writer, it is that a reader has to know every word for the thing. So this
module is the ONE place that list lives. A third flip gate adds its key
HERE and every reader gets it.

The two payloads disagree on field names as well as on the key, so
`flip_record` normalises them to one shape. Callers must not read the raw
dict — the normalised keys are the contract:

    side   the ORIGINAL side we were on   ('HOME' / 'AWAY')
    label  the ORIGINAL pick label        ('Louisville ML')
    tier   the ORIGINAL tier              ('STRONG')
    basis  why the flip is defensible     (measured record, not an opinion)
    marker which key carried it           ('_flipped_from' / '_oc_flipped')
    receipt_frozen_on_original  True when a published receipt will grade
           the ORIGINAL side, so the record scores our worse side and the
           prose must not claim a record or a closing-line edge.
"""

from __future__ import annotations

#: Every key a gate has ever used to record a side flip, newest last.
#: A new flip gate appends here and inherits every reader in the repo.
FLIP_KEYS = ('_flipped_from', '_oc_flipped')


def flip_record(pp: dict | None) -> dict | None:
    """Return the normalised flip record on `pp`, or None if it was not flipped.

    Checks every key in FLIP_KEYS. When more than one is present the FIRST
    match in FLIP_KEYS order wins, which keeps `_flipped_from` — the gate
    that also records `receipt_frozen_on_original` — authoritative over a
    later oddscrowd flip of the same pick.
    """
    if not isinstance(pp, dict):
        return None
    for key in FLIP_KEYS:
        rec = pp.get(key)
        if not isinstance(rec, dict):
            continue
        # `_oc_flipped` names the original fields orig_*; `_flipped_from`
        # stores them bare. Read both and let the bare one win so a future
        # writer using either spelling resolves.
        return {
            'marker': key,
            'side': rec.get('side') or rec.get('orig_side'),
            'label': rec.get('label') or rec.get('orig_label'),
            'tier': rec.get('tier') or rec.get('orig_tier'),
            'line': rec.get('line'),
            'sub': rec.get('sub'),
            'at': rec.get('at'),
            # `_oc_flipped` calls it `reason`, `_flipped_from` calls it
            # `basis`. Both are the measured justification for the flip.
            'basis': rec.get('basis') or rec.get('reason'),
            # Only flip_football_spreads computes this today. Absent means
            # UNKNOWN, not False — see is_receipt_frozen below.
            'receipt_frozen_on_original': rec.get(
                'receipt_frozen_on_original'),
            '_raw': rec,
        }
    return None


def was_flipped(pp: dict | None) -> bool:
    """True when any gate flipped this pick's side."""
    return flip_record(pp) is not None


def is_receipt_frozen(pp: dict | None) -> bool:
    """True when a published receipt is known to grade the ORIGINAL side.

    Returns False when the writer did not record it. That is deliberately
    the permissive direction for PROSE (we only add the stronger "do not
    claim a record" warning when we know it applies) and it must NOT be
    used as a grading input — `None` and `False` are different facts and a
    caller that needs to tell them apart should read
    `flip_record(pp)['receipt_frozen_on_original']` directly.
    """
    rec = flip_record(pp)
    return bool(rec and rec.get('receipt_frozen_on_original'))
