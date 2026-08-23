"""MLB game context pipeline.

SIGN CONVENTIONS (load-bearing — verified empirically 2026-05-20):
Two spread fields use OPPOSITE signs. Read carefully before adding logic.

  projected_spread / model_pred_spread (v3 / v4):
    POSITIVE = home favored by X runs
    NEGATIVE = away favored by X runs

  close_spread / open_spread:
    NEGATIVE = home favored (home runline -1.5)
    POSITIVE = home is dog (home runline +1.5)

  spread_delta = projected_spread + close_spread
    SIGN = "model leans more home than market does"
    MAGNITUDE = strength of model-vs-market disagreement (in runs)

  home_ml_odds / away_ml_odds / *_ml_close / *_ml_open:
    Standard ML — NEGATIVE = favorite, POSITIVE = dog

  model_pred_total / projected_total: straight runs, no sign convention
  over_lean: True = over, False = under, None = neutral (v3-derived from
             projected_total ≥ close_total + 1.5)

PREFERRED MODEL FIELDS (v4 over v3):
  Consumers should prefer `model_pred_spread` / `model_pred_total`
  (XGBoost v4) and fall back to `projected_spread` / `projected_total`
  (v3) only when v4 is suppressed (missing xERA, opp data, etc.).
  See compute_primary_play (this file) and play_of_day.build_lean for
  the canonical pattern.
"""
import requests
from datetime import datetime, date, timedelta, timezone
import time
import json
from math import radians, sin, cos, sqrt, atan2
from typing import Optional

import os
from dotenv import load_dotenv
load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")

def sanitize_xera(xera, pitcher_name=''):
    """Validate xERA. Originally capped at 6.5 to filter April small-sample
    noise, but that was nulling out genuinely struggling pitchers (Lodolo,
    Imai, etc.) by mid-May once samples grew. Reformulated 2026-05-18:
      - Cap at 9.5 (truly absurd values only — likely a parse error)
      - Floor at 1.5 (sub-1.5 xERA over a season is implausible)
    Real-world MLB xERA range is ~1.8 to 8.5; we accept that whole range
    and let the downstream model handle the signal."""
    if xera is None:
        return None
    try:
        val = float(xera)
        if val > 9.5 or val < 1.5:
            print(f'  ⚠️ xERA {val:.2f} for {pitcher_name} outside MLB realistic range (1.5-9.5) — treating as None')
            return None
        return round(val, 2)
    except (TypeError, ValueError):
        return None

def sanitize_k_pct(k_pct, pitcher_name=''):
    """Cap K% at 40 — above this is suspect early season small sample"""
    if k_pct is None:
        return None
    try:
        val = float(k_pct)
        if val > 40.0:
            print(f'  ⚠️ Suspicious K% {val} for {pitcher_name} — capping at None')
            return None
        return round(val, 2)
    except:
        return None

def get_final_score(game_id_mlb):
    """Fetch final score from MLB Stats API by game PK"""
    try:
        r = requests.get(
            f'https://statsapi.mlb.com/api/v1/game/{game_id_mlb}/linescore',
            timeout=15
        )
        data = r.json()
        home_runs = data.get('teams', {}).get('home', {}).get('runs')
        away_runs = data.get('teams', {}).get('away', {}).get('runs')
        innings = data.get('innings', [])
        game_over = len(innings) >= 9 and home_runs is not None
        return home_runs, away_runs, game_over
    except Exception as e:
        print(f'  Error fetching final score: {e}')
        return None, None, False

def get_mlb_game_pk(home_team, away_team, game_date, commence_time_hint=None):
    """Find MLB Stats API game PK by team names and date.

    DOUBLEHEADER FIX (2026-04-29): when same teams play twice in a day, the
    schedule endpoint returns BOTH games. Without disambiguation, this function
    returned whichever game was first in the response — attaching game 1's
    score to both DH rows. If commence_time_hint is provided, picks the game
    with the closest start time.
    """
    try:
        r = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={
                'sportId': 1,
                'date': game_date,
                'hydrate': 'linescore'
            },
            timeout=15
        )
        dates = r.json().get('dates', [])
        candidates = []
        for d in dates:
            for game in d.get('games', []):
                mlb_home = game.get('teams', {}).get('home', {}).get('team', {}).get('name', '')
                mlb_away = game.get('teams', {}).get('away', {}).get('team', {}).get('name', '')
                home_match = home_team.lower() in mlb_home.lower() or mlb_home.lower() in home_team.lower()
                away_match = away_team.lower() in mlb_away.lower() or mlb_away.lower() in away_team.lower()
                if home_match and away_match:
                    status = game.get('status', {}).get('abstractGameState', '')
                    if status == 'Final':
                        candidates.append(game)

        if not candidates:
            return None, None
        if len(candidates) == 1:
            return candidates[0].get('gamePk'), candidates[0]

        # Doubleheader: 2+ matches. Disambiguate by closest commence_time if provided.
        if commence_time_hint:
            try:
                hint_dt = datetime.fromisoformat(commence_time_hint.replace('Z', '+00:00'))
                best = min(
                    candidates,
                    key=lambda g: abs((datetime.fromisoformat(g.get('gameDate', '').replace('Z', '+00:00')) - hint_dt).total_seconds())
                )
                gn = best.get('gameNumber', '?')
                print(f"  🎯 Doubleheader detected ({len(candidates)} games) — matched game #{gn} by start time")
                return best.get('gamePk'), best
            except Exception as e:
                print(f"  DH disambig failed ({e}) — falling back to first game")
        # No hint: warn and use first (preserves old behavior for safety)
        print(f"  ⚠️ Doubleheader detected with NO commence_time hint — using first game (may be wrong DH leg)")
        return candidates[0].get('gamePk'), candidates[0]
    except Exception as e:
        print(f'  Error finding game PK: {e}')
        return None, None

def get_probable_pitchers(game_date):
    """Fetch probable pitchers from MLB Stats API.

    DH FIX (2026-04-30): returns a LIST of per-game dicts (was a dict keyed by
    home_team, which collided on doubleheaders — game 2 overwrote game 1).
    Use match_probable_pitcher() with a commence_time hint to disambiguate.
    """
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "date": game_date,
                "hydrate": "probablePitcher"
            }
        )
        data = r.json()
        games_list = []
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                game_pk = str(game.get("gamePk", ""))
                home_team = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                away_team = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                home_pitcher = game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName", None)
                away_pitcher = game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName", None)
                home_pitcher_id = game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("id", None)
                away_pitcher_id = game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("id", None)
                games_list.append({
                    "game_pk": game_pk,
                    "home_team": home_team,
                    "away_team": away_team,
                    "commence_time": game.get("gameDate"),  # ISO timestamp
                    "home_pitcher": home_pitcher,
                    "away_pitcher": away_pitcher,
                    "home_pitcher_id": home_pitcher_id,
                    "away_pitcher_id": away_pitcher_id,
                    "game_number": game.get("gameNumber", 1),
                })
        print(f"Found probable pitchers for {len(games_list)} games")
        return games_list
    except Exception as e:
        print(f"MLB Stats API error: {e}")
        return []


def match_probable_pitcher(games_list, home_team, away_team, commence_time_hint=None):
    """Find the matching probable-pitcher entry for a given Odds API game.

    Uses team-pair match. If multiple matches (doubleheader), disambiguates
    by closest commence_time. Returns dict (or empty dict if no match).
    """
    candidates = [
        g for g in games_list
        if (home_team.lower() in g["home_team"].lower() or g["home_team"].lower() in home_team.lower())
        and (away_team.lower() in g["away_team"].lower() or g["away_team"].lower() in away_team.lower())
    ]
    if not candidates:
        return {}
    if len(candidates) == 1:
        return candidates[0]
    # Doubleheader: disambiguate by closest start time
    if commence_time_hint:
        try:
            hint_dt = datetime.fromisoformat(commence_time_hint.replace('Z', '+00:00'))
            best = min(
                candidates,
                key=lambda g: abs((datetime.fromisoformat((g.get('commence_time') or '').replace('Z', '+00:00')) - hint_dt).total_seconds())
                              if g.get('commence_time') else 999999
            )
            print(f"  🎯 DH probable pitcher matched: game #{best.get('game_number')} ({best.get('home_pitcher')} vs {best.get('away_pitcher')})")
            return best
        except Exception as e:
            print(f"  DH probable-pitcher disambig failed ({e}) — using first")
    return candidates[0]

def get_pitcher_days_rest(pitcher_id, game_date):
    """Calculate days rest for a pitcher based on last appearance"""
    if not pitcher_id:
        return None
    try:
        # Look back 15 days for last start
        end_date = game_date
        start_date = (datetime.strptime(game_date, '%Y-%m-%d') - timedelta(days=15)).strftime('%Y-%m-%d')
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "startDate": start_date,
                "endDate": end_date,
                "hydrate": f"probablePitcher",
                "fields": "dates,date,games,teams,probablePitcher,id"
            }
        )
        data = r.json()
        last_start = None
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                for side in ["home", "away"]:
                    pp = game.get("teams", {}).get(side, {}).get("probablePitcher", {})
                    if pp.get("id") == pitcher_id:
                        game_date_str = date_entry.get("date")
                        if game_date_str and game_date_str < end_date:
                            if not last_start or game_date_str > last_start:
                                last_start = game_date_str
        if last_start:
            last_dt = datetime.strptime(last_start, '%Y-%m-%d')
            today_dt = datetime.strptime(end_date, '%Y-%m-%d')
            return (today_dt - last_dt).days
        return None
    except Exception as e:
        return None

def get_umpires(game_date):
    """Fetch home plate umpires from MLB Stats API.

    DH FIX (2026-04-30): returns LIST of per-game dicts (was keyed by home_team).
    Use match_umpire() with commence_time hint to disambiguate.
    """
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "date": game_date,
                "hydrate": "officials"
            }
        )
        data = r.json()
        games_list = []
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                home_team = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                away_team = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                officials = game.get("officials", [])
                home_plate_ump = next(
                    (o.get("official", {}).get("fullName")
                    for o in officials
                    if o.get("officialType") == "Home Plate"),
                    None
                )
                if home_plate_ump:
                    games_list.append({
                        "home_team": home_team,
                        "away_team": away_team,
                        "commence_time": game.get("gameDate"),
                        "umpire": home_plate_ump,
                    })
        print(f"Found umpires for {len(games_list)} games")
        return games_list
    except Exception as e:
        print(f"Umpire fetch error: {e}")
        return []


def match_umpire(games_list, home_team, away_team, commence_time_hint=None):
    """DH-aware umpire lookup. Returns ump name or None."""
    candidates = [
        g for g in games_list
        if (home_team.lower() in g["home_team"].lower() or g["home_team"].lower() in home_team.lower())
        and (away_team.lower() in g["away_team"].lower() or g["away_team"].lower() in away_team.lower())
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].get("umpire")
    if commence_time_hint:
        try:
            hint_dt = datetime.fromisoformat(commence_time_hint.replace('Z', '+00:00'))
            best = min(
                candidates,
                key=lambda g: abs((datetime.fromisoformat((g.get('commence_time') or '').replace('Z', '+00:00')) - hint_dt).total_seconds())
                              if g.get('commence_time') else 999999
            )
            return best.get("umpire")
        except Exception:
            pass
    return candidates[0].get("umpire")

def get_umpire_stats(ump_name):
    """Look up umpire tendencies from Supabase"""
    if not ump_name:
        return None
    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_umpires?ump_name=ilike.{requests.utils.quote('*'+ump_name+'*')}&select=*&limit=1",
            headers=headers
        )
        data = r.json()
        return data[0] if data else None
    except:
        return None

def get_team_stats(team_name, season=2026):
    """Fetch team batting stats from MLB Stats API"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": season}
        )
        teams = r.json().get("teams", [])
        team = next((t for t in teams if t["name"].lower() == team_name.lower()), None)
        if not team:
            # Try partial match
            team = next((t for t in teams if team_name.lower() in t["name"].lower() or t["name"].lower() in team_name.lower()), None)
        if not team:
            return None
        
        team_id = team["id"]
        
        # Get team batting stats
        r2 = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats",
            params={"stats": "season", "group": "hitting", "season": season}
        )
        stats = r2.json().get("stats", [])
        if not stats or not stats[0].get("splits"):
            return None
            
        batting = stats[0]["splits"][0]["stat"]
        return {
            "runs_per_game": float(batting.get("runs", 0)) / max(float(batting.get("gamesPlayed", 1)), 1),
            "avg": float(batting.get("avg", 0.250)),
            "obp": float(batting.get("obp", 0.320)),
            "slg": float(batting.get("slg", 0.400)),
            "ops": float(batting.get("ops", 0.720)),
            "home_runs": int(batting.get("homeRuns", 0)),
            "games_played": int(batting.get("gamesPlayed", 0)),
        }
    except Exception as e:
        return None

def get_team_splits(team_name, season=2026):
    """Fetch home/away splits from MLB Stats API"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": season}
        )
        teams = r.json().get("teams", [])
        team = next((t for t in teams if t["name"].lower() == team_name.lower()), None)
        if not team:
            team = next((t for t in teams if team_name.lower() in t["name"].lower() or t["name"].lower() in team_name.lower()), None)
        if not team:
            return None

        team_id = team["id"]

        r2 = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats",
            params={
                "stats": "homeAndAway",
                "group": "hitting",
                "season": season
            }
        )
        splits = r2.json().get("stats", [])
        if not splits:
            return None

        home_stats = None
        away_stats = None

        for split_group in splits:
            for split in split_group.get("splits", []):
                split_type = split.get("split", {}).get("code", "")
                stat = split.get("stat", {})
                games = int(stat.get("gamesPlayed", 1))
                if games == 0:
                    continue
                runs_per_game = float(stat.get("runs", 0)) / games
                ops = float(stat.get("ops", 0.720))
                if split_type == "H":
                    home_stats = {"runs_per_game": runs_per_game, "ops": ops, "games": games}
                elif split_type == "A":
                    away_stats = {"runs_per_game": runs_per_game, "ops": ops, "games": games}

        return {"home": home_stats, "away": away_stats}
    except Exception as e:
        return None

def get_team_strikeout_rate(team_name, season=2026):
    """Fetch team strikeout rate from MLB Stats API"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": season}
        )
        teams = r.json().get("teams", [])
        team = next((t for t in teams if t["name"].lower() == team_name.lower()), None)
        if not team:
            team = next((t for t in teams if team_name.lower() in t["name"].lower() or t["name"].lower() in team_name.lower()), None)
        if not team:
            return None

        team_id = team["id"]
        r2 = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats",
            params={"stats": "season", "group": "hitting", "season": season}
        )
        stats = r2.json().get("stats", [])
        if not stats or not stats[0].get("splits"):
            return None

        batting = stats[0]["splits"][0]["stat"]
        ab = float(batting.get("atBats", 0))
        so = float(batting.get("strikeOuts", 0))
        games = float(batting.get("gamesPlayed", 1))
        if ab == 0 or games < 5:
            return None
        k_rate = (so / ab) * 100
        return round(k_rate, 1)
    except Exception as e:
        return None

def get_team_last10(team_name, season=2026):
    """Fetch team last 10 games record from MLB Stats API standings"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/standings",
            params={
                "leagueId": "103,104",
                "season": season,
                "standingsTypes": "regularSeason",
                "hydrate": "team,streak,division,sport,league,record(overallRecords,splitRecords)"
            },
            timeout=10
        )
        data = r.json()
        for record in data.get("records", []):
            for team_record in record.get("teamRecords", []):
                name = team_record.get("team", {}).get("name", "")
                if name.lower() == team_name.lower() or team_name.lower() in name.lower() or name.lower() in team_name.lower():
                    records = team_record.get("records", {})
                    # lastTen lives in splitRecords, not overallRecords
                    split_records = records.get("splitRecords", [])
                    last10 = next((r for r in split_records if r.get("type") == "lastTen"), None)
                    # Belt-and-suspenders: also check overallRecords + top-level "records" list shape
                    if not last10:
                        overall = records.get("overallRecords", [])
                        last10 = next((r for r in overall if r.get("type") == "lastTen"), None)
                    streak = team_record.get("streak", {}).get("streakCode", "")
                    wins = team_record.get("wins", 0)
                    losses = team_record.get("losses", 0)
                    return {
                        "wins": wins,
                        "losses": losses,
                        "last10": f"{last10.get('wins', 0)}-{last10.get('losses', 0)}" if last10 else None,
                        "streak": streak
                    }
        return None
    except Exception:
        return None

def get_catcher_framing(catcher_name):
    """Look up catcher framing runs from Supabase mlb_catcher_framing"""
    if not catcher_name:
        return None
    try:
        import unicodedata
        normalized = unicodedata.normalize('NFKD', catcher_name).encode('ascii', 'ignore').decode('ascii')
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_catcher_framing?player_name=ilike.{requests.utils.quote('*'+normalized+'*')}&select=framing_runs,strike_rate&limit=1",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=5
        )
        data = r.json()
        if data and data[0].get('framing_runs') is not None:
            return float(data[0]['framing_runs'])
        return None
    except Exception:
        return None

def get_confirmed_lineups(game_date):
    """Fetch confirmed batting lineups from MLB Stats API.

    DH FIX (2026-04-30): returns LIST of per-game dicts (was keyed by home_team
    which collided on doubleheaders). Use match_lineup() to disambiguate via
    commence_time when looking up.
    """
    print(f"Fetching confirmed lineups for {game_date}...")
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "date": game_date,
                "hydrate": "lineups"
            }
        )
        data = r.json()
        games_list = []
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                home_team = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                away_team = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                home_lineup = game.get("lineups", {}).get("homePlayers", [])
                away_lineup = game.get("lineups", {}).get("awayPlayers", [])
                home_batters = [p.get("fullName", "") for p in home_lineup if p.get("primaryPosition", {}).get("abbreviation") != "P"]
                away_batters = [p.get("fullName", "") for p in away_lineup if p.get("primaryPosition", {}).get("abbreviation") != "P"]
                home_catcher = next((p.get("fullName", "") for p in home_lineup if p.get("primaryPosition", {}).get("abbreviation") == "C"), None)
                away_catcher = next((p.get("fullName", "") for p in away_lineup if p.get("primaryPosition", {}).get("abbreviation") == "C"), None)
                entry = {
                    "home_team": home_team,
                    "away_team": away_team,
                    "commence_time": game.get("gameDate"),
                    "game_number": game.get("gameNumber", 1),
                }
                if home_batters or away_batters:
                    entry.update({
                        "home_lineup": home_batters[:9],
                        "away_lineup": away_batters[:9],
                        "home_catcher": home_catcher,
                        "away_catcher": away_catcher,
                        "lineup_confirmed": True,
                    })
                    print(f"  ✅ Lineup confirmed: {away_team} @ {home_team} (game #{entry['game_number']})")
                else:
                    entry.update({
                        "home_lineup": [],
                        "away_lineup": [],
                        "lineup_confirmed": False,
                    })
                games_list.append(entry)
        return games_list
    except Exception as e:
        print(f"Lineup fetch error: {e}")
        return []


def match_lineup(games_list, home_team, away_team, commence_time_hint=None):
    """Find matching lineup entry. DH-aware via commence_time disambiguation."""
    candidates = [
        g for g in games_list
        if (home_team.lower() in g["home_team"].lower() or g["home_team"].lower() in home_team.lower())
        and (away_team.lower() in g["away_team"].lower() or g["away_team"].lower() in away_team.lower())
    ]
    if not candidates:
        return {}
    if len(candidates) == 1:
        return candidates[0]
    if commence_time_hint:
        try:
            hint_dt = datetime.fromisoformat(commence_time_hint.replace('Z', '+00:00'))
            return min(
                candidates,
                key=lambda g: abs((datetime.fromisoformat((g.get('commence_time') or '').replace('Z', '+00:00')) - hint_dt).total_seconds())
                              if g.get('commence_time') else 999999
            )
        except Exception:
            pass
    return candidates[0]

def get_batter_handedness(player_name, season=2026):
    """Look up batter hitting hand from MLB Stats API"""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": player_name, "sportId": 1},
            timeout=10
        )
        data = r.json()
        people = data.get("people", [])
        if not people:
            return None
        person = people[0]
        bat_side = person.get("batSide", {}).get("code", None)
        return bat_side  # 'L', 'R', or 'S' (switch)
    except Exception as e:
        return None

def calc_platoon_advantage(lineup_names, pitcher_hand):
    """
    Calculate platoon advantage score for a lineup vs a pitcher.
    Returns (score, note) where score > 0 = lineup advantage, < 0 = pitcher advantage
    """
    if not lineup_names or not pitcher_hand:
        return None, None

    batters = [b.strip() for b in lineup_names.split(',') if b.strip()]
    if not batters:
        return None, None

    handedness = []
    for name in batters[:9]:  # top 9 only
        hand = get_batter_handedness(name)
        if hand:
            handedness.append(hand)
        time.sleep(0.1)  # rate limit

    if not handedness:
        return None, None

    total = len(handedness)
    # Opposite hand batters have platoon advantage
    if pitcher_hand == 'R':
        # LHB and switch hitters have advantage vs RHP
        advantage_batters = [h for h in handedness if h in ['L', 'S']]
        disadvantage_batters = [h for h in handedness if h == 'R']
    elif pitcher_hand == 'L':
        # RHB and switch hitters have advantage vs LHP
        advantage_batters = [h for h in handedness if h in ['R', 'S']]
        disadvantage_batters = [h for h in handedness if h == 'L']
    else:
        return None, None

    adv_count = len(advantage_batters)
    dis_count = len(disadvantage_batters)

    # Score: positive = lineup has platoon advantage, negative = pitcher has platoon advantage
    # Scale: each batter with platoon advantage = +1, disadvantage = -1
    score = round((adv_count - dis_count) / total * 10, 1)

    l_count = handedness.count('L')
    r_count = handedness.count('R')
    s_count = handedness.count('S')

    note = f"{l_count}L/{r_count}R/{s_count}S vs {pitcher_hand}HP — "
    if score >= 3:
        note += f"lineup has strong platoon advantage (+{score})"
    elif score >= 1:
        note += f"lineup has slight platoon advantage (+{score})"
    elif score <= -3:
        note += f"pitcher has strong platoon advantage ({score})"
    elif score <= -1:
        note += f"pitcher has slight platoon advantage ({score})"
    else:
        note += "neutral platoon matchup"

    return score, note

def get_pitcher_splits(pitcher_id):
    """Fetch pitcher home/away ERA splits, aggregating raw ER/IP across 2026 +
    2025 + 2024 so early-season thin samples don't produce fake splits.

    Trigger: same data-integrity class as the 5/27 Matz vs-team incident — a
    pitcher with 6 IP at home and 0 ER would have shown a 0.00 home ERA pre-
    fix and fed split_delta as if it were stable. Now we sum raw earned runs
    and innings across seasons and hard-gate at 15 IP per side. If a side has
    <15 IP across the 3-year window, that side returns None and split_delta
    sees missing data instead of garbage.

    Returns: {home_era, away_era, home_ip, away_ip} or None.
    """
    if not pitcher_id:
        return None
    try:
        agg = {"h": {"er": 0, "ip": 0.0}, "a": {"er": 0, "ip": 0.0}}
        for season in (2026, 2025, 2024):
            try:
                r = requests.get(
                    f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
                    params={
                        "stats": "statSplits",
                        "group": "pitching",
                        "season": season,
                        "sitCodes": "h,a",
                    },
                    timeout=10,
                )
                stats = r.json().get("stats", [])
            except Exception:
                continue
            for split_group in stats:
                for split in split_group.get("splits", []):
                    split_info = split.get("split", {})
                    code = (split_info.get("code", "") or "").strip().lower()
                    description = (split_info.get("description", "") or "").strip().lower()
                    stat_obj = split.get("stat", {})
                    ip_str = str(stat_obj.get("inningsPitched", "0") or "0")
                    try:
                        ip = float(ip_str.replace(".1", ".333").replace(".2", ".667"))
                        er = int(stat_obj.get("earnedRuns", 0) or 0)
                    except (ValueError, TypeError):
                        continue
                    if code == "h" or "home" in description:
                        agg["h"]["ip"] += ip
                        agg["h"]["er"] += er
                    elif code == "a" or "away" in description or "road" in description:
                        agg["a"]["ip"] += ip
                        agg["a"]["er"] += er
            # Stop expanding the window once both sides clear the gate
            if agg["h"]["ip"] >= 25 and agg["a"]["ip"] >= 25:
                break
        # 15-IP hard floor per side (≈3 starts) — same threshold as vs-team mastery
        home_era = round((agg["h"]["er"] * 9.0) / agg["h"]["ip"], 2) if agg["h"]["ip"] >= 15 else None
        away_era = round((agg["a"]["er"] * 9.0) / agg["a"]["ip"], 2) if agg["a"]["ip"] >= 15 else None
        if home_era is None and away_era is None:
            return None
        return {
            "home_era": home_era,
            "away_era": away_era,
            "home_ip": round(agg["h"]["ip"], 1),
            "away_ip": round(agg["a"]["ip"], 1),
        }
    except Exception:
        return None

def _refresh_pitcher_inning_buckets(pitcher_name, existing_row):
    """If a starter row exists but is missing inning bucket data (e.g. they were
    confirmed AFTER pitcher_stats.py ran for the day), fetch + PATCH the buckets
    in-place. Saves a manual backfill step for late-confirmed starters."""
    try:
        if existing_row.get('innings_1_3_era') is not None:
            return existing_row  # already has buckets
        from pitcher_stats import get_inning_bucket_splits
        buckets = get_inning_bucket_splits(pitcher_name)
        if not buckets:
            return existing_row
        # PATCH the row in Supabase
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        encoded = requests.utils.quote(existing_row.get('player_name', pitcher_name))
        # 2026-08-22: 30s to match bumped statsapi timeout in
        # pitcher_stats.get_inning_bucket_splits — the fetch step is what
        # takes long; PATCH is fast but stays consistent.
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=eq.{encoded}&season=eq.2026",
            headers=headers,
            json=buckets,
            timeout=30,
        )
        if r.status_code in (200, 204):
            print(f"  🔄 Auto-refreshed inning buckets for {pitcher_name} (was missing in DB)")
            existing_row.update(buckets)
        return existing_row
    except Exception as e:
        print(f"  inning bucket refresh failed for {pitcher_name}: {e}")
        return existing_row


def get_pitcher_stats(pitcher_name):
    """Look up pitcher stats from Supabase"""
    if not pitcher_name:
        return None
    try:
        import unicodedata
        # Normalize accent characters so López matches Lopez etc
        normalized_name = unicodedata.normalize('NFKD', pitcher_name).encode('ascii', 'ignore').decode('ascii')
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        # Try normalized name first
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=ilike.{requests.utils.quote('*'+normalized_name+'*')}&select=*&limit=1",
            headers=headers
        )
        data = r.json()
        if data:
            return _refresh_pitcher_inning_buckets(pitcher_name, data[0])
        # Fall back to original name
        r2 = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=ilike.{requests.utils.quote('*'+pitcher_name+'*')}&select=*&limit=1",
            headers=headers
        )
        data2 = r2.json()
        return _refresh_pitcher_inning_buckets(pitcher_name, data2[0]) if data2 else None
    except:
        return None

# Venue coordinates for weather lookup
VENUE_COORDS = {
    "Coors Field": (39.7559, -104.9942),
    "Great American Ball Park": (39.0979, -84.5082),
    "Citizens Bank Park": (39.9061, -75.1665),
    "Fenway Park": (42.3467, -71.0972),
    "Daikin Park": (29.7572, -95.3555),
    "Minute Maid Park": (29.7572, -95.3555),
    "Wrigley Field": (41.9484, -87.6553),
    "Globe Life Field": (32.7473, -97.0822),
    "Camden Yards": (39.2838, -76.6217),
    "Rogers Centre": (43.6414, -79.3894),
    "Truist Park": (33.8908, -84.4678),
    "Angel Stadium": (33.8003, -117.8827),
    "Target Field": (44.9817, -93.2781),
    "Progressive Field": (41.4962, -81.6852),
    "Kauffman Stadium": (39.0517, -94.4803),
    "T-Mobile Park": (47.5914, -122.3325),
    "George M. Steinbrenner Field": (27.9784, -82.5033),
    "Tropicana Field": (27.7683, -82.6534),
    "Guaranteed Rate Field": (41.8300, -87.6339),
    "loanDepot Park": (25.7781, -80.2197),
    "PNC Park": (40.4469, -80.0057),
    "Oracle Park": (37.7786, -122.3893),
    "Sutter Health Park": (38.5803, -121.5014),
    "Comerica Park": (42.3390, -83.0485),
    "Dodger Stadium": (34.0739, -118.2400),
    "UNIQLO Field at Dodger Stadium": (34.0739, -118.2400),
    "Petco Park": (32.7076, -117.1570),
    "Citi Field": (40.7571, -73.8458),
    "Yankee Stadium": (40.8296, -73.9262),
    "Busch Stadium": (38.6226, -90.1928),
    "American Family Field": (43.0280, -87.9712),
    "Chase Field": (33.4453, -112.0667),
    "Nationals Park": (38.8730, -77.0074),
}

# Team to venue mapping
TEAM_VENUE = {
    "Colorado Rockies": "Coors Field",
    "Cincinnati Reds": "Great American Ball Park",
    "Philadelphia Phillies": "Citizens Bank Park",
    "Boston Red Sox": "Fenway Park",
    "Houston Astros": "Daikin Park",
    "Chicago Cubs": "Wrigley Field",
    "Texas Rangers": "Globe Life Field",
    "Baltimore Orioles": "Camden Yards",
    "Toronto Blue Jays": "Rogers Centre",
    "Atlanta Braves": "Truist Park",
    "Los Angeles Angels": "Angel Stadium",
    "Minnesota Twins": "Target Field",
    "Cleveland Guardians": "Progressive Field",
    "Kansas City Royals": "Kauffman Stadium",
    "Seattle Mariners": "T-Mobile Park",
    "Tampa Bay Rays": "George M. Steinbrenner Field",
    "Chicago White Sox": "Guaranteed Rate Field",
    "Miami Marlins": "loanDepot Park",
    "Pittsburgh Pirates": "PNC Park",
    "San Francisco Giants": "Oracle Park",
    "Oakland Athletics": "Sutter Health Park",
    "Athletics": "Sutter Health Park",
    "Detroit Tigers": "Comerica Park",
    "Los Angeles Dodgers": "Dodger Stadium",
    "San Diego Padres": "Petco Park",
    "New York Mets": "Citi Field",
    "New York Yankees": "Yankee Stadium",
    "St. Louis Cardinals": "Busch Stadium",
    "Milwaukee Brewers": "American Family Field",
    "Arizona Diamondbacks": "Chase Field",
    "Washington Nationals": "Nationals Park",
}

# Dome stadiums — weather irrelevant
DOME_VENUES = ["George M. Steinbrenner Field", "Tropicana Field", "loanDepot Park", "Rogers Centre", "American Family Field", "Chase Field", "Globe Life Field", "Daikin Park"]

TEAM_TIMEZONES = {
    'New York Yankees': 'ET', 'New York Mets': 'ET', 'Boston Red Sox': 'ET',
    'Baltimore Orioles': 'ET', 'Tampa Bay Rays': 'ET', 'Toronto Blue Jays': 'ET',
    'Philadelphia Phillies': 'ET', 'Atlanta Braves': 'ET', 'Miami Marlins': 'ET',
    'Washington Nationals': 'ET', 'Pittsburgh Pirates': 'ET', 'Cleveland Guardians': 'ET',
    'Detroit Tigers': 'ET', 'Cincinnati Reds': 'ET',
    'Chicago Cubs': 'CT', 'Chicago White Sox': 'CT',
    'Milwaukee Brewers': 'CT', 'Minnesota Twins': 'CT', 'Kansas City Royals': 'CT',
    'St. Louis Cardinals': 'CT', 'Houston Astros': 'CT', 'Texas Rangers': 'CT',
    'Colorado Rockies': 'MT', 'Arizona Diamondbacks': 'MT',
    'Los Angeles Dodgers': 'PT', 'Los Angeles Angels': 'PT', 'Athletics': 'PT',
    'San Francisco Giants': 'PT', 'San Diego Padres': 'PT',
    'Seattle Mariners': 'PT', 'Oakland Athletics': 'PT',
}
TZ_OFFSET = {'ET': 0, 'CT': 1, 'MT': 2, 'PT': 3}

def haversine(coord1, coord2):
    """Calculate distance in miles between two lat/lon coordinates"""
    R = 3958.8  # Earth radius in miles
    lat1, lon1 = radians(coord1[0]), radians(coord1[1])
    lat2, lon2 = radians(coord2[0]), radians(coord2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def get_team_schedule_features(team_name, game_date):
    """Fetch last 5 games for a team and calculate run diff, consecutive road games, days since last home game"""
    try:
        # Get MLB team ID
        teams_resp = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=15)
        team_last = team_name.split(' ')[-1].lower()
        mlb_team = None
        for t in teams_resp.json().get('teams', []):
            if t.get('name', '').lower().endswith(team_last) or team_last in t.get('name', '').lower():
                mlb_team = t
                break
        if not mlb_team:
            return None, None, None, None

        team_id = mlb_team['id']
        end_date = game_date
        start_date = (datetime.strptime(game_date, '%Y-%m-%d') - timedelta(days=30)).strftime('%Y-%m-%d')

        sched_resp = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={'teamId': team_id, 'sportId': 1, 'startDate': start_date, 'endDate': end_date, 'hydrate': 'linescore', 'gameType': 'R'},
            timeout=15
        )

        all_games = []
        for d in sched_resp.json().get('dates', []):
            for g in d.get('games', []):
                if g.get('status', {}).get('detailedState') == 'Final':
                    all_games.append(g)

        # Sort by date descending
        all_games.sort(key=lambda g: g.get('gameDate', ''), reverse=True)

        # Last 5 run differential
        run_diff = 0
        for g in all_games[:5]:
            ls = g.get('linescore', {})
            home_runs = ls.get('teams', {}).get('home', {}).get('runs', 0) or 0
            away_runs = ls.get('teams', {}).get('away', {}).get('runs', 0) or 0
            is_home = g.get('teams', {}).get('home', {}).get('team', {}).get('id') == team_id
            if is_home:
                run_diff += (home_runs - away_runs)
            else:
                run_diff += (away_runs - home_runs)

        last5_run_diff = round(run_diff, 1) if len(all_games) >= 1 else None

        # Days since last home game
        days_since_home = None
        for g in all_games:
            is_home = g.get('teams', {}).get('home', {}).get('team', {}).get('id') == team_id
            if is_home:
                last_home_date = g.get('gameDate', '')[:10]
                try:
                    days_since_home = (datetime.strptime(game_date, '%Y-%m-%d') - datetime.strptime(last_home_date, '%Y-%m-%d')).days
                except:
                    pass
                break

        # Consecutive road games (counting backwards)
        consec_road = 0
        for g in all_games:
            is_home = g.get('teams', {}).get('home', {}).get('team', {}).get('id') == team_id
            if is_home:
                break
            consec_road += 1

        # Last game venue name (for travel distance)
        last_venue = None
        if all_games:
            last_venue = all_games[0].get('venue', {}).get('name')

        return last5_run_diff, days_since_home, consec_road, last_venue
    except Exception as e:
        print(f"  ⚠️ Schedule features error for {team_name}: {e}")
        return None, None, None, None

