"""UFC v2 model training — time-based split + calibrated multi-class.

v1 (May 2026) shipped with a random 80/20 split; every fight leaked
because feature snapshots share a cross-fighter career-stats table and
symmetrization created near-duplicate rows in both halves. v2 fixes that:

  1. Split by event_date BEFORE symmetrization (no fight in two splits).
  2. Random search on validate split, XGBoost early-stopping = 50 rounds.
  3. Method model uses inverse-frequency sample_weight to prevent SUB
     collapse (v1 predicted zero SUBs on holdout).
  4. Distance model retested; kept only if it beats majority baseline.

Data reality (pulled 2026-08-19): 1,115 fights spanning 2024-05 through
2026-08. Splits chosen against that window:
  train:  event_date <  2025-10-01
  val:    2025-10-01 <= event_date < 2026-02-01
  test:   event_date >= 2026-02-01

v1 models are NOT overwritten. v2 artifacts go to
`mlb_pipeline/models/ufc_v2_*.json`. ufc_predict.py is untouched — the
swap to v2 requires user sign-off on the report.

Usage:
  python ufc_v2_train.py
  python ufc_v2_train.py --seed 42
"""
import os
import sys
import json
import argparse
import pickle
import random
import requests
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ufc_features import build_features, label_for_fight

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(MODELS_DIR, exist_ok=True)

# Split boundaries — tuned against actual data window (2024-05 to 2026-08).
TRAIN_END = "2025-10-01"
VAL_END = "2026-02-01"


# -----------------------------------------------------------------------------
# Data fetch
# -----------------------------------------------------------------------------
def fetch_all_resolved_fights():
    all_fights = []
    offset = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/ufc_fight_results"
            f"?select=*&order=event_date.asc&limit=1000&offset={offset}",
            headers=HEADERS, timeout=30,
        )
        batch = r.json() if r.status_code == 200 else []
        if not batch or not isinstance(batch, list):
            break
        all_fights.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return all_fights


# -----------------------------------------------------------------------------
# Symmetrization — same pattern v1 used, applied per-split (not globally)
# -----------------------------------------------------------------------------
def _swap_ab(feat_dict):
    """Return feature vector with fighter_a / fighter_b swapped.

    UFCStats lists the winner first, so raw fighter_a always won. To force
    the model to learn symmetric structure, every fight gets included in
    both orientations. Critically for v2, symmetrization is done AFTER the
    time split so both copies of a fight land in the same split.
    """
    a_keys = [k for k in feat_dict if k.startswith("a_")]
    b_keys = [k for k in feat_dict if k.startswith("b_")]
    swapped = dict(feat_dict)
    for ak, bk in zip(sorted(a_keys), sorted(b_keys)):
        swapped[ak], swapped[bk] = feat_dict[bk], feat_dict[ak]
    for k in ("reach_diff", "height_diff", "age_diff", "slpm_diff",
              "str_def_diff", "td_def_diff", "sub_threat_diff",
              "finishing_rate_diff", "experience_diff", "form_diff"):
        if k in feat_dict:
            swapped[k] = -feat_dict[k]
    if "stance_southpaw_a" in feat_dict and "stance_southpaw_b" in feat_dict:
        swapped["stance_southpaw_a"] = feat_dict["stance_southpaw_b"]
        swapped["stance_southpaw_b"] = feat_dict["stance_southpaw_a"]
    return swapped


def build_split_matrix(fights, split_name, verbose=True):
    """Build feature+label rows for one split, then symmetrize.

    Only fights inside the split contribute (both orientations of the same
    fight go to the same split — no leakage across boundaries).
    """
    rows = []
    skipped_no_features = 0
    skipped_no_label = 0
    skipped_bad = 0

    for i, fight in enumerate(fights):
        if not fight.get("fighter_a_url") or not fight.get("fighter_b_url"):
            skipped_bad += 1
            continue
        if not fight.get("event_date"):
            skipped_bad += 1
            continue

        labels = label_for_fight(fight)
        if labels is None:
            skipped_no_label += 1
            continue

        rounds_sched = fight.get("total_rounds_scheduled") or 3
        feat, ok = build_features(
            fight["fighter_a_url"], fight["fighter_b_url"],
            fight["event_date"], total_rounds_scheduled=rounds_sched,
        )
        if not ok:
            skipped_no_features += 1
            continue

        rows.append({**feat, **labels})
        swapped_feat = _swap_ab(feat)
        swapped_labels = dict(labels)
        swapped_labels["winner_a"] = 1 - labels["winner_a"]
        rows.append({**swapped_feat, **swapped_labels})

        if verbose and (i + 1) % 100 == 0:
            print(f"  [{split_name}] processed {i+1}/{len(fights)}, kept {len(rows)}")

    if verbose:
        print(f"  [{split_name}] final rows: {len(rows)} "
              f"(no_features={skipped_no_features}, "
              f"no_label={skipped_no_label}, bad={skipped_bad})")
    return rows


