"""UFC v1 model training.

Iterates ufc_fight_results, builds feature vectors via ufc_features.py,
trains four XGBoost classifiers, persists models to disk.

Models:
  ufc_v1_winner.json    — binary (P fighter_a wins)
  ufc_v1_method.json    — multinomial (KO/TKO=0, SUB=1, DEC=2)
  ufc_v1_distance.json  — binary (P went distance)
  ufc_v1_round.json     — multinomial (R1=0, R2=1, R3=2, R4=3, R5=4) for finishes

Usage:
  python ufc_train.py                  # train all 4
  python ufc_train.py --eval-only      # eval saved models on holdout
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ufc_features import build_features, label_for_fight

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(MODELS_DIR, exist_ok=True)


def fetch_all_resolved_fights():
    """Pull every resolved fight with both fighter URLs and a clean outcome."""
    all_fights = []
    offset = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/ufc_fight_results?select=*&limit=1000&offset={offset}",
            headers=HEADERS, timeout=20,
        )
        batch = r.json() if r.status_code == 200 else []
        if not batch or not isinstance(batch, list):
            break
        all_fights.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return all_fights


def _swap_ab(feat_dict):
    """Return a feature vector with fighter_a / fighter_b swapped.

    UFCStats always lists the winner first, so fighter_a in raw data is
    always the winner. To train a usable winner-prediction model we need
    to either randomize a/b at training time or include each fight in
    BOTH orderings. Doing the latter — doubles training data and forces
    the model to learn symmetric features rather than 'a always wins'.
    """
    a_keys = [k for k in feat_dict if k.startswith("a_")]
    b_keys = [k for k in feat_dict if k.startswith("b_")]
    swapped = dict(feat_dict)
    for ak, bk in zip(sorted(a_keys), sorted(b_keys)):
        swapped[ak], swapped[bk] = feat_dict[bk], feat_dict[ak]
    # Diff features flip sign
    for k in ("reach_diff", "height_diff", "age_diff", "slpm_diff", "str_def_diff",
              "td_def_diff", "sub_threat_diff", "finishing_rate_diff",
              "experience_diff", "form_diff"):
        if k in feat_dict:
            swapped[k] = -feat_dict[k]
    # Stance flags swap
    if "stance_southpaw_a" in feat_dict and "stance_southpaw_b" in feat_dict:
        swapped["stance_southpaw_a"] = feat_dict["stance_southpaw_b"]
        swapped["stance_southpaw_b"] = feat_dict["stance_southpaw_a"]
    # stance_mismatch and is_5_round are symmetric — no change
    return swapped


def build_training_matrix(fights, verbose=True):
    """Iterate fights, build feature vector + labels for each. Skip invalid rows.

    Each fight contributes TWO training rows: original (a,b) and swapped
    (b,a) with winner_a label flipped. Forces winner model to be symmetric.
    """
    rows = []
    skipped_no_features = 0
    skipped_no_label = 0
    skipped_bad_data = 0

    for i, fight in enumerate(fights):
        if not fight.get("fighter_a_url") or not fight.get("fighter_b_url"):
            skipped_bad_data += 1
            continue
        if not fight.get("event_date"):
            skipped_bad_data += 1
            continue

        labels = label_for_fight(fight)
        if labels is None:
            skipped_no_label += 1
            continue

        rounds_sched = fight.get("total_rounds_scheduled") or 3
        feat, ok = build_features(
            fight["fighter_a_url"],
            fight["fighter_b_url"],
            fight["event_date"],
            total_rounds_scheduled=rounds_sched,
        )
        if not ok:
            skipped_no_features += 1
            continue

        # Original orientation
        rows.append({**feat, **labels})
        # Swapped orientation — winner flips, method/distance/round same
        swapped_feat = _swap_ab(feat)
        swapped_labels = dict(labels)
        swapped_labels["winner_a"] = 1 - labels["winner_a"]
        rows.append({**swapped_feat, **swapped_labels})

        if verbose and (i + 1) % 100 == 0:
            print(f"  Built {i+1}/{len(fights)} (kept {len(rows)})")

    if verbose:
        print(f"\nTraining rows: {len(rows)}")
        print(f"  Skipped — no features (debut/missing stats): {skipped_no_features}")
        print(f"  Skipped — no label (draw/NC/DQ): {skipped_no_label}")
        print(f"  Skipped — bad data: {skipped_bad_data}")
    return rows


def matrix_to_xy(rows, label_key, drop_label_keys=None):
    """Convert list-of-dicts to (X array, y array, feature_names)."""
    drop_label_keys = drop_label_keys or {"winner_a", "method_class", "went_distance", "finish_round"}
    valid = [r for r in rows if r.get(label_key) is not None]
    if not valid:
        return None, None, None
    feature_names = sorted([k for k in valid[0].keys() if k not in drop_label_keys])
    X = np.array([[r[fn] for fn in feature_names] for r in valid], dtype=float)
    y = np.array([r[label_key] for r in valid])
    return X, y, feature_names


def train_xgboost(X, y, name, objective, num_class=None):
    """Train an XGBoost classifier. Returns (booster, val_metric)."""
    try:
        import xgboost as xgb
    except ImportError:
        print("  ❌ xgboost not installed. Run: pip install xgboost")
        return None, None
    from sklearn.model_selection import train_test_split

    X_tr, X_va, y_tr, y_va = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y if num_class else None)

    params = {
        "objective": objective,
        "eta": 0.05,
        "max_depth": 5,
        "min_child_weight": 3,
        "subsample": 0.85,
        "colsample_bytree": 0.85,
        "verbosity": 0,
    }
    if num_class:
        params["num_class"] = num_class
        params["eval_metric"] = "mlogloss"
    else:
        params["eval_metric"] = "logloss"

    dtrain = xgb.DMatrix(X_tr, label=y_tr)
    dval = xgb.DMatrix(X_va, label=y_va)

    bst = xgb.train(
        params, dtrain, num_boost_round=400,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=30,
        verbose_eval=False,
    )

    # Eval
    pred = bst.predict(dval)
    if num_class:
        pred_class = np.argmax(pred, axis=1)
        acc = (pred_class == y_va).mean()
        print(f"  {name}: train n={len(X_tr)} val n={len(X_va)} | val accuracy {acc:.3f}")
        return bst, {"val_accuracy": float(acc), "n_train": len(X_tr), "n_val": len(X_va)}
    else:
        # Binary — measure auc + accuracy
        pred_class = (pred >= 0.5).astype(int)
        acc = (pred_class == y_va).mean()
        try:
            from sklearn.metrics import roc_auc_score
            auc = roc_auc_score(y_va, pred)
            print(f"  {name}: train n={len(X_tr)} val n={len(X_va)} | val accuracy {acc:.3f} AUC {auc:.3f}")
            return bst, {"val_accuracy": float(acc), "val_auc": float(auc),
                         "n_train": len(X_tr), "n_val": len(X_va)}
        except Exception:
            print(f"  {name}: train n={len(X_tr)} val n={len(X_va)} | val accuracy {acc:.3f}")
            return bst, {"val_accuracy": float(acc), "n_train": len(X_tr), "n_val": len(X_va)}


def save_model(booster, feature_names, name, metrics):
    """Persist model + metadata."""
    model_path = os.path.join(MODELS_DIR, f"ufc_v1_{name}.json")
    booster.save_model(model_path)
    meta = {
        "name": name,
        "feature_names": feature_names,
        "metrics": metrics,
        "trained_at": datetime.utcnow().isoformat(),
    }
    meta_path = os.path.join(MODELS_DIR, f"ufc_v1_{name}.meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  💾 Saved {model_path}")


def run():
    print("Pulling resolved fights...")
    fights = fetch_all_resolved_fights()
    print(f"  Loaded {len(fights)} resolved fights\n")

    print("Building feature matrix...")
    rows = build_training_matrix(fights)
    if not rows:
        print("❌ No training rows produced — check ufc_fighter_history is populated")
        return

    # Model 1: Winner
    print("\n=== Training: Winner (P[fighter_a wins]) ===")
    X, y, feats = matrix_to_xy(rows, "winner_a")
    print(f"  Class balance: a wins {y.mean():.3f}")
    booster, metrics = train_xgboost(X, y, "winner", "binary:logistic")
    if booster:
        save_model(booster, feats, "winner", metrics)

    # Model 2: Method (KO/TKO=0, SUB=1, DEC=2)
    print("\n=== Training: Method of victory ===")
    X, y, feats = matrix_to_xy(rows, "method_class")
    counts = np.bincount(y.astype(int))
    print(f"  Class counts: KO/TKO={counts[0]}, SUB={counts[1] if len(counts)>1 else 0}, DEC={counts[2] if len(counts)>2 else 0}")
    booster, metrics = train_xgboost(X, y, "method", "multi:softprob", num_class=3)
    if booster:
        save_model(booster, feats, "method", metrics)

    # Model 3: Distance (binary — went distance or finished)
    print("\n=== Training: Went distance ===")
    X, y, feats = matrix_to_xy(rows, "went_distance")
    print(f"  Class balance: distance {y.mean():.3f}")
    booster, metrics = train_xgboost(X, y, "distance", "binary:logistic")
    if booster:
        save_model(booster, feats, "distance", metrics)

    # Model 4: Round of finish (R1=0, R2=1, R3=2, R4=3, R5=4) — only for finished fights
    print("\n=== Training: Round of finish (finished fights only) ===")
    X, y, feats = matrix_to_xy(rows, "finish_round")
    if X is not None and len(X) > 50:
        # Map round 1-5 → class 0-4
        y_cls = (y - 1).astype(int)
        counts = np.bincount(y_cls, minlength=5)
        print(f"  Class counts: R1={counts[0]}, R2={counts[1]}, R3={counts[2]}, R4={counts[3]}, R5={counts[4]}")
        booster, metrics = train_xgboost(X, y_cls, "round", "multi:softprob", num_class=5)
        if booster:
            save_model(booster, feats, "round", metrics)
    else:
        print(f"  Skipping — only {len(X) if X is not None else 0} finished fights (need 50+)")

    print("\n✅ Training complete")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--eval-only", action="store_true", help="Eval saved models against holdout")
    args = p.parse_args()
    run()
