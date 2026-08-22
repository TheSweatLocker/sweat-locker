"""UFC v1 model inference + audit cohort calibration.

Provides:
  - predict_fight(fighter_a_url, fighter_b_url, fight_date, total_rounds_scheduled)
    → returns dict of probabilities for all 4 models
  - score_card(event_name=None) → scores upcoming card (or specified event),
    stores predictions to ufc_picks
  - backtest_cohorts() → runs all 4 models against historical fights,
    populates audit cohort hit rates
"""
import os
import sys
import json
import argparse
import requests
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
SUPABASE_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ufc_features import build_features

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
_LOADED_MODELS = {}

# 2026-08-20: v2 SWAP. Per _ufc_v2_roi_backtest_2026-08-20.md on the
# fair OOS window (n=50, event_date > 2026-05-06), v2 beat v1 by +15.1pp
# ROI (-5.8% vs -20.9%). Both models still bleed on this thin sample,
# but v2 bleeds less — running v1 while v2 is better = throwing money
# away for another 30 days. Swap now, gather 30d live data, revert if
# it drifts.
#
# Per-model policy from retrain report:
#   winner   → v2 (64.3% test acc, +9.4pp over v1 leaky OOS)
#   method   → v2 (fixed SUB collapse — v1 never predicted SUB)
#   distance → v1 (v2 barely beats baseline, retrain report says
#                  SHADOW ONLY — don't ship v2)
#   round    → v1 (v2 not retrained — deprecated in v2 pipeline)
#
# UFC_MODEL_VERSION env var overrides per-model (e.g. UFC_MODEL_VERSION=v1
# forces everything to v1 for emergency rollback).
_MODEL_VERSION_BY_NAME = {
    'winner':   'v2',
    'method':   'v2',
    'distance': 'v1',
    'round':    'v1',
}


def _model_version_for(name: str) -> str:
    """Resolve which version to load. Env override wins, else per-model default."""
    override = os.environ.get('UFC_MODEL_VERSION')
    if override in ('v1', 'v2'):
        return override
    return _MODEL_VERSION_BY_NAME.get(name, 'v1')


def _load_model(name):
    """Lazy-load saved model + metadata. Version resolves per-model.
    Falls back to v1 if v2 file is missing (safety net for partial rollout)."""
    if name in _LOADED_MODELS:
        return _LOADED_MODELS[name]
    try:
        import xgboost as xgb
    except ImportError:
        return None
    for version in (_model_version_for(name), 'v1'):
        model_path = os.path.join(MODELS_DIR, f"ufc_{version}_{name}.json")
        meta_path = os.path.join(MODELS_DIR, f"ufc_{version}_{name}.meta.json")
        if os.path.exists(model_path) and os.path.exists(meta_path):
            bst = xgb.Booster()
            bst.load_model(model_path)
            with open(meta_path) as f:
                meta = json.load(f)
            meta['_model_version'] = version  # audit trail
            _LOADED_MODELS[name] = (bst, meta)
            return _LOADED_MODELS[name]
    return None


def _feature_vector(feat_dict, feature_names):
    """Reorder feat_dict into the array form the model expects."""
    return np.array([[feat_dict.get(fn, 0) for fn in feature_names]], dtype=float)


def _swap_ab_features(feat):
    """Swap A/B feature orientation for symmetric-average prediction.
    Matches the _swap_ab doubling used at training time. See
    _ufc_v2_backtest_roi_2026-08-20.py::predict_symmetric_pa for the
    reference implementation this ports from."""
    a_keys = sorted(k for k in feat if k.startswith("a_"))
    b_keys = sorted(k for k in feat if k.startswith("b_"))
    sw = dict(feat)
    for ak, bk in zip(a_keys, b_keys):
        sw[ak], sw[bk] = feat[bk], feat[ak]
    for k in ("reach_diff", "height_diff", "age_diff", "slpm_diff",
              "str_def_diff", "td_def_diff", "sub_threat_diff",
              "finishing_rate_diff", "experience_diff", "form_diff"):
        if k in feat:
            sw[k] = -feat[k]
    if "stance_southpaw_a" in feat and "stance_southpaw_b" in feat:
        sw["stance_southpaw_a"] = feat["stance_southpaw_b"]
        sw["stance_southpaw_b"] = feat["stance_southpaw_a"]
    return sw


