"""
Model attribution backtest v6 — middle-zone / inverse analysis.

Inverts the usual hunt. Instead of "what features make this play reliable",
asks: "given that this play is near 50% baseline, what features tell us
which side of the coin flip will land?"

THE QUESTION
  Most attribution work finds where models EXCEL or FAIL. The middle is
  noise. But maybe the middle isn't noise — maybe it's a coin flip until
  some 2nd-order feature disambiguates it. v6 looks for those.

MIDDLE-ZONE DEFINITIONS (where each model is near 50%):
  - v3_ml / v4_ml / jerry_ml: |spread| < 1.0
  - v3_rl / v4_rl / jerry_rl: |spread| < 1.5 (the "soft favorite" zone)
  - v3_tot / v4_tot / jerry_tot: |projection - line| < 1.0 (dead zone)
  - conf_ml / conf_rl: |confluence_net| <= 3 (no consensus)

MODEL-DISAGREEMENT RESOLUTION:
  Separate analysis. Given v3 vs v4 disagree (or v3 vs Jerry, v4 vs Jerry),
  what features predict which one is right?

OUTPUT: models/attribution_cohorts_v6.json
USAGE: python backtest_model_attribution_v6_middle_zone.py
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
OUT_PATH = Path(__file__).parent / "models" / "attribution_cohorts_v6.json"


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


# Play helpers
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

def home_is_dog(g):
    cs = _f(g.get("close_spread")) or _f(g.get("open_spread"))
    if cs is None: return None
    return cs > 0


# ── Middle zone filters ──
def in_ml_middle(g, model_field):
    s = _f(g.get(model_field))
    return s is not None and abs(s) < 1.0

def in_rl_middle(g, model_field):
    s = _f(g.get(model_field))
    return s is not None and abs(s) < 1.5

def in_tot_middle(g, model_field):
    line = _f(g.get("close_total")) or _f(g.get("open_total"))
    mt = _f(g.get(model_field))
    if line is None or mt is None: return False
    return abs(mt - line) < 1.0

def in_conf_middle(g):
    cn = _i(g.get("signal_confluence_net"))
    return cn is not None and abs(cn) <= 3


# ── Cohorts to test against middle zones ──
def cohort_memberships(g):
    out = {}
    # Pitching
    h_xera = _f(g.get("home_sp_xera")); a_xera = _f(g.get("away_sp_xera"))
    if h_xera and a_xera:
        gap = abs(h_xera - a_xera)
        if gap >= 1.5: out["xera_gap_loud"] = True
        elif gap <= 0.3: out["xera_gap_tight"] = True
    for side, x in (("home", h_xera), ("away", a_xera)):
        if x is None: continue
        if x <= 3.0: out[f"{side}_xera_elite"] = True
        elif x >= 4.75: out[f"{side}_xera_shaky"] = True

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

        fi = _f(g.get(f"{side}_first_inning_era"))
        if fi is not None:
            if fi >= 6.0: out[f"{side}_sp_1st_inn_shaky"] = True
            elif fi <= 1.5: out[f"{side}_sp_1st_inn_clean"] = True

        kg = _f(g.get(f"{side}_k_gap"))
        if kg is not None:
            if kg >= 3.0: out[f"{side}_k_gap_loud_plus"] = True
            elif kg <= -3.0: out[f"{side}_k_gap_loud_minus"] = True

        bp = _i(g.get(f"{side}_bp_relievers_3d"))
        if bp is not None:
            if bp >= 8: out[f"{side}_bp_taxed"] = True
            elif bp <= 3: out[f"{side}_bp_rested"] = True

    # Offense
    h_wrc = _f(g.get("home_wrc_plus")); a_wrc = _f(g.get("away_wrc_plus"))
    if h_wrc is not None and a_wrc is not None:
        diff = h_wrc - a_wrc
        if abs(diff) >= 15: out["wrc_gap_loud"] = True
        if diff >= 15: out["wrc_loud_home_better"] = True
        elif diff <= -15: out["wrc_loud_away_better"] = True

    # L10 R/G drift
    h_l10 = _f(g.get("home_last10_runs_per_game"))
    a_l10 = _f(g.get("away_last10_runs_per_game"))
    h_rpg = _f(g.get("home_runs_per_game"))
    a_rpg = _f(g.get("away_runs_per_game"))
    for side, l10, rpg in (("home", h_l10, h_rpg), ("away", a_l10, a_rpg)):
        if l10 is None or rpg is None: continue
        drift = l10 - rpg
        if drift >= 1.5: out[f"{side}_offense_hot"] = True
        elif drift <= -1.5: out[f"{side}_offense_cold"] = True

    # Park / temp / ump
    park = _f(g.get("park_run_factor"))
    if park is not None:
        if park >= 108: out["park_extreme_hitter"] = True
        elif park >= 104: out["park_hitter_friendly"] = True
        elif park <= 92: out["park_extreme_pitcher"] = True
        elif park <= 96: out["park_pitcher_friendly"] = True

    temp = _f(g.get("temperature"))
    if temp is not None:
        if temp <= 50: out["temp_cold"] = True
        elif temp >= 80: out["temp_hot"] = True

    un = (g.get("umpire_note") or "").lower()
    if "over-friendly" in un or "hitter-friendly" in un: out["ump_over"] = True
    elif "under-friendly" in un or "pitcher-friendly" in un: out["ump_under"] = True

    # NRFI
    nrfi = _f(g.get("nrfi_score"))
    if nrfi is not None:
        if nrfi >= 90: out["nrfi_high"] = True
        elif nrfi <= 25: out["yrfi_lean"] = True

    # Mismatches
    if out.get("home_xera_elite") and out.get("away_xera_shaky"):
        out["mismatch_home_elite"] = True
    if out.get("away_xera_elite") and out.get("home_xera_shaky"):
        out["mismatch_away_elite"] = True

    if out.get("home_sp_form_drift_bad") and out.get("away_offense_hot"):
        out["home_drift+away_hot"] = True
    if out.get("away_sp_form_drift_bad") and out.get("home_offense_hot"):
        out["away_drift+home_hot"] = True

    return out


PLAY_TYPES = [
    ("v3_ml",    "projected_spread",   ml_call,    actual_ml, "ml"),
    ("v4_ml",    "model_pred_spread",  ml_call,    actual_ml, "ml"),
    ("jerry_ml", "jerry_pred_spread",  ml_call,    actual_ml, "ml"),
    ("v3_rl",    "projected_spread",   rl_call,    actual_rl, "rl"),
    ("v4_rl",    "model_pred_spread",  rl_call,    actual_rl, "rl"),
    ("jerry_rl", "jerry_pred_spread",  rl_call,    actual_rl, "rl"),
    ("v3_tot",   "projected_total",    None,       actual_total, "tot"),
    ("v4_tot",   "model_pred_total",   None,       actual_total, "tot"),
    ("jerry_tot","jerry_pred_total",   None,       actual_total, "tot"),
    ("conf_ml",  None,                 None,       actual_ml, "conf_ml"),
    ("conf_rl",  None,                 None,       actual_rl, "conf_rl"),
]


def get_call(play_label, model_field, fn, g):
    if play_label.startswith("conf"):
        return conf_side(g.get("signal_confluence_net"))
    if model_field is None: return None
    if play_label.endswith("_tot"):
        return total_call(_f(g.get(model_field)),
                          _f(g.get("close_total")) or _f(g.get("open_total")))
    return fn(_f(g.get(model_field)))


def in_middle_zone(play_label, model_field, g):
    if play_label.startswith("conf"): return in_conf_middle(g)
    if play_label.endswith("_ml"): return in_ml_middle(g, model_field)
    if play_label.endswith("_rl"): return in_rl_middle(g, model_field)
    if play_label.endswith("_tot"): return in_tot_middle(g, model_field)
    return False


def score(call, actual):
    if call is None or actual is None: return None
    if actual == "push": return "P"
    return "W" if call == actual else "L"


# ── Disagreement resolution: when X and Y disagree, who wins? ──
def disagreement_pairs():
    return [
        ("v3_vs_v4_disagree_ml", "projected_spread", "model_pred_spread", ml_call, actual_ml),
        ("v3_vs_jerry_disagree_ml", "projected_spread", "jerry_pred_spread", ml_call, actual_ml),
        ("v4_vs_jerry_disagree_ml", "model_pred_spread", "jerry_pred_spread", ml_call, actual_ml),
        ("v3_vs_v4_disagree_rl", "projected_spread", "model_pred_spread", rl_call, actual_rl),
        ("v4_vs_jerry_disagree_rl", "model_pred_spread", "jerry_pred_spread", rl_call, actual_rl),
    ]


def run(min_n=10, deviation=10.0):
    print("[v6 / middle-zone] fetching graded rows...")
    rows = fetch_rows()
    print(f"  {len(rows)} rows pulled")

    # MIDDLE-ZONE ANALYSIS: for each play, restrict to games in that play's
    # middle zone, then test all cohorts for win-rate lift.
    middle_findings = []
    for play_label, model_field, call_fn, actual_fn, _kind in PLAY_TYPES:
        # Filter middle-zone rows
        middle_rows = [g for g in rows if in_middle_zone(play_label, model_field, g)]
        # Baseline within middle
        base = {"w": 0, "l": 0, "p": 0}
        for g in middle_rows:
            call = get_call(play_label, model_field, call_fn, g)
            act = actual_fn(g)
            res = score(call, act)
            if res is None: continue
            if res == "W": base["w"] += 1
            elif res == "L": base["l"] += 1
            elif res == "P": base["p"] += 1
        n_base = base["w"] + base["l"]
        if n_base < 30: continue  # need enough middle-zone games to test
        base_pct = round(100 * base["w"] / n_base, 1)

        # Cohort sweep within middle zone
        by_cohort = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
        for g in middle_rows:
            call = get_call(play_label, model_field, call_fn, g)
            res = score(call, actual_fn(g))
            if res is None: continue
            for ck in cohort_memberships(g):
                t = by_cohort[(ck, "any")]
                t["w"] += (res == "W"); t["l"] += (res == "L"); t["p"] += (res == "P")
                if call:
                    t2 = by_cohort[(ck, call)]
                    t2["w"] += (res == "W"); t2["l"] += (res == "L"); t2["p"] += (res == "P")

        # Rank within this middle
        for (ck, dirn), t in by_cohort.items():
            n = t["w"] + t["l"]
            if n < min_n: continue
            pct = round(100 * t["w"] / n, 1)
            delta = round(pct - base_pct, 1)
            if abs(delta) < deviation: continue
            middle_findings.append({
                "play": play_label,
                "middle_baseline": base_pct,
                "middle_baseline_n": n_base,
                "cohort": ck,
                "direction": dirn,
                "win_pct": pct,
                "delta_pp": delta,
                "n": n,
                "wins": t["w"],
                "losses": t["l"],
                "score": round(abs(delta) * math.log(n), 2),
            })

    middle_findings.sort(key=lambda x: -x["score"])

    # ── DISAGREEMENT RESOLUTION ──
    # When two models disagree on direction, what features predict which one wins?
    disagree_findings = []
    for pair_name, field_a, field_b, call_fn, actual_fn in disagreement_pairs():
        # Build the subset of games where A and B disagree
        disagree_rows = []
        for g in rows:
            a_call = call_fn(_f(g.get(field_a)))
            b_call = call_fn(_f(g.get(field_b)))
            if a_call and b_call and a_call != b_call:
                disagree_rows.append((g, a_call, b_call))
        if len(disagree_rows) < 30: continue
        # Baseline: how often does A win when they disagree?
        base = {"a_wins": 0, "b_wins": 0, "push": 0}
        for g, a, b in disagree_rows:
            act = actual_fn(g)
            if act is None: continue
            if act == "push": base["push"] += 1
            elif act == a: base["a_wins"] += 1
            else: base["b_wins"] += 1
        n_base = base["a_wins"] + base["b_wins"]
        if n_base < 30: continue
        a_pct = round(100 * base["a_wins"] / n_base, 1)
        print(f"  [disagree] {pair_name}: A wins {base['a_wins']}, B wins {base['b_wins']} (A: {a_pct}%, n={n_base})")

        # Cohort sweep on the disagreement subset — what features predict A winning?
        by_cohort = defaultdict(lambda: {"a_wins": 0, "b_wins": 0, "push": 0})
        for g, a, b in disagree_rows:
            act = actual_fn(g)
            if act is None: continue
            for ck in cohort_memberships(g):
                t = by_cohort[ck]
                if act == "push": t["push"] += 1
                elif act == a: t["a_wins"] += 1
                else: t["b_wins"] += 1

        for ck, t in by_cohort.items():
            n = t["a_wins"] + t["b_wins"]
            if n < min_n: continue
            pct = round(100 * t["a_wins"] / n, 1)
            delta = round(pct - a_pct, 1)
            if abs(delta) < deviation: continue
            disagree_findings.append({
                "pair": pair_name,
                "disagreement_baseline_a_wins_pct": a_pct,
                "cohort": ck,
                "a_wins_pct_in_cohort": pct,
                "delta_pp": delta,
                "n": n,
                "a_wins": t["a_wins"],
                "b_wins": t["b_wins"],
                "score": round(abs(delta) * math.log(n), 2),
            })
    disagree_findings.sort(key=lambda x: -x["score"])

    # ── PRINT ──
    print()
    print("=" * 145)
    print(f"MIDDLE-ZONE COHORT LIFTS  (signal found in the noise — min_n={min_n}, |delta|>={deviation}pp)")
    print("=" * 145)
    print(f"  {'PLAY':<12} {'DIR':<5} {'COHORT':<38} {'MID-BASE':>9} {'COHORT %':>9} {'DELTA':>7} {'n':>4}  {'W-L':>8}")
    print("-" * 145)
    for f in middle_findings[:40]:
        d = f"+{f['delta_pp']}" if f['delta_pp'] >= 0 else f"{f['delta_pp']}"
        print(f"  {f['play']:<12} {f['direction']:<5} {f['cohort']:<38} {f['middle_baseline']:>5}%/{f['middle_baseline_n']:<3} {f['win_pct']:>7}% {d:>6}pp {f['n']:>4}  {f['wins']:>3}-{f['losses']:<3}")

    print()
    print("=" * 145)
    print(f"DISAGREEMENT RESOLUTION  (when A and B disagree, what feature predicts A winning?)")
    print("=" * 145)
    print(f"  {'PAIR':<28} {'COHORT':<38} {'DISAGREE A%':>11} {'COHORT A%':>10} {'DELTA':>7} {'n':>4}  {'A-B':>8}")
    print("-" * 145)
    for f in disagree_findings[:30]:
        d = f"+{f['delta_pp']}" if f['delta_pp'] >= 0 else f"{f['delta_pp']}"
        print(f"  {f['pair']:<28} {f['cohort']:<38} {f['disagreement_baseline_a_wins_pct']:>10}% {f['a_wins_pct_in_cohort']:>9}% {d:>6}pp {f['n']:>4}  {f['a_wins']:>3}-{f['b_wins']:<3}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "rows_analyzed": len(rows),
        "middle_zone_findings": middle_findings,
        "disagreement_findings": disagree_findings,
    }, indent=2, default=str))
    print()
    print(f"[v6 / middle-zone] wrote {OUT_PATH} ({len(middle_findings)} mid + {len(disagree_findings)} disagree)")


if __name__ == "__main__":
    run()
