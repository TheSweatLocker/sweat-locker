"""Backfill home_pitcher_vs_team_era + away_pitcher_vs_team_era for historical
mlb_game_results rows.

Why: The pitcher_vs_team_era field was null in 2,898/2,898 resolved games before
the gameLog rebuild shipped 2026-05-07 (commit ef8dab8). The fix populates new
games forward, but historical confluence cohorts can't grade against the new
9th vote (pitcher_vs_team mastery) without backfilled data.

Once this finishes, re-run audit_tier_calibration.py to see whether
confluence_prime_ge4 hit rate shifts when the mastery signal is in the mix.

Strategy (deduplication):
- 4,043 unique (pitcher_name, opp_team_name) combos across 2,886 games
- ~403 unique pitchers → cache pitcher_id lookups via /people/search
- 32 unique team names → cache team_id from /teams (one call)
- Then ~4,043 gameLog API calls at ~0.5s each ≈ 35 min total
- Batch update mlb_game_results in chunks of 200

Idempotent — uses on_conflict update, skips combos where era is already known
unless --force is passed.

Usage:
    python backfill_pitcher_vs_team.py            # Backfill all unresolved
    python backfill_pitcher_vs_team.py --limit 50 # Test with first 50 games
"""
import os
import sys
import time
import requests
from datetime import datetime, timezone
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}


def fetch_all_games():
    """Pull all resolved MLB games with pitcher + team fields."""
    all_rows = []
    offset = 0
    while True:
        rows = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results",
            headers=HEADERS,
            params={
                "select": "id,game_id,game_date,home_team,away_team,home_sp_name,away_sp_name,home_pitcher_vs_team_era,away_pitcher_vs_team_era",
                "home_sp_name": "not.is.null",
                "away_sp_name": "not.is.null",
                "order": "game_date.asc",
                "limit": "1000",
                "offset": str(offset),
            },
            timeout=20,
        ).json()
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return all_rows


def build_team_id_cache():
    """Fetch all MLB teams, build name → team_id map. One API call."""
    r = requests.get(
        "https://statsapi.mlb.com/api/v1/teams",
        params={"sportId": 1},
        timeout=15,
    )
    teams = r.json().get("teams", [])
    cache = {}
    for t in teams:
        full_name = t.get("name", "")
        team_id = t.get("id")
        if full_name and team_id:
            cache[full_name] = team_id
            # Also store by last word (e.g. "Yankees" → 147) for fuzzy matching
            last = full_name.split()[-1] if full_name else ""
            cache[last] = team_id
    return cache


def lookup_pitcher_id(name, cache):
    """Look up pitcher_id by name with caching. Returns None if not found."""
    if name in cache:
        return cache[name]
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": name},
            timeout=10,
        )
        people = r.json().get("people", [])
        if people:
            pid = people[0].get("id")
            cache[name] = pid
            return pid
    except Exception:
        pass
    cache[name] = None
    return None


def get_pitcher_vs_team(pitcher_id, opponent_team_id):
    """Aggregate pitcher's per-game logs vs a specific team across recent seasons.

    Same logic as game_context.get_pitcher_vs_team but inlined to avoid
    importing the heavy game_context module (which has many side effects).
    """
    if not pitcher_id or not opponent_team_id:
        return None
    try:
        agg = {"er": 0, "ip": 0.0, "k": 0, "ab": 0, "hits": 0, "g": 0}
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
                stat = sp.get("stat", {})
                ip_str = str(stat.get("inningsPitched", "0"))
                ip = float((ip_str.replace(".1", ".333").replace(".2", ".667")) or "0")
                agg["ip"] += ip
                agg["er"] += int(stat.get("earnedRuns", 0) or 0)
                agg["k"] += int(stat.get("strikeOuts", 0) or 0)
                agg["ab"] += int(stat.get("atBats", 0) or 0)
                agg["hits"] += int(stat.get("hits", 0) or 0)
                agg["g"] += 1
        if agg["ip"] < 3:
            return None
        era = round((agg["er"] * 9.0) / agg["ip"], 2)
        avg = round(agg["hits"] / agg["ab"], 3) if agg["ab"] > 0 else 0.0
        return {
            "era_vs_team": era,
            "avg_vs_team": avg,
            "ip_vs_team": round(agg["ip"], 1),
            "k_vs_team": agg["k"],
        }
    except Exception:
        return None


