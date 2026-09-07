"""NFL player-stats puller — Phase 2 prep.

Pulls per-player per-week stats from the nflverse player_stats release.
~5,500 rows per season covering every roster player who recorded a stat.

Used downstream by Phase 2 props pipeline (June-July build) to compute
rolling L4 / L8 / season averages for prop modeling. Mirrors pattern from
nfl_pipeline.py (team stats) and nba_pipeline.py.

Required SQL: 20260507b_nfl_player_stats.sql

Usage:
    python nfl_player_stats.py            # Current season (auto-detect)
    python nfl_player_stats.py --season 2024
    python nfl_player_stats.py --start 2022 --end 2025  # Backfill range
"""
import os
import sys
import csv
import io
import gzip
import urllib.request
from datetime import datetime, timezone
from dotenv import load_dotenv

import requests as rq

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}

NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"


def detect_current_season():
    now = datetime.now()
    if now.month <= 2:
        return now.year - 1
    if now.month <= 8:
        return now.year - 1
    return now.year


def fetch_player_stats(season):
    """Pull weekly player-stats CSV for one season.

    2026-09-07: nflverse restructured their releases. New URL pattern
    for 2025+ uses `stats_player/stats_player_week_YYYY.csv` (uncompressed).
    Legacy URL `player_stats/player_stats_YYYY.csv.gz` still works for
    2024 and earlier. Try new first, fall back to old for backward-compat
    with old seasons.
    """
    # Try new format first (2025+)
    new_url = f"{NFLVERSE_BASE}/stats_player/stats_player_week_{season}.csv"
    old_url = f"{NFLVERSE_BASE}/player_stats/player_stats_{season}.csv.gz"

    for url, is_gz in ((new_url, False), (old_url, True)):
        print(f"  Trying {url.split('/')[-1]}...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                if is_gz:
                    raw = gzip.decompress(raw)
            text = raw.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            print(f"  ✅ Got {len(rows):,} rows for {season}")
            return rows
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue  # Try next URL
            raise
        except Exception as e:
            print(f"  ⚠️  Error fetching {season} from {url.split('/')[-1]}: {e}")
            continue

    print(f"  ⚠️  No data for {season} (both URLs 404)")
    return []


def _i(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def transform(row):
    return {
        "player_id": row.get("player_id"),
        "player_name": row.get("player_display_name") or row.get("player_name"),
        "position": row.get("position") or None,
        "position_group": row.get("position_group") or None,
        "team": row.get("recent_team"),
        "season": _i(row.get("season")),
        "week": _i(row.get("week")),
        "season_type": (row.get("season_type") or "REG").upper(),
        "opponent_team": row.get("opponent_team") or None,
        # Passing
        "completions": _i(row.get("completions")),
        "attempts": _i(row.get("attempts")),
        "passing_yards": _i(row.get("passing_yards")),
        "passing_tds": _i(row.get("passing_tds")),
        "interceptions": _i(row.get("interceptions")),
        "sacks": _i(row.get("sacks")),
        "sack_yards": _i(row.get("sack_yards")),
        "passing_air_yards": _i(row.get("passing_air_yards")),
        "passing_yards_after_catch": _i(row.get("passing_yards_after_catch")),
        "passing_first_downs": _i(row.get("passing_first_downs")),
        "passing_epa": _f(row.get("passing_epa")),
        "pacr": _f(row.get("pacr")),
        "dakota": _f(row.get("dakota")),
        # Rushing
        "carries": _i(row.get("carries")),
        "rushing_yards": _i(row.get("rushing_yards")),
        "rushing_tds": _i(row.get("rushing_tds")),
        "rushing_first_downs": _i(row.get("rushing_first_downs")),
        "rushing_epa": _f(row.get("rushing_epa")),
        # Receiving
        "receptions": _i(row.get("receptions")),
        "targets": _i(row.get("targets")),
        "receiving_yards": _i(row.get("receiving_yards")),
        "receiving_tds": _i(row.get("receiving_tds")),
        "receiving_air_yards": _i(row.get("receiving_air_yards")),
        "receiving_yards_after_catch": _i(row.get("receiving_yards_after_catch")),
        "receiving_first_downs": _i(row.get("receiving_first_downs")),
        "receiving_epa": _f(row.get("receiving_epa")),
        "racr": _f(row.get("racr")),
        "target_share": _f(row.get("target_share")),
        "air_yards_share": _f(row.get("air_yards_share")),
        "wopr": _f(row.get("wopr")),
        "special_teams_tds": _i(row.get("special_teams_tds")),
        "fantasy_points": _f(row.get("fantasy_points")),
        "fantasy_points_ppr": _f(row.get("fantasy_points_ppr")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_batch(rows):
    if not rows:
        return 0
    r = rq.post(
        f"{SUPABASE_URL}/rest/v1/nfl_player_stats?on_conflict=player_id,season,week,season_type",
        headers=WRITE_HEADERS, json=rows, timeout=60,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ❌ Upsert error {r.status_code}: {r.text[:300]}")
        return 0
    return len(rows)


def run(seasons=None):
    seasons = seasons or [detect_current_season()]
    print(f"=== NFL player stats puller (seasons: {seasons}) ===")
    total = 0
    for season in seasons:
        rows = fetch_player_stats(season)
        if not rows:
            continue
        print(f"  {season}: {len(rows)} player-week rows")
        transformed = [transform(r) for r in rows
                       if r.get("player_id") and r.get("season") and r.get("week")]
        # Batch upsert in chunks of 200
        batch_total = 0
        for i in range(0, len(transformed), 200):
            chunk = transformed[i:i+200]
            n = upsert_batch(chunk)
            batch_total += n
        print(f"    Upserted {batch_total} rows")
        total += batch_total
    print(f"\n✅ Total upserted: {total}")


if __name__ == "__main__":
    seasons = None
    if "--season" in sys.argv:
        idx = sys.argv.index("--season")
        if idx + 1 < len(sys.argv):
            seasons = [int(sys.argv[idx + 1])]
    elif "--start" in sys.argv and "--end" in sys.argv:
        start = int(sys.argv[sys.argv.index("--start") + 1])
        end = int(sys.argv[sys.argv.index("--end") + 1])
        seasons = list(range(start, end + 1))
    run(seasons)
