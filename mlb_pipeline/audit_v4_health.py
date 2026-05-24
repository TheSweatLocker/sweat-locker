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
    # OVER/UNDER directional split (added 2026-05-24 for auto-throttle).
    # Asymmetric tracking — model's OVER bias is the specific leak.
    tot_over_w = tot_over_l = 0
    tot_under_w = tot_under_l = 0
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
            won = (tot_res == "over") == picks_over
            if won: tot_w += 1
            else: tot_l += 1
            if picks_over:
                if won: tot_over_w += 1
                else: tot_over_l += 1
            else:
                if won: tot_under_w += 1
                else: tot_under_l += 1
    return {
        "days_back": days_back,
        "ml": (ml_w, ml_l),
        "rl": (rl_w, rl_l),
        "tot": (tot_w, tot_l),
        "tot_over": (tot_over_w, tot_over_l),
        "tot_under": (tot_under_w, tot_under_l),
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

    # Compute OVER/UNDER hit rates (added 2026-05-24 for auto-throttle).
    # Threshold logic: suppress OVER picks when 7d hit rate < 50%.
    # Hysteresis: require 7d ≥ 52% to LIFT suppression (avoid flapping
    # at the 50% boundary). When n_7d < 10, default to existing
    # suppressed=True (no sample = don't trust).
    tot_over_7d = windows[7]["tot_over"]
    tot_under_7d = windows[7]["tot_under"]
    tot_over_30d = windows[30]["tot_over"]
    over_7d_n = sum(tot_over_7d)
    over_7d_pct = (tot_over_7d[0] / max(over_7d_n, 1)) * 100 if over_7d_n > 0 else None
    under_7d_pct = (tot_under_7d[0] / max(sum(tot_under_7d), 1)) * 100 if sum(tot_under_7d) > 0 else None
    over_30d_pct = (tot_over_30d[0] / max(sum(tot_over_30d), 1)) * 100 if sum(tot_over_30d) > 0 else None

    # Pull previous suppression state for hysteresis
    prev_over_suppressed = True  # default to safe (suppressed) on first run
    try:
        prev = get("model_health", model_version="eq.v4", order="computed_date.desc", limit="1")
        if prev and prev[0].get("over_suppressed") is not None:
            prev_over_suppressed = bool(prev[0]["over_suppressed"])
    except Exception:
        pass

    # Auto-flip with hysteresis
    if over_7d_n < 10:
        over_suppressed = True  # insufficient sample — stay safe
    elif prev_over_suppressed:
        # Currently suppressed — only lift when 7d ≥ 52% (need clear evidence)
        over_suppressed = over_7d_pct < 52.0
    else:
        # Currently unsuppressed — only re-suppress when 7d < 48% (don't flap)
        over_suppressed = over_7d_pct < 48.0

    print(f"\n--- OVER/UNDER SPLIT (auto-throttle) ---")
    if over_7d_pct is not None:
        print(f"  7d OVER:  {tot_over_7d[0]}-{tot_over_7d[1]} ({over_7d_pct:.1f}%)")
    if under_7d_pct is not None:
        print(f"  7d UNDER: {tot_under_7d[0]}-{tot_under_7d[1]} ({under_7d_pct:.1f}%)")
    if over_30d_pct is not None:
        print(f"  30d OVER: {tot_over_30d[0]}-{tot_over_30d[1]} ({over_30d_pct:.1f}%)")
    flip_note = " (FLIPPED)" if over_suppressed != prev_over_suppressed else ""
    print(f"  → over_suppressed: {over_suppressed}{flip_note}")

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
        "window_7d_total_over_w": tot_over_7d[0],
        "window_7d_total_over_l": tot_over_7d[1],
        "window_7d_total_over_pct": round(over_7d_pct, 2) if over_7d_pct is not None else None,
        "window_7d_total_under_w": tot_under_7d[0],
        "window_7d_total_under_l": tot_under_7d[1],
        "window_7d_total_under_pct": round(under_7d_pct, 2) if under_7d_pct is not None else None,
        "window_30d_total_over_w": tot_over_30d[0],
        "window_30d_total_over_l": tot_over_30d[1],
        "window_30d_total_over_pct": round(over_30d_pct, 2) if over_30d_pct is not None else None,
        "over_suppressed": over_suppressed,
        "under_suppressed": False,  # UNDER side healthy; no current suppression needed
        "status": status,
    }
    try:
        upsert("model_health", [payload], on_conflict="computed_date,model_version")
        print(f"\n✅ Wrote to model_health table")
    except Exception as e:
        print(f"\n⚠️  Could not write model_health: {e}")
        print(f"   (run migration 20260524_model_health_totals.sql first)")

    return 0 if status != "cold" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
