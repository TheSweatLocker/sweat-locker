"""Buy-down cohort hit-rate calibration (engine clarity refactor Phase 6).

Computes live buy-down cheat-line hit rates from resolved games so Jerry
can cite real numbers instead of the hardcoded backtest claims that were
stripped 2026-06-17.

For each resolved game, replays the buy-down classification logic from
generate_mlb_game_reads.py and grades the cheated line against the actual
total. Aggregates per (cohort, direction) over rolling 30d / 60d / 90d
windows. Writes to jerry_cache row `buy_down_calibration` (no new
schema needed).

Cohorts (mirror of generate_mlb_game_reads.py:buy_down_play logic):
  consensus_loud      — all 3 models populated, all loud (>=0.3 dead-band),
                        all agree direction, loudest edge >= 2.0
  model_edge_2        — at least one model loud >= 2.0, no opposition
                        (other models silent or same direction)
  consensus           — all 3 models populated and all agree, no loud requirement

Buy-down: cheated_line = close_total ± 2 runs (subtract for OVER, add
for UNDER). Hit = OVER side: actual > cheated_line / UNDER side:
actual < cheated_line.

Run:
  python buy_down_calibration.py          # nightly cron
  python buy_down_calibration.py --dry    # don't write, just print
  python buy_down_calibration.py --debug  # verbose per-game grading

Spec: docs/engine_clarity_refactor.md Phase 6.
"""
import os
import sys
import json
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS_R = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
HEADERS_W = {**HEADERS_R, "Content-Type": "application/json",
             "Prefer": "resolution=merge-duplicates,return=minimal"}

DEAD_BAND = 0.3
BUY_DOWN_RUNS = 2.0
MIN_N_TO_QUOTE = 30  # threshold for Jerry-citable hit rates


def _f(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _classify_buy_down(v3, v4, jr, close_total):
    """Replay buy_down classifier from generate_mlb_game_reads.py.
    Returns (cohort, direction) or (None, None) if game doesn't qualify."""
    if close_total is None:
        return None, None
    edges = []
    for v in (v3, v4, jr):
        edges.append(v - close_total if v is not None else None)
    loud_dirs = []
    for e in edges:
        if e is not None and abs(e) >= DEAD_BAND:
            loud_dirs.append("OVER" if e > 0 else "UNDER")
    all_loud_agree = len(loud_dirs) > 0 and all(d == loud_dirs[0] for d in loud_dirs)
    all_three_agree = (
        v3 is not None and v4 is not None and jr is not None
        and all(e is not None and abs(e) >= DEAD_BAND for e in edges)
        and all_loud_agree
    )
    loudest = max((abs(e) for e in edges if e is not None), default=0)
    qualifies = all_loud_agree and (all_three_agree or loudest >= 2.0)
    if not qualifies:
        return None, None
    direction = loud_dirs[0]
    if all_three_agree and loudest >= 2.0:
        return "consensus_loud", direction
    if loudest >= 2.0:
        return "model_edge_2", direction
    return "consensus", direction


def _grade(direction, close_total, actual_total):
    """Did the buy-down line cash?
    OVER buy-down: cheated to (close - 2). Hit if actual > cheated.
    UNDER buy-down: cheated to (close + 2). Hit if actual < cheated."""
    if direction == "OVER":
        cheated = close_total - BUY_DOWN_RUNS
        if actual_total > cheated:
            return "W"
        if actual_total < cheated:
            return "L"
        return "P"  # push exactly on cheated line
    if direction == "UNDER":
        cheated = close_total + BUY_DOWN_RUNS
        if actual_total < cheated:
            return "W"
        if actual_total > cheated:
            return "L"
        return "P"
    return None


def fetch_resolved_games(since_date):
    """Pull resolved games since `since_date` (inclusive) with the model
    projections + actual totals needed for grading."""
    cols = ("game_date,close_total,projected_total,model_pred_total,"
            "jerry_pred_total,home_score,away_score")
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/mlb_game_results",
        params={
            "game_date": f"gte.{since_date.isoformat()}",
            "select": cols,
            "order": "game_date.asc",
        },
        headers=HEADERS_R,
        timeout=20,
    )
    rows = r.json() if r.status_code == 200 else []
    # Recompute total_runs since the column is sometimes null even when
    # scores exist (resolver writes scores but not aggregate).
    out = []
    for g in rows:
        hs = g.get("home_score"); as_ = g.get("away_score")
        if hs is None or as_ is None:
            continue
        g["total_runs"] = int(hs) + int(as_)
        out.append(g)
    return out


