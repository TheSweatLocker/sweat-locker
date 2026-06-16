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
