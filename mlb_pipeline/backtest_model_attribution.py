"""
Model attribution backtest.

For every graded game in mlb_game_results, asks: "given this set of
features, which model is reliable for which play type?"

Outputs the cohorts where a model's win rate deviates materially from
its own baseline — i.e. the conditional rules where signal exists.
That replaces hand-wavy "Jerry is hot this week" with data-discovered
"Jerry hits 61% when v4 agrees, 40% when they disagree."

USAGE:
  python backtest_model_attribution.py
  python backtest_model_attribution.py --days 14    # last 14 days only
  python backtest_model_attribution.py --min-n 20   # cohort floor

OUTPUTS:
  - stdout: ranked top cohorts (per model, per play type)
  - models/attribution_cohorts.json: machine-readable for downstream wiring
"""
import os
import sys
import json
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

OUT_PATH = Path(__file__).parent / "models" / "attribution_cohorts.json"


# ---------------------------------------------------------------- fetch

def fetch_rows(days=None):
    """Pull every graded row with at least the model + spread data we need.
    `days` filters to last N days; None pulls everything available."""
    params = {
        "home_score": "not.is.null",
        "signal_confluence_net": "not.is.null",
        "select": "*",
        "order": "game_date.desc",
    }
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        params["game_date"] = f"gte.{cutoff}"
    rows = []
    page = 0
    while True:
        params["offset"] = str(page * 1000)
        params["limit"] = "1000"
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results",
            params=params, headers=HEADERS, timeout=30,
        )
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    return rows


# ---------------------------------------------------------------- helpers

def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def ml_call(spread):
    """v3/v4/jerry spread sign: POSITIVE = home favored."""
    if spread is None or abs(spread) < 0.3:
        return None
    return "home" if spread > 0 else "away"


def rl_call(spread):
    """Pick the dog runline side as the default when spread mag < 1.5."""
    if spread is None:
        return None
    if spread > 1.5: return "home"
    if spread < -1.5: return "away"
    return "away" if spread < 0 else "home"


def conf_side(net):
    try: n = int(net)
    except (TypeError, ValueError): return None
    if n > 1: return "home"
    if n < -1: return "away"
    return None


def total_call(model_total, line):
    """Reasonable lean threshold — require ≥0.7 runs of edge to count."""
    if model_total is None or line is None: return None
    if model_total >= line + 0.7: return "over"
    if model_total <= line - 0.7: return "under"
    return None


def actual_ml(g):
    if g.get("home_win") is True: return "home"
    if g.get("home_win") is False: return "away"
    return None


def actual_rl(g):
    hs = g.get("home_score"); as_ = g.get("away_score")
    if hs is None or as_ is None: return None
    margin = abs(hs - as_)
    if margin <= 1: return "push"
    return "home" if hs > as_ else "away"


def actual_total(g):
    """Use close_total if available else open_total (early-game class)."""
    line = g.get("close_total") or g.get("open_total")
    hs = g.get("home_score"); as_ = g.get("away_score")
    if line is None or hs is None: return None, None
    total = hs + as_
    if total > line: return "over", line
    if total < line: return "under", line
    return "push", line


# ---------------------------------------------------------------- cohorts

def _band(val, bands):
    """Return label of the first band the value fits in, or None."""
    if val is None: return None
    for label, lo, hi in bands:
        if lo <= val < hi:
            return f"{label}"
    return None


