"""ESPN box-score-based NFL prop resolver — grades within minutes of game end.

Complements resolve_nfl_props.py which uses nflverse weekly data (only
publishes Tue AM after games → 5-6 day wait for TNF props). This grader
hits ESPN's summary API immediately post-game.

Usage:
    python resolve_nfl_props_espn.py --date 2026-09-10    # grade one day
    python resolve_nfl_props_espn.py --game_id abc123     # one game
    python resolve_nfl_props_espn.py --lookback 3         # last N days

Chain: run this in NFL workflow immediately after games complete, then
       nflverse fallback runs Tue for anything still ungraded.
"""
import argparse, os, sys, functools
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_READ  = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# ESPN team abbreviation → common team-name variants for matching.
# Only used when props table has full team name and ESPN returns abbrev.
_ESPN_TEAM_ABBR = {
    'NE':'New England Patriots','SEA':'Seattle Seahawks','KC':'Kansas City Chiefs',
    'BUF':'Buffalo Bills','MIA':'Miami Dolphins','NYJ':'New York Jets',
    'BAL':'Baltimore Ravens','CIN':'Cincinnati Bengals','CLE':'Cleveland Browns',
    'PIT':'Pittsburgh Steelers','HOU':'Houston Texans','IND':'Indianapolis Colts',
    'JAX':'Jacksonville Jaguars','TEN':'Tennessee Titans','DEN':'Denver Broncos',
    'LAC':'Los Angeles Chargers','LV':'Las Vegas Raiders',
    'DAL':'Dallas Cowboys','NYG':'New York Giants','PHI':'Philadelphia Eagles',
    'WAS':'Washington Commanders','CHI':'Chicago Bears','DET':'Detroit Lions',
    'GB':'Green Bay Packers','MIN':'Minnesota Vikings','ATL':'Atlanta Falcons',
    'CAR':'Carolina Panthers','NO':'New Orleans Saints','TB':'Tampa Bay Buccaneers',
    'ARI':'Arizona Cardinals','LA':'Los Angeles Rams','LAR':'Los Angeles Rams',
    'SF':'San Francisco 49ers',
}


# Prop type → (ESPN stat category, stat label) — used to pull the right
# number from ESPN's boxscore.players array.
# ESPN boxscore structure:
# players[team_idx].statistics[category_idx].athletes[athlete_idx].stats[stat_col_idx]
# Categories: 'passing','rushing','receiving','defensive','fumbles'
# Stat columns per category from ESPN 'labels' array.
# (category, ESPN label, side) — `side` picks a half out of a combined
# "a/b" cell, None for plain numeric cells.
#
# 2026-09-18 BUG FIX. pass_attempts mapped to 'ATT' and pass_completions
# to 'CMP', but ESPN's passing labels are:
#     ['C/ATT','YDS','AVG','TD','INT','SACKS','QBR','RTG']
# Neither 'ATT' nor 'CMP' exists there, so _parse_athlete_stat's
# `if label not in labels: return None` bailed every time and the caller
# stored 0.0. The split-on-'/' logic below it was unreachable.
#
# Effect: 19% of graded NFL props carried final_value=0.0 and were graded
# against that zero. Verified wrong, all STRONG tier:
#   Caleb Williams pass_completions O20.5 -> Loss, actually 21/29 = WIN
#   Aaron Rodgers  pass_completions O20.5 -> Loss, actually 24/40 = WIN
#   C.J. Stroud    pass_completions U19.5 -> Win,  actually 26/38 = LOSS
# Both directions were corrupted, so the NFL prop record was wrong both ways.
PROP_TO_ESPN = {
    'pass_yds':       ('passing',   'YDS',   None),
    'pass_tds':       ('passing',   'TD',    None),
    'pass_attempts':  ('passing',   'C/ATT', 'right'),
    'pass_completions': ('passing', 'C/ATT', 'left'),
    'ints':           ('passing',   'INT',   None),
    'pass_interceptions': ('passing', 'INT', None),
    'rush_yds':       ('rushing',   'YDS',   None),
    'rush_attempts':  ('rushing',   'CAR',   None),
    'rush_tds':       ('rushing',   'TD',    None),
    'reception_yds':  ('receiving', 'YDS',   None),
    'rec_yds':        ('receiving', 'YDS',   None),
    'receptions':     ('receiving', 'REC',   None),
    'reception_tds':  ('receiving', 'TD',    None),
    'rec_tds':        ('receiving', 'TD',    None),
    # anytime_td = sum of rushing_tds + receiving_tds (handled specially)
}