def compute_calibration(games, debug=False):
    """Aggregate hit rates per (cohort, direction, window).
    Returns dict shaped for jerry_cache write.

    Windows: 30d / 60d / 90d ending today.
    """
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    windows = {
        "30d": today - timedelta(days=30),
        "60d": today - timedelta(days=60),
        "90d": today - timedelta(days=90),
    }

    # counts[(cohort, direction, window)] = {"w": int, "l": int, "p": int}
    counts = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})

    for g in games:
        try:
            gd = date.fromisoformat(g["game_date"])
        except (KeyError, ValueError):
            continue
        v3 = _f(g.get("projected_total"))
        v4 = _f(g.get("model_pred_total"))
        jr = _f(g.get("jerry_pred_total"))
        close = _f(g.get("close_total"))
        actual = _f(g.get("total_runs"))
        cohort, direction = _classify_buy_down(v3, v4, jr, close)
        if cohort is None:
            continue
        result = _grade(direction, close, actual)
        if result is None:
            continue
        if debug:
            print(f"  {gd} | {cohort:14s} {direction:5s} | close {close} v3 {v3} v4 {v4} jr {jr} | actual {actual} -> {result}")
        for win_label, win_start in windows.items():
            if gd >= win_start:
                counts[(cohort, direction, win_label)][result.lower()] += 1

    out_records = []
    for (cohort, direction, win_label), c in sorted(counts.items()):
        n = c["w"] + c["l"]  # exclude pushes from rate
        if n == 0:
            continue
        rate = c["w"] / n
        out_records.append({
            "cohort": cohort,
            "direction": direction,
            "window": win_label,
            "wins": c["w"],
            "losses": c["l"],
            "pushes": c["p"],
            "actionable_n": n,
            "hit_rate": round(rate, 4),
            "quotable": n >= MIN_N_TO_QUOTE,
        })
    return out_records


def upsert(records):
    """Write the calibration payload to jerry_cache row buy_down_calibration."""
    payload = {
        "cache_key": "buy_down_calibration",
        "game_id": "buy_down_calibration",  # NOT NULL constraint on game_id
        "sport": "mlb",
        "narrative": "",
        "data": json.dumps({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "min_n_to_quote": MIN_N_TO_QUOTE,
            "records": records,
        }),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=HEADERS_W,
        json=payload,
        timeout=15,
    )
    if r.status_code not in (200, 201, 204):
        print(f"[ERR] upsert failed {r.status_code}: {r.text[:300]}")
        return False
    return True


def run():
    dry = "--dry" in sys.argv
    debug = "--debug" in sys.argv
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    since = today - timedelta(days=120)  # pull 120d so 90d window has full coverage
    print(f"=== buy_down_calibration {today} (since {since}) ===")
    games = fetch_resolved_games(since)
    print(f"  Pulled {len(games)} resolved games")
    records = compute_calibration(games, debug=debug)
    print(f"  Computed {len(records)} (cohort x direction x window) cells")
    if not records:
        print("  [WARN] no records - nothing to write")
        return

    # Pretty-print summary
    print(f"\n  {'cohort':17s} {'dir':5s} {'win':4s} {'W':>3s}-{'L':>3s}-{'P':>3s} {'rate':>6s} {'quote?':>7s}")
    for r in records:
        flag = "Y" if r["quotable"] else "n"
        print(f"  {r['cohort']:17s} {r['direction']:5s} {r['window']:4s} "
              f"{r['wins']:>3d}-{r['losses']:>3d}-{r['pushes']:>3d} "
              f"{r['hit_rate']*100:>5.1f}% {flag:>7s}")

    if dry:
        print("\n  --dry: skipping write")
        return
    if upsert(records):
        print(f"\n  [OK] wrote buy_down_calibration ({len(records)} records)")


if __name__ == "__main__":
    run()