def predict_fight(fighter_a_url, fighter_b_url, fight_date, total_rounds_scheduled=3):
    """Run all 4 models for a fight. Returns dict of probabilities."""
    try:
        import xgboost as xgb
    except ImportError:
        return {"error": "xgboost not installed"}

    feat, ok = build_features(fighter_a_url, fighter_b_url, fight_date, total_rounds_scheduled)
    if not ok:
        return {"error": "missing features (debut or stats unavailable)"}

    out = {"features_built": True}

    # Winner — 2026-08-20: use symmetric-average probability. The backtest
    # (see _ufc_v2_roi_backtest_2026-08-20.md) uses the same _swap_ab
    # doubling from training time to reduce orientation bias in prediction.
    # Prior version scored in the natural (A, B) orientation only, which
    # can differ by 1-3pp from the symmetric average. Ports the trick
    # forward as the retrain report recommended.
    m = _load_model("winner")
    if m:
        bst, meta = m
        # Natural orientation P(A wins)
        x_nat = _feature_vector(feat, meta["feature_names"])
        p_nat = float(bst.predict(xgb.DMatrix(x_nat, feature_names=meta["feature_names"]))[0])
        # Swapped orientation — "P(A wins)" is now really P(original B wins)
        sw = _swap_ab_features(feat)
        x_sw = _feature_vector(sw, meta["feature_names"])
        p_sw_orient = float(bst.predict(xgb.DMatrix(x_sw, feature_names=meta["feature_names"]))[0])
        # Symmetric average of P(original A wins)
        p = 0.5 * (p_nat + (1.0 - p_sw_orient))
        out["_winner_model_version"] = meta.get('_model_version', 'v1')
        out["_p_winner_a_natural"] = p_nat
        out["_p_winner_a_symmetric"] = p
        # 2026-08-17: apply post-hoc calibration if calibrator file exists.
        # Isotonic regression trained on graded shadow-mode fights via
        # ufc_calibration_fit.py — remaps raw model prob → historically
        # observed prob. Fixes overconfidence (model 85%+ actual ~40%).
        # If no calibrator file, raw model probability passes through.
        try:
            import pickle
            from pathlib import Path as _P
            calib_path = _P(__file__).parent / "models" / "ufc_calibrator_v1.pkl"
            if calib_path.exists():
                with open(calib_path, "rb") as f:
                    calib_bundle = pickle.load(f)
                iso = calib_bundle.get("calibrator")
                if iso is not None:
                    raw_p = p
                    p = float(iso.predict([raw_p])[0])
                    out["p_winner_a_raw"] = raw_p
                    out["p_winner_a_calibrated"] = p
                    out["_calibrator_trained_at"] = calib_bundle.get("trained_at")
        except Exception as _cal_err:
            out["_calibrator_err"] = str(_cal_err)
        out["p_winner_a"] = p
        out["p_winner_b"] = 1 - p

    # Method (KO/TKO=0, SUB=1, DEC=2)
    m = _load_model("method")
    if m:
        bst, meta = m
        x = _feature_vector(feat, meta["feature_names"])
        probs = bst.predict(xgb.DMatrix(x, feature_names=meta["feature_names"]))[0]
        out["p_method_ko"] = float(probs[0])
        out["p_method_sub"] = float(probs[1])
        out["p_method_dec"] = float(probs[2])

    # Distance
    m = _load_model("distance")
    if m:
        bst, meta = m
        x = _feature_vector(feat, meta["feature_names"])
        p = float(bst.predict(xgb.DMatrix(x, feature_names=meta["feature_names"]))[0])
        out["p_distance"] = p

    # Round of finish (R1=0..R5=4)
    m = _load_model("round")
    if m:
        bst, meta = m
        x = _feature_vector(feat, meta["feature_names"])
        probs = bst.predict(xgb.DMatrix(x, feature_names=meta["feature_names"]))[0]
        for i, p in enumerate(probs):
            out[f"p_round_{i+1}"] = float(p)

    return out


