"""
XGBoost training scaffolding for MLB prediction models.

Trains three separate models on pipeline data:
1. NRFI model — predicts NRFI/YRFI from pitcher + game context
2. Total runs model — predicts total runs scored
3. Home win model — predicts home team win probability

Usage:
  python mlb_pipeline/train_xgboost.py              # Train all 3 models
  python mlb_pipeline/train_xgboost.py --target nrfi  # Just NRFI
  python mlb_pipeline/train_xgboost.py --audit       # Show data volume + feature coverage

Models are saved to mlb_pipeline/models/ and can be loaded for predictions
by predict_xgboost.py during the daily pipeline run.
"""
import os
import argparse
import json
import pickle
from datetime import datetime
import requests
from dotenv import load_dotenv

# Lazy imports — only import heavy libs when actually training
load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
os.makedirs(MODELS_DIR, exist_ok=True)

# Features to use for each model target
# Each feature must exist in mlb_game_results and be a number (or bool)
NUMERIC_FEATURES = [
    # Pitcher quality
    'home_sp_xera', 'away_sp_xera',
    'home_sp_k_pct', 'away_sp_k_pct',
    'home_sp_gb_pct', 'away_sp_gb_pct',
    'home_sp_whiff_rate', 'away_sp_whiff_rate',
    'home_sp_days_rest', 'away_sp_days_rest',
    'home_last_pitch_count', 'away_last_pitch_count',
    'home_pitcher_vs_team_era', 'away_pitcher_vs_team_era',
    # First inning splits (for NRFI)
    'home_first_inning_era', 'away_first_inning_era',
    'home_first_inning_whip', 'away_first_inning_whip',
    # Offensive quality
    'home_runs_per_game', 'away_runs_per_game',
    'home_wrc_plus', 'away_wrc_plus',
    'home_woba', 'away_woba',
    'home_ops', 'away_ops',
    'home_team_k_pct', 'away_team_k_pct',
    'home_k_gap', 'away_k_gap',
    'home_platoon_advantage', 'away_platoon_advantage',
    'home_lineup_weight', 'away_lineup_weight',
    'home_lineup_ops', 'away_lineup_ops',
    # Bullpen
    'home_bullpen_era', 'away_bullpen_era',
    'home_bp_relievers_3d', 'away_bp_relievers_3d',
    # Situational
    'home_last5_run_diff', 'away_last5_run_diff',
    'home_injury_count', 'away_injury_count',
    'home_travel_distance_last_game',
    'away_consecutive_road_games',
    'days_since_last_home_game',
    # Game context
    'park_run_factor', 'temperature', 'wind_mph',
    'nrfi_score', 'projected_total', 'projected_spread',
    'open_total', 'close_total', 'open_spread', 'close_spread',
]

CATEGORICAL_FEATURES = [
    'home_sp_hand', 'away_sp_hand',
    'wind_direction', 'wind_blowing_in', 'is_dome',
    'timezone_change',
]


def fetch_training_data(min_season=2025):
    """Pull all game results with scores from Supabase (paginated)"""
    print(f'Fetching training data (season >= {min_season})...')
    all_games = []
    offset = 0
    while True:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results'
            f'?season=gte.{min_season}&home_score=not.is.null'
            f'&select=*&limit=1000&offset={offset}',
            headers=HEADERS
        )
        batch = r.json()
        if not batch or not isinstance(batch, list):
            break
        all_games.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    print(f'  Loaded {len(all_games)} games')
    return all_games


def audit_data(games):
    """Show feature coverage — how many rows have each feature populated"""
    print(f'\n=== DATA AUDIT ({len(games)} games) ===\n')

    # Season breakdown
    seasons = {}
    for g in games:
        s = g.get('season', 'unknown')
        seasons[s] = seasons.get(s, 0) + 1
    print('Games by season:')
    for s in sorted(seasons.keys(), key=str):
        print(f'  {s}: {seasons[s]}')

    # Feature coverage
    print(f'\nFeature coverage (% of rows with non-null value):')
    all_features = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    coverage = {}
    for f in all_features:
        populated = sum(1 for g in games if g.get(f) is not None)
        coverage[f] = populated / len(games) * 100 if games else 0

    # Sort by coverage, show high/low
    sorted_features = sorted(coverage.items(), key=lambda x: -x[1])
    print(f'\n  High coverage (>80%):')
    for f, pct in sorted_features:
        if pct >= 80:
            print(f'    {pct:>5.1f}%  {f}')
    print(f'\n  Medium coverage (30-80%):')
    for f, pct in sorted_features:
        if 30 <= pct < 80:
            print(f'    {pct:>5.1f}%  {f}')
    print(f'\n  Low coverage (<30%) — may not be worth using yet:')
    for f, pct in sorted_features:
        if pct < 30:
            print(f'    {pct:>5.1f}%  {f}')

    # Target coverage
    print(f'\nTarget variable coverage:')
    nrfi_count = sum(1 for g in games if g.get('nrfi_result'))
    total_count = sum(1 for g in games if g.get('home_score') is not None and g.get('away_score') is not None)
    win_count = sum(1 for g in games if g.get('home_win') is not None)
    print(f'  NRFI result:  {nrfi_count}/{len(games)} ({nrfi_count/len(games)*100:.1f}%)')
    print(f'  Total runs:   {total_count}/{len(games)} ({total_count/len(games)*100:.1f}%)')
    print(f'  Home win:     {win_count}/{len(games)} ({win_count/len(games)*100:.1f}%)')


