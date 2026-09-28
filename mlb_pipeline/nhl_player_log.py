"""NHL per-player game logs from the NHL's own API.

Andy 2026-09-27: "all prop jerry nhl need to have the graphs depicting
L10 meeting prop line ... analyze based on prior performance L5 L10 and
opponent stats."

── WHY THIS EXISTS: THE ESPN PATH IS DEAD ──
backfill_prop_lookback.backfill_nba_nhl already claims to cover NHL, via
_espn_player_id hitting

    site.api.espn.com/apis/site/v2/sports/hockey/nhl/athletes?search=...

Tested 2026-09-27: that endpoint returns **HTTP 404**, and ESPN's
common/v3 search returns count 0 for Connor McDavid, Auston Matthews and
Nathan MacKinnon. So every NHL player id resolved to None and the L10
lookback silently produced nothing — the graphs would have stayed empty
even once books posted props, with no error anywhere.

── THE REPLACEMENT IS FIRST-PARTY ──
api-web.nhle.com is the NHL's own API, already used in this codebase for
goalie stats, and it carries exactly the fields the prop graph needs:

    roster/{TEAM}/{season}        -> player ids per club
    player/{id}/game-log/{season}/{type}
        gameDate, opponentAbbrev, homeRoadFlag,
        shots, goals, assists, points, toi, ...

homeRoadFlag is the 'H'/'R' the chart's `home` field wants, which NFL
still cannot supply (its note says "nfl_player_stats has no home/away
column — deferred"). NHL gets it for free.

── SEASON HANDLING MIRRORS THE TEAM RATINGS ──
Same problem, same answer: on opening night the current season has no
games, so L10 must come from last season. The season falls back when the
current one returns an empty log, and the caller is told which season the
rows came from so a graph is never captioned as current when it is not.

── WHAT THIS CANNOT DO, STATED PLAINLY ──
The skater game log has no BLOCKS and no HITS. _NHL_STAT_KEY in
backfill_prop_lookback maps both, and there is no field to map them to
here. Those prop types return NO rows rather than a fabricated series —
an empty graph is honest, a wrong one is not.
"""
from __future__ import annotations

from typing import Optional

import requests

API = 'https://api-web.nhle.com/v1'
UA = {'User-Agent': 'Mozilla/5.0 (compatible; SweatLocker/1.0)'}

# 32 club codes as the NHL API spells them.
TEAMS = ('ANA', 'BOS', 'BUF', 'CAR', 'CBJ', 'CGY', 'CHI', 'COL', 'DAL',
         'DET', 'EDM', 'FLA', 'LAK', 'MIN', 'MTL', 'NJD', 'NSH', 'NYI',
         'NYR', 'OTT', 'PHI', 'PIT', 'SEA', 'SJS', 'STL', 'TBL', 'TOR',
         'UTA', 'VAN', 'VGK', 'WPG', 'WSH')

# prop_type fragment -> game-log field. Deliberately omits blocks and hits:
# the skater log does not carry them (see module note).
STAT_FIELD = {
    'sog': 'shots', 'shots': 'shots', 'shots_on_goal': 'shots',
    'points': 'points', 'goals': 'goals', 'assists': 'assists',
    'saves': 'saves', 'total_saves': 'saves',
    'goals_against': 'goalsAgainst',
}

_ROSTER_CACHE: dict = {}
_LOG_CACHE: dict = {}


def _season_str(year: int) -> str:
    """2026 -> '20262027', the NHL's season id format."""
    return f'{year}{year + 1}'


def _norm(name: str) -> str:
    return ' '.join(str(name or '').lower().replace('.', '').split())


def build_name_index(season_year: int) -> dict:
    """-> {normalised player name: (player_id, team_abbrev, position)}.

    One roster call per club, cached for the process. 32 requests once is
    far cheaper than a search call per player, and it is exact-match
    rather than a fuzzy search that can return the wrong athlete.
    """
    key = season_year
    if key in _ROSTER_CACHE:
        return _ROSTER_CACHE[key]
    idx: dict = {}
    ss = _season_str(season_year)
    for team in TEAMS:
        try:
            r = requests.get(f'{API}/roster/{team}/{ss}', headers=UA, timeout=20)
            if r.status_code != 200:
                continue
            j = r.json() or {}
        except Exception:
            continue
        for group in ('forwards', 'defensemen', 'goalies'):
            for p in (j.get(group) or []):
                fn = (p.get('firstName') or {})
                ln = (p.get('lastName') or {})
                fn = fn.get('default') if isinstance(fn, dict) else fn
                ln = ln.get('default') if isinstance(ln, dict) else ln
                if not fn or not ln or not p.get('id'):
                    continue
                idx[_norm(f'{fn} {ln}')] = (
                    int(p['id']), team,
                    'G' if group == 'goalies' else p.get('positionCode') or '')
    _ROSTER_CACHE[key] = idx
    return idx