def get_weather(venue, lat, lon):
    if venue in DOME_VENUES:
        return {"temperature": 72, "wind_speed": 0, "wind_direction": "N/A", "precipitation": 0, "is_dome": True}
    try:
        r = requests.get(
            f"https://api.openweathermap.org/data/2.5/weather",
            params={"lat": lat, "lon": lon, "appid": WEATHER_API_KEY, "units": "imperial"}
        )
        data = r.json()
        main_data = data.get("main", {})
        if not main_data:
            print(f"  Weather API no 'main' data for {venue}: {data.get('message', 'unknown')}")
            return {"temperature": 70, "wind_speed": 5, "wind_direction": "N", "precipitation": 0, "is_dome": False}
        wind_data = data.get("wind", {})
        wind_deg = wind_data.get("deg", 0)
        wind_speed = round(wind_data.get("speed", 0))
        directions = ["N","NE","E","SE","S","SW","W","NW"]
        wind_dir = directions[round(wind_deg/45) % 8]
        return {
            "temperature": round(main_data.get("temp", 70)),
            "wind_speed": wind_speed,
            "wind_direction": wind_dir,
            "precipitation": data.get("rain", {}).get("1h", 0),
            "is_dome": False
        }
    except Exception as e:
        print(f"Weather error for {venue}: {e}")
        return {"temperature": 70, "wind_speed": 5, "wind_direction": "N", "precipitation": 0, "is_dome": False}


def get_weather_forecast(venue, lat, lon, kickoff_utc):
    """Rain probability at game kickoff using OpenWeatherMap 5-day/3-hour forecast.

    2026-07-22 — added after 7/21 postponement wiped POTD + STRONG YRFI + SKIP
    prop in a single night (13% of slate + 3 headline picks). Play-of-day
    should downweight games with high rain probability at kickoff.

    Returns {"rain_prob_at_kickoff": float 0-1, "rain_risk_flag": bool}.
    Dome games always return (0.0, False). Missing key or forecast → falls
    back to (0.0, False) so pipeline never blocks on weather API issues.
    """
    if venue in DOME_VENUES:
        return {"rain_prob_at_kickoff": 0.0, "rain_risk_flag": False}
    if not kickoff_utc:
        return {"rain_prob_at_kickoff": 0.0, "rain_risk_flag": False}
    try:
        kickoff_dt = kickoff_utc if isinstance(kickoff_utc, datetime) else \
            datetime.fromisoformat(str(kickoff_utc).replace('Z', '+00:00'))
        # Free-tier 5-day/3-hour forecast endpoint
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"lat": lat, "lon": lon, "appid": WEATHER_API_KEY, "units": "imperial"},
            timeout=8,
        )
        data = r.json()
        if not data or not data.get("list"):
            return {"rain_prob_at_kickoff": 0.0, "rain_risk_flag": False}
        # Find the 3-hour block whose dt is closest to kickoff (each block is
        # 3 hours wide, so max distance to nearest block is ~90 min)
        kickoff_ts = kickoff_dt.replace(tzinfo=timezone.utc).timestamp() \
            if kickoff_dt.tzinfo is None else kickoff_dt.timestamp()
        best = None
        best_gap = None
        for block in data["list"]:
            bdt = block.get("dt")
            if bdt is None:
                continue
            gap = abs(bdt - kickoff_ts)
            if best_gap is None or gap < best_gap:
                best_gap = gap
                best = block
        if best is None:
            return {"rain_prob_at_kickoff": 0.0, "rain_risk_flag": False}
        # pop = 0.0-1.0 probability of precipitation for that block
        pop = float(best.get("pop", 0.0) or 0.0)
        return {
            "rain_prob_at_kickoff": round(pop, 2),
            # 40% threshold — enough to postpone-risk POTD but not so tight
            # that every summer thunderstorm map lights up the whole slate.
            # Calibrate against actual postponement rate after 30 days of data.
            "rain_risk_flag": pop >= 0.4,
        }
    except Exception as e:
        print(f"Weather forecast error for {venue}: {e}")
        return {"rain_prob_at_kickoff": 0.0, "rain_risk_flag": False}


def get_mlb_games(target_date_et=None):
    """Pull Odds API games for the target ET date. Defaults to today ET when
    not provided. Pass an ET YYYY-MM-DD string to build the window around a
    different date — used by the afternoon `--date tomorrow` preview pass."""
    try:
        if target_date_et:
            # Build a UTC window that covers the entire ET date (4am UTC = 12am ET)
            day_start = datetime.strptime(target_date_et, '%Y-%m-%d')
            time_from = f"{day_start.strftime('%Y-%m-%d')}T04:00:00Z"
            time_to = f"{(day_start + timedelta(days=1)).strftime('%Y-%m-%d')}T03:59:59Z"
        else:
            time_from = f"{(datetime.now(timezone.utc) - timedelta(hours=5)).strftime('%Y-%m-%d')}T04:00:00Z"
            time_to = f"{(datetime.now(timezone.utc) - timedelta(hours=5) + timedelta(days=1)).strftime('%Y-%m-%d')}T03:59:59Z"
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "spreads,totals,h2h",
                "oddsFormat": "american",
                "bookmakers": "draftkings",
                "commenceTimeFrom": time_from,
                "commenceTimeTo": time_to,
            }
        )
        data = r.json()
        if not isinstance(data, list):
            print(f"Odds API returned unexpected response: {str(data)[:200]}")
            return []

        # Fetch F5 totals separately (alternate markets endpoint)
        try:
            r2 = requests.get(
                "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": "us",
                    "markets": "alternate_totals_1st_5_innings",
                    "oddsFormat": "american",
                    "bookmakers": "draftkings",
                    "commenceTimeFrom": time_from,
                    "commenceTimeTo": time_to,
                }
            )
            f5_data = r2.json()
            if isinstance(f5_data, list):
                # Merge F5 markets into main game data
                f5_by_id = {g["id"]: g for g in f5_data}
                for game in data:
                    f5_game = f5_by_id.get(game["id"])
                    if f5_game:
                        for bk in f5_game.get("bookmakers", []):
                            # Find matching bookmaker in main data
                            for main_bk in game.get("bookmakers", []):
                                if main_bk["key"] == bk["key"]:
                                    main_bk["markets"].extend(bk.get("markets", []))
                                    break
                print(f"F5 totals merged for {len(f5_by_id)} games")
            else:
                print(f"F5 totals not available: {str(f5_data)[:100]}")
        except Exception as e2:
            print(f"F5 totals fetch failed (non-critical): {e2}")

        return data
    except Exception as e:
        print(f"Odds API error: {e}")
        return []
def get_pitcher_last_outing(pitcher_id):
    """Fetch pitcher's last game pitch count and innings from MLB Stats API.

    2026-08-14: instrumented with Session B data-quality assertions. Every
    fetch validates:
      * splits list is non-empty
      * game_date ordering is ascending (verifies our splits[-1] = most
        recent assumption — this is the assertion that would have caught
        the 2026-08-13 bug day 1 if we'd had it)
      * IP within realistic range (0-9)
      * pitch count within realistic range (0-140)
    Failures log to data_quality_events but never raise — pipeline continues
    with best-effort value.
    """
    if not pitcher_id:
        return None
    try:
        from data_quality import DQ, get_range
        dq = DQ(source='game_context.py:get_pitcher_last_outing', sport='MLB')
        ctx_id = {'pitcher_id': pitcher_id}

        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
            params={"stats": "gameLog", "group": "pitching", "season": 2026},
            timeout=10
        )
        splits_wrapper = r.json().get("stats", [])
        if not dq.assert_non_empty(splits_wrapper, 'stats_wrapper',
                                    context=ctx_id, severity='info'):
            return None

        inner_splits = splits_wrapper[0].get("splits") if splits_wrapper else None
        if not dq.assert_non_empty(inner_splits, 'gamelog_splits',
                                    context=ctx_id, severity='info'):
            return None

        # 2026-08-14: THE ORDERING GUARD. Confirms MLB API returns splits
        # ascending (oldest → newest), which means splits[-1] is the most
        # recent game. If MLB ever flips this convention, we detect
        # immediately — no silent bug lasting months.
        dates = [s.get('date') for s in inner_splits if s.get('date')]
        dq.assert_ordering_asc(dates, 'gamelog_dates_ascending',
                                context={**ctx_id, 'n_games': len(dates)})

        # 2026-08-13 BUG FIX: MLB Stats API gameLog returns splits in
        # CHRONOLOGICAL ASCENDING order (oldest first). Index [-1] = most
        # recent. Bugfix history preserved above ordering assertion which
        # provides go-forward protection.
        last = inner_splits[-1]["stat"]

        pitches = int(last.get("numberOfPitches", 0) or 0)
        raw_ip = last.get("inningsPitched", "0")
        try:
            innings = float(raw_ip.replace('.1', '.33').replace('.2', '.67') or "0")
        except (AttributeError, ValueError):
            dq._log('fetch_shape', 'ip_parse',
                    f'inningsPitched not string: {raw_ip!r}',
                    severity='warn', context=ctx_id, sport='MLB')
            innings = 0.0
        earned_runs = int(last.get("earnedRuns", 0) or 0)

        # Range guards
        ip_rng = get_range('MLB', 'pitcher_ip_single_game')
        if ip_rng:
            dq.assert_range(innings, ip_rng[0], ip_rng[1],
                             'pitcher_last_outing.innings',
                             context={**ctx_id, 'value': innings, 'date': dates[-1] if dates else None})
        p_rng = get_range('MLB', 'pitcher_pitches_single_game')
        if p_rng:
            dq.assert_range(pitches, p_rng[0], p_rng[1],
                             'pitcher_last_outing.pitches',
                             context={**ctx_id, 'value': pitches})

        return {
            "pitches": pitches,
            "innings": innings,
            "earned_runs": earned_runs,
        }
    except Exception as e:
        # Preserve prior fail-open behavior; log unexpected exceptions via DQ
        try:
            from data_quality import DQ
            DQ(source='game_context.py:get_pitcher_last_outing', sport='MLB')._log(
                'fetch_shape', 'unhandled_exception',
                f'{type(e).__name__}: {str(e)[:200]}',
                severity='warn', context={'pitcher_id': pitcher_id})
        except Exception:
            pass
        return None

def get_pitcher_vs_team(pitcher_id, opponent_team_id):
    """Aggregate pitcher's per-game logs vs a specific team across recent seasons.

    The MLB Stats API `vsTeam`/`vsTeamTotal` splits return BATTER stats from the
    opposing team's perspective (avg, ops, obp) — they do NOT include era or
    inningsPitched fields. Prior implementation always returned None because
    of the `ip < 3` filter on a missing field. Fix: pull per-game pitching log
    via `stats=gameLog`, filter to games where opponent.id matches, sum ER/IP/K
    across 2025-2026 to compute opponent-specific ERA.
    """
    if not pitcher_id or not opponent_team_id:
        return None
    try:
        agg = {"er": 0, "ip": 0.0, "k": 0, "ab": 0, "hits": 0, "g": 0}
        # 2026-05-27 INCIDENT: previous lookback was only (2026, 2025) — 2
        # seasons. Matz had 2 starts vs BAL in that window with one hot
        # outing → DB stored 0.93 ERA / .200 BAA on 9.7 IP as "mastery."
        # His actual CAREER vs BAL: 11 starts, 38.3 IP, 4.23 ERA (league
        # average, NOT mastery). The 2-season window was masquerading
        # small-sample hot streaks as career trends and we publicly cited
        # one as a top play. Extended lookback to 5 seasons (2022-2026)
        # to capture veteran starters' full body of work against opponents.
        for season in (2026, 2025, 2024, 2023, 2022):
            r = requests.get(
                f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
                params={"stats": "gameLog", "group": "pitching", "season": season},
                timeout=10,
            )
            stats_block = r.json().get("stats", [])
            splits = stats_block[0].get("splits", []) if stats_block else []
            for sp in splits:
                if sp.get("opponent", {}).get("id") != opponent_team_id:
                    continue
                stat = sp.get("stat", {})
                ip_str = str(stat.get("inningsPitched", "0"))
                # MLB encodes 6.1 IP as 6.333, 6.2 IP as 6.667
                ip = float((ip_str.replace(".1", ".333").replace(".2", ".667")) or "0")
                agg["ip"] += ip
                agg["er"] += int(stat.get("earnedRuns", 0) or 0)
                agg["k"] += int(stat.get("strikeOuts", 0) or 0)
                agg["ab"] += int(stat.get("atBats", 0) or 0)
                agg["hits"] += int(stat.get("hits", 0) or 0)
                agg["g"] += 1
        # 2026-08-21 SOFTENED (per feedback_vs_team_gate_soften): return the
        # raw numbers regardless of sample size, but include mastery_reliable
        # flag so downstream label/prose gates can distinguish trustworthy
        # career signal from small-sample data.
        #
        # Prior behavior: hard null gate at ip<15 to prevent Matz-incident-
        # style mastery hallucination (2026-05-27: 9.7 IP → 0.93 ERA vs BAL
        # cited as "mastery," career actually 4.23). Effect: Cameron (11 IP,
        # 12 K vs DET → 9.82 K/9) had his ENTIRE vs-team signal suppressed,
        # even the K/9 rate stat that stabilizes fast.
        #
        # New behavior: surface the numbers. Downstream mastery LABEL gates
        # already exist (jerry_model.py:115 mastery_ip_gate=15,
        # cohort_features.py:149 mastery cohort ip>=15) so "mastery" prose
        # never fires below 15 IP regardless of source. Meanwhile signals
        # like pitcher_vs_team_ip_below_outs_line — which compute rate stats
        # and gate themselves on n_starts + sample — get the data they need.
        # Minimum floor kept at 3 IP to filter out relief-appearance noise.
        if agg["ip"] < 3:
            return None
        era = round((agg["er"] * 9.0) / agg["ip"], 2)
        avg = round(agg["hits"] / agg["ab"], 3) if agg["ab"] > 0 else 0.0
        return {
            "era_vs_team": era,
            "avg_vs_team": avg,
            "ip_vs_team": round(agg["ip"], 1),
            "k_vs_team": agg["k"],
            "n_starts_vs_team": agg["g"],
            "mastery_reliable": agg["ip"] >= 15,
        }
    except Exception:
        return None

def get_pitcher_vs_team_recent(pitcher_id, opponent_team_id, n_starts=3):
    """Pull pitcher's MOST RECENT n_starts appearances against a specific
    opponent and compute ERA / BAA / IP across just those starts.

    Different signal from get_pitcher_vs_team (which aggregates 5 seasons of
    career data with a 15-IP gate). Recent mastery captures whether the
    pitcher's CURRENT version is dominating or struggling against this opp
    specifically — useful when career mastery exists but the pitcher has
    deteriorated (or improved) significantly.

    Pulls same gameLog data, sorts by date descending, takes only the last
    n_starts vs this opp, returns aggregated stats with a 10-IP minimum
    gate (n_starts × ~3-5 IP typical → 10 IP floor catches noise).

    Returns dict {era, avg, ip, k, n_starts, latest_date} or None.

    Added 2026-05-30 per user direction: career mastery alone misses cases
    where a pitcher has faced this team recently and either dominated or
    been tagged outside their lifetime norm.
    """
    if not pitcher_id or not opponent_team_id:
        return None
    try:
        all_starts = []  # list of (date_str, stat_dict)
        for season in (2026, 2025, 2024):
            r = requests.get(
                f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
                params={"stats": "gameLog", "group": "pitching", "season": season},
                timeout=10,
            )
            stats_block = r.json().get("stats", [])
            splits = stats_block[0].get("splits", []) if stats_block else []
            for sp in splits:
                if sp.get("opponent", {}).get("id") != opponent_team_id:
                    continue
                date_str = sp.get("date") or sp.get("game", {}).get("officialDate") or ""
                all_starts.append((date_str, sp.get("stat", {})))
        # Sort descending by date, take last n
        all_starts.sort(key=lambda x: x[0], reverse=True)
        recent = all_starts[:n_starts]
        if not recent:
            return None
        agg = {"er": 0, "ip": 0.0, "k": 0, "ab": 0, "hits": 0}
        for _, stat in recent:
            ip_str = str(stat.get("inningsPitched", "0"))
            ip = float((ip_str.replace(".1", ".333").replace(".2", ".667")) or "0")
            agg["ip"] += ip
            agg["er"] += int(stat.get("earnedRuns", 0) or 0)
            agg["k"] += int(stat.get("strikeOuts", 0) or 0)
            agg["ab"] += int(stat.get("atBats", 0) or 0)
            agg["hits"] += int(stat.get("hits", 0) or 0)
        # 6-IP minimum gate (was 10; lowered 2026-07-23 after MC v2 ablation
        # showed mastery mult was firing on 0.00 of backtest games due to
        # gate blocking young pitcher matchups). MC multiplier clamps to
        # 0.80-1.20 so bad-sample risk is capped even at partial IP.
        # User-facing "mastery" copy still gates at 15 IP career via
        # get_pitcher_vs_team — this only affects internal MC input.
        if agg["ip"] < 6:
            return None
        return {
            "era_vs_team_recent": round((agg["er"] * 9.0) / agg["ip"], 2),
            "avg_vs_team_recent": round(agg["hits"] / agg["ab"], 3) if agg["ab"] > 0 else 0.0,
            "ip_vs_team_recent": round(agg["ip"], 1),
            "k_vs_team_recent": agg["k"],
            "n_starts_recent": len(recent),
            "latest_date_recent": recent[0][0] if recent else None,
        }
    except Exception:
        return None


def get_mlb_injuries(team_name):
    """Fetch injured players for a team from MLB Stats API"""
    if not team_name:
        return None
    try:
        teams_resp = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=10)
        team_last = team_name.split(' ')[-1].lower()
        mlb_team = next((t for t in teams_resp.json().get('teams', [])
            if t.get('name', '').lower().endswith(team_last) or team_last in t.get('name', '').lower()), None)
        if not mlb_team:
            return None

        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{mlb_team['id']}/roster",
            params={'rosterType': 'depthChart', 'season': 2026},
            timeout=10
        )
        # Get injured list
        il_resp = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{mlb_team['id']}/roster",
            params={'rosterType': 'active', 'season': 2026},
            timeout=10
        )
        active_ids = set()
        for p in il_resp.json().get('roster', []):
            active_ids.add(p.get('person', {}).get('id'))

        # Now check 40-man for guys NOT on active roster = likely IL
        full_resp = requests.get(
            f"https://statsapi.mlb.com/api/v1/teams/{mlb_team['id']}/roster/40Man",
            timeout=10
        )
        injured = []
        for p in full_resp.json().get('roster', []):
            pid = p.get('person', {}).get('id')
            status = p.get('status', {}).get('description', '')
            if pid not in active_ids and 'Injured' in status:
                injured.append({
                    'name': p.get('person', {}).get('fullName', ''),
                    'position': p.get('position', {}).get('abbreviation', ''),
                    'status': status,
                })

        if injured:
            return {
                'count': len(injured),
                'key_players': [p['name'] for p in injured if p['position'] in ['P', 'SP', 'RP', 'C', 'SS', 'CF', 'DH']],
                'all': injured,
                'summary': ', '.join([f"{p['name']} ({p['position']})" for p in injured[:5]]),
            }
        return {'count': 0, 'key_players': [], 'all': [], 'summary': 'No key injuries'}
    except:
        return None

def get_bullpen_usage(team_name, game_date):
    """Check how many bullpen arms were used in the last 3 days via boxscore"""
    if not team_name:
        return None
    try:
        teams_resp = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=10)
        team_last = team_name.split(' ')[-1].lower()
        mlb_team = next((t for t in teams_resp.json().get('teams', [])
            if t.get('name', '').lower().endswith(team_last) or team_last in t.get('name', '').lower()), None)
        if not mlb_team:
            return None

        team_id = mlb_team['id']
        end = game_date
        start = (datetime.strptime(game_date, '%Y-%m-%d') - timedelta(days=3)).strftime('%Y-%m-%d')

        # Get recent games
        sched = requests.get(
            'https://statsapi.mlb.com/api/v1/schedule',
            params={'teamId': team_id, 'sportId': 1, 'startDate': start, 'endDate': end, 'gameType': 'R'},
            timeout=10
        )
        total_relievers = 0
        games_played = 0
        for d in sched.json().get('dates', []):
            for g in d.get('games', []):
                if g.get('status', {}).get('detailedState') != 'Final':
                    continue
                game_pk = g.get('gamePk')
                if not game_pk:
                    continue
                # Fetch boxscore to count pitchers used
                try:
                    box = requests.get(
                        f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore',
                        timeout=10
                    )
                    box_data = box.json()
                    # Determine if team is home or away
                    is_home = g.get('teams', {}).get('home', {}).get('team', {}).get('id') == team_id
                    side = 'home' if is_home else 'away'
                    team_box = box_data.get('teams', {}).get(side, {})
                    pitcher_ids = team_box.get('pitchers', [])
                    if len(pitcher_ids) > 1:
                        total_relievers += len(pitcher_ids) - 1  # subtract starter
                    games_played += 1
                except:
                    games_played += 1
        return {
            "relievers_used_3d": total_relievers,
            "games_last_3d": games_played,
            "avg_relievers": round(total_relievers / max(games_played, 1), 1),
        }
    except:
        return None

def get_bullpen_stats(team_name):
    """Look up bullpen ERA from Supabase"""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_bullpen_stats?team=eq.{requests.utils.quote(team_name)}&select=*&limit=1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
        )
        data = r.json()
        return data[0] if data else None
    except:
        return None

def get_pitcher_first_inning(pitcher_name):
    """Fetch pitcher's first inning splits from mlb_pitcher_stats"""
    if not pitcher_name:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_pitcher_stats?player_name=ilike.*{requests.utils.quote(pitcher_name.split(' ')[-1])}*&select=first_inning_era,first_inning_whip,first_inning_avg,first_inning_ip&limit=1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
        )
        data = r.json()
        if data and len(data) > 0 and data[0].get('first_inning_era') is not None:
            return data[0]
        return None
    except:
        return None

_L10_RECORD_CACHE = {}


def get_team_l10_record(team_name, as_of_date=None):
    """Compute team's L10 W-L from mlb_game_results, looking back from
    as_of_date (defaults to today ET). Per-process cached so a cron pass
    doesn't re-query for every game.

    Returns dict {'wins': int, 'losses': int, 'games_played': int,
                  'l10_win_pct': float} or None on failure.

    Built 2026-05-30 per user direction. Different signal from offense_drift:
    drift is offensive-only (R/G delta), L10 W-L captures pitching + clutch +
    overall team momentum. Used by Jerry Model's momentum multiplier.
    """
    if not team_name:
        return None
    if as_of_date is None:
        as_of_date = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')
    key = (team_name, as_of_date)
    if key in _L10_RECORD_CACHE:
        return _L10_RECORD_CACHE[key]
    try:
        qt = requests.utils.quote(team_name)
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results?or=(home_team.eq.{qt},away_team.eq.{qt})"
            f"&game_date=lt.{as_of_date}&home_score=not.is.null"
            f"&select=game_date,home_team,away_team,home_score,away_score"
            f"&order=game_date.desc&limit=10",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=15,
        )
        games = r.json() if r.status_code == 200 else []
        w = l = 0
        for g in games:
            hs, ascore = g.get('home_score'), g.get('away_score')
            if hs is None or ascore is None or hs == ascore:
                continue
            if g.get('home_team') == team_name:
                if hs > ascore: w += 1
                else: l += 1
            elif g.get('away_team') == team_name:
                if ascore > hs: w += 1
                else: l += 1
        total = w + l
        result = {
            'wins': w, 'losses': l, 'games_played': total,
            'l10_win_pct': round(w / total, 3) if total > 0 else None,
        }
        _L10_RECORD_CACHE[key] = result
        return result
    except Exception:
        return None


def get_team_woba_wrc(team_name):
    """Look up team wOBA and wRC+ from Supabase mlb_team_offense table"""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_team_offense?team=eq.{requests.utils.quote(team_name)}&select=*&limit=1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
        )
        data = r.json()
        return data[0] if data else None
    except:
        return None


_V4_OVER_SUPPRESSED_CACHE = None
_V4_OVER_CALL_RATE_CACHE = None


def _compose_ensemble_sub(md) -> str:
    """Reader-friendly 1-liner for the ensemble pick's `sub` field.

    Pulls the top-3 contributions' display_prose. Falls back to signal
    key if a source didn't set display_prose. Never leaks internal names.
    2026-08-22: sentence-case every chip so bulleted list looks consistent
    (mix of "Fade: Home ..." and "K-friendly ump" was reading sloppy)."""
    supporting = sorted(
        [c for c in md.contributions if c.side == md.pick and c.contribution > 0],
        key=lambda c: -c.contribution,
    )
    if not supporting:
        return f'{md.display_label} — ensemble score {md.score:.2f}'
    top = supporting[:3]
    def _title(s: str) -> str:
        if not s: return s
        s = s.strip()
        # Fade: prefix is already handled upstream; don't double-case
        if s.startswith('Fade:'): return s
        if s and s[0].isalpha() and s[0].islower():
            return s[0].upper() + s[1:]
        return s
    parts = [_title(c.display_prose) for c in top if c.display_prose and not c.display_prose.startswith('_')]
    if not parts:
        return f'{md.display_label} — {len(supporting)} signals aligned'
    return f'{md.display_label}: ' + ' · '.join(parts)


def v4_over_call_rate_14d():
    """Return the fraction of the last 14 days' MLB games where v4 called
    OVER vs the close total. This is the CALL-FREQUENCY bias — separate
    from HIT-RATE bias tracked by is_v4_over_suppressed().

    2026-08-16 morning audit found v4 called OVER on 93% of 8/15 games,
    which even at neutral hit-rate is not useful signal — it's just
    "v4 always says OVER". This function surfaces that bias so play_of_day
    can dampen v4's OVER contribution more aggressively when the model
    is spraying OVER calls indiscriminately.

    Returns None if data unavailable. Cached per-run.
    """
    global _V4_OVER_CALL_RATE_CACHE
    if _V4_OVER_CALL_RATE_CACHE is not None:
        return _V4_OVER_CALL_RATE_CACHE
    try:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context"
            f"?game_date=gte.{cutoff}"
            f"&model_pred_total=not.is.null&close_total=not.is.null"
            f"&select=model_pred_total,close_total&limit=500",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10,
        )
        data = r.json() if r.status_code == 200 else []
        if not data:
            _V4_OVER_CALL_RATE_CACHE = None
            return None
        overs = sum(1 for row in data
                    if row.get('model_pred_total') is not None
                    and row.get('close_total') is not None
                    and float(row['model_pred_total']) > float(row['close_total']))
        rate = overs / len(data)
        _V4_OVER_CALL_RATE_CACHE = rate
        return rate
    except Exception:
        _V4_OVER_CALL_RATE_CACHE = None
        return None


def v4_over_bias_severe():
    """True when v4's 14d OVER call-rate exceeds 75% — model is spraying
    OVER calls without discrimination, so its OVER vote is uninformative.
    Play_of_day should drop v4's OVER contribution entirely when severe."""
    rate = v4_over_call_rate_14d()
    return rate is not None and rate > 0.75


def is_v4_over_suppressed():
    """Read the latest v4 over_suppressed flag from model_health.
    Cached per-run so we don't hit the DB once per game. Defaults to True
    (safe) if model_health table missing or read fails.

    Built 2026-05-24 to replace the hardcoded V4_OVER_SUPPRESSED constant
    with an auto-throttle. audit_v4_health.py flips this nightly based on
    the rolling 7d OVER hit rate (with hysteresis to avoid 50%-boundary
    flapping).
    """
    global _V4_OVER_SUPPRESSED_CACHE
    if _V4_OVER_SUPPRESSED_CACHE is not None:
        return _V4_OVER_SUPPRESSED_CACHE
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/model_health"
            f"?model_version=eq.v4&order=computed_date.desc&limit=1"
            f"&select=over_suppressed,computed_date",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10,
        )
        data = r.json() if r.status_code == 200 else None
        if data and data[0].get("over_suppressed") is not None:
            _V4_OVER_SUPPRESSED_CACHE = bool(data[0]["over_suppressed"])
            return _V4_OVER_SUPPRESSED_CACHE
    except Exception:
        pass
    _V4_OVER_SUPPRESSED_CACHE = True  # safe default
    return True


def fetch_h2h_recent(team, opponent):
    """Pull team-vs-opponent rolling H2H stats (populated by
    enrich_team_vs_opp.py). Returns dict with games_played, rpg_vs_opp,
    rpg_delta_vs_l14, or None if no record.

    Built 2026-05-24 — used by compute_confluence as an OVERRIDE signal
    when team-level recency contradicts opponent-specific recency.
    """
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_team_vs_opp_recent"
            f"?team=eq.{requests.utils.quote(team)}"
            f"&opponent=eq.{requests.utils.quote(opponent)}"
            f"&select=games_played,rpg_vs_opp,rpg_delta_vs_l14,ops,last_h2h_date&limit=1",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10,
        )
        data = r.json() if r.status_code == 200 else None
        return data[0] if data else None
    except Exception:
        return None

def calc_batting_order_weight(lineup_names):
    """
    Fetch individual batter OPS from MLB Stats API and compute
    a weighted lineup strength score. Top of order weighted more heavily.
    Returns (lineup_weight, lineup_ops_avg) tuple.
    lineup_weight: 0-10 scale, 6.0 = league average
    lineup_ops_avg: actual OPS average of confirmed batters
    """
    if not lineup_names:
        return None, None
    batters = [b.strip() for b in lineup_names.split(',') if b.strip()]
    if len(batters) < 3:
        return None, None

    # Position weights: top of order matters more for run production
    weights = [1.0, 0.95, 0.90, 0.75, 0.70, 0.65, 0.50, 0.45, 0.40]
    league_avg_ops = 0.710

    ops_values = []
    weighted_ops_sum = 0
    weight_sum = 0

    for i, batter in enumerate(batters[:9]):
        w = weights[i] if i < len(weights) else 0.35
        try:
            search_name = batter.encode('ascii', 'ignore').decode('ascii').strip()
            if len(search_name) < 3:
                continue
            r = requests.get(
                'https://statsapi.mlb.com/api/v1/people/search',
                params={'names': search_name, 'sportId': 1},
                timeout=8
            )
            people = r.json().get('people', [])
            if not people:
                continue
            pid = people[0]['id']
            sr = requests.get(
                f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                params={'stats': 'season', 'group': 'hitting', 'season': 2026},
                timeout=8
            )
            splits = sr.json().get('stats', [{}])[0].get('splits', [])
            if not splits:
                continue
            stat = splits[0].get('stat', {})
            ops = float(stat.get('ops', 0) or 0)
            pa = int(stat.get('plateAppearances', 0) or 0)
            if pa < 10 or ops == 0:
                continue
            ops_values.append(ops)
            weighted_ops_sum += ops * w
            weight_sum += w
        except:
            continue

    if len(ops_values) < 3 or weight_sum == 0:
        # Fallback to position-based weight if not enough stats
        fallback = sum(weights[i] for i in range(min(len(batters), 9)))
        return round(fallback, 2), None

    weighted_ops = weighted_ops_sum / weight_sum
    lineup_ops_avg = sum(ops_values) / len(ops_values)

    # Convert to 0-10 scale: league avg OPS (0.710) = 6.0
    # Every 0.050 OPS above/below shifts score by ~1.0
    lineup_weight = round(6.0 + (weighted_ops - league_avg_ops) / 0.050, 2)
    lineup_weight = max(2.0, min(10.0, lineup_weight))

    return lineup_weight, round(lineup_ops_avg, 3)

def detect_opener(pitcher_id):
    """Check if pitcher is likely an opener/bullpen day — low games started vs games played"""
    if not pitcher_id:
        return False
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats",
            params={"stats": "season", "group": "pitching", "season": 2026},
            timeout=10
        )
        splits = r.json().get("stats", [])
        if not splits or not splits[0].get("splits"):
            return False
        s = splits[0]["splits"][0]["stat"]
        gs = int(s.get("gamesStarted", 0) or 0)
        gp = int(s.get("gamesPlayed", 0) or 0)
        ip = float(s.get("inningsPitched", "0").replace('.1','.33').replace('.2','.67') or "0")
        # Opener signals: mostly relief appearances, or very low IP per start
        if gp > 0 and gs == 0:
            return True  # pure reliever being used as opener
        if gs > 0 and ip / gs < 3.5:
            return True  # averaging less than 3.5 IP per start — likely short opener
        return False
    except:
        return False

