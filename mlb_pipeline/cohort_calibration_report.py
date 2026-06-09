"""
Nightly calibration check: did Phase 2 cohort tiers actually predict?

For each graded game on the target date, runs every model's play (ML/RL/Total)
through cohort_signals.evaluate_game_for_play and tallies the actual outcomes
by tier. Compares observed hit rate vs the predicted shrunken_pct range so
we can spot:

  - Calibrated tiers: observed ≈ predicted
  - Under-performing tiers: observed << predicted (LEAN/STRONG/LOCK that don't deliver)
  - Drifting cohorts: specific rules whose hit rate has materially declined
  - Hot cohorts: rules over-performing predictions

Also tracks per-cohort performance so we can spot drift in specific rules.

USAGE:
    python cohort_calibration_report.py            # yesterday (ET)
    python cohort_calibration_report.py --date 2026-06-08
    python cohort_calibration_report.py --since-days 7   # rolling 7-day window

Designed to run in the morning cron after resolve_game_results.py finishes,
producing a record we can compare day-over-day to track Phase 2's actual
production performance.
"""
import os
import sys
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def _et_date(offset_days=0):
    return (datetime.now(timezone.utc) - timedelta(hours=4) - timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def fetch_rows(date_from, date_to):
    rows = []
    page = 0
    while True:
        params = {
            "home_score": "not.is.null",
            "signal_confluence_net": "not.is.null",
            "game_date": f"gte.{date_from}",
            "select": "*",
            "order": "game_date.desc",
            "offset": str(page * 1000),
            "limit": "1000",
        }
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results",
            params=params, headers=HEADERS, timeout=30,
        )
        if r.status_code != 200: break
        batch = r.json()
        if not batch: break
        rows.extend([g for g in batch if g.get("game_date") and g["game_date"] <= date_to])
        if len(batch) < 1000: break
        page += 1
    return rows


# Play call helpers (mirror cohort_signals / backtest)
def ml_call(s):
    if s is None or abs(s) < 0.3: return None
    return "home" if s > 0 else "away"

def rl_call(s):
    if s is None: return None
    if s > 1.5: return "home"
    if s < -1.5: return "away"
    return "away" if s < 0 else "home"

def conf_side(net):
    n = _i(net)
    if n is None: return None
    if n > 1: return "home"
    if n < -1: return "away"
    return None

def total_call(mt, line):
    if mt is None or line is None: return None
    if mt >= line + 0.7: return "over"
    if mt <= line - 0.7: return "under"
    return None

def actual_ml(g):
    if g.get("home_win") is True: return "home"
    if g.get("home_win") is False: return "away"
    return None

def actual_rl(g):
    hs = g.get("home_score"); as_ = g.get("away_score")
    if hs is None or as_ is None: return None
    m = abs(hs - as_)
    if m <= 1: return "push"
    return "home" if hs > as_ else "away"

def actual_total(g):
    line = g.get("close_total") or g.get("open_total")
    hs = g.get("home_score"); as_ = g.get("away_score")
    if line is None or hs is None: return None
    t = hs + as_
    if t > line: return "over"
    if t < line: return "under"
    return "push"


PLAY_CALL = {
    "v3_ml":    lambda g: ml_call(_f(g.get("projected_spread"))),
    "v4_ml":    lambda g: ml_call(_f(g.get("model_pred_spread"))),
    "jerry_ml": lambda g: ml_call(_f(g.get("jerry_pred_spread"))),
    "conf_ml":  lambda g: conf_side(g.get("signal_confluence_net")),
    "v3_rl":    lambda g: rl_call(_f(g.get("projected_spread"))),
    "v4_rl":    lambda g: rl_call(_f(g.get("model_pred_spread"))),
    "jerry_rl": lambda g: rl_call(_f(g.get("jerry_pred_spread"))),
    "conf_rl":  lambda g: conf_side(g.get("signal_confluence_net")),
    "v3_tot":   lambda g: total_call(_f(g.get("projected_total")), _f(g.get("close_total")) or _f(g.get("open_total"))),
    "v4_tot":   lambda g: total_call(_f(g.get("model_pred_total")), _f(g.get("close_total")) or _f(g.get("open_total"))),
    "jerry_tot":lambda g: total_call(_f(g.get("jerry_pred_total")), _f(g.get("close_total")) or _f(g.get("open_total"))),
}

ACTUAL_FN = {
    "v3_ml": actual_ml, "v4_ml": actual_ml, "jerry_ml": actual_ml, "conf_ml": actual_ml,
    "v3_rl": actual_rl, "v4_rl": actual_rl, "jerry_rl": actual_rl, "conf_rl": actual_rl,
    "v3_tot": actual_total, "v4_tot": actual_total, "jerry_tot": actual_total,
}


# Tier predicted ranges (from refresh_cohort_signals.TIER_THRESHOLDS)
TIER_RANGES = {
    "LOCK":        (75, 100),
    "STRONG_EDGE": (65, 75),
    "LEAN":        (60, 65),
    "NEUTRAL":     (45, 60),
    "SOFT_FADE":   (35, 45),
    "FADE":        (28, 35),
    "HARD_FADE":   (0, 28),
}


