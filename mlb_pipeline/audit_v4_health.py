"""v4 model health check — detects sustained-cold direction accuracy.

Tracks v4 (XGBoost) model's direction-accuracy on resolved MLB games
over rolling 5d / 7d / 14d windows. When the 5d ML hit rate drops
below 50% for sustained periods, surfaces a warning so the system
can throttle (or downweight v4's confluence vote).

Output:
  - Console summary
  - Upserts to model_health table for ongoing tracking and so
    generate_sweat_card / play_of_day can read the current status
  - Returns 'cold' / 'warm' / 'hot' label

Cron: hooks into mlb_pipeline.yml nightly alongside the other audits.

Built 2026-05-23 — v4 ML 7d at 44.2%, below coinflip.
"""
import os, json, urllib.request, urllib.parse
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

load_dotenv()
URL = os.environ.get("SUPABASE_URL")
KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

# Health thresholds (ML direction-accuracy on v4)
COLD_THRESHOLD = 48.0      # below this on 5d = cold
HOT_THRESHOLD = 56.0       # above this on 5d = hot
MIN_SAMPLE_PER_WINDOW = 25  # need this many games before trusting rate


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


def grade_window(days_back, until_date=None):
    """Compute v4 ML/RL/Totals direction accuracy over the last N days."""
    until = until_date or datetime.now(timezone.utc) - timedelta(hours=4)
    start = until - timedelta(days=days_back)

    rows = get("mlb_game_results",
               select="game_date,home_win,total_runs,close_total,total_result,spread_result,model_pred_spread,model_pred_total",
               game_date=f"gte.{start.strftime('%Y-%m-%d')}")

    ml_w = ml_l = 0
    rl_w = rl_l = 0
    tot_w = tot_l = 0
    for g in rows:
        if g.get("home_win") is None:
            continue
        home_won = bool(g.get("home_win"))
        v4_sp = g.get("model_pred_spread")
        v4_tot = g.get("model_pred_total")
        ct = g.get("close_total")
        if v4_sp is not None:
            picks_home = float(v4_sp) > 0
            if picks_home == home_won: ml_w += 1
            else: ml_l += 1
        sp_res = (g.get("spread_result") or "").lower()
        if v4_sp is not None and sp_res in ("home_covered", "away_covered"):
            picks_home = float(v4_sp) > 0
            if (sp_res == "home_covered") == picks_home: rl_w += 1
            else: rl_l += 1
        tot_res = (g.get("total_result") or "").lower()
        if v4_tot is not None and ct is not None and tot_res in ("over", "under"):
            picks_over = float(v4_tot) > float(ct)
            if (tot_res == "over") == picks_over: tot_w += 1
            else: tot_l += 1
    return {
        "days_back": days_back,
        "ml": (ml_w, ml_l),
        "rl": (rl_w, rl_l),
        "tot": (tot_w, tot_l),
    }


def main():
    print(f"=== v4 MODEL HEALTH CHECK — {datetime.now(timezone.utc).isoformat()} ===")
    windows = {w: grade_window(w) for w in (5, 7, 14, 30)}
    for w, data in windows.items():
        mw, ml = data["ml"]
        rw, rl = data["rl"]
        tw, tl = data["tot"]
        ml_pct = (mw / max(mw+ml, 1)) * 100
        rl_pct = (rw / max(rw+rl, 1)) * 100
        tot_pct = (tw / max(tw+tl, 1)) * 100
        print(f"  Last {w:>2}d  ML {mw:>3}-{ml:<3} {ml_pct:5.1f}%  |  "
              f"RL {rw:>3}-{rl:<3} {rl_pct:5.1f}%  |  Total {tw:>3}-{tl:<3} {tot_pct:5.1f}%  "
              f"(n_ml={mw+ml})")

    # Classify health off the 5d ML window (most sensitive)
    five = windows[5]
    mw, ml = five["ml"]
    n5 = mw + ml
    pct5 = (mw / max(n5, 1)) * 100 if n5 > 0 else 0

    if n5 < MIN_SAMPLE_PER_WINDOW:
        status = "insufficient_sample"
    elif pct5 < COLD_THRESHOLD:
        status = "cold"
    elif pct5 > HOT_THRESHOLD:
        status = "hot"
    else:
        status = "neutral"

    print(f"\nSTATUS: {status} (5d ML {pct5:.1f}% on n={n5})")

    # Recommendations based on status
    if status == "cold":
        print("\n📉 v4 is cold. Recommended runtime adjustments:")
        print("  - Drop v4 spread vote weight in confluence (vote count 1 → 0.5)")
        print("  - Raise PRIME conviction floor for game-side primary plays by 5pt")
        print("  - generate_sweat_card auto-suppression already provides protection")
        print("    on the cohort layer; this just adds a model-layer guard.")
    elif status == "hot":
        print("\n📈 v4 is hot. Normal weighting; consider easing PRIME floor.")
    elif status == "neutral":
        print("\n⚖️  v4 within normal range. No adjustments needed.")
    else:
        print("\n❓ Insufficient sample to classify (<25 graded games on 5d).")

    # Upsert to model_health table
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")
    payload = {
        "computed_date": today,
        "model_version": "v4",
        "window_5d_ml_w": windows[5]["ml"][0],
        "window_5d_ml_l": windows[5]["ml"][1],
        "window_5d_ml_pct": round(pct5, 2) if n5 > 0 else None,
        "window_7d_ml_w": windows[7]["ml"][0],
        "window_7d_ml_l": windows[7]["ml"][1],
        "window_7d_ml_pct": round((windows[7]["ml"][0] / max(sum(windows[7]["ml"]), 1)) * 100, 2),
        "window_14d_ml_w": windows[14]["ml"][0],
        "window_14d_ml_l": windows[14]["ml"][1],
        "window_14d_ml_pct": round((windows[14]["ml"][0] / max(sum(windows[14]["ml"]), 1)) * 100, 2),
        "window_30d_ml_w": windows[30]["ml"][0],
        "window_30d_ml_l": windows[30]["ml"][1],
        "window_30d_ml_pct": round((windows[30]["ml"][0] / max(sum(windows[30]["ml"]), 1)) * 100, 2),
        "status": status,
    }
    try:
        upsert("model_health", [payload], on_conflict="computed_date,model_version")
        print(f"\n✅ Wrote to model_health table")
    except Exception as e:
        print(f"\n⚠️  Could not write model_health: {e}")
        print(f"   (run migration 20260523_model_health.sql first)")

    return 0 if status != "cold" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
