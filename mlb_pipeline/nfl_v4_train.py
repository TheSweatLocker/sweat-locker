"""NFL V4 XGBoost trainer (2026-08-13).

Trains two XGBoost models — spread predictor + total predictor — on
2020-2024 nflverse historical data. Saves to models/nfl_v4_spread.pkl and
models/nfl_v4_total.pkl. Inference script (nfl_v4_inference.py) loads
these and runs per-game predictions.

Training data: nfl_data_py pull of every regular-season + playoff game
2020-2024 with:
  * Weekly team EPA (offense + defense, split pass/rush)
  * Roster / injury metadata (QB starter, key OL/DL out flags)
  * Schedule metadata (rest days, division game, roof, weather, surface)
  * Actual outcome (home_score, away_score) as target

Feature set (MVP v1 — factors we can grab clean from nfl_data_py):
  1.  home_off_epa_l4 / away_off_epa_l4       (last-4-game rolling)
  2.  home_def_epa_l4 / away_def_epa_l4
  3.  home_pass_epa_l4 / away_pass_epa_l4
  4.  home_rush_epa_l4 / away_rush_epa_l4
  5.  home_rest / away_rest                    (rest days)
  6.  home_short_week (bool) / away_short_week
  7.  home_bye (bool) / away_bye
  8.  div_game (bool)
  9.  wind / temp                              (0 for domes)
 10.  is_dome (bool)
 11.  is_playoff (bool)
 12.  week                                     (season progression)
 13.  home_qb_starter_id / away_qb_starter_id  (categorical, hash-encoded)
 14.  season                                   (regime feature)

Two separate models:
  * Target for spread model: home_score - away_score
  * Target for total model:  home_score + away_score

Deferred to V5 (post-launch):
  * QB × opponent historical (needs longer history + join engineering)
  * Coach × coach history
  * Ref crew tendencies (need scrape)
  * Stadium-specific HFA (needs per-stadium home-record backfill)
  * Rivalry beyond division

Requires: nfl_data_py, xgboost, pandas, numpy, scikit-learn (already in
pipeline requirements per workflow yml).

CLI:
    python nfl_v4_train.py [--seasons 2020,2021,2022,2023,2024]
                            [--out-dir models] [--dry-run]

Run periodically (~once per year post-season) to refit with fresh data.
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def load_training_data(seasons: list) -> 'pd.DataFrame':
    """Pull nfl_data_py schedules + weekly team stats, engineer features,
    return one row per game with feature vector + target."""
    import pandas as pd
    import numpy as np
    import nfl_data_py as nfl

    print(f'=== loading nfl_data_py for seasons {seasons} ===')

    # 1. Schedules give us matchup + result + rest + roof + weather
    schedules = nfl.import_schedules(seasons)
    print(f'  loaded {len(schedules)} schedule rows')

    # Keep only completed games with scores
    schedules = schedules[schedules['home_score'].notna() & schedules['away_score'].notna()].copy()
    print(f'  {len(schedules)} completed games with scores')

    # 2. Weekly stats — team-level offense + defense EPA
    # Pull weekly EPA totals per team; we'll compute L4 rolling per (team, season, week)
    # nfl_data_py's import_weekly_data returns player-week rows; team_level is what we want.
    # Simpler: use pbp aggregated. Fall back to schedule-only features if too heavy.
    try:
        pbp = nfl.import_pbp_data(seasons, downcast=True)
        # Aggregate per (season, week, posteam) → offense EPA
        off_epa = (pbp.groupby(['season','week','posteam'])
                       .agg(off_epa=('epa','mean'),
                            pass_epa=('pass_epa','mean'),
                            rush_epa=('rush_epa','mean'))
                       .reset_index())
        # Rolling L4 per team over season
        off_epa = off_epa.sort_values(['posteam','season','week'])
        for col in ('off_epa','pass_epa','rush_epa'):
            off_epa[f'{col}_l4'] = (off_epa.groupby(['posteam','season'])[col]
                                          .transform(lambda s: s.rolling(4, min_periods=1).mean()))
        # Defense EPA: what team allowed while on defense
        def_epa = (pbp.groupby(['season','week','defteam'])
                       .agg(def_epa=('epa','mean'))
                       .reset_index())
        def_epa = def_epa.sort_values(['defteam','season','week'])
        def_epa['def_epa_l4'] = (def_epa.groupby(['defteam','season'])['def_epa']
                                        .transform(lambda s: s.rolling(4, min_periods=1).mean()))
        print(f'  computed L4 EPA per team-week (off: {len(off_epa)} rows · def: {len(def_epa)} rows)')
    except Exception as e:
        print(f'  ⚠ pbp aggregation failed: {e} — falling back to schedule-only features')
        off_epa = pd.DataFrame(); def_epa = pd.DataFrame()

    # 3. Merge features into schedules
    schedules['home_short_week'] = (schedules['home_rest'] <= 4).astype(int)
    schedules['away_short_week'] = (schedules['away_rest'] <= 4).astype(int)
    schedules['home_bye'] = (schedules['home_rest'] >= 7).astype(int)
    schedules['away_bye'] = (schedules['away_rest'] >= 7).astype(int)
    schedules['is_playoff'] = (schedules['game_type'] != 'REG').astype(int) if 'game_type' in schedules.columns else 0
    schedules['is_dome'] = schedules['roof'].isin(['dome','closed']).astype(int) if 'roof' in schedules.columns else 0
    # NaN → 0 for numeric fields we're about to model
    for col in ('wind','temp','home_rest','away_rest'):
        if col in schedules.columns:
            schedules[col] = schedules[col].fillna(0)

    if len(off_epa) > 0:
        # Merge home + away team EPA at (season, week)
        schedules = schedules.merge(
            off_epa[['season','week','posteam','off_epa_l4','pass_epa_l4','rush_epa_l4']]
                .rename(columns={'posteam':'home_team',
                                 'off_epa_l4':'home_off_epa_l4',
                                 'pass_epa_l4':'home_pass_epa_l4',
                                 'rush_epa_l4':'home_rush_epa_l4'}),
            on=['season','week','home_team'], how='left')
        schedules = schedules.merge(
            off_epa[['season','week','posteam','off_epa_l4','pass_epa_l4','rush_epa_l4']]
                .rename(columns={'posteam':'away_team',
                                 'off_epa_l4':'away_off_epa_l4',
                                 'pass_epa_l4':'away_pass_epa_l4',
                                 'rush_epa_l4':'away_rush_epa_l4'}),
            on=['season','week','away_team'], how='left')
        schedules = schedules.merge(
            def_epa[['season','week','defteam','def_epa_l4']]
                .rename(columns={'defteam':'home_team','def_epa_l4':'home_def_epa_l4'}),
            on=['season','week','home_team'], how='left')
        schedules = schedules.merge(
            def_epa[['season','week','defteam','def_epa_l4']]
                .rename(columns={'defteam':'away_team','def_epa_l4':'away_def_epa_l4'}),
            on=['season','week','away_team'], how='left')

    # 4. Targets
    schedules['spread_actual'] = schedules['home_score'] - schedules['away_score']
    schedules['total_actual']  = schedules['home_score'] + schedules['away_score']

    return schedules


FEATURE_COLS = [
    'home_off_epa_l4','away_off_epa_l4',
    'home_def_epa_l4','away_def_epa_l4',
    'home_pass_epa_l4','away_pass_epa_l4',
    'home_rush_epa_l4','away_rush_epa_l4',
    'home_rest','away_rest',
    'home_short_week','away_short_week',
    'home_bye','away_bye',
    'div_game','wind','temp','is_dome','is_playoff','week','season',
]


def train_and_save(df, out_dir: Path, dry_run: bool = False) -> dict:
    """Train two XGBRegressor models (spread, total), save .pkl each."""
    import numpy as np
    import pandas as pd
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_absolute_error, root_mean_squared_error
    import xgboost as xgb
    import pickle

    # Drop rows where any critical feature is NaN
    features_available = [c for c in FEATURE_COLS if c in df.columns]
    missing = [c for c in FEATURE_COLS if c not in df.columns]
    if missing: print(f'  ⚠ missing features (will be skipped): {missing}')
    df = df.dropna(subset=features_available + ['spread_actual','total_actual']).copy()
    df['div_game'] = df.get('div_game', 0).fillna(0).astype(int) if 'div_game' in df.columns else 0
    print(f'  training on {len(df)} games ({len(features_available)} features)')

    X = df[features_available].values
    y_spread = df['spread_actual'].values
    y_total  = df['total_actual'].values

    # Split by season — hold out most recent for validation
    max_season = int(df['season'].max())
    train_mask = df['season'] < max_season
    val_mask   = df['season'] == max_season
    if train_mask.sum() < 200 or val_mask.sum() < 50:
        # Fall back to random split
        idx_train, idx_val = train_test_split(np.arange(len(df)), test_size=0.2, random_state=42)
        X_train, X_val = X[idx_train], X[idx_val]
        y_spr_train, y_spr_val = y_spread[idx_train], y_spread[idx_val]
        y_tot_train, y_tot_val = y_total[idx_train], y_total[idx_val]
        val_note = 'random 80/20 split (thin per-season data)'
    else:
        X_train, X_val = X[train_mask], X[val_mask]
        y_spr_train, y_spr_val = y_spread[train_mask], y_spread[val_mask]
        y_tot_train, y_tot_val = y_total[train_mask], y_total[val_mask]
        val_note = f'holdout season {max_season} ({val_mask.sum()} games)'
    print(f'  validation: {val_note}')

    metrics = {}
    saved = {}
    for target_name, y_train, y_val in (
        ('spread', y_spr_train, y_spr_val),
        ('total',  y_tot_train, y_tot_val),
    ):
        model = xgb.XGBRegressor(
            n_estimators=500, max_depth=5, learning_rate=0.05,
            subsample=0.85, colsample_bytree=0.85,
            reg_alpha=0.5, reg_lambda=1.0,
            random_state=42, n_jobs=-1,
            early_stopping_rounds=25,
        )
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
        preds = model.predict(X_val)
        mae  = float(mean_absolute_error(y_val, preds))
        rmse = float(root_mean_squared_error(y_val, preds))
        metrics[target_name] = {'mae': round(mae, 2), 'rmse': round(rmse, 2), 'n_val': int(len(y_val))}
        print(f'  {target_name}: MAE {mae:.2f} · RMSE {rmse:.2f} · n_val {len(y_val)}')
        if dry_run: continue
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f'nfl_v4_{target_name}.pkl'
        with open(path, 'wb') as f:
            pickle.dump({'model': model, 'feature_cols': features_available,
                         'trained_at': datetime.now(timezone.utc).isoformat(),
                         'n_train': int(len(y_train)), 'metrics': metrics[target_name]}, f)
        saved[target_name] = str(path)
        print(f'  saved → {path}')

    return {'metrics': metrics, 'saved': saved, 'validation': val_note}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seasons', default='2020,2021,2022,2023,2024',
        help='Comma-separated list of seasons to train on')
    p.add_argument('--out-dir', default='models',
        help='Directory to save .pkl files (relative to this script)')
    p.add_argument('--dry-run', action='store_true',
        help='Train + report metrics, but do not save .pkl')
    args = p.parse_args()

    seasons = [int(s.strip()) for s in args.seasons.split(',')]
    out_dir = Path(__file__).parent / args.out_dir

    df = load_training_data(seasons)
    result = train_and_save(df, out_dir, dry_run=args.dry_run)
    print(f'\n=== summary ===')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