def cohort_memberships(g):
    """Return dict of cohort_key -> bool/None indicating each cohort the
    game qualifies for. None = no signal (cohort not testable for this row)."""
    out = {}

    # Confluence magnitude bands
    cn = g.get("signal_confluence_net")
    if cn is not None:
        mag = abs(int(cn))
        out[f"conf_mag={mag}"] = True
        if mag >= 4: out["conf_mag>=4"] = True
        elif mag >= 2: out["conf_mag=2-3"] = True
        else: out["conf_mag<=1"] = True

    # Model agreement cohorts (ML direction)
    v3s = _f(g.get("projected_spread"))
    v4s = _f(g.get("model_pred_spread"))
    js = _f(g.get("jerry_pred_spread"))
    v3_dir = ml_call(v3s); v4_dir = ml_call(v4s); j_dir = ml_call(js)
    if v3_dir and v4_dir:
        out["v3_v4_agree"] = (v3_dir == v4_dir)
        out["v3_v4_disagree"] = (v3_dir != v4_dir)
    if v3_dir and j_dir:
        out["v3_jerry_agree"] = (v3_dir == j_dir)
        out["v3_jerry_disagree"] = (v3_dir != j_dir)
    if v4_dir and j_dir:
        out["v4_jerry_agree"] = (v4_dir == j_dir)
        out["v4_jerry_disagree"] = (v4_dir != j_dir)
    # All-three agreement
    if v3_dir and v4_dir and j_dir:
        out["all_models_agree"] = (v3_dir == v4_dir == j_dir)

    # Spread magnitude (v3 / v4 / Jerry)
    for name, spread in (("v3", v3s), ("v4", v4s), ("jerry", js)):
        if spread is None: continue
        mag = abs(spread)
        if mag >= 2.5: out[f"{name}_spread_loud"] = True
        elif mag >= 1.5: out[f"{name}_spread_mid"] = True
        else: out[f"{name}_spread_quiet"] = True

    # Park factor
    park = _f(g.get("park_run_factor"))
    if park is not None:
        if park >= 105: out["park_hitter_friendly"] = True
        elif park <= 95: out["park_pitcher_friendly"] = True
        else: out["park_neutral"] = True

    # Temperature
    temp = _f(g.get("temperature"))
    if temp is not None:
        if temp <= 50: out["temp_cold"] = True
        elif temp >= 80: out["temp_hot"] = True

    # Pitcher mastery vs team (≥15 IP sample required)
    for side in ("home", "away"):
        vt_era = _f(g.get(f"{side}_pitcher_vs_team_era"))
        vt_ip = _f(g.get(f"{side}_pitcher_vs_team_ip"))
        if vt_era is not None and vt_ip and vt_ip >= 15:
            if vt_era <= 2.5: out[f"{side}_sp_mastery"] = True
            elif vt_era >= 5.5: out[f"{side}_sp_blowup_history"] = True

    # Pitcher form drift (L3 - xERA)
    for side in ("home", "away"):
        l3 = _f(g.get(f"{side}_pitcher_last_3_era"))
        xera = _f(g.get(f"{side}_sp_xera"))
        if l3 is not None and xera is not None:
            drift = l3 - xera
            if drift >= 2.0: out[f"{side}_sp_form_drift_bad"] = True
            elif drift <= -1.5: out[f"{side}_sp_form_hot"] = True

    # wRC+ differential between teams (offense gap)
    hw = _f(g.get("home_wrc_plus")); aw = _f(g.get("away_wrc_plus"))
    if hw is not None and aw is not None:
        diff = hw - aw
        if abs(diff) >= 15:
            out["wrc_gap_loud"] = True
            out["wrc_gap_loud_home_better" if diff > 0 else "wrc_gap_loud_away_better"] = True

    # NRFI band (we have a v2 model — useful for game-state)
    nrfi = _f(g.get("nrfi_score"))
    if nrfi is not None:
        if nrfi >= 90: out["nrfi_high"] = True
        elif nrfi <= 25: out["yrfi_lean"] = True

    # Lineup confirmed flag (only sometimes)
    return out


# ---------------------------------------------------------------- scoring

def score_play(call, actual):
    """Returns 'W'|'L'|'P' or None when not applicable."""
    if call is None or actual is None: return None
    if actual == "push": return "P"
    return "W" if call == actual else "L"


PLAY_TYPES = [
    # (label, model_key, predict_fn)
    ("v3_ml", lambda g: ml_call(_f(g.get("projected_spread")))),
    ("v4_ml", lambda g: ml_call(_f(g.get("model_pred_spread")))),
    ("jerry_ml", lambda g: ml_call(_f(g.get("jerry_pred_spread")))),
    ("conf_ml", lambda g: conf_side(g.get("signal_confluence_net"))),

    ("v3_rl", lambda g: rl_call(_f(g.get("projected_spread")))),
    ("v4_rl", lambda g: rl_call(_f(g.get("model_pred_spread")))),
    ("jerry_rl", lambda g: rl_call(_f(g.get("jerry_pred_spread")))),
    ("conf_rl", lambda g: conf_side(g.get("signal_confluence_net"))),

    ("v3_tot", lambda g: total_call(_f(g.get("projected_total")), g.get("close_total") or g.get("open_total"))),
    ("v4_tot", lambda g: total_call(_f(g.get("model_pred_total")), g.get("close_total") or g.get("open_total"))),
    ("jerry_tot", lambda g: total_call(_f(g.get("jerry_pred_total")), g.get("close_total") or g.get("open_total"))),
]


def actual_for_play(play_label, g):
    if play_label.endswith("_ml"): return actual_ml(g)
    if play_label.endswith("_rl"): return actual_rl(g)
    if play_label.endswith("_tot"): return actual_total(g)[0]
    return None


