"""The one definition of "is this game played in the weather?"

== 2026-11-12 . THE VALIDATED WEATHER LENS WAS GATED ON A SPELLING ========
ANDY asked for a weather read on WAS @ NYG and whether NFL weather is
broken. It is: two tables spell the same fact differently and the readers
only knew one of them.

    nfl_game_results.roof   'outdoors' 1298 . 'dome' 365 . 'closed' 236
                            . 'open' 35 . NULL 33        (nflverse vocabulary)
    nfl_game_context.roof   'outdoor' 80 . 'dome' 44 . NULL 192
                                                         (nfl_weather_pull:294)

`nfl_game_context.compute_cohort_tags` tested `roof in ('outdoors','open')`
to emit `nfl_outdoor_under` -- the tag carrying the one NFL weather lens
that has ever validated: wind >= 12 or temp <= 40 -> UNDER, 55.8%
walk-forward over 28 seasons, CI[52.9,58.6], 17 of 26 seasons, against a
49.3% control side. `'outdoor'` is in neither set, so:

    nfl_outdoor_under fired on 0 of 316 nfl_game_context rows, ever.
    20 rows qualify once the spelling is accepted -- including TWO in
    week 10: HOU @ CLE (wind 13, total 40.5) and BUF @ NYJ (wind 15,
    total 50.5).

`nfl_situational_lenses.roof_cond` had the same two sets and returned None
for 'outdoor', so 272 of 316 context rows were dropped from the lens
measurement rather than counted -- a 0-rows-read that looks like "no
signal" instead of a wiring error (feedback_zero_rows_is_a_wiring_error).

WHY THIS DOES NOT RENAME THE STORED VALUE. The obvious fix is to make
nfl_weather_pull write 'outdoors' and backfill the 80 rows. That breaks the
app: GameDetailV2.tsx:8396 reads
`String(roof).toLowerCase() === 'outdoor'` to decide whether to show the
cold and wind chips on the game screen, against nfl_game_context -- the
table holding the singular. Renaming the data to fix the pipeline would
dark the chip the data exists to feed. So the vocabulary stays and the
readers learn both, here, once (feedback_guard_belongs_at_the_chokepoint).

A RETRACTABLE ROOF THAT IS OPEN IS OUTDOORS. 'open' is a real value on 35
result rows and those rows carry wind; grouping on the physical condition
rather than the stadium type is both the right question and what keeps
'open' from being dropped by a sample floor.

NULL IS NOT OUTDOORS. 192 context rows and 33 result rows have no roof at
all. `is_outdoor(None)` is False and `roof_cond(None)` is None, so an
unknown venue never enters the outdoor side of a measurement by default --
`roof not in ('dome','closed')` was the old shorthand and it silently
counted every unknown as outdoors.
"""
from __future__ import annotations

#: Played in the weather. 'open' = retractable roof, open.
OUTDOOR = frozenset({'outdoors', 'outdoor', 'open'})

#: No wind, no rain, controlled temperature.
INDOOR = frozenset({'dome', 'closed', 'indoor', 'indoors',
                    'retractable-closed'})

#: What a writer should store for a new row. Kept as the nflverse spelling
#: because that is the larger corpus; the app's singular is still accepted.
CANONICAL_OUTDOOR = 'outdoors'


def _norm(roof) -> str:
    return str(roof or '').strip().lower()


def is_outdoor(roof) -> bool:
    """True only for a known open-air condition. NULL/unknown -> False."""
    return _norm(roof) in OUTDOOR


def is_indoor(roof) -> bool:
    """True only for a known enclosed condition. NULL/unknown -> False."""
    return _norm(roof) in INDOOR


def roof_cond(roof) -> str | None:
    """'outdoors' | 'indoors' | None for unknown.

    Returns the CONDITION, not the stadium type, so both vocabularies and
    both retractable states collapse onto the two buckets a weather
    measurement actually wants.
    """
    v = _norm(roof)
    if v in INDOOR:
        return 'indoors'
    if v in OUTDOOR:
        return 'outdoors'
    return None


def outdoor_under_qualifies(roof, temp, wind,
                            temp_max: float = 40, wind_min: float = 12) -> bool:
    """The validated lens: open air AND (cold OR windy) -> lean UNDER.

    Thresholds are the ones the 28-season walk-forward was run at. A game
    with no temp and no wind reading does NOT qualify -- an absent reading
    is not a calm day (feedback_any_null_finding_must_be_investigated).
    """
    if not is_outdoor(roof):
        return False
    cold = windy = False
    try:
        if temp is not None:
            cold = float(temp) <= temp_max
    except (TypeError, ValueError):
        pass
    try:
        if wind is not None:
            windy = float(wind) >= wind_min
    except (TypeError, ValueError):
        pass
    return cold or windy
