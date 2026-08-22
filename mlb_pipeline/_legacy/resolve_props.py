import requests
import os
from dotenv import load_dotenv
from datetime import datetime, date, timedelta
import time

from season_calendar import is_in_season

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

def get_pending_props():
    """Get all pending prop rows from mlb_pipeline_props (the canonical
    table generate_props.py writes to).

    2026-06-18 fix: previously read from the LEGACY prop_grades table,
    which generate_props.py stopped writing to ~2026-06-04. Result: no
    props were being graded for ~2 weeks despite generate_props.py
    writing 25+ rows/day to mlb_pipeline_props. This restored the
    grading flow.

    Filters:
      - game_date <= yesterday (don't grade in-flight games)
      - result is null (not yet graded)
      - tier in {PRIME, STRONG, LEAN, COVERAGE} (skip SKIP-tier only)

    COVERAGE tier was added 2026-07-31 by sweep_prop_coverage. Those
    stubs need grading too so prop_jerry_reads can inherit W/L on
    every BACK/FADE call, not just the ~30% that map to legacy-scored
    rows. Without this filter, 121 of 181 prop_jerry_reads sat
    ungraded after Day 1 of the Jerry-first launch.
    """
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props"
        f"?game_date=lte.{yesterday}&result=is.null"
        f"&tier=in.(PRIME,STRONG,LEAN,COVERAGE)&select=*",
        headers=HEADERS,
        timeout=30
    )
    data = r.json() if isinstance(r.json(), list) else []
    print(f"Found {len(data)} pending props to resolve")
    return data

def get_mlb_game_result(home_team, away_team, game_date):
    """Get MLB game final score from MLB Stats API"""
    try:
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={
                'sportId': 1,
                'date': game_date,
                'hydrate': 'linescore,boxscore'
            },
            timeout=15
        )
        dates = r.json().get('dates', [])
        for d in dates:
            for game in d.get('games', []):
                if game.get('status', {}).get('abstractGameState') != 'Final':
                    continue
                gHome = game.get('teams', {}).get('home', {}).get('team', {}).get('name', '')
                gAway = game.get('teams', {}).get('away', {}).get('team', {}).get('name', '')
                if (home_team.lower() in gHome.lower() or gHome.lower() in home_team.lower()) and \
                   (away_team.lower() in gAway.lower() or gAway.lower() in away_team.lower()):
                    return game
        return None
    except Exception as e:
        print(f"Error fetching MLB game: {e}")
        return None

def get_pitcher_strikeouts(game, pitcher_name):
    """Get pitcher strikeout total from MLB boxscore"""
    try:
        game_pk = game.get('gamePk')
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore',
            timeout=15
        )
        boxscore = r.json()
        for team_side in ['home', 'away']:
            pitchers = boxscore.get('teams', {}).get(team_side, {}).get('pitchers', [])
            players = boxscore.get('teams', {}).get(team_side, {}).get('players', {})
            for pitcher_id in pitchers:
                player_key = f'ID{pitcher_id}'
                player = players.get(player_key, {})
                full_name = player.get('person', {}).get('fullName', '')
                last_name = pitcher_name.split(' ')[-1].lower()
                if last_name in full_name.lower():
                    stats = player.get('stats', {}).get('pitching', {})
                    strikeouts = stats.get('strikeOuts', None)
                    if strikeouts is not None:
                        return int(strikeouts)
        return None
    except Exception as e:
        return None

def _pitcher_stat(game, pitcher_name, stat_key):
    """Shared helper: pull a pitcher boxscore stat by key.
    2026-06-18 — added to support hits-allowed, walks, ER, outs props
    after migrating resolver from prop_grades to mlb_pipeline_props."""
    try:
        game_pk = game.get('gamePk')
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore',
            timeout=15
        )
        boxscore = r.json()
        for team_side in ['home', 'away']:
            pitchers = boxscore.get('teams', {}).get(team_side, {}).get('pitchers', [])
            players = boxscore.get('teams', {}).get(team_side, {}).get('players', {})
            for pitcher_id in pitchers:
                player_key = f'ID{pitcher_id}'
                player = players.get(player_key, {})
                full_name = player.get('person', {}).get('fullName', '')
                last_name = pitcher_name.split(' ')[-1].lower()
                if last_name in full_name.lower():
                    stats = player.get('stats', {}).get('pitching', {})
                    v = stats.get(stat_key)
                    if v is None:
                        return None
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            return None
        return None
    except Exception:
        return None


def get_pitcher_hits_allowed(game, pitcher_name):
    """Hits allowed by pitcher — MLB Stats API boxscore key 'hits'."""
    return _pitcher_stat(game, pitcher_name, 'hits')


