"""
Model attribution backtest v2 — deeper dig.

What v1 missed:
  - Direction-conditional cohorts. v1 collapsed home/away picks together.
    "conf=5 ML at 73%" hides whether the edge is on HOME picks, AWAY
    picks, or both. v2 splits every play by call direction.
  - DOG vs FAV cohorts. Bettors price RL by who's the dog — and our
    cohort_stats showed conf=4 DOG RL at 68.8% but didn't test conf=4
    FAV RL separately. v2 does.
  - 2D feature interactions. v1 had model-agreement and 1D features
    only. v2 adds pairs like (form_drift + park), (mastery + conf),
    (form_drift + opposing-side conf direction).
  - More play types: NRFI / YRFI splits, total-over-specific vs
    total-under-specific (asymmetry happens — v4 has documented OVER
    drift per project_v4_over_drift memory).
  - More features: bullpen taxed/rested, k_gap loud, days rest,
    pitcher-hand splits, umpire over-tendency, lineup confirmed.

USAGE:
  python backtest_model_attribution_v2.py
  python backtest_model_attribution_v2.py --min-n 20 --deviation 5.0

OUTPUT: models/attribution_cohorts_v2.json
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

OUT_PATH = Path(__file__).parent / "models" / "attribution_cohorts_v2.json"


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v)
    except (TypeError, ValueError): return None


# ---------------------------------------------------------------- fetch

def fetch_rows(days=None):
    rows = []
    page = 0
    while True:
        params = {
            "home_score": "not.is.null",
            "signal_confluence_net": "not.is.null",
            "select": "*",
            "order": "game_date.desc",
            "offset": str(page * 1000),
            "limit": "1000",
        }
        if days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            params["game_date"] = f"gte.{cutoff}"
        r = requests.get(f"{SUPABASE_URL}/rest/v1/mlb_game_results",
                         params=params, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch: break
        rows.extend(batch)
        if len(batch) < 1000: break
        page += 1
    return rows


# ---------------------------------------------------------------- play calls

def ml_call(spread):
    if spread is None or abs(spread) < 0.3: return None
    return "home" if spread > 0 else "away"


def rl_call(spread):
    """Dog +1.5 default — pick the side that gets points."""
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


def actual_nrfi(g):
    """nrfi_result column stores 'NRFI' or 'YRFI'."""
    r = g.get("nrfi_result")
    if r == "NRFI": return "nrfi"
    if r == "YRFI": return "yrfi"
    return None


def home_is_dog(g):
    """Per sign convention: POSITIVE close_spread = home is +1.5 dog."""
    cs = _f(g.get("close_spread")) or _f(g.get("open_spread"))
    if cs is None: return None
    return cs > 0


# ---------------------------------------------------------------- cohorts

def cohort_memberships(g):
    """Each entry: cohort_key -> True (member). Caller checks key existence,
    not truthiness, so each row contributes only to cohorts it qualifies for."""
    out = {}

    # ── Confluence magnitude bands (fine-grained) ──
    cn = _i(g.get("signal_confluence_net"))
    if cn is not None:
        mag = abs(cn)
        out[f"conf_mag={mag}"] = True
        out[f"conf_mag>={min(mag,6)}"] = True
        if mag >= 4: out["conf_mag>=4"] = True
        if mag >= 5: out["conf_mag>=5"] = True
        if 2 <= mag <= 3: out["conf_mag_mid"] = True

    # ── Confluence × dog/fav direction ──
    cs = conf_side(cn) if cn is not None else None
    hd = home_is_dog(g)
    if cs and hd is not None:
        if (cs == "home" and hd) or (cs == "away" and not hd):
            out["conf_points_to_dog"] = True
        else:
            out["conf_points_to_fav"] = True

    # ── Model agreement cohorts (ML direction) ──
    v3s = _f(g.get("projected_spread"))
    v4s = _f(g.get("model_pred_spread"))
    js  = _f(g.get("jerry_pred_spread"))
    v3d, v4d, jd = ml_call(v3s), ml_call(v4s), ml_call(js)
    if v3d and v4d:
        out["v3_v4_agree" if v3d == v4d else "v3_v4_disagree"] = True
    if v3d and jd:
        out["v3_jerry_agree" if v3d == jd else "v3_jerry_disagree"] = True
    if v4d and jd:
        out["v4_jerry_agree" if v4d == jd else "v4_jerry_disagree"] = True
    if v3d and v4d and jd:
        out["all_models_agree"] = (v3d == v4d == jd)
        out["two_of_three_agree"] = sum([v3d==v4d, v3d==jd, v4d==jd]) >= 2

    # ── Spread magnitude bands ──
    for name, spread in (("v3", v3s), ("v4", v4s), ("jerry", js)):
        if spread is None: continue
        mag = abs(spread)
        if mag >= 3.0: out[f"{name}_spread_extreme"] = True  # ≥3 runs is a "loud" call
        elif mag >= 2.0: out[f"{name}_spread_loud"] = True
        elif mag >= 1.0: out[f"{name}_spread_mid"] = True
        else: out[f"{name}_spread_quiet"] = True

    # ── Spread direction (model sees home/away as favorite) ──
    for name, spread in (("v3", v3s), ("v4", v4s)):
        d = ml_call(spread)
        if d:
            out[f"{name}_picks_{d}"] = True
            # Combined with whether home is dog
            if hd is not None:
                pick_is_dog = (d == "home" and hd) or (d == "away" and not hd)
                out[f"{name}_picks_dog" if pick_is_dog else f"{name}_picks_fav"] = True

    # ── Total magnitude vs line ──
    line = _f(g.get("close_total")) or _f(g.get("open_total"))
    for name, mt in (("v3", _f(g.get("projected_total"))),
                     ("v4", _f(g.get("model_pred_total"))),
                     ("jerry", _f(g.get("jerry_pred_total")))):
        if mt is None or line is None: continue
        delta = mt - line
        if abs(delta) >= 2.0: out[f"{name}_tot_loud"] = True
        elif abs(delta) >= 1.0: out[f"{name}_tot_mid"] = True
        if delta >= 1.0: out[f"{name}_tot_over_lean"] = True
        elif delta <= -1.0: out[f"{name}_tot_under_lean"] = True

    # ── Park factor ──
    park = _f(g.get("park_run_factor"))
    if park is not None:
        if park >= 108: out["park_extreme_hitter"] = True  # Coors-class
        elif park >= 104: out["park_hitter_friendly"] = True
        elif park <= 92: out["park_extreme_pitcher"] = True
        elif park <= 96: out["park_pitcher_friendly"] = True
        else: out["park_neutral"] = True

    # ── Temperature ──
    temp = _f(g.get("temperature"))
    if temp is not None:
        if temp <= 45: out["temp_freezing"] = True
        elif temp <= 60: out["temp_cool"] = True
        elif temp >= 85: out["temp_hot"] = True

    # ── Pitcher mastery vs team (≥15 IP) ──
    for side in ("home", "away"):
        vt_era = _f(g.get(f"{side}_pitcher_vs_team_era"))
        vt_ip = _f(g.get(f"{side}_pitcher_vs_team_ip"))
        if vt_era is not None and vt_ip and vt_ip >= 15:
            if vt_era <= 2.5: out[f"{side}_sp_mastery"] = True
            elif vt_era >= 5.5: out[f"{side}_sp_blowup_history"] = True

    # ── Pitcher form drift (L3 - xERA) ──
    for side in ("home", "away"):
        l3 = _f(g.get(f"{side}_pitcher_last_3_era"))
        xera = _f(g.get(f"{side}_sp_xera"))
        if l3 is not None and xera is not None:
            drift = l3 - xera
            if drift >= 2.0: out[f"{side}_sp_form_drift_bad"] = True
            elif drift <= -1.5: out[f"{side}_sp_form_hot"] = True

    # ── Pitcher xERA tier ──
    for side in ("home", "away"):
        xera = _f(g.get(f"{side}_sp_xera"))
        if xera is None: continue
        if xera <= 3.0: out[f"{side}_sp_elite"] = True
        elif xera <= 4.0: out[f"{side}_sp_solid"] = True
        elif xera >= 5.0: out[f"{side}_sp_shaky"] = True

    # ── K-gap loud (≥3 runs of K advantage) ──
    for side in ("home", "away"):
        kg = _f(g.get(f"{side}_k_gap"))
        if kg is None: continue
        if kg >= 3.0: out[f"{side}_k_gap_loud_plus"] = True
        elif kg <= -3.0: out[f"{side}_k_gap_loud_minus"] = True

    # ── Bullpen taxed (3-day reliever usage) ──
    for side in ("home", "away"):
        bp = _i(g.get(f"{side}_bp_relievers_3d"))
        if bp is None: continue
        if bp >= 8: out[f"{side}_bp_taxed"] = True
        elif bp <= 3: out[f"{side}_bp_rested"] = True

    # ── 1st-inning shaky ──
    for side in ("home", "away"):
        fi = _f(g.get(f"{side}_first_inning_era"))
        if fi is None: continue
        if fi >= 6.0: out[f"{side}_sp_1st_inn_shaky"] = True
        elif fi <= 1.5: out[f"{side}_sp_1st_inn_clean"] = True

    # ── Days rest ──
    for side in ("home", "away"):
        dr = _i(g.get(f"{side}_sp_days_rest"))
        if dr is None: continue
        if dr == 4: out[f"{side}_sp_short_rest"] = True
        elif dr >= 7: out[f"{side}_sp_long_rest"] = True

    # ── wRC+ differential ──
    hw = _f(g.get("home_wrc_plus")); aw = _f(g.get("away_wrc_plus"))
    if hw is not None and aw is not None:
        diff = hw - aw
        if abs(diff) >= 20: out["wrc_gap_huge"] = True
        elif abs(diff) >= 10: out["wrc_gap_modest"] = True

    # ── NRFI score band ──
    nrfi = _f(g.get("nrfi_score"))
    if nrfi is not None:
        if nrfi >= 90: out["nrfi_high"] = True
        elif nrfi >= 70: out["nrfi_lean"] = True
        elif nrfi <= 25: out["yrfi_lean"] = True

    # ── Umpire over/under bias (encoded in umpire_note) ──
    un = (g.get("umpire_note") or "").lower()
    if "over-friendly" in un or "hitter-friendly" in un: out["ump_over"] = True
    elif "under-friendly" in un or "pitcher-friendly" in un: out["ump_under"] = True

    # ── Lineup confirmed at log time (when available) ──
    if g.get("lineup_confirmed") is True: out["lineup_confirmed"] = True
    elif g.get("lineup_confirmed") is False: out["lineup_unconfirmed"] = True

    # ─────────────────────────────────────────────────────────────────
    # 2D INTERACTION COHORTS (carefully chosen — not combinatorial)
    # ─────────────────────────────────────────────────────────────────

    # Form drift + opp lineup quality (does drift matter more vs strong bats?)
    for side in ("home", "away"):
        opp = "away" if side == "home" else "home"
        if out.get(f"{side}_sp_form_drift_bad"):
            opp_wrc = _f(g.get(f"{opp}_wrc_plus"))
            if opp_wrc is not None:
                if opp_wrc >= 105: out[f"{side}_drift+opp_hot"] = True
                elif opp_wrc <= 95: out[f"{side}_drift+opp_cold"] = True

    # Form drift + park
    for side in ("home", "away"):
        if out.get(f"{side}_sp_form_drift_bad") and out.get("park_hitter_friendly"):
            out[f"{side}_drift+park_hot"] = True
        if out.get(f"{side}_sp_form_drift_bad") and out.get("park_extreme_hitter"):
            out[f"{side}_drift+coors"] = True

    # Mastery + form drift on the OTHER starter (the rare alignment play)
    if out.get("home_sp_mastery") and out.get("away_sp_form_drift_bad"):
        out["home_mastery+away_drift"] = True
    if out.get("away_sp_mastery") and out.get("home_sp_form_drift_bad"):
        out["away_mastery+home_drift"] = True

    # Confluence mag=4 + dog (the documented 82.6%→68.8% PEAK cohort)
    if out.get("conf_mag=4") and out.get("conf_points_to_dog"):
        out["conf4+dog"] = True
    if out.get("conf_mag=4") and out.get("conf_points_to_fav"):
        out["conf4+fav"] = True
    if out.get("conf_mag=5") and out.get("conf_points_to_dog"):
        out["conf5+dog"] = True
    if out.get("conf_mag=5") and out.get("conf_points_to_fav"):
        out["conf5+fav"] = True
    if out.get("conf_mag>=6") and out.get("conf_points_to_dog"):
        out["conf6+dog"] = True
    if out.get("conf_mag>=6") and out.get("conf_points_to_fav"):
        out["conf6+fav"] = True

    # Loud v3 spread + confluence agree → strong consensus
    if (out.get("v3_spread_loud") or out.get("v3_spread_extreme")) and out.get("v3_v4_agree"):
        out["v3_loud+v4_agree"] = True

    # Bullpen taxed + over lean
    if out.get("home_bp_taxed") or out.get("away_bp_taxed"):
        out["bullpen_taxed_either"] = True
    if out.get("home_bp_taxed") and out.get("away_bp_taxed"):
        out["both_bullpens_taxed"] = True

    # Hot day + hitter park (over fuel)
    if out.get("temp_hot") and out.get("park_hitter_friendly"):
        out["hot+hitter_park"] = True

    # Cold day + pitcher park (under fuel)
    if out.get("temp_cool") and out.get("park_pitcher_friendly"):
        out["cool+pitcher_park"] = True

    # Two shaky 1st-inning starters (YRFI setup)
    if out.get("home_sp_1st_inn_shaky") and out.get("away_sp_1st_inn_shaky"):
        out["both_sp_1st_inn_shaky"] = True

    # K-gap aligned with spread (does pitching advantage forecast spread cover?)
    if v3d == "home" and out.get("home_k_gap_loud_plus"):
        out["v3_home_pick+home_k_advantage"] = True
    if v3d == "away" and out.get("away_k_gap_loud_plus"):
        out["v3_away_pick+away_k_advantage"] = True

    return out


# ---------------------------------------------------------------- play types

PLAY_TYPES = [
    # MLs
    ("v3_ml", lambda g: ml_call(_f(g.get("projected_spread")))),
    ("v4_ml", lambda g: ml_call(_f(g.get("model_pred_spread")))),
    ("jerry_ml", lambda g: ml_call(_f(g.get("jerry_pred_spread")))),
    ("conf_ml", lambda g: conf_side(g.get("signal_confluence_net"))),

    # RLs
    ("v3_rl", lambda g: rl_call(_f(g.get("projected_spread")))),
    ("v4_rl", lambda g: rl_call(_f(g.get("model_pred_spread")))),
    ("jerry_rl", lambda g: rl_call(_f(g.get("jerry_pred_spread")))),
    ("conf_rl", lambda g: conf_side(g.get("signal_confluence_net"))),

    # Totals (combined over/under)
    ("v3_tot", lambda g: total_call(_f(g.get("projected_total")),
                                    _f(g.get("close_total")) or _f(g.get("open_total")))),
    ("v4_tot", lambda g: total_call(_f(g.get("model_pred_total")),
                                    _f(g.get("close_total")) or _f(g.get("open_total")))),
    ("jerry_tot", lambda g: total_call(_f(g.get("jerry_pred_total")),
                                       _f(g.get("close_total")) or _f(g.get("open_total")))),

    # NRFI / YRFI (using nrfi_score from game_context)
    ("nrfi_pick_high", lambda g: "nrfi" if (_f(g.get("nrfi_score")) or 0) >= 70 else None),
    ("yrfi_pick_low", lambda g: "yrfi" if (_f(g.get("nrfi_score")) or 100) <= 25 else None),
]


def actual_for_play(play, g):
    if play.endswith("_ml"): return actual_ml(g)
    if play.endswith("_rl"): return actual_rl(g)
    if play.endswith("_tot"): return actual_total(g)
    if play.startswith("nrfi_") or play.startswith("yrfi_"): return actual_nrfi(g)
    return None


def score_play(call, actual):
    if call is None or actual is None: return None
    if actual == "push": return "P"
    return "W" if call == actual else "L"


# ---------------------------------------------------------------- run

def run(days=None, min_n=15, deviation=4.0):
    print(f"[attribution v2] fetching graded rows{' (last ' + str(days) + ' days)' if days else ''}...")
    rows = fetch_rows(days=days)
    print(f"  {len(rows)} rows pulled")

    # Direction-conditional baselines: per (play, call_direction)
    # AND per (play, all calls combined)
    baselines = {}
    splits = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})  # (play, direction)
    overall = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})  # play

    for g in rows:
        for play, predict in PLAY_TYPES:
            call = predict(g)
            act = actual_for_play(play, g)
            res = score_play(call, act)
            if res is None: continue
            t = overall[play]
            if res == "W": t["w"] += 1
            elif res == "L": t["l"] += 1
            elif res == "P": t["p"] += 1
            if call:
                t2 = splits[(play, call)]
                if res == "W": t2["w"] += 1
                elif res == "L": t2["l"] += 1
                elif res == "P": t2["p"] += 1

    for play, t in overall.items():
        n = t["w"] + t["l"]
        baselines[play] = {**t, "n": n, "pct": round(100*t["w"]/n, 1) if n else None}

    direction_baselines = {}
    for (play, dirn), t in splits.items():
        n = t["w"] + t["l"]
        direction_baselines[(play, dirn)] = {**t, "n": n, "pct": round(100*t["w"]/n, 1) if n else None}

    # Cohort tallies — per (play, cohort, call_direction)
    by_cohort = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
    for g in rows:
        cohorts = cohort_memberships(g)
        for play, predict in PLAY_TYPES:
            call = predict(g)
            act = actual_for_play(play, g)
            res = score_play(call, act)
            if res is None: continue
            for ck in cohorts:
                # Tally for combined direction
                t = by_cohort[(play, ck, "any")]
                if res == "W": t["w"] += 1
                elif res == "L": t["l"] += 1
                elif res == "P": t["p"] += 1
                # Tally for specific call direction
                if call:
                    t2 = by_cohort[(play, ck, call)]
                    if res == "W": t2["w"] += 1
                    elif res == "L": t2["l"] += 1
                    elif res == "P": t2["p"] += 1

    # Rank findings
    findings = []
    for (play, ck, dirn), t in by_cohort.items():
        n = t["w"] + t["l"]
        if n < min_n: continue
        pct = 100 * t["w"] / n
        if dirn == "any":
            base = baselines[play]["pct"] or 0
        else:
            db = direction_baselines.get((play, dirn))
            base = (db or {}).get("pct") or baselines[play]["pct"] or 0
        delta = pct - base
        if abs(delta) < deviation: continue
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
            "pushes": t["p"],
            "score": round(score, 2),
        })
    findings.sort(key=lambda x: -x["score"])

    # ── print baselines ──
    print()
    print("=" * 110)
    print("OVERALL BASELINES")
    print("=" * 110)
    for play, b in baselines.items():
        print(f"  {play:<18} {b['w']}-{b['l']}-{b['p']}P (n={b['n']}, {b['pct']}%)")

    print()
    print("=" * 110)
    print("PER-DIRECTION BASELINES")
    print("=" * 110)
    seen = set()
    for (play, dirn), b in sorted(direction_baselines.items()):
        if b["n"] < 30: continue
        seen.add(play)
        print(f"  {play:<18} {dirn:<8} {b['w']}-{b['l']}-{b['p']}P (n={b['n']}, {b['pct']}%)")

    print()
    print("=" * 130)
    print(f"TOP COHORTS (min_n={min_n}, |delta|>= {deviation}pp)")
    print("=" * 130)
    print(f"  {'PLAY':<14} {'DIR':<5} {'COHORT':<34} {'WIN%':>6} {'BASE%':>6} {'DELTA':>7} {'n':>5}  {'W-L':>9}")
    print("-" * 130)
    for f in findings[:80]:
        d = f"+{f['delta_pp']}" if f['delta_pp'] >= 0 else f"{f['delta_pp']}"
        print(f"  {f['play']:<14} {f['direction']:<5} {f['cohort']:<34} {f['win_pct']:>5}% {f['baseline_pct']:>5}% {d:>6}pp {f['n']:>5}  {f['wins']:>3}-{f['losses']:<3}")

    # ── write JSON ──
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "rows_analyzed": len(rows),
        "days_window": days,
        "min_n": min_n,
        "deviation_threshold_pp": deviation,
        "baselines": baselines,
        "direction_baselines": {f"{p}|{d}": v for (p, d), v in direction_baselines.items()},
        "cohorts": findings,
    }, indent=2))
    print()
    print(f"[attribution v2] wrote {OUT_PATH} ({len(findings)} cohorts)")


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