_ALIASES: dict[str, set] = {}


def _load_aliases() -> None:
    """Build code -> {every known spelling} from nfl_team_aliases.

    Read once per run. Failure is non-fatal: without it the grader
    behaves exactly as it did before, which is worse but not broken.
    """
    global _ALIASES
    if _ALIASES:
        return
    try:
        r = requests.get(f'{SB}/rest/v1/nfl_team_aliases', headers=H_READ,
                         timeout=30,
                         params={'select': 'canonical_name,full_name,city,'
                                           'alt_names,espn_name'})
        if r.status_code != 200:
            print(f'  ⚠ alias load failed {r.status_code} — '
                  f'falling back to exact abbreviation match only')
            return
        for a in r.json():
            canon = a.get('canonical_name')
            if not canon:
                continue
            names = {canon, a.get('full_name'), a.get('city'),
                     a.get('espn_name')}
            names |= set(a.get('alt_names') or [])
            clean = {str(n).strip() for n in names if n}
            for n in clean:
                _ALIASES.setdefault(n.upper(), set()).update(clean)
            _ALIASES.setdefault(canon.upper(), set()).update(clean)
    except Exception as e:
        print(f'  ⚠ alias load error {type(e).__name__} — exact match only')


def _alias_variants(code: str) -> set:
    """Every spelling of this team, including the one we were handed."""
    if not code:
        return set()
    return _ALIASES.get(str(code).upper(), set()) | {code}


def _espn_events_on_date(date_str: str) -> list:
    """Return ESPN event list for one date (YYYY-MM-DD)."""
    ymd = date_str.replace('-', '')
    r = requests.get(f'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={ymd}',
                     timeout=15)
    if r.status_code != 200: return []
    return r.json().get('events', [])


def _espn_boxscore(event_id: str) -> dict | None:
    """Pull full ESPN summary + boxscore for one event."""
    r = requests.get(f'https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}',
                     timeout=15)
    if r.status_code != 200: return None
    return r.json()


def _parse_athlete_stat(stats_row: list, label: str, labels: list,
                        side: str | None = None) -> float | None:
    """Extract one stat from an athlete's stats list.

    `side` splits a combined cell: ESPN reports passing completions and
    attempts together as a single 'C/ATT' column ('21/29'), so
    pass_completions asks for 'left' and pass_attempts for 'right'.
    """
    if label not in labels: return None
    idx = labels.index(label)
    if idx >= len(stats_row): return None
    val = stats_row[idx]
    if isinstance(val, str) and '/' in val:
        left, right = val.split('/', 1)
        if side == 'right':
            val = right
        elif side == 'left':
            val = left
        else:
            # Combined cell but nobody said which half — refuse to guess
            # rather than silently return a wrong number.
            return None
    try: return float(val)
    except (TypeError, ValueError): return None


def _player_appeared(boxscore: dict, player_name: str) -> bool:
    """Did this player show up ANYWHERE in the boxscore?

    2026-09-19: needed to tell a real zero from a did-not-play. A WR who
    suited up and caught nothing genuinely has 0 receptions; a WR who was
    inactive has no line at all, and grading his UNDER as a win is
    inventing a result. ESPN only lists players who recorded a stat in
    some category, so presence anywhere is our DNP proxy.
    """
    if not boxscore:
        return False
    want = (player_name or '').lower().strip()
    if not want:
        return False
    parts = want.split()
    want_alt = f'{parts[0][0]}.{parts[-1]}' if len(parts) >= 2 else None
    for team in boxscore.get('boxscore', {}).get('players', []):
        for grp in team.get('statistics', []):
            for ath in grp.get('athletes', []):
                a = ath.get('athlete', {})
                nm = (a.get('displayName') or '').lower().strip()
                short = (a.get('shortName') or '').lower().strip().replace(' ', '')
                if nm == want or (want_alt and short == want_alt):
                    return True
    return False