def calc_nrfi_score(home_pitcher_stats, away_pitcher_stats, home_days_rest, away_days_rest, temperature, wind_speed, wind_direction, park_run_factor, home_wrc_plus, away_wrc_plus, home_first_inn=None, away_first_inn=None, home_is_opener=False, away_is_opener=False, game_month=None, umpire_stats=None, home_inning_1_rpg=None, away_inning_1_rpg=None, home_pitcher_splits=None, away_pitcher_splits=None):
    """
    Calculate NRFI (No Run First Inning) probability score 0-100.
    Higher = stronger NRFI lean.

    umpire_stats (added 2026-04-30): dict with 'nrfi_rate' field.
    home/away_inning_1_rpg (added 2026-04-30): team's per-game runs scored
        in the 1st inning specifically (from mlb_team_offense). League avg
        ~0.5 R/G. Captures the offense side of the NRFI matchup that the
        prior pitcher-only formula missed.
    """
    score = 50  # neutral baseline

    # ── HOME PITCHER ──
    if home_pitcher_stats:
        raw_xera = sanitize_xera(home_pitcher_stats.get('xera'), 'home')
        xera = float(raw_xera if raw_xera is not None else 4.5)
        gb_pct = float(home_pitcher_stats.get('gb_pct', 0.42) or 0.42)
        raw_k = home_pitcher_stats.get('k_pct', 0.20) or 0.20
        k_pct = float(raw_k) if float(raw_k) <= 0.40 else 0.20  # cap at 40% — above is suspect
        whiff = float(home_pitcher_stats.get('whiff_rate', 0.25) or 0.25)

        # xERA signal — elite starter = strong NRFI lean
        if xera <= 3.00: score += 12
        elif xera <= 3.50: score += 8
        elif xera <= 4.00: score += 4
        elif xera >= 5.00: score -= 6
        elif xera >= 4.50: score -= 3

        # GB% — ground ball pitchers limit first inning damage
        if gb_pct >= 0.50: score += 6
        elif gb_pct >= 0.45: score += 3
        elif gb_pct <= 0.35: score -= 4

        # K% — high strikeout pitchers limit traffic
        if k_pct >= 0.28: score += 6
        elif k_pct >= 0.23: score += 3
        elif k_pct <= 0.15: score -= 4

    # ── AWAY PITCHER ──
    if away_pitcher_stats:
        raw_xera = sanitize_xera(away_pitcher_stats.get('xera'), 'away')
        xera = float(raw_xera if raw_xera is not None else 4.5)
        gb_pct = float(away_pitcher_stats.get('gb_pct', 0.42) or 0.42)
        raw_k = away_pitcher_stats.get('k_pct', 0.20) or 0.20
        k_pct = float(raw_k) if float(raw_k) <= 0.40 else 0.20  # cap at 40% — above is suspect

        if xera <= 3.00: score += 12
        elif xera <= 3.50: score += 8
        elif xera <= 4.00: score += 4
        elif xera >= 5.00: score -= 6
        elif xera >= 4.50: score -= 3

        if gb_pct >= 0.50: score += 6
        elif gb_pct >= 0.45: score += 3
        elif gb_pct <= 0.35: score -= 4

        if k_pct >= 0.28: score += 6
        elif k_pct >= 0.23: score += 3
        elif k_pct <= 0.15: score -= 4

    # ── DAYS REST ──
    # Fresh pitchers = best stuff in inning 1
    home_rest = int(home_days_rest or 4)
    away_rest = int(away_days_rest or 4)
    if home_rest >= 5 and away_rest >= 5: score += 6
    elif home_rest >= 5 or away_rest >= 5: score += 3
    elif home_rest <= 3 or away_rest <= 3: score -= 4

    # ── WEATHER ──
    temp = float(temperature or 72)
    wind = float(wind_speed or 0)
    wind_dir = (wind_direction or '').upper()

    # Temperature — calibrated from 140+ game outcomes:
    # 45 or below: 79.2% NRFI (strongest signal in dataset)
    # 46-55: 43.5% NRFI (WORSE than base rate — penalty)
    # 56-70: 41.2% NRFI (worst range — penalty)
    # 71+: 59.3% NRFI (above base rate — slight bonus)
    if temp <= 45: score += 15      # cold = dominant NRFI signal
    elif temp <= 55: score -= 4     # cool but not cold = danger zone
    elif temp <= 70: score -= 3     # mild = slight penalty
    elif temp >= 85: score -= 2     # hot = slight offense boost
    else: score += 2                # 71-84 = above base rate

    # Park factor available for compounding with weather
    park = float(park_run_factor or 100)

    # Wind — threshold lowered from 12 to 10mph (material lift for offense starts earlier
    # than historically modeled). Compound penalty at hitter-friendly parks (park >= 105).
    if wind >= 10:
        blowing_in = any(d in wind_dir for d in ['NNW', 'NNE', 'NW', 'NE', 'N', 'IN'])
        blowing_out = any(d in wind_dir for d in ['SSW', 'SSE', 'SW', 'SE', 'S', 'OUT'])
        if blowing_in:
            score += 8 if wind >= 15 else 5  # blowing in = suppresses offense
        elif blowing_out:
            base_penalty = 8 if wind >= 15 else 5
            if park >= 105:
                base_penalty += 3  # hitter park + wind out = compound offense signal
            score -= base_penalty

    # ── PARK FACTOR ── (recalibrated: range is now 80-133)
    if park <= 85: score += 8    # extreme pitcher park (Globe Life, KC)
    elif park <= 92: score += 5  # strong pitcher park
    elif park <= 97: score += 2
    elif park >= 120: score -= 8  # extreme hitter park (Coors)
    elif park >= 110: score -= 5  # strong hitter park
    elif park >= 105: score -= 2

    # ── OFFENSIVE QUALITY ──
    home_wrc = float(home_wrc_plus or 100)
    away_wrc = float(away_wrc_plus or 100)
    avg_wrc = (home_wrc + away_wrc) / 2
    if avg_wrc >= 115: score -= 8   # both elite offenses
    elif avg_wrc >= 108: score -= 4
    elif avg_wrc <= 88: score += 6  # both weak offenses
    elif avg_wrc <= 95: score += 3

    # ── FIRST INNING SPLITS ──
    # Pitcher's actual 1st inning ERA is the most direct NRFI signal
    if home_first_inn and home_first_inn.get('first_inning_era') is not None:
        fi_era = float(home_first_inn['first_inning_era'])
        if fi_era <= 1.50: score += 8     # elite 1st inning pitcher
        elif fi_era <= 3.00: score += 4
        elif fi_era >= 6.00: score -= 6   # gets hit early
        elif fi_era >= 4.50: score -= 3
        # WHIP reinforces — low traffic = no runs
        fi_whip = float(home_first_inn.get('first_inning_whip', 1.3) or 1.3)
        if fi_whip <= 0.90: score += 3
        elif fi_whip >= 1.60: score -= 3

    if away_first_inn and away_first_inn.get('first_inning_era') is not None:
        fi_era = float(away_first_inn['first_inning_era'])
        if fi_era <= 1.50: score += 8
        elif fi_era <= 3.00: score += 4
        elif fi_era >= 6.00: score -= 6
        elif fi_era >= 4.50: score -= 3
        fi_whip = float(away_first_inn.get('first_inning_whip', 1.3) or 1.3)
        if fi_whip <= 0.90: score += 3
        elif fi_whip >= 1.60: score -= 3

    # ── HOME ACE BONUS ──
    # If home pitcher xERA is 1.5+ better than away pitcher, boost NRFI
    if home_pitcher_stats and away_pitcher_stats:
        home_xera = sanitize_xera(home_pitcher_stats.get('xera'), 'home')
        away_xera = sanitize_xera(away_pitcher_stats.get('xera'), 'away')
        if home_xera and away_xera:
            xera_gap = abs(float(home_xera) - float(away_xera))
            worse_xera = max(float(home_xera), float(away_xera))
            better_xera = min(float(home_xera), float(away_xera))

            # Ace bonus — both arms elite or one dominant
            if (float(away_xera) - float(home_xera)) >= 1.5:
                score += 5

            # ── DUAL ARM GATE ──
            # Single liability arm penalty — one bad pitcher kills NRFI confidence
            # If one arm is good (<3.5) but other is bad (>4.5), penalize
            if better_xera <= 3.5 and worse_xera >= 4.5:
                penalty = min(12, round((worse_xera - 4.0) * 3))
                score -= penalty
            # Both arms bad — heavy penalty
            elif better_xera >= 4.5 and worse_xera >= 4.5:
                score -= 10
            # Both arms elite — bonus
            elif better_xera <= 3.5 and worse_xera <= 3.5:
                score += 5

    # ── MONTHLY ADJUSTMENT ──
    # 2025 data (2,401 games): Sept 44.4%, Aug 48.2% vs June 53.3%, March 55.4%
    # Late season fatigue + expanded rosters + motivation drops = more YRFI
    if game_month:
        m = int(game_month)
        if m == 9: score -= 5          # September: -5.4% vs baseline
        elif m == 8: score -= 2        # August: slight penalty
        elif m in (3, 4): score += 2   # Early season: pitchers fresh, lineups cold
        elif m == 6: score += 3        # June: peak NRFI month (53.3%)

    # ── OPENER / BULLPEN GAME DETECTION ──
    # Relievers used as openers are volatile — unpredictable first inning
    if home_is_opener:
        score -= 8
    if away_is_opener:
        score -= 8

    # ── V2 ADDITIONS WITH PRIME TIER GUARD (2026-04-30) ──
    # Backtest showed v2 additions (umpire + team 1st-inning offense) were
    # diluting the well-calibrated 90-94 PRIME tier (81.8% historical hit rate).
    # Solution: compute v2 adjustment, then GUARD the 90-94 boundary in BOTH
    # directions — never push a game INTO PRIME from outside, never push OUT
    # of PRIME from inside. Protects the proven tier; lets v2 refine other tiers.
    base_score = score
    v2_adj = 0
    if umpire_stats and umpire_stats.get('nrfi_rate') is not None:
        try:
            nr = float(umpire_stats['nrfi_rate'])
            v2_adj += max(-3, min(3, round((nr - 0.50) * 15)))
        except (TypeError, ValueError):
            pass
    LEAGUE_1ST_INN_RPG = 0.5
    for rpg in (home_inning_1_rpg, away_inning_1_rpg):
        if rpg is None:
            continue
        try:
            delta = float(rpg) - LEAGUE_1ST_INN_RPG
            v2_adj += max(-2, min(2, round(-delta * 8)))
        except (TypeError, ValueError):
            pass

    # Pitcher historical NRFI rate (added 2026-05-05). Each starter with
    # >=10 starts contributes ±3, capped. Sequencing + lineup-context
    # independence that 1st-inning ERA misses. update_pitcher_nrfi_rates()
    # populates mlb_pitcher_stats.nrfi_rate weekly.
    for pstats in (home_pitcher_stats, away_pitcher_stats):
        if not pstats:
            continue
        nrfi_rate = pstats.get('nrfi_rate')
        if nrfi_rate is None:
            continue
        try:
            v2_adj += max(-3, min(3, round((float(nrfi_rate) - 0.50) * 15)))
        except (TypeError, ValueError):
            pass

    # Home/away split (added 2026-05-05). Pitcher in favorable venue split
    # → push NRFI; unfavorable → push YRFI. Each pitcher capped at ±2;
    # both can combine to ±4. Splits live on get_pitcher_splits() dict
    # (home_era / away_era), passed in separately from pitcher_stats.
    for pstats, splits, is_home in (
        (home_pitcher_stats, home_pitcher_splits, True),
        (away_pitcher_stats, away_pitcher_splits, False),
    ):
        if not pstats or not splits:
            continue
        season = pstats.get('era')
        split_era = splits.get('home_era') if is_home else splits.get('away_era')
        if season is None or split_era is None:
            continue
        try:
            delta = float(split_era) - float(season)
            if delta <= -1.0:
                v2_adj += 2
            elif delta >= 1.0:
                v2_adj -= 2
        except (TypeError, ValueError):
            pass

    provisional = base_score + v2_adj
    base_in_prime = 90 <= base_score <= 94
    provisional_in_prime = 90 <= provisional <= 94

    if base_in_prime and not provisional_in_prime:
        # Don't let v2 push a calibrated PRIME game out of PRIME
        score = max(90, min(94, provisional))
    elif not base_in_prime and provisional_in_prime:
        # Don't let v2 push a non-PRIME game INTO PRIME (dilutes the tier)
        if base_score < 90:
            score = 89  # cap just below PRIME
        else:
            score = 95  # base was 95+ volatile, keep it there
    else:
        score = provisional

    return max(0, min(100, round(score)))

def get_park_factors(home_team):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_park_factors?team=eq.{requests.utils.quote(home_team)}&select=*",
            headers=headers
        )
        data = r.json()
        return data[0] if data else None
    except:
        return None

_AUDIT_CACHE = {}


def _audit_note_for(cohort_key):
    """Pull the latest 30d hit-rate string for a cohort from mlb_tier_calibration.

    Returns a short human-readable string like '68.8% on 16 games (30d)' or
    None if the cohort isn't yet calibrated. App renders this directly under
    'THE PLAY' so audit numbers stay server-driven (no hardcoded copy in
    app/index.tsx — added 2026-05-19 after user flagged stale '352 games'
    inflated audit copy).
    """
    if not cohort_key:
        return None
    if cohort_key in _AUDIT_CACHE:
        return _AUDIT_CACHE[cohort_key]
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_tier_calibration",
            params={
                'tier': f'eq.{cohort_key}',
                'window_label': 'eq.30d',
                'select': 'hit_rate,total,computed_date',
                'order': 'computed_date.desc',
                'limit': '1',
            },
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        if rows and (rows[0].get('total') or 0) >= 5:
            rate = float(rows[0].get('hit_rate') or 0)
            n = int(rows[0].get('total') or 0)
            note = f"{rate*100:.1f}% on {n} games (30d)"
            _AUDIT_CACHE[cohort_key] = note
            return note
    except Exception:
        pass
    _AUDIT_CACHE[cohort_key] = None
    return None


_COHORT_RATE_CACHE = {}


def _cohort_rate_n(cohort_key):
    """Return (hit_rate, total_n) tuple from mlb_tier_calibration 30d window.

    Same data source as _audit_note_for but exposes the numbers so we can
    gate primary-play surfacing on live cohort health, not just display the
    audit note next to a still-firing play.

    Added 2026-05-27 after the xERA-gap rule was surfacing LIGHT Over plays
    on a 37%-hit-rate cohort (n=32, well below break-even). The audit note
    said 37% but the play still fired — display contradicted recommendation.
    Returns (None, 0) on miss / error so callers can default to "no data, no
    suppression."""
    if not cohort_key:
        return (None, 0)
    if cohort_key in _COHORT_RATE_CACHE:
        return _COHORT_RATE_CACHE[cohort_key]
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_tier_calibration",
            params={
                'tier': f'eq.{cohort_key}',
                'window_label': 'eq.30d',
                'select': 'hit_rate,total',
                'order': 'computed_date.desc',
                'limit': '1',
            },
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        if rows:
            rate = float(rows[0].get('hit_rate') or 0)
            n = int(rows[0].get('total') or 0)
            _COHORT_RATE_CACHE[cohort_key] = (rate, n)
            return (rate, n)
    except Exception:
        pass
    _COHORT_RATE_CACHE[cohort_key] = (None, 0)
    return (None, 0)


def _cohort_healthy(cohort_key, min_rate=0.52, min_n=15):
    """Return True when a cohort is currently firing above the suppress floor.

    2026-08-12: raised floor from 0.48 → 0.52 (at break-even). Prior 0.48
    was letting coin-flip cohorts surface as LEAN recommendations. Real
    example: PHI @ STL 8/12 — xera_gap_2_3_over cohort at 50% n=36 fired
    LEAN OVER while Jerry + simulator both said UNDER. The 50% cohort
    hit rate is literally a coin flip — not worth recommending. Floor at
    0.52 ensures cohorts must at least clear break-even (-110 needs 52.4%)
    before we surface them.

    Returns True when n < min_n — insufficient sample, don't suppress on
    noise. Returns False when rate is non-null AND below floor AND sample
    meets min_n. Returns True for unknown cohorts (default-permissive)."""
    rate, n = _cohort_rate_n(cohort_key)
    if rate is None or n < min_n:
        return True
    return rate >= min_rate


# ── Jerry fallback cache (2026-08-07 GAP-PASS-badge fix) ───────────
# When compute_primary_play would return None, look up Jerry's directional
# read as a fallback so the app never shows a PASS badge on games where
# Jerry actually has a take. Cached per-date to avoid hammering PostgREST.
_JERRY_READS_CACHE: dict = {}


def _jerry_fallback_for_game(game_id: str, game_date: str,
                              home_team: Optional[str] = None,
                              away_team: Optional[str] = None) -> Optional[dict]:
    """Return a SOFT primary_play dict from Jerry's read for this game,
    or None if Jerry didn't have a directional take either.

    Uses per-date cache — first lookup fetches all reads for the date,
    subsequent lookups hit the cache. Silently no-ops on network errors.

    2026-08-09: pass home_team/away_team so ML labels render as team
    name (e.g. "Dodgers ML") not "Home ML" — user feedback.
    """
    if not game_id or not game_date: return None
    if not SUPABASE_URL or not SUPABASE_KEY: return None
    if game_date not in _JERRY_READS_CACHE:
        try:
            r = requests.get(
                f'{SUPABASE_URL}/rest/v1/jerry_reads',
                headers={'apikey': SUPABASE_KEY,
                         'Authorization': f'Bearer {SUPABASE_KEY}'},
                params={'game_date': f'eq.{game_date}',
                        'sport': 'eq.MLB',
                        'select': 'game_id,call_market,call_side,call_line,call_text,conviction'},
                timeout=8,
            )
            rows = r.json() if r.status_code == 200 else []
            _JERRY_READS_CACHE[game_date] = {row['game_id']: row for row in rows if row.get('game_id')}
        except Exception:
            _JERRY_READS_CACHE[game_date] = {}
    read = _JERRY_READS_CACHE.get(game_date, {}).get(game_id)
    if not read: return None
    market = (read.get('call_market') or '').lower()
    side = read.get('call_side')
    text = read.get('call_text') or ''
    line = read.get('call_line')
    conv = read.get('conviction') or 0

    # Skip if Jerry also said PASS or no directional call
    if market in ('', 'pass') or not side:
        return None
    # Map market to primary_play type
    type_map = {'ml': 'ml', 'rl': 'ml', 'total': 'total', 'prop': 'prop'}
    pp_type = type_map.get(market)
    if not pp_type: return None
    # 2026-08-15 morning-audit rule: Jerry MLB was 0-2 on ML conv=55 on 8/15
    # and the ML surface is under-water rolling. Raise the ML floor above
    # the total floor. Totals stayed profitable at conv 55; MLs did not.
    # PASS on ML fallbacks below 60 rather than shipping a soft take.
    if pp_type == 'ml' and conv < 60:
        return None
    # Build display label
    if market == 'total' and line is not None:
        label = f'{"Over" if side == "OVER" else "Under"} {line}'
    elif market in ('ml', 'rl'):
        side_upper = (side or '').upper()
        team = None
        if side_upper == 'HOME' and home_team:
            team = home_team
        elif side_upper == 'AWAY' and away_team:
            team = away_team
        label = f'{team} ML' if team else f'{side.title()} ML'
    else:
        label = text or f'{side}'
    # Tier maps: cap at LEAN since this is a fallback (primary path didn't
    # find PRIME/STRONG). App renders LEAN badge instead of PASS.
    tier = 'LEAN' if conv >= 50 else 'READ'
    return {
        'type': pp_type,
        'tier': tier,
        'label': label,
        'sub': f'Jerry fallback (conv {conv}) — primary path found no STRONG/PRIME edge',
        'signal_floor': 50 if tier == 'LEAN' else 30,
        'audit_note': 'jerry_read fallback · added 2026-08-07 to prevent PASS badge on games with directional Jerry take',
    }


