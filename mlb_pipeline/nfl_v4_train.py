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

        # ══ 2026-10-14 · THIS IS WHY v4 HAS BEEN A CONSTANT SINCE 08-14 ══
        # nflverse no longer ships `pass_epa` / `rush_epa` as pbp columns.
        # The .agg() below named them, raised KeyError, and the `except` at the
        # bottom of this block swallowed it and fell back to schedule-only
        # features. Training then "succeeded" and saved a spread model whose
        # feature_cols are 13 schedule/weather fields with NO team identity —
        # so v4 could only ever emit league-average HFA. Measured on the rows
        # it is still writing: sd 0.272, 6 distinct values, ZERO negative,
        # v4_confidence 0.311 on every row.
        #
        # nfl_v4_inference.py:121-131 already derives these two from
        # `play_type`. The trainer never got the same guard, so the two sides
        # disagreed about what a feature vector contains and the model silently
        # won.
        if 'pass_epa' not in pbp.columns or 'rush_epa' not in pbp.columns:
            if 'play_type' in pbp.columns:
                pbp['pass_epa'] = pbp['epa'].where(pbp['play_type'] == 'pass')
                pbp['rush_epa'] = pbp['epa'].where(pbp['play_type'] == 'run')
                print('  derived pass_epa / rush_epa from play_type '
                      '(nflverse no longer ships them)')
            else:
                pbp['pass_epa'] = pbp['epa']
                pbp['rush_epa'] = pbp['epa']
                print('  ⚠ no play_type either — pass/rush EPA both set to '
                      'overall EPA')

        # Aggregate per (season, week, posteam) → offense EPA
        off_epa = (pbp.groupby(['season','week','posteam'])
                       .agg(off_epa=('epa','mean'),
                            pass_epa=('pass_epa','mean'),
                            rush_epa=('rush_epa','mean'))
                       .reset_index())
        # Rolling L4 per team over season
        off_epa = off_epa.sort_values(['posteam','season','week'])
        for col in ('off_epa','pass_epa','rush_epa'):
            # ══ 2026-10-14 · .shift(1) IS THE WHOLE BALLGAME ══
            # Without it, rolling(4) INCLUDES the current week, so week W's
            # `off_epa_l4` carries week W's own EPA — the result of the very
            # game being predicted. First retrain after fixing the
            # feature-blindness scored spread MAE 7.48 against a market that
            # manages about 10 on NFL margin, which is not a breakthrough, it
            # is the outcome leaking into the features.
            #
            # It is also a train/serve skew: at inference the game has not
            # been played, so build_current_epa() can only ever supply
            # strictly-earlier weeks. Training on same-game EPA teaches the
            # model to lean on a feature that is never there live.
            off_epa[f'{col}_l4'] = (off_epa.groupby(['posteam','season'])[col]
                                          .transform(lambda s: s.shift(1)
                                                     .rolling(4, min_periods=1)
                                                     .mean()))
        # Defense EPA: what team allowed while on defense
        def_epa = (pbp.groupby(['season','week','defteam'])
                       .agg(def_epa=('epa','mean'))
                       .reset_index())
        def_epa = def_epa.sort_values(['defteam','season','week'])
        # Same shift on defence, same reason.
        def_epa['def_epa_l4'] = (def_epa.groupby(['defteam','season'])['def_epa']
                                        .transform(lambda s: s.shift(1)
                                                   .rolling(4, min_periods=1)
                                                   .mean()))
        print(f'  computed L4 EPA per team-week (off: {len(off_epa)} rows · def: {len(def_epa)} rows)')
    except Exception as e:
        # ══ 2026-10-14 · NO MORE SILENT FALLBACK ══
        # This except used to set off_epa/def_epa empty and carry on, which is
        # how a spread model with no team identity got trained, saved and
        # shipped. A model that cannot know who is playing is not a degraded
        # model, it is a different and useless one, so failing to build team
        # features is now fatal. Fix the cause or pass --allow-schedule-only
        # deliberately, which also renames the output so it can never be
        # mistaken for the real thing.
        import traceback
        traceback.print_exc(limit=4)
        raise RuntimeError(
            f'team EPA features could not be built ({type(e).__name__}: {e}). '
            f'Refusing to train a feature-blind v4 — that is exactly how the '
            f'2026-08-14 model shipped as a constant. Pass '
            f'--allow-schedule-only only if you really want a schedule-only '
            f'baseline.') from e

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