def _extract_player_stat(boxscore: dict, player_name: str, prop_type: str) -> float | None:
    """Find a player's stat in an ESPN boxscore."""
    if not boxscore: return None
    # Special: anytime_td = rushing_tds + receiving_tds
    if prop_type.startswith('anytime_td'):
        rush_td = _extract_player_stat(boxscore, player_name, 'rush_tds')
        rec_td  = _extract_player_stat(boxscore, player_name, 'rec_tds')
        rush_td = rush_td or 0; rec_td = rec_td or 0
        return float(rush_td + rec_td)

    # Strip _over/_under suffix
    base = prop_type.replace('_over', '').replace('_under', '')
    espn_cfg = PROP_TO_ESPN.get(base)
    if not espn_cfg:
        return None
    category_name, stat_label, stat_side = espn_cfg

    players_section = boxscore.get('boxscore', {}).get('players', [])
    for team in players_section:
        for stat_group in team.get('statistics', []):
            if stat_group.get('name') != category_name: continue
            labels = stat_group.get('labels', [])
            for ath in stat_group.get('athletes', []):
                name = ath.get('athlete', {}).get('displayName', '')
                if name.lower() != player_name.lower():
                    # Try partial-last-name match
                    if player_name.split()[-1].lower() not in name.lower():
                        continue
                stats_row = ath.get('stats', [])
                val = _parse_athlete_stat(stats_row, stat_label, labels, stat_side)
                if val is not None: return val
    return None


def _grade_prop(prop_line: float, direction: str, actual: float) -> str:
    if actual > prop_line:
        return 'Win' if direction.upper() == 'OVER' else 'Loss'
    if actual < prop_line:
        return 'Win' if direction.upper() == 'UNDER' else 'Loss'
    return 'Push'


