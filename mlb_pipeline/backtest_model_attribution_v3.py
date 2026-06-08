"""
Model attribution backtest v3 — 3-way interaction cohorts.

Builds on v2 by adding ~40 cherry-picked 3-feature combinations to test
whether compound signal stacks lift the strong 2D cohorts even higher (or
expose hidden fade conditions inside them). No combinatorial explosion;
each 3-way is hand-selected to either (a) further sharpen an existing
loud signal or (b) test a counterintuitive hypothesis.

USAGE:
  python backtest_model_attribution_v3.py
  python backtest_model_attribution_v3.py --min-n 15 --deviation 8.0

OUTPUT: models/attribution_cohorts_v3.json
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

OUT_PATH = Path(__file__).parent / "models" / "attribution_cohorts_v3.json"


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v)
    except (TypeError, ValueError): return None


# ---------------------------------------------------------------- fetch

def fetch_rows():
    rows = []
    page = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mlb_game_results",
            params={
                "home_score": "not.is.null",
                "signal_confluence_net": "not.is.null",
                "select": "*",
                "order": "game_date.desc",
                "offset": str(page * 1000),
                "limit": "1000",
            },
            headers=HEADERS, timeout=30,
        )
        if r.status_code != 200: break
        batch = r.json()
        if not batch: break
        rows.extend(batch)
        if len(batch) < 1000: break
        page += 1
    return rows


# ---------------------------------------------------------------- play helpers

def ml_call(spread):
    if spread is None or abs(spread) < 0.3: return None
    return "home" if spread > 0 else "away"


def rl_call(spread):
    if spread is None: return None
    if spread > 1.5: return "home"
    if spread < -1.5: return "away"
    return "away" if spread < 0 else "home"


def conf_side(net):
    n = _i(net)
    if n is None: return None
    if n > 1: return "home"
    if n < -1: return "away"
    return None


def total_call(model_total, line):
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
    line = g.get("close_total") or g.get("open_total")
    hs = g.get("home_score"); as_ = g.get("away_score")
    if line is None or hs is None: return None
    total = hs + as_
    if total > line: return "over"
    if total < line: return "under"
    return "push"


def home_is_dog(g):
    cs = _f(g.get("close_spread")) or _f(g.get("open_spread"))
    if cs is None: return None
    return cs > 0


# ---------------------------------------------------------------- cohorts (1D + 2D + 3D)

def cohort_memberships(g):
    """Same as v2 plus 3-way interactions."""
    out = {}

    # ─── 1D features (same as v2, abbreviated) ───
    cn = _i(g.get("signal_confluence_net"))
    mag = abs(cn) if cn is not None else None
    if mag is not None:
        out[f"conf_mag={mag}"] = True
        if mag >= 4: out["conf_mag>=4"] = True
        if mag == 3: out["conf_mag=3"] = True

    cs_dir = conf_side(cn) if cn is not None else None
    hd = home_is_dog(g)
    if cs_dir and hd is not None:
        out["conf_points_to_dog" if (cs_dir == "home" and hd) or (cs_dir == "away" and not hd) else "conf_points_to_fav"] = True

    v3s = _f(g.get("projected_spread"))
    v4s = _f(g.get("model_pred_spread"))
    js = _f(g.get("jerry_pred_spread"))
    v3d, v4d, jd = ml_call(v3s), ml_call(v4s), ml_call(js)
    if v3d and v4d:
        out["v3_v4_agree" if v3d == v4d else "v3_v4_disagree"] = True
    if v4d and jd:
        out["v4_jerry_agree" if v4d == jd else "v4_jerry_disagree"] = True

    # Spread magnitude
    for name, spread in (("v3", v3s), ("v4", v4s)):
        if spread is None: continue
        m = abs(spread)
        if m >= 2.0: out[f"{name}_spread_loud"] = True

    # Total magnitude
    line = _f(g.get("close_total")) or _f(g.get("open_total"))
    for name, mt in (("v3", _f(g.get("projected_total"))),
                     ("v4", _f(g.get("model_pred_total")))):
        if mt is None or line is None: continue
        d = mt - line
        if abs(d) >= 2.0: out[f"{name}_tot_loud"] = True
        elif abs(d) >= 1.0: out[f"{name}_tot_mid"] = True
        if d >= 1.0: out[f"{name}_tot_over_lean"] = True
        elif d <= -1.0: out[f"{name}_tot_under_lean"] = True

    # Park
    park = _f(g.get("park_run_factor"))
    if park is not None:
        if park >= 108: out["park_extreme_hitter"] = True
        elif park >= 104: out["park_hitter_friendly"] = True
        elif park <= 92: out["park_extreme_pitcher"] = True
        elif park <= 96: out["park_pitcher_friendly"] = True

    # Temperature
    temp = _f(g.get("temperature"))
    if temp is not None:
        if temp <= 50: out["temp_freezing"] = True
        elif temp <= 60: out["temp_cool"] = True
        elif temp >= 80: out["temp_hot"] = True

    # Form drift / mastery / blowup history
    for side in ("home", "away"):
        l3 = _f(g.get(f"{side}_pitcher_last_3_era"))
        xera = _f(g.get(f"{side}_sp_xera"))
        if l3 is not None and xera is not None:
            d = l3 - xera
            if d >= 2.0: out[f"{side}_sp_form_drift_bad"] = True
            elif d <= -1.5: out[f"{side}_sp_form_hot"] = True

        vt_era = _f(g.get(f"{side}_pitcher_vs_team_era"))
        vt_ip = _f(g.get(f"{side}_pitcher_vs_team_ip"))
        if vt_era is not None and vt_ip and vt_ip >= 15:
            if vt_era <= 2.5: out[f"{side}_sp_mastery"] = True
            elif vt_era >= 5.5: out[f"{side}_sp_blowup_history"] = True

    # xERA tier
    for side in ("home", "away"):
        xera = _f(g.get(f"{side}_sp_xera"))
        if xera is None: continue
        if xera <= 3.0: out[f"{side}_sp_elite"] = True
        elif xera >= 5.0: out[f"{side}_sp_shaky"] = True

    # K-gap loud
    for side in ("home", "away"):
        kg = _f(g.get(f"{side}_k_gap"))
        if kg is None: continue
        if kg >= 3.0: out[f"{side}_k_gap_loud_plus"] = True

    # Bullpen taxed
    for side in ("home", "away"):
        bp = _i(g.get(f"{side}_bp_relievers_3d"))
        if bp is None: continue
        if bp >= 8: out[f"{side}_bp_taxed"] = True
    if out.get("home_bp_taxed") or out.get("away_bp_taxed"):
        out["bullpen_taxed_either"] = True

    # 1st inning shaky
    for side in ("home", "away"):
        fi = _f(g.get(f"{side}_first_inning_era"))
        if fi is None: continue
        if fi >= 6.0: out[f"{side}_sp_1st_inn_shaky"] = True

    # Umpire
    un = (g.get("umpire_note") or "").lower()
    if "over-friendly" in un or "hitter-friendly" in un: out["ump_over"] = True
    elif "under-friendly" in un or "pitcher-friendly" in un: out["ump_under"] = True

    # 2D combos that anchor 3-way exploration
    if out.get("conf_mag=4") and out.get("conf_points_to_fav"): out["conf4+fav"] = True
    if out.get("conf_mag=4") and out.get("conf_points_to_dog"): out["conf4+dog"] = True

    # Opp lineup quality on the form-drift SP
    if out.get("home_sp_form_drift_bad"):
        opp_wrc = _f(g.get("away_wrc_plus"))
        if opp_wrc and opp_wrc >= 105: out["home_drift+opp_hot"] = True
    if out.get("away_sp_form_drift_bad"):
        opp_wrc = _f(g.get("home_wrc_plus"))
        if opp_wrc and opp_wrc >= 105: out["away_drift+opp_hot"] = True

    # ─── 3-WAY INTERACTIONS (the new dig) ───

    # ── TOTALS-focused 3-ways ──
    # v3 loud totals + form drift + park / temp / ump
    for prefix in ("v3_tot_loud", "v3_tot_over_lean", "v3_tot_under_lean"):
        if not out.get(prefix): continue
        for second in ("home_sp_form_drift_bad", "away_sp_form_drift_bad",
                       "home_sp_blowup_history", "away_sp_blowup_history"):
            if out.get(second):
                key = f"{prefix}+{second}"
                out[key] = True
                # Layer 3: park
                if out.get("park_extreme_hitter"): out[f"{key}+coors"] = True
                elif out.get("park_hitter_friendly"): out[f"{key}+park_hitter"] = True
                elif out.get("park_pitcher_friendly"): out[f"{key}+park_pitcher"] = True
                # Layer 3: temp
                if out.get("temp_hot"): out[f"{key}+temp_hot"] = True
                elif out.get("temp_cool"): out[f"{key}+temp_cool"] = True
                # Layer 3: umpire
                if out.get("ump_over"): out[f"{key}+ump_over"] = True
                elif out.get("ump_under"): out[f"{key}+ump_under"] = True

    # v3 lean over + hitter setup compound
    if out.get("v3_tot_over_lean"):
        if out.get("park_hitter_friendly") and out.get("temp_hot"):
            out["v3_over+park_hitter+hot"] = True
        if out.get("park_hitter_friendly") and out.get("bullpen_taxed_either"):
            out["v3_over+park_hitter+pen_taxed"] = True
        if out.get("ump_over") and out.get("park_hitter_friendly"):
            out["v3_over+ump_over+park_hitter"] = True

    # v3 lean under + pitcher setup compound
    if out.get("v3_tot_under_lean"):
        if out.get("park_pitcher_friendly") and out.get("temp_cool"):
            out["v3_under+park_pitcher+cool"] = True
        if out.get("ump_under") and out.get("park_pitcher_friendly"):
            out["v3_under+ump_under+park_pitcher"] = True
        if out.get("home_sp_elite") and out.get("away_sp_elite"):
            out["v3_under+both_aces"] = True

    # Triple-loud agreement: v3 + v4 same direction on totals + both loud
    v3_t = _f(g.get("projected_total")); v4_t = _f(g.get("model_pred_total"))
    if line and v3_t and v4_t:
        d3 = v3_t - line; d4 = v4_t - line
        same_dir = (d3 > 0 and d4 > 0) or (d3 < 0 and d4 < 0)
        if same_dir and abs(d3) >= 1.0 and abs(d4) >= 1.0:
            out["v3_v4_tot_consensus"] = True
            if abs(d3) >= 2.0 and abs(d4) >= 2.0:
                out["v3_v4_tot_loud_consensus"] = True

    # ── SIDES-focused 3-ways ──
    # conf4+fav + v3+v4 agree + spread loud → "everyone screaming fav"
    if out.get("conf4+fav") and out.get("v3_v4_agree"):
        out["conf4+fav+v3v4_agree"] = True
        if out.get("v3_spread_loud"): out["conf4+fav+v3v4_agree+v3_loud"] = True
        if out.get("home_sp_form_drift_bad") or out.get("away_sp_form_drift_bad"):
            out["conf4+fav+v3v4_agree+drift"] = True

    # conf4+fav + mastery on FAV side
    # (we don't know which side is fav without conf direction)
    if cs_dir == "home" and out.get("conf4+fav") and out.get("home_sp_mastery"):
        out["conf4+fav+mastery_on_fav"] = True
    if cs_dir == "away" and out.get("conf4+fav") and out.get("away_sp_mastery"):
        out["conf4+fav+mastery_on_fav"] = True

    # conf4+fav + drift on UNDERDOG side (the side fav SHOULD beat)
    if cs_dir == "home" and out.get("conf4+fav") and out.get("away_sp_form_drift_bad"):
        out["conf4+fav+drift_on_dog"] = True
    if cs_dir == "away" and out.get("conf4+fav") and out.get("home_sp_form_drift_bad"):
        out["conf4+fav+drift_on_dog"] = True

    # v3+v4 agree + loud spread + park hitter
    if out.get("v3_v4_agree") and out.get("v3_spread_loud"):
        out["v3v4_agree+v3_loud"] = True
        if out.get("park_hitter_friendly"): out["v3v4_agree+v3_loud+park_hitter"] = True

    # v4 RL + v4 picks dog + form drift on fav
    # (v4_dir is the side v4 picks; home_is_dog tells us if home is the dog)
    if v4d and hd is not None:
        v4_picks_dog = (v4d == "home" and hd) or (v4d == "away" and not hd)
        if v4_picks_dog:
            out["v4_picks_dog"] = True
            # Layer 2: form drift on the OTHER (fav) side
            fav_side = "away" if hd else "home"
            if out.get(f"{fav_side}_sp_form_drift_bad"):
                out["v4_picks_dog+drift_on_fav"] = True
            if out.get(f"{fav_side}_sp_blowup_history"):
                out["v4_picks_dog+blowup_on_fav"] = True

    # Mastery × drift on opposing sides (the rare "perfect alignment")
    if out.get("home_sp_mastery") and out.get("away_sp_form_drift_bad"):
        out["home_mastery+away_drift"] = True
    if out.get("away_sp_mastery") and out.get("home_sp_form_drift_bad"):
        out["away_mastery+home_drift"] = True

    return out


# ---------------------------------------------------------------- plays

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


# ---------------------------------------------------------------- run

def run(min_n=15, deviation=8.0):
    print("[attribution v3] fetching graded rows...")
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

    # Per-direction baselines
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

    # Rank
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
        # Highlight 3-way cohorts specifically
        is_3way = "+" in ck and ck.count("+") >= 2
        score = abs(delta) * math.log(n)
        findings.append({
            "play": play,
            "cohort": ck,
            "direction": dirn,
            "win_pct": round(pct, 1),
            "baseline_pct": base,
            "delta_pp": round(delta, 1),
            "n": n,
            "wins": t["w"],
            "losses": t["l"],
            "is_3way": is_3way,
            "score": round(score, 2),
        })
    findings.sort(key=lambda x: -x["score"])

    # Output: 3-way cohorts highlighted
    threeway = [f for f in findings if f["is_3way"]]
    twoway = [f for f in findings if not f["is_3way"]]

    print()
    print("=" * 130)
    print(f"TOP 3-WAY COHORTS (min_n={min_n}, |delta|>={deviation}pp)")
    print("=" * 130)
    print(f"  {'PLAY':<14} {'DIR':<5} {'COHORT':<48} {'WIN%':>6} {'BASE%':>6} {'DELTA':>7} {'n':>4}  {'W-L':>8}")
    print("-" * 130)
    for f in threeway[:40]:
        d = f"+{f['delta_pp']}" if f['delta_pp'] >= 0 else f"{f['delta_pp']}"
        print(f"  {f['play']:<14} {f['direction']:<5} {f['cohort']:<48} {f['win_pct']:>5}% {f['baseline_pct']:>5}% {d:>6}pp {f['n']:>4}  {f['wins']:>3}-{f['losses']:<3}")

    print()
    print("=" * 130)
    print(f"TOP 2-WAY COHORTS — for context (showing top 20)")
    print("=" * 130)
    print(f"  {'PLAY':<14} {'DIR':<5} {'COHORT':<48} {'WIN%':>6} {'BASE%':>6} {'DELTA':>7} {'n':>4}  {'W-L':>8}")
    print("-" * 130)
    for f in twoway[:20]:
        d = f"+{f['delta_pp']}" if f['delta_pp'] >= 0 else f"{f['delta_pp']}"
        print(f"  {f['play']:<14} {f['direction']:<5} {f['cohort']:<48} {f['win_pct']:>5}% {f['baseline_pct']:>5}% {d:>6}pp {f['n']:>4}  {f['wins']:>3}-{f['losses']:<3}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "rows_analyzed": len(rows),
        "min_n": min_n,
        "deviation_threshold_pp": deviation,
        "baselines": baselines,
        "cohorts": findings,
    }, indent=2))
    print()
    print(f"[attribution v3] wrote {OUT_PATH} ({len(findings)} total, {len(threeway)} 3-way)")


if __name__ == "__main__":
    min_n = 15; deviation = 8.0
    if "--min-n" in sys.argv:
        try: min_n = int(sys.argv[sys.argv.index("--min-n") + 1])
        except (IndexError, ValueError): pass
    if "--deviation" in sys.argv:
        try: deviation = float(sys.argv[sys.argv.index("--deviation") + 1])
        except (IndexError, ValueError): pass
    run(min_n=min_n, deviation=deviation)