def get_pitcher_walks(game, pitcher_name):
    """Walks (BB) by pitcher — key 'baseOnBalls'."""
    return _pitcher_stat(game, pitcher_name, 'baseOnBalls')


def get_pitcher_earned_runs(game, pitcher_name):
    """Earned runs by pitcher — key 'earnedRuns'."""
    return _pitcher_stat(game, pitcher_name, 'earnedRuns')


def is_mlb_game_final(game):
    """Confirm MLB game is finalized before grading any prop.

    Bug fix 2026-06-23: when grader ran before game finalized, boxscore
    showed inningsPitched='0.0' so get_pitcher_outs returned 0, the prop
    got marked Win with final_value=0, and was never re-graded. Same bug
    pattern could affect any pitcher prop (Ks/BB/ER/Hits) — any 0-value
    could be a premature poll vs an actual zero outing.

    Reads game.status.detailedState from the schedule-style game dict.
    Falls back to direct gamePk lookup if status not in dict.
    """
    status = (game.get('status') or {}).get('detailedState', '') if isinstance(game.get('status'), dict) else ''
    if status in ('Final', 'Game Over', 'Completed Early'):
        return True
    if status:
        # Status is set but not final → don't grade
        return False
    # Status not in dict — do a single lookup
    game_pk = game.get('gamePk')
    if not game_pk:
        return False
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live',
            timeout=10,
        )
        feed = r.json() if r.status_code == 200 else {}
        s = feed.get('gameData', {}).get('status', {}).get('detailedState', '')
        return s in ('Final', 'Game Over', 'Completed Early')
    except Exception:
        return False


def get_pitcher_outs(game, pitcher_name):
    """Outs recorded — derive from inningsPitched (string like "5.2").
    Format: integer.fractional where fractional is # of outs in next inning.
    So "5.2" = 5 innings + 2 outs = 17 outs total.

    Caller MUST confirm game is final via is_mlb_game_final() first.
    A finalized starter with 0 outs is statistically implausible (~1 in 10K
    starts), so we return None on IP=0 to be safe — better to skip than
    grade an artifact.
    """
    try:
        game_pk = game.get('gamePk')
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore',
            timeout=15
        )
        boxscore = r.json()
        for team_side in ['home', 'away']:
            pitchers = boxscore.get('teams', {}).get(team_side, {}).get('pitchers', [])
            players = boxscore.get('teams', {}).get(team_side, {}).get('players', {})
            for pitcher_id in pitchers:
                player_key = f'ID{pitcher_id}'
                player = players.get(player_key, {})
                full_name = player.get('person', {}).get('fullName', '')
                last_name = pitcher_name.split(' ')[-1].lower()
                if last_name in full_name.lower():
                    ip = player.get('stats', {}).get('pitching', {}).get('inningsPitched')
                    if ip is None:
                        return None
                    try:
                        # "5.2" → 5 innings + 2 outs = 17 outs total
                        s = str(ip)
                        if '.' in s:
                            whole, frac = s.split('.', 1)
                            outs = int(whole) * 3 + int(frac[0])
                        else:
                            outs = int(s) * 3
                        # A finalized starter with 0 outs is statistically
                        # implausible (would mean he was pulled before recording
                        # an out — happens but ~1 in 10K starts). If IP=0 here
                        # the more likely explanation is still a polling artifact
                        # despite our final-status check; safer to return None.
                        if outs == 0:
                            return None
                        return outs
                    except (TypeError, ValueError):
                        return None
        return None
    except Exception:
        return None


def get_batter_hits(game, batter_name):
    """Get batter hit total from MLB boxscore"""
    try:
        game_pk = game.get('gamePk')
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore',
            timeout=15
        )
        boxscore = r.json()
        for team_side in ['home', 'away']:
            batters = boxscore.get('teams', {}).get(team_side, {}).get('batters', [])
            players = boxscore.get('teams', {}).get(team_side, {}).get('players', {})
            for batter_id in batters:
                player_key = f'ID{batter_id}'
                player = players.get(player_key, {})
                full_name = player.get('person', {}).get('fullName', '')
                last_name = batter_name.split(' ')[-1].lower()
                if last_name in full_name.lower():
                    stats = player.get('stats', {}).get('batting', {})
                    hits = stats.get('hits', None)
                    if hits is not None:
                        return int(hits)
        return None
    except Exception as e:
        return None

def get_nba_player_stats(player_name, game_date):
    """Get NBA player stats from BDL for a specific date"""
    try:
        BDL_API_KEY = os.environ.get("BDL_API_KEY")
        r = requests.get(
            'https://api.balldontlie.io/nba/v1/stats',
            headers={'Authorization': BDL_API_KEY},
            params={
                'dates[]': game_date,
                'per_page': 100
            },
            timeout=15
        )
        stats = r.json().get('data', [])
        last_name = player_name.split(' ')[-1].lower()
        for stat in stats:
            player = stat.get('player', {})
            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".lower()
            if last_name in full_name:
                return stat
        return None
    except Exception as e:
        return None

