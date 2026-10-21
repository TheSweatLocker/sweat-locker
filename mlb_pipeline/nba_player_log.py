#!/usr/bin/env python3
"""NBA player game logs for prop L5/L10/season lookback.

Mirrors nhl_player_log.recent_form so backfill_prop_lookback can call either
the same way.

══ WHY THIS EXISTS ══
backfill_prop_lookback's NBA path went through
    site.api.espn.com/apis/site/v2/sports/basketball/nba/athletes
for ids and .../athletes/{id}/gamelog for logs. Both return **HTTP 404**
(tested 2026-10-21, same as the hockey endpoints did on 09-27). A 404 made
_espn_player_id return None silently, so every NBA player resolved to nothing
and the job printed "updated 0 props with lookback" while exiting 0. Measured:
player_l5_hit_count fed on 0 of 3,222 NBA props.

AND THE 404 WAS HIDING SOMETHING WORSE. The caller set

    labels_cache[pid] = events[0].get('_labels') or ['points','rebounds','assists']

No event carries '_labels', so labels always fell back to that 3-item list,
and _extract_stat resolves a stat by INDEXING that list against the stats
array. NBA's array is [MIN, FG, FG%, 3PT, ...], so 'points' would have read
index 0 = MINUTES, 'rebounds' index 1 = a "9-17" field-goal string. Had the
endpoint worked, the board would have filled with confident, wrong L5/L10
numbers instead of none. Parsing here is by NAME, never by assumed position.

══ WHAT WORKS, VERIFIED 2026-10-21 ══
  ids : sports.core.api.espn.com/v3/sports/basketball/nba/athletes
        ?limit=1000&active=true  -> 754 athletes, one call, full names + ids
  log : site.web.api.espn.com/apis/common/v3/sports/basketball/nba/
        athletes/{id}/gamelog  -> labels, names, events{id:{gameDate,...}},
        seasonTypes[].categories[].events[].stats

══ PRESEASON IS EXCLUDED, DELIBERATELY ══
The gamelog returns seasonTypes for BOTH '2026-27 Regular Season' and
'2026-27 Preseason'. On 2026-10-21, opening day, Wembanyama's log held 1
regular-season game and 4 preseason games. Lumping them gives an "L5" that is
80% preseason -- different rotations, different minutes, meaningless for a
prop line. A season label does not exclude preseason
(feedback_season_filter_does_not_exclude_preseason); the seasonType
displayName is what separates them, so that is what is matched.

══ WHAT THIS CANNOT DO ══
NBA's regular season opened 2026-10-21. Until roughly mid-November there are
not 5, let alone 10, regular-season games to look back on, so L5/L10 will be
thin or empty no matter how correct this module is. It returns FEWER rows
rather than padding with preseason, and the caller's compute_lookback already
drops a prop whose l10 is None. That is the honest behaviour: an empty
lookback gates a prop for missing data, a fabricated one prices a bet.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import requests

UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.espn.com/'}

_ATHLETES = ('https://sports.core.api.espn.com/v3/sports/basketball/nba/'
             'athletes')
_GAMELOG = ('https://site.web.api.espn.com/apis/common/v3/sports/basketball/'
            'nba/athletes/{pid}/gamelog')

# prop_type family (suffix already stripped) -> the semantic name ESPN uses in
# the gamelog's `names` array, or a composite marker handled below.
STAT_FIELD = {
    'pts': 'points', 'points': 'points',
    'reb': 'totalRebounds', 'rebounds': 'totalRebounds',
    'ast': 'assists', 'assists': 'assists',
    'stl': 'steals', 'steals': 'steals',
    'blk': 'blocks', 'blocks': 'blocks',
    'threes': 'threePointFieldGoalsMade',
    'threes_made': 'threePointFieldGoalsMade',
    'turnovers': 'turnovers', 'to': 'turnovers',
    'minutes': 'minutes', 'min': 'minutes',
    'pra': '_pra', 'pr': '_pr', 'pa': '_pa', 'ra': '_ra',
}

_NAME_INDEX: dict = {}
_LOG_CACHE: dict = {}


def _norm(name: str) -> str:
    s = (name or '').lower().strip()
    s = s.replace('.', '').replace("'", '').replace('-', ' ')
    for suf in (' jr', ' sr', ' ii', ' iii', ' iv', ' v'):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return ' '.join(s.split())


def build_name_index() -> dict:
    """{normalised full name: espn id}. One call, cached for the process.

    Built from the roster listing rather than a fuzzy search, for the reason
    nhl_player_log documents: a search endpoint happily returns the WRONG
    athlete, and a wrong athlete produces a plausible series for the wrong
    player, which is worse than no series.
    """
    if _NAME_INDEX:
        return _NAME_INDEX
    try:
        r = requests.get(_ATHLETES, headers=UA,
                         params={'limit': 1000, 'active': 'true'}, timeout=40)
        if r.status_code != 200:
            return _NAME_INDEX
        items = (r.json() or {}).get('items') or []
    except Exception:
        return _NAME_INDEX
    dupes = 0
    for it in items:
        pid = it.get('id')
        full = it.get('fullName') or (
            f"{it.get('firstName', '')} {it.get('lastName', '')}".strip())
        if not (pid and full):
            continue
        key = _norm(full)
        if key in _NAME_INDEX and _NAME_INDEX[key] != pid:
            # Two active players normalise to the same name. Refuse rather
            # than pick: a coin-flip here attributes one player's form to
            # another. Mark it so resolve_player returns None.
            _NAME_INDEX[key] = None
            dupes += 1
            continue
        _NAME_INDEX[key] = pid
    if dupes:
        print(f'  [nba_player_log] {dupes} ambiguous name(s) left '
              f'unresolvable rather than guessed')
    return _NAME_INDEX


def resolve_player(name: str) -> Optional[str]:
    idx = build_name_index()
    return idx.get(_norm(name))


def _num(raw) -> Optional[float]:
    """ESPN gives '34', '7.7', or made-attempted like '0-4'. For a
    made-attempted field only the MADE half is the prop stat."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ('', '-', '--'):
        return None
    if '-' in s and not s.startswith('-'):
        s = s.split('-', 1)[0]
    try:
        return float(s)
    except ValueError:
        return None


