"""
Model attribution backtest v4 — per-pitcher-class deep dive.

Inverts the usual question. Instead of "what features correlate with
model X being right?", asks: "given a starter class, which model is
best?"

PITCHER CLASS TAXONOMY tested:
  - xERA tier: elite (<=3.0) / solid (3.0-4.0) / mid (4.0-4.75) / shaky (>=4.75)
  - K% tier: high-K (>=26) / mid (22-26) / low (<22)
  - Form tier: hot L3 (L3-xERA <= -1.0) / steady / drifting (L3-xERA >= +2.0)
  - Matchup tier: vs strong bats (opp wRC+ >= 105) / vs weak (<= 95)
  - Workload tier: short rest (4d) / regular (5d) / long rest (>=7d)
  - Career tier: rookie/early-career (small sample on vs_team) / veteran (vs_team_ip >= 30)
  - 1st-inning class: clean (<=1.5) / shaky (>=6.0)
  - WHIP class (when populated): elite (<=1.10) / shaky (>=1.40)

DUAL CLASSIFICATION cohorts (joint conditions on BOTH starters):
  - Both elite (both <=3.0 xERA)
  - Both shaky (both >=4.75)
  - Mismatch (one elite, one shaky)
  - Both with form drift bad
  - Both with hot form

OUTPUT: models/attribution_cohorts_v4.json
USAGE: python backtest_model_attribution_v4_pitcher_class.py
"""
import os
import sys
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
OUT_PATH = Path(__file__).parent / "models" / "attribution_cohorts_v4.json"


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def fetch_rows():
    rows = []; page = 0
    while True:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/mlb_game_results",
            params={"home_score": "not.is.null", "signal_confluence_net": "not.is.null",
                    "select": "*", "order": "game_date.desc",
                    "offset": str(page * 1000), "limit": "1000"},
            headers=HEADERS, timeout=30)
        if r.status_code != 200: break
        batch = r.json()
        if not batch: break
        rows.extend(batch)
        if len(batch) < 1000: break
        page += 1
    return rows


# Play helpers (same shape)
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


# ---------------------------------------------------------------- pitcher class

def xera_tier(xera):
    if xera is None: return None
    if xera <= 3.0: return "elite"
    if xera <= 4.0: return "solid"
    if xera <= 4.75: return "mid"
    return "shaky"


def k_pct_tier(kp):
    if kp is None: return None
    if kp >= 26: return "high_k"
    if kp >= 22: return "mid_k"
    return "low_k"


def form_tier(l3, xera):
    if l3 is None or xera is None: return None
    d = l3 - xera
    if d >= 2.0: return "drift_bad"
    if d <= -1.5: return "form_hot"
    return "steady"


def workload_tier(days_rest):
    dr = _i(days_rest)
    if dr is None: return None
    if dr <= 4: return "short_rest"
    if dr >= 7: return "long_rest"
    return "reg_rest"


def first_inn_tier(fi):
    fi = _f(fi)
    if fi is None: return None
    if fi >= 6.0: return "shaky_1st"
    if fi <= 1.5: return "clean_1st"
    return None


def whip_tier(whip):
    whip = _f(whip)
    if whip is None: return None
    if whip <= 1.10: return "elite_whip"
    if whip >= 1.40: return "shaky_whip"
    return None


