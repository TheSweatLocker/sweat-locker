"""
Backfill Savant fields (OAA, xwOBA, barrel%, catcher framing) onto 2026
historical mlb_game_results rows.

Uses today's Savant snapshot applied to each historical game — approximate,
NOT point-in-time. Stamps stats_snapshot_date so training pipeline can
distinguish forward-clean vs backfilled data.

Run after savant_enrichment.py has populated mlb_team_offense and
mlb_catcher_framing for the current season.
"""
import os
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SEASON = 2026

def headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def fetch_team_savant_snapshot():
    """Current Savant snapshot of all teams: OAA, xwOBA, barrel%"""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_team_offense?season=eq.{SEASON}&select=team,oaa,xwoba,xslg,barrel_pct,hard_hit_pct",
        headers=headers(),
        timeout=30
    )
    data = r.json() if r.status_code == 200 else []
    return {row["team"]: row for row in data}

def fetch_games_to_backfill():
    """Pull 2026 mlb_game_results where Savant fields are NULL"""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_results"
        f"?game_date=gte.{SEASON}-01-01"
        f"&game_date=lt.{SEASON + 1}-01-01"
        f"&home_team_oaa=is.null"
        f"&select=game_id,game_date,home_team,away_team"
        f"&order=game_date.asc"
        f"&limit=1000",
        headers=headers(),
        timeout=30
    )
    return r.json() if r.status_code == 200 else []

def update_game(game_id, payload):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/mlb_game_results?game_id=eq.{requests.utils.quote(game_id)}",
        headers=headers(),
        json=payload,
        timeout=15
    )
    return r.status_code in (200, 204)

def run():
    snapshot = fetch_team_savant_snapshot()
    print(f"Fetched Savant snapshot for {len(snapshot)} teams")
    if not snapshot:
        print("No snapshot — run savant_enrichment.py first")
        return

    games = fetch_games_to_backfill()
    print(f"Found {len(games)} games to backfill")
    if not games:
        return

    # ET not local — match pipeline's ET convention
    today_iso = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')
    ok, fail = 0, 0
    for i, g in enumerate(games):
        try:
            home_data = snapshot.get(g['home_team'], {})
            away_data = snapshot.get(g['away_team'], {})
            payload = {
                "home_team_oaa": home_data.get('oaa'),
                "away_team_oaa": away_data.get('oaa'),
                "home_team_xwoba": home_data.get('xwoba'),
                "away_team_xwoba": away_data.get('xwoba'),
                "home_team_barrel_pct": home_data.get('barrel_pct'),
                "away_team_barrel_pct": away_data.get('barrel_pct'),
                "stats_snapshot_date": today_iso,
            }
            if update_game(g['game_id'], payload):
                ok += 1
                if ok % 25 == 0:
                    print(f"  [{ok}/{len(games)}] {g['game_date']} {g['away_team']} @ {g['home_team']}")
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"  error on {g.get('game_id')}: {e}")

    print(f"\nDone. ✅ {ok} / ❌ {fail}")
    print(f"(Approximate backfill — stamped stats_snapshot_date={today_iso}. "
          f"Not point-in-time. Use forward-only games for training.)")

if __name__ == "__main__":
    run()
