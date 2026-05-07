"""NFL backfill — pull historical seasons into nfl_game_results.

Loads the nflverse schedules CSV (covers all NFL seasons 1999-present,
single CSV file) and writes every game into nfl_game_results with
captured market lines + final outcomes.

Provides the audit baseline for nfl tier calibration cohorts. Without
this, Phase 1 audit cohorts have nothing to grade against.

Usage:
    python nfl_backfill_results.py                      # Default: 2025 only
    python nfl_backfill_results.py --season 2024        # Specific season
    python nfl_backfill_results.py --start 2022 --end 2025  # Range
    python nfl_backfill_results.py --all                # All seasons (1999+, ~7000+ games)
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

NFLVERSE_SCHEDULES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "schedules/games.csv.gz"
)


def fetch_all_games():
    """Pull the complete games.csv covering all NFL seasons."""
    print(f"  Fetching {NFLVERSE_SCHEDULES_URL.split('/')[-1]}...")
    req = urllib.request.Request(NFLVERSE_SCHEDULES_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = gzip.decompress(r.read())
    text = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


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


def _b(v):
    if v is None or v == "":
        return None
    s = str(v).lower().strip()
    if s in ("1", "true", "t", "yes"):
        return True
    if s in ("0", "false", "f", "no"):
        return False
    return None


def transform(row):
    """Map nflverse schedule row → nfl_game_results schema.

    Outcomes (home_score / away_score) populate when the game has been played.
    Resolver job updates spread/total_result post-game; we set the basic ones
    here from the final score for already-completed games.
    """
    home_score = _i(row.get("home_score"))
    away_score = _i(row.get("away_score"))
    close_spread = _f(row.get("spread_line"))
    close_total = _f(row.get("total_line"))

    # Compute outcomes for completed games
    home_win = None
    total_points = None
    spread_result = None
    total_result = None
    if home_score is not None and away_score is not None:
        home_win = home_score > away_score
        total_points = home_score + away_score
        # Spread result: spread_line is home spread (negative = favored)
        if close_spread is not None:
            margin = home_score - away_score  # positive = home wins
            home_cover_threshold = -close_spread  # home spread of -3 means need to win by > 3
            if margin > home_cover_threshold:
                spread_result = "home_covered"
            elif margin < home_cover_threshold:
                spread_result = "away_covered"
            else:
                spread_result = "push"
        # Total result
        if close_total is not None:
            if total_points > close_total:
                total_result = "over"
            elif total_points < close_total:
                total_result = "under"
            else:
                total_result = "push"

    return {
        "game_id": row.get("game_id"),
        "season": _i(row.get("season")),
        "week": _i(row.get("week")),
        "game_type": row.get("game_type"),
        "game_date": row.get("gameday") or None,
        "weekday": row.get("weekday"),
        "gametime": row.get("gametime"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        # Schedule has only one set of lines (closing); set both fields equal
        "open_spread": close_spread,
        "close_spread": close_spread,
        "open_total": close_total,
        "close_total": close_total,
        "open_home_ml": _i(row.get("home_moneyline")),
        "close_home_ml": _i(row.get("home_moneyline")),
        "open_away_ml": _i(row.get("away_moneyline")),
        "close_away_ml": _i(row.get("away_moneyline")),
        "stadium": row.get("stadium"),
        "roof": row.get("roof"),
        "surface": row.get("surface"),
        "temp": _i(row.get("temp")),
        "wind": _i(row.get("wind")),
        "div_game": _b(row.get("div_game")),
        "home_rest": _i(row.get("home_rest")),
        "away_rest": _i(row.get("away_rest")),
        "home_qb_id": row.get("home_qb_id"),
        "away_qb_id": row.get("away_qb_id"),
        "home_qb_name": row.get("home_qb_name"),
        "away_qb_name": row.get("away_qb_name"),
        "home_coach": row.get("home_coach"),
        "away_coach": row.get("away_coach"),
        "referee": row.get("referee"),
        "home_score": home_score,
        "away_score": away_score,
        "total_points": total_points,
        "home_win": home_win,
        "overtime": _b(row.get("overtime")),
        "spread_result": spread_result,
        "total_result": total_result,
        "result_logged_at": datetime.now(timezone.utc).isoformat() if home_score is not None else None,
    }


def upsert_batch(rows):
    if not rows:
        return 0
    r = rq.post(
        f"{SUPABASE_URL}/rest/v1/nfl_game_results?on_conflict=game_id",
        headers=WRITE_HEADERS, json=rows, timeout=60,
    )
    if r.status_code not in (200, 201, 204):
        print(f"  ❌ Upsert error {r.status_code}: {r.text[:300]}")
        return 0
    return len(rows)


def run(seasons=None):
    seasons = seasons or [2025]
    print(f"=== NFL backfill (seasons: {seasons}) ===")
    all_rows = fetch_all_games()
    print(f"  Total games in nflverse: {len(all_rows)}")
    target = [r for r in all_rows if _i(r.get("season")) in set(seasons)]
    print(f"  Filtered to target seasons: {len(target)}")

    transformed = [transform(r) for r in target if r.get("game_id")]
    valid = [t for t in transformed if t["game_id"] and t["season"]]
    completed = sum(1 for t in valid if t["home_score"] is not None)
    print(f"  Valid rows: {len(valid)} (completed games: {completed})")

    # Batch upsert in chunks of 200 to avoid request size limits
    total = 0
    for i in range(0, len(valid), 200):
        chunk = valid[i:i+200]
        n = upsert_batch(chunk)
        total += n
        print(f"  Upserted batch {i//200 + 1}: {n} rows ({total}/{len(valid)})")
    print(f"\n✅ Backfill complete: {total} game rows written")


if __name__ == "__main__":
    seasons = [2025]
    if "--all" in sys.argv:
        # All seasons since 1999 (start of nflverse coverage). Heavy.
        seasons = list(range(1999, datetime.now().year + 1))
    elif "--season" in sys.argv:
        idx = sys.argv.index("--season")
        if idx + 1 < len(sys.argv):
            seasons = [int(sys.argv[idx + 1])]
    elif "--start" in sys.argv and "--end" in sys.argv:
        start = int(sys.argv[sys.argv.index("--start") + 1])
        end = int(sys.argv[sys.argv.index("--end") + 1])
        seasons = list(range(start, end + 1))
    run(seasons)