def cohort_memberships(g):
    out = {}

    # Per-side starter class
    for side in ("home", "away"):
        xera = _f(g.get(f"{side}_sp_xera"))
        kpct = _f(g.get(f"{side}_sp_k_pct"))
        l3 = _f(g.get(f"{side}_pitcher_last_3_era"))
        dr = g.get(f"{side}_sp_days_rest")
        fi = g.get(f"{side}_first_inning_era")

        xt = xera_tier(xera)
        kt = k_pct_tier(kpct)
        ft = form_tier(l3, xera)
        wt = workload_tier(dr)
        fit = first_inn_tier(fi)

        if xt: out[f"{side}_xera_{xt}"] = True
        if kt: out[f"{side}_kpct_{kt}"] = True
        if ft: out[f"{side}_form_{ft}"] = True
        if wt: out[f"{side}_workload_{wt}"] = True
        if fit: out[f"{side}_{fit}"] = True

    # ── DUAL classification (both starters) ──
    h_xera = _f(g.get("home_sp_xera")); a_xera = _f(g.get("away_sp_xera"))
    h_tier = xera_tier(h_xera); a_tier = xera_tier(a_xera)
    if h_tier and a_tier:
        if h_tier == "elite" and a_tier == "elite": out["both_aces"] = True
        elif h_tier == "shaky" and a_tier == "shaky": out["both_shaky"] = True
        elif (h_tier == "elite" and a_tier == "shaky"): out["mismatch_home_elite"] = True
        elif (a_tier == "elite" and h_tier == "shaky"): out["mismatch_away_elite"] = True
        # xERA gap (loud predictor)
        if h_xera and a_xera:
            gap = abs(h_xera - a_xera)
            if gap >= 1.5: out["xera_gap_loud"] = True
            elif gap <= 0.3: out["xera_gap_tight"] = True

    # Form: both starters tier
    h_l3 = _f(g.get("home_pitcher_last_3_era")); a_l3 = _f(g.get("away_pitcher_last_3_era"))
    h_form = form_tier(h_l3, h_xera); a_form = form_tier(a_l3, a_xera)
    if h_form and a_form:
        if h_form == "drift_bad" and a_form == "drift_bad": out["both_drifting"] = True
        elif h_form == "form_hot" and a_form == "form_hot": out["both_hot"] = True

    # K class joint: matchup of high-K starter vs low-K lineup (sweet spot for K props + low total)
    h_k = _f(g.get("home_sp_k_pct")); a_k = _f(g.get("away_sp_k_pct"))
    h_team_k = _f(g.get("home_team_k_pct")); a_team_k = _f(g.get("away_team_k_pct"))
    if h_k is not None and a_team_k is not None:
        # Home pitcher faces away team
        if h_k >= 28 and a_team_k >= 25: out["home_sp_high_k+away_team_high_k"] = True
        elif h_k <= 20 and a_team_k <= 20: out["home_sp_low_k+away_team_low_k"] = True
    if a_k is not None and h_team_k is not None:
        if a_k >= 28 and h_team_k >= 25: out["away_sp_high_k+home_team_high_k"] = True
        elif a_k <= 20 and h_team_k <= 20: out["away_sp_low_k+home_team_low_k"] = True

    # Career sample size on vs_team
    for side in ("home", "away"):
        vt_ip = _f(g.get(f"{side}_pitcher_vs_team_ip"))
        if vt_ip is None: continue
        if vt_ip >= 30: out[f"{side}_sp_vs_team_veteran"] = True
        elif vt_ip <= 10: out[f"{side}_sp_vs_team_rookie"] = True

    # Mismatch with form drift: ace facing struggling SP on other side
    if out.get("home_xera_elite") and out.get("away_form_drift_bad"):
        out["ace_home+drifting_away"] = True
    if out.get("away_xera_elite") and out.get("home_form_drift_bad"):
        out["ace_away+drifting_home"] = True

    # Both 1st-inn shaky (YRFI setup)
    if out.get("home_shaky_1st") and out.get("away_shaky_1st"):
        out["both_1st_inn_shaky"] = True
    if out.get("home_clean_1st") and out.get("away_clean_1st"):
        out["both_1st_inn_clean"] = True

    # 1D model-state for cross-correlation
    cn = _i(g.get("signal_confluence_net"))
    if cn is not None:
        mag = abs(cn)
        if mag == 4: out["conf_mag=4"] = True
        elif mag == 5: out["conf_mag=5"] = True

    return out


PLAY_TYPES = [
    ("v3_ml", lambda g: ml_call(_f(g.get("projected_spread")))),
    ("v4_ml", lambda g: ml_call(_f(g.get("model_pred_spread")))),
    ("jerry_ml", lambda g: ml_call(_f(g.get("jerry_pred_spread")))),
    ("conf_ml", lambda g: conf_side(g.get("signal_confluence_net"))),
    ("v3_rl", lambda g: rl_call(_f(g.get("projected_spread")))),
    ("v4_rl", lambda g: rl_call(_f(g.get("model_pred_spread")))),
    ("jerry_rl", lambda g: rl_call(_f(g.get("jerry_pred_spread")))),
    ("conf_rl", lambda g: conf_side(g.get("signal_confluence_net"))),
    ("v3_tot", lambda g: total_call(_f(g.get("projected_total")),
                                    _f(g.get("close_total")) or _f(g.get("open_total")))),
    ("v4_tot", lambda g: total_call(_f(g.get("model_pred_total")),
                                    _f(g.get("close_total")) or _f(g.get("open_total")))),
    ("jerry_tot", lambda g: total_call(_f(g.get("jerry_pred_total")),
                                       _f(g.get("close_total")) or _f(g.get("open_total")))),
]


def actual_for_play(play, g):
    if play.endswith("_ml"): return actual_ml(g)
    if play.endswith("_rl"): return actual_rl(g)
    if play.endswith("_tot"): return actual_total(g)
    return None