def build_feature_matrix(games, min_coverage=50):
    """
    Convert games to feature matrix. Only use features with >= min_coverage% populated.
    Returns (X, feature_names) with null-imputed values.
    """
    import numpy as np

    # Determine which features to include based on coverage
    usable_features = []
    for f in NUMERIC_FEATURES:
        populated = sum(1 for g in games if g.get(f) is not None)
        pct = populated / len(games) * 100
        if pct >= min_coverage:
            usable_features.append(f)

    print(f'  Using {len(usable_features)} features (>= {min_coverage}% coverage)')

    # Build matrix — impute missing with column median
    rows = []
    for g in games:
        row = []
        for f in usable_features:
            v = g.get(f)
            try:
                row.append(float(v) if v is not None else None)
            except:
                row.append(None)
        rows.append(row)

    X = np.array(rows, dtype=object)
    # Impute column medians for nulls
    for col_idx in range(X.shape[1]):
        col_vals = [v for v in X[:, col_idx] if v is not None]
        median = np.median(col_vals) if col_vals else 0
        for row_idx in range(X.shape[0]):
            if X[row_idx, col_idx] is None:
                X[row_idx, col_idx] = median
    return X.astype(float), usable_features


def train_nrfi_model(games):
    """Binary classification: NRFI vs YRFI"""
    import xgboost as xgb
    import numpy as np
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, roc_auc_score, log_loss

    print('\n=== TRAINING NRFI MODEL ===')
    labeled = [g for g in games if g.get('nrfi_result') in ('NRFI', 'YRFI')]
    print(f'Labeled games: {len(labeled)}')
    if len(labeled) < 100:
        print(f'⚠️  Not enough data to train (need 100+, have {len(labeled)}). Aborting.')
        return None

    X, features = build_feature_matrix(labeled)
    y = np.array([1 if g['nrfi_result'] == 'NRFI' else 0 for g in labeled])

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    print(f'Train: {len(X_train)} | Test: {len(X_test)}')

    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=3,
        objective='binary:logistic',
        eval_metric='logloss',
        random_state=42,
    )
    model.fit(X_train, y_train)

    # Evaluate
    preds = model.predict(X_test)
    probs = model.predict_proba(X_test)[:, 1]
    acc = accuracy_score(y_test, preds)
    auc = roc_auc_score(y_test, probs)
    ll = log_loss(y_test, probs)
    print(f'Accuracy: {acc:.3f} | AUC: {auc:.3f} | LogLoss: {ll:.3f}')

    # Performance by confidence tier
    print('\nPerformance by prediction confidence:')
    for thresh in [0.5, 0.6, 0.7, 0.8]:
        high_conf_mask = (probs >= thresh) | (probs <= 1 - thresh)
        if high_conf_mask.sum() > 0:
            hc_preds = preds[high_conf_mask]
            hc_true = y_test[high_conf_mask]
            hc_acc = accuracy_score(hc_true, hc_preds)
            print(f'  Threshold {thresh}: {high_conf_mask.sum()} games, {hc_acc:.3f} accuracy')

    # Feature importance
    print('\nTop 10 features:')
    importances = sorted(zip(features, model.feature_importances_), key=lambda x: -x[1])
    for f, imp in importances[:10]:
        print(f'  {imp:.4f}  {f}')

    # Save
    model_path = os.path.join(MODELS_DIR, 'nrfi_model.pkl')
    with open(model_path, 'wb') as f:
        pickle.dump({'model': model, 'features': features, 'trained_at': datetime.now().isoformat(),
                     'accuracy': acc, 'auc': auc, 'n_train': len(X_train), 'n_test': len(X_test)}, f)
    print(f'\n✅ Saved to {model_path}')
    return model


