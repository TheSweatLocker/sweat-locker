"""
Model attribution backtest v5 — recency stability check.

Same cohort definitions as v4 (broad sweep + 2D interactions +
direction-conditional + pitcher-class). Runs three time windows
side-by-side and flags cohorts where recent performance materially
diverges from lifetime — those are the candidates to filter OUT
before wiring into the selector.

WINDOWS:
  - lifetime (all 518 graded rows)
  - last 30 days
  - last 14 days

OUTPUT INTERPRETATION:
  - Stable cohort   = lifetime ~= 30d ~= 14d (within ~10pp)
  - Decaying        = lifetime is strong, 30d/14d significantly weaker
  - Strengthening   = lifetime modest, 30d/14d significantly stronger
  - Reversal flag   = sign change (was +edge, now -edge or vice versa)

USAGE: python backtest_model_attribution_v5_recency.py
OUTPUT: models/attribution_cohorts_v5_recency.json
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
OUT_PATH = Path(__file__).parent / "models" / "attribution_cohorts_v5_recency.json"


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


# ─── Play helpers ───
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


# ─── Cohort memberships (the high-yield set from v2/v3/v4) ───
def cohort_memberships(g):
    out = {}

    # Confluence
    cn = _i(g.get("signal_confluence_net"))
    if cn is not None:
        mag = abs(cn)
        out[f"conf_mag={mag}"] = True
        if mag >= 4: out["conf_mag>=4"] = True

    cs_dir = conf_side(cn) if cn is not None else None
    hd = home_is_dog(g)
    if cs_dir and hd is not None:
        if (cs_dir == "home" and hd) or (cs_dir == "away" and not hd):
            out["conf_points_to_dog"] = True
        else:
            out["conf_points_to_fav"] = True
        if out.get("conf_mag=4") and out.get("conf_points_to_fav"): out["conf4+fav"] = True
        if out.get("conf_mag=4") and out.get("conf_points_to_dog"): out["conf4+dog"] = True

    # Model agreement
    v3s = _f(g.get("projected_spread"))
    v4s = _f(g.get("model_pred_spread"))
    js  = _f(g.get("jerry_pred_spread"))
    v3d, v4d, jd = ml_call(v3s), ml_call(v4s), ml_call(js)
    if v3d and v4d: out["v3_v4_agree" if v3d == v4d else "v3_v4_disagree"] = True
    if v4d and jd: out["v4_jerry_agree" if v4d == jd else "v4_jerry_disagree"] = True

    # Spread magnitude
    if v3s is not None:
        mag = abs(v3s)
        if mag >= 2.0: out["v3_spread_loud"] = True

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

    # v3+v4 consensus on totals (loud, same direction)
    v3_t = _f(g.get("projected_total")); v4_t = _f(g.get("model_pred_total"))
    if line and v3_t and v4_t:
        d3 = v3_t - line; d4 = v4_t - line
        if (d3 > 0 and d4 > 0) or (d3 < 0 and d4 < 0):
            if abs(d3) >= 1.0 and abs(d4) >= 1.0:
                out["v3_v4_tot_consensus"] = True
                if abs(d3) >= 2.0 and abs(d4) >= 2.0:
                    out["v3_v4_tot_loud_consensus"] = True

    # Form drift / hot
    for side in ("home", "away"):
        l3 = _f(g.get(f"{side}_pitcher_last_3_era"))
        xera = _f(g.get(f"{side}_sp_xera"))
        if l3 is not None and xera is not None:
            d = l3 - xera
            if d >= 2.0: out[f"{side}_sp_form_drift_bad"] = True
            elif d <= -1.5: out[f"{side}_sp_form_hot"] = True

    # Mastery / blowup
    for side in ("home", "away"):
        vt_era = _f(g.get(f"{side}_pitcher_vs_team_era"))
        vt_ip = _f(g.get(f"{side}_pitcher_vs_team_ip"))
        if vt_era is not None and vt_ip and vt_ip >= 15:
            if vt_era <= 2.5: out[f"{side}_sp_mastery"] = True
            elif vt_era >= 5.5: out[f"{side}_sp_blowup_history"] = True

    # xERA tier + gap
    h_xera = _f(g.get("home_sp_xera")); a_xera = _f(g.get("away_sp_xera"))
    for side, x in (("home", h_xera), ("away", a_xera)):
        if x is None: continue
        if x <= 3.0: out[f"{side}_xera_elite"] = True
        elif x >= 4.75: out[f"{side}_xera_shaky"] = True

    if h_xera and a_xera:
        gap = abs(h_xera - a_xera)
        if gap >= 1.5: out["xera_gap_loud"] = True
        elif gap <= 0.3: out["xera_gap_tight"] = True
        if h_xera <= 3.0 and a_xera >= 4.75: out["mismatch_home_elite"] = True
        elif a_xera <= 3.0 and h_xera >= 4.75: out["mismatch_away_elite"] = True

    # K-class joint
    h_k = _f(g.get("home_sp_k_pct")); a_k = _f(g.get("away_sp_k_pct"))
    h_team_k = _f(g.get("home_team_k_pct")); a_team_k = _f(g.get("away_team_k_pct"))
    if h_k is not None and a_team_k is not None:
        if h_k >= 28 and a_team_k >= 25: out["home_sp_high_k+away_team_high_k"] = True
    if a_k is not None and h_team_k is not None:
        if a_k >= 28 and h_team_k >= 25: out["away_sp_high_k+home_team_high_k"] = True

    # K-gap loud
    for side in ("home", "away"):
        kg = _f(g.get(f"{side}_k_gap"))
        if kg is None: continue
        if kg >= 3.0: out[f"{side}_k_gap_loud_plus"] = True

    # Park / temp / ump
    park = _f(g.get("park_run_factor"))
    if park is not None:
        if park >= 108: out["park_extreme_hitter"] = True
        elif park >= 104: out["park_hitter_friendly"] = True
        elif park <= 92: out["park_extreme_pitcher"] = True
        elif park <= 96: out["park_pitcher_friendly"] = True

    temp = _f(g.get("temperature"))
    if temp is not None:
        if temp <= 50: out["temp_freezing"] = True
        elif temp >= 80: out["temp_hot"] = True

    un = (g.get("umpire_note") or "").lower()
    if "over-friendly" in un or "hitter-friendly" in un: out["ump_over"] = True
    elif "under-friendly" in un or "pitcher-friendly" in un: out["ump_under"] = True

    # Form drift + opp wRC+ hot
    if out.get("home_sp_form_drift_bad"):
        opp_wrc = _f(g.get("away_wrc_plus"))
        if opp_wrc and opp_wrc >= 105: out["home_drift+opp_hot"] = True
    if out.get("away_sp_form_drift_bad"):
        opp_wrc = _f(g.get("home_wrc_plus"))
        if opp_wrc and opp_wrc >= 105: out["away_drift+opp_hot"] = True

    # v3 tot loud + form drift compound
    for prefix in ("v3_tot_loud",):
        if not out.get(prefix): continue
        for second in ("home_sp_form_drift_bad", "away_sp_form_drift_bad"):
            if out.get(second):
                out[f"{prefix}+{second}"] = True

    # Both shaky / both aces / both 1st-inn shaky
    if out.get("home_xera_shaky") and out.get("away_xera_shaky"):
        out["both_shaky"] = True

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


def tally_window(rows, window_label, min_n=8):
    """Return dict {(play, cohort, dirn) -> {w,l,p,n,pct}} for this row set."""
    overall = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
    splits = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})
    by_cohort = defaultdict(lambda: {"w": 0, "l": 0, "p": 0})

    for g in rows:
        cohorts = cohort_memberships(g)
        for play, predict in PLAY_TYPES:
            call = predict(g)
            res = score_play(call, actual_for_play(play, g))
            if res is None: continue
            t = overall[play]
            t["w"] += (res == "W"); t["l"] += (res == "L"); t["p"] += (res == "P")
            if call:
                t2 = splits[(play, call)]
                t2["w"] += (res == "W"); t2["l"] += (res == "L"); t2["p"] += (res == "P")
                for ck in cohorts:
                    tc = by_cohort[(play, ck, "any")]
                    tc["w"] += (res == "W"); tc["l"] += (res == "L"); tc["p"] += (res == "P")
                    tc2 = by_cohort[(play, ck, call)]
                    tc2["w"] += (res == "W"); tc2["l"] += (res == "L"); tc2["p"] += (res == "P")

    # Compute baselines (per play, per (play, dir))
    play_base = {}
    for play, t in overall.items():
        n = t["w"] + t["l"]
        play_base[play] = round(100*t["w"]/n, 1) if n else None
    dir_base = {}
    for (play, d), t in splits.items():
        n = t["w"] + t["l"]
        dir_base[(play, d)] = round(100*t["w"]/n, 1) if n else None

    # Cohort outputs
    out = {}
    for (play, ck, dirn), t in by_cohort.items():
        n = t["w"] + t["l"]
        if n < min_n: continue
        pct = round(100*t["w"]/n, 1) if n else None
        if dirn == "any":
            base = play_base.get(play) or 0
        else:
            base = dir_base.get((play, dirn)) or play_base.get(play) or 0
        out[(play, ck, dirn)] = {
            "n": n, "w": t["w"], "l": t["l"], "p": t["p"],
            "pct": pct, "delta_pp": round((pct - base) if pct is not None else 0, 1),
            "baseline_pct": base,
        }
    return {"cohorts": out, "play_baselines": play_base, "dir_baselines": dir_base}


def run():
    print("[v5 / recency] fetching all rows...")
    rows = fetch_rows()
    print(f"  {len(rows)} rows pulled")

    today = datetime.now(timezone.utc).date()
    cutoff_30 = (today - timedelta(days=30)).isoformat()
    cutoff_14 = (today - timedelta(days=14)).isoformat()

    rows_30 = [r for r in rows if r.get("game_date") and r["game_date"] >= cutoff_30]
    rows_14 = [r for r in rows if r.get("game_date") and r["game_date"] >= cutoff_14]
    print(f"  last 30d: {len(rows_30)} rows | last 14d: {len(rows_14)} rows")

    print("\n[v5 / recency] tallying each window...")
    life = tally_window(rows, "lifetime", min_n=15)
    last30 = tally_window(rows_30, "30d", min_n=8)
    last14 = tally_window(rows_14, "14d", min_n=5)

    # Side-by-side report — for each strong lifetime cohort, compare
    findings = []
    for key, life_data in life["cohorts"].items():
        # Only report cohorts with meaningful lifetime signal
        if abs(life_data["delta_pp"]) < 7.0: continue
        play, ck, dirn = key
        d30 = last30["cohorts"].get(key, {})
        d14 = last14["cohorts"].get(key, {})

        # Classify recency status
        status = "stable"
        notes = []
        if d30:
            drift_30 = (d30["pct"] - life_data["pct"]) if d30.get("pct") is not None else None
            if drift_30 is not None and abs(drift_30) >= 15:
                if (life_data["pct"] > 50 and d30["pct"] < 50) or (life_data["pct"] < 50 and d30["pct"] > 50):
                    status = "REVERSED_30d"
                    notes.append(f"sign flip in 30d ({d30['pct']}%, n={d30['n']})")
                elif drift_30 < -15:
                    status = "decaying"
                    notes.append(f"30d at {d30['pct']}% ({drift_30:+.1f}pp)")
                elif drift_30 > 15:
                    status = "strengthening"
                    notes.append(f"30d at {d30['pct']}% ({drift_30:+.1f}pp)")
        if d14 and d14.get("n", 0) >= 5:
            drift_14 = (d14["pct"] - life_data["pct"]) if d14.get("pct") is not None else None
            if drift_14 is not None and abs(drift_14) >= 20:
                notes.append(f"14d at {d14['pct']}% ({drift_14:+.1f}pp, n={d14['n']})")

        findings.append({
            "play": play, "cohort": ck, "direction": dirn,
            "lifetime": {"pct": life_data["pct"], "n": life_data["n"], "delta": life_data["delta_pp"]},
            "last_30d": {"pct": d30.get("pct"), "n": d30.get("n"), "delta": d30.get("delta_pp")} if d30 else None,
            "last_14d": {"pct": d14.get("pct"), "n": d14.get("n"), "delta": d14.get("delta_pp")} if d14 else None,
            "status": status,
            "notes": "; ".join(notes) if notes else "",
        })

    # Sort: decaying/reversed first (the ones we DON'T want to wire),
    # then stable strong-edge cohorts
    findings.sort(key=lambda f: (
        0 if f["status"] == "REVERSED_30d" else
        1 if f["status"] == "decaying" else
        2 if f["status"] == "strengthening" else 3,
        -abs(f["lifetime"]["delta"])
    ))

    # ── REPORTING ──
    print()
    print("=" * 145)
    print("RECENCY-AT-RISK COHORTS  (reversed or decaying — flag before wiring)")
    print("=" * 145)
    print(f"  {'STATUS':<14} {'PLAY':<13} {'DIR':<5} {'COHORT':<38} {'LIFE %':>8} {'30d %':>8} {'14d %':>8}  {'NOTES'}")
    print("-" * 145)
    at_risk = [f for f in findings if f["status"] in ("REVERSED_30d", "decaying")]
    for f in at_risk[:25]:
        l = f["lifetime"]; d30 = f.get("last_30d") or {}; d14 = f.get("last_14d") or {}
        life_s = f"{l['pct']}%/{l['n']}"
        d30_s = f"{d30.get('pct') or '--'}%/{d30.get('n') or '-'}" if d30 else "n/a"
        d14_s = f"{d14.get('pct') or '--'}%/{d14.get('n') or '-'}" if d14 else "n/a"
        print(f"  {f['status']:<14} {f['play']:<13} {f['direction']:<5} {f['cohort']:<38} {life_s:>8} {d30_s:>8} {d14_s:>8}  {f['notes']}")

    print()
    print("=" * 145)
    print("STRENGTHENING (lifetime modest, 30d significantly stronger)")
    print("=" * 145)
    print(f"  {'PLAY':<13} {'DIR':<5} {'COHORT':<38} {'LIFE %':>8} {'30d %':>8} {'14d %':>8}  {'NOTES'}")
    print("-" * 145)
    strengthening = [f for f in findings if f["status"] == "strengthening"]
    for f in strengthening[:15]:
        l = f["lifetime"]; d30 = f.get("last_30d") or {}; d14 = f.get("last_14d") or {}
        life_s = f"{l['pct']}%/{l['n']}"
        d30_s = f"{d30.get('pct') or '--'}%/{d30.get('n') or '-'}" if d30 else "n/a"
        d14_s = f"{d14.get('pct') or '--'}%/{d14.get('n') or '-'}" if d14 else "n/a"
        print(f"  {f['play']:<13} {f['direction']:<5} {f['cohort']:<38} {life_s:>8} {d30_s:>8} {d14_s:>8}  {f['notes']}")

    print()
    print("=" * 145)
    print("STABLE STRONG-EDGE COHORTS  (lifetime + recent both holding — safe to wire)")
    print("=" * 145)
    print(f"  {'PLAY':<13} {'DIR':<5} {'COHORT':<38} {'LIFE %':>8} {'30d %':>8} {'14d %':>8}  {'n14'}")
    print("-" * 145)
    stable = [f for f in findings if f["status"] == "stable" and abs(f["lifetime"]["delta"]) >= 12.0]
    for f in stable[:35]:
        l = f["lifetime"]; d30 = f.get("last_30d") or {}; d14 = f.get("last_14d") or {}
        life_s = f"{l['pct']}%/{l['n']}"
        d30_s = f"{d30.get('pct') or '--'}%/{d30.get('n') or '-'}" if d30 else "n/a"
        d14_s = f"{d14.get('pct') or '--'}%/{d14.get('n') or '-'}" if d14 else "n/a"
        print(f"  {f['play']:<13} {f['direction']:<5} {f['cohort']:<38} {life_s:>8} {d30_s:>8} {d14_s:>8}  {d14.get('n') or '-'}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "lifetime_n": len(rows),
        "last_30d_n": len(rows_30),
        "last_14d_n": len(rows_14),
        "findings": findings,
    }, indent=2, default=str))
    print()
    print(f"[v5 / recency] wrote {OUT_PATH} ({len(findings)} cohorts analyzed)")
    print(f"  at-risk: {len(at_risk)} | strengthening: {len(strengthening)} | stable strong-edge: {len(stable)}")


if __name__ == "__main__":
    run()
