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
_AMBIG_CACHE: dict = {}
_LOG_CACHE: dict = {}


def _season_str(year: int) -> str:
    """2026 -> '20262027', the NHL's season id format."""
    return f'{year}{year + 1}'


def _norm(name: str) -> str:
    """Normalise a player name for cross-source matching.

    ══ 2026-10-03 · ACCENTS WERE 6 OF THE 7 MISSES ══
    This lowercased and dropped periods only, so every diacritic in the NHL
    was a miss. Measured against the 497 distinct players on the 10-03 prop
    board: 490 resolved, 7 did not, and folding accents plus the book's
    parenthetical disambiguator recovers 6 of those 7 —

        Aatu Raty           -> roster "Aatu Räty"
        Juraj Slafkovsky    -> roster "Juraj Slafkovský"
        Martin Fehervary    -> roster "Martin Fehérváry"
        Oskar Back          -> roster "Oskar Bäck"
        Noah Östlund        -> prop carries the accent, roster does not
        Elias Pettersson (2004)  -> the BOOK adds a birth year to separate
                                    two Petterssons; the roster does not

    The last one matters beyond the accent: stripping " (2004)" leaves
    "elias pettersson", and the roster has both an Elias and a Marcus
    Pettersson — so the EXACT index still resolves it correctly while the
    surname fallback below would have refused. Dropping the suffix recovers
    the player without weakening the no-guessing rule.

    Charles-Alexis Legault remains unresolved and should: he is on no NHL
    club roster, so NULL is the honest answer.

    Both build_name_index and resolve_player route through here, so index
    keys and lookups fold identically — there is no half-normalised state.
    """
    import unicodedata as _u
    s = str(name or '')
    # Drop a trailing parenthetical the book adds to disambiguate namesakes.
    if '(' in s:
        s = s.split('(')[0]
    s = _u.normalize('NFKD', s)
    s = ''.join(c for c in s if not _u.combining(c))
    s = s.lower().replace('.', '').replace('-', ' ').replace("'", '')
    return ' '.join(s.split())


def build_name_index(season_year: int) -> dict:
    """-> {normalised player name: (player_id, team_abbrev, position)}.

    One roster call per club, cached for the process. 32 requests once is
    far cheaper than a search call per player, and it is exact-match
    rather than a fuzzy search that can return the wrong athlete.

    ══ 2026-10-03 · A COLLIDING NAME WAS SILENTLY DROPPING A PLAYER ══
    This dict assignment overwrites, so when two players normalise to the
    same name one of them vanished and the other answered for both. Vancouver
    carries TWO Elias Petterssons -- 8480012 (C, born 1998) and 8483678
    (D, born 2004) -- so 766 roster entries produced a 765-key index, and
    resolve_player('Elias Pettersson') returned whichever club was walked
    last. A coin flip, invisible on read.

    It bit immediately: the books disambiguate with a birth year, writing
    "Elias Pettersson (2004)", and the accent-folding change above strips
    that suffix -- so the hint the book went out of its way to supply was
    being thrown away right before a 50/50 guess. That is the Kopylov
    failure shape (project_stat_integrity_audit_1002).

    Collisions now go to _AMBIG_CACHE keyed by the same normalised name,
    carrying birth years, and resolve_player uses the book's year hint to
    pick -- refusing when it cannot. The primary index keeps its original
    shape so existing callers (recent_form) are unaffected.
    """
    key = season_year
    if key in _ROSTER_CACHE:
        return _ROSTER_CACHE[key]
    idx: dict = {}
    cand: dict = {}
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
                nk = _norm(f'{fn} {ln}')
                pos = 'G' if group == 'goalies' else (p.get('positionCode') or '')
                byear = None
                bd = p.get('birthDate')
                if bd and len(str(bd)) >= 4 and str(bd)[:4].isdigit():
                    byear = int(str(bd)[:4])
                idx[nk] = (int(p['id']), team, pos)
                cand.setdefault(nk, []).append(
                    (int(p['id']), team, pos, byear))
    _ROSTER_CACHE[key] = idx
    _AMBIG_CACHE[key] = {k: v for k, v in cand.items() if len(v) > 1}
    if _AMBIG_CACHE[key]:
        for k, v in _AMBIG_CACHE[key].items():
            print(f'  ℹ roster name collision {k!r}: '
                  + ', '.join(f'{i} ({t} {po} b{by})' for i, t, po, by in v)
                  + ' — resolve_player needs a year hint to pick')
    return idx


def resolve_player(name: str, season_year: int) -> Optional[tuple]:
    """-> (player_id, team, position) or None.

    Order: ambiguous-name arbitration, exact match, unique-surname fallback.
    Returns None rather than guessing at any step.
    """
    idx = build_name_index(season_year)
    n = _norm(name)

    # Two players share this normalised name. Use the birth year the book
    # supplies ("Elias Pettersson (2004)") to pick, and refuse otherwise —
    # an arbitrary pick here attaches a prop to the wrong athlete.
    amb = _AMBIG_CACHE.get(season_year, {}).get(n)
    if amb:
        import re as _re
        yrs = [int(y) for y in _re.findall(r'(19\d{2}|20\d{2})', str(name or ''))]
        if yrs:
            hits = [c for c in amb if c[3] in yrs]
            if len(hits) == 1:
                return hits[0][:3]
        print(f'  ⚠ {name!r} is ambiguous across {len(amb)} rostered players '
              f'and no usable year hint — refusing to guess')
        return None

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