def matrix_to_xy(rows, label_key, feature_names=None):
    """Convert list-of-dicts to (X, y, feature_names).

    When feature_names is supplied, uses that ordering exactly (needed to
    keep train/val/test aligned column-for-column).
    """
    drop = {"winner_a", "method_class", "went_distance", "finish_round"}
    valid = [r for r in rows if r.get(label_key) is not None]
    if not valid:
        return None, None, None
    if feature_names is None:
        feature_names = sorted([k for k in valid[0].keys() if k not in drop])
    X = np.array([[r.get(fn, 0) for fn in feature_names] for r in valid], dtype=float)
    y = np.array([r[label_key] for r in valid])
    return X, y, feature_names


# -----------------------------------------------------------------------------
# Metrics helpers
# -----------------------------------------------------------------------------
def _binary_metrics(y_true, y_prob):
    from sklearn.metrics import log_loss, roc_auc_score
    pred = (y_prob >= 0.5).astype(int)
    acc = float((pred == y_true).mean())
    ll = float(log_loss(y_true, np.clip(y_prob, 1e-6, 1 - 1e-6)))
    try:
        auc = float(roc_auc_score(y_true, y_prob))
    except Exception:
        auc = None
    return {"log_loss": ll, "accuracy": acc, "auc": auc, "n": int(len(y_true))}


def _multi_metrics(y_true, y_prob, n_classes):
    from sklearn.metrics import log_loss
    pred = np.argmax(y_prob, axis=1)
    acc = float((pred == y_true).mean())
    ll = float(log_loss(y_true, np.clip(y_prob, 1e-6, 1 - 1e-6),
                        labels=list(range(n_classes))))
    return {"log_loss": ll, "accuracy": acc, "n": int(len(y_true))}


def _per_class_prf(y_true, y_pred, n_classes, class_names):
    """Precision / recall / f1 / support per class + confusion matrix."""
    from sklearn.metrics import precision_recall_fscore_support, confusion_matrix
    p, r, f, sup = precision_recall_fscore_support(
        y_true, y_pred, labels=list(range(n_classes)), zero_division=0
    )
    cm = confusion_matrix(y_true, y_pred, labels=list(range(n_classes)))
    return {
        "per_class": {
            class_names[i]: {
                "precision": float(p[i]),
                "recall": float(r[i]),
                "f1": float(f[i]),
                "support": int(sup[i]),
                "predicted_count": int((y_pred == i).sum()),
            } for i in range(n_classes)
        },
        "confusion_matrix": cm.tolist(),
        "class_order": class_names,
    }


# -----------------------------------------------------------------------------
# Random search + training
# -----------------------------------------------------------------------------
def _sample_config(rng):
    return {
        "max_depth": rng.choice([3, 4, 5, 6, 7]),
        "learning_rate": rng.choice([0.02, 0.03, 0.05, 0.075, 0.1]),
        "min_child_weight": rng.choice([1, 3, 5, 8]),
        "subsample": rng.choice([0.7, 0.8, 0.9, 1.0]),
        "colsample_bytree": rng.choice([0.6, 0.75, 0.9, 1.0]),
        "reg_lambda": rng.choice([0.5, 1.0, 2.0, 5.0]),
    }


