"""Season calendar — single source of truth for sport season windows.

Pipeline scripts call ``is_in_season(sport)`` to self-no-op during offseason
months so cron jobs stay quiet without YAML edits or manual disabling.

Two-layer gate pattern used by NBA scripts (transferable to NFL/NHL/etc):

    if not is_in_season("NBA"):
        print("⊘ NBA offseason per season_calendar — skipping")
        return
    if not os.environ.get("BDL_API_KEY"):
        print("⊘ BDL_API_KEY not set — skipping (offseason or sub cancelled)")
        return

Layer 1 (this module): rough date window — catches routine offseason.
Layer 2 (env var check): hard switch the user controls via GitHub Secrets
when subs are cancelled mid-cycle.

Windows are intentionally wide (Finals/playoffs can run long); the env var
layer handles tighter cutoffs when the user explicitly disables a sport.
"""
from datetime import date as _date


SEASON_WINDOWS = {
    "NBA":   {"start": (10, 1),  "end": (6, 25)},
    "NFL":   {"start": (8, 1),   "end": (2, 15)},
    "MLB":   {"start": (2, 20),  "end": (11, 5)},
    "NHL":   {"start": (9, 15),  "end": (6, 25)},
    "NCAAB": {"start": (11, 1),  "end": (4, 10)},
    "NCAAF": {"start": (8, 25),  "end": (1, 15)},
}


# ════════════════════════════════════════════════════════════════════════
# PLAY WEEKS — how far ahead content must be generated
#
# WHY THIS LIVES HERE
# -------------------
# The app does NOT show a rolling date range for weekly sports. It shows two
# season-week NUMBERS: the current play-week on the "today" tab and
# current+1 on the "tomorrow" tab (app/index.tsx `_seasonWeekOf` /
# `_currentSeasonWeek`, ~line 2966). So the set of games a user can see is
# bounded by a week boundary, not by "N days from now".
#
# Every generator that fills those cards used its own hand-tuned day count
# instead. Measured 2026-09-29, the four windows that all had to agree:
#
#     app/index.tsx                  current week + next week  (up to +13d)
#     generate_ncaaf_game_reads      now + 10 days
#     generate_nfl_game_reads        now + 10 days  (odds cutoff)
#     generate_nfl_game_reads        now + 12 days  (fetch_nfl_contexts)
#
# Consequence, simulated day by day: on any WEDNESDAY the NFL "tomorrow" tab
# renders next week's whole Sunday slate — 14 games — past the +10 horizon,
# so every one of those cards had no Jerry read. Thursday leaked one more
# (the following MNF). It self-healed by Friday, which is why it survived:
# the gap was widest on the day nobody checked and gone by the weekend.
# NCAAF was unaffected only by luck — its play-week runs Wed→Tue and its
# games cluster Thu-Sat, so next week's Saturday always landed inside +10.
#
# The fix is not a bigger constant — a constant cannot know where the week
# boundary is, and the next person to tune one reopens the gap. `read_horizon`
# returns the LAST DATE the app can display, derived from the same anchors the
# client uses. Generators ask for that instead of guessing.
#
# Anchors are Andy authority and deliberately sit on the dead day BETWEEN fan
# weeks, so floor(days/7) rolls on the right day with no special-case clause.
# They must stay identical to _seasonWeekAnchors in app/index.tsx and to
# current_nfl_season_week() from migration 20260913f. Three copies is two too
# many; this module is the one Python callers should use.
# ════════════════════════════════════════════════════════════════════════

PLAY_WEEK_ANCHORS = {
    "NFL":   _date(2026, 9, 9),    # Tue dead-day; Week 1 = 9/9-9/15
    "NCAAF": _date(2026, 8, 27),   # Wed dead-day; Week 1 play-week start
}

# Cap so a date far past the season cannot produce week 400.
MAX_PLAY_WEEK = {"NFL": 22, "NCAAF": 20}

# Floor on read_horizon, in days. Purely non-regression insurance: the old
# hand-tuned constants were 10 and 12, so never generate LESS than they did
# even if an anchor is edited badly. Cheap to honour — a football read is
# ~$0.001 of Haiku.
MIN_HORIZON_DAYS = 12


def season_week_of(sport, on):
    """Season week number containing date `on`. None if sport has no anchor.

    0 means "before week 1". Mirrors app/index.tsx `_seasonWeekOf`.
    """
    anchor = PLAY_WEEK_ANCHORS.get(str(sport).upper())
    if not anchor:
        return None
    delta = (on - anchor).days
    if delta < 0:
        return 0
    return min(MAX_PLAY_WEEK.get(str(sport).upper(), 22), delta // 7 + 1)


def current_season_week(sport, on=None):
    """Season week the app is currently treating as 'this week'."""
    if on is None:
        on = _date.today()
    return season_week_of(sport, on)


def read_horizon(sport, on=None):
    """Last game_date the app can display for `sport`, as a date.

    That is the final day of the NEXT play-week, because the "tomorrow" tab
    shows current_week + 1 in full. Generators should fetch through this date
    — anything past it is invisible to users; anything short of it renders a
    card with no read.

    Returns None for sports with no play-week anchor (daily sports), so the
    caller keeps its own logic.
    """
    key = str(sport).upper()
    anchor = PLAY_WEEK_ANCHORS.get(key)
    if not anchor:
        return None
    if on is None:
        on = _date.today()
    from datetime import timedelta as _td
    wk = season_week_of(sport, on) or 1        # treat pre-season as week 1
    # Start of the current week, then 13 days covers this week + all of next.
    wk_start = anchor + _td(days=7 * (wk - 1))
    horizon = wk_start + _td(days=13)
    floor = on + _td(days=MIN_HORIZON_DAYS)
    return max(horizon, floor)


def is_in_season(sport, on=None):
    """Return True if `sport` is in its active season window on `on`.

    Defaults to today's date. Unknown sports return True (don't gate).
    Windows that cross the calendar year (e.g. NBA Oct→Jun) are handled.
    """
    win = SEASON_WINDOWS.get(sport.upper())
    if not win:
        return True
    if on is None:
        on = _date.today()
    s_m, s_d = win["start"]
    e_m, e_d = win["end"]
    md = (on.month, on.day)
    if (s_m, s_d) <= (e_m, e_d):
        return (s_m, s_d) <= md <= (e_m, e_d)
    return md >= (s_m, s_d) or md <= (e_m, e_d)


def season_status(sport, on=None):
    """Human-readable status string for logging."""
    if is_in_season(sport, on=on):
        return f"{sport.upper()} in-season"
    win = SEASON_WINDOWS.get(sport.upper())
    if not win:
        return f"{sport.upper()} unknown sport"
    return f"{sport.upper()} offseason (active {win['start'][0]:02d}/{win['start'][1]:02d}-{win['end'][0]:02d}/{win['end'][1]:02d})"


if __name__ == "__main__":
    print(f"Season status - {_date.today()}")
    for sport in SEASON_WINDOWS:
        marker = "IN" if is_in_season(sport) else "OFF"
        print(f"  {season_status(sport):<60s} [{marker}]")
