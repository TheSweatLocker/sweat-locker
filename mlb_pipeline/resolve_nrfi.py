import requests
import os
from dotenv import load_dotenv
from datetime import date, timedelta

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
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_results?game_date=eq.{yesterday}&nrfi_result=is.null&select=*",
        headers=HEADERS,
        timeout=30
    )
    return r.json()

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

        # game_id from Odds API — need MLB game_pk
        # Try matching via home/away team in MLB Stats API
        try:
            game_date = game.get("game_date")
            r = requests.get(
                "https://statsapi.mlb.com/api/v1/schedule",
                params={"sportId": 1, "date": game_date, "hydrate": "linescore"},
                timeout=15
            )
            dates = r.json().get("dates", [])
            for d in dates:
                for mlb_game in d.get("games", []):
                    mlb_home = mlb_game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                    mlb_away = mlb_game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                    if game["home_team"].lower() in mlb_home.lower() or mlb_home.lower() in game["home_team"].lower():
                        if mlb_game.get("status", {}).get("abstractGameState") == "Final":
                            game_pk = mlb_game.get("gamePk")
                            home_r1, away_r1 = get_first_inning_runs(game_pk)
                            if home_r1 is not None and away_r1 is not None:
                                total_r1 = home_r1 + away_r1
                                result = "NRFI" if total_r1 == 0 else "YRFI"
                                update_nrfi_result(game_id, result)
                                print(f"  {game['away_team']} @ {game['home_team']}: {away_r1}+{home_r1}={total_r1} → {result} (NRFI score was {nrfi_score})")
                                resolved += 1
        except Exception as e:
            print(f"  Error: {e}")

    print(f"\nDone! {resolved} NRFI results resolved")

if __name__ == "__main__":
    run()