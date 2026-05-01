import requests
import os
from dotenv import load_dotenv
from datetime import date, datetime, timedelta, timezone

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

def get_first_inning_runs(game_pk):
    """Get first inning runs from MLB Stats API linescore"""
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/game/{game_pk}/linescore",
            timeout=15
        )
        data = r.json()
        innings = data.get("innings", [])
        if not innings:
            return None, None
        first = innings[0]
        home_runs = first.get("home", {}).get("runs", None)
        away_runs = first.get("away", {}).get("runs", None)
        return home_runs, away_runs
    except Exception as e:
        return None, None

def get_pending_nrfi():
    """Get games from yesterday with no nrfi_result"""
    # ET not UTC/local — match pipeline's ET stamping convention
    et_today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    yesterday = (et_today - timedelta(days=1)).isoformat()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_results?game_date=eq.{yesterday}&nrfi_result=is.null&select=*",
        headers=HEADERS,
        timeout=30
    )
    return r.json()


_NAME_SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv'}


def _last_name(full_name):
    """Suffix-aware last name (McCullers Jr. → McCullers)."""
    if not full_name:
        return ''
    parts = [p for p in full_name.strip().split() if p.lower().rstrip('.') not in _NAME_SUFFIXES]
    return parts[-1].lower() if parts else ''


def _matches_pitcher_hint(mlb_game, home_sp_name, away_sp_name):
    """For DH disambiguation: True if the MLB API game's probable pitcher
    last names match the row's stored sp_names. Returns True when no hint
    available so non-DH days behave unchanged."""
    if not home_sp_name and not away_sp_name:
        return True
    teams = mlb_game.get('teams', {})
    mlb_home_p = teams.get('home', {}).get('probablePitcher', {}).get('fullName', '')
    mlb_away_p = teams.get('away', {}).get('probablePitcher', {}).get('fullName', '')
    # Only enforce match when both sides are present in API; if MLB API has
    # no probablePitcher data fall through to legacy behavior.
    if not mlb_home_p and not mlb_away_p:
        return True
    home_ok = (not home_sp_name) or _last_name(home_sp_name) == _last_name(mlb_home_p)
    away_ok = (not away_sp_name) or _last_name(away_sp_name) == _last_name(mlb_away_p)
    return home_ok and away_ok

def update_nrfi_result(game_id, result):
    """Update nrfi_result in both tables"""
    for table in ['mlb_game_results', 'mlb_game_context']:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}?game_id=eq.{game_id}",
            headers=HEADERS,
            json={"nrfi_result": result}
        )

def run():
    print("Resolving NRFI results...")
    games = get_pending_nrfi()
    print(f"Found {len(games)} games to resolve")

    resolved = 0
    for game in games:
        game_id = game.get("game_id")
        nrfi_score = game.get("nrfi_score")
        if not game_id or not nrfi_score:
            continue

        # game_id from Odds API — need MLB game_pk.
        # DH FIX (2026-05-01): on DH days the team-only match would resolve
        # twice and the second write clobbered the first. Now we also match
        # the row's home_sp_name/away_sp_name against MLB API probablePitcher
        # to pick the correct DH gamePk. After the first resolution we break.
        try:
            game_date = game.get("game_date")
            r = requests.get(
                "https://statsapi.mlb.com/api/v1/schedule",
                params={"sportId": 1, "date": game_date, "hydrate": "linescore,probablePitcher"},
                timeout=15
            )
            dates = r.json().get("dates", [])
            row_home_sp = game.get("home_sp_name")
            row_away_sp = game.get("away_sp_name")
            row_home = game.get("home_team", "")
            row_away = game.get("away_team", "")
            done = False
            for d in dates:
                if done:
                    break
                for mlb_game in d.get("games", []):
                    mlb_home = mlb_game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                    mlb_away = mlb_game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                    home_match = row_home.lower() in mlb_home.lower() or mlb_home.lower() in row_home.lower()
                    away_match = row_away.lower() in mlb_away.lower() or mlb_away.lower() in row_away.lower()
                    if not (home_match and away_match):
                        continue
                    if mlb_game.get("status", {}).get("abstractGameState") != "Final":
                        continue
                    if not _matches_pitcher_hint(mlb_game, row_home_sp, row_away_sp):
                        continue  # DH game with different starter — skip
                    game_pk = mlb_game.get("gamePk")
                    home_r1, away_r1 = get_first_inning_runs(game_pk)
                    if home_r1 is None or away_r1 is None:
                        continue
                    total_r1 = home_r1 + away_r1
                    result = "NRFI" if total_r1 == 0 else "YRFI"
                    update_nrfi_result(game_id, result)
                    print(f"  {row_away} @ {row_home}: {away_r1}+{home_r1}={total_r1} → {result} (NRFI score was {nrfi_score})")
                    resolved += 1
                    done = True
                    break
        except Exception as e:
            print(f"  Error: {e}")

    print(f"\nDone! {resolved} NRFI results resolved")

if __name__ == "__main__":
    run()