def train_winner(X_tr, y_tr, X_va, y_va, X_te, y_te, feature_names,
                 n_configs=25, seed=42):
    """XGBoost binary winner. Random search on val log-loss, ES=50."""
    import xgboost as xgb

    rng = random.Random(seed)
    dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=feature_names)
    dval = xgb.DMatrix(X_va, label=y_va, feature_names=feature_names)
    dtest = xgb.DMatrix(X_te, label=y_te, feature_names=feature_names)

    best = None
    trials = []
    for i in range(n_configs):
        cfg = _sample_config(rng)
        params = {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "eta": cfg["learning_rate"],
            "max_depth": cfg["max_depth"],
            "min_child_weight": cfg["min_child_weight"],
            "subsample": cfg["subsample"],
            "colsample_bytree": cfg["colsample_bytree"],
            "reg_lambda": cfg["reg_lambda"],
            "verbosity": 0,
            "seed": seed,
        }
        bst = xgb.train(
            params, dtrain, num_boost_round=800,
            evals=[(dval, "val")],
            early_stopping_rounds=50, verbose_eval=False,
        )
        best_it = bst.best_iteration
        val_pred = bst.predict(dval, iteration_range=(0, best_it + 1))
        val_m = _binary_metrics(y_va, val_pred)
        trials.append({"config": cfg, "best_iter": int(best_it), "val": val_m})
        print(f"  cfg {i+1}/{n_configs} d={cfg['max_depth']} eta={cfg['learning_rate']} "
              f"-> val logloss {val_m['log_loss']:.4f} acc {val_m['accuracy']:.3f}")
        if best is None or val_m["log_loss"] < best["val"]["log_loss"]:
            best = {"booster": bst, "config": cfg, "best_iter": int(best_it), "val": val_m}

    it = best["best_iter"] + 1
    train_pred = best["booster"].predict(dtrain, iteration_range=(0, it))
    val_pred = best["booster"].predict(dval, iteration_range=(0, it))
    test_pred = best["booster"].predict(dtest, iteration_range=(0, it))

    return {
        "booster": best["booster"],
        "config": best["config"],
        "best_iter": best["best_iter"],
        "train_metrics": _binary_metrics(y_tr, train_pred),
        "val_metrics": _binary_metrics(y_va, val_pred),
        "test_metrics": _binary_metrics(y_te, test_pred),
        "test_probs": test_pred,
        "train_trials": trials,
    }


def _class_weights(y):
    """Inverse-frequency sample weight (normalized to mean=1)."""
    counts = np.bincount(y.astype(int))
    inv = 1.0 / np.clip(counts, 1, None)
    w_per_class = inv / inv.mean()
    return np.array([w_per_class[int(c)] for c in y])


def train_method(X_tr, y_tr, X_va, y_va, X_te, y_te, feature_names,
                 n_configs=25, seed=42, heavier=False):
    """3-class method model. Inverse-frequency sample weight to protect SUB.

    Returns booster + metrics. If heavier=True, applies SUB^2 boost — used
    as a fallback if the initial run predicts zero SUBs on test.
    """
    import xgboost as xgb

    rng = random.Random(seed)
    w_tr = _class_weights(y_tr)
    if heavier:
        # Boost SUB (class 1) further
        sub_mask = (y_tr == 1)
        w_tr[sub_mask] *= 2.0

    dtrain = xgb.DMatrix(X_tr, label=y_tr, weight=w_tr, feature_names=feature_names)
    dval = xgb.DMatrix(X_va, label=y_va, feature_names=feature_names)
    dtest = xgb.DMatrix(X_te, label=y_te, feature_names=feature_names)

    best = None
    trials = []
    for i in range(n_configs):
        cfg = _sample_config(rng)
        params = {
            "objective": "multi:softprob",
            "num_class": 3,
            "eval_metric": "mlogloss",
            "eta": cfg["learning_rate"],
            "max_depth": cfg["max_depth"],
            "min_child_weight": cfg["min_child_weight"],
            "subsample": cfg["subsample"],
            "colsample_bytree": cfg["colsample_bytree"],
            "reg_lambda": cfg["reg_lambda"],
            "verbosity": 0,
            "seed": seed,
        }
        bst = xgb.train(
            params, dtrain, num_boost_round=800,
            evals=[(dval, "val")],
            early_stopping_rounds=50, verbose_eval=False,
        )
        best_it = bst.best_iteration
        val_pred = bst.predict(dval, iteration_range=(0, best_it + 1))
        val_m = _multi_metrics(y_va, val_pred, 3)
        trials.append({"config": cfg, "best_iter": int(best_it), "val": val_m})
        print(f"  method cfg {i+1}/{n_configs} d={cfg['max_depth']} eta={cfg['learning_rate']} "
              f"-> val mlogloss {val_m['log_loss']:.4f} acc {val_m['accuracy']:.3f}")
        if best is None or val_m["log_loss"] < best["val"]["log_loss"]:
            best = {"booster": bst, "config": cfg, "best_iter": int(best_it), "val": val_m}

    it = best["best_iter"] + 1
    train_pred = best["booster"].predict(dtrain, iteration_range=(0, it))
    val_pred = best["booster"].predict(dval, iteration_range=(0, it))
    test_pred = best["booster"].predict(dtest, iteration_range=(0, it))
    test_argmax = np.argmax(test_pred, axis=1)

    class_names = ["KO_TKO", "SUB", "DEC"]
    return {
        "booster": best["booster"],
        "config": best["config"],
        "best_iter": best["best_iter"],
        "train_metrics": _multi_metrics(y_tr, train_pred, 3),
        "val_metrics": _multi_metrics(y_va, val_pred, 3),
        "test_metrics": _multi_metrics(y_te, test_pred, 3),
        "test_per_class": _per_class_prf(y_te, test_argmax, 3, class_names),
        "test_predicted_class_counts": {
            class_names[i]: int((test_argmax == i).sum()) for i in range(3)
        },
        "heavier": heavier,
        "trials_count": len(trials),
    }