def resolve_player(name: str, season_year: int) -> Optional[tuple]:
    """-> (player_id, team, position) or None. Exact, then surname fallback."""
    idx = build_name_index(season_year)
    n = _norm(name)
    if n in idx:
        return idx[n]
    # Books sometimes shorten or punctuate differently ("Alex" vs
    # "Alexander", "T.J." vs "TJ"). Fall back to a unique surname match —
    # and ONLY a unique one, because guessing between two players with the
    # same surname is how a prop gets attached to the wrong athlete.
    parts = n.split()
    if len(parts) >= 2:
        surname = parts[-1]
        hits = [v for k, v in idx.items() if k.split()[-1] == surname]
        if len(hits) == 1:
            return hits[0]
    return None


def game_log(player_id: int, season_year: int, game_type: int = 2) -> list:
    """Raw game-log rows, most recent first. game_type 2 = regular season."""
    key = (player_id, season_year, game_type)
    if key in _LOG_CACHE:
        return _LOG_CACHE[key]
    try:
        r = requests.get(
            f'{API}/player/{player_id}/game-log/{_season_str(season_year)}/{game_type}',
            headers=UA, timeout=20)
        rows = (r.json() or {}).get('gameLog') or [] if r.status_code == 200 else []
    except Exception:
        rows = []
    _LOG_CACHE[key] = rows
    return rows


def recent_form(player_name: str, prop_type: str, season_year: int,
                n: int = 10) -> Optional[dict]:
    """-> {rows:[{value, opp, home, date}], season_used, field} or None.

    `rows` is most-recent-first; the chart reverses it. Falls back to the
    prior season when the current one has no games yet, and reports which
    season was used so the caller never captions old data as current.
    """
    field = None
    pt = _norm(prop_type).replace(' ', '_')
    for frag, f in STAT_FIELD.items():
        if frag in pt:
            field = f
            break
    if field is None:
        return None          # blocks / hits / anything the log lacks

    for year in (season_year, season_year - 1):
        who = resolve_player(player_name, year)
        if not who:
            continue
        pid = who[0]
        log = game_log(pid, year)
        if not log:
            continue
        rows = []
        for g in log[:n]:
            # SAVES IS DERIVED. The goalie game log carries shotsAgainst,
            # goalsAgainst and savePctg but no `saves` field, so the most
            # commonly priced goalie market has to be computed rather than
            # read. saves = shots faced - goals allowed.
            if field == 'saves':
                sa, ga = g.get('shotsAgainst'), g.get('goalsAgainst')
                v = (int(sa) - int(ga)) if sa is not None and ga is not None else None
            else:
                v = g.get(field)
            if v is None:
                continue
            rows.append({
                'value': v,
                'opp': g.get('opponentAbbrev'),
                # The chart wants a bool: True = played at home.
                'home': (str(g.get('homeRoadFlag') or '').upper() == 'H'),
                'date': g.get('gameDate'),
            })
        if rows:
            return {'rows': rows, 'season_used': year, 'field': field,
                    'player_id': pid, 'team': who[1], 'position': who[2]}
    return None


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    yr = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    print(f'=== building name index for {_season_str(yr)} ===')
    idx = build_name_index(yr)
    print(f'  {len(idx)} players across {len(TEAMS)} clubs')
    if not idx:
        print('  (empty — current season rosters may not be published yet)')
        idx = build_name_index(yr - 1)
        print(f'  {yr-1}: {len(idx)} players')
    print()
    for who, pt in (('Connor McDavid', 'shots_on_goal'),
                    ('Auston Matthews', 'goals'),
                    ('Nathan MacKinnon', 'points'),
                    ('Cale Makar', 'assists'),
                    ('Sidney Crosby', 'blocks')):
        rf = recent_form(who, pt, yr, n=10)
        if not rf:
            print(f'  {who:20s} {pt:16s} -> no data '
                  f'({"stat not in NHL game log" if pt in ("blocks","hits") else "unresolved"})')
            continue
        vals = [r['value'] for r in rf['rows']]
        opps = [('@' if not r['home'] else '') + str(r['opp']) for r in rf['rows']]
        print(f'  {who:20s} {pt:16s} season {rf["season_used"]} '
              f'({rf["team"]}, {rf["position"]})')
        print(f'      L{len(vals)}: {vals}')
        print(f'      opp   : {opps}')