def _score_play(call, actual):
    if call is None or actual is None: return None
    if actual == "push": return "P"
    return "W" if call == actual else "L"


def run(date_from, date_to):
    print(f"=== Cohort calibration report — {date_from} to {date_to} ===\n")

    rows = fetch_rows(date_from, date_to)
    print(f"  Graded games in window: {len(rows)}")
    if not rows:
        print("  No graded games in window. Exit.")
        return

    sys.path.insert(0, str(Path(__file__).parent))
    from cohort_signals import evaluate_game_for_play

    # Per-tier tally
    tier_tally = defaultdict(lambda: {"w": 0, "l": 0, "p": 0, "n": 0})
    # Per-cohort tally for the loud (LOCK / STRONG_EDGE / FADE / HARD_FADE) rules
    cohort_tally = defaultdict(lambda: {"w": 0, "l": 0, "p": 0, "tier": None, "play": None, "shrunken_pct": None})
    # Per-play overall (game-level)
    play_tally = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})

    for g in rows:
        for play, predict in PLAY_CALL.items():
            call = predict(g)
            if call is None: continue
            actual = ACTUAL_FN[play](g)
            res = _score_play(call, actual)
            if res is None: continue

            # Track per-play base hit rate (no cohort applied)
            pt = play_tally[play]
            if res == "W": pt["w"] += 1
            elif res == "L": pt["l"] += 1
            elif res == "P": pt["p"] += 1

            # Evaluate cohort matches for this play+direction
            matches = evaluate_game_for_play(g, play, direction=call) or []
            for m in matches:
                tier = m.get("tier") or "UNKNOWN"
                # Track tier-level
                tt = tier_tally[tier]
                tt["n"] += 1
                if res == "W": tt["w"] += 1
                elif res == "L": tt["l"] += 1
                elif res == "P": tt["p"] += 1
                # Track per-cohort
                key = (m.get("matches_if_raw"), play, m.get("direction"))
                ct = cohort_tally[key]
                ct["tier"] = tier
                ct["play"] = play
                ct["shrunken_pct"] = m.get("shrunken_pct")
                if res == "W": ct["w"] += 1
                elif res == "L": ct["l"] += 1
                elif res == "P": ct["p"] += 1

    # ── Tier calibration report ──
    print()
    print("=" * 105)
    print(f"{'TIER':<14} {'OBSERVED':<22} {'PRED RANGE':<14} {'VERDICT':<22} {'NOTE'}")
    print("-" * 105)
    POSITIVE_TIERS = {"LOCK", "STRONG_EDGE", "LEAN"}
    FADE_TIERS = {"SOFT_FADE", "FADE", "HARD_FADE"}
    for tier in ("LOCK", "STRONG_EDGE", "LEAN", "NEUTRAL", "SOFT_FADE", "FADE", "HARD_FADE"):
        t = tier_tally.get(tier) or {}
        n = (t.get("w") or 0) + (t.get("l") or 0)
        if n == 0:
            print(f"  {tier:<14} {'no fires this window':<22} {'-':<14} {'-':<22}")
            continue
        observed_pct = round(100 * (t.get("w") or 0) / n, 1)
        lo, hi = TIER_RANGES[tier]
        sample_flag = "  [n<10]" if n < 10 else ""

        # Verdict: tier-aware. For positive tiers we want observed >= pred range.
        # For fade tiers we want observed <= pred range (because the play lost = fade worked).
        if tier in POSITIVE_TIERS:
            if observed_pct >= lo - 3:
                verdict = "DELIVERING" + sample_flag
                note = "edge real" if observed_pct >= lo else "near boundary"
            elif observed_pct >= lo - 10:
                verdict = "near floor"
                note = "watch — close to range"
            else:
                verdict = "UNDERPERFORMING"
                note = "tier predicting more than landing"
        elif tier in FADE_TIERS:
            # Lower observed = better fade. predicted hit % is also the floor where it stops being a fade.
            if observed_pct <= hi:
                verdict = "FADE CORRECT" + sample_flag
                note = "side picked lost as predicted"
            elif observed_pct <= hi + 10:
                verdict = "marginal fade"
                note = "watch — losing the fade edge"
            else:
                verdict = "FADE FAILED"
                note = "side picked won despite cohort fade"
        else:
            # NEUTRAL: not actionable. Skip nuanced labeling.
            verdict = "neutral"
            note = "no action band"

        observed_str = f"{(t.get('w') or 0)}-{(t.get('l') or 0)}-{(t.get('p') or 0)}P ({observed_pct}%)"
        print(f"  {tier:<14} {observed_str:<22} {lo}-{hi}%{'':<6} {verdict:<22} {note}")

    # ── Per-play base hit rate (for context) ──
    print()
    print("=" * 95)
    print("BASE PLAY HIT RATES (no cohort filter — context only)")
    print("=" * 95)
    for play in PLAY_CALL:
        t = play_tally.get(play) or {}
        n = (t.get("w") or 0) + (t.get("l") or 0)
        if n == 0: continue
        pct = round(100 * (t.get("w") or 0) / n, 1)
        print(f"  {play:<12} {(t.get('w') or 0)}-{(t.get('l') or 0)}-{(t.get('p') or 0)}P ({pct}%)")

    # ── Top cohorts (best/worst performers) ──
    print()
    print("=" * 95)
    print("TOP PERFORMING COHORTS (loud tiers, sorted by lift vs prediction)")
    print("=" * 95)
    print(f"  {'PLAY':<10} {'COHORT':<35} {'TIER':<13} {'OBS':<14} {'PRED':<8} DELTA")
    print("-" * 95)
    rows_out = []
    for (cohort, play, direction), t in cohort_tally.items():
        n = t["w"] + t["l"]
        if n < 1: continue
        if t.get("tier") not in ("LOCK", "STRONG_EDGE", "FADE", "HARD_FADE"): continue
        observed = round(100 * t["w"] / n, 1)
        pred = t.get("shrunken_pct") or 0
        # For FADEs, the "lift" is how much LOWER observed is than the baseline
        # (which means the fade is right) — but our pred is the model's hit rate.
        # We'll report observed vs pred as-is; user interprets context.
        lift = round(observed - pred, 1)
        rows_out.append((play, cohort, t["tier"], t["w"], t["l"], t["p"], n, observed, pred, lift))

    # Sort by absolute lift (interest signal)
    rows_out.sort(key=lambda r: -abs(r[9]))
    for row in rows_out[:15]:
        play, cohort, tier, w, l, p, n, obs, pred, lift = row
        ds = f"+{lift}" if lift >= 0 else f"{lift}"
        obs_str = f"{w}-{l}-{p}P ({obs}%)"
        print(f"  {play:<10} {cohort[:33]:<35} {tier:<13} {obs_str:<14} {pred}%   {ds}pp")

    # ── Headline ──
    print()
    print("=" * 95)
    lock_t = tier_tally.get("LOCK") or {}
    se_t = tier_tally.get("STRONG_EDGE") or {}
    fade_t = tier_tally.get("FADE") or {}
    hf_t = tier_tally.get("HARD_FADE") or {}
    lock_n = (lock_t.get("w") or 0) + (lock_t.get("l") or 0)
    se_n = (se_t.get("w") or 0) + (se_t.get("l") or 0)
    fade_n = (fade_t.get("w") or 0) + (fade_t.get("l") or 0)
    hf_n = (hf_t.get("w") or 0) + (hf_t.get("l") or 0)

    print("HEADLINE")
    if lock_n:
        lock_pct = round(100*(lock_t.get('w') or 0)/lock_n, 1)
        print(f"  LOCK rules:        {(lock_t.get('w') or 0)}-{(lock_t.get('l') or 0)} ({lock_pct}%) — predicted 75-100%, ", end="")
        if lock_pct >= 70: print("DELIVERING")
        else: print("under — investigate")
    if se_n:
        se_pct = round(100*(se_t.get('w') or 0)/se_n, 1)
        print(f"  STRONG_EDGE rules: {(se_t.get('w') or 0)}-{(se_t.get('l') or 0)} ({se_pct}%) — predicted 65-75%, ", end="")
        if se_pct >= 60: print("delivering")
        else: print("under — investigate")
    fade_total_n = fade_n + hf_n
    if fade_total_n:
        fade_w = (fade_t.get('w') or 0) + (hf_t.get('w') or 0)
        fade_l = (fade_t.get('l') or 0) + (hf_t.get('l') or 0)
        fade_pct = round(100 * fade_w / fade_total_n, 1)
        # FADE tier means model-pick is unreliable. Lower observed = better fade.
        print(f"  FADE/HARD_FADE:    {fade_w}-{fade_l} ({fade_pct}%) — predicted <35%, ", end="")
        if fade_pct < 40: print("CORRECTLY FADED")
        else: print("FAILED — model pick won despite cohort fade")

    # ── Write JSON archive ──
    out_path = Path(__file__).parent / "models" / f"calibration_{date_from}_{date_to}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    archive = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "date_from": date_from, "date_to": date_to,
        "rows_analyzed": len(rows),
        "tier_tallies": {k: dict(v) for k, v in tier_tally.items()},
        "play_tallies": {k: dict(v) for k, v in play_tally.items()},
        "cohort_tallies": [
            {
                "cohort": k[0], "play": k[1], "direction": k[2],
                "tier": v.get("tier"), "shrunken_pct": v.get("shrunken_pct"),
                "w": v["w"], "l": v["l"], "p": v["p"],
            }
            for k, v in cohort_tally.items() if (v["w"] + v["l"]) >= 1
        ],
    }
    out_path.write_text(json.dumps(archive, indent=2))
    print()
    print(f"[calibration] wrote {out_path}")


if __name__ == "__main__":
    today = _et_date()
    date_to = _et_date(1)
    date_from = date_to
    if "--date" in sys.argv:
        try:
            date_from = sys.argv[sys.argv.index("--date") + 1]
            date_to = date_from
        except (IndexError, ValueError): pass
    if "--since-days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--since-days") + 1])
            date_from = _et_date(days)
            date_to = _et_date(1)
        except (IndexError, ValueError): pass
    run(date_from, date_to)