def compute_primary_play(ctx):
    """Compute the headline primary-play recommendation for a game, server-side.

    Lives here (not in the app) so that all tier thresholds — confluence net,
    spread_delta, NRFI bands — can be tuned from Python without an App Store
    submission. App reads ctx.primary_play directly; no threshold logic in JS.

    Returns a dict (jsonb-friendly) or None if no qualifying play.
    Schema: {type, tier, label, sub, signal_floor, audit_note?}
      type: 'ml' | 'nrfi' | 'yrfi' | 'over' | None
      tier: 'PRIME' | 'STRONG' | 'LEAN' | None
      signal_floor: int — used for app's calcGameSweatScore floor
      audit_note: optional string — current 30d cohort hit rate for the
                  app to render. Sourced from mlb_tier_calibration so the
                  number stays fresh without app rebuilds.

    Mirrors prior in-app logic (now removed): hybrid PRIME ML requires both
    confluence ≥+4 AND |spread_delta| ≥2.0; STRONG requires ≥+2 AND ≥1.5.
    """
    nrfi = ctx.get('nrfi_score')
    conf = ctx.get('signal_confluence_net')
    # Prefer v4 (model_pred_spread) over v3 (projected_spread). v4 is the
    # XGBoost runs model; when present it beats v3 on direction. v3 stays as
    # fallback when v4 is suppressed (missing xERA, opp data, etc.). Audited
    # 2026-05-20 — prior v3-only logic would mis-fire when v4 disagreed.
    v4_spread = ctx.get('model_pred_spread')
    v3_spread = ctx.get('projected_spread')
    jerry_spread = ctx.get('jerry_pred_spread')
    # Prefer the WEIGHTED composite over raw v4 (7/22 audit fix).
    # Old behavior: proj_spread = v4 fallback v3. Problem: when v4 disagrees
    # with jerry+v3+confluence (which was ~75% of the 7/21 slate) delta
    # collapses below the 2.0 PRIME/STRONG gate and NO primary play fires.
    # New: blend via tier_discipline_gate.weighted_composite_spread (same
    # audit-backed formula the resolver uses). Falls back to v4→v3 if the
    # import isn't available (defensive, keeps offline scripts working).
    # Read panel_implied_margin if game_context has it pre-computed
    # (added 2026-07-22 — see 20260722_context_panel_implied.sql). Panel
    # was 72% on sides in 7/21 audit; with-panel composite hits 62% n=187.
    panel_margin_ctx = ctx.get('panel_implied_margin')
    try:
        from tier_discipline_gate import weighted_composite_spread
        composite = weighted_composite_spread(
            v3_spread, v4_spread, jerry_spread,
            panel_margin=panel_margin_ctx,
        )
        proj_spread = composite if composite is not None else (v4_spread if v4_spread is not None else v3_spread)
    except Exception:
        proj_spread = v4_spread if v4_spread is not None else v3_spread
    # Fall back to open_spread when close_spread isn't set yet. Lines barely
    # move during a normal day; using the open line as the comparison anchor
    # is far better than skipping primary-play computation entirely (the prior
    # behavior, which left every game with `primary_play = None` whenever the
    # 2pm cron hadn't run yet — see 2026-05-23 noon-run audit).
    close_spread = ctx.get('close_spread') or ctx.get('open_spread')
    home_ml = ctx.get('home_ml_odds') or ctx.get('home_ml_close') or ctx.get('home_ml_open')
    away_ml = ctx.get('away_ml_odds') or ctx.get('away_ml_close') or ctx.get('away_ml_open')
    home_team = ctx.get('home_team') or 'Home'
    away_team = ctx.get('away_team') or 'Away'

    # Recompute spread_delta from the preferred spread so |delta| gate is
    # consistent with the direction we're using. Stored ctx.spread_delta is
    # always v3-derived (set in upload_game_context line 1934) so we can't
    # trust it when v4 is the active spread.
    sd = None
    if proj_spread is not None and close_spread is not None:
        try:
            sd = round(float(proj_spread) + float(close_spread), 2)
        except (TypeError, ValueError):
            sd = ctx.get('spread_delta')
    else:
        sd = ctx.get('spread_delta')
    abs_delta = abs(float(sd)) if sd is not None else 0.0

    # ML auto-fade gate: only surface ML primary play when model agrees with
    # market direction (otherwise we're recommending an underdog or mixed cohort).
    def _ml_playable():
        if proj_spread is None:
            return False
        try:
            model_picks_home = float(proj_spread) > 0
        except (TypeError, ValueError):
            return False
        ml_market_picks_home = None
        rl_market_picks_home = None
        if home_ml is not None and away_ml is not None:
            try:
                ml_market_picks_home = float(home_ml) < float(away_ml)
            except (TypeError, ValueError):
                pass
        if close_spread is not None:
            try:
                rl_market_picks_home = float(close_spread) < 0
            except (TypeError, ValueError):
                pass
        # Mixed cohort = ML fav and RL fav are different teams → suppress
        if (ml_market_picks_home is not None and rl_market_picks_home is not None
                and ml_market_picks_home != rl_market_picks_home):
            return False
        market_picks_home = (ml_market_picks_home if ml_market_picks_home is not None
                             else rl_market_picks_home)
        if market_picks_home is None:
            return True
        return model_picks_home == market_picks_home

    ml_playable = _ml_playable()
    fav = home_team if (proj_spread is not None and float(proj_spread) > 0) else away_team

    # ─── ANTI-CONSENSUS FADE (added 2026-07-27) ───────────────────────
    # 30d audit finding: when MC agrees with exactly ONE stat lens
    # (Panel / Jerry / v3 / v4) and the other 3 stat lens all pick the
    # OPPOSITE side, the MC+partner side LOSES the ML at brutal rates:
    #   MC + Panel alone vs 3 others   : 1-9  (10%) n=10
    #   MC + Jerry alone vs 3 others   : 2-5  (29%) n=7
    #   MC + v3    alone vs 3 others   : 3-6  (33%) n=9
    #   MC + v4    alone vs 3 others   : 3-6  (33%) n=9
    #   AGGREGATE                      : 9-26 (26%) n=35
    #
    # Inverse: taking the 3-lens majority side hits 26/35 (74%). MC+Panel
    # alone is the strongest single trap (90% fade win) — worth a
    # dedicated STRONG tier. Other partner combos hit 67-71% inverse,
    # LEAN tier.
    #
    # Fires BEFORE all other tiers because it's a directional flag that
    # overrides the naive "MC likes this" reading — a heavy fav MC-HC
    # can still be a fade if the other lens all disagree.
    def _mc_side_of(margin):
        if margin is None: return None
        try: return 'H' if float(margin) > 0 else 'A'
        except: return None
    mc_probs = ctx.get('mc_probabilities') or {}
    mc_em = mc_probs.get('mc_expected_margin') if isinstance(mc_probs, dict) else None
    mc_side_val = _mc_side_of(mc_em)
    stat_lens_sides = {
        'panel': _mc_side_of(panel_margin_ctx),
        'jerry': _mc_side_of(jerry_spread),
        'v3':    _mc_side_of(v3_spread),
        'v4':    _mc_side_of(v4_spread),
    }
    if mc_side_val:
        # Which stat lens agree with MC?
        mc_partners = [k for k, v in stat_lens_sides.items() if v == mc_side_val]
        # Which stat lens dissent from MC?
        mc_dissenters = [k for k, v in stat_lens_sides.items() if v and v != mc_side_val]
        # Fire ONLY if exactly one partner + 3 dissenters (clean pattern)
        if len(mc_partners) == 1 and len(mc_dissenters) == 3:
            partner = mc_partners[0]
            fade_side_val = 'H' if mc_side_val == 'A' else 'A'
            fade_team = home_team if fade_side_val == 'H' else away_team
            # Juice check on fade side ML — same guard as ML consensus tier
            fade_ml = home_ml if fade_side_val == 'H' else away_ml
            juice_skip = False
            try:
                if fade_ml is not None and float(fade_ml) <= -220:
                    juice_skip = True
            except (TypeError, ValueError):
                pass
            if not juice_skip:
                # MC+Panel is the strongest trap → STRONG tier fade
                is_panel = partner == 'panel'
                tier = 'STRONG' if is_panel else 'LEAN'
                floor = 72 if is_panel else 62
                # 2026-08-13 (P3): label wording rewrite. Prior sub read
                # "ANTI-CONSENSUS FADE — MC likes X but only jerry agrees"
                # which had readers unsure which side was the pick vs the
                # fade. New copy leads with the PICK team explicitly.
                mc_team = home_team if mc_side_val == 'H' else away_team
                hit_rate_note = '90%' if is_panel else '68%'
                sample_note = 'n=10' if is_panel else 'n=25'
                return {
                    'type': 'ml',
                    'tier': tier,
                    'label': f'{fade_team} ML',
                    'sub': f'Take {fade_team}: 3 of 4 stat lens agree, MC+{partner} alone on {mc_team}. Fading the MC minority wins {hit_rate_note} ({sample_note}).',
                    'signal_floor': floor,
                    'audit_note': f'anti-consensus fade tier · aggregate 9-26 (26% inverse=74%) 30d',
                }

    # ─── MC HIGH-CONF headline (added 2026-07-25) ─────────────────────
    # MC HIGH-CONF chip fires when Monte Carlo simulator shows ≥80% win
    # prob AND ≥15pp gap vs market implied. Highest single-lens signal
    # in the stack. Fires ~2x per 15-game slate. When it fires, this is
    # the loudest thing the model saw — beats confluence for headline.
    #
    # Juice-fav trap gate (7/24 finding): if the MC-picked side is a
    # HEAVY favorite (ML ≤ -150), surface as ML play — NOT RL -1.5.
    # 45d data: heavy home favs cover -1.5 only 29% (n=24), light favs
    # -130/-149 only 40% (n=121). MC's 80% signal is win-prob strength,
    # NOT cover strength. See project_juice_fav_rl_trap_724.
    mc_hc_flag = ctx.get('mc_high_conf_flag')
    mc_hc_side = ctx.get('mc_high_conf_side')
    mc_hc_pct  = ctx.get('mc_high_conf_pct')
    if mc_hc_flag and mc_hc_side in ('HOME', 'AWAY') and mc_hc_pct is not None:
        winning_team = home_team if mc_hc_side == 'HOME' else away_team
        winning_ml = home_ml if mc_hc_side == 'HOME' else away_ml
        juice_note = ""
        try:
            if winning_ml is not None and float(winning_ml) <= -150:
                juice_note = " · ML play (juice fav — RL -1.5 covers only 29-40% historically)"
        except (TypeError, ValueError):
            pass

        # 2026-07-29 MC-HC lens-support gate ([[project_mc_hc_recalibration_729]]).
        # MC HIGH-CONF picks hit 40% (4-6) over last 10 fires per daily_grades —
        # dangerous to auto-PRIME when only MC agrees. Gate: require ≥4/5 of
        # {panel, jerry, v3, v4, conf} to point the same side. Below that,
        # downgrade PRIME → STRONG and note the downgrade in sub.
        mc_hc_side_letter = 'H' if mc_hc_side == 'HOME' else 'A'
        conf_side_letter = None
        if conf is not None:
            try:
                cn = int(conf)
                conf_side_letter = 'H' if cn > 0 else ('A' if cn < 0 else None)
            except (TypeError, ValueError):
                conf_side_letter = None
        # stat_lens_sides is computed above (line ~2189). Reuse.
        lens_supporting = sum(1 for k, v in stat_lens_sides.items()
                              if v == mc_hc_side_letter)
        if conf_side_letter == mc_hc_side_letter:
            lens_supporting += 1
        mc_gate_pass = lens_supporting >= 4  # 4 of 5 (4 stat + conf)

        # 2026-08-06 juice-band gate on top of the lens-support gate.
        # 60d audit: MC HIGH-CONF at MC80-89% × ML>-110 (thin/plus) hits
        # 42.9% (n=7) — market is fair-pricing what MC thinks is a huge
        # favorite, which means the market has a specific reason (matchup,
        # BP, weather). Trust market at fair odds even on loud MC.
        # Same trap on the heavy-fav side (ML<=-200) but that's already
        # gated downstream in generate_sweat_card.
        thin_juice_trap = False
        try:
            if winning_ml is not None and float(winning_ml) > -110:
                thin_juice_trap = True
        except (TypeError, ValueError):
            pass

        if mc_gate_pass and not thin_juice_trap:
            return {
                "type": "ml",
                "tier": "PRIME",
                "label": f"{winning_team} ML",
                "sub": f"MC HIGH-CONF: {mc_hc_pct*100:.0f}% win prob (sim on 10k){juice_note} · {lens_supporting}/5 lens confirm",
                "signal_floor": 88,
                "audit_note": "MC HIGH-CONF chip · lens-support gate passed",
            }
        elif mc_gate_pass and thin_juice_trap:
            # Lens support solid, but market fair-pricing a "huge fav" is a
            # trap signature. Demote to STRONG so it can still surface but
            # doesn't headline as PRIME.
            return {
                "type": "ml",
                "tier": "STRONG",
                "label": f"{winning_team} ML",
                "sub": (f"MC HIGH-CONF: {mc_hc_pct*100:.0f}% win prob{juice_note} · "
                        f"DOWNGRADED — market at {winning_ml:+d} is thin juice on a MC-loud fav "
                        f"(60d MC×thin-juice hits 43% n=7)"),
                "signal_floor": 72,
                "audit_note": "MC-HC juice-band gate 8/6 — thin juice on MC-loud fav is priced-in trap",
            }
        else:
            # Downgrade to STRONG — insufficient lens support flags a risk that
            # MC is a lone-loud signal (40% recent).
            return {
                "type": "ml",
                "tier": "STRONG",
                "label": f"{winning_team} ML",
                "sub": (f"MC HIGH-CONF: {mc_hc_pct*100:.0f}% win prob (sim on 10k){juice_note} · "
                        f"DOWNGRADED — only {lens_supporting}/5 lens agree (need 4)"),
                "signal_floor": 72,
                "audit_note": "MC-HC lens-support gate 7/29 — 40% recent hits w/o confirm",
            }

    # ─── Jerry + v4 direction agreement gate (added 2026-07-25) ───────
    # 7/24 audit: Jerry sides 12-3 (80%), v4 sides 12-3 (80%). When BOTH
    # agree on side direction, they're extremely accurate. When they
    # SPLIT (e.g., yesterday's MIL where Jerry HOME/v4 AWAY), PRIME tier
    # shouldn't fire — historical PRIME ML with split lenses hits worse.
    # This gate demotes PRIME → STRONG when Jerry + v4 direction disagree.
    def _jerry_v4_agree_direction():
        if jerry_spread is None or v4_spread is None:
            return None  # can't evaluate — don't gate
        try:
            j_dir = 1 if float(jerry_spread) > 0 else -1
            v_dir = 1 if float(v4_spread) > 0 else -1
            return j_dir == v_dir
        except (TypeError, ValueError):
            return None
    jerry_v4_align = _jerry_v4_agree_direction()

    # PRIME ML: confluence ≥+4 AND |delta| ≥2.0 (hybrid threshold)
    # 2026-05-27: gated on cohort health — see _cohort_healthy docstring.
    # 2026-07-25: also gated on Jerry+v4 direction agreement.
    if (conf is not None and int(conf) >= 4 and abs_delta >= 2.0 and ml_playable
            and jerry_v4_align is not False):
        if _cohort_healthy('confluence_prime_ge4'):
            agree_note = " · Jerry+v4 aligned" if jerry_v4_align else ""
            return {
                "type": "ml",
                "tier": "PRIME",
                "label": f"{fav} ML",
                "sub": f"PRIME confluence ({int(conf)} signals, {abs_delta:.1f} delta){agree_note}",
                "signal_floor": 85,
                "audit_note": _audit_note_for('confluence_prime_ge4'),
            }
    # Demote-to-STRONG path when Jerry+v4 split (would-have-been PRIME)
    if (conf is not None and int(conf) >= 4 and abs_delta >= 2.0 and ml_playable
            and jerry_v4_align is False):
        if _cohort_healthy('confluence_strong_2_3'):
            return {
                "type": "ml",
                "tier": "STRONG",
                "label": f"{fav} ML",
                "sub": f"STRONG (would be PRIME but Jerry+v4 split direction — {jerry_spread:+.1f} vs {v4_spread:+.1f})",
                "signal_floor": 72,
                "audit_note": "Jerry+v4 split gate — 7/24 audit says agree-only prints PRIME",
            }
    # NRFI/YRFI demoted 2026-05-30 — see project_nrfi_demotion. Audit
    # showed PRIME NRFI 90-94 hits 50% on n=22 / 30d (coinflip). ML/totals
    # now lead the headline; NRFI surfaces as supplementary only. PRIME
    # NRFI block REMOVED. STRONG NRFI block below requires a companion
    # signal (ace duel / cold weather / pitcher park / NRFI-friendly ump)
    # — bare NRFI 90-94 alone is not playable as primary anymore.
    h1 = ctx.get('home_first_inning_era')
    a1 = ctx.get('away_first_inning_era')
    try:
        max_fi = max(float(h1 or 0), float(a1 or 0))
    except (TypeError, ValueError):
        max_fi = 0.0
    # YRFI sweet spot (6.0-7.9 1st-inn ERA) audit was real — keeps STRONG tier
    # because it's a different cohort from NRFI sweet spot.
    if nrfi is not None and int(nrfi) <= 25 and 6.0 <= max_fi < 8.0:
        if _cohort_healthy('yrfi_lean_le40'):
            return {
                "type": "yrfi",
                "tier": "STRONG",
                "label": "YRFI",
                "sub": f"NRFI {int(nrfi)} + 1st-inn ERA {max_fi:.1f} (audit sweet spot)",
                "signal_floor": 72,
                "audit_note": _audit_note_for('yrfi_lean_le40'),
            }
    # STRONG ML: confluence ≥+2 AND |delta| ≥2.0
    # Raised from 1.5 to 2.0 on 2026-05-21 audit. spread_delta_1_5_2 cohort
    # (delta in 1.5-2.0 band) hits only 40-43% lifetime — a trap zone where
    # the model's pick LOSES more than it wins. spread_delta_ge2 cohort
    # hits 55-58%. Old threshold put STRONG picks square in the trap; new
    # threshold matches the cohort cliff. See project_spread_delta_trap_zone.
    #
    # 2026-07-29 |net|=3 TRAP DOWNGRADE ([[project_confluence_net3_trap_729]]):
    # Lifetime audit found |net|=3 hits 30.8% (n=26) — the worst bucket in the
    # entire confluence distribution. |net|=2 hits 58.3%, |net|=4 hits 75%.
    # Non-monotonic — the mid-tier cohorts (h2h_recent_home, bp_taxed, trend)
    # pull good signals to +3 by adding fade-direction votes. Downgrade
    # |net|=3 games one grade: STRONG → LEAN.
    if conf is not None and int(conf) >= 2 and abs_delta >= 2.0 and ml_playable:
        if _cohort_healthy('confluence_strong_2_3'):
            net3_trap = abs(int(conf)) == 3
            if net3_trap:
                return {
                    "type": "ml",
                    "tier": "LEAN",
                    "label": f"{fav} ML lean",
                    "sub": (f"LEAN (would be STRONG but |net|=3 trap bucket — "
                            f"30.8% hit rate historically, downgraded)"),
                    "signal_floor": 62,
                    "audit_note": "|net|=3 trap downgrade 7/29 · project_confluence_net3_trap_729",
                }
            return {
                "type": "ml",
                "tier": "STRONG",
                "label": f"{fav} ML lean",
                "sub": f"STRONG confluence ({int(conf)} signals, {abs_delta:.1f} delta)",
                "signal_floor": 70,
                "audit_note": _audit_note_for('confluence_strong_2_3'),
            }
    # OVER lean — prefer v4 model_pred_total when present. v4 went 7-1 on
    # totals 5/19 but sample is small, so use a conservative threshold
    # (≥2.5 LIGHT / ≥3.5 STRONG) until we have a dedicated v4-OVER cohort
    # audit. The prop-signal override layer already flips model_pred_total
    # downstream when PRIME/STRONG prop concentration disagrees, so this
    # path inherits that correction. Audited 2026-05-20: prior path only
    # read ctx.over_lean (v3-derived) — missed v4 PRIME edges entirely.
    ct = ctx.get('close_total') or ctx.get('open_total')
    # 2026-06-05 line-sanity guard. Yankees 6/4 incident: primary_play
    # was written as "Over 3.5 vs market 7" because at some earlier write
    # the line had been read incorrectly (likely the F5 line of ~3.5 leaked
    # into close_total). Reject the entire primary_play computation if the
    # line falls outside the plausible MLB full-game total range (5.5-13.5).
    # Better to surface no primary_play than a wrong one with a phantom line.
    if ct is not None:
        try:
            ct_f = float(ct)
            if ct_f < 5.5 or ct_f > 13.5:
                print(f"  ⚠️ primary_play skipped — line {ct_f} outside plausible range (5.5-13.5)")
                return None
        except (TypeError, ValueError):
            return None
    v4_total = ctx.get('model_pred_total')
    # ─── MC TOTAL LENS PROMOTED to primary (7/25) ─────────────────────
    # 7/24 audit: Composite 27% totals, v4 40%, Jerry 33%, MC 53% (best).
    # MC v2 rich simulator went 5/5 on 7/23. Even after 7/24 drop, MC
    # remains highest-hit-rate total lens over the sample.
    #
    # New primary total logic:
    #   1. Prefer MC mean_total when present
    #   2. Apply extrapolation cap: if |MC - line| > 3, DOWNGRADE (models
    #      tend to overshoot when outputs get extreme)
    #   3. Fall back to v4 only if MC is unavailable
    #
    # v4 OVER kept behind is_v4_over_suppressed() guard; v4 UNDER still
    # solid (55% 30d) as fallback path.
    mc = ctx.get('mc_probabilities') or {}
    mc_mean_total = mc.get('mc_mean_total') if isinstance(mc, dict) else None

    if mc_mean_total is not None and ct is not None:
        try:
            mc_delta = float(mc_mean_total) - float(ct)
        except (TypeError, ValueError):
            mc_delta = None
        abs_mc_delta = abs(mc_delta) if mc_delta is not None else 0
        # Tier ladder + extrapolation cap:
        #   2.0 <= abs_delta < 3.0  → LIGHT (real but modest edge)
        #   3.0 <= abs_delta <= 4.0 → STRONG (elite edge)
        #   abs_delta > 4.0         → LIGHT (extrapolation cap — MC
        #     overshoots at extreme deltas per 7/24 audit: ARI/WSH said
        #     14.6 actual 5, ATL/BAL said 6.9 actual 13).
        if mc_delta is not None and abs_mc_delta >= 2.0:
            extrapolation = abs_mc_delta > 4.0
            if extrapolation:
                tier = 'LEAN'  # 2026-08-07: was LIGHT, renamed to LEAN so app renders proper badge (not PASS default)
            elif abs_mc_delta >= 3.0:
                tier = 'STRONG'
            else:
                tier = 'LEAN'  # 2026-08-07: was LIGHT, renamed to LEAN so app renders proper badge (not PASS default)
            direction = 'over' if mc_delta > 0 else 'under'
            note = ' (extrap cap: |delta|>4 downgraded)' if extrapolation else ''
            return {
                "type": direction,
                "tier": tier,
                "label": f"{'Over' if direction=='over' else 'Under'} {ct}",
                "sub": f"MC simulator {mc_mean_total:.1f} vs line {ct} ({mc_delta:+.1f}){note}",
                "signal_floor": 72 if tier == 'STRONG' else 62,
                "audit_note": "MC v2 totals 53% (30d, best-lens); extrap cap fires when |delta|>4",
            }
    # v4 OVER suppression — auto-throttle as of 2026-05-24.
    # Was a hardcoded True; now reads model_health.over_suppressed which
    # is flipped nightly by audit_v4_health.py based on rolling 7d OVER
    # hit rate (with hysteresis: only lift when 7d >= 52%, only re-suppress
    # when 7d < 48%). Falls back to True if model_health unreadable.
    # Now used ONLY as fallback when MC unavailable (7/25 promotion).
    #
    # 2026-08-13 Jerry-disagreement gate (P1 fix): v4 30d OVER hit rate is
    # 40% (below break-even). When v4 and Jerry disagree on direction, defer
    # to Jerry — the Pirates/Marlins case where v4 said Over 8.0 (11.0 pred)
    # but Jerry said Under 8.0 (7.89 pred) shipped the wrong direction on
    # the primary_play tile. Now: if Jerry's total pred sits on the opposite
    # side of the line by >= 0.5 runs, skip v4 and let Jerry fallback own it.
    V4_OVER_SUPPRESSED = is_v4_over_suppressed()
    jerry_home_r = ctx.get('jerry_pred_home_runs')
    jerry_away_r = ctx.get('jerry_pred_away_runs')
    jerry_total_sum = None
    if jerry_home_r is not None and jerry_away_r is not None:
        try: jerry_total_sum = float(jerry_home_r) + float(jerry_away_r)
        except (TypeError, ValueError): pass
    if v4_total is not None and ct is not None:
        try:
            v4_delta = float(v4_total) - float(ct)
        except (TypeError, ValueError):
            v4_delta = None
        # Jerry-disagreement gate: skip v4 direction when Jerry firmly opposes
        jerry_delta = (jerry_total_sum - float(ct)) if jerry_total_sum is not None else None
        v4_over_jerry_under = (v4_delta is not None and v4_delta > 0 and
                                jerry_delta is not None and jerry_delta <= -0.5)
        v4_under_jerry_over = (v4_delta is not None and v4_delta < 0 and
                                jerry_delta is not None and jerry_delta >= 0.5)
        if v4_delta is not None and v4_delta >= 2.5 and not V4_OVER_SUPPRESSED and not v4_over_jerry_under:
            # Contradiction flag (P2): note when v4 wins BUT Jerry disagreed
            # (soft disagreement < 0.5 fell through the gate — record it so
            # the app can render a warning chip).
            contradiction = None
            if jerry_delta is not None and jerry_delta < v4_delta - 1.5:
                contradiction = f'Jerry pred {jerry_total_sum:.1f} vs v4 {v4_total:.1f} · Δ={v4_delta-jerry_delta:+.1f}'
            out = {
                "type": "over",
                "tier": "STRONG" if v4_delta >= 3.5 else "LEAN",
                "label": f"Over {ct}",
                "sub": f"v4 model {v4_total:.1f} vs line {ct} (+{v4_delta:.1f}) [fallback]",
                "signal_floor": 72 if v4_delta >= 3.5 else 62,
                "audit_note": "v4 fallback (MC unavailable) · v4 total 40% (30d)",
            }
            if contradiction: out['contradiction_flag'] = contradiction
            return out
        # UNDER side stays active — v4 UNDER picks audit at 55% (30d)
        if v4_delta is not None and v4_delta <= -2.5 and not v4_under_jerry_over:
            contradiction = None
            if jerry_delta is not None and jerry_delta > v4_delta + 1.5:
                contradiction = f'Jerry pred {jerry_total_sum:.1f} vs v4 {v4_total:.1f} · Δ={jerry_delta-v4_delta:+.1f}'
            out = {
                "type": "under",
                "tier": "STRONG" if v4_delta <= -3.5 else "LEAN",
                "label": f"Under {ct}",
                "sub": f"v4 model {v4_total:.1f} vs line {ct} ({v4_delta:+.1f}) [fallback]",
                "signal_floor": 72 if v4_delta <= -3.5 else 62,
                "audit_note": "v4 UNDER cohort 55.1% (30d, n=49)",
            }
            if contradiction: out['contradiction_flag'] = contradiction
            return out
    # Legacy OVER path — v3 xERA gap rule fired (fallback when v4 missing
    # OR v4 edge is in 1.5-2.5 soft zone — v3 confirmation required).
    #
    # 2026-05-27: added cohort-health gate. The xera_gap_2_3_over cohort
    # dropped to 37% on n=32 (vs lifetime 58.2% it was calibrated on);
    # this play was still firing even though the audit number contradicted
    # the recommendation. The _cohort_healthy gate suppresses when the
    # live 30d rate dips below 0.48 with n>=15 — i.e., the rule has been
    # losing money lately and shouldn't surface as a play.
    if ctx.get('over_lean') is True:
        if not _cohort_healthy('xera_gap_2_3_over'):
            return None  # cohort failing live — suppress until it recovers
        return {
            "type": "over",
            "tier": "LEAN",
            "label": f"Over {ct}" if ct else "Over",
            "sub": "xERA gap rule fired",
            "signal_floor": 60,
            "audit_note": _audit_note_for('xera_gap_2_3_over'),
        }

    # ─── ML CONSENSUS FALLBACK (added 2026-07-27) ─────────────────────
    # Fires when 5+/6 lens agree on ML winner but no lens has a big enough
    # spread edge to trigger the delta-gated tiers above. Real example:
    # CLE @ CIN 7/27 — Panel +0.14, Jerry +0.84, v3 +1.01, v4 +0.80, MC +1.17
    # all agreed CIN wins ML, confluence +1 agreed HOME → 5/6 lens on CIN.
    # But all deltas < 1.5 (none cover -1.5 line) so nothing fired.
    #
    # Backed by 30d audit: Jerry+MC ML-direction agreement hit 68% (n=34).
    # 5+/6 ML consensus is a strictly stronger signal.
    # Guard: skip if ML price is heavy juice (<= -220) — the juice-fav-RL-trap
    # memory shows heavy favs win outright but eat ROI on the ML price.
    home_ml_val = ctx.get('home_ml_close') or ctx.get('home_ml_odds')
    away_ml_val = ctx.get('away_ml_close') or ctx.get('away_ml_odds')
    if proj_spread is not None and close_spread is not None:
        # Count ML-direction agreement across lens
        margins = {
            'panel': panel_margin_ctx,
            'jerry': jerry_spread,
            'v3': v3_spread,
            'v4': v4_spread,
        }
        try:
            mc_em = _f((ctx.get('mc_probabilities') or {}).get('mc_expected_margin')) if isinstance(ctx.get('mc_probabilities'), dict) else None
            margins['mc'] = mc_em
        except Exception:
            pass
        # Positive margin = home wins outright
        try:
            composite_side = 'H' if float(proj_spread) > 0 else 'A'
        except (TypeError, ValueError):
            composite_side = None
        if composite_side:
            agree_count = 1  # composite counts as its own vote
            for name, m in margins.items():
                if m is None: continue
                try:
                    side = 'H' if float(m) > 0 else 'A'
                    if side == composite_side: agree_count += 1
                except (TypeError, ValueError):
                    pass
            # Confluence direction adds another vote
            if conf is not None:
                try:
                    conf_side = 'H' if int(conf) > 0 else 'A' if int(conf) < 0 else None
                    if conf_side and conf_side == composite_side:
                        agree_count += 1
                except (TypeError, ValueError):
                    pass
            # agree_count now out of ~6 (composite + up to 5 lens + confluence dir)
            # Total possible: composite + 5 lens + conf = 7. But composite is
            # derived from lens so we cap at 6 unique signals.
            winning_team = home_team if composite_side == 'H' else away_team
            winning_ml = home_ml_val if composite_side == 'H' else away_ml_val
            # Juice-fav skip
            juice_skip = False
            try:
                if winning_ml is not None and float(winning_ml) <= -220:
                    juice_skip = True
            except (TypeError, ValueError):
                pass
            if not juice_skip and agree_count >= 5:
                # STRONG at 6/6, LEAN at 5/6
                tier = 'STRONG' if agree_count >= 6 else 'LEAN'
                floor = 72 if tier == 'STRONG' else 62
                return {
                    'type': 'ml',
                    'tier': tier,
                    'label': f'{winning_team} ML',
                    'sub': f'ML consensus fallback — {agree_count}/6 lens agree on {winning_team} (small spread deltas but strong direction)',
                    'signal_floor': floor,
                    'audit_note': 'ML consensus tier · Jerry+MC-agree cohort 68% 30d (n=34) as baseline',
                }

    # ── 2026-08-07 GAP-PASS-badge fix ────────────────────────────────
    # Before returning None (which the app renders as PASS badge), try
    # Jerry's directional read as a fallback. If Jerry has a total /
    # ml / prop call, surface it as a LEAN primary_play so the app
    # renders a real tier instead of PASS.
    #
    # This is a display-hygiene fallback — the primary paths above
    # remain authoritative for STRONG/PRIME calls. We only fall
    # through to Jerry when no strong-edge play cleared.
    game_id = ctx.get('game_id')
    game_date = ctx.get('game_date')
    if game_id and game_date:
        jerry_fb = _jerry_fallback_for_game(
            game_id, game_date,
            home_team=ctx.get('home_team'),
            away_team=ctx.get('away_team'),
        )
        if jerry_fb: return jerry_fb
    return None


def upload_game_context(context, commence_time=None):
    """Upload game context to Supabase with line-lock semantics.

    Two protections to prevent overwriting good data with bad:
    1. STRIP NONE odds fields — when the morning run sends close_*=None or the
       afternoon run sends open_*=None, those would overwrite previously-stored
       values with NULL. Stripping the field from payload preserves DB value.
    2. PRE-GAME LOCK — once commence_time has passed, the Odds API starts
       returning live in-game spreads/totals (e.g. 15.5 spread when game is
       in progress 12-2). Don't let those overwrite the captured pre-game
       close_* values. Strip those fields after game start.

    NRFI score lock (existing): preserves first-locked NRFI score after 8am ET.
    """
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }

    # Panel-implied margin + total — 2026-07-22.
    # 7/21 audit: Panel = 72% on sides (8-3), best lens layer of the night.
    # Precomputing at context write time lets weighted_composite_spread use
    # the with-panel weight variant (62.0% n=187) instead of panel-free
    # (60.4% n=462). Formula mirrors backfill_panel_implied.compute_panel.
    try:
        _asp_er = context.get("away_pitcher_projected_er")
        _hsp_er = context.get("home_pitcher_projected_er")
        _asp_outs = context.get("away_pitcher_projected_outs")
        _hsp_outs = context.get("home_pitcher_projected_outs")
        _abp = context.get("away_bullpen_era")
        _hbp = context.get("home_bullpen_era")
        if _asp_er is not None and _hsp_er is not None:
            asp_er_f = float(_asp_er); hsp_er_f = float(_hsp_er)
            asp_outs_f = float(_asp_outs) if _asp_outs is not None else 15.0
            hsp_outs_f = float(_hsp_outs) if _hsp_outs is not None else 15.0
            abp_f = float(_abp) if _abp is not None else 4.10
            hbp_f = float(_hbp) if _hbp is not None else 4.10
            _away_bp_ip = max(0, 9 - asp_outs_f / 3)
            _home_bp_ip = max(0, 9 - hsp_outs_f / 3)
            _home_scores = asp_er_f + abp_f * _away_bp_ip / 9
            _away_scores = hsp_er_f + hbp_f * _home_bp_ip / 9
            context["panel_implied_total"] = round(_home_scores + _away_scores, 2)
            context["panel_implied_margin"] = round(_home_scores - _away_scores, 2)
    except (TypeError, ValueError) as e:
        # Missing/malformed input — leave panel cols null so composite falls
        # back to panel-free weights per weighted_composite_spread contract.
        pass

    # Compute server-side primary play (replaces in-app threshold logic).
    # App reads ctx.primary_play directly so tuning thresholds doesn't require
    # an App Store resubmission.
    try:
        # 2026-08-16 CUTOVER: ensemble_scorer v2 is the authority. Old
        # compute_primary_play kept as fallback when ensemble returns
        # nothing (e.g. registry / signal_sources not populated for a sport).
        # Backtest over 60d (n=170): 61.1% HR / +18.6% ROI, so ensemble
        # takes precedence.
        ensemble_pp = None
        try:
            from ensemble_scorer import score_game as _ensemble_score
            decision = _ensemble_score('MLB', context)
            if decision is not None:
                top = decision.top()
                if top.pick is not None:
                    # Convert MarketDecision -> primary_play dict format
                    # so downstream (app, sweat card, sharp, ladder) is unchanged.
                    sub = _compose_ensemble_sub(top)
                    ensemble_pp = {
                        'type': top.market,
                        'tier': top.tier,
                        'label': top.display_label,
                        'side': top.side,
                        'line': top.line,
                        'conviction': top.conviction,
                        'score': round(top.score, 2),
                        'sub': sub,
                        # 2026-08-22 caption honest about the [:8] slice below.
                        # Padres audit surfaced: caption said "12 sources"
                        # but _ensemble_sources array only had 8.
                        'audit_note': (
                            f'ensemble_scorer v2 · showing top {min(len(top.contributions), 8)}'
                            f' of {len(top.contributions)} sources · '
                            f'score={top.score:.2f} margin={top.margin:+.2f}'
                        ),
                        '_engine': 'ensemble_v2',
                        '_ensemble_sources': [
                            {'signal_key': c.signal_key, 'class': c.signal_class,
                             'side': c.side, 'weight': round(c.weight, 2),
                             'n': c.n, 'contribution': round(c.contribution, 2),
                             'prose': c.display_prose}
                            for c in top.contributions[:8]
                        ],
                        # Preserve secondary + tertiary market picks so downstream
                        # can surface them (Sweat Card multi-play, etc.)
                        '_ensemble_all_markets': {
                            'ml':    {'pick': decision.ml.pick, 'label': decision.ml.display_label,
                                      'tier': decision.ml.tier, 'conviction': decision.ml.conviction},
                            'rl':    {'pick': decision.rl.pick, 'label': decision.rl.display_label,
                                      'tier': decision.rl.tier, 'conviction': decision.rl.conviction},
                            'total': {'pick': decision.total.pick, 'label': decision.total.display_label,
                                      'tier': decision.total.tier, 'conviction': decision.total.conviction},
                        },
                        # 2026-08-21: signals that fired on the LOSING side of each
                        # market. Powers the "context chips" surface in game detail —
                        # e.g., Rockies ATS_cold_season fires FADE-home-spread but
                        # HOME_RL still won the RL market. Chip renders as
                        # informational context, not a pick. Empty array when no
                        # meaningful losing-side signals fired on that market.
                        '_losing_market_notes': [
                            {'market': md.market,
                             'losing_side': md.runner_up_side,
                             'top_signals': [
                                 {'signal_key': c.signal_key,
                                  'class': c.signal_class,
                                  'side': c.side,
                                  'contribution': round(c.contribution, 2),
                                  'prose': c.display_prose}
                                 for c in md.runner_up_contributions
                             ]}
                            for md in (decision.ml, decision.rl, decision.total)
                            if md.runner_up_contributions
                        ],
                    }
        except Exception as e:
            # Ensemble unavailable — fall back to old logic. Log once.
            pass

        if ensemble_pp is not None:
            context["primary_play"] = ensemble_pp
        else:
            # Fallback to legacy hand-tuned rule engine
            context["primary_play"] = compute_primary_play(context)
            context["primary_play"]["_engine"] = "legacy_compute_primary_play" if isinstance(context["primary_play"], dict) else None

        # 2026-08-23: OC-flip + MC-dissent extracted to defensive_gates.py so
        # recompute_primary_play.py can call the identical logic. Previously
        # both blocks lived inline here (~130 lines total); recompute path
        # bypassed them, letting Orioles PRIME 86 (MC 40%) + Mariners PRIME 84
        # (MC 35%) escape demotion. See defensive_gates.py for the full
        # empirical rationale on each gate. Order: OC flip runs FIRST (may
        # change pp.side), then MC dissent (which reads pp.side).
        from defensive_gates import apply_all_defensive_gates
        apply_all_defensive_gates(context.get("primary_play"), context)

        # 2026-08-22 SHARP-FADE SURFACING — DISABLED 2026-08-23.
        # This block auto-populated _losing_market_notes with a Fadereport
        # "sharp opposes this play" chip when FR strength >= 15pts. Full
        # audit next morning showed the notes would have been actively
        # HARMFUL to users on 8/22: FR-strong-opposing = 0-2, OC ≥20pp
        # opposing = 0-3. Sharp side went 0-5 while our picks went 5-0
        # on the same games. 30d source hit rates ALL below -110 breakeven
        # (OC 50.4% n=389, CZ 47.5% n=318, FR 45.1% n=144). The real +EV
        # signal is DISSENT_OC 30d = 67.6% (n=34), not blanket sharp-side
        # following.
        #
        # Disabled until reframed: instead of "sharp opposes us", the
        # future version should pull the source's actual hit-rate from
        # sharp_agreement_calibration and only fire when a proven-dissent
        # pattern (MAJ_when_CZ_dissents, 3_of_3_AGREE fade) applies. See
        # queued item: promote MC + FR to first-class signal_sources with
        # registry weights.
        if False:  # kill switch — logic preserved for the reframed version
            try:
                pp = context.get("primary_play")
                if pp and isinstance(pp, dict) and pp.get("_engine") == "ensemble_v2":
                    fr_rows = requests.get(
                        f"{SUPABASE_URL}/rest/v1/fadereport_signals",
                        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                        params={
                            "sport": "eq.MLB",
                            "snapshot_date": f"eq.{game_date_et}",
                            "home_team": f"eq.{home_team}",
                            "away_team": f"eq.{away_team}",
                            "select": "market,sharp_side_norm,strength_pts,strength_tier,"
                                      "bets_side_pct,money_side_pct,reasoning",
                        },
                        timeout=8,
                    )
                    fr = fr_rows.json() if fr_rows.status_code == 200 else []
                    all_mkts = pp.get("_ensemble_all_markets") or {}
                    our_picks = {
                        "ml":    (all_mkts.get("ml") or {}).get("pick"),
                        "rl":    (all_mkts.get("rl") or {}).get("pick"),
                        "total": (all_mkts.get("total") or {}).get("pick"),
                    }
                    def _side_of(pick):
                        if not pick: return None
                        p = str(pick).upper()
                        if p.startswith("HOME"): return "HOME"
                        if p.startswith("AWAY"): return "AWAY"
                        if p in ("OVER", "UNDER"): return p
                        return None
                    lmn = pp.get("_losing_market_notes")
                    if not isinstance(lmn, list): lmn = []
                    for row in fr:
                        mkt = str(row.get("market") or "").lower()
                        our_side = _side_of(our_picks.get(mkt))
                        sharp_side = str(row.get("sharp_side_norm") or "").upper()
                        strength = row.get("strength_pts") or 0
                        if (our_side and sharp_side and our_side != sharp_side
                                and float(strength) >= 15):
                            lmn.append({
                                "market": mkt,
                                "losing_side": sharp_side,
                                "top_signals": [{
                                    "signal_key": "fadereport_sharp_fade",
                                    "class": "sharp",
                                    "side": sharp_side,
                                    "contribution": round(float(strength) / 100.0, 2),
                                    "prose": (
                                        f"Sharp fade: FR strength {int(float(strength))} on {sharp_side} · "
                                        f"bets {row.get('bets_side_pct')}% / money {row.get('money_side_pct')}% "
                                        f"other side. {row.get('reasoning') or ''}"[:180]
                                    ),
                                }],
                            })
                    if lmn:
                        pp["_losing_market_notes"] = lmn
            except Exception:
                pass  # never block pipeline on external-signal surfacing

        # 2026-08-16 Bundle H: Playbook tier gate. Scan the primary_play's
        # sub/audit_note for any ANTI_VALIDATED signal names from the
        # registry. If PRIME earned solely on an ANTI signal, demote to
        # STRONG + append the audit reason. Silently no-ops when the
        # registry is empty or lookup fails — never blocks the pipeline.
        try:
            pp = context.get("primary_play")
            if pp and isinstance(pp, dict) and pp.get('tier') == 'PRIME':
                from signal_registry_lookup import signals_for_scope, is_anti_validated
                # Get all ANTI_VALIDATED signal names for this sport
                anti_names = [s['signal_name']
                              for s in signals_for_scope(sport='MLB',
                                                          min_tier='ANTI_VALIDATED')
                              if s.get('tier') == 'ANTI_VALIDATED']
                sub_txt = (pp.get('sub') or '') + ' ' + (pp.get('audit_note') or '')
                fired_anti = [n for n in anti_names if n and n in sub_txt]
                if fired_anti:
                    pp['tier'] = 'STRONG'
                    pp['_playbook_gate'] = 'PRIME_DOWNGRADE_ANTI_VALIDATED'
                    pp['_anti_signals'] = fired_anti
                    orig_sub = pp.get('sub') or ''
                    pp['sub'] = f"{orig_sub} [Playbook: PRIME demoted to STRONG — leaned on ANTI_VALIDATED {'/'.join(fired_anti)}]"
        except Exception:
            pass  # registry unavailable — leave primary_play untouched
        # 2026-08-12: defensive label normalization. compute_primary_play
        # sometimes emits "Home ML" / "Away ML" when team names weren't
        # available at compute-time OR when an intermediate path skips the
        # team-name substitution. Never let these generic labels ship to
        # the app. Rewrite from ctx.home_team / away_team.
        pp = context.get("primary_play")
        if pp and isinstance(pp, dict):
            label = pp.get('label') or ''
            home_t = context.get('home_team')
            away_t = context.get('away_team')
            if label == 'Home ML' and home_t:
                pp['label'] = f'{home_t} ML'
            elif label == 'Away ML' and away_t:
                pp['label'] = f'{away_t} ML'
            elif label == 'Home ML lean' and home_t:
                pp['label'] = f'{home_t} ML lean'
            elif label == 'Away ML lean' and away_t:
                pp['label'] = f'{away_t} ML lean'
        # Stamp computation time so the app can suppress stale renders.
        # See 20260604_primary_play_computed_at.sql + app stale-check.
        # When the cron writes a fresh play, this timestamp updates.
        # When the cron fails or skips a game (e.g., live-game preserve),
        # the timestamp remains old and the app suppresses display.
        context["primary_play_computed_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        print(f"  primary_play compute failed: {e}")
        context["primary_play"] = None
        context["primary_play_computed_at"] = None

    # NRFI score stability: lock after 8am ET run so tier classifications don't drift
    try:
        et_now = datetime.now(timezone.utc) - timedelta(hours=4)
        if et_now.hour >= 8 and context.get("nrfi_score") is not None:
            game_id = context.get("game_id")
            if game_id:
                check_headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
                check_r = requests.get(
                    f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_id=eq.{requests.utils.quote(game_id)}&select=nrfi_score",
                    headers=check_headers, timeout=10
                )
                existing = check_r.json()
                if existing and len(existing) > 0 and existing[0].get("nrfi_score") is not None:
                    locked_score = existing[0]["nrfi_score"]
                    if locked_score != context.get("nrfi_score"):
                        print(f"  🔒 NRFI locked at {locked_score} (new calc was {context.get('nrfi_score')})")
                        context["nrfi_score"] = locked_score
    except Exception as e:
        print(f"  NRFI lock check skipped: {e}")

    # === ODDS LINE LOCK ===
    # Step 1: detect game state from commence_time
    game_started = False
    if commence_time:
        try:
            game_dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
            game_started = datetime.now(timezone.utc) >= game_dt
        except Exception:
            game_started = False

    # Step 2: Strip None odds fields (don't overwrite existing DB values with NULL)
    odds_keys_all = [
        'open_spread', 'open_total', 'close_spread', 'close_total',
        'home_ml_open', 'away_ml_open', 'home_ml_close', 'away_ml_close',
    ]
    for k in list(odds_keys_all):
        if k in context and context[k] is None:
            del context[k]

    # Step 3: If game has started, strip close_* + live ML fields
    # (preserve last pre-game close values; don't write live in-game odds).
    #
    # BUT: if close_total/close_spread/home_ml_close were never captured
    # pre-game (e.g. game started before the 2pm cron ran — common for
    # 1pm-3pm ET first pitches), promote the morning open_* values to
    # close_* before stripping. That way downstream consumers (model-vs-
    # market deltas, recap card, sweat card) get a number to render
    # instead of perpetual NULL. The morning open IS the canonical
    # pre-game line for early games. Added 2026-06-07 after 12/15 games
    # on a 1-3pm ET start slate had NULL close_total post-cron.
    if game_started:
        # Look up existing DB state first — needed for the close_* backfill
        # below AND to know which keys we just promoted (so strip doesn't
        # wipe them).
        existing = {}
        try:
            game_id = context.get('game_id')
            if game_id:
                cur = requests.get(
                    f"{SUPABASE_URL}/rest/v1/mlb_game_context"
                    f"?game_id=eq.{game_id}"
                    f"&select=close_total,close_spread,home_ml_close,away_ml_close,open_total,open_spread,home_ml_open,away_ml_open",
                    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                    timeout=10,
                )
                if cur.status_code == 200 and cur.json():
                    existing = cur.json()[0]
        except Exception as e:
            print(f"  ⚠️ close_* DB state check skipped: {e}")

        # Promote morning open_* → close_* when close was never captured
        # pre-game. The morning open IS the canonical pre-game line for
        # games starting before the 2pm cron runs.
        promote_map = {
            'close_total': 'open_total',
            'close_spread': 'open_spread',
            'home_ml_close': 'home_ml_open',
            'away_ml_close': 'away_ml_open',
        }
        promoted = set()
        for close_k, open_k in promote_map.items():
            if existing.get(close_k) is None and existing.get(open_k) is not None:
                context[close_k] = existing[open_k]
                promoted.add(close_k)
        if promoted:
            print(f"  ↺ Game started w/ NULL close_* — promoted morning open to: {', '.join(sorted(promoted))}")

        live_keys = [
            'close_spread', 'close_total',
            'home_ml_close', 'away_ml_close',
            'home_ml_odds', 'away_ml_odds',  # these are the "latest" odds, would be live
        ]
        stripped = []
        for k in live_keys:
            if k in promoted:
                continue  # just promoted from open — let it land
            if k in context:
                del context[k]
                stripped.append(k)
        if stripped:
            print(f"  🔒 Pre-game odds locked (game started) — preserved: {', '.join(stripped)}")

    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/mlb_game_context?on_conflict=game_id",
        headers=headers,
        json=context
    )
    # Pre-migration / schema-cache-stale fallback (5/30, looped 5/30 PM).
    # PostgREST returns 400 with ONLY THE FIRST unknown column per
    # response. Original code retried once and died on the 2nd missing
    # column. Loop strip-and-retry up to 8 rounds so multi-column gaps
    # (e.g. all 8 recent-mastery columns missing on a stale schema cache)
    # don't leave the entire game un-upserted.
    candidate_strip_keys = (
        'jerry_pred_home_runs', 'jerry_pred_away_runs', 'jerry_pred_spread',
        'jerry_pred_total', 'jerry_components', 'jerry_weights_version',
        'home_l10_wins', 'home_l10_losses', 'away_l10_wins', 'away_l10_losses',
        'home_pitcher_vs_team_recent_era', 'away_pitcher_vs_team_recent_era',
        'home_pitcher_vs_team_recent_ip', 'away_pitcher_vs_team_recent_ip',
        'home_pitcher_vs_team_recent_baa', 'away_pitcher_vs_team_recent_baa',
        'home_pitcher_vs_team_recent_n_starts', 'away_pitcher_vs_team_recent_n_starts',
        # 2026-06-18 Phase 1 additions — strip if columns not yet migrated
        'data_completeness', 'model_confidence',
    )
    stripped_total = []
    retry_rounds = 0
    while r.status_code == 400 and retry_rounds < 8:
        round_stripped = []
        for k in candidate_strip_keys:
            if k in r.text and k in context:
                context.pop(k, None)
                round_stripped.append(k)
                stripped_total.append(k)
        if not round_stripped:
            break  # error isn't a missing-column issue we can fix
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context?on_conflict=game_id",
            headers=headers,
            json=context,
        )
        retry_rounds += 1
    if stripped_total and r.status_code in [200, 201, 204]:
        print(f"  ⚠️ game_context: stripped {len(stripped_total)} cols across {retry_rounds} retries — schema cache may be stale; run NOTIFY pgrst, 'reload schema' in Supabase")
    if r.status_code not in [200, 201, 204]:
        print(f"Upload failed {r.status_code}: {r.text}")

    # 2026-08-23 Wave 1b: snapshot primary_play on every successful publish.
    # game_context is the primary write path (recompute already snapshots on
    # its side under source='recompute'). Together they form the append-only
    # audit trail. Best-effort — never blocks the publish. See migration
    # 20260823_primary_play_snapshots_append_mode.sql.
    if r.status_code in [200, 201, 204]:
        pp = context.get("primary_play")
        if isinstance(pp, dict) and context.get("game_id"):
            snap = {
                "sport": "MLB",
                "game_date": context.get("game_date"),
                "game_id": context.get("game_id"),
                "snapshot_source": "card_lock",
                "home_team": context.get("home_team"),
                "away_team": context.get("away_team"),
                "primary_play": pp,
                "pick_type": pp.get("type"),
                "pick_label": pp.get("label"),
                "pick_side": pp.get("side"),
                "pick_line": pp.get("line"),
                "tier": pp.get("tier"),
                "conviction": pp.get("conviction"),
                "score": pp.get("score"),
            }
            try:
                sr = requests.post(
                    f"{SUPABASE_URL}/rest/v1/primary_play_snapshots",
                    headers={**headers, "Prefer": "return=minimal"},
                    json=snap, timeout=8,
                )
                if sr.status_code >= 400 and sr.status_code != 404:
                    print(f"  ⚠ snapshot write {sr.status_code}: {sr.text[:80]}")
            except Exception:
                pass  # never block publish on snapshot failure

    return r.status_code in [200, 201, 204]