def train_total_model(games):
    """Regression: predict total runs scored"""
    import xgboost as xgb
    import numpy as np
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_absolute_error, mean_squared_error

    print('\n=== TRAINING TOTAL RUNS MODEL ===')
    labeled = [g for g in games if g.get('home_score') is not None and g.get('away_score') is not None]
    print(f'Labeled games: {len(labeled)}')
    if len(labeled) < 100:
        print(f'⚠️  Not enough data (need 100+, have {len(labeled)}). Aborting.')
        return None

    X, features = build_feature_matrix(labeled)
    y = np.array([g['home_score'] + g['away_score'] for g in labeled])

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    print(f'Train: {len(X_train)} | Test: {len(X_test)}')

    model = xgb.XGBRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
        objective='reg:squarederror', random_state=42,
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    rmse = mean_squared_error(y_test, preds) ** 0.5
    print(f'MAE: {mae:.2f} runs | RMSE: {rmse:.2f} runs')

    # How does it do on over/under?
    # Use close_total as the line, check directional accuracy
    test_indices = list(range(len(y_test)))
    correct_ou = 0
    total_ou = 0
    for i, pred in enumerate(preds):
        idx = X_test[i]  # not useful — we need to map back
        # Skip — requires more setup
    # (Would need to track game metadata through the split to do proper O/U eval)

    print('\nTop 10 features:')
    importances = sorted(zip(features, model.feature_importances_), key=lambda x: -x[1])
    for f, imp in importances[:10]:
        print(f'  {imp:.4f}  {f}')

    model_path = os.path.join(MODELS_DIR, 'total_model.pkl')
    with open(model_path, 'wb') as f:
        pickle.dump({'model': model, 'features': features, 'trained_at': datetime.now().isoformat(),
                     'mae': mae, 'rmse': rmse, 'n_train': len(X_train), 'n_test': len(X_test)}, f)
    print(f'\n✅ Saved to {model_path}')
    return model


def train_home_win_model(games):
    """Binary classification: home team wins"""
    import xgboost as xgb
    import numpy as np
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, roc_auc_score

    print('\n=== TRAINING HOME WIN MODEL ===')
    labeled = [g for g in games if g.get('home_win') is not None]
    print(f'Labeled games: {len(labeled)}')
    if len(labeled) < 100:
        print(f'⚠️  Not enough data (need 100+, have {len(labeled)}). Aborting.')
        return None

    X, features = build_feature_matrix(labeled)
    y = np.array([1 if g['home_win'] else 0 for g in labeled])

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    print(f'Train: {len(X_train)} | Test: {len(X_test)}')

    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
        objective='binary:logistic', eval_metric='logloss', random_state=42,
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    probs = model.predict_proba(X_test)[:, 1]
    acc = accuracy_score(y_test, preds)
    auc = roc_auc_score(y_test, probs)
    print(f'Accuracy: {acc:.3f} | AUC: {auc:.3f}')

    print('\nPerformance by confidence tier:')
    for thresh in [0.55, 0.6, 0.65, 0.7]:
        high_conf_mask = (probs >= thresh) | (probs <= 1 - thresh)
        if high_conf_mask.sum() > 0:
            hc_acc = accuracy_score(y_test[high_conf_mask], preds[high_conf_mask])
            print(f'  Threshold {thresh}: {high_conf_mask.sum()} games, {hc_acc:.3f} accuracy')

    print('\nTop 10 features:')
    importances = sorted(zip(features, model.feature_importances_), key=lambda x: -x[1])
    for f, imp in importances[:10]:
        print(f'  {imp:.4f}  {f}')

    model_path = os.path.join(MODELS_DIR, 'home_win_model.pkl')
    with open(model_path, 'wb') as f:
        pickle.dump({'model': model, 'features': features, 'trained_at': datetime.now().isoformat(),
                     'accuracy': acc, 'auc': auc, 'n_train': len(X_train), 'n_test': len(X_test)}, f)
    print(f'\n✅ Saved to {model_path}')
    return model


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=['nrfi', 'total', 'win', 'all'], default='all')
    parser.add_argument('--audit', action='store_true', help='Just show data audit, no training')
    parser.add_argument('--min-season', type=int, default=2025, help='Minimum season to include')
    args = parser.parse_args()

    games = fetch_training_data(min_season=args.min_season)

    if args.audit:
        audit_data(games)
    else:
        if args.target in ('nrfi', 'all'):
            train_nrfi_model(games)
        if args.target in ('total', 'all'):
            train_total_model(games)
        if args.target in ('win', 'all'):
            train_home_win_model(games)