def resolve_date(date: str, dry_run: bool = False) -> int:
    print(f'== resolve_nfl_props_espn · {date} · dry={dry_run} ==')
    # 1. Pull unresolved NFL props for this date
    r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props',
        params={'game_date': f'eq.{date}',
                'result': 'is.null',
                'select': 'id,game_id,player_name,player_team,opp_team,prop_type,'
                          'direction,prop_line'},
        headers=H_READ, timeout=15)
    props = r.json() if r.status_code == 200 else []
    print(f'  {len(props)} unresolved props for {date}')
    if not props: return 0

    # 2. Load ESPN events on this date + build (away@home) → event_id map
    # 2026-09-10: also load prior + next day. UTC-vs-ET crossover means
    # a TNF game (kickoff 8:20 PM ET) can have game_date = UTC next-day
    # while ESPN scoreboard files it on ET date. Search ±1 day to catch both.
    from datetime import date as _date
    y, m, d_ = map(int, date.split('-'))
    d_center = _date(y, m, d_)
    events = []
    for delta in (-1, 0, 1):
        d_query = (d_center + timedelta(days=delta)).isoformat()
        e = _espn_events_on_date(d_query)
        events.extend(e)
    print(f'  {len(events)} ESPN events on {date} ±1 day')
    _load_aliases()
    events_by_pair = {}
    for ev in events:
        status = ev.get('status', {}).get('type', {}).get('completed')
        if not status: continue  # skip games not yet final
        comps = ev.get('competitions', [{}])[0].get('competitors', [])
        home_ab = None; away_ab = None
        for c in comps:
            ab = c.get('team', {}).get('abbreviation')
            if c.get('homeAway') == 'home': home_ab = ab
            else: away_ab = ab
        if home_ab and away_ab:
            events_by_pair[(away_ab, home_ab)] = ev['id']
            # 2026-09-24 ROOT CAUSE of 46 permanently ungraded props.
            #
            # ESPN calls Washington "WSH". We store "WAS". The exact pair
            # lookup missed, and the substring fallback below cannot save
            # it either — 'WSH' in 'WAS' is False and so is the reverse.
            # So every prop on WAS @ DAL (09-20, a STATUS_FINAL game we
            # already hold a 20-37 score for) was marked ungradeable, and
            # since the pipeline calls this with --lookback 3 those props
            # fell out of reach permanently four days later.
            #
            # 19 of the 46 were STRONG tier: publishable plays missing
            # from the record entirely, and it would have recurred every
            # week Washington played.
            #
            # nfl_team_aliases already maps WSH -> WAS and 2,800 other
            # variants. It just was not being consulted here. Index every
            # alias so a feed renaming a team cannot silently erase a
            # week of grades again.
            for a in _alias_variants(away_ab):
                for h in _alias_variants(home_ab):
                    events_by_pair.setdefault((a, h), ev['id'])
            # Also index by full team names
            home_full = _ESPN_TEAM_ABBR.get(home_ab, '')
            away_full = _ESPN_TEAM_ABBR.get(away_ab, '')
            if home_full and away_full:
                events_by_pair[(away_full, home_full)] = ev['id']
    print(f'  {len(events_by_pair)//2} completed games available for grading')
    if not events_by_pair:
        print('  ⚠ no completed games — nothing to grade')
        return 0

    # 3. Group props by game_id (or by team pair)
    # Load nfl_game_context to get (home_team, away_team) per game_id
    gids = list(set(p['game_id'] for p in props))
    ids_csv = ','.join(f'"{g}"' for g in gids)
    r2 = requests.get(f'{SB}/rest/v1/nfl_game_context',
        params={'game_id': f'in.({ids_csv})',
                'select': 'game_id,home_team,away_team'},
        headers=H_READ, timeout=15)
    ctx_by_gid = {}
    if r2.status_code == 200:
        for row in r2.json():
            if isinstance(row, dict): ctx_by_gid[row['game_id']] = row

    # 4. Cache ESPN boxscores per event
    boxscores = {}
    graded = 0
    ungradeable = 0
    for p in props:
        gid = p['game_id']
        ctx = ctx_by_gid.get(gid)
        if not ctx:
            ungradeable += 1; continue
        pair = (ctx['away_team'], ctx['home_team'])
        event_id = events_by_pair.get(pair)
        if not event_id:
            # Try abbreviation variants — ESPN might use abbrev in team names
            found = False
            for (a, h), eid in events_by_pair.items():
                if a in ctx['away_team'] or ctx['away_team'] in a:
                    if h in ctx['home_team'] or ctx['home_team'] in h:
                        event_id = eid; found = True; break
            if not found:
                ungradeable += 1; continue

        # Get boxscore (cached)
        if event_id not in boxscores:
            boxscores[event_id] = _espn_boxscore(event_id)
        box = boxscores.get(event_id)
        if not box:
            ungradeable += 1; continue

        # Extract player stat
        player = p['player_name']
        prop_type = p['prop_type']
        actual = _extract_player_stat(box, player, prop_type)
        if actual is None:
            # 2026-09-19: this used to be a flat `actual = 0.0`, which
            # collapsed THREE different situations into one confident
            # answer:
            #   1. player appeared and genuinely recorded 0  -> 0 is right
            #   2. player did NOT play                       -> Void, not 0
            #   3. the stat could not be READ (the C/ATT label bug, a name
            #      mismatch, a wrong date)                   -> unknown
            # Case 3 was the C/ATT damage: 35 props graded against a zero
            # the parser never actually read. Case 2 is still live — a DNP
            # UNDER graded as a win is a fabricated result.
            if not _player_appeared(box, player):
                print(f'  ⚪ {player[:25]:<25} DID NOT PLAY -> Void')
                if not dry_run:
                    requests.patch(
                        f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{p["id"]}',
                        headers=H_WRITE,
                        json={'result': 'Void', 'final_value': None,
                              'resolved_at': datetime.now(timezone.utc).isoformat()},
                        timeout=15)
                ungradeable += 1
                continue
            # Player appeared but has no line in this category — a real
            # zero (a RB with no targets has 0 receptions).
            actual = 0.0

        prop_line = float(p['prop_line'])
        direction = p['direction'] or 'OVER'
        verdict = _grade_prop(prop_line, direction, actual)

        emoji = '✅' if verdict == 'Win' else '❌' if verdict == 'Loss' else '⚪'
        print(f'  {emoji} {player[:25]:<25} {direction.upper():<6} {prop_line:>5} {prop_type[:16]:<16}  actual={actual}  → {verdict}')

        if not dry_run:
            payload = {'result': verdict, 'final_value': actual,
                       'resolved_at': datetime.now(timezone.utc).isoformat()}
            r3 = requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{p["id"]}',
                headers=H_WRITE, json=payload, timeout=15)
            if r3.status_code not in (200, 204):
                print(f'    ⚠ patch failed {r3.status_code}: {r3.text[:100]}')
        graded += 1

    print(f'\n  ✓ graded {graded} props  ·  ungradeable {ungradeable}')
    return graded


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--lookback', type=int, default=None,
                    help='grade the last N days (overrides --date)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.lookback:
        today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
        for i in range(args.lookback):
            d = (today - timedelta(days=i)).isoformat()
            resolve_date(d, dry_run=args.dry_run)
    else:
        d = args.date or (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
        resolve_date(d, dry_run=args.dry_run)
