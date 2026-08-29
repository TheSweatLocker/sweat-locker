"""Sport-universal in-season gate.

Every sport-specific script should call `is_sport_in_season(sport)` at
top-level and exit 0 immediately if False. This kills off-season pipeline
waste (API calls, DB reads, Claude tokens) before it happens rather than
relying on empty rowsets to no-op downstream.

Windows are conservative — they include preseason/playoff buffer so we
don't miss real games. When in doubt, favor running.

Usage:
    from season_gate import is_sport_in_season
    if not is_sport_in_season('NFL'):
        print('NFL off-season — skipping'); sys.exit(0)

Override for on-demand backfills:
    if not is_sport_in_season('NFL') and '--force-offseason' not in sys.argv:
        ...
"""
from datetime import datetime, timezone, timedelta


def _today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


# Sport → (start_month, end_month) inclusive. Wraps year-end when end < start.
# Preseason inclusion: NFL starts month=8 to include August preseason IF the user
# has opted in via UI or the NFL workflow's own gates. Everything else is
# regular-season + playoffs + a small buffer.
SEASON_WINDOWS = {
    'MLB':   (3, 11),    # Mar spring training - Oct/early-Nov World Series
    'NFL':   (8, 2),     # Late-Aug lead-up to Wk1 through early-Feb Super Bowl. 2026-08-28: was (9,2), pulled forward to Aug so Wk1 slate loads before kickoff.
    'NCAAF': (8, 1),     # Late Aug Week 0 - early Jan CFP title
    'NBA':   (10, 6),    # Oct preseason - mid-June Finals
    'NHL':   (10, 6),    # Oct preseason - mid-June Cup
    'NCAAB': (11, 4),    # Early Nov - early April Final Four
    'UFC':   None,       # Year-round weekly cards; never off-season
}

# Preseason months per sport (opt-in territory — most sports we skip preseason
# entirely because betting edge is near-zero on rosters that don't play).
PRESEASON_MONTHS = {
    'NFL':   {8},        # August preseason weeks
    'NBA':   {10},       # Early Oct preseason
    'NHL':   {9},        # Late Sept preseason
}


def is_sport_in_season(sport: str, date=None, include_preseason: bool = False) -> bool:
    """Return True if the sport is currently in season.

    - `date`: datetime.date or None (uses today ET)
    - `include_preseason`: if True, includes the preseason months for NFL/NBA/NHL.
      Default False — most callers should not process preseason data.
    """
    sport = (sport or '').upper()
    window = SEASON_WINDOWS.get(sport)
    if window is None:
        # Year-round sports (UFC) — always in season
        return True

    d = date or _today_et()
    month = d.month
    start, end = window

    # Regular window check (handles wrap-around like Sept→Feb)
    if start <= end:
        in_window = start <= month <= end
    else:
        in_window = month >= start or month <= end

    if in_window:
        return True

    # Preseason opt-in
    if include_preseason and month in PRESEASON_MONTHS.get(sport, set()):
        return True

    return False


def season_gate_or_exit(sport: str, allow_flag: str = '--force-offseason') -> None:
    """Convenience: call at script top. Exits 0 with a log line if off-season.
    User can bypass with the flag (default `--force-offseason`).
    """
    import sys
    if allow_flag in sys.argv:
        return
    if not is_sport_in_season(sport):
        print(f'{sport} off-season — skipping (pass {allow_flag} to override)')
        sys.exit(0)