def score_play(call, actual):
    if call is None or actual is None: return None
    if actual == "push": return "P"
    return "W" if call == actual else "L"


def run(min_n=15, deviation=6.0):
    print("[attribution v4 / pitcher-class] fetching graded rows...")
    rows = fetch_rows()
    print(f"  {len(rows)} rows pulled")

    # Baselines
    baselines = {}
    overall = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
    for g in rows:
        for play, predict in PLAY_TYPES:
            res = score_play(predict(g), actual_for_play(play, g))
            if res is None: continue
            t = overall[play]
            if res == "W": t["w"] += 1
            elif res == "L": t["l"] += 1
            elif res == "P": t["p"] += 1
    for play, t in overall.items():
        n = t["w"] + t["l"]
        baselines[play] = {**t, "n": n, "pct": round(100*t["w"]/n, 1) if n else None}

    # Direction baselines
    splits = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
    for g in rows:
        for play, predict in PLAY_TYPES:
            call = predict(g)
            if call is None: continue
            res = score_play(call, actual_for_play(play, g))
            if res is None: continue
            t = splits[(play, call)]
            if res == "W": t["w"] += 1
            elif res == "L": t["l"] += 1
            elif res == "P": t["p"] += 1
    direction_baselines = {}
    for (play, d), t in splits.items():
        n = t["w"] + t["l"]
        direction_baselines[(play, d)] = {**t, "n": n, "pct": round(100*t["w"]/n, 1) if n else None}

    # Cohort tallies
    by_cohort = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
    for g in rows:
        cohorts = cohort_memberships(g)
        for play, predict in PLAY_TYPES:
            call = predict(g)
            if call is None: continue
            res = score_play(call, actual_for_play(play, g))
            if res is None: continue
            for ck in cohorts:
                t = by_cohort[(play, ck, "any")]
                if res == "W": t["w"] += 1
                elif res == "L": t["l"] += 1
                elif res == "P": t["p"] += 1
                t2 = by_cohort[(play, ck, call)]
                if res == "W": t2["w"] += 1
                elif res == "L": t2["l"] += 1
                elif res == "P": t2["p"] += 1

    findings = []
    for (play, ck, dirn), t in by_cohort.items():
        n = t["w"] + t["l"]
        if n < min_n: continue
        pct = 100 * t["w"] / n
        if dirn == "any":
            base = baselines[play]["pct"] or 0
        else:
            base = (direction_baselines.get((play, dirn)) or {}).get("pct") or baselines[play]["pct"] or 0
        delta = pct - base
        if abs(delta) < deviation: continue
        score = abs(delta) * math.log(n)
        findings.append({
            "play": play, "cohort": ck, "direction": dirn,
            "win_pct": round(pct, 1), "baseline_pct": base,
            "delta_pp": round(delta, 1), "n": n,
            "wins": t["w"], "losses": t["l"],
            "score": round(score, 2),
        })
    findings.sort(key=lambda x: -x["score"])

    print()
    print("=" * 130)
    print(f"TOP PITCHER-CLASS COHORTS  (min_n={min_n}, |delta|>={deviation}pp)")
    print("=" * 130)
    print(f"  {'PLAY':<14} {'DIR':<5} {'COHORT':<46} {'WIN%':>6} {'BASE%':>6} {'DELTA':>7} {'n':>4}  {'W-L':>8}")
    print("-" * 130)
    for f in findings[:60]:
        d = f"+{f['delta_pp']}" if f['delta_pp'] >= 0 else f"{f['delta_pp']}"
        print(f"  {f['play']:<14} {f['direction']:<5} {f['cohort']:<46} {f['win_pct']:>5}% {f['baseline_pct']:>5}% {d:>6}pp {f['n']:>4}  {f['wins']:>3}-{f['losses']:<3}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "rows_analyzed": len(rows),
        "min_n": min_n, "deviation_threshold_pp": deviation,
        "baselines": baselines, "cohorts": findings,
    }, indent=2))
    print(); print(f"[attribution v4] wrote {OUT_PATH} ({len(findings)} cohorts)")


if __name__ == "__main__":
    min_n = 15; deviation = 6.0
    if "--min-n" in sys.argv:
        try: min_n = int(sys.argv[sys.argv.index("--min-n") + 1])
        except (IndexError, ValueError): pass
    if "--deviation" in sys.argv:
        try: deviation = float(sys.argv[sys.argv.index("--deviation") + 1])
        except (IndexError, ValueError): pass
    run(min_n=min_n, deviation=deviation)
