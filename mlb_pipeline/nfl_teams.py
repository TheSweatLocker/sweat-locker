#!/usr/bin/env python3
"""Canonical NFL team keys — the bridge between two ID spaces.

WHY (2026-10-04)
----------------
`jerry_reads.game_id` is the **Odds API** event hash (32 hex). The results
tables key on the nflverse form `20261004_NYJ_CHI`. There is no bridge table,
so the only join available is the team pair — and the two sides spell teams
differently:

    jerry_reads.input_snapshot.matchup   "New York Jets @ Chicago Bears"
    nfl_game_results.away_team/home_team "NYJ" / "CHI"

Every NFL game_read, POTD and card receipt therefore failed to grade:
`by_gid` missed (wrong ID space) and `by_match` missed (full name vs abbrev).
On 2026-10-04 that was 14 of 27 ungraded receipts with 9 games already final.

The abbreviations below are the ones the results tables actually store,
verified against 32 distinct values in `nfl_game_results` since 2026-09-01.
Note **LA**, not LAR, for the Rams — `generate_nfl_game_reads._short_team`
maps to 'LAR', which does not exist in the results tables.
"""
from __future__ import annotations
import re

# Full name → the abbreviation the results tables store.
NAME_TO_ABBR = {
    'arizona cardinals': 'ARI', 'atlanta falcons': 'ATL',
    'baltimore ravens': 'BAL', 'buffalo bills': 'BUF',
    'carolina panthers': 'CAR', 'chicago bears': 'CHI',
    'cincinnati bengals': 'CIN', 'cleveland browns': 'CLE',
    'dallas cowboys': 'DAL', 'denver broncos': 'DEN',
    'detroit lions': 'DET', 'green bay packers': 'GB',
    'houston texans': 'HOU', 'indianapolis colts': 'IND',
    'jacksonville jaguars': 'JAX', 'kansas city chiefs': 'KC',
    'las vegas raiders': 'LV', 'los angeles chargers': 'LAC',
    'los angeles rams': 'LA', 'miami dolphins': 'MIA',
    'minnesota vikings': 'MIN', 'new england patriots': 'NE',
    'new orleans saints': 'NO', 'new york giants': 'NYG',
    'new york jets': 'NYJ', 'philadelphia eagles': 'PHI',
    'pittsburgh steelers': 'PIT', 'san francisco 49ers': 'SF',
    'seattle seahawks': 'SEA', 'tampa bay buccaneers': 'TB',
    'tennessee titans': 'TEN', 'washington commanders': 'WAS',
}

# Alternate abbreviations seen in feeds, mapped onto the stored form.
ABBR_ALIAS = {
    'LAR': 'LA', 'RAMS': 'LA', 'STL': 'LA',
    'WSH': 'WAS', 'WFT': 'WAS', 'JAC': 'JAX',
    'KAN': 'KC', 'GNB': 'GB', 'SFO': 'SF', 'TAM': 'TB',
    'NWE': 'NE', 'NOR': 'NO', 'LVR': 'LV', 'OAK': 'LV',
    'SD': 'LAC', 'SDG': 'LAC', 'ARZ': 'ARI', 'BLT': 'BAL',
    'CLV': 'CLE', 'HST': 'HOU',
}

# Nickname-only forms ("jets", "bears"). All 32 NFL nicknames are distinct, so
# every team lands here — but a nickname shared by two clubs would be dropped
# rather than guessed, which is why this is built with a set and filtered.
_NICK: dict[str, set] = {}
for _full, _ab in NAME_TO_ABBR.items():
    _NICK.setdefault(_full.rsplit(' ', 1)[-1], set()).add(_ab)
NICK_TO_ABBR = {k: next(iter(v)) for k, v in _NICK.items() if len(v) == 1}

VALID = set(NAME_TO_ABBR.values())


def _clean(s: str) -> str:
    return re.sub(r'[^a-z0-9 ]', '', str(s or '').lower()).strip()


def canon(name: str) -> str | None:
    """Resolve any NFL team spelling to the abbreviation results tables store.

    Returns None rather than a guess when the input is unknown or ambiguous —
    a wrong team on a graded receipt is worse than an ungraded one.
    """
    if not name:
        return None
    raw = str(name).strip()
    up = re.sub(r'[^A-Z0-9]', '', raw.upper())
    if up in VALID:
        return up
    if up in ABBR_ALIAS:
        return ABBR_ALIAS[up]
    low = _clean(raw)
    if low in NAME_TO_ABBR:
        return NAME_TO_ABBR[low]
    # Trailing-word nickname ("the Bears", "Chicago Bears Defense")
    for word in reversed(low.split()):
        if word in NICK_TO_ABBR:
            return NICK_TO_ABBR[word]
    return None


def canon_matchup(matchup: str) -> tuple[str, str] | None:
    """'New York Jets @ Chicago Bears' → ('NYJ', 'CHI') as (away, home)."""
    if not matchup or '@' not in str(matchup):
        return None
    away, home = str(matchup).split('@', 1)
    a, h = canon(away), canon(home)
    return (a, h) if a and h else None


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    assert len(VALID) == 32, VALID
    for probe, want in (('New York Jets', 'NYJ'), ('LAR', 'LA'),
                        ('Los Angeles Rams', 'LA'), ('WSH', 'WAS'),
                        ('Bears', 'CHI'), ('JAC', 'JAX'), ('LA', 'LA'),
                        ('Not A Team', None), ('Jets', 'NYJ')):
        got = canon(probe)
        assert got == want, f'{probe!r} -> {got!r}, wanted {want!r}'
    assert canon_matchup('New York Jets @ Chicago Bears') == ('NYJ', 'CHI')
    assert canon_matchup('no at sign') is None
    print(f'nfl_teams OK · {len(VALID)} teams · {len(NICK_TO_ABBR)} nicknames')