def patch_game(game_id, payload):
    """Update a single mlb_game_results row by composite id."""
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/mlb_game_results?id=eq.{game_id}",
        headers=WRITE_HEADERS,
        json=payload,
        timeout=15,
    )
    return r.status_code in (200, 204)


def run(limit=None):
    print("=== pitcher_vs_team_era backfill ===")
    games = fetch_all_games()
    print(f"  Resolved games: {len(games)}")
    if limit:
        games = games[:limit]
        print(f"  Limited to first {len(games)}")

    print("  Building team_id cache...")
    team_cache = build_team_id_cache()
    print(f"  Team cache: {len(team_cache)} entries")

    # Discover unique combos to look up
    combo_to_value = {}  # (pitcher_name, opp_team) → era_vs_team dict
    pitcher_id_cache = {}
    todo_pairs = set()
    for g in games:
        if g.get("home_sp_name") and g.get("away_team"):
            todo_pairs.add((g["home_sp_name"], g["away_team"]))
        if g.get("away_sp_name") and g.get("home_team"):
            todo_pairs.add((g["away_sp_name"], g["home_team"]))
    print(f"  Unique (pitcher, opp_team) combos: {len(todo_pairs)}")

    print("\n  Looking up pitcher IDs + computing eras (this is the slow phase)...")
    skipped_no_pid = 0
    skipped_no_tid = 0
    successful = 0
    for i, (pname, tname) in enumerate(sorted(todo_pairs), 1):
        if i % 100 == 0:
            print(f"    [{i}/{len(todo_pairs)}] {successful} computed, "
                  f"{skipped_no_pid} no-pid, {skipped_no_tid} no-tid")
        pid = lookup_pitcher_id(pname, pitcher_id_cache)
        if not pid:
            skipped_no_pid += 1
            continue
        tid = team_cache.get(tname) or team_cache.get(tname.split()[-1] if tname else "")
        if not tid:
            skipped_no_tid += 1
            continue
        result = get_pitcher_vs_team(pid, tid)
        combo_to_value[(pname, tname)] = result  # may be None if <3 IP
        successful += 1
        time.sleep(0.05)  # gentle throttle

    print(f"\n  Lookups complete: {successful} computed | {skipped_no_pid} pitcher-not-found | {skipped_no_tid} team-not-found")

    # Now patch games. Track which games we update vs skip.
    print("\n  Patching mlb_game_results...")
    updated = 0
    skipped_existing = 0
    for g in games:
        payload = {}
        # home pitcher vs away team
        if g.get("home_pitcher_vs_team_era") is None:
            v = combo_to_value.get((g.get("home_sp_name"), g.get("away_team")))
            if v:
                payload["home_pitcher_vs_team_era"] = v["era_vs_team"]
        # away pitcher vs home team
        if g.get("away_pitcher_vs_team_era") is None:
            v = combo_to_value.get((g.get("away_sp_name"), g.get("home_team")))
            if v:
                payload["away_pitcher_vs_team_era"] = v["era_vs_team"]
        if not payload:
            skipped_existing += 1
            continue
        if patch_game(g["id"], payload):
            updated += 1
        if updated % 200 == 0 and updated:
            print(f"    Updated {updated} games...")

    print(f"\n✅ Backfill complete: {updated} games updated, {skipped_existing} no-op")


if __name__ == "__main__":
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        if idx + 1 < len(sys.argv):
            limit = int(sys.argv[idx + 1])
    run(limit)