def _fetch_log(pid: str) -> Optional[dict]:
    if pid in _LOG_CACHE:
        return _LOG_CACHE[pid]
    try:
        r = requests.get(_GAMELOG.format(pid=pid), headers=UA, timeout=30)
        data = r.json() if r.status_code == 200 else None
    except Exception:
        data = None
    _LOG_CACHE[pid] = data
    return data


def recent_form(player_name: str, prop_type: str, season_year: int = 0,
                n: int = 10,
                before_date: Optional[str] = None) -> Optional[dict]:
    """-> {rows:[{value, opp, home, date}], season_used, field} or None.

    `rows` is most-recent-first, matching nhl_player_log.

    before_date (YYYY-MM-DD) is the LEAK GUARD and keeps only games played
    strictly before it. Without it the window includes the game being
    predicted, so player_l5_hit_count partly encodes its own outcome -- the
    MLB prop L5 leak (project_prop_l5_leak_922), which there moved PRIME from
    -9.3% to +21.6% ROI. A row with no usable date is DROPPED when the guard
    is set: an undated game cannot be proven to precede the one being
    predicted, and keeping it is how the guard leaks anyway.

    The slice comes after the filters, so the window is n USABLE games rather
    than whatever survives n raw ones.
    """
    base = (prop_type or '').lower()
    for suf in ('_over', '_under'):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    field = STAT_FIELD.get(base)
    if not field:
        return None

    pid = resolve_player(player_name)
    if not pid:
        return None
    data = _fetch_log(pid)
    if not data:
        return None

    names = [str(x) for x in (data.get('names') or [])]
    events_meta = data.get('events') or {}

    def _idx(semantic: str) -> Optional[int]:
        """Index of a semantic field in the stats array, BY NAME.

        ESPN writes combined fields as 'threePointFieldGoalsMade-
        threePointFieldGoalsAttempted', so an exact match misses the one we
        want; a prefix match on the made half is correct and still unique.
        """
        if semantic in names:
            return names.index(semantic)
        for i, nm in enumerate(names):
            if nm.split('-', 1)[0] == semantic:
                return i
        return None

    def _val(stats: list, semantic: str) -> Optional[float]:
        i = _idx(semantic)
        if i is None or i >= len(stats):
            return None
        return _num(stats[i])

    COMPOSITE = {'_pra': ('points', 'totalRebounds', 'assists'),
                 '_pr': ('points', 'totalRebounds'),
                 '_pa': ('points', 'assists'),
                 '_ra': ('totalRebounds', 'assists')}

    # Rows are collected PER SEASON, never pooled across seasons.
    #
    # The gamelog can carry several seasonTypes at once -- on 2026-10-21
    # Wembanyama's held only '2026-27 Regular Season' while Jokic's also held
    # '2025-26 Regular Season'. Matching every label containing "regular
    # season" and appending into one list silently mixes October 2026 games
    # with March 2026 ones and calls the result "L5". Prior-season fallback is
    # legitimate -- nhl_player_log does exactly that when the current season
    # is empty -- but only if it is one season at a time and the caller is
    # TOLD which, so old form is never captioned as current.
    per_season: dict = {}
    for st in (data.get('seasonTypes') or []):
        label = str(st.get('displayName') or '')
        # Regular season ONLY. See the preseason note in the module docstring.
        if 'regular season' not in label.lower():
            continue
        rows = per_season.setdefault(label, [])
        for cat in (st.get('categories') or []):
            for ev in (cat.get('events') or []):
                stats = ev.get('stats') or []
                if field in COMPOSITE:
                    parts = [_val(stats, p) for p in COMPOSITE[field]]
                    value = None if any(p is None for p in parts) else sum(
                        parts)
                else:
                    value = _val(stats, field)
                if value is None:
                    continue
                meta = events_meta.get(str(ev.get('eventId'))) or {}
                gdate = str(meta.get('gameDate') or '')[:10]
                if before_date:
                    if not re.match(r'^\d{4}-\d{2}-\d{2}$', gdate):
                        continue
                    if gdate >= before_date:
                        continue
                rows.append({
                    'value': value,
                    'date': gdate or None,
                    'opp': ((meta.get('opponent') or {}) or {}).get(
                        'abbreviation'),
                    'home': (meta.get('atVs') == 'vs'),
                })

    # Newest season that actually has games wins; no pooling. Labels sort
    # correctly as strings because they are '2026-27 Regular Season' style.
    for label in sorted(per_season, reverse=True):
        rows = per_season[label]
        if not rows:
            continue
        # Most-recent-first, then slice. Dateless rows sort last so a usable,
        # dated game is never dropped for one we cannot place in time.
        rows.sort(key=lambda r: (r['date'] or ''), reverse=True)
        rows = rows[:n]
        if rows:
            return {'rows': rows, 'season_used': label, 'field': field}
    return None


if __name__ == '__main__':
    import sys
    nm = sys.argv[1] if len(sys.argv) > 1 else 'Victor Wembanyama'
    pt = sys.argv[2] if len(sys.argv) > 2 else 'pts_over'
    bd = sys.argv[3] if len(sys.argv) > 3 else None
    print(f'index size: {len(build_name_index())}')
    print(f'{nm} -> id {resolve_player(nm)}')
    out = recent_form(nm, pt, n=10, before_date=bd)
    if not out:
        print('no rows')
    else:
        print(f"field={out['field']}")
        for r in out['rows']:
            print(f"   {r['date']}  {r['value']:>6}  "
                  f"{'vs' if r['home'] else '@'} {r['opp']}")