# ---------------------------------------------------------------- driver

def run(days=None, min_n=15, deviation=4.0):
    print(f"[attribution] fetching graded rows{' (last ' + str(days) + ' days)' if days else ''}...")
    rows = fetch_rows(days=days)
    print(f"  {len(rows)} rows pulled")
    if not rows:
        return

    # Baselines: each play_type's overall win rate
    baseline = {}  # play_label -> {w, l, p, n, pct}
    for play_label, predict in PLAY_TYPES:
        tally = {"w": 0, "l": 0, "p": 0}
        for g in rows:
            res = score_play(predict(g), actual_for_play(play_label, g))
            if res == "W": tally["w"] += 1
            elif res == "L": tally["l"] += 1
            elif res == "P": tally["p"] += 1
        n = tally["w"] + tally["l"]
        baseline[play_label] = {
            **tally, "n": n,
            "pct": round(100 * tally["w"] / n, 1) if n else None,
        }

    # Conditional cohorts: (play_label, cohort_key) -> tally
    conditional = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
    for g in rows:
        cohorts = cohort_memberships(g)
        for play_label, predict in PLAY_TYPES:
            res = score_play(predict(g), actual_for_play(play_label, g))
            if res is None: continue
            for cohort_key, is_member in cohorts.items():
                if not is_member: continue
                t = conditional[(play_label, cohort_key)]
                if res == "W": t["w"] += 1
                elif res == "L": t["l"] += 1
                elif res == "P": t["p"] += 1

    # Score & rank: how interesting is each (play_label, cohort) pair?
    findings = []
    for (play_label, cohort_key), t in conditional.items():
        n = t["w"] + t["l"]
        if n < min_n:
            continue
        pct = 100 * t["w"] / n
        base = baseline[play_label]["pct"] or 0
        delta = pct - base
        if abs(delta) < deviation:
            continue
        # Score = absolute deviation × log sample size, favors big effects + big samples
        score = abs(delta) * math.log(n)
        findings.append({
            "play": play_label,
            "cohort": cohort_key,
            "win_pct": round(pct, 1),
            "baseline_pct": base,
            "delta_pp": round(delta, 1),
            "n": n,
            "wins": t["w"],
            "losses": t["l"],
            "pushes": t["p"],
            "score": round(score, 2),
        })
    findings.sort(key=lambda x: -x["score"])

    # ---- output ----
    print()
    print("=" * 110)
    print(f"BASELINES (overall win rate per play type)")
    print("=" * 110)
    for play_label, predict in PLAY_TYPES:
        b = baseline[play_label]
        print(f"  {play_label:<14} {b['w']}-{b['l']}-{b['p']}P (n={b['n']}, {b['pct']}%)")

    print()
    print("=" * 110)
    print(f"TOP COHORTS BY SIGNAL STRENGTH  (min n={min_n}, |delta| >= {deviation}pp)")
    print("=" * 110)
    print(f"  {'PLAY':<14} {'COHORT':<30} {'WIN%':>6} {'BASE%':>6} {'DELTA':>6} {'n':>5}  {'W-L':>10}")
    print("-" * 110)
    for f in findings[:40]:
        d = f"+{f['delta_pp']}" if f['delta_pp'] >= 0 else f"{f['delta_pp']}"
        print(f"  {f['play']:<14} {f['cohort']:<30} {f['win_pct']:>5}% {f['baseline_pct']:>5}% {d:>6}pp {f['n']:>5}  {f['wins']:>3}-{f['losses']:<3}")

    # ---- write JSON ----
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "rows_analyzed": len(rows),
        "days_window": days,
        "min_n": min_n,
        "deviation_threshold_pp": deviation,
        "baselines": baseline,
        "cohorts": findings,
    }, indent=2))
    print()
    print(f"[attribution] wrote {OUT_PATH} ({len(findings)} interesting cohorts)")


if __name__ == "__main__":
    days = None
    min_n = 15
    deviation = 4.0
    if "--days" in sys.argv:
        try: days = int(sys.argv[sys.argv.index("--days") + 1])
        except (IndexError, ValueError): pass
    if "--min-n" in sys.argv:
        try: min_n = int(sys.argv[sys.argv.index("--min-n") + 1])
        except (IndexError, ValueError): pass
    if "--deviation" in sys.argv:
        try: deviation = float(sys.argv[sys.argv.index("--deviation") + 1])
        except (IndexError, ValueError): pass
    run(days=days, min_n=min_n, deviation=deviation)