def log_game_result(context):
    """Log pre-game data to mlb_game_results for XGBoost training.

    spread_delta hardening (2026-05-06): always re-derive from
    projected_spread + close_spread at write time. Earlier path trusted
    context.get('spread_delta') which could go stale relative to
    projected_spread when recalc_spreads.py rewrote one column without
    the other. Yankees 5/4 audit grade was wrong because of this mismatch
    (results row had spread_delta=-2.89 with projected_spread=+2.61 — math
    inconsistent). Deriving on every write keeps both columns coherent.
    """
    try:
        # Derive spread_delta consistently with projected_spread + close_spread
        _ps = context.get("projected_spread")
        _cs = context.get("close_spread") or context.get("open_spread")
        derived_spread_delta = None
        if _ps is not None and _cs is not None:
            try:
                derived_spread_delta = round(float(_ps) + float(_cs), 2)
            except (TypeError, ValueError):
                derived_spread_delta = context.get("spread_delta")
        else:
            derived_spread_delta = context.get("spread_delta")
        record = {
            "game_id": context.get("game_id"),
            "game_date": context.get("game_date"),
            "season": 2026,
            "home_team": context.get("home_team"),
            "away_team": context.get("away_team"),
            "venue": context.get("venue"),
            "dome_game": context.get("venue") in DOME_VENUES,
            "home_sp_name": context.get("home_pitcher"),
            "home_sp_hand": context.get("home_throws"),
            "home_sp_xera": context.get("home_sp_xera"),
            "home_sp_k_pct": sanitize_k_pct(float(context["pitcher_context"].split("K% ")[1].split("%")[0]), context.get("home_pitcher", '')) if context.get("pitcher_context") and "K% " in context.get("pitcher_context", "") else None,
            "home_sp_whiff_rate": float(context["pitcher_context"].split("whiff ")[1].split("%")[0]) if context.get("pitcher_context") and "whiff " in context.get("pitcher_context", "") else None,
            "home_sp_gb_pct": float(context["pitcher_context"].split("GB% ")[1].split("%")[0]) if context.get("pitcher_context") and "GB% " in context.get("pitcher_context", "") else None,
            "home_sp_days_rest": context.get("home_days_rest"),
            "away_sp_name": context.get("away_pitcher"),
            "away_sp_hand": context.get("away_throws"),
            "away_sp_xera": context.get("away_sp_xera"),
            "away_sp_k_pct": None,
            "away_sp_whiff_rate": None,
            "away_sp_gb_pct": None,
            "away_sp_days_rest": context.get("away_days_rest"),
            "home_runs_per_game": context.get("home_runs_per_game"),
            "away_runs_per_game": context.get("away_runs_per_game"),
            "home_ops": context.get("home_ops"),
            "away_ops": context.get("away_ops"),
            "home_team_k_pct": context.get("home_team_k_pct"),
            "away_team_k_pct": context.get("away_team_k_pct"),
            "home_k_gap": context.get("home_k_gap"),
            "away_k_gap": context.get("away_k_gap"),
            "home_woba": context.get("home_woba"),
            "away_woba": context.get("away_woba"),
            "home_wrc_plus": context.get("home_wrc_plus"),
            "away_wrc_plus": context.get("away_wrc_plus"),
            "home_platoon_advantage": context.get("home_platoon_advantage"),
            "away_platoon_advantage": context.get("away_platoon_advantage"),
            "home_platoon_note": context.get("home_platoon_note"),
            "away_platoon_note": context.get("away_platoon_note"),
            "home_wrc_vs_opp_hand": context.get("home_wrc_vs_opp_hand"),
            "away_wrc_vs_opp_hand": context.get("away_wrc_vs_opp_hand"),
            "home_ops_vs_opp_hand": context.get("home_ops_vs_opp_hand"),
            "away_ops_vs_opp_hand": context.get("away_ops_vs_opp_hand"),
            "home_pitcher_last_3_era": context.get("home_pitcher_last_3_era"),
            "away_pitcher_last_3_era": context.get("away_pitcher_last_3_era"),
            "home_pitcher_last_3_k_pct": context.get("home_pitcher_last_3_k_pct"),
            "away_pitcher_last_3_k_pct": context.get("away_pitcher_last_3_k_pct"),
            "home_team_oaa": context.get("home_team_oaa"),
            "away_team_oaa": context.get("away_team_oaa"),
            "home_team_xwoba": context.get("home_team_xwoba"),
            "away_team_xwoba": context.get("away_team_xwoba"),
            "home_team_barrel_pct": context.get("home_team_barrel_pct"),
            "away_team_barrel_pct": context.get("away_team_barrel_pct"),
            "home_catcher_framing": context.get("home_catcher_framing"),
            "away_catcher_framing": context.get("away_catcher_framing"),
            "stats_snapshot_date": context.get("stats_snapshot_date"),
            "home_lineup_weight": context.get("home_lineup_weight"),
            "away_lineup_weight": context.get("away_lineup_weight"),
            "home_bullpen_era": context.get("home_bullpen_era"),
            "away_bullpen_era": context.get("away_bullpen_era"),
            "home_last_pitch_count": context.get("home_last_pitch_count"),
            "away_last_pitch_count": context.get("away_last_pitch_count"),
            "home_pitcher_vs_team_era": context.get("home_pitcher_vs_team_era"),
            "away_pitcher_vs_team_era": context.get("away_pitcher_vs_team_era"),
            # IP added 2026-07-23 — historical pipeline gap where era was
            # copied to results but ip was dropped, breaking MC mastery
            # sample-size gate. Now writes both so future backfills fire
            # the mastery mult with full sample confidence.
            "home_pitcher_vs_team_ip": context.get("home_pitcher_vs_team_ip"),
            "away_pitcher_vs_team_ip": context.get("away_pitcher_vs_team_ip"),
            "home_bp_relievers_3d": context.get("home_bp_relievers_3d"),
            "away_bp_relievers_3d": context.get("away_bp_relievers_3d"),
            "home_injury_count": context.get("home_injury_count"),
            "away_injury_count": context.get("away_injury_count"),
            "home_injury_summary": context.get("home_injury_summary"),
            "away_injury_summary": context.get("away_injury_summary"),
            "park_run_factor": context.get("park_run_factor"),
            "temperature": context.get("temperature"),
            "wind_mph": context.get("wind_speed"),
            "wind_direction": context.get("wind_direction"),
            "umpire": context.get("umpire"),
            "umpire_note": context.get("umpire_note"),
            "projected_total": context.get("projected_total"),
            "over_lean": context.get("over_lean"),
            "projected_spread": context.get("projected_spread"),
            "spread_lean": context.get("spread_lean"),
            "spread_delta": derived_spread_delta,
            "signal_confluence_net": context.get("signal_confluence_net"),
            # XGBoost predictions (added 2026-05-06) — context dict had these
            # but log_game_result was missing them so resolved rows had NULL.
            # Required for v2/v3 backtesting + audit calibration.
            "model_pred_home_runs": context.get("model_pred_home_runs"),
            "model_pred_away_runs": context.get("model_pred_away_runs"),
            "model_pred_spread": context.get("model_pred_spread"),
            "model_pred_total": context.get("model_pred_total"),
            "open_spread": context.get("open_spread"),
            "close_spread": context.get("close_spread"),
            "confidence": context.get("confidence"),
            "model_version": "v0.1",
            "wind_blowing_in": context.get("wind_blowing_in"),
            "is_dome": context.get("is_dome"),
            "timezone_change": context.get("timezone_change"),
            "home_last5_run_diff": context.get("home_last5_run_diff"),
            "away_last5_run_diff": context.get("away_last5_run_diff"),
            "days_since_last_home_game": context.get("days_since_last_home_game"),
            "away_consecutive_road_games": context.get("away_consecutive_road_games"),
            "home_travel_distance_last_game": context.get("home_travel_distance_last_game"),
            "open_total": context.get("open_total"),
            "close_total": context.get("close_total"),
            "f5_total_line": context.get("f5_total_line"),
            "nrfi_score": context.get("nrfi_score"),
            "home_first_inning_era": context.get("home_first_inning_era"),
            "away_first_inning_era": context.get("away_first_inning_era"),
            "home_first_inning_whip": context.get("home_first_inning_whip"),
            "away_first_inning_whip": context.get("away_first_inning_whip"),
            "projected_spread": context.get("projected_spread"),
            "spread_lean": context.get("spread_lean"),
            "spread_delta": derived_spread_delta,
            "open_spread": context.get("open_spread"),
            "close_spread": context.get("close_spread"),
            # ML open/close for line movement audit
            "home_ml_open": context.get("home_ml_open"),
            "away_ml_open": context.get("away_ml_open"),
            "home_ml_close": context.get("home_ml_close"),
            "away_ml_close": context.get("away_ml_close"),
            # 5/30 — snapshot the headline play decision so audit can grade
            # SIDE/TOTAL/PROP plays against outcomes going forward. Columns
            # added by migration 20260530_results_primary_play_and_dims.sql;
            # the field-strip fallback below handles the pre-migration case
            # so log_game_result doesn't 400 if the migration hasn't run.
            # NOTE 2026-06-07: primary_play_computed_at is a CONTEXT-row
            # freshness timestamp (migration 20260604) and lives only on
            # mlb_game_context, not mlb_game_results. Writing it here was
            # 400ing every record with PGRST204 and silently blacking out
            # 6/6 + 6/7 grading — do NOT add it back.
            "primary_play": context.get("primary_play"),
            "sweat_dimensions": (context.get("sweat_breakdown") or {}).get("dimensions"),
            # Jerry Model (shadow mode) — added by 20260530_jerry_model_columns.sql.
            # Pre-migration retry in the post() block below strips these
            # if columns aren't there yet.
            "jerry_pred_home_runs": context.get("jerry_pred_home_runs"),
            "jerry_pred_away_runs": context.get("jerry_pred_away_runs"),
            "jerry_pred_spread": context.get("jerry_pred_spread"),
            "jerry_pred_total": context.get("jerry_pred_total"),
            "jerry_components": context.get("jerry_components"),
            "jerry_weights_version": context.get("jerry_weights_version"),
            # 5/30 recency snapshot — added so future backtests can replay
            # different recency-weight configurations against real drift
            # data instead of None. Columns added by
            # 20260530_results_recency_snapshot.sql; pre-migration strip
            # in the retry block below handles the gap.
            "home_offense_drift": context.get("home_offense_drift"),
            "away_offense_drift": context.get("away_offense_drift"),
            "home_ops_last7": context.get("home_ops_last7"),
            "away_ops_last7": context.get("away_ops_last7"),
            "home_ops_last14": context.get("home_ops_last14"),
            "away_ops_last14": context.get("away_ops_last14"),
            "home_wrc_proxy_l14": context.get("home_wrc_proxy_l14"),
            "away_wrc_proxy_l14": context.get("away_wrc_proxy_l14"),
            "home_last10_runs_per_game": context.get("home_last10_runs_per_game"),
            "away_last10_runs_per_game": context.get("away_last10_runs_per_game"),
            "home_last5_runs_per_game": context.get("home_last5_runs_per_game"),
            "away_last5_runs_per_game": context.get("away_last5_runs_per_game"),
            # L10 W-L for Jerry momentum + backtest queries.
            # Columns added by 20260530_l10_record_columns.sql.
            "home_l10_wins": context.get("home_l10_wins"),
            "home_l10_losses": context.get("home_l10_losses"),
            "away_l10_wins": context.get("away_l10_wins"),
            "away_l10_losses": context.get("away_l10_losses"),
        }

        # Parse away pitcher stats from pitcher_context
        pitcher_ctx = context.get("pitcher_context", "")
        if " | " in pitcher_ctx:
            away_ctx = pitcher_ctx.split(" | ")[1]
            try:
                record["away_sp_xera"] = sanitize_xera(float(away_ctx.split("xERA ")[1].split(",")[0]), context.get("away_pitcher", '')) if "xERA " in away_ctx else None
                record["away_sp_k_pct"] = sanitize_k_pct(float(away_ctx.split("K% ")[1].split("%")[0]), context.get("away_pitcher", '')) if "K% " in away_ctx else None
                record["away_sp_whiff_rate"] = float(away_ctx.split("whiff ")[1].split("%")[0]) if "whiff " in away_ctx else None
                record["away_sp_gb_pct"] = float(away_ctx.split("GB% ")[1].split("%")[0]) if "GB% " in away_ctx else None
                record["away_sp_hand"] = away_ctx.split("(")[1][0] if "(" in away_ctx else None
            except:
                pass

        # Try to fetch final score
        home_score = None
        away_score = None
        total_runs = 0
        home_win = None
        total_result = None
        run_line_result = None
        margin_of_victory = 0
        home_spread_covered = None

        home_team = context.get("home_team")
        away_team = context.get("away_team")
        game_date = context.get("game_date")

        # Pass commence_time hint so doubleheader days resolve to correct game
        game_pk, mlb_game = get_mlb_game_pk(
            home_team, away_team, game_date,
            commence_time_hint=context.get("commence_time")
        )
        if game_pk:
            home_score, away_score, game_over = get_final_score(game_pk)
            if home_score is not None and away_score is not None:
                total_runs = home_score + away_score
                home_win = home_score > away_score
                margin_of_victory = abs(home_score - away_score)

                # Total result vs close total (fall back to open_total if close unavailable)
                total_line = record.get('close_total') or record.get('open_total')
                if total_line:
                    total_result = 'Over' if total_runs > float(total_line) else 'Under' if total_runs < float(total_line) else 'Push'

                # Run line result (home -1.5)
                # Run line result — home covers if they win by 2+
                if (home_score - away_score) > 1.5:
                    run_line_result = 'home'
                elif (away_score - home_score) > 1.5:
                    run_line_result = 'away'
                else:
                    run_line_result = 'push'

                # Home spread covered (using close spread if available)
                close_spread = record.get('close_spread')
                if close_spread:
                    home_spread_covered = (home_score - away_score) > -float(close_spread)

                print(f'  Final score: {away_team} {away_score} @ {home_team} {home_score} | Total: {total_runs}')

        record['home_score'] = home_score
        record['away_score'] = away_score

        record['home_win'] = home_win

        record['total_result'] = total_result
        record['run_line_result'] = run_line_result
        record['home_spread_covered'] = home_spread_covered

        # Also parse close total from bookmakers for training
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"
        }
        # Strip None odds fields so morning open + afternoon close don't overwrite each other
        odds_keys_all = [
            'open_spread', 'open_total', 'close_spread', 'close_total',
            'home_ml_open', 'away_ml_open', 'home_ml_close', 'away_ml_close',
        ]
        for k in list(odds_keys_all):
            if k in record and record[k] is None:
                del record[k]
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results?on_conflict=game_id",
            headers=headers,
            json=record
        )
        # 5/30 — graceful pre-migration retry. If primary_play / sweat_dimensions
        # columns haven't been added yet (migration not applied), PostgREST
        # returns 400 with PGRST204 or similar. Strip the new fields and
        # retry so existing data still lands until the SQL is run.
        if r.status_code == 400:
            # Generic PGRST204 strip-retry: parse the column name from the
            # error message and drop it, loop until 2xx or we've tried 8x.
            # The 6/6 + 6/7 silent blackout happened because the explicit
            # strip list was missing `primary_play_computed_at` — easier
            # to derive dynamically than maintain a list.
            stripped = []
            for _attempt in range(8):
                try:
                    err = r.json()
                    msg = err.get("message", "") if isinstance(err, dict) else ""
                except (ValueError, AttributeError):
                    msg = r.text
                # PostgREST PGRST204 format: "Could not find the 'X' column"
                import re as _re_local
                m = _re_local.search(r"'([a-z_0-9]+)'", msg)
                if not m or r.status_code != 400:
                    break
                col = m.group(1)
                if col not in record:
                    break
                record.pop(col, None)
                stripped.append(col)
                r = requests.post(
                    f"{SUPABASE_URL}/rest/v1/mlb_game_results?on_conflict=game_id",
                    headers=headers,
                    json=record
                )
                if r.status_code in [200, 201, 204]:
                    break
            if stripped and r.status_code in [200, 201, 204]:
                print(f"  ⚠️ game_results: stripped missing cols ({', '.join(stripped[:5])}) — schema lag, apply pending migrations")
        if r.status_code not in [200, 201, 204]:
            print(f"  ⚠️ game_results log failed {r.status_code}: {r.text[:100]}")
        else:
            print(f"  📊 Training row logged: {context.get('away_team')} @ {context.get('home_team')}")
    except Exception as e:
        print(f"  ⚠️ game_results error: {e}")

