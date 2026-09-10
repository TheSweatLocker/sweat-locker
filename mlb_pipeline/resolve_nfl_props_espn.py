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
PROP_TO_ESPN = {
    'pass_yds':       ('passing',   'YDS'),
    'pass_tds':       ('passing',   'TD'),
    'pass_attempts':  ('passing',   'ATT'),  # C/ATT is combined, need parse
    'pass_completions': ('passing', 'CMP'),
    'ints':           ('passing',   'INT'),
    'pass_interceptions': ('passing', 'INT'),
    'rush_yds':       ('rushing',   'YDS'),
    'rush_attempts':  ('rushing',   'CAR'),
    'rush_tds':       ('rushing',   'TD'),
    'reception_yds':  ('receiving', 'YDS'),
    'rec_yds':        ('receiving', 'YDS'),
    'receptions':     ('receiving', 'REC'),
    'reception_tds':  ('receiving', 'TD'),
    'rec_tds':        ('receiving', 'TD'),
    # anytime_td = sum of rushing_tds + receiving_tds (handled specially)
}


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


def _parse_athlete_stat(stats_row: list, label: str, labels: list) -> float | None:
    """Extract one stat from an athlete's stats list."""
    if label not in labels: return None
    idx = labels.index(label)
    if idx >= len(stats_row): return None
    val = stats_row[idx]
    # Values sometimes like '10/15' for C/ATT — pick right side for ATT, left for CMP
    if isinstance(val, str) and '/' in val:
        left, right = val.split('/', 1)
        # If label was ATT, take right side; if CMP, take left
        val = right if label == 'ATT' else left
    try: return float(val)
    except (TypeError, ValueError): return None


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
    category_name, stat_label = espn_cfg

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
                val = _parse_athlete_stat(stats_row, stat_label, labels)
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
            # Player didn't record this stat — grade as 0 for over-under
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
