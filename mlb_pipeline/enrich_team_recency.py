"""Self-aggregate team recency stats (L7 / L14 OPS).

Pulls per-team game logs from MLB Stats API and computes rolling OPS
over the last 7 and 14 calendar days. Writes back to mlb_team_offense
as `ops_last7` / `ops_last14` plus a `wrc_proxy_l14` derived value
(OPS delta vs league avg, scaled like wRC+).

Why this exists:
  Savant's leaderboard endpoints don't support date-range filters
  (verified 2026-05-23 — all three variants returned season-wide data
  silently). Statcast play-by-play CSV with date filter times out.
  FanGraphs scraping is unreliable. So we self-aggregate from official
  MLB Stats API team game logs.

Cadence: nightly cron, after game results resolver runs so the latest
day's games are included in the window.

Data flow:
  MLB Stats API team gameLog (hitting splits)
    -> per-game stats (hits, walks, AB, total_bases, etc)
    -> filter to last 7 / last 14 calendar days
    -> sum and derive OPS = ((H+BB+HBP)/(AB+BB+HBP+SF)) + (TB/AB)
    -> upsert to mlb_team_offense.ops_last7 / ops_last14
    -> derive wrc_proxy_l14 = (ops_last14 - league_avg) / league_avg * 100 + 100

Built 2026-05-23 after Savant date-filter approach hit blockers (see
project_v11_recency_wrc memory note for full backstory).
"""
import os
import json
import urllib.request
import urllib.parse
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

load_dotenv()
URL = os.environ.get("SUPABASE_URL")
KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
H = {
    "apikey": KEY,
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
}

SEASON = 2026
LEAGUE_AVG_OPS = 0.720  # MLB 2026 baseline; refine annually


def get(path, **q):
    qs = urllib.parse.urlencode(q, safe="=.,*()")
    u = f"{URL}/rest/v1/{path}?{qs}"
    req = urllib.request.Request(u, headers=H)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def upsert(path, rows, on_conflict):
    qs = urllib.parse.urlencode({"on_conflict": on_conflict})
    u = f"{URL}/rest/v1/{path}?{qs}"
    headers = {**H, "Prefer": "resolution=merge-duplicates,return=minimal"}
    req = urllib.request.Request(u, headers=headers, data=json.dumps(rows).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status


def fetch_all_teams():
    """Get every MLB team id+name from the stats API."""
    u = f"https://statsapi.mlb.com/api/v1/teams?sportId=1&season={SEASON}"
    req = urllib.request.Request(u, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    return [{"id": t["id"], "name": t["name"]} for t in data.get("teams", [])]


def fetch_team_gamelog(team_id):
    """Pull hitting gameLog for a team this season."""
    u = (f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats"
         f"?stats=gameLog&group=hitting&season={SEASON}")
    req = urllib.request.Request(u, headers={"User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"    gameLog fetch failed: {e}")
        return []
    splits = data.get("stats", [])
    if not splits or not splits[0].get("splits"):
        return []
    return splits[0]["splits"]


def aggregate_window(splits, cutoff_date):
    """Sum hitting totals for games on or after cutoff_date.
    Returns dict with at_bats, walks, hits, total_bases, hbp, sac_flies, plate_appearances
    or None if no games in window.
    """
    totals = {"ab": 0, "bb": 0, "h": 0, "tb": 0, "hbp": 0, "sf": 0, "pa": 0, "games": 0}
    cutoff_dt = datetime.strptime(cutoff_date, "%Y-%m-%d").date()
    for sp in splits:
        gd_str = sp.get("date")
        if not gd_str:
            continue
        try:
            gd = datetime.strptime(gd_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if gd < cutoff_dt:
            continue
        stat = sp.get("stat", {})
        try:
            totals["ab"] += int(stat.get("atBats", 0) or 0)
            totals["bb"] += int(stat.get("baseOnBalls", 0) or 0)
            totals["h"] += int(stat.get("hits", 0) or 0)
            totals["tb"] += int(stat.get("totalBases", 0) or 0)
            totals["hbp"] += int(stat.get("hitByPitch", 0) or 0)
            totals["sf"] += int(stat.get("sacFlies", 0) or 0)
            totals["pa"] += int(stat.get("plateAppearances", 0) or 0)
            totals["games"] += 1
        except (TypeError, ValueError):
            continue
    return totals if totals["games"] > 0 else None


def derive_ops(totals):
    """OPS = OBP + SLG, where:
      OBP = (H + BB + HBP) / (AB + BB + HBP + SF)
      SLG = TB / AB
    """
    if totals is None:
        return None
    ab = totals["ab"]
    if ab == 0:
        return None
    obp_denom = ab + totals["bb"] + totals["hbp"] + totals["sf"]
    if obp_denom == 0:
        return None
    obp = (totals["h"] + totals["bb"] + totals["hbp"]) / obp_denom
    slg = totals["tb"] / ab
    return round(obp + slg, 3)


def wrc_proxy_from_ops(ops):
    """Crude wRC+ approximation: scale OPS delta vs league avg.
    Real wRC+ requires park-adjusted run values from FanGraphs.
    This proxy uses ~140 OPS points per 100 wRC+ delta as a rough scale.
    """
    if ops is None:
        return None
    delta = ops - LEAGUE_AVG_OPS
    return round(100 + (delta * 100 / 0.140), 1)


def main():
    today_et = datetime.now(timezone.utc) - timedelta(hours=4)
    cutoff_l7 = (today_et - timedelta(days=7)).strftime("%Y-%m-%d")
    cutoff_l14 = (today_et - timedelta(days=14)).strftime("%Y-%m-%d")
    print(f"=== TEAM RECENCY ENRICHMENT — {today_et.strftime('%Y-%m-%d')} ===")
    print(f"  L7 cutoff: {cutoff_l7}  |  L14 cutoff: {cutoff_l14}")

    teams = fetch_all_teams()
    print(f"  Pulled {len(teams)} MLB teams")

    rows_to_upsert = []
    for team in teams:
        splits = fetch_team_gamelog(team["id"])
        if not splits:
            continue
        l7_totals = aggregate_window(splits, cutoff_l7)
        l14_totals = aggregate_window(splits, cutoff_l14)
        ops_l7 = derive_ops(l7_totals)
        ops_l14 = derive_ops(l14_totals)
        wrc_proxy = wrc_proxy_from_ops(ops_l14)
        if ops_l7 is None and ops_l14 is None:
            print(f"  ⊘ {team['name']}: no games in window")
            continue
        rows_to_upsert.append({
            "team": team["name"],
            "season": SEASON,
            "ops_last7": ops_l7,
            "ops_last14": ops_l14,
            "wrc_proxy_l14": wrc_proxy,
        })
        print(f"  {team['name']:28} L7 OPS={ops_l7 or '-':<6}  L14 OPS={ops_l14 or '-':<6}  wrc_proxy={wrc_proxy or '-'}")

    if not rows_to_upsert:
        print("  No team data to upsert.")
        return 0

    try:
        upsert("mlb_team_offense", rows_to_upsert, on_conflict="team,season")
        print(f"\n  ✅ Upserted recency stats for {len(rows_to_upsert)} teams")
    except Exception as e:
        print(f"\n  ❌ Upsert failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