def run(target_date=None):
    # target_date: None → today ET (normal). YYYY-MM-DD or 'tomorrow' → preview
    # mode for the afternoon cron. Tomorrow rows skip the resolved-game log
    # since the games haven't happened yet, but populate everything that's
    # stable a day out (probable pitchers, opening lines, weather forecast,
    # all projected_* stats). The Tomorrow tab in the app reads these rows.
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    if target_date == 'tomorrow':
        today = (et_now + timedelta(days=1)).strftime('%Y-%m-%d')
    elif target_date:
        today = target_date
    else:
        today = et_now.strftime('%Y-%m-%d')
    is_preview = today != et_now.strftime('%Y-%m-%d')
    label = "TOMORROW PREVIEW" if is_preview else "today"
    print(f"Fetching MLB games for {label}...")
    print(f"  (ET date: {today})")
    # CHANGED 2026-05-04: do NOT pre-delete today's rows. Previously we
    # cleared today + 2 days back at the start of every run, then re-uploaded
    # each game in a per-game try/except. Any single-game error (MLB API
    # timeout, parse failure, etc.) caught by the per-game handler would
    # leave that game's row permanently deleted until the next successful
    # run touched it. Tonight's slate dropped Mets/COL on one rebuild and
    # Toronto/Tampa on the next.
    #
    # The upload_game_context POST uses on_conflict=game_id, so re-running
    # naturally upserts existing rows. Skip today's clear; only clear rows
    # 2+ days old (housekeeping for stale entries).
    # 2026-07-12: extended retention window from 2 days → 14 days. 7/12
    # POTD backfill (7/7-7/11) was blocked because those rows had been
    # purged. mlb_game_results has the archive with 178 cols including
    # projections but running-mode gate/scorer code reads game_context.
    # Keep 14d of rolling history for backtest + audit work; delete only
    # rows older than that. Storage impact is negligible (~15 rows/day).
    for d in range(14, 21):
        past_date = (et_now - timedelta(days=d)).strftime('%Y-%m-%d')
        delete_resp = requests.delete(
            f"{SUPABASE_URL}/rest/v1/mlb_game_context?game_date=eq.{past_date}",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
        )
        print(f"Cleared {past_date}: status {delete_resp.status_code}")
    games = get_mlb_games(target_date_et=today)

    if not games:
        # 2026-08-18 FAIL-LOUD: differentiate real off-day from Odds API blip.
        # Cross-check against MLB Stats API — if games exist there but Odds
        # returned empty, sys.exit(1) so the whole workflow aborts at this
        # step instead of running 122 downstream no-ops and only surfacing
        # at the health check 30 min later.
        #
        # 2026-08-18 REVISION: only apply this gate when target_date is
        # TODAY (or the past). For future dates (e.g. "Build tomorrow
        # preview slate" step which passes --date "$TOMORROW"), Odds API
        # legitimately doesn't post odds until ~24hr before game time —
        # empty return is EXPECTED, not a blip. Applying the gate to
        # tomorrow-preview killed the 14:37 workflow_dispatch run today.
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        today_et = (_dt.now(_tz.utc) - _td(hours=4)).date().isoformat()
        gate_active = today <= today_et  # today or backfill; skip for future
        if gate_active:
            try:
                r = requests.get(
                    "https://statsapi.mlb.com/api/v1/schedule",
                    params={"sportId": 1, "date": today}, timeout=10,
                )
                scheduled = sum(len(d.get("games", [])) for d in r.json().get("dates", []))
            except Exception as _e:
                scheduled = -1
            if scheduled > 0:
                print(f"❌ ODDS API BLIP — MLB Stats API confirms {scheduled} games "
                      f"for {today} but Odds API returned empty. Aborting pipeline "
                      f"so downstream steps don't silently no-op. Retry the workflow.")
                import sys; sys.exit(1)
            print(f"No MLB games found (MLB schedule also empty for {today} — genuine off-day)")
        else:
            # Future date + empty odds = expected (odds not posted yet).
            print(f"No MLB games found for {today} — future date, odds not yet posted (expected). Skipping preview build.")
        return

    processed = 0
    # Track game_ids that successfully called log_game_result so we can
    # verify rows actually landed in mlb_game_results at run-end. Added
    # 2026-06-06 — the 6/5 morning cron had log_game_result silently fail
    # on all 15 games (zero result rows for the date) and we only found
    # out the next morning when the resolver had 0 games to grade.
    logged_game_ids = []

    # Fetch probable pitchers from MLB Stats API
    probable_pitchers = get_probable_pitchers(today)
    print(f"Probable pitchers loaded for {len(probable_pitchers)} games")
    umpire_assignments = get_umpires(today)
    print(f"Umpire assignments loaded for {len(umpire_assignments)} games")
    confirmed_lineups = get_confirmed_lineups(today)
    print(f"Confirmed lineups loaded for {len(confirmed_lineups)} games")

    for game in games:
        try:
            home_team = game["home_team"]
            away_team = game["away_team"]
            game_id = game["id"]
            # Derive game_date from commence_time in ET, not system date
            commence_time = game.get("commence_time", "")
            if commence_time:
                game_utc = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
                game_et = game_utc - timedelta(hours=4)  # EDT
                game_date_et = game_et.strftime('%Y-%m-%d')
            else:
                game_date_et = today
            
            # Get probable pitchers — DH-aware lookup using commence_time hint
            pitcher_info = match_probable_pitcher(probable_pitchers, home_team, away_team, commence_time_hint=commence_time)
            home_pitcher = pitcher_info.get("home_pitcher")
            away_pitcher = pitcher_info.get("away_pitcher")
            home_pitcher_id = pitcher_info.get("home_pitcher_id")
            away_pitcher_id = pitcher_info.get("away_pitcher_id")
            
            # Calculate days rest
            home_days_rest = get_pitcher_days_rest(home_pitcher_id, today)
            away_days_rest = get_pitcher_days_rest(away_pitcher_id, today)
            if home_days_rest:
                print(f"  {home_pitcher} days rest: {home_days_rest}")
            if away_days_rest:
                print(f"  {away_pitcher} days rest: {away_days_rest}")
            
            # Get pitcher stats from Supabase
            home_pitcher_stats = get_pitcher_stats(home_pitcher) if home_pitcher else None
            away_pitcher_stats = get_pitcher_stats(away_pitcher) if away_pitcher else None

            # Get pitcher home/away splits
            home_pitcher_splits = get_pitcher_splits(home_pitcher_id) if home_pitcher_id else None
            away_pitcher_splits = get_pitcher_splits(away_pitcher_id) if away_pitcher_id else None
            if home_pitcher_splits:
                print(f"  {home_pitcher} splits — Home ERA: {home_pitcher_splits.get('home_era')} ({home_pitcher_splits.get('home_ip')} IP), Away ERA: {home_pitcher_splits.get('away_era')} ({home_pitcher_splits.get('away_ip')} IP)")
            if away_pitcher_splits:
                print(f"  {away_pitcher} splits — Home ERA: {away_pitcher_splits.get('home_era')} ({away_pitcher_splits.get('home_ip')} IP), Away ERA: {away_pitcher_splits.get('away_era')} ({away_pitcher_splits.get('away_ip')} IP)")
            
            if home_pitcher:
                print(f"  {home_team} starter: {home_pitcher} — xERA: {home_pitcher_stats.get('xera', 'N/A') if home_pitcher_stats else 'stats not found'}")
            if away_pitcher:
                print(f"  {away_team} starter: {away_pitcher} — xERA: {away_pitcher_stats.get('xera', 'N/A') if away_pitcher_stats else 'stats not found'}")
            
            # Get venue
            venue = TEAM_VENUE.get(home_team, "Unknown")
            coords = VENUE_COORDS.get(venue, (40.7128, -74.0060))
            
            # Get weather (current) + kickoff forecast
            weather = get_weather(venue, coords[0], coords[1])
            forecast = get_weather_forecast(venue, coords[0], coords[1], commence_time)
            weather["rain_prob_at_kickoff"] = forecast["rain_prob_at_kickoff"]
            weather["rain_risk_flag"] = forecast["rain_risk_flag"]
            
            # Get park factors
            park = get_park_factors(home_team)
            park_run_factor = park["run_factor"] if park else 100
            
            # Parse market lines from Odds API
            total_line = None
            spread_line = None
            f5_total_line = None
            home_ml_odds = None
            away_ml_odds = None
            for bm in game.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    if mkt["key"] == "totals" and not total_line:
                        total_line = mkt["outcomes"][0]["point"] if mkt["outcomes"] else None
                    if mkt["key"] in ("totals_1st_5_innings", "alternate_totals_1st_5_innings") and not f5_total_line:
                        f5_total_line = mkt["outcomes"][0]["point"] if mkt["outcomes"] else None
                    if mkt["key"] == "spreads" and not spread_line:
                        home_outcome = next((o for o in mkt.get("outcomes", []) if o.get("name") == home_team), None)
                        if home_outcome:
                            spread_line = home_outcome.get("point")
                    if mkt["key"] == "h2h" and home_ml_odds is None:
                        for o in mkt.get("outcomes", []):
                            if o.get("name") == home_team:
                                home_ml_odds = o.get("price")
                            elif o.get("name") == away_team:
                                away_ml_odds = o.get("price")
                if total_line and spread_line is not None and f5_total_line is not None and home_ml_odds is not None:
                    break
            # Estimate F5 from full total if API doesn't provide it
            if not f5_total_line and total_line:
                f5_total_line = round(float(total_line) * 0.565, 1)
                print(f"  F5 total line: {f5_total_line} (estimated from full total)")
            elif f5_total_line:
                print(f"  F5 total line: {f5_total_line}")

            # Determine if this is 8am (open) or 2pm (close) run — use ET not local/UTC
            # 2026-05-23: lowered from 13 → 10 ET. Lines settle within ~2 hours of
            # market open (~9 ET on a normal MLB slate). Anything from 10 ET on
            # should populate close_*, otherwise manual noon runs leave close_*
            # null and downstream consumers (compute_primary_play, sweat card v4
            # deltas) can't reason about market price.
            et_now = datetime.now(timezone.utc) - timedelta(hours=4)
            current_hour = et_now.hour
            is_open_run = current_hour < 10  # before 10am ET = opening line
            
            # Weather adjustment
            # Weather adjustment — calibrated from 170+ game correlation analysis:
            # Temperature (0.143 corr) is 3.5x stronger than park factor (0.041)
            # Model has -0.23 run Under bias — correct with +0.25 baseline
            weather_adj = 0.25  # Under bias correction from backtesting
            if not weather.get("is_dome"):
                temp = weather.get("temperature", 70)
                if weather["wind_speed"] > 15 and weather["wind_direction"] in ["S", "SW", "SE"]:
                    weather_adj += 1.5  # wind blowing out
                elif weather["wind_speed"] > 15 and weather["wind_direction"] in ["N", "NW", "NE"]:
                    weather_adj -= 1.5  # wind blowing in
                # Temperature effect — stronger weight based on 0.143 correlation
                if temp < 45:
                    weather_adj -= 1.5  # freezing = heavy suppression
                elif temp < 55:
                    weather_adj -= 0.8  # cold
                elif temp < 65:
                    weather_adj -= 0.3  # cool
                elif temp > 85:
                    weather_adj += 0.8  # hot = more offense
                elif temp > 75:
                    weather_adj += 0.3  # warm
                if weather["precipitation"] > 0:
                    weather_adj -= 0.5

            # Park adjustment
            park_adj = (park_run_factor - 100) / 40  # dampened — 2025 data shows PF overstates real impact

            # Data-availability flag (LEGACY field name "confidence" — kept for
            # back-compat). 2026-06-18 — Phase 1 of engine_clarity_refactor:
            # this is NOT model confidence. It just signals whether park data
            # + non-dome conditions exist, which affects how much weight the
            # downstream consumers should put on park/weather adjustments.
            # Real model confidence comes from projection-vs-line gap + model
            # agreement (computed separately further down once projections
            # exist). When the schema gets a `data_completeness` column,
            # migrate readers to that.
            confidence = "HIGH" if park and not weather.get("is_dome") else "MEDIUM" if park else "LOW"
            # Explicit alias for the data-availability semantics so callers
            # can use the non-misleading name in code:
            data_completeness = confidence

            # Get team offensive stats + home/away splits
            home_stats = get_team_stats(home_team)
            away_stats = get_team_stats(away_team)
            home_splits = get_team_splits(home_team)
            away_splits = get_team_splits(away_team)

           # Minimum 5 games before trusting team R/G — early season samples are noise
            home_split_games = home_splits['home']['games'] if home_splits and home_splits.get('home') else 0
            away_split_games = away_splits['away']['games'] if away_splits and away_splits.get('away') else 0
            home_total_games = home_stats.get('games_played', 0) if home_stats else 0
            away_total_games = away_stats.get('games_played', 0) if away_stats else 0

            home_rpg = (home_splits['home']['runs_per_game'] if home_split_games >= 5
                else home_stats['runs_per_game'] if home_stats and home_total_games >= 5
                else None)
            away_rpg = (away_splits['away']['runs_per_game'] if away_split_games >= 5
                else away_stats['runs_per_game'] if away_stats and away_total_games >= 5
                else None)
            home_ops_split = (home_splits['home']['ops'] if home_split_games >= 5
                  else home_stats['ops'] if home_stats and home_total_games >= 5
                  else None)
            away_ops_split = (away_splits['away']['ops'] if away_split_games >= 5
                  else away_stats['ops'] if away_stats and away_total_games >= 5
                  else None)

            if home_rpg:
                print(f"  {home_team} home R/G: {home_rpg:.2f}")
            if away_rpg:
                print(f"  {away_team} away R/G: {away_rpg:.2f}")

            # Reset throws variables to avoid scope issues
            home_throws = None
            away_throws = None

            # 2026-08-21 FIX: pull pitcher handedness INDEPENDENTLY of
            # lineup_confirmed. Prior version only set home_throws/away_throws
            # inside the lineup-confirmed block (line ~4045), so pre-lineup
            # ctx had home_throws=NULL, blocking every batter_platoon signal
            # from firing. Now the field populates as soon as we have a
            # pitcher name (via pitcher_stats.get('throws') OR direct MLB API
            # lookup as fallback).
            try:
                if home_pitcher_stats:
                    _t = home_pitcher_stats.get('throws')
                    if _t: home_throws = _t
                if not home_throws and home_pitcher:
                    try:
                        from pitcher_stats import get_pitcher_handedness
                        _h = get_pitcher_handedness(home_pitcher)
                        if _h: home_throws = _h
                    except Exception: pass
                if away_pitcher_stats:
                    _t = away_pitcher_stats.get('throws')
                    if _t: away_throws = _t
                if not away_throws and away_pitcher:
                    try:
                        from pitcher_stats import get_pitcher_handedness
                        _h = get_pitcher_handedness(away_pitcher)
                        if _h: away_throws = _h
                    except Exception: pass
            except Exception as _e:
                print(f"  [warn] pitcher_throws early-populate failed: {_e}")
            
            # Get team strikeout rates
            home_k_pct = get_team_strikeout_rate(home_team)
            away_k_pct = get_team_strikeout_rate(away_team)
            if home_k_pct:
                print(f"  {home_team} K%: {home_k_pct:.1f}%")
            if away_k_pct:
                print(f"  {away_team} K%: {away_k_pct:.1f}%")

            # Calculate K rate gap vs pitcher
            home_pitcher_k = None
            away_pitcher_k = None
            if home_pitcher_stats and home_pitcher_stats.get('k_pct') is not None:
                raw_k = float(home_pitcher_stats.get('k_pct', 0) or 0)
                home_pitcher_k = raw_k * 100 if raw_k < 1 else raw_k
            if away_pitcher_stats and away_pitcher_stats.get('k_pct') is not None:
                raw_k = float(away_pitcher_stats.get('k_pct', 0) or 0)
                away_pitcher_k = raw_k * 100 if raw_k < 1 else raw_k

            # K gap: positive = pitcher K rate exceeds lineup K rate (pitcher edge)
            # away lineup faces home pitcher, home lineup faces away pitcher
            home_k_gap = round(home_pitcher_k - away_k_pct, 1) if home_pitcher_k and away_k_pct else None
            away_k_gap = round(away_pitcher_k - home_k_pct, 1) if away_pitcher_k and home_k_pct else None
            # Cap K gap contribution — early season K% samples are volatile
            if home_k_gap is not None:
                home_k_gap = max(min(home_k_gap, 15), -15)
            if away_k_gap is not None:
                away_k_gap = max(min(away_k_gap, 15), -15)
            if home_k_gap is not None:
                print(f"  K gap — {home_pitcher} vs {away_team} lineup: {home_k_gap:+.1f}pts")
            if away_k_gap is not None:
                print(f"  K gap — {away_pitcher} vs {home_team} lineup: {away_k_gap:+.1f}pts")
            
            # Get wOBA/wRC+ team offense
            home_offense = get_team_woba_wrc(home_team)
            away_offense = get_team_woba_wrc(away_team)
            if home_offense:
                print(f"  {home_team} wOBA: {home_offense.get('woba')} wRC+: {home_offense.get('wrc_plus')} K%: {home_offense.get('k_pct')}%")
            if away_offense:
                print(f"  {away_team} wOBA: {away_offense.get('woba')} wRC+: {away_offense.get('wrc_plus')} K%: {away_offense.get('k_pct')}%")

            # Team defense + expected offense from Savant enrichment
            def _fi(v):
                try: return int(float(v)) if v is not None else None
                except: return None
            def _ff(v):
                try:
                    f = float(v)
                    return f if f == f else None
                except: return None
            home_team_oaa = _fi(home_offense.get('oaa')) if home_offense else None
            away_team_oaa = _fi(away_offense.get('oaa')) if away_offense else None
            home_team_xwoba = _ff(home_offense.get('xwoba')) if home_offense else None
            away_team_xwoba = _ff(away_offense.get('xwoba')) if away_offense else None
            home_team_barrel_pct = _ff(home_offense.get('barrel_pct')) if home_offense else None
            away_team_barrel_pct = _ff(away_offense.get('barrel_pct')) if away_offense else None
            if home_team_oaa is not None or away_team_oaa is not None:
                print(f"  OAA: {home_team} {home_team_oaa} vs {away_team} {away_team_oaa}")
            if home_team_xwoba is not None:
                print(f"  {home_team} xwOBA {home_team_xwoba} (season wOBA {home_offense.get('woba')})")
            if away_team_xwoba is not None:
                print(f"  {away_team} xwOBA {away_team_xwoba} (season wOBA {away_offense.get('woba')})")

            # Platoon-adjusted offense: home team's wRC+/OPS vs away pitcher's hand, and vice versa
            home_pitcher_hand = (home_pitcher_stats.get('throws') if home_pitcher_stats else None) or 'R'
            away_pitcher_hand = (away_pitcher_stats.get('throws') if away_pitcher_stats else None) or 'R'
            home_wrc_vs_opp_hand = None
            away_wrc_vs_opp_hand = None
            home_ops_vs_opp_hand = None
            away_ops_vs_opp_hand = None
            if home_offense:
                opp_hand_key = 'rhp' if away_pitcher_hand == 'R' else 'lhp'
                home_wrc_vs_opp_hand = home_offense.get(f'wrc_plus_vs_{opp_hand_key}')
                home_ops_vs_opp_hand = home_offense.get(f'ops_vs_{opp_hand_key}')
            if away_offense:
                opp_hand_key = 'rhp' if home_pitcher_hand == 'R' else 'lhp'
                away_wrc_vs_opp_hand = away_offense.get(f'wrc_plus_vs_{opp_hand_key}')
                away_ops_vs_opp_hand = away_offense.get(f'ops_vs_{opp_hand_key}')
            if home_wrc_vs_opp_hand is not None:
                print(f"  {home_team} wRC+ vs {away_pitcher_hand}HP: {home_wrc_vs_opp_hand} (season: {home_offense.get('wrc_plus')})")
            if away_wrc_vs_opp_hand is not None:
                print(f"  {away_team} wRC+ vs {home_pitcher_hand}HP: {away_wrc_vs_opp_hand} (season: {away_offense.get('wrc_plus')})")

            # Pitcher recent form — last 3 starts
            home_pitcher_last_3_era = home_pitcher_stats.get('last_3_era') if home_pitcher_stats else None
            away_pitcher_last_3_era = away_pitcher_stats.get('last_3_era') if away_pitcher_stats else None
            home_pitcher_last_3_k_pct = home_pitcher_stats.get('last_3_k_pct') if home_pitcher_stats else None
            away_pitcher_last_3_k_pct = away_pitcher_stats.get('last_3_k_pct') if away_pitcher_stats else None

            # Get bullpen stats
            home_bullpen = get_bullpen_stats(home_team)
            away_bullpen = get_bullpen_stats(away_team)

            # MLB Injuries
            home_injuries = get_mlb_injuries(home_team)
            away_injuries = get_mlb_injuries(away_team)
            if home_injuries and home_injuries['count'] > 0:
                print(f"  {home_team} injuries ({home_injuries['count']}): {home_injuries['summary']}")
            if away_injuries and away_injuries['count'] > 0:
                print(f"  {away_team} injuries ({away_injuries['count']}): {away_injuries['summary']}")

            # Pitcher last outing — pitch count affects fatigue
            home_last_outing = get_pitcher_last_outing(home_pitcher_id) if home_pitcher_id else None
            away_last_outing = get_pitcher_last_outing(away_pitcher_id) if away_pitcher_id else None
            if home_last_outing:
                print(f"  {home_pitcher} last outing: {home_last_outing['pitches']} pitches, {home_last_outing['innings']} IP")
            if away_last_outing:
                print(f"  {away_pitcher} last outing: {away_last_outing['pitches']} pitches, {away_last_outing['innings']} IP")

            # Pitcher vs team history
            # Get opponent team IDs
            home_team_id = None
            away_team_id = None
            try:
                teams_r = requests.get('https://statsapi.mlb.com/api/v1/teams?sportId=1', timeout=10)
                for t in teams_r.json().get('teams', []):
                    if home_team.lower() in t.get('name', '').lower() or t.get('name', '').lower().endswith(home_team.split(' ')[-1].lower()):
                        home_team_id = t['id']
                    if away_team.lower() in t.get('name', '').lower() or t.get('name', '').lower().endswith(away_team.split(' ')[-1].lower()):
                        away_team_id = t['id']
            except:
                pass
            home_vs_away = get_pitcher_vs_team(home_pitcher_id, away_team_id) if home_pitcher_id and away_team_id else None
            away_vs_home = get_pitcher_vs_team(away_pitcher_id, home_team_id) if away_pitcher_id and home_team_id else None
            # Recent mastery (L3 starts vs opp) — different signal from career.
            # Added 2026-05-30 per user direction. Captures pitcher's current
            # form against this specific team.
            home_vs_away_recent = get_pitcher_vs_team_recent(home_pitcher_id, away_team_id, n_starts=3) if home_pitcher_id and away_team_id else None
            away_vs_home_recent = get_pitcher_vs_team_recent(away_pitcher_id, home_team_id, n_starts=3) if away_pitcher_id and home_team_id else None
            if home_vs_away:
                print(f"  {home_pitcher} vs {away_team}: ERA {home_vs_away['era_vs_team']}, AVG {home_vs_away['avg_vs_team']}, {home_vs_away['ip_vs_team']} IP")
            if away_vs_home:
                print(f"  {away_pitcher} vs {home_team}: ERA {away_vs_home['era_vs_team']}, AVG {away_vs_home['avg_vs_team']}, {away_vs_home['ip_vs_team']} IP")

            # Bullpen usage last 3 days
            home_bp_usage = get_bullpen_usage(home_team, game_date_et)
            away_bp_usage = get_bullpen_usage(away_team, game_date_et)
            if home_bp_usage and home_bp_usage['games_last_3d'] > 0:
                print(f"  {home_team} bullpen last 3d: {home_bp_usage['relievers_used_3d']} relievers in {home_bp_usage['games_last_3d']} games")
            if away_bp_usage and away_bp_usage['games_last_3d'] > 0:
                print(f"  {away_team} bullpen last 3d: {away_bp_usage['relievers_used_3d']} relievers in {away_bp_usage['games_last_3d']} games")

            # Fetch first inning splits for NRFI model — try Supabase first, then MLB API direct
            home_first_inn = get_pitcher_first_inning(home_pitcher) if home_pitcher else None
            away_first_inn = get_pitcher_first_inning(away_pitcher) if away_pitcher else None
            # If Supabase has no first inning data, query MLB Stats API directly
            if not home_first_inn and home_pitcher:
                try:
                    from pitcher_stats import get_first_inning_splits
                    home_first_inn = get_first_inning_splits(home_pitcher)
                except:
                    pass
            if not away_first_inn and away_pitcher:
                try:
                    from pitcher_stats import get_first_inning_splits
                    away_first_inn = get_first_inning_splits(away_pitcher)
                except:
                    pass
            if home_first_inn:
                print(f"  {home_pitcher} 1st inning: ERA {home_first_inn.get('first_inning_era')}, WHIP {home_first_inn.get('first_inning_whip')}")
            else:
                print(f"  {home_pitcher} 1st inning: no data yet")
            if away_first_inn:
                print(f"  {away_pitcher} 1st inning: ERA {away_first_inn.get('first_inning_era')}, WHIP {away_first_inn.get('first_inning_whip')}")
            else:
                print(f"  {away_pitcher} 1st inning: no data yet")

            # Detect opener/bullpen games
            home_is_opener = detect_opener(home_pitcher_id) if home_pitcher_id else False
            away_is_opener = detect_opener(away_pitcher_id) if away_pitcher_id else False
            if home_is_opener:
                print(f"  ⚠️ {home_pitcher} detected as OPENER/BULLPEN — NRFI penalty applied")
            if away_is_opener:
                print(f"  ⚠️ {away_pitcher} detected as OPENER/BULLPEN — NRFI penalty applied")

            # Pre-fetch umpire stats so they can feed NRFI calc (added 2026-04-30)
            _ump_name_for_nrfi = match_umpire(umpire_assignments, home_team, away_team, commence_time_hint=commence_time)
            _ump_stats_for_nrfi = get_umpire_stats(_ump_name_for_nrfi) if _ump_name_for_nrfi else None

            # Calculate NRFI score — use local variables not context dict
            nrfi_score = calc_nrfi_score(
                home_pitcher_stats,
                away_pitcher_stats,
                home_days_rest,
                away_days_rest,
                weather.get("temperature"),
                weather.get("wind_speed"),
                weather.get("wind_direction"),
                park_run_factor,
                home_offense.get("wrc_plus") if home_offense else None,
                away_offense.get("wrc_plus") if away_offense else None,
                home_first_inn,
                away_first_inn,
                home_is_opener,
                away_is_opener,
                game_month=game_date_et[5:7] if game_date_et else None,
                umpire_stats=_ump_stats_for_nrfi,
                home_inning_1_rpg=home_offense.get('inning_1_runs_per_game') if home_offense else None,
                away_inning_1_rpg=away_offense.get('inning_1_runs_per_game') if away_offense else None,
                home_pitcher_splits=home_pitcher_splits,
                away_pitcher_splits=away_pitcher_splits,
            )
            if nrfi_score:
                # NRFI lean threshold raised from 60 to 70 (2026-04-29):
                # 352-game audit showed 60-69 band hits 45.2% — actively negative EV.
                # Only label as NRFI lean when in profitable zone (≥70 = 59% audit).
                print(f"  NRFI score: {nrfi_score} ({'NRFI lean' if nrfi_score >= 70 else 'YRFI lean' if nrfi_score <= 40 else 'neutral'})")
            # Get confirmed lineup
            lineup_info = match_lineup(confirmed_lineups, home_team, away_team, commence_time_hint=commence_time)
            home_lineup = lineup_info.get("home_lineup", [])
            away_lineup = lineup_info.get("away_lineup", [])
            lineup_confirmed = lineup_info.get("lineup_confirmed", False)
            home_catcher_name = lineup_info.get("home_catcher")
            away_catcher_name = lineup_info.get("away_catcher")
            home_catcher_framing = get_catcher_framing(home_catcher_name) if home_catcher_name else None
            away_catcher_framing = get_catcher_framing(away_catcher_name) if away_catcher_name else None
            if home_catcher_framing is not None or away_catcher_framing is not None:
                print(f"  Catcher framing: {home_catcher_name or 'TBD'} {home_catcher_framing} | {away_catcher_name or 'TBD'} {away_catcher_framing}")
            # Calculate batting order weights with individual batter OPS
            home_lineup_str = ', '.join(home_lineup) if isinstance(home_lineup, list) else home_lineup or ''
            away_lineup_str = ', '.join(away_lineup) if isinstance(away_lineup, list) else away_lineup or ''
            home_lineup_weight, home_lineup_ops = (None, None)
            away_lineup_weight, away_lineup_ops = (None, None)
            if lineup_confirmed and home_lineup_str:
                home_lineup_weight, home_lineup_ops = calc_batting_order_weight(home_lineup_str)
            if lineup_confirmed and away_lineup_str:
                away_lineup_weight, away_lineup_ops = calc_batting_order_weight(away_lineup_str)
            if home_lineup_weight:
                print(f"  {home_team} lineup weight: {home_lineup_weight}{f' (avg OPS {home_lineup_ops})' if home_lineup_ops else ''}")
            if away_lineup_weight:
                print(f"  {away_team} lineup weight: {away_lineup_weight}{f' (avg OPS {away_lineup_ops})' if away_lineup_ops else ''}")
            # Calculate platoon advantage if lineups confirmed
            home_platoon_score, home_platoon_note = None, None
            away_platoon_score, away_platoon_note = None, None
            if lineup_confirmed and home_pitcher_stats and away_pitcher_stats:
                home_throws = home_pitcher_stats.get('throws', None)
                away_throws = away_pitcher_stats.get('throws', None)
                if home_lineup and away_throws:
                    print(f"  Calculating away lineup platoon vs {away_pitcher} ({away_throws}HP)...")
                    away_platoon_score, away_platoon_note = calc_platoon_advantage(
                        ', '.join(away_lineup) if isinstance(away_lineup, list) else away_lineup,
                        away_throws
                    )
                    if away_platoon_note:
                        print(f"  Away platoon: {away_platoon_note}")
                if away_lineup and home_throws:
                    print(f"  Calculating home lineup platoon vs {home_pitcher} ({home_throws}HP)...")
                    home_platoon_score, home_platoon_note = calc_platoon_advantage(
                        ', '.join(home_lineup) if isinstance(home_lineup, list) else home_lineup,
                        home_throws
                    )
                    if home_platoon_note:
                        print(f"  Home platoon: {home_platoon_note}")
            # Get last 10 form
            home_form = get_team_last10(home_team)
            away_form = get_team_last10(away_team)
            if home_form:
                print(f"  {home_team} record: {home_form['wins']}-{home_form['losses']}, last 10: {home_form['last10']}, streak: {home_form['streak']}")
            if away_form:
                print(f"  {away_team} record: {away_form['wins']}-{away_form['losses']}, last 10: {away_form['last10']}, streak: {away_form['streak']}")
            if home_bullpen:
                print(f"  {home_team} bullpen ERA: {home_bullpen.get('bullpen_era')} save%: {home_bullpen.get('save_pct')}%")
            if away_bullpen:
                print(f"  {away_team} bullpen ERA: {away_bullpen.get('bullpen_era')} save%: {away_bullpen.get('save_pct')}%")
            
            # Get umpire early — needed for total projection
            ump_name = match_umpire(umpire_assignments, home_team, away_team, commence_time_hint=commence_time)
            ump_stats = get_umpire_stats(ump_name) if ump_name else None

            # ── PROJECTED TOTAL (calibrated from 170+ game backtesting) ──
            # Inputs used + their correlation with actual total:
            # projected_total: 0.212, temperature: 0.143, nrfi_score: -0.164
            # away_xera: 0.123, home_xera: 0.096, away_wrc: 0.086
            home_xera_val = sanitize_xera(home_pitcher_stats.get('xera'), home_pitcher) if home_pitcher_stats else None
            away_xera_val = sanitize_xera(away_pitcher_stats.get('xera'), away_pitcher) if away_pitcher_stats else None
            home_wrc = home_offense.get('wrc_plus') if home_offense else None
            away_wrc = away_offense.get('wrc_plus') if away_offense else None
            # Park factor dampened — 2025 data shows 20-pt PF swing = only ~0.4 runs real impact
            # Old: park_mult = pf/100 (way too aggressive). New: halve the effect.
            park_mult = 1.0 + (park_run_factor - 100) / 200 if park_run_factor else 1.0

            home_bp_era = home_bullpen.get('bullpen_era', 4.0) if home_bullpen else 4.0
            away_bp_era = away_bullpen.get('bullpen_era', 4.0) if away_bullpen else 4.0
            if home_rpg and away_rpg:
                # Total formula v5: Ridge regression trained on 153 games
                # CV results: 1.5+ delta 61.6%, 3+ delta 70.6%
                # Market MAE: 3.64 | Model MAE: 3.75 (close to market but finds edges at extremes)
                projected_total = None

                # Try ML model first
                try:
                    import pickle
                    import os
                    model_path = os.path.join(os.path.dirname(__file__), 'models', 'total_model_v5.pkl')
                    if os.path.exists(model_path):
                        with open(model_path, 'rb') as mf:
                            bundle = pickle.load(mf)
                        features_needed = bundle['features']
                        feat_vals = {
                            'home_runs_per_game': home_rpg,
                            'away_runs_per_game': away_rpg,
                            'home_sp_xera': float(home_xera_val) if home_xera_val else 4.25,
                            'away_sp_xera': float(away_xera_val) if away_xera_val else 4.25,
                            'home_wrc_plus': float(home_wrc) if home_wrc else 100,
                            'away_wrc_plus': float(away_wrc) if away_wrc else 100,
                            'home_team_k_pct': float(home_k_pct) if home_k_pct else 22.0,
                            'away_team_k_pct': float(away_k_pct) if away_k_pct else 22.0,
                            'park_run_factor': float(park_run_factor) if park_run_factor else 100,
                            'temperature': float(weather.get('temperature', 70)),
                            'close_total': float(total_line) if total_line else 8.5,
                        }
                        X_row = [[feat_vals[f] for f in features_needed]]
                        projected_total = round(float(bundle['model'].predict(X_row)[0]), 1)
                except Exception as e:
                    projected_total = None

                # Fallback to rule-based if ML model missing or errored
                if projected_total is None:
                    base_total = home_rpg + away_rpg
                    projected_runs = base_total * park_mult
                    projected_total = round(projected_runs + weather_adj, 1)

                    if nrfi_score:
                        nrfi_adj = max(-1.0, min(1.0, (nrfi_score - 50) * -0.02))
                        projected_total = round(projected_total + nrfi_adj, 1)

                # Bullpen differential (weak signal but directional — 0.25 coefficient as before)
                bp_total_adj = ((home_bp_era + away_bp_era) / 2 - 4.0) * 0.25
                projected_total = round(projected_total + bp_total_adj, 1)

                # Umpire over_rate adjustment
                if ump_stats and ump_stats.get('over_rate'):
                    ump_adj = (float(ump_stats['over_rate']) - 0.5) * 1.2
                    projected_total = round(projected_total + ump_adj, 1)

                # Lineup weight adjustment — confirmed lineup quality vs average
                # lineup_weight 6.0 = average, higher = stronger top of order
                if home_lineup_weight and home_lineup_weight != 6.0:
                    lineup_adj = (home_lineup_weight - 6.0) * 0.15
                    projected_total = round(projected_total + lineup_adj, 1)
                if away_lineup_weight and away_lineup_weight != 6.0:
                    lineup_adj = (away_lineup_weight - 6.0) * 0.15
                    projected_total = round(projected_total + lineup_adj, 1)

                # Injury impact — key players missing suppresses offense.
                # 2026-05-23 retune: prior version used raw count, treating an
                # extra reliever IL the same as a starting SS out. Now: parse
                # position from injuries['all'] and weight POSITION PLAYERS
                # higher than pitchers (pitching depth is deeper than position
                # depth on most rosters). Star position-player positions
                # (C/SS/CF/3B/SP) count even more.
                def _injury_impact(injuries):
                    if not injuries or not isinstance(injuries.get('all'), list):
                        return -0.15 if (injuries and injuries.get('count', 0) >= 1) else 0.0
                    all_inj = injuries['all']
                    position_players = [p for p in all_inj if p.get('position', '').upper() not in ('P', 'SP', 'RP')]
                    key_positions = {'C', 'SS', 'CF', '3B', 'SP'}
                    key_count = sum(1 for p in all_inj if p.get('position', '').upper() in key_positions)
                    pp_count = len(position_players)
                    # Weighted adjustment — position players hurt more
                    weight = (pp_count * 0.10) + (key_count * 0.05)
                    return -round(min(0.7, weight), 2)

                home_inj_adj = _injury_impact(home_injuries)
                away_inj_adj = _injury_impact(away_injuries)
                projected_total = round(projected_total + home_inj_adj + away_inj_adj, 1)

                # Defensive OAA adjustment (2026-05-23). Was a tracked-but-idle
                # field — Savant Outs Above Average for each team's defense.
                # Combined OAA captures how much both defenses suppress balls
                # in play. Coefficient kept conservative (0.04 per OAA point,
                # capped at ±0.6) since OAA correlation w/ runs is modest
                # against the other signals already baked in.
                home_oaa = home_team_oaa if isinstance(home_team_oaa, (int, float)) else None
                away_oaa = away_team_oaa if isinstance(away_team_oaa, (int, float)) else None
                if home_oaa is not None and away_oaa is not None:
                    combined_oaa = float(home_oaa) + float(away_oaa)
                    # Positive combined OAA = both defenses above avg → suppress total
                    oaa_adj = -1 * max(-15, min(15, combined_oaa)) * 0.04
                    oaa_adj = round(max(-0.6, min(0.6, oaa_adj)), 2)
                    if abs(oaa_adj) >= 0.1:
                        projected_total = round(projected_total + oaa_adj, 1)

                # ── MARKET-ANCHORED MODEL ──
                # Start from posted total and only adjust where we have proven edges
                # Blended with stats model: 40% stats + 60% market-anchored
                if total_line:
                    market_base = float(total_line)
                    market_adj = 0
                    market_adj += weather_adj  # weather proven at 0.143 correlation
                    if nrfi_score:
                        market_adj += max(-0.5, min(0.5, (nrfi_score - 50) * -0.01))
                    market_adj += bp_total_adj * 0.5  # half-weight bullpen on market model
                    if ump_stats and ump_stats.get('over_rate'):
                        market_adj += (float(ump_stats['over_rate']) - 0.5) * 0.6
                    market_anchored = round(market_base + market_adj, 1)

                    # Blend: 40% stats model + 60% market-anchored
                    blended_total = round(projected_total * 0.4 + market_anchored * 0.6, 1)
                    print(f"  Stats model: {projected_total} | Market-anchored: {market_anchored} | Blended: {blended_total}")
                    projected_total = blended_total

                    delta = projected_total - total_line
                    # v5 ML model thresholds (from cross-validated audit):
                    # 1.5+ delta = 61.6% hit rate, 3.0+ delta = 70.6% hit rate
                    # Minimum threshold 1.5 for lean, anything under is neutral
                    over_lean = True if delta > 1.5 else False if delta < -1.5 else None
                else:
                    over_lean = None
                print(f"  {home_team} avg: {home_rpg:.2f} R/G | {away_team} avg: {away_rpg:.2f} R/G | Projected: {projected_total}")
            else:
                projected_runs = None
                if total_line:
                    net_adj = weather_adj + park_adj
                    projected_total = round(total_line + net_adj, 1)
                    over_lean = True if net_adj > 0.5 else None  # no under lean on fallback — 46.2% hit rate
                else:
                    projected_total = None
                    over_lean = None
                print(f"  Team stats not available yet — market line fallback: {projected_total}")

            # ── SANITY FLOOR/CEILING (added 2026-05-25) ──
            # 5/24 TB@NYY produced projected_total=1.5 (root cause unidentified;
            # likely upstream input corruption — e.g. close_total briefly held
            # an F5/alt-market value at compute time). No realistic MLB game
            # totals below 4.0 or above 16.0. Clamp + log loudly so the next
            # occurrence surfaces in pipeline output instead of silently
            # poisoning the sweat card.
            if projected_total is not None and total_line is not None:
                try:
                    pt_f = float(projected_total)
                    tl_f = float(total_line)
                    if pt_f < 4.0 or pt_f > 16.0:
                        print(f"  ⚠️  projected_total={pt_f} out of bounds for MLB — "
                              f"clamping to market line {tl_f}. Investigate upstream.")
                        projected_total = round(tl_f, 1)
                        over_lean = None  # no edge claim on a salvaged number
                except (TypeError, ValueError):
                    pass

            # ── xERA GAP OVER BOOST ──
            # Heuristic: a moderate xERA gap correlates with overs. Audit
            # (2026-05-12, 2900+ games): gap 2.0-3.0 → 58.2% OVER (n=67),
            # gap ≥3.0 → only 52.0% OVER (n=25, ~coin flip — the extreme-gap
            # games tend to be blowouts, not shootouts). So we only fire the
            # lean in the 2.0-3.0 band now.
            #
            # 5/30 BUG FIX: previously the boost fired when `over_lean is None`,
            # which catches BOTH "v3 truly neutral" AND "v3 soft UNDER lean"
            # (delta between -1.5 and 1.5 sets over_lean None per line 3044).
            # 5/30 PHI/LAD: v3 delta -0.6 (soft UNDER), xERA gap 2.26 flipped
            # over_lean to True → build_lean returned "Over 8.5" → POTD posted
            # OVER while sweat dimension correctly said UNDER. The xERA gap
            # audit was measured on games where v3 was neutral or pointing
            # OVER — applying the boost when v3 actively soft-leans UNDER is
            # outside the cohort that earned the 58% number. New gate:
            # require v3's projection to NOT soft-lean UNDER before firing
            # the OVER boost.
            if home_xera_val and away_xera_val:
                xera_gap = abs(float(home_xera_val) - float(away_xera_val))
                if 2.0 <= xera_gap < 3.0:
                    v3_soft_under = (
                        projected_total is not None
                        and total_line is not None
                        and float(projected_total) < float(total_line) - 0.3
                    )
                    if over_lean is None and not v3_soft_under:
                        over_lean = True
                        print(f"  xERA gap {xera_gap:.1f} → Over lean (audit: 58.2% OVER on 2.0-3.0 gaps)")
                    elif over_lean is None and v3_soft_under:
                        delta_v3 = float(projected_total) - float(total_line)
                        print(f"  xERA gap {xera_gap:.1f} but v3 soft UNDER ({delta_v3:+.2f}) — not firing OVER boost")
                    elif over_lean == False:
                        over_lean = None
                        print(f"  xERA gap {xera_gap:.1f} conflicts with Under lean → neutral")
                elif xera_gap >= 3.0:
                    print(f"  xERA gap {xera_gap:.1f} ≥3.0 — not firing lean (audit: only 52% OVER on extreme gaps)")

            # ── PROJECTED SPREAD (v3: starter + bullpen weighted run expectation) ──
            # Starter throws ~5.5 IP (60% of 9-inning game), bullpen throws ~3.5 IP (40%)
            # Weight BOTH into run expectation instead of treating bullpen as a post-hoc adjustment
            # This fixes over-amplification when starter xERA is extreme (e.g. Gausman 1.78)
            projected_spread = None
            spread_lean = None
            try:
                league_avg_rpg = 4.25  # 2026 league average R/G
                STARTER_WEIGHT = 0.6
                BULLPEN_WEIGHT = 0.4
                LEAGUE_AVG_BP = 4.0  # league average bullpen ERA baseline

                if home_xera_val and away_xera_val and home_wrc and away_wrc:
                    home_bp = home_bp_era if home_bp_era else LEAGUE_AVG_BP
                    away_bp = away_bp_era if away_bp_era else LEAGUE_AVG_BP

                    # Home team faces away starter + away bullpen
                    home_factor = (STARTER_WEIGHT * (float(away_xera_val) / 4.25) +
                                   BULLPEN_WEIGHT * (float(away_bp) / 4.25))
                    # Away team faces home starter + home bullpen
                    away_factor = (STARTER_WEIGHT * (float(home_xera_val) / 4.25) +
                                   BULLPEN_WEIGHT * (float(home_bp) / 4.25))

                    home_expected = league_avg_rpg * (float(home_wrc) / 100) * home_factor * park_mult
                    away_expected = league_avg_rpg * (float(away_wrc) / 100) * away_factor * park_mult

                    projected_spread = round(home_expected - away_expected, 2)
                    if projected_spread >= 0.5:
                        spread_lean = 'home'
                    elif projected_spread <= -0.5:
                        spread_lean = 'away'
                    else:
                        spread_lean = None
                    print(f"  Projected spread: {home_team} {'+' if projected_spread >= 0 else ''}{projected_spread} | Lean: {spread_lean or 'neutral'}")
                elif home_rpg and away_rpg:
                    # No pitcher data — use raw R/G + park only
                    projected_spread = round((home_rpg - away_rpg) * park_mult, 2)
                    spread_lean = 'home' if projected_spread >= 0.5 else 'away' if projected_spread <= -0.5 else None
                    print(f"  Projected spread (no pitcher data): {projected_spread}")
            except Exception as e:
                print(f"  ⚠️ Spread projection error: {e}")

            # ── XGBoost RUNS MODEL — INFORMATIONAL (added 2026-04-25) ──
            # Trained model predictions stored ALONGSIDE v3 (not replacing yet).
            # Walk-forward validation showed +5.3pt direction lift but unreliable
            # magnitudes on outlier games (Coors-type totals, pitcher's duels).
            # Compare model vs v3 vs actual for 2 weeks before promoting to primary.
            model_pred_home_runs = None
            model_pred_away_runs = None
            model_pred_spread = None
            model_pred_total = None
            try:
                from predict_runs import predict_runs, MODELS_LOADED, build_feature_dict
                if MODELS_LOADED and projected_spread is not None:
                    # v4 model schema (2026-05-18) — kitchen-sink 82-feature
                    # set. Pass everything we have; build_feature_dict reads
                    # what it needs and leaves missing values as None
                    # (XGBoost handles NaN natively).
                    # Pull from dicts that exist at this point in the build.
                    # Some features (is_dome, signal_confluence_net,
                    # last10_*, offense_drift, etc.) are set LATER in the
                    # function; for those we leave None and the model
                    # handles missing values natively (XGBoost trained
                    # with NaN routes).
                    _hofs = home_offense or {}
                    _aofs = away_offense or {}
                    _hps = home_pitcher_stats or {}
                    _aps = away_pitcher_stats or {}
                    _hfi = home_first_inn or {}
                    _afi = away_first_inn or {}
                    _hvs = home_vs_away or {}
                    _avs = away_vs_home or {}
                    _ctx_for_model = {
                        # Pitcher quality (multi-window)
                        'home_sp_xera': home_xera_val,
                        'away_sp_xera': away_xera_val,
                        'home_sp_k_pct': _hps.get('k_pct'),
                        'away_sp_k_pct': _aps.get('k_pct'),
                        'home_sp_gb_pct': _hps.get('gb_pct'),
                        'away_sp_gb_pct': _aps.get('gb_pct'),
                        'home_pitcher_last_3_era': home_pitcher_last_3_era,
                        'away_pitcher_last_3_era': away_pitcher_last_3_era,
                        'home_pitcher_last_3_k_pct': home_pitcher_last_3_k_pct,
                        'away_pitcher_last_3_k_pct': away_pitcher_last_3_k_pct,
                        'home_first_inning_era': _hfi.get('first_inning_era'),
                        'away_first_inning_era': _afi.get('first_inning_era'),
                        'home_first_inning_whip': _hfi.get('first_inning_whip'),
                        'away_first_inning_whip': _afi.get('first_inning_whip'),
                        'home_sp_days_rest': home_days_rest,
                        'away_sp_days_rest': away_days_rest,
                        # Mastery (TOP feature by importance)
                        'home_pitcher_vs_team_era': _hvs.get('era_vs_team'),
                        'away_pitcher_vs_team_era': _avs.get('era_vs_team'),
                        'home_pitcher_vs_team_avg': _hvs.get('avg_vs_team'),
                        'away_pitcher_vs_team_avg': _avs.get('avg_vs_team'),
                        # Offense
                        'home_wrc_plus': _hofs.get('wrc_plus'),
                        'away_wrc_plus': _aofs.get('wrc_plus'),
                        'home_wrc_vs_opp_hand': home_wrc_vs_opp_hand,
                        'away_wrc_vs_opp_hand': away_wrc_vs_opp_hand,
                        'home_woba': _hofs.get('woba'),
                        'away_woba': _aofs.get('woba'),
                        'home_ops': _hofs.get('ops'),
                        'away_ops': _aofs.get('ops'),
                        'home_ops_vs_opp_hand': home_ops_vs_opp_hand,
                        'away_ops_vs_opp_hand': away_ops_vs_opp_hand,
                        'home_team_xwoba': _hofs.get('xwoba'),
                        'away_team_xwoba': _aofs.get('xwoba'),
                        'home_team_barrel_pct': _hofs.get('barrel_pct'),
                        'away_team_barrel_pct': _aofs.get('barrel_pct'),
                        'home_runs_per_game': home_rpg,
                        'away_runs_per_game': away_rpg,
                        # Recency (from offense dict where available)
                        'home_last10_runs_per_game': _hofs.get('last10_runs_per_game'),
                        'away_last10_runs_per_game': _aofs.get('last10_runs_per_game'),
                        'home_last10_runs_allowed': _hofs.get('last10_runs_allowed'),
                        'away_last10_runs_allowed': _aofs.get('last10_runs_allowed'),
                        'home_last10_run_diff': _hofs.get('last10_run_diff'),
                        'away_last10_run_diff': _aofs.get('last10_run_diff'),
                        'home_last5_runs_per_game': _hofs.get('last5_runs_per_game'),
                        'away_last5_runs_per_game': _aofs.get('last5_runs_per_game'),
                        'home_offense_drift': _hofs.get('offense_drift'),
                        'away_offense_drift': _aofs.get('offense_drift'),
                        # K matchup
                        'home_team_k_pct': _hofs.get('k_pct'),
                        'away_team_k_pct': _aofs.get('k_pct'),
                        'home_k_gap': home_k_gap,
                        'away_k_gap': away_k_gap,
                        # Defense
                        'home_team_oaa': _hofs.get('oaa'),
                        'away_team_oaa': _aofs.get('oaa'),
                        'home_catcher_framing': home_catcher_framing,
                        'away_catcher_framing': away_catcher_framing,
                        # Environment
                        'park_run_factor': park_run_factor,
                        'wind_mph': weather.get('wind_speed') if weather else None,
                        'temperature': weather.get('temperature') if weather else None,
                        # Market
                        'close_total': total_line if not is_open_run else None,
                        'close_spread': spread_line if not is_open_run else None,
                        'open_total': total_line if is_open_run else None,
                        'open_spread': spread_line if is_open_run else None,
                        # v3 anchors
                        'projected_spread': projected_spread,
                        'projected_total': projected_total,
                    }
                    # v4 model (2026-05-18) handles 1st-inn fragility and
                    # pitcher mastery natively as features — the old L3 ≥ 7.5
                    # guard is no longer needed. Keep ONLY the null-xERA
                    # guard (xgb can't reason about missing primary features)
                    # and the disagreement guard (catches blind spots).
                    skip_xgb_reason = None
                    if home_xera_val is None or away_xera_val is None:
                        skip_xgb_reason = "missing xERA"

                    if skip_xgb_reason:
                        print(f"  XGBoost suppressed: {skip_xgb_reason} — using v3 projected_total only")
                    else:
                        feat = build_feature_dict(_ctx_for_model)
                        pred_h, pred_a = predict_runs(feat)
                        if pred_h is not None and pred_a is not None:
                            xgb_total = pred_h + pred_a
                            # Disagreement guard relaxed 2026-06-06: was 2.5,
                            # now 4.0. The 2.5 threshold was over-aggressive —
                            # SF@CHC 6/6 had v3=5.7, v4=9.42 (delta +3.72)
                            # suppressed despite Jerry independently predicting
                            # 9.46 (matching v4). v3 was the outlier, not v4,
                            # but the guard killed the model anyway. Real
                            # XGBoost blind spots show >4-run swings; 2.5-4.0
                            # is just legitimate model disagreement.
                            #
                            # Jerry can't be a tiebreaker here because the
                            # Jerry projection isn't computed until after this
                            # block. If we ever reorder, switch to a 3-way
                            # vote: trust v4 when Jerry agrees with v4 vs v3.
                            DISAGREEMENT_THRESHOLD = 4.0
                            if projected_total is not None and abs(xgb_total - projected_total) >= DISAGREEMENT_THRESHOLD:
                                print(f"  XGBoost suppressed: disagrees with v3 by {abs(xgb_total - projected_total):.1f} runs (xgb {xgb_total:.1f} vs v3 {projected_total:.1f}) — model blind spot, falling back to v3")
                            else:
                                model_pred_home_runs = round(pred_h, 2)
                                model_pred_away_runs = round(pred_a, 2)
                                model_pred_spread = round(pred_h - pred_a, 2)
                                model_pred_total = round(xgb_total, 2)
                                print(f"  XGBoost (informational): spread {model_pred_spread:+.2f} (v3 {projected_spread:+.2f}), total {model_pred_total:.1f} (v3 {projected_total:.1f})")
            except Exception as e:
                print(f"  XGBoost predict failed: {e}")

            # Spread delta — projected vs posted
            # CONVENTION FIX: projected_spread is in run-differential terms (positive = home wins by X).
            # close_spread (spread_line) is in sportsbook convention (negative = home favorite).
            # To compare, convert close_spread to run-diff: market_run_diff = -spread_line.
            # delta = projected - market_run_diff = projected_spread + spread_line.
            spread_delta = None
            if projected_spread is not None and spread_line is not None:
                spread_delta = round(projected_spread + spread_line, 2)
                print(f"  Spread delta: model {projected_spread} vs market run-diff {-spread_line} (posted {spread_line}) = {'+' if spread_delta >= 0 else ''}{spread_delta}")

            # ── SIGNAL CONFLUENCE (added 2026-04-24) ──
            # Single-signal magnitude is noisy. Real edges show up when MULTIPLE
            # independent signals all favor the same side. Backtest 4/10-4/23:
            #   net confluence >= +4 → 71% hit rate (n=7) — PRIME tier
            #   net confluence >= +2 → 55% hit rate (n=29) — STRONG
            #   net confluence >= +1 → 47% hit rate (n=40) — LEAN/informational
            # Each signal votes 'home' or 'away' if its threshold is met. Net = supporting - opposing.
            confluence_net = None
            confluence_support = 0
            confluence_breakdown = {}
            if projected_spread is not None:
                model_pick = 'home' if projected_spread > 0 else 'away'
                breakdown = {}

                # Helper to safely cast
                def _f(v):
                    try: return float(v) if v is not None else None
                    except: return None

                # Signal: xERA gap (lower = better pitcher → that side's TEAM favored)
                hx, ax = _f(home_xera_val), _f(away_xera_val)
                if hx is not None and ax is not None:
                    gap = ax - hx
                    if abs(gap) >= 0.5:
                        breakdown['xera'] = 'home' if gap > 0 else 'away'

                # Signal: wRC+ vs opp hand (higher = better offense). Fall back to season wRC+ when split missing.
                hw = _f(home_wrc_vs_opp_hand) if home_wrc_vs_opp_hand is not None else _f(home_wrc)
                aw = _f(away_wrc_vs_opp_hand) if away_wrc_vs_opp_hand is not None else _f(away_wrc)
                if hw is not None and aw is not None and abs(hw - aw) >= 8:
                    breakdown['wrc_hand'] = 'home' if hw > aw else 'away'

                # Signal: pitcher L3 ERA (lower = hot)
                h_l3 = _f(home_pitcher_last_3_era)
                a_l3 = _f(away_pitcher_last_3_era)
                if h_l3 is not None and a_l3 is not None and abs(a_l3 - h_l3) >= 1.0:
                    breakdown['l3_era'] = 'home' if h_l3 < a_l3 else 'away'

                # Signal: pitcher L3 K% (higher = striking guys out)
                h_l3k = _f(home_pitcher_last_3_k_pct)
                a_l3k = _f(away_pitcher_last_3_k_pct)
                if h_l3k is not None and a_l3k is not None and abs(h_l3k - a_l3k) >= 4:
                    breakdown['l3_k'] = 'home' if h_l3k > a_l3k else 'away'

                # Signal: lineup weight (confirmed lineup OPS edge)
                h_lw = _f(home_lineup_weight)
                a_lw = _f(away_lineup_weight)
                if h_lw is not None and a_lw is not None and abs(h_lw - a_lw) >= 0.5:
                    breakdown['lineup'] = 'home' if h_lw > a_lw else 'away'

                # Signal: bullpen ERA (lower = better relief)
                hbp = _f(home_bp_era)
                abp = _f(away_bp_era)
                if hbp is not None and abp is not None and abs(abp - hbp) >= 0.5:
                    breakdown['bullpen'] = 'home' if hbp < abp else 'away'

                # Signal: bullpen taxed (2026-05-23). bp_relievers_3d tracks
                # how many relievers each team used in the last 3 days. A pen
                # that's burned 4+ arms is functionally short — gas tank low,
                # high-leverage guys unavailable. Tracked field was idle in
                # confluence (only displayed in scout report). When |delta|
                # ≥ 2 relievers, vote against the more-taxed pen's team.
                try:
                    h_bp_used = _f(home_bp_usage.get('relievers_used_3d')) if home_bp_usage else None
                    a_bp_used = _f(away_bp_usage.get('relievers_used_3d')) if away_bp_usage else None
                    if h_bp_used is not None and a_bp_used is not None:
                        bp_delta = h_bp_used - a_bp_used  # +ve = home pen more taxed
                        if abs(bp_delta) >= 2:
                            breakdown['bp_taxed'] = 'away' if bp_delta > 0 else 'home'
                except (NameError, AttributeError):
                    pass  # bp_usage not in scope on this build path

                # Signal: L14 OPS proxy delta (2026-05-23; retuned same-day).
                # Self-aggregated team-level recency-of-quality-contact.
                #
                # Initial threshold of ±15 fired on 23 of 30 teams tonight —
                # that's noise-fitting, not signal. Raised to ±25 (catches
                # the genuine cases like Cubs -139, ATL -43, Rays +54 while
                # cutting borderline noise).
                #
                # Also added L7 same-direction confirmation: only fire the
                # vote if L7 OPS delta agrees with L14 (both teams hot OR
                # both cold). Catches the "team was hot but cooling" trap —
                # the better play there is fading, not stacking. When L7 and
                # L14 disagree, we skip the vote and let other signals decide.
                try:
                    h_wrc_l14 = _f((home_offense or {}).get('wrc_proxy_l14'))
                    a_wrc_l14 = _f((away_offense or {}).get('wrc_proxy_l14'))
                    h_ops_l7 = _f((home_offense or {}).get('ops_last7'))
                    a_ops_l7 = _f((away_offense or {}).get('ops_last7'))
                    if h_wrc_l14 is not None and a_wrc_l14 is not None:
                        wrc_delta = h_wrc_l14 - a_wrc_l14
                        if abs(wrc_delta) >= 25:
                            # L14 says delta direction. Confirm with L7 sign.
                            l7_confirms = True  # default if L7 missing
                            if h_ops_l7 is not None and a_ops_l7 is not None:
                                l7_delta = h_ops_l7 - a_ops_l7
                                # Sign must agree (both >0 or both <0).
                                l7_confirms = (wrc_delta > 0 and l7_delta > 0) or \
                                              (wrc_delta < 0 and l7_delta < 0)
                            if l7_confirms:
                                breakdown['ops_l14_heat'] = 'home' if wrc_delta > 0 else 'away'
                except (NameError, AttributeError, TypeError):
                    pass  # missing recency data — silent skip

                # Signal: opponent-specific H2H recency OVERRIDE (2026-05-24).
                # Built after the LAA-vs-TEX miss — LAA L14 wRC+ proxy showed
                # ice cold (51 vs season 99) but they had put 14 runs on TEX
                # in 2 H2H games (7.0 R/G — +3.6 vs their L10 baseline). The
                # team-level recency missed the matchup-specific pattern.
                #
                # When |rpg_vs_opp_delta| >= 1.5 R/G on n>=2 H2H games this
                # season, fire as an OVERRIDE vote (counts double). This
                # signal specifically catches the case where overall L14
                # contradicts opponent-specific recency.
                # 2026-07-29 INVERSION: lifetime audit ([[project_cohort_inversion_729]])
                # found h2h_recent_home fires wrong direction 68.9% of the time
                # (31.1% hit rate n=45). Root cause hypothesis: recent H2H is
                # already priced by the books; naive signal captures a mean-reversion
                # trap. Invert both sign of home + away H2H signals — flipped vote
                # should push toward ~68.9% at the same sample size.
                # Guarded by env INVERT_H2H (defaults on) for safe rollback.
                _INVERT_H2H_RECENT = os.environ.get('INVERT_H2H_RECENT', '1') != '0'
                try:
                    h_h2h = fetch_h2h_recent(home_team, away_team) if 'fetch_h2h_recent' in globals() else None
                    a_h2h = fetch_h2h_recent(away_team, home_team) if 'fetch_h2h_recent' in globals() else None
                    if h_h2h and h_h2h.get('games_played', 0) >= 2:
                        h_delta = h_h2h.get('rpg_delta_vs_l14')
                        if h_delta is not None and abs(h_delta) >= 1.5:
                            side = 'home' if h_delta > 0 else 'away'
                            if _INVERT_H2H_RECENT:
                                side = 'away' if side == 'home' else 'home'
                            breakdown['h2h_recent_home'] = side
                    if a_h2h and a_h2h.get('games_played', 0) >= 2:
                        a_delta = a_h2h.get('rpg_delta_vs_l14')
                        if a_delta is not None and abs(a_delta) >= 1.5:
                            side = 'away' if a_delta > 0 else 'home'
                            if _INVERT_H2H_RECENT:
                                side = 'home' if side == 'away' else 'away'
                            breakdown['h2h_recent_away'] = side
                except (NameError, AttributeError, TypeError):
                    pass  # H2H data not loaded — silent skip

                # Signal: line movement as sharp-money proxy (2026-05-24).
                # User pasted Action Network sharp/public split data this AM
                # — confirmed our DotD CHW pick was being faded by sharps
                # (+17% money on SF the other way). Codified the lesson:
                # when the LINE moves significantly between open and close,
                # the side it moved TOWARDS is the side sharp money supports.
                # We don't have Pinnacle-specific feeds yet, but DraftKings
                # close-vs-open is a reasonable proxy (consensus-aware book).
                # Fires when |spread_move| >= 0.5 OR |total_move| >= 0.5.
                try:
                    o_sp = _f(open_spread); c_sp = _f(close_spread)
                    o_tot = _f(open_total); c_tot = _f(close_total)
                    if o_sp is not None and c_sp is not None:
                        # close_spread is HOME-side: more NEGATIVE = home favored more
                        # If close_spread < open_spread → line moved TOWARD HOME
                        spread_move = c_sp - o_sp
                        if abs(spread_move) >= 0.5:
                            # Negative move = sharp on home; positive = sharp on away
                            breakdown['line_move_spread'] = 'home' if spread_move < 0 else 'away'
                    if o_tot is not None and c_tot is not None:
                        # close_total HIGHER than open = sharp money on OVER
                        # We don't vote on total in confluence (it's a side-vote system)
                        # — total movement gets surfaced in the breakdown only for
                        # downstream consumers (audit, narrative); no actual vote.
                        pass
                except (NameError, AttributeError, TypeError):
                    pass  # open/close odds missing — silent skip

                # Signal: recency (last 10 games) — added 2026-04-29.
                # Catches hot/cold streaks the season-long stats hide. Conservative
                # single-vote integration; projection blend weight pending backtest.
                # Each team's recency_score = (offense_L10_RPG - season_RPG)
                #                            - (opp_pitching_L10_RA - opp_pitching_season_RA)
                # If |home - away recency| >= 0.8, vote for the hotter side.
                try:
                    h_off_l10 = _f((home_offense or {}).get('last10_runs_per_game'))
                    a_off_l10 = _f((away_offense or {}).get('last10_runs_per_game'))
                    h_off_szn = _f((home_offense or {}).get('runs_per_game'))
                    a_off_szn = _f((away_offense or {}).get('runs_per_game'))
                    h_pitch_l10_ra = _f((home_offense or {}).get('last10_runs_allowed'))
                    a_pitch_l10_ra = _f((away_offense or {}).get('last10_runs_allowed'))
                    # Use season runs allowed proxy via bullpen_era (rough but available)
                    # Better: pull team RA season — for now, just use offense delta as primary signal
                    if (h_off_l10 is not None and a_off_l10 is not None
                        and h_off_szn is not None and a_off_szn is not None):
                        h_recency = h_off_l10 - h_off_szn
                        a_recency = a_off_l10 - a_off_szn
                        # Layer in opponent's recent pitching if available
                        if a_pitch_l10_ra is not None:
                            # Opp giving up more runs lately = bonus for home
                            opp_pitch_szn = _f(away_bp_era)  # rough proxy
                            if opp_pitch_szn is not None:
                                h_recency += (a_pitch_l10_ra - opp_pitch_szn) * 0.3
                        if h_pitch_l10_ra is not None:
                            opp_pitch_szn = _f(home_bp_era)
                            if opp_pitch_szn is not None:
                                a_recency += (h_pitch_l10_ra - opp_pitch_szn) * 0.3
                        net_recency = h_recency - a_recency
                        if abs(net_recency) >= 0.8:
                            breakdown['recency'] = 'home' if net_recency > 0 else 'away'
                        # EXTREME matchup: one team genuinely HOT (≥+1.0 R/G L10
                        # vs season) and opponent COLD (≤-1.0 R/G). When both
                        # extremes converge, fire an additional confluence vote
                        # so the matchup gets +2 net (vs +1 for normal recency).
                        h_off_delta = h_off_l10 - h_off_szn
                        a_off_delta = a_off_l10 - a_off_szn
                        if h_off_delta >= 1.0 and a_off_delta <= -1.0:
                            breakdown['recency_extreme'] = 'home'  # 🔥 home vs ❄️ away
                        elif a_off_delta >= 1.0 and h_off_delta <= -1.0:
                            breakdown['recency_extreme'] = 'away'  # 🔥 away vs ❄️ home

                    # STREAK INFLECTION (added 2026-04-30): detects when a team's
                    # very-recent L5 disagrees with their L10 baseline by ≥1.0 R/G.
                    # Signals a streak ending (cold team warming up) or starting
                    # (hot team cooling). Layers on top of recency vote.
                    h_off_l5 = _f((home_offense or {}).get('last5_runs_per_game'))
                    a_off_l5 = _f((away_offense or {}).get('last5_runs_per_game'))
                    if h_off_l5 is not None and h_off_l10 is not None and a_off_l5 is not None and a_off_l10 is not None:
                        # Trend = L5 - L10. Positive = team accelerating, negative = decelerating.
                        h_trend = h_off_l5 - h_off_l10
                        a_trend = a_off_l5 - a_off_l10
                        # Net trend favoring home minus away
                        net_trend = h_trend - a_trend
                        if abs(net_trend) >= 1.0:
                            breakdown['trend'] = 'home' if net_trend > 0 else 'away'
                except Exception:
                    pass  # missing L-N data is fine — signal just doesn't fire

                # Signal: days rest (penalty when one pitcher is short rest)
                h_dr = _f(home_days_rest)
                a_dr = _f(away_days_rest)
                if h_dr is not None and a_dr is not None:
                    if h_dr < 4 and a_dr >= 4:
                        breakdown['rest'] = 'away'  # home pitcher tired
                    elif a_dr < 4 and h_dr >= 4:
                        breakdown['rest'] = 'home'

                # Signal: park + offense alignment
                _park = _f(park_run_factor)
                if _park is not None and hw is not None and aw is not None:
                    if _park >= 105:  # hitter park rewards better offense
                        if hw - aw >= 5: breakdown['park'] = 'home'
                        elif aw - hw >= 5: breakdown['park'] = 'away'
                    elif _park <= 95:  # pitcher park rewards better pitcher
                        if hx is not None and ax is not None:
                            if ax - hx >= 0.5: breakdown['park'] = 'home'
                            elif hx - ax >= 0.5: breakdown['park'] = 'away'

                # Signal: pitcher vs team mastery (added 2026-05-07).
                # Catches Eovaldi-shape blind spots — a pitcher with mediocre
                # general numbers who has historically dominated this specific
                # lineup (e.g. Eovaldi 0.36 ERA / 25 IP vs NYY despite 4.38 xERA).
                #
                # Threshold lessons:
                #   - 15 IP minimum sample (Lorenzen vs NYM at 7 IP was a mirage that
                #     blew up 5/6, total went 15 vs UNDER 9.5 line). 15 IP = ~3-4 starts.
                #   - 1.5 ERA delta minimum vs pitcher's overall xera, so we only fire
                #     on demonstrated mastery, not garden-variety good outings.
                #
                # Vote rule: which TEAM benefits when their pitcher has mastery?
                # The pitcher's TEAM. So home_vs_away mastery = vote home (away
                # lineup will struggle). away_vs_home mastery = vote away.
                # If both pitchers have mastery flags, signal cancels (no vote).
                try:
                    home_pvt = home_vs_away  # in scope from earlier in build
                    away_pvt = away_vs_home
                    home_mastery = (
                        home_pvt and home_pvt.get('ip_vs_team', 0) >= 15
                        and hx is not None
                        and home_pvt.get('era_vs_team') is not None
                        and (hx - home_pvt['era_vs_team']) >= 1.5
                    )
                    away_mastery = (
                        away_pvt and away_pvt.get('ip_vs_team', 0) >= 15
                        and ax is not None
                        and away_pvt.get('era_vs_team') is not None
                        and (ax - away_pvt['era_vs_team']) >= 1.5
                    )
                    if home_mastery and not away_mastery:
                        breakdown['pitcher_vs_team'] = 'home'
                    elif away_mastery and not home_mastery:
                        breakdown['pitcher_vs_team'] = 'away'
                    # both-mastery and neither-mastery cases: no vote
                except (NameError, TypeError, KeyError):
                    # home_vs_away / away_vs_home not in scope or malformed —
                    # signal silently doesn't fire, no penalty
                    pass

                # SHADOW SIGNALS — Tier 1 launch-blocker work, logged but NOT
                # counted in net/support/against until backtest validates each.
                # Stored in a parallel `shadow_breakdown` dict with `_shadow_`
                # prefix on keys so consumers can join historical picks against
                # the shadow vote pattern.
                shadow_breakdown = {}

                # Shadow #6 — Travel fatigue. away_consecutive_road_games >= 6
                # is a real fatigue threshold per public research (~1-2%
                # offensive dip). Only applies to away team (home always 0).
                try:
                    away_road = _f(g_road := g.get('away_consecutive_road_games'))  # type: ignore
                    if away_road and away_road >= 7:
                        shadow_breakdown['_shadow_travel_fade'] = 'home'  # fade away offense
                    elif away_road and away_road >= 5:
                        shadow_breakdown['_shadow_travel_fade'] = 'home'  # weaker
                except Exception:
                    pass

                # Shadow #7 — Long-rest pitcher. >=8 days rest doubles
                # variance — pitchers come back EITHER dominant or rusty.
                # Shadow signal "long_rest_volatility" doesn't pick a side,
                # logged for backtest as a tier-cap candidate (if validated,
                # PRIME caps to STRONG when either pitcher is long-rest).
                try:
                    h_rest = _f(g.get('home_days_rest'))
                    a_rest = _f(g.get('away_days_rest'))
                    if (h_rest and h_rest >= 8) or (a_rest and a_rest >= 8):
                        shadow_breakdown['_shadow_long_rest'] = 'volatile'
                except Exception:
                    pass

                # Shadow #8 — Inning-1 team stats into NRFI. Currently NRFI
                # scoring uses overall opp team K% / wRC+ but not the team's
                # inning-1 specific runs/game. mlb_team_offense.inning_1_runs_per_game
                # exists but isn't pulled into game_context yet. Shadow flag
                # marks games where TEAM inning-1 RPG diverges from overall
                # baseline (would tighten NRFI predictions). Implementation
                # blocked on enriching home_offense / away_offense with this
                # field — adding column read here as a marker for the work.
                # When _enriched: vote will go on lower inning-1-RPG team.
                shadow_breakdown['_shadow_inning1_team_stats'] = 'pending_enrichment'

                support = sum(1 for v in breakdown.values() if v == model_pick)
                against = sum(1 for v in breakdown.values() if v != model_pick)
                confluence_net = support - against
                confluence_support = support
                confluence_breakdown = breakdown
                # Absolute home-vs-away lean — added 2026-07-22.
                # signal_confluence_net is MODEL-RELATIVE (supports vs opposes
                # the projected_spread direction), which reads wrong when
                # displayed as if it were home-lean. Concrete example: MIN@CLE
                # 7/22 had breakdown 4-HOME/2-AWAY (net home_lean = +2), but
                # model_pick=away → stored confluence_net = 2-4 = -2. Users see
                # -2 and think "away lean" when actually 4/6 signals point HOME.
                # New field is direction-agnostic so app can display the more
                # intuitive value. Primary_play gate keeps using confluence_net
                # (correct for fav-only playable ML picks).
                _home_signals = sum(1 for v in breakdown.values() if v == 'home')
                _away_signals = sum(1 for v in breakdown.values() if v == 'away')
                confluence_home_lean = _home_signals - _away_signals
                # Total possible signals — the universe that COULD vote when
                # all data is present. Display denominator. Bump this if you
                # add a new vote above so X / N display stays accurate.
                # Current set: xera, wrc_hand, l3_era, l3_k, lineup, bullpen,
                #              bp_taxed, ops_l14_heat, trend, recency, park,
                #              h2h_recent_home, h2h_recent_away, pitcher_vs_team
                #              (14 total — keep this in sync with the votes
                #              actually computed above).
                CONFLUENCE_TOTAL_POSSIBLE = 14
                confluence_voted = len(breakdown)
                confluence_no_data = CONFLUENCE_TOTAL_POSSIBLE - confluence_voted

                # Confluence STRENGTH label — internal diagnostic only.
                # 2026-06-18 (Phase 2 of engine_clarity_refactor): renamed
                # from 'tier'/PRIME/STRONG/LEAN to confluence_strength to
                # avoid colliding with the system-wide PRIME taxonomy.
                # The system PRIME label is owned by sweat_dim score
                # (>=80) + resolver tier — NOT by confluence net count.
                # The values here represent confluence loudness only.
                confluence_strength = 'NONE'
                if confluence_net >= 4: confluence_strength = 'LOUD'
                elif confluence_net >= 2: confluence_strength = 'EDGE'
                elif confluence_net >= 1: confluence_strength = 'LEAN'

                # Mastery gate hardening (added 2026-05-07).
                # Audit on backfilled data found LOUD confluence with mastery
                # *disagreeing* hits 3-4 (42.9% — fade band), while LOUD with
                # mastery *agreeing* hits 7-0 (100%). Explicit downgrade
                # when mastery disagrees with model_pick at LOUD or EDGE
                # strength — drop one step.
                pvt_vote = breakdown.get('pitcher_vs_team')
                if pvt_vote and pvt_vote != model_pick and confluence_strength in ('LOUD', 'EDGE'):
                    pre_strength = confluence_strength
                    if confluence_strength == 'LOUD':
                        confluence_strength = 'EDGE'
                    elif confluence_strength == 'EDGE':
                        confluence_strength = 'LEAN'
                    print(f"  ⚠️ Mastery disagree gate fired: {pre_strength} → {confluence_strength} "
                          f"(pitcher_vs_team votes {pvt_vote.upper()}, model_pick is {model_pick.upper()})")

                sig_str = ', '.join(f"{k}:{v[0].upper()}" for k,v in breakdown.items()) or 'no signals'
                print(f"  Signal confluence: {confluence_strength} (net {confluence_net:+d}, support {support}, against {against}) — {sig_str}")

            # Print umpire info (already fetched above for total projection)
            if ump_name:
                k_rate = ump_stats.get('k_rate_above_avg', 'N/A') if ump_stats else 'not in database'
                over_pct = ump_stats.get('over_rate', 'N/A') if ump_stats else 'N/A'
                print(f"  Umpire: {ump_name} — K rate: {k_rate}, Over%: {over_pct}")

            # Build pitcher context string for Jerry.
            # 2026-07-25 DQ FIX: mlb_pitcher_stats fields have INCONSISTENT
            # units — some rows store as decimal (0.295 = 29.5%), some as
            # percent (18.9 = 18.9%), some as default 10.0 (was a bug —
            # now nulled by DQ cleanup). Previously the code always did
            # `value * 100` which produced "1000%" and "7200% LOB" when
            # source was already percent. Normalize: values < 1 = decimal,
            # values >= 1 = already percent, skip if None.
            def _pct(v):
                if v is None: return None
                try:
                    f = float(v)
                    if f == 0: return 0.0
                    return f * 100 if f < 1 else f
                except (TypeError, ValueError):
                    return None
            def _fmt_pitcher_str(pitcher_stats, name, throws_default='R'):
                xera = pitcher_stats.get('xera', 'N/A')
                kpct = _pct(pitcher_stats.get('k_pct'))
                whiff = _pct(pitcher_stats.get('whiff_rate'))
                gb = _pct(pitcher_stats.get('gb_pct'))
                fb = _pct(pitcher_stats.get('fb_pct'))
                lob = _pct(pitcher_stats.get('lob_pct'))
                throws = pitcher_stats.get('throws') or throws_default
                pitcher_type = ("GB pitcher" if (gb or 0) > 50
                                else "FB pitcher" if (fb or 0) > 40
                                else "neutral")
                # Only include fields with valid data; skip NULL entries
                parts = [f"xERA {xera}"]
                if kpct is not None: parts.append(f"K% {kpct:.1f}%")
                if whiff is not None: parts.append(f"whiff {whiff:.1f}%")
                if gb is not None: parts.append(f"GB% {gb:.1f}%")
                if fb is not None: parts.append(f"FB% {fb:.1f}%")
                if lob is not None: parts.append(f"LOB% {lob:.1f}%")
                return f"{name} ({throws}HP): " + ", ".join(parts) + f" ({pitcher_type})"

            pitcher_context = ""
            if home_pitcher_stats:
                pitcher_context += _fmt_pitcher_str(home_pitcher_stats, home_pitcher)
            if away_pitcher_stats:
                if pitcher_context:
                    pitcher_context += " | "
                pitcher_context += _fmt_pitcher_str(away_pitcher_stats, away_pitcher)
            # Build umpire note
            ump_note = ""
            if ump_stats:
                k_tendency = "K-friendly" if ump_stats.get('k_rate_above_avg', 0) > 0.5 else "hitter-friendly" if ump_stats.get('k_rate_above_avg', 0) < -0.5 else "neutral"
                over_pct = ump_stats.get('over_rate', 0.5) * 100
                ump_note = f"{ump_name} — {k_tendency} zone, {over_pct:.0f}% over rate"

            # ── XGBOOST FEATURES ──
            # Wind blowing in (Wrigley-specific for now, expandable later)
            # Any outdoor park with wind > 15mph blowing toward home plate
            is_dome = venue in DOME_VENUES
            wind_blowing_in = (
                not is_dome and
                (weather.get('wind_speed') or 0) > 15 and
                weather.get('wind_direction') in ['N', 'NE', 'NW']
            )

            # Timezone change
            home_tz = TEAM_TIMEZONES.get(home_team, TEAM_TIMEZONES.get(home_team.split(' ')[-1], 'ET'))
            away_tz = TEAM_TIMEZONES.get(away_team, TEAM_TIMEZONES.get(away_team.split(' ')[-1], 'ET'))
            tz_change = abs(TZ_OFFSET.get(home_tz, 0) - TZ_OFFSET.get(away_tz, 0))

            # Schedule-based features (one API call per team covers run diff, home days, road streak)
            home_run_diff, home_days_since_home, _, home_last_venue = get_team_schedule_features(home_team, game_date_et)
            time.sleep(0.3)
            away_run_diff, _, away_consec_road, _ = get_team_schedule_features(away_team, game_date_et)

            # Travel distance for home team
            home_travel_dist = None
            if home_last_venue and venue:
                # Normalize venue names — MLB API may return slightly different names
                VENUE_ALIASES = {
                    'Oriole Park at Camden Yards': 'Camden Yards',
                    'Oriole Park At Camden Yards': 'Camden Yards',
                    'Oakland Coliseum': 'Sutter Health Park',
                    'Oakland-Alameda County Coliseum': 'Sutter Health Park',
                    'RingCentral Coliseum': 'Sutter Health Park',
                    'loanDepot park': 'loanDepot Park',
                    'loandepot park': 'loanDepot Park',
                    'Tropicana Field ': 'Tropicana Field',
                    'Minute Maid Park': 'Daikin Park',
                    'UNIQLO Field at Dodger Stadium': 'Dodger Stadium',
                    'Tropicana Field': 'George M. Steinbrenner Field',
                    'T-Mobile Park ': 'T-Mobile Park',
                    'Rate Field': 'Guaranteed Rate Field',
                    'Guaranteed Rate  Field': 'Guaranteed Rate Field',
                }
                last_venue_norm = VENUE_ALIASES.get(home_last_venue, home_last_venue)
                venue_norm = VENUE_ALIASES.get(venue, venue)
                if last_venue_norm in VENUE_COORDS and venue_norm in VENUE_COORDS:
                    try:
                        home_travel_dist = round(haversine(VENUE_COORDS[last_venue_norm], VENUE_COORDS[venue_norm]))
                    except:
                        pass
                elif home_last_venue not in VENUE_COORDS and home_last_venue:
                    print(f"  ⚠️ Missing VENUE_COORDS for: '{home_last_venue}'")

            # Parse L10 W-L into integers (used by Jerry + persisted to
            # context dict below). MLB Stats API returns strings like "8-2".
            def _parse_l10(s):
                if not s: return (None, None)
                try:
                    parts = str(s).split('-')
                    return (int(parts[0]), int(parts[1]))
                except (ValueError, IndexError):
                    return (None, None)
            _h_l10_w, _h_l10_l = _parse_l10(home_form.get('last10') if home_form else None)
            _a_l10_w, _a_l10_l = _parse_l10(away_form.get('last10') if away_form else None)

            # ── JERRY MODEL — SHADOW MODE (added 2026-05-30) ──
            # Linear-formula projection with inning-bucket simulation,
            # mastery, recency-weighted offense, home/away R/G splits,
            # bullpen gas, park, weather, L10 momentum. Runs alongside
            # v3/v4 for 2-4 weeks of audit before any user-facing surfacing.
            # See mlb_pipeline/jerry_model.py for the math and
            # _backtest_jerry.py for hit-rate comparison.
            jerry_pred_home_runs = None
            jerry_pred_away_runs = None
            jerry_pred_total = None
            jerry_pred_spread = None
            jerry_components = None
            jerry_weights_version = None
            try:
                from jerry_model import compute_jerry_projection
                # Build minimal ctx for Jerry from local vars + lookup dicts.
                # Mirrors enrich_ctx_for_jerry but pulls from already-loaded
                # in-process data instead of re-querying Supabase.
                _hps_j = home_pitcher_stats or {}
                _aps_j = away_pitcher_stats or {}
                _hofs_j = home_offense or {}
                _aofs_j = away_offense or {}
                _hbp_j = home_bullpen or {}
                _abp_j = away_bullpen or {}
                _hvs_j = home_vs_away or {}
                _avs_j = away_vs_home or {}
                _hfi_j = home_first_inn or {}
                _afi_j = away_first_inn or {}
                _ctx_jerry = {
                    'home_team': home_team, 'away_team': away_team,
                    'home_pitcher': home_pitcher, 'away_pitcher': away_pitcher,
                    'home_l10_wins': _h_l10_w, 'home_l10_losses': _h_l10_l,
                    'away_l10_wins': _a_l10_w, 'away_l10_losses': _a_l10_l,
                    'home_sp_xera': home_xera_val, 'away_sp_xera': away_xera_val,
                    'home_pitcher_last_3_era': home_pitcher_last_3_era,
                    'away_pitcher_last_3_era': away_pitcher_last_3_era,
                    'home_first_inning_era': _hfi_j.get('first_inning_era'),
                    'away_first_inning_era': _afi_j.get('first_inning_era'),
                    'home_innings_1_3_era': _hps_j.get('innings_1_3_era'),
                    'home_innings_4_6_era': _hps_j.get('innings_4_6_era'),
                    'home_innings_7_9_era': _hps_j.get('innings_7_9_era'),
                    'away_innings_1_3_era': _aps_j.get('innings_1_3_era'),
                    'away_innings_4_6_era': _aps_j.get('innings_4_6_era'),
                    'away_innings_7_9_era': _aps_j.get('innings_7_9_era'),
                    'home_pitcher_vs_team_era': _hvs_j.get('era_vs_team'),
                    'away_pitcher_vs_team_era': _avs_j.get('era_vs_team'),
                    'home_pitcher_vs_team_ip': _hvs_j.get('ip_vs_team'),
                    'away_pitcher_vs_team_ip': _avs_j.get('ip_vs_team'),
                    # Recent mastery (5/30 add) — L3-start ERA vs this opp
                    'home_pitcher_vs_team_recent_era': (home_vs_away_recent or {}).get('era_vs_team_recent'),
                    'away_pitcher_vs_team_recent_era': (away_vs_home_recent or {}).get('era_vs_team_recent'),
                    'home_pitcher_vs_team_recent_ip': (home_vs_away_recent or {}).get('ip_vs_team_recent'),
                    'away_pitcher_vs_team_recent_ip': (away_vs_home_recent or {}).get('ip_vs_team_recent'),
                    'home_pitcher_split_delta': home_pitcher_splits.get('split_delta') if home_pitcher_splits else None,
                    'away_pitcher_split_delta': away_pitcher_splits.get('split_delta') if away_pitcher_splits else None,
                    'home_wrc_plus': home_wrc, 'away_wrc_plus': away_wrc,
                    'home_wrc_vs_opp_hand': home_wrc_vs_opp_hand,
                    'away_wrc_vs_opp_hand': away_wrc_vs_opp_hand,
                    'home_wrc_proxy_l14': _hofs_j.get('wrc_proxy_l14'),
                    'away_wrc_proxy_l14': _aofs_j.get('wrc_proxy_l14'),
                    'home_team_barrel_pct': _hofs_j.get('barrel_pct'),
                    'away_team_barrel_pct': _aofs_j.get('barrel_pct'),
                    'home_team_xwoba': _hofs_j.get('xwoba'),
                    'away_team_xwoba': _aofs_j.get('xwoba'),
                    'home_team_oaa': _hofs_j.get('oaa'),
                    'away_team_oaa': _aofs_j.get('oaa'),
                    'home_catcher_framing': home_catcher_framing,
                    'away_catcher_framing': away_catcher_framing,
                    'home_innings_1_3_runs_per_game': _hofs_j.get('innings_1_3_runs_per_game'),
                    'home_innings_4_6_runs_per_game': _hofs_j.get('innings_4_6_runs_per_game'),
                    'home_innings_7_9_runs_per_game': _hofs_j.get('innings_7_9_runs_per_game'),
                    'away_innings_1_3_runs_per_game': _aofs_j.get('innings_1_3_runs_per_game'),
                    'away_innings_4_6_runs_per_game': _aofs_j.get('innings_4_6_runs_per_game'),
                    'away_innings_7_9_runs_per_game': _aofs_j.get('innings_7_9_runs_per_game'),
                    'home_runs_per_game_season': _hofs_j.get('runs_per_game'),
                    'away_runs_per_game_season': _aofs_j.get('runs_per_game'),
                    'home_runs_per_game_home': _hofs_j.get('runs_per_game_home'),
                    'away_runs_per_game_away': _aofs_j.get('runs_per_game_away'),
                    'home_bullpen_era': _hbp_j.get('bullpen_era'),
                    'away_bullpen_era': _abp_j.get('bullpen_era'),
                    'home_pitching_7_9_era': _hbp_j.get('pitching_7_9_era'),
                    'away_pitching_7_9_era': _abp_j.get('pitching_7_9_era'),
                    'home_bp_relievers_3d': (home_bp_usage or {}).get('relievers_used_3d') if 'home_bp_usage' in dir() else None,
                    'away_bp_relievers_3d': (away_bp_usage or {}).get('relievers_used_3d') if 'away_bp_usage' in dir() else None,
                    'park_run_factor': park_run_factor,
                    'temperature': weather.get('temperature') if weather else None,
                    'wind_speed': weather.get('wind_speed') if weather else None,
                    'wind_direction': weather.get('wind_direction') if weather else None,
                }
                _jr = compute_jerry_projection(_ctx_jerry)
                jerry_pred_home_runs = _jr.get('jerry_home_runs')
                jerry_pred_away_runs = _jr.get('jerry_away_runs')
                jerry_pred_total = _jr.get('jerry_total')
                jerry_pred_spread = _jr.get('jerry_spread')
                jerry_components = _jr.get('components')
                jerry_weights_version = _jr.get('weights_version')
                missing_n = len(_jr.get('missing_inputs') or [])
                if missing_n:
                    print(f"  Jerry projection: total {jerry_pred_total} spread {jerry_pred_spread:+.2f} (missing {missing_n} inputs)")
                else:
                    print(f"  Jerry projection: total {jerry_pred_total} spread {jerry_pred_spread:+.2f}")
            except Exception as e:
                print(f"  Jerry projection failed (shadow mode — game still ships): {e}")

            # Model confidence proxy (Phase 1 of engine_clarity_refactor —
            # 2026-06-18). Distinct from the legacy `confidence` field which
            # is a data-availability flag. This one is a real conviction
            # proxy derived from:
            #   - direction agreement across v3/v4/Jerry total projections
            #     (3 of 3 agree direction = highest)
            #   - magnitude of the strongest model edge vs close_total
            #     (larger edge = more conviction)
            #
            # Output: 'STRONG' / 'EDGE' / 'LEAN' / 'NEUTRAL' / 'CONFLICTED'
            #
            # When the schema gets a `model_confidence` column, this value
            # populates it (the strip-unknown fallback in upload_game_context
            # silently drops it pre-migration). Until then, callers can
            # derive it themselves from the projection fields already
            # surfaced in the row.
            def _dir(v, line):
                if v is None or line is None: return None
                if v > line + 0.3: return 'OVER'
                if v < line - 0.3: return 'UNDER'
                return None
            try:
                _line = close_total if close_total is not None else open_total
                _v3 = projected_total
                _v4 = model_pred_total
                _jr = jerry_pred_total
                _v3d = _dir(_v3, _line)
                _v4d = _dir(_v4, _line)
                _jrd = _dir(_jr, _line)
                _dirs = [d for d in (_v3d, _v4d, _jrd) if d is not None]
                _agreement = (
                    len(_dirs) >= 2
                    and all(d == _dirs[0] for d in _dirs)
                )
                _max_edge = 0.0
                for v in (_v3, _v4, _jr):
                    if v is not None and _line is not None:
                        _max_edge = max(_max_edge, abs(float(v) - float(_line)))
                if not _dirs:
                    model_confidence_proxy = 'NEUTRAL'
                elif not _agreement:
                    model_confidence_proxy = 'CONFLICTED'
                elif _max_edge >= 2.0 and len(_dirs) == 3:
                    model_confidence_proxy = 'STRONG'
                elif _max_edge >= 1.0:
                    model_confidence_proxy = 'EDGE'
                else:
                    model_confidence_proxy = 'LEAN'
            except Exception:
                model_confidence_proxy = None

            context = {
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "game_date": game_date_et,
                "venue": venue,
                "home_pitcher": home_pitcher,
                "away_pitcher": away_pitcher,
                "home_sp_xera": sanitize_xera(home_pitcher_stats.get("xera"), home_pitcher) if home_pitcher_stats else None,
                "away_sp_xera": sanitize_xera(away_pitcher_stats.get("xera"), away_pitcher) if away_pitcher_stats else None,
                "home_throws": home_throws,
                "away_throws": away_throws,
                "home_days_rest": home_days_rest,
                "away_days_rest": away_days_rest,
                # 2026-08-22 FATIGUE — pitch count + IP from previous outing.
                # get_pitcher_last_outing() was being called + printed for
                # months but the result was thrown away. Now persisted so
                # ensemble scorer, prop template, and game Jerry can cite:
                # "Skubal threw 108 pitches over 7.2 IP on 4 days rest —
                # elevated fatigue for a K prop OVER". User-flagged
                # "Cameron 5-day rest after 9 innings" analysis vector.
                "home_pitcher_last_outing_pitches": home_last_outing.get('pitches') if home_last_outing else None,
                "home_pitcher_last_outing_ip": home_last_outing.get('innings') if home_last_outing else None,
                "away_pitcher_last_outing_pitches": away_last_outing.get('pitches') if away_last_outing else None,
                "away_pitcher_last_outing_ip": away_last_outing.get('innings') if away_last_outing else None,
                "home_pitcher_home_era": home_pitcher_splits.get('home_era') if home_pitcher_splits else None,
                "home_pitcher_away_era": home_pitcher_splits.get('away_era') if home_pitcher_splits else None,
                "away_pitcher_home_era": away_pitcher_splits.get('home_era') if away_pitcher_splits else None,
                "away_pitcher_away_era": away_pitcher_splits.get('away_era') if away_pitcher_splits else None,
                "umpire": ump_name,
                "umpire_note": ump_note,
                "pitcher_context": pitcher_context,
                "temperature": weather["temperature"],
                "wind_speed": weather["wind_speed"],
                "wind_direction": weather["wind_direction"],
                "precipitation": weather["precipitation"],
                # Rain risk added 2026-07-22 — 7/21 postponement wiped 3
                # headline picks. rain_risk_flag=True → POTD/DAWG downweight.
                "rain_prob_at_kickoff": weather.get("rain_prob_at_kickoff"),
                "rain_risk_flag": weather.get("rain_risk_flag"),
                "park_run_factor": park_run_factor,
                "open_total": total_line if is_open_run else None,
                "close_total": total_line if not is_open_run else None,
                "f5_total_line": f5_total_line,
                "projected_total": projected_total,
                "over_lean": over_lean,
                "projected_spread": projected_spread,
                "spread_lean": spread_lean,
                "spread_delta": spread_delta,
                "signal_confluence_net": confluence_net,
                "signal_confluence_support": confluence_support,
                "signal_confluence_breakdown": confluence_breakdown if confluence_breakdown else None,
                # Absolute HOME−AWAY signal count (2026-07-22). See explainer
                # comment above confluence_home_lean assignment for rationale.
                "signal_confluence_home_lean": confluence_home_lean if confluence_breakdown else None,
                # Normalized denominator for the app's "X of Y signals" display.
                # Voted = how many signals had data + clear-enough delta to vote.
                # Total = the canonical signal universe (currently 14). Without
                # these the app rendered "6/6" for one game and "9/9" for another
                # — same "all signals agree" message, different denominators,
                # confused users.
                "signal_confluence_signals_voted": confluence_voted if confluence_breakdown else None,
                "signal_confluence_signals_total": CONFLUENCE_TOTAL_POSSIBLE if confluence_breakdown else None,
                # Shadow signals (Tier 1 backtest queue) — logged but NOT
                # counted in net/support/against. After 1-2 weeks of data,
                # join against resolved game outcomes to validate before
                # promoting any to live signals.
                "signal_confluence_shadow_breakdown": shadow_breakdown if shadow_breakdown else None,
                "model_pred_home_runs": model_pred_home_runs,
                "model_pred_away_runs": model_pred_away_runs,
                "model_pred_spread": model_pred_spread,
                "model_pred_total": model_pred_total,
                # Jerry Model — shadow mode, columns added by
                # 20260530_jerry_model_columns.sql. Pre-migration fallback
                # in upload_game_context strips these if PostgREST 400s.
                "jerry_pred_home_runs": jerry_pred_home_runs,
                "jerry_pred_away_runs": jerry_pred_away_runs,
                "jerry_pred_spread": jerry_pred_spread,
                "jerry_pred_total": jerry_pred_total,
                "jerry_components": jerry_components,
                "jerry_weights_version": jerry_weights_version,
                "open_spread": spread_line if is_open_run else None,
                "close_spread": spread_line if not is_open_run else None,
                "home_ml_odds": home_ml_odds,
                "away_ml_odds": away_ml_odds,
                # ML open/close tracking — open captured on morning run, close on afternoon
                # (before games start). Once game starts, upload_game_context strips close_*
                # to preserve pre-game values.
                "home_ml_open": home_ml_odds if is_open_run else None,
                "away_ml_open": away_ml_odds if is_open_run else None,
                "home_ml_close": home_ml_odds if not is_open_run else None,
                "away_ml_close": away_ml_odds if not is_open_run else None,
                "confidence": confidence,  # legacy data-availability flag
                "data_completeness": data_completeness,  # explicit alias (Phase 1)
                # Real model conviction proxy from projections + agreement.
                # See computation block above. Pre-migration: column may not
                # exist yet; upload_game_context's strip-unknown fallback
                # handles 400s gracefully. Once schema migrates, app reads
                # this directly for "X% confidence" displays.
                "model_confidence": model_confidence_proxy,
                "fetched_at": datetime.now().isoformat(),
                "home_runs_per_game": home_rpg,
                "away_runs_per_game": away_rpg,
                "home_ops": home_ops_split,
                "away_ops": away_ops_split,
                "home_lineup": ", ".join(home_lineup) if home_lineup else None,
                "away_lineup": ", ".join(away_lineup) if away_lineup else None,
                "lineup_confirmed": lineup_confirmed,
                "home_lineup_weight": home_lineup_weight,
                "away_lineup_weight": away_lineup_weight,
                "home_lineup_ops": home_lineup_ops,
                "away_lineup_ops": away_lineup_ops,
                "nrfi_score": nrfi_score,
                "home_first_inning_era": home_first_inn.get("first_inning_era") if home_first_inn else None,
                "away_first_inning_era": away_first_inn.get("first_inning_era") if away_first_inn else None,
                "home_first_inning_whip": home_first_inn.get("first_inning_whip") if home_first_inn else None,
                "away_first_inning_whip": away_first_inn.get("first_inning_whip") if away_first_inn else None,
                "home_woba": home_offense.get('woba') if home_offense else None,
                "away_woba": away_offense.get('woba') if away_offense else None,
                "home_wrc_plus": home_offense.get('wrc_plus') if home_offense else None,
                "away_wrc_plus": away_offense.get('wrc_plus') if away_offense else None,
                "home_team_k_pct": home_k_pct,
                "away_team_k_pct": away_k_pct,
                "home_k_gap": home_k_gap,
                "away_k_gap": away_k_gap,
                "home_platoon_advantage": home_platoon_score,
                "away_platoon_advantage": away_platoon_score,
                "home_platoon_note": home_platoon_note,
                "away_platoon_note": away_platoon_note,
                "home_wrc_vs_opp_hand": home_wrc_vs_opp_hand,
                "away_wrc_vs_opp_hand": away_wrc_vs_opp_hand,
                "home_ops_vs_opp_hand": home_ops_vs_opp_hand,
                "away_ops_vs_opp_hand": away_ops_vs_opp_hand,
                "home_pitcher_last_3_era": home_pitcher_last_3_era,
                "away_pitcher_last_3_era": away_pitcher_last_3_era,
                "home_pitcher_last_3_k_pct": home_pitcher_last_3_k_pct,
                "away_pitcher_last_3_k_pct": away_pitcher_last_3_k_pct,
                "home_team_oaa": home_team_oaa,
                "away_team_oaa": away_team_oaa,
                "home_team_xwoba": home_team_xwoba,
                "away_team_xwoba": away_team_xwoba,
                "home_team_barrel_pct": home_team_barrel_pct,
                "away_team_barrel_pct": away_team_barrel_pct,
                # Multi-window recency (L5 / L10 / L20) — copied from team_offense
                "home_last5_runs_per_game": home_offense.get('last5_runs_per_game') if home_offense else None,
                "home_last10_runs_per_game": home_offense.get('last10_runs_per_game') if home_offense else None,
                "home_last20_runs_per_game": home_offense.get('last20_runs_per_game') if home_offense else None,
                "home_last5_runs_allowed": home_offense.get('last5_runs_allowed') if home_offense else None,
                "home_last10_runs_allowed": home_offense.get('last10_runs_allowed') if home_offense else None,
                "home_last20_runs_allowed": home_offense.get('last20_runs_allowed') if home_offense else None,
                "home_last5_run_diff": home_offense.get('last5_run_diff') if home_offense else None,
                "home_last10_run_diff": home_offense.get('last10_run_diff') if home_offense else None,
                "home_last20_run_diff": home_offense.get('last20_run_diff') if home_offense else None,
                "away_last5_runs_per_game": away_offense.get('last5_runs_per_game') if away_offense else None,
                "away_last10_runs_per_game": away_offense.get('last10_runs_per_game') if away_offense else None,
                "away_last20_runs_per_game": away_offense.get('last20_runs_per_game') if away_offense else None,
                "away_last5_runs_allowed": away_offense.get('last5_runs_allowed') if away_offense else None,
                "away_last10_runs_allowed": away_offense.get('last10_runs_allowed') if away_offense else None,
                "away_last20_runs_allowed": away_offense.get('last20_runs_allowed') if away_offense else None,
                "away_last5_run_diff": away_offense.get('last5_run_diff') if away_offense else None,
                "away_last10_run_diff": away_offense.get('last10_run_diff') if away_offense else None,
                "away_last20_run_diff": away_offense.get('last20_run_diff') if away_offense else None,
                # L7/L14 OPS self-aggregated (2026-05-23 — enrich_team_recency.py)
                # Cleaner recency signal than runs/game which has BABIP noise.
                # wrc_proxy_l14 is OPS-derived ~wRC+ scale, 100 = avg.
                "home_ops_last7": home_offense.get('ops_last7') if home_offense else None,
                "home_ops_last14": home_offense.get('ops_last14') if home_offense else None,
                "home_wrc_proxy_l14": home_offense.get('wrc_proxy_l14') if home_offense else None,
                "away_ops_last7": away_offense.get('ops_last7') if away_offense else None,
                "away_ops_last14": away_offense.get('ops_last14') if away_offense else None,
                "away_wrc_proxy_l14": away_offense.get('wrc_proxy_l14') if away_offense else None,
                # Offense drift = L10 R/G - season R/G. Negative = currently
                # cold relative to season baseline. Added 2026-05-07 to flag the
                # "good season offense, cold bats currently" trap (Twins 5/6 case).
                # Read by generate_props.py to fade hits-OVER PRIME when drift < -1.0.
                "home_offense_drift": (
                    round(float(home_offense.get('last10_runs_per_game')) - float(home_rpg), 2)
                    if home_offense and home_offense.get('last10_runs_per_game') is not None
                       and home_rpg is not None
                    else None
                ),
                "away_offense_drift": (
                    round(float(away_offense.get('last10_runs_per_game')) - float(away_rpg), 2)
                    if away_offense and away_offense.get('last10_runs_per_game') is not None
                       and away_rpg is not None
                    else None
                ),
                "home_catcher_framing": home_catcher_framing,
                "away_catcher_framing": away_catcher_framing,
                "stats_snapshot_date": (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d'),
                "home_record": f"{home_form['wins']}-{home_form['losses']}" if home_form else None,
                "away_record": f"{away_form['wins']}-{away_form['losses']}" if away_form else None,
                "home_last10": home_form['last10'] if home_form else None,
                "away_last10": away_form['last10'] if away_form else None,
                # Parsed integer L10 W-L for Jerry Model + audit queries.
                # Columns added by 20260530_l10_record_columns.sql.
                "home_l10_wins": _h_l10_w,
                "home_l10_losses": _h_l10_l,
                "away_l10_wins": _a_l10_w,
                "away_l10_losses": _a_l10_l,
                "home_streak": home_form['streak'] if home_form else None,
                "away_streak": away_form['streak'] if away_form else None,
                "home_bullpen_era": home_bullpen['bullpen_era'] if home_bullpen else None,
                "away_bullpen_era": away_bullpen['bullpen_era'] if away_bullpen else None,
                "home_save_pct": home_bullpen['save_pct'] if home_bullpen else None,
                "away_save_pct": away_bullpen['save_pct'] if away_bullpen else None,
                # 2026-08-22 LATE-INNING BULLPEN ERA — 7th-9th inning ERA is
                # the key signal for late-game props (outs_over, ha at high
                # lines) and total late-game modeling. get_bullpen_stats
                # already fetches this via select=*; audit found it was
                # threaded into Jerry ctx but never persisted to the main
                # row. Now available for prop scorer, template coverage
                # check, and ensemble late-inning signals.
                "home_bullpen_late_era": home_bullpen.get('pitching_7_9_era') if home_bullpen else None,
                "away_bullpen_late_era": away_bullpen.get('pitching_7_9_era') if away_bullpen else None,
                "home_bullpen_late_k_pct": home_bullpen.get('pitching_7_9_k_pct') if home_bullpen else None,
                "away_bullpen_late_k_pct": away_bullpen.get('pitching_7_9_k_pct') if away_bullpen else None,
                # 2026-08-22 OPENERS — detect_opener() returns True/False
                # and was ONLY consumed by calc_nrfi_score inline. Now
                # persisted so prop scorer knows to demote outs_over /
                # ks_over lines when a starter is actually functioning as
                # a bullpen game (pulled after 2-3 IP). User-flagged
                # short-outing risk vector.
                "home_is_opener": bool(home_is_opener),
                "away_is_opener": bool(away_is_opener),
                # 2026-08-22 UMPIRE NUMERIC FIELDS — get_umpire_stats
                # returns full row (over_rate, k_rate_above_avg, nrfi_rate,
                # run_factor) but only umpire NAME + umpire_note STRING
                # were persisted. Prop template coverage chip couldn't
                # verify K-friendly-ump signal without string-parsing the
                # note. Now surfaced numerically for both the ensemble
                # signal_sources ("umpire_k >= 15%" etc.) and the prop
                # coverage check.
                "umpire_over_rate": (ump_stats or {}).get('over_rate'),
                "umpire_k_rate_above_avg": (ump_stats or {}).get('k_rate_above_avg'),
                "umpire_nrfi_rate": (ump_stats or {}).get('nrfi_rate'),
                "umpire_run_factor": (ump_stats or {}).get('run_factor'),
                "umpire_games_sampled": (ump_stats or {}).get('games_sampled'),
                "home_last_pitch_count": home_last_outing['pitches'] if home_last_outing else None,
                "away_last_pitch_count": away_last_outing['pitches'] if away_last_outing else None,
                "home_last_ip": home_last_outing['innings'] if home_last_outing else None,
                "away_last_ip": away_last_outing['innings'] if away_last_outing else None,
                "home_pitcher_vs_team_era": home_vs_away['era_vs_team'] if home_vs_away else None,
                "away_pitcher_vs_team_era": away_vs_home['era_vs_team'] if away_vs_home else None,
                "home_pitcher_vs_team_avg": home_vs_away['avg_vs_team'] if home_vs_away else None,
                "away_pitcher_vs_team_avg": away_vs_home['avg_vs_team'] if away_vs_home else None,
                # Recent mastery (L3 starts vs opp) — separate signal from
                # career. Added 2026-05-30. Columns by 20260530_recent_mastery_columns.sql.
                "home_pitcher_vs_team_recent_era": home_vs_away_recent.get('era_vs_team_recent') if home_vs_away_recent else None,
                "away_pitcher_vs_team_recent_era": away_vs_home_recent.get('era_vs_team_recent') if away_vs_home_recent else None,
                "home_pitcher_vs_team_recent_ip": home_vs_away_recent.get('ip_vs_team_recent') if home_vs_away_recent else None,
                "away_pitcher_vs_team_recent_ip": away_vs_home_recent.get('ip_vs_team_recent') if away_vs_home_recent else None,
                "home_pitcher_vs_team_recent_baa": home_vs_away_recent.get('avg_vs_team_recent') if home_vs_away_recent else None,
                "away_pitcher_vs_team_recent_baa": away_vs_home_recent.get('avg_vs_team_recent') if away_vs_home_recent else None,
                "home_pitcher_vs_team_recent_n_starts": home_vs_away_recent.get('n_starts_recent') if home_vs_away_recent else None,
                "away_pitcher_vs_team_recent_n_starts": away_vs_home_recent.get('n_starts_recent') if away_vs_home_recent else None,
                # K-rate mastery dimension for K props (added 2026-05-25).
                # get_pitcher_vs_team already computes k_vs_team + ip_vs_team
                # from gameLog splits — derive K/9 here and persist alongside
                # the existing ERA/BAA dims so score_pitcher_ks can read it
                # without needing a separate lookup.
                "home_pitcher_vs_team_k_per_9": (
                    round((home_vs_away['k_vs_team'] * 9.0) / home_vs_away['ip_vs_team'], 2)
                    if home_vs_away and home_vs_away.get('ip_vs_team') and home_vs_away.get('k_vs_team') is not None
                    else None
                ),
                "away_pitcher_vs_team_k_per_9": (
                    round((away_vs_home['k_vs_team'] * 9.0) / away_vs_home['ip_vs_team'], 2)
                    if away_vs_home and away_vs_home.get('ip_vs_team') and away_vs_home.get('k_vs_team') is not None
                    else None
                ),
                "home_pitcher_vs_team_ip": home_vs_away['ip_vs_team'] if home_vs_away else None,
                "away_pitcher_vs_team_ip": away_vs_home['ip_vs_team'] if away_vs_home else None,
                "home_bp_relievers_3d": home_bp_usage['relievers_used_3d'] if home_bp_usage else None,
                "away_bp_relievers_3d": away_bp_usage['relievers_used_3d'] if away_bp_usage else None,
                "home_injury_count": home_injuries['count'] if home_injuries else 0,
                "away_injury_count": away_injuries['count'] if away_injuries else 0,
                "home_injury_summary": home_injuries['summary'] if home_injuries else None,
                "away_injury_summary": away_injuries['summary'] if away_injuries else None,
                "wind_blowing_in": wind_blowing_in,
                "is_dome": is_dome,
                "timezone_change": tz_change,
                "home_last5_run_diff": home_run_diff,
                "away_last5_run_diff": away_run_diff,
                "days_since_last_home_game": home_days_since_home,
                "away_consecutive_road_games": away_consec_road,
                "home_travel_distance_last_game": home_travel_dist,
            }
            if upload_game_context(context, commence_time=commence_time):
                lean = "OVER" if context["over_lean"] else "UNDER" if context["over_lean"] is False else "NEUTRAL"
                print(f"✅ {away_team} @ {home_team} — {venue} — {weather['temperature']}°F, wind {weather['wind_speed']}mph {weather['wind_direction']} — {lean}")
                processed += 1
                # Skip the training-row log when previewing a future date — those games
                # haven't been played, no score/result to log, and writing an empty
                # row to mlb_game_results corrupts the resolved-game audit.
                if not is_preview:
                    log_game_result(context)
                    # Track which game_ids we logged so the post-run sanity
                    # check at the bottom of run() can verify they actually
                    # landed. Added 2026-06-06 after the 6/5 silent-blackout
                    # incident where the morning cron's log_game_result calls
                    # all failed quietly and zero rows existed for 6/5 in
                    # mlb_game_results — only caught because the resolver
                    # found "0 graded games" the next morning.
                    logged_game_ids.append(context.get('game_id'))
            else:
                print(f"❌ Failed: {away_team} @ {home_team}")
                
        except Exception as e:
            game_label = game.get('home_team', 'unknown') if isinstance(game, dict) else 'unknown'
            print(f"❌ Error processing {game_label}: {e}")
    
    print(f"\nDone! Processed {processed} games")

    # POST-RUN VERIFICATION (added 2026-06-06)
    # Confirm every game_id we called log_game_result on actually has a
    # row in mlb_game_results. If any are missing, retry them once with
    # explicit failure logging. The 6/5 silent blackout would have
    # surfaced here as 15 missing rows.
    if not is_preview and logged_game_ids:
        try:
            ids_str = ",".join([f'"{gid}"' for gid in logged_game_ids if gid])
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/mlb_game_results"
                f"?game_id=in.({ids_str})&select=game_id",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                timeout=15,
            )
            existing = {row["game_id"] for row in r.json()} if r.status_code == 200 else set()
            missing = [gid for gid in logged_game_ids if gid and gid not in existing]
            if missing:
                print(f"\n🚨 POST-RUN AUDIT FAIL: {len(missing)} game(s) called log_game_result "
                      f"but no mlb_game_results row exists. Run resolve_game_results "
                      f"won't grade these tomorrow. Missing game_ids: {missing[:5]}...")
                # Best-effort retry: write minimal stub rows so the resolver
                # has something to update later. Preserves audit even if
                # the original log_game_result POST silently failed.
                # mlb_game_results has home_team + away_team as NOT NULL —
                # the 6/6+6/7 silent blackout was partly caused by this stub
                # write omitting them and 400ing every row. Look up team
                # names from context if we have them, else fall back to
                # mlb_game_context for the same date.
                ctx_lookup = {}
                try:
                    ctx_r = requests.get(
                        f"{SUPABASE_URL}/rest/v1/mlb_game_context"
                        f"?game_id=in.({ids_str})&select=game_id,home_team,away_team",
                        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                        timeout=15,
                    )
                    if ctx_r.status_code == 200:
                        ctx_lookup = {row["game_id"]: row for row in ctx_r.json()}
                except Exception:
                    pass
                stub_ok = 0
                stub_fail = 0
                for gid in missing:
                    ctx_row = ctx_lookup.get(gid, {})
                    stub_payload = {
                        "game_id": gid,
                        "game_date": today,
                        "season": 2026,
                        "home_team": ctx_row.get("home_team") or "UNKNOWN",
                        "away_team": ctx_row.get("away_team") or "UNKNOWN",
                    }
                    sr = requests.post(
                        f"{SUPABASE_URL}/rest/v1/mlb_game_results?on_conflict=game_id",
                        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                                 "Content-Type": "application/json",
                                 "Prefer": "resolution=merge-duplicates,return=minimal"},
                        json=stub_payload,
                        timeout=10,
                    )
                    if sr.status_code in (200, 201, 204):
                        stub_ok += 1
                    else:
                        stub_fail += 1
                        if stub_fail <= 2:
                            print(f"      stub-write FAILED {sr.status_code}: {sr.text[:200]}")
                print(f"   Stub rows: {stub_ok} written / {stub_fail} failed. "
                      f"resolve_game_results.run() will fill scores tomorrow.")
            else:
                print(f"✅ POST-RUN AUDIT: all {len(logged_game_ids)} game_results rows confirmed.")
        except Exception as e:
            print(f"⚠️ POST-RUN AUDIT failed (non-fatal): {e}")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None,
                   help="Target ET date (YYYY-MM-DD or 'tomorrow'). "
                        "Defaults to today. Tomorrow mode is a preview pass — "
                        "skips game_results training-row log.")
    args = p.parse_args()
    run(target_date=args.date)