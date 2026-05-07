"""NFL pipeline — server-side team-stats puller (Phase 1 prep).

Pulls per-team season-aggregate stats from nflverse-data (free, public,
released as gzipped CSV at github.com/nflverse/nflverse-data/releases).
No package dependency — vanilla urllib + csv module reads gzipped CSVs.

Mirrors the pattern proven by ncaab_pipeline.py and nba_pipeline.py:
  - Pull from authoritative free source
  - Normalize via alias table (when populated)
  - Upsert to Supabase team-stats table

Phase 2 (June-July): nfl_player_stats puller, nfl_props pipeline,
nfl_pick_logger.py for game-state captures.

Required SQL: 20260507_nfl_foundation.sql

Usage:
    python nfl_pipeline.py            # Pull current season (auto-detects)
    python nfl_pipeline.py --season 2025  # Pull specific season
"""
import os
import sys
import csv
import io
import gzip
import json
import urllib.request
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import requests as rq

HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}

NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"


def detect_current_season():
    """NFL season = year that the season STARTED. Sept-Dec games are this year,
    Jan-Feb games are still labeled with the prior year. We pull the most
    recent completed season by default to ensure data is final."""
    now = datetime.now()
    # Before March = prior season still in playoff cycle, label by start year
    if now.month <= 2:
        return now.year - 1
    # March-Aug = offseason, last completed season is now.year - 1
    if now.month <= 8:
        return now.year - 1
    # Sept-Dec = current season is now.year (in progress)
    return now.year


def fetch_team_stats(season, season_type="reg"):
    """Pull per-team season-aggregate stats CSV from nflverse releases."""
    url = f"{NFLVERSE_BASE}/stats_team/stats_team_{season_type}_{season}.csv.gz"
    print(f"  Fetching {url.split('/')[-1]}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = gzip.decompress(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  ⚠️  No data for {season} {season_type} (404 — season may not exist yet)")
            return []
        raise
    text = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _i(v):
    """Cast to int or None."""
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _f(v):
    """Cast to float or None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def transform(row, season, season_type):
    """Map nflverse row → nfl_team_stats schema. nflverse column names are
    verbose; we keep only the load-bearing signals for Phase 1 modeling."""
    return {
        "team": row.get("team"),
        "season": int(season),
        "season_type": season_type.upper(),
        "games": _i(row.get("games")),
        # Passing
        "pass_attempts": _i(row.get("attempts")),
        "pass_completions": _i(row.get("completions")),
        "pass_yards": _i(row.get("passing_yards")),
        "pass_tds": _i(row.get("passing_tds")),
        "pass_ints": _i(row.get("passing_interceptions")),
        "pass_epa": _f(row.get("passing_epa")),
        "pass_cpoe": _f(row.get("passing_cpoe")),
        "pass_air_yards": _i(row.get("passing_air_yards")),
        "pass_yac": _i(row.get("passing_yards_after_catch")),
        "sacks_suffered": _i(row.get("sacks_suffered")),
        # Rushing
        "rush_attempts": _i(row.get("carries")),
        "rush_yards": _i(row.get("rushing_yards")),
        "rush_tds": _i(row.get("rushing_tds")),
        "rush_epa": _f(row.get("rushing_epa")),
        "rush_first_downs": _i(row.get("rushing_first_downs")),
        # Receiving (separate from passing — captures YAC + target quality)
        "receiving_epa": _f(row.get("receiving_epa")),
        # Defense
        "def_sacks": _i(row.get("def_sacks")),
        "def_ints": _i(row.get("def_interceptions")),
        "def_pass_def": _i(row.get("def_pass_defended")),
        "def_fumbles_forced": _i(row.get("def_fumbles_forced")),
        "def_tds": _i(row.get("def_tds")),
        # Penalties
        "penalties": _i(row.get("penalties")),
        "penalty_yards": _i(row.get("penalty_yards")),
        # Special teams
        "fg_made": _i(row.get("fg_made")),
        "fg_att": _i(row.get("fg_att")),
        "fg_pct": _f(row.get("fg_pct")),
        "fg_long": _i(row.get("fg_long")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_team_stats(rows):
    if not rows:
        return False
    r = rq.post(
        f"{SUPABASE_URL}/rest/v1/nfl_team_stats?on_conflict=team,season,season_type",
        headers=WRITE_HEADERS, json=rows, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ❌ Upsert error {r.status_code}: {r.text[:300]}")
        return False
    return True


def run(season=None):
    season = season or detect_current_season()
    print(f"=== NFL Phase 1 — team stats ({season}) ===")

    total_upserted = 0
    for season_type in ("reg", "post"):
        rows = fetch_team_stats(season, season_type)
        if not rows:
            continue
        print(f"  {season} {season_type.upper()}: {len(rows)} team-rows")
        transformed = [transform(r, season, season_type) for r in rows if r.get("team")]
        # Drop rows where required fields are missing
        valid = [t for t in transformed if t["team"] and t["season"]]
        if upsert_team_stats(valid):
            print(f"  ✅ Upserted {len(valid)} {season_type.upper()} team-stat rows")
            total_upserted += len(valid)

    print(f"\nTotal upserted: {total_upserted}")


if __name__ == "__main__":
    season_arg = None
    if "--season" in sys.argv:
        idx = sys.argv.index("--season")
        if idx + 1 < len(sys.argv):
            season_arg = int(sys.argv[idx + 1])
    run(season_arg)
