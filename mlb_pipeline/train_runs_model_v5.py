"""v5 runs model — recency-weighted retrain on the v4 feature set.

Built 2026-05-24 after audit_v4_totals found v4 has OVER-side calibration
drift (43% 30d hit rate on OVER picks). v4 was trained with the existing
recency-weight pattern but with TWO known issues:

  1. The walk-forward validation used recency weights (last 30% of training
     data weighted 2.5x), but the FINAL saved model trained on ALL data
     WITHOUT sample_weight. Bug — the deployed model doesn't get the
     recency boost the validation showed worked.

  2. Recency was position-based (last 30% of rows), not calendar-based.
     A model trained in May still puts ~equal weight on April + early-May
     samples. With offenses cooling in May, this leaves the model
     systematically over-projecting runs.

v5 fixes both:
  - Calendar-based weighting: last 30 days = 3.0x, last 60 days = 1.5x,
    older = 1.0x. Captures the actual run-environment shift.
  - Weights applied in BOTH walk-forward AND final fit (no more bug).

Feature set unchanged from v4 (no new features yet — historical L14 OPS
data doesn't exist pre-2026-05-23 when enrich_team_recency shipped).
This isolates the test: does pure recency-weight tightening fix the
OVER bias?

Saved as:
  - models/home_runs_model_v5.pkl
  - models/away_runs_model_v5.pkl
  - models/runs_model_meta_v5.json

Does NOT replace v4 yet — game_context.py keeps loading v4 unless
explicitly switched. Compare v5 metrics here, then plumb v5 in a
separate step if it actually outperforms.
"""
import os
import sys
import pickle
import json
import argparse
import numpy as np
from datetime import datetime, timedelta, timezone

# Reuse v4's data loader + feature engineering — same features, same data
from train_runs_model import (
    fetch_data, build_matrix, current_formula_projection, _make_xgb,
    MODELS_DIR,
)


def compute_calendar_recency_weights(games, today_date):
    """Calendar-based weights. Last 30 days = 3.0x, last 60 days = 1.5x,
    older = 1.0x. today_date is a date string 'YYYY-MM-DD'."""
    today = datetime.strptime(today_date, '%Y-%m-%d').date()
    weights = np.ones(len(games), dtype=np.float32)
    for i, g in enumerate(games):
        gd_str = g.get('game_date') or g.get('stats_snapshot_date')
        if not gd_str:
            continue
        try:
            gd = datetime.strptime(gd_str[:10], '%Y-%m-%d').date()
        except ValueError:
            continue
        days_old = (today - gd).days
        if days_old <= 30:
            weights[i] = 3.0
        elif days_old <= 60:
            weights[i] = 1.5
        # else 1.0 (default)
    return weights


def walkforward_v5(games, warmup=200, refit_every=14):
    """Walk-forward training + prediction with calendar-recency weights."""
    X_all, y_home_all, y_away_all, feat_names = build_matrix(games)
    n = len(games)
    print(f'  Feature matrix: {n} games x {X_all.shape[1]} features')
    print(f'  Date range: {games[0]["game_date"]} -> {games[-1]["game_date"]}')

    preds_h_v5, preds_a_v5 = [], []
    preds_h_v4, preds_a_v4 = [], []
    actual_h, actual_a = [], []
    formula_h, formula_a = [], []

    model_h_v5 = model_a_v5 = None
    model_h_v4 = model_a_v4 = None

    for i in range(warmup, n):
        if (i - warmup) % refit_every == 0 or model_h_v5 is None:
            X_train = X_all[:i]
            y_train_h = y_home_all[:i]
            y_train_a = y_away_all[:i]
            today_for_weights = games[i].get('game_date', '2026-05-24')

            # v5: calendar-based weights
            w_v5 = compute_calendar_recency_weights(games[:i], today_for_weights)
            model_h_v5 = _make_xgb()
            model_a_v5 = _make_xgb()
            model_h_v5.fit(X_train, y_train_h, sample_weight=w_v5)
            model_a_v5.fit(X_train, y_train_a, sample_weight=w_v5)

            # v4-style: position-based weights (last 30% = 2.5x)
            w_v4 = np.ones(i, dtype=np.float32)
            w_v4[int(i * 0.7):] = 2.5
            model_h_v4 = _make_xgb()
            model_a_v4 = _make_xgb()
            model_h_v4.fit(X_train, y_train_h, sample_weight=w_v4)
            model_a_v4.fit(X_train, y_train_a, sample_weight=w_v4)

        x_i = X_all[i:i+1]
        preds_h_v5.append(float(model_h_v5.predict(x_i)[0]))
        preds_a_v5.append(float(model_a_v5.predict(x_i)[0]))
        preds_h_v4.append(float(model_h_v4.predict(x_i)[0]))
        preds_a_v4.append(float(model_a_v4.predict(x_i)[0]))
        actual_h.append(float(y_home_all[i]))
        actual_a.append(float(y_away_all[i]))
        fh, fa = current_formula_projection(games[i])
        formula_h.append(fh)
        formula_a.append(fa)

    return {
        'v5': {'home': np.array(preds_h_v5), 'away': np.array(preds_a_v5)},
        'v4': {'home': np.array(preds_h_v4), 'away': np.array(preds_a_v4)},
        'formula': {'home': np.array(formula_h), 'away': np.array(formula_a)},
        'actual': {'home': np.array(actual_h), 'away': np.array(actual_a)},
        'X_all': X_all, 'y_home_all': y_home_all, 'y_away_all': y_away_all,
        'feat_names': feat_names,
        'n_validated': len(preds_h_v5),
    }


