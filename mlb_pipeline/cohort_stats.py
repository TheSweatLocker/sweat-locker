"""
Nightly cohort recompute.

Replaces hardcoded cohort percentages (82.6% DOG RL, 69.2% FAV ML, etc.)
with values computed fresh against the current graded-games dataset.

OUTPUT: a row in `jerry_cache` with cache_key='cohort_stats' holding a
flat dict keyed by cohort name. Each entry has { win_pct, n, computed_at,
expression }. Consumed by cohort_lookup.get_cohort() at render time.

Using jerry_cache (not a local JSON file) so the next morning's cron
runner — a fresh container — can read the values without filesystem
persistence. Same pattern as other shared cache entries.

USAGE:
  python cohort_stats.py            — recompute + upsert to jerry_cache
  python cohort_stats.py --dryrun   — print without writing
"""
import os
import sys
import json
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
WRITE_HEADERS = {**HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}

CACHE_KEY = "cohort_stats"


def fetch_graded_rows():
    """Pull rows that have BOTH a final outcome AND enough metadata to be
    cohort-classified. Paginates because PostgREST caps at 1000 per call."""
    rows = []
    page = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results",
            params={
                "home_score": "not.is.null",
                "signal_confluence_net": "not.is.null",
                "close_spread": "not.is.null",
                "select": "game_id,game_date,home_score,away_score,home_win,total_result,run_line_result,signal_confluence_net,close_spread,home_ml_close,away_ml_close",
                "order": "game_date.desc",
                "offset": str(page * 1000),
                "limit": "1000",
            },
            headers=HEADERS,
            timeout=30,
        )
        if r.status_code != 200:
            print(f"  fetch failed at page {page}: {r.status_code}")
            break
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    return rows


def _conf_side(net):
    """Confluence direction: positive = home, negative = away, 0 = neutral."""
    try:
        n = int(net)
    except (TypeError, ValueError):
        return None
    if n > 0:
        return "home"
    if n < 0:
        return "away"
    return None


def _dog_side(close_spread):
    """close_spread sign convention: NEGATIVE = home favored, POSITIVE = home dog."""
    try:
        cs = float(close_spread)
    except (TypeError, ValueError):
        return None
    if cs > 0:
        return "home"  # home is +1.5 dog
    if cs < 0:
        return "away"  # away is +1.5 dog
    return None


def _rl_result_for_side(side, run_line_result):
    """Did the dog cover its +1.5 runline? `run_line_result` is the winning
    runline side: 'home', 'away', or 'push'. The +1.5 dog covers when its
    side wins the runline, or pushes when push."""
    if not side or not run_line_result:
        return None
    if run_line_result == "push":
        return "push"
    return "win" if run_line_result == side else "loss"


def _ml_result_for_side(side, home_win):
    """Did the picked ML side win outright?"""
    if not side or home_win is None:
        return None
    if side == "home":
        return "win" if home_win else "loss"
    return "win" if not home_win else "loss"


def compute_cohorts(rows):
    """Returns dict of cohort_name -> {wins, losses, pushes, n, win_pct, expression}."""
    out = {}

    def init(name, expression):
        out[name] = {"wins": 0, "losses": 0, "pushes": 0, "n": 0, "expression": expression}

    def record(name, result):
        if result == "win":
            out[name]["wins"] += 1
            out[name]["n"] += 1
        elif result == "loss":
            out[name]["losses"] += 1
            out[name]["n"] += 1
        elif result == "push":
            out[name]["pushes"] += 1

    # Confluence × runline cohorts (the 82.6% PEAK family)
    for mag in (2, 3, 4, 5, 6):
        init(f"conf{mag}_dog_rl",
             f"|signal_confluence_net|={mag} AND confluence points to the DOG RL side")
        init(f"conf{mag}_fav_rl",
             f"|signal_confluence_net|={mag} AND confluence points to the FAV RL side")
        init(f"conf{mag}_dog_ml",
             f"|signal_confluence_net|={mag} AND confluence points to the DOG ML side")
        init(f"conf{mag}_fav_ml",
             f"|signal_confluence_net|={mag} AND confluence points to the FAV ML side")

    for g in rows:
        side = _conf_side(g.get("signal_confluence_net"))
        dog = _dog_side(g.get("close_spread"))
        if not side or not dog:
            continue
        try:
            mag = abs(int(g.get("signal_confluence_net")))
        except (TypeError, ValueError):
            continue
        if mag < 2 or mag > 6:
            continue
        is_dog = (side == dog)
        rl_res = _rl_result_for_side(side, g.get("run_line_result"))
        ml_res = _ml_result_for_side(side, g.get("home_win"))
        if is_dog:
            if rl_res: record(f"conf{mag}_dog_rl", rl_res)
            if ml_res: record(f"conf{mag}_dog_ml", ml_res)
        else:
            if rl_res: record(f"conf{mag}_fav_rl", rl_res)
            if ml_res: record(f"conf{mag}_fav_ml", ml_res)

    # Finalize: compute win_pct
    finalized = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for name, c in out.items():
        n = c["n"]
        finalized[name] = {
            "wins": c["wins"],
            "losses": c["losses"],
            "pushes": c["pushes"],
            "n": n,
            "win_pct": round(100.0 * c["wins"] / n, 1) if n else None,
            "expression": c["expression"],
            "computed_at": now_iso,
        }
    return finalized


def main():
    dryrun = "--dryrun" in sys.argv
    print(f"[cohort_stats] fetching graded game rows…")
    rows = fetch_graded_rows()
    print(f"  {len(rows)} rows pulled")
    stats = compute_cohorts(rows)
    # Print the cohorts that show up in user-facing copy
    print(f"\n[cohort_stats] computed {len(stats)} cohorts")
    for key in ("conf4_dog_rl", "conf4_fav_ml", "conf5_dog_rl", "conf6_dog_rl"):
        c = stats.get(key) or {}
        print(f"  {key}: {c.get('win_pct')}% (n={c.get('n')})")
    if dryrun:
        print("\n[cohort_stats] DRYRUN — not writing.")
        return
    payload = {
        "cache_key": CACHE_KEY,
        "game_id": CACHE_KEY,
        "sport": "mlb",
        "narrative": "",  # NOT NULL column — empty for cohort stats rows
        "data": json.dumps(stats),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/jerry_cache?on_conflict=cache_key",
        headers=WRITE_HEADERS, json=payload, timeout=15,
    )
    if r.status_code in (200, 201, 204):
        print(f"\n[cohort_stats] upserted jerry_cache row '{CACHE_KEY}' ({len(stats)} cohorts)")
    else:
        print(f"\n[cohort_stats] upsert failed {r.status_code}: {r.text[:300]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