def train_distance(X_tr, y_tr, X_va, y_va, X_te, y_te, feature_names,
                   n_configs=20, seed=42):
    """Binary distance model with inverse-freq sample weights."""
    import xgboost as xgb

    rng = random.Random(seed)
    counts = np.bincount(y_tr.astype(int))
    scale_pos_weight = counts[0] / max(counts[1], 1)  # negatives / positives
    dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=feature_names)
    dval = xgb.DMatrix(X_va, label=y_va, feature_names=feature_names)
    dtest = xgb.DMatrix(X_te, label=y_te, feature_names=feature_names)

    best = None
    for i in range(n_configs):
        cfg = _sample_config(rng)
        params = {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "eta": cfg["learning_rate"],
            "max_depth": cfg["max_depth"],
            "min_child_weight": cfg["min_child_weight"],
            "subsample": cfg["subsample"],
            "colsample_bytree": cfg["colsample_bytree"],
            "reg_lambda": cfg["reg_lambda"],
            "scale_pos_weight": scale_pos_weight,
            "verbosity": 0,
            "seed": seed,
        }
        bst = xgb.train(
            params, dtrain, num_boost_round=600,
            evals=[(dval, "val")],
            early_stopping_rounds=50, verbose_eval=False,
        )
        best_it = bst.best_iteration
        val_pred = bst.predict(dval, iteration_range=(0, best_it + 1))
        val_m = _binary_metrics(y_va, val_pred)
        if best is None or val_m["log_loss"] < best["val"]["log_loss"]:
            best = {"booster": bst, "config": cfg, "best_iter": int(best_it), "val": val_m}

    it = best["best_iter"] + 1
    test_pred = best["booster"].predict(dtest, iteration_range=(0, it))
    test_m = _binary_metrics(y_te, test_pred)

    # Baseline = predict majority class
    maj_class = int(np.bincount(y_tr.astype(int)).argmax())
    baseline_acc = float((y_te == maj_class).mean())

    return {
        "booster": best["booster"],
        "config": best["config"],
        "best_iter": best["best_iter"],
        "test_metrics": test_m,
        "baseline_acc": baseline_acc,
        "beats_baseline": bool(test_m["accuracy"] > baseline_acc),
    }