def report(results, games, warmup=200):
    """Print v5 vs v4 vs formula metrics + ship-gate."""
    a_h = results['actual']['home']
    a_a = results['actual']['away']
    a_total = a_h + a_a
    a_diff = a_h - a_a

    # We also need close_total to compute direction-vs-market
    close_totals = []
    for g in games[warmup:]:
        ct = g.get('close_total') or g.get('open_total')
        close_totals.append(float(ct) if ct is not None else float('nan'))
    close_totals = np.array(close_totals)

    print(f'\n=== V5 vs V4 vs FORMULA — walk-forward (n={results["n_validated"]}) ===')
    print(f'\n{"Model":<14} {"Total MAE":<12} {"Spread MAE":<12} {"Dir Acc":<10} {"TOT > line%":<14} {"TOT < line%"}')
    print('-' * 85)

    for label, preds in [('v5 (cal)', results['v5']), ('v4 (pos)', results['v4']), ('formula', results['formula'])]:
        h = preds['home']; a = preds['away']
        # Formula path can produce None for incomplete games — coerce to nan
        h = np.array([float(x) if x is not None else float('nan') for x in h])
        a = np.array([float(x) if x is not None else float('nan') for x in a])
        tot = h + a
        diff = h - a
        valid = ~np.isnan(tot) & ~np.isnan(a_total)
        if not valid.any():
            print(f'{label:<14} (no valid preds)')
            continue

        total_mae = float(np.mean(np.abs(tot[valid] - a_total[valid])))
        spread_mae = float(np.mean(np.abs(diff[valid] - a_diff[valid])))
        dir_acc = float(((diff[valid] > 0) == (a_diff[valid] > 0)).mean()) * 100

        # Total direction — predict OVER if tot > close, vs actual a_total > close
        mask = ~np.isnan(close_totals) & valid
        if mask.any():
            picks_over = tot[mask] > close_totals[mask]
            actuals_over = a_total[mask] > close_totals[mask]
            actuals_under = a_total[mask] < close_totals[mask]
            over_picks_won = ((picks_over) & actuals_over).sum()
            over_picks_total = picks_over.sum()
            under_picks_won = ((~picks_over) & actuals_under).sum()
            under_picks_total = (~picks_over).sum()
            over_pct = (over_picks_won / max(over_picks_total, 1)) * 100
            under_pct = (under_picks_won / max(under_picks_total, 1)) * 100
        else:
            over_pct = under_pct = float('nan')

        print(f'{label:<14} {total_mae:<12.3f} {spread_mae:<12.3f} {dir_acc:<10.1f} {over_pct:<14.1f} {under_pct:.1f}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-save', action='store_true')
    parser.add_argument('--warmup', type=int, default=200)
    args = parser.parse_args()

    print('=== v5 RUNS MODEL — recency-weighted retrain ===')
    games = fetch_data()
    if len(games) < args.warmup + 30:
        print(f'  ERROR: only {len(games)} games, need {args.warmup+30}+')
        return 1

    games.sort(key=lambda g: g.get('game_date') or '')
    print(f'  Loaded {len(games)} games')

    results = walkforward_v5(games, warmup=args.warmup)
    report(results, games, warmup=args.warmup)

    if args.no_save:
        print('\n  --no-save flag set, skipping model write.')
        return 0

    # Final fit on ALL data with calendar-recency weights (bug fix: v4
    # saved final ignored sample_weight; v5 applies it).
    X_all = results['X_all']
    y_home_all = results['y_home_all']
    y_away_all = results['y_away_all']
    feat_names = results['feat_names']
    today_for_weights = games[-1].get('game_date', '2026-05-24')
    w_final = compute_calendar_recency_weights(games, today_for_weights)

    print(f'\n  Final fit: {len(games)} games, calendar weights applied')
    print(f'  Weight distribution: last 30d={int((w_final == 3.0).sum())} games (3.0x), '
          f'30-60d={int((w_final == 1.5).sum())} (1.5x), '
          f'older={int((w_final == 1.0).sum())} (1.0x)')

    model_h_final = _make_xgb()
    model_a_final = _make_xgb()
    model_h_final.fit(X_all, y_home_all, sample_weight=w_final)
    model_a_final.fit(X_all, y_away_all, sample_weight=w_final)

    meta = {
        'version': 'v5',
        'trained_at': datetime.utcnow().isoformat(),
        'n_games': int(X_all.shape[0]),
        'feature_names': feat_names,
        'model_type': 'xgb',
        'recency_weighting': 'calendar',
        'recency_weights': {
            'last_30d': 3.0,
            'last_60d': 1.5,
            'older': 1.0,
        },
        'weight_counts': {
            'last_30d': int((w_final == 3.0).sum()),
            'last_60d': int((w_final == 1.5).sum()),
            'older': int((w_final == 1.0).sum()),
        },
        'walkforward_n': results['n_validated'],
        'notes': 'v5 = v4 feature set + calendar-based recency weights + fixed final-fit weight bug. Built 2026-05-24 in response to v4 OVER bias (43% 30d hit rate).',
    }

    os.makedirs(MODELS_DIR, exist_ok=True)
    with open(os.path.join(MODELS_DIR, 'home_runs_model_v5.pkl'), 'wb') as f:
        pickle.dump({'model': model_h_final, 'features': feat_names, 'meta': meta}, f)
    with open(os.path.join(MODELS_DIR, 'away_runs_model_v5.pkl'), 'wb') as f:
        pickle.dump({'model': model_a_final, 'features': feat_names, 'meta': meta}, f)
    with open(os.path.join(MODELS_DIR, 'runs_model_meta_v5.json'), 'w') as f:
        json.dump(meta, f, indent=2)

    print(f'\n  ✅ Saved home_runs_model_v5.pkl + away_runs_model_v5.pkl + meta')
    print(f'\n  NEXT: plumb v5 into game_context.py (separate ship — see meta.version)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
