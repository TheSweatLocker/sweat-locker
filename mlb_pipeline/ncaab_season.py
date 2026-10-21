#!/usr/bin/env python3
"""The current NCAAB season, derived from the date instead of hardcoded.

WHY
───
On 2026-10-21, five places pinned the NCAAB season to last year:

    ncaab_pipeline.py     --season default "2025-26"   WRITES ncaab_team_stats
    kenpom_pull.py        --season default 2026        writes kenpom_cache
    ncaab_pipeline.yml    --season 2026                overrides the above
    ncaab_game_context.py SEASON = "2025-26"           READS season=eq.<SEASON>
    ncaab_odds_pull.py    SEASON = '2025-26'

The 2026-27 season opens 2026-11-03. Left alone, every NCAAB card would have
launched on the 2025-26 FINAL ratings and stayed there all year, because
nothing in the chain derives the season — and `ncaab_team_stats` keys on
(team, season), so the reader asking for "2025-26" will happily keep finding
last year's rows forever. Worse, fixing only the WRITER would have been
actively worse than leaving it: the reader would then ask for 2025-26, find
the newly written 2026-27 rows under a different key, and return NOTHING.

Measured at the time: ncaab_team_stats held 365 rows of '2025-26' and 364 of
'2024-25', newest updated_at 2026-08-24 — two months stale, exactly as a
pinned season would look.

This is the same family as the NBA ratings that read two seasons old because
a `season` sort key was never fetched, and the Madden week collision. A
season boundary that lives in a constant is a bug with a date on it.

CONVENTION
──────────
KenPom names a season by the calendar year it ENDS: season 2027 is the
2026-27 campaign. College basketball runs November to April, so anything from
August onward belongs to the season ending NEXT year.

    season_end_year()  -> 2027   (int, what kenpom_pull --season wants)
    season_label()     -> '2026-27'  (str, what ncaab_team_stats.season holds)

August is the cutover rather than November on purpose: KenPom publishes
PRESEASON ratings well before opening night, and the pipeline needs to be
pulling the new season's numbers while it builds toward launch, not still
writing last season's.
"""
from __future__ import annotations

from datetime import date

# Month at which the new season's identity begins. August, so preseason
# ratings land under the upcoming season rather than the finished one.
_ROLLOVER_MONTH = 8


def season_end_year(today: date | None = None) -> int:
    """KenPom-style season year: the calendar year the season ENDS."""
    d = today or date.today()
    return d.year + 1 if d.month >= _ROLLOVER_MONTH else d.year


def season_label(today: date | None = None) -> str:
    """'2026-27' — the form stored in ncaab_team_stats.season."""
    y = season_end_year(today)
    return f'{y - 1}-{str(y)[2:]}'


def parse_label(label: str) -> int:
    """'2026-27' -> 2027. Accepts a bare year too, so a caller can pass
    either form without the caller having to know which."""
    s = str(label).strip()
    if '-' not in s:
        return int(s)
    head, tail = s.split('-', 1)
    base = int(head)
    # '2026-27' -> 2027; '2026-2027' -> 2027
    return base + 1 if len(tail) <= 2 else int(tail)


if __name__ == '__main__':
    import sys
    # Spot-check the boundary rather than asserting it in prose.
    cases = [
        (date(2026, 7, 31), 2026, '2025-26'),   # before rollover
        (date(2026, 8, 1),  2027, '2026-27'),   # rollover
        (date(2026, 10, 21), 2027, '2026-27'),  # today, preseason
        (date(2026, 11, 3), 2027, '2026-27'),   # opening night
        (date(2027, 3, 15), 2027, '2026-27'),   # March, same season
        (date(2027, 4, 6),  2027, '2026-27'),   # title game
        (date(2027, 8, 1),  2028, '2027-28'),   # next rollover
    ]
    bad = 0
    for d, want_y, want_l in cases:
        got_y, got_l = season_end_year(d), season_label(d)
        ok = (got_y == want_y and got_l == want_l)
        bad += 0 if ok else 1
        print(f'{"ok " if ok else "FAIL"} {d}  -> {got_y} {got_l}'
              + ('' if ok else f'   want {want_y} {want_l}'))
    for lbl, want in (('2026-27', 2027), ('2025-26', 2026), ('2027', 2027)):
        got = parse_label(lbl)
        ok = got == want
        bad += 0 if ok else 1
        print(f'{"ok " if ok else "FAIL"} parse {lbl!r} -> {got}'
              + ('' if ok else f'   want {want}'))
    print(f'\ntoday: season_end_year={season_end_year()} '
          f'season_label={season_label()!r}')
    sys.exit(1 if bad else 0)
