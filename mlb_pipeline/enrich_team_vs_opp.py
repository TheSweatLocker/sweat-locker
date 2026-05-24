"""Self-aggregate per-(team, opponent) recent stats from MLB Stats API gameLog.

Built 2026-05-24 after the LAA-vs-TEX miss:
  - LAA L14 wRC+ proxy showed ICE COLD (51 vs season 99)
  - LAA had put 14 runs on TEX in 2 H2H games (7.0 R/G)
  - Our model recommended TEX ML — the user's gut caught it
  - This signal codifies that catch

For each team, walks the season gameLog and groups by opponent. Takes
the LAST 5 H2H games per pair (rolling window per opponent), aggregates
the hitting line, and writes to mlb_team_vs_opp_recent.

Output is keyed by (team, opponent, season) so the matchup-specific
signal can be pulled in O(1) at game_context build time.

Cadence: nightly, after resolver runs so today's games are included.
"""
import os
import json
import urllib.request
import urllib.parse
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from collections import defaultdict

load_dotenv()
URL = os.environ.get("SUPABASE_URL")
KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
H = {
    "apikey": KEY,
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
}

SEASON = 2026
H2H_WINDOW = 5  # last N head-to-head games


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
    u = f"https://statsapi.mlb.com/api/v1/teams?sportId=1&season={SEASON}"
    req = urllib.request.Request(u, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    return [{"id": t["id"], "name": t["name"]} for t in data.get("teams", [])]


def fetch_team_gamelog(team_id):
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


def derive_ops(totals):
    ab = totals.get("ab", 0)
    if ab == 0:
        return None
    obp_denom = ab + totals.get("bb", 0) + totals.get("hbp", 0) + totals.get("sf", 0)
    if obp_denom == 0:
        return None
    obp = (totals.get("h", 0) + totals.get("bb", 0) + totals.get("hbp", 0)) / obp_denom
    slg = totals.get("tb", 0) / ab
    return round(obp + slg, 3)


def fetch_team_overall_l14_rpg(team_name):
    """Pull team's overall L14 R/G from mlb_team_offense (already populated
    by enrich_team_recency). Used to compute the delta vs opp-specific."""
    try:
        # Don't pre-quote — urlencode in get() handles it. Pre-quoting
        # causes double-encoding (%20 -> %2520) and the query returns []
        rows = get("mlb_team_offense",
                   team=f"eq.{team_name}",
                   season=f"eq.{SEASON}",
                   select="last10_runs_per_game")
        if rows:
            v = rows[0].get("last10_runs_per_game")
            return float(v) if v is not None else None
    except Exception:
        pass
    return None


def main():
    today_et = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")
    print(f"=== TEAM-VS-OPP H2H ENRICHMENT — {today_et} ===")

    teams = fetch_all_teams()
    print(f"  Pulled {len(teams)} MLB teams")

    # Map name -> id for opponent resolution
    name_by_id = {t["id"]: t["name"] for t in teams}

    rows_to_upsert = []
    pair_count = 0

    for team in teams:
        splits = fetch_team_gamelog(team["id"])
        if not splits:
            continue
        # Group splits by opponent id
        by_opp = defaultdict(list)
        for sp in splits:
            opp_id = sp.get("opponent", {}).get("id")
            if not opp_id:
                continue
            by_opp[opp_id].append(sp)
        # Pull this team's overall L14 R/G once
        overall_l14_rpg = fetch_team_overall_l14_rpg(team["name"])

        for opp_id, opp_splits in by_opp.items():
            opp_name = name_by_id.get(opp_id)
            if not opp_name:
                continue
            # Sort by date desc and take last H2H_WINDOW
            opp_splits.sort(key=lambda s: s.get("date", ""), reverse=True)
            recent = opp_splits[:H2H_WINDOW]
            if not recent:
                continue

            totals = {"h": 0, "ab": 0, "bb": 0, "tb": 0, "hbp": 0, "sf": 0, "r_scored": 0, "r_allowed": 0}
            for sp in recent:
                stat = sp.get("stat", {})
                try:
                    totals["ab"] += int(stat.get("atBats", 0) or 0)
                    totals["bb"] += int(stat.get("baseOnBalls", 0) or 0)
                    totals["h"] += int(stat.get("hits", 0) or 0)
                    totals["tb"] += int(stat.get("totalBases", 0) or 0)
                    totals["hbp"] += int(stat.get("hitByPitch", 0) or 0)
                    totals["sf"] += int(stat.get("sacFlies", 0) or 0)
                    totals["r_scored"] += int(stat.get("runs", 0) or 0)
                except (TypeError, ValueError):
                    continue
                # Runs allowed in this matchup — split has opponent runs in another endpoint, skip for now
            games = len(recent)
            ops = derive_ops(totals)
            rpg = round(totals["r_scored"] / games, 2) if games else None
            delta = (rpg - overall_l14_rpg) if (rpg is not None and overall_l14_rpg is not None) else None
            last_date = recent[0].get("date") if recent else None

            rows_to_upsert.append({
                "team": team["name"],
                "opponent": opp_name,
                "season": SEASON,
                "games_played": games,
                "runs_scored": totals["r_scored"],
                "runs_allowed": 0,  # need separate query; skip for v1.1 initial
                "hits": totals["h"],
                "at_bats": totals["ab"],
                "walks": totals["bb"],
                "hbp": totals["hbp"],
                "sac_flies": totals["sf"],
                "ops": ops,
                "last_h2h_date": last_date,
                "rpg_vs_opp": rpg,
                "rpg_delta_vs_l14": round(delta, 2) if delta is not None else None,
                "computed_date": today_et,
            })
            pair_count += 1

    print(f"  Computed {pair_count} (team, opponent) pairs from {len(teams)} teams")

    if not rows_to_upsert:
        print("  No pairs to upsert.")
        return 0

    # Upsert in batches of 100 to avoid huge payload
    batch_size = 100
    for i in range(0, len(rows_to_upsert), batch_size):
        chunk = rows_to_upsert[i:i + batch_size]
        try:
            upsert("mlb_team_vs_opp_recent", chunk, on_conflict="team,opponent,season")
        except Exception as e:
            print(f"  ❌ Batch {i//batch_size + 1} upsert failed: {e}")
            return 1
    print(f"  ✅ Upserted {len(rows_to_upsert)} pairs")

    # Report the most notable hot/cold deltas (sanity check the signal)
    notable = [r for r in rows_to_upsert if r.get("rpg_delta_vs_l14") is not None and abs(r["rpg_delta_vs_l14"]) >= 1.5 and r["games_played"] >= 2]
    notable.sort(key=lambda r: -abs(r["rpg_delta_vs_l14"]))
    print(f"\n  Top 10 H2H matchup deltas (|delta| >= 1.5 R/G, n>=2):")
    for r in notable[:10]:
        direction = "HOT" if r["rpg_delta_vs_l14"] > 0 else "COLD"
        print(f"    {r['team']:25} vs {r['opponent']:25} | {r['games_played']}g | {r['rpg_vs_opp']:.1f} R/G (delta {r['rpg_delta_vs_l14']:+.1f}) — {direction}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