def resolve_prop(prop):
    """Resolve a single mlb_pipeline_props row to Win/Loss/Push.

    2026-06-18 schema migration: column names changed when moving from
    prop_grades to mlb_pipeline_props:
      player    -> player_name
      market    -> prop_type   (e.g. 'ks_over', 'hits_under')
      line      -> prop_line
      best_side -> direction   ('over' / 'under', lowercase)
      sport     -> (always MLB in this table — no sport column)
      game      -> matchup
      created_at-> game_date   (used directly, no slicing)
    """
    prop_type = (prop.get('prop_type') or '').lower()
    player = prop.get('player_name', '')
    line = float(prop.get('prop_line', 0) or 0)
    direction = (prop.get('direction') or 'over').lower()
    matchup = prop.get('matchup', '')
    game_date = prop.get('game_date')

    if not game_date or not matchup:
        return None

    actual_value = None
    parts = matchup.split(' @ ')
    if len(parts) != 2:
        return None
    away_team = parts[0].strip()
    home_team = parts[1].strip()
    mlb_game = get_mlb_game_result(home_team, away_team, game_date)
    if not mlb_game:
        return None

    # Bug fix 2026-06-23: refuse to grade pitcher props on a non-final game.
    # Premature grades polled IP=0 then locked Win/Loss + final_value=0 forever.
    pitcher_props = ('ks_over', 'ks_under', 'bb_over', 'bb_under',
                     'er_over', 'er_under', 'ha_over', 'ha_under',
                     'outs_over', 'outs_under', 'hits_over', 'hits_under')
    if prop_type in pitcher_props and not is_mlb_game_final(mlb_game):
        return None

    # Map prop_type to the actual-value fetcher
    if prop_type in ('ks_over', 'ks_under') or 'strikeout' in prop_type:
        actual_value = get_pitcher_strikeouts(mlb_game, player)
    elif prop_type in ('hits_over', 'hits_under'):
        actual_value = get_batter_hits(mlb_game, player)
    elif prop_type in ('ha_over', 'ha_under'):
        # Pitcher hits allowed — use the same boxscore fetcher;
        # extend get_pitcher_strikeouts pattern to read hits allowed
        actual_value = get_pitcher_hits_allowed(mlb_game, player)
    elif prop_type in ('bb_over', 'bb_under'):
        actual_value = get_pitcher_walks(mlb_game, player)
    elif prop_type in ('er_over', 'er_under'):
        actual_value = get_pitcher_earned_runs(mlb_game, player)
    elif prop_type in ('outs_over', 'outs_under'):
        actual_value = get_pitcher_outs(mlb_game, player)
    time.sleep(0.3)

    if actual_value is None:
        return None

    # Determine Win/Loss/Push (push when actual equals line, since
    # lines on most prop markets are integer.5)
    if direction == 'over':
        if actual_value > line: result = 'Win'
        elif actual_value < line: result = 'Loss'
        else: result = 'Push'
    else:
        if actual_value < line: result = 'Win'
        elif actual_value > line: result = 'Loss'
        else: result = 'Push'

    print(f"  {player} {prop_type} {direction} {line} -> actual {actual_value} -> {result}")
    return result, actual_value

def update_prop_result(prop_id, result, actual_value=None):
    """Update prop result + final_value on the mlb_pipeline_props row."""
    payload = {"result": result, "resolved_at": datetime.now().isoformat()}
    if actual_value is not None:
        payload["final_value"] = actual_value
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/mlb_pipeline_props?id=eq.{prop_id}",
        headers=HEADERS,
        json=payload,
    )
    return r.status_code in [200, 201, 204]

def run():
    print("Starting prop result resolution...")
    pending = get_pending_props()

    if not pending:
        print("No pending props to resolve")
        return

    resolved = 0
    failed = 0
    skipped = 0

    for prop in pending:
        try:
            # mlb_pipeline_props has no sport column — all rows are MLB.
            outcome = resolve_prop(prop)
            if outcome:
                result, actual_value = outcome
                if update_prop_result(prop['id'], result, actual_value):
                    resolved += 1
                else:
                    failed += 1
            else:
                skipped += 1

        except Exception as e:
            print(f"Error resolving {prop.get('player_name')}: {e}")
            failed += 1

    print(f"\nDone! {resolved} resolved, {failed} errors, {skipped} skipped")

if __name__ == "__main__":
    run()