#: The features that let the model know WHO IS PLAYING. Without at least one
#: of these, v4's remaining inputs are rest, weather, week and season, which
#: carry exactly one signal — league-average home-field advantage — and the
#: model collapses to a constant. The 2026-08-14 model shipped with none of
#: them and has been emitting +1.5-always-HOME ever since, so their presence is
#: now checked before anything is written to disk.
TEAM_FEATURES = [
    'home_off_epa_l4', 'away_off_epa_l4',
    'home_def_epa_l4', 'away_def_epa_l4',
    'home_pass_epa_l4', 'away_pass_epa_l4',
    'home_rush_epa_l4', 'away_rush_epa_l4',
]


def train_and_save(df, out_dir: Path, dry_run: bool = False,
                   allow_schedule_only: bool = False) -> dict:
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

    # ══ 2026-10-14 · A FEATURE-BLIND v4 MUST NOT REACH DISK ══
    # This is the check whose absence let the 08-14 model ship. `missing` was
    # printed as a warning, the 8 team-EPA features were dropped, and a model
    # of rest+weather+week+season was saved under the name of a spread model.
    team_have = [c for c in TEAM_FEATURES if c in features_available]
    if not team_have:
        if not allow_schedule_only:
            raise RuntimeError(
                'NONE of the team-strength features survived: '
                f'{TEAM_FEATURES}. Only {features_available} are available, '
                'which carry no team identity — the model would collapse to '
                'league-average HFA, exactly as the 2026-08-14 model did '
                '(sd 0.272, 6 distinct values, 0 negative). Refusing to save. '
                'Pass --allow-schedule-only to build a deliberate '
                'schedule-only baseline instead; it is written under a '
                'different name so it can never be loaded as v4.')
        print('  ⚠⚠ SCHEDULE-ONLY baseline requested — no team features. '
              'Saving under *_schedule_only.pkl, NOT as v4.')
    else:
        print(f'  team-strength features present: {len(team_have)}/'
              f'{len(TEAM_FEATURES)} → {team_have}')
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
        # A schedule-only baseline is written under a DIFFERENT name so
        # nfl_v4_inference.py can never load it as v4.
        suffix = '_schedule_only' if not team_have else ''
        path = out_dir / f'nfl_v4_{target_name}{suffix}.pkl'
        with open(path, 'wb') as f:
            pickle.dump({'model': model, 'feature_cols': features_available,
                         'trained_at': datetime.now(timezone.utc).isoformat(),
                         'n_train': int(len(y_train)),
                         'metrics': metrics[target_name],
                         # so a feature-blind model can be spotted
                         # without re-deriving it from feature_cols
                         'team_features': team_have,
                         'schedule_only': not team_have}, f)
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
    p.add_argument('--allow-schedule-only', action='store_true',
        help='Deliberately build a schedule-only baseline with NO team '
             'features. Saves as nfl_v4_*_schedule_only.pkl so it can never '
             'be loaded as v4. Without this flag, missing team features is '
             'a hard failure.')
    args = p.parse_args()

    seasons = [int(s.strip()) for s in args.seasons.split(',')]
    out_dir = Path(__file__).parent / args.out_dir

    df = load_training_data(seasons)
    result = train_and_save(df, out_dir, dry_run=args.dry_run,
                            allow_schedule_only=args.allow_schedule_only)
    print(f'\n=== summary ===')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