# -----------------------------------------------------------------------------
# Calibration + feature importance for report
# -----------------------------------------------------------------------------
def calibration_bins(y_true, y_prob, n_bins=20):
    """Bin probs, return (bin_center, empirical_rate, count) per bin."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        if i == n_bins - 1:
            mask = (y_prob >= lo) & (y_prob <= hi)
        else:
            mask = (y_prob >= lo) & (y_prob < hi)
        n = int(mask.sum())
        if n == 0:
            continue
        emp = float(y_true[mask].mean())
        out.append({
            "bin_lo": float(lo), "bin_hi": float(hi),
            "avg_pred": float(y_prob[mask].mean()),
            "empirical": emp,
            "n": n,
        })
    return out


def feature_importance_top(booster, feature_names, top=15):
    """Extract gain-weighted importance.

    XGBoost keys booster gain dict by either `f{N}` (default when the model
    was fit without feature_names) OR by the actual feature name (when fit
    with named columns). Prior version's `k.startswith("f")` check was too
    greedy — it matched real feature names starting with 'f' like
    `finishing_rate_diff`, then tried `int("inishing_rate_diff")` and
    crashed the whole run right before save. Fix: only treat as `fN` when
    the suffix is purely digits AND the index is in range.
    """
    imp = booster.get_score(importance_type="gain")
    scored = []
    for k, v in imp.items():
        if k.startswith("f") and k[1:].isdigit():
            idx = int(k[1:])
            name = feature_names[idx] if 0 <= idx < len(feature_names) else k
        else:
            name = k
        scored.append((name, float(v)))
    scored.sort(key=lambda x: -x[1])
    return scored[:top]


# -----------------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------------
def save_v2_model(booster, name, feature_names, meta_payload):
    model_path = os.path.join(MODELS_DIR, f"ufc_v2_{name}.json")
    booster.save_model(model_path)
    meta = {
        "name": name,
        "version": "v2",
        "feature_names": feature_names,
        "trained_at": datetime.utcnow().isoformat(),
        **meta_payload,
    }
    with open(os.path.join(MODELS_DIR, f"ufc_v2_{name}.meta.json"), "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"  saved {model_path}")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def run(seed=42):
    print("Pulling resolved fights...")
    fights = fetch_all_resolved_fights()
    print(f"  Total: {len(fights)}")

    train_fights = [f for f in fights if f["event_date"] < TRAIN_END]
    val_fights = [f for f in fights if TRAIN_END <= f["event_date"] < VAL_END]
    test_fights = [f for f in fights if f["event_date"] >= VAL_END]
    print(f"  Train  (event_date < {TRAIN_END}):     {len(train_fights)} fights")
    print(f"  Val    ({TRAIN_END} to <{VAL_END}):   {len(val_fights)} fights")
    print(f"  Test   (event_date >= {VAL_END}):     {len(test_fights)} fights")

    # Optional pickle cache — features are 15+ min to rebuild via API.
    cache_path = os.environ.get("UFC_V2_ROW_CACHE")
    if cache_path and os.path.exists(cache_path):
        print(f"\nLoading feature rows from cache: {cache_path}")
        with open(cache_path, "rb") as f:
            payload = pickle.load(f)
        train_rows = payload["train"]
        val_rows = payload["val"]
        test_rows = payload["test"]
        print(f"  cached: train={len(train_rows)}, val={len(val_rows)}, test={len(test_rows)}")
    else:
        print("\nBuilding features (Train)...")
        train_rows = build_split_matrix(train_fights, "train")
        print("Building features (Val)...")
        val_rows = build_split_matrix(val_fights, "val")
        print("Building features (Test)...")
        test_rows = build_split_matrix(test_fights, "test")
        if cache_path:
            with open(cache_path, "wb") as f:
                pickle.dump({"train": train_rows, "val": val_rows, "test": test_rows}, f)
            print(f"  saved cache: {cache_path}")

    # -----------------------------
    # Winner model
    # -----------------------------
    print("\n=== Winner (P[fighter_a wins]) ===")
    X_tr, y_tr, feats = matrix_to_xy(train_rows, "winner_a")
    X_va, y_va, _ = matrix_to_xy(val_rows, "winner_a", feature_names=feats)
    X_te, y_te, _ = matrix_to_xy(test_rows, "winner_a", feature_names=feats)
    print(f"  Rows: train {len(X_tr)}, val {len(X_va)}, test {len(X_te)}")
    print(f"  Features: {len(feats)}")

    winner_result = train_winner(X_tr, y_tr, X_va, y_va, X_te, y_te,
                                 feats, n_configs=25, seed=seed)
    print(f"\n  WINNER best config: {winner_result['config']}")
    print(f"    train:  {winner_result['train_metrics']}")
    print(f"    val:    {winner_result['val_metrics']}")
    print(f"    test:   {winner_result['test_metrics']}")

    winner_calib = calibration_bins(y_te, winner_result["test_probs"], n_bins=20)
    winner_imp = feature_importance_top(winner_result["booster"], feats, top=15)

    save_v2_model(
        winner_result["booster"], "winner", feats,
        meta_payload={
            "hyperparams": winner_result["config"],
            "best_iter": winner_result["best_iter"],
            "n_train": len(X_tr), "n_val": len(X_va), "n_test": len(X_te),
            "train_metrics": winner_result["train_metrics"],
            "val_metrics": winner_result["val_metrics"],
            "test_metrics": winner_result["test_metrics"],
            "split_boundaries": {"train_end": TRAIN_END, "val_end": VAL_END},
            "calibration_bins_test": winner_calib,
            "feature_importance_top15": [{"name": n, "gain": g} for n, g in winner_imp],
        },
    )

    # -----------------------------
    # Method model
    # -----------------------------
    print("\n=== Method (KO/TKO=0, SUB=1, DEC=2) ===")
    Xm_tr, ym_tr, feats_m = matrix_to_xy(train_rows, "method_class")
    Xm_va, ym_va, _ = matrix_to_xy(val_rows, "method_class", feature_names=feats_m)
    Xm_te, ym_te, _ = matrix_to_xy(test_rows, "method_class", feature_names=feats_m)

    tr_counts = np.bincount(ym_tr.astype(int), minlength=3)
    te_counts = np.bincount(ym_te.astype(int), minlength=3)
    print(f"  Train counts: KO={tr_counts[0]} SUB={tr_counts[1]} DEC={tr_counts[2]}")
    print(f"  Test  counts: KO={te_counts[0]} SUB={te_counts[1]} DEC={te_counts[2]}")

    method_result = train_method(Xm_tr, ym_tr, Xm_va, ym_va, Xm_te, ym_te,
                                 feats_m, n_configs=20, seed=seed, heavier=False)
    # SUB collapse retry
    if method_result["test_predicted_class_counts"]["SUB"] == 0:
        print("\n  METHOD retry with heavier SUB weight...")
        method_result = train_method(Xm_tr, ym_tr, Xm_va, ym_va, Xm_te, ym_te,
                                     feats_m, n_configs=20, seed=seed, heavier=True)
    print(f"\n  METHOD best config: {method_result['config']}")
    print(f"    train:  {method_result['train_metrics']}")
    print(f"    val:    {method_result['val_metrics']}")
    print(f"    test:   {method_result['test_metrics']}")
    print(f"    per-class: {method_result['test_per_class']['per_class']}")
    print(f"    predicted class counts: {method_result['test_predicted_class_counts']}")

    save_v2_model(
        method_result["booster"], "method", feats_m,
        meta_payload={
            "hyperparams": method_result["config"],
            "best_iter": method_result["best_iter"],
            "n_train": len(Xm_tr), "n_val": len(Xm_va), "n_test": len(Xm_te),
            "class_names": ["KO_TKO", "SUB", "DEC"],
            "train_metrics": method_result["train_metrics"],
            "val_metrics": method_result["val_metrics"],
            "test_metrics": method_result["test_metrics"],
            "test_per_class": method_result["test_per_class"],
            "test_predicted_class_counts": method_result["test_predicted_class_counts"],
            "heavier_sub_weight_used": method_result["heavier"],
            "split_boundaries": {"train_end": TRAIN_END, "val_end": VAL_END},
            "sample_weight_strategy": "inverse_frequency" + ("_x2_sub_boost" if method_result["heavier"] else ""),
        },
    )

    # -----------------------------
    # Distance model — save only if beats baseline
    # -----------------------------
    print("\n=== Distance (went distance yes/no) ===")
    Xd_tr, yd_tr, feats_d = matrix_to_xy(train_rows, "went_distance")
    Xd_va, yd_va, _ = matrix_to_xy(val_rows, "went_distance", feature_names=feats_d)
    Xd_te, yd_te, _ = matrix_to_xy(test_rows, "went_distance", feature_names=feats_d)

    dist_result = train_distance(Xd_tr, yd_tr, Xd_va, yd_va, Xd_te, yd_te,
                                 feats_d, n_configs=20, seed=seed)
    print(f"  DIST test: {dist_result['test_metrics']}")
    print(f"  DIST baseline (majority class): {dist_result['baseline_acc']:.3f}")
    print(f"  DIST beats baseline: {dist_result['beats_baseline']}")

    if dist_result["beats_baseline"] and dist_result["test_metrics"]["accuracy"] >= 0.55:
        save_v2_model(
            dist_result["booster"], "distance", feats_d,
            meta_payload={
                "hyperparams": dist_result["config"],
                "best_iter": dist_result["best_iter"],
                "n_train": len(Xd_tr), "n_val": len(Xd_va), "n_test": len(Xd_te),
                "test_metrics": dist_result["test_metrics"],
                "baseline_acc": dist_result["baseline_acc"],
                "split_boundaries": {"train_end": TRAIN_END, "val_end": VAL_END},
            },
        )
        dist_verdict = "KEEP"
    else:
        print("  DIST verdict: DROP — recommend removing distance-line picks from product.")
        dist_verdict = "DROP"

    # -----------------------------
    # Smoke test — load saved v2 winner + method, score one held-out fight
    # -----------------------------
    print("\n=== Smoke test: load v2 artifacts ===")
    import xgboost as xgb
    smoke = {}
    try:
        bst_w = xgb.Booster()
        bst_w.load_model(os.path.join(MODELS_DIR, "ufc_v2_winner.json"))
        with open(os.path.join(MODELS_DIR, "ufc_v2_winner.meta.json")) as f:
            meta_w = json.load(f)
        one_x = X_te[:1]
        dm = xgb.DMatrix(one_x, feature_names=meta_w["feature_names"])
        p = float(bst_w.predict(dm)[0])
        print(f"  winner smoke prob: {p:.4f} (label={int(y_te[0])})")
        smoke["winner_prob"] = p
        smoke["winner_true"] = int(y_te[0])
    except Exception as e:
        print(f"  winner smoke FAILED: {e}")
        smoke["winner_error"] = str(e)

    try:
        bst_m = xgb.Booster()
        bst_m.load_model(os.path.join(MODELS_DIR, "ufc_v2_method.json"))
        with open(os.path.join(MODELS_DIR, "ufc_v2_method.meta.json")) as f:
            meta_m = json.load(f)
        one_x = Xm_te[:1]
        dm = xgb.DMatrix(one_x, feature_names=meta_m["feature_names"])
        probs = bst_m.predict(dm)[0].tolist()
        print(f"  method smoke probs: KO={probs[0]:.3f} SUB={probs[1]:.3f} DEC={probs[2]:.3f} "
              f"(label={int(ym_te[0])})")
        smoke["method_probs"] = probs
        smoke["method_true"] = int(ym_te[0])
    except Exception as e:
        print(f"  method smoke FAILED: {e}")
        smoke["method_error"] = str(e)

    # -----------------------------
    # Return everything for the report writer
    # -----------------------------
    return {
        "split_counts_fights": {
            "train": len(train_fights),
            "val": len(val_fights),
            "test": len(test_fights),
        },
        "split_counts_rows": {
            "train": len(train_rows), "val": len(val_rows), "test": len(test_rows),
        },
        "split_boundaries": {"train_end": TRAIN_END, "val_end": VAL_END},
        "winner": {
            "config": winner_result["config"],
            "best_iter": winner_result["best_iter"],
            "train_metrics": winner_result["train_metrics"],
            "val_metrics": winner_result["val_metrics"],
            "test_metrics": winner_result["test_metrics"],
            "calibration_bins_test": winner_calib,
            "feature_importance_top15": [{"name": n, "gain": g} for n, g in winner_imp],
        },
        "method": {
            "config": method_result["config"],
            "best_iter": method_result["best_iter"],
            "train_counts": tr_counts.tolist(),
            "test_counts": te_counts.tolist(),
            "train_metrics": method_result["train_metrics"],
            "val_metrics": method_result["val_metrics"],
            "test_metrics": method_result["test_metrics"],
            "test_per_class": method_result["test_per_class"],
            "test_predicted_class_counts": method_result["test_predicted_class_counts"],
            "heavier_sub_weight_used": method_result["heavier"],
        },
        "distance": {
            "verdict": dist_verdict,
            "test_metrics": dist_result["test_metrics"],
            "baseline_acc": dist_result["baseline_acc"],
        },
        "smoke_test": smoke,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dump", type=str, default=None,
                   help="If set, dump the full result dict as JSON here.")
    args = p.parse_args()
    result = run(seed=args.seed)
    if args.dump:
        with open(args.dump, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nResults dumped to {args.dump}")
    print("\nDone.")