def score_card(event_name=None):
    """Score a card from ufc_upcoming_event (default = latest)."""
    if event_name:
        params = {"event_name": f"eq.{event_name}", "select": "*"}
    else:
        params = {"select": "*", "order": "updated_at.desc", "limit": "1"}
    r = requests.get(f"{SUPABASE_URL}/rest/v1/ufc_upcoming_event", headers=HEADERS, params=params, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        print("No upcoming event found")
        return
    event = rows[0]
    fight_card = event.get("fight_card")
    if isinstance(fight_card, str):
        fight_card = json.loads(fight_card)
    print(f"\n=== {event['event_name']} ({event.get('event_date')}) ===\n")
    print(f"{'Fight':<55s} {'P(A win)':>10s} {'KO%':>7s} {'SUB%':>7s} {'DEC%':>7s} {'Dist%':>7s}")
    print("-" * 100)

    # Map fighter names → URLs from ufc_fighter_stats
    name_to_url = {}
    for f in fight_card:
        for nm in (f.get("fighter1"), f.get("fighter2")):
            if nm and nm not in name_to_url:
                rr = requests.get(
                    f"{SUPABASE_URL}/rest/v1/ufc_fighter_stats",
                    headers=HEADERS,
                    params={"fighter_name": f"ilike.{nm}", "select": "fighter_url", "limit": "1"},
                    timeout=10,
                )
                d = rr.json() if rr.status_code == 200 else []
                if d:
                    name_to_url[nm] = d[0]["fighter_url"]

    fight_date = event.get("event_date")
    # Convert "May 09, 2026" → "2026-05-09"
    iso_date = None
    if fight_date:
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                iso_date = datetime.strptime(fight_date.replace(".", ""), fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue

    for i, f in enumerate(fight_card):
        a_name = f.get("fighter1") or "?"
        b_name = f.get("fighter2") or "?"
        a_url = name_to_url.get(a_name)
        b_url = name_to_url.get(b_name)
        if not (a_url and b_url and iso_date):
            print(f"{a_name} vs {b_name:<35s} (missing data)")
            continue
        # Main event = 5 rounds, others = 3
        rounds = 5 if i == 0 else 3
        pred = predict_fight(a_url, b_url, iso_date, rounds)
        if "error" in pred:
            print(f"{a_name[:25]} vs {b_name[:25]:<25s} ({pred['error']})")
            continue
        matchup = f"{a_name[:25]} vs {b_name[:25]}"
        print(f"{matchup:<55s} {pred.get('p_winner_a',0):>10.3f}"
              f" {pred.get('p_method_ko',0)*100:>6.1f}% {pred.get('p_method_sub',0)*100:>6.1f}%"
              f" {pred.get('p_method_dec',0)*100:>6.1f}% {pred.get('p_distance',0)*100:>6.1f}%")


def backtest_cohorts():
    """Run all 4 models against the held-out training set (or full historical)
    to populate audit cohort hit rates."""
    print("Backtest mode — TODO: implement after training run validates")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--score-card", action="store_true")
    p.add_argument("--event", help="Specific event name to score")
    p.add_argument("--backtest", action="store_true")
    args = p.parse_args()
    if args.score_card or args.event:
        score_card(args.event)
    elif args.backtest:
        backtest_cohorts()
    else:
        print("Use --score-card or --backtest")
