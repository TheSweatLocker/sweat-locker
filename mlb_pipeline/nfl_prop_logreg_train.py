"""NFL PROP logistic regression trainer — port of mlb_prop_logreg_train.py.

Week 1 backtest (2026-09-14) revealed 0/636 NFL props had any LR shadow
signal. Everything gates on tier heuristics; PRIME hits 50% (coin flip,
-2.18u). Andy directive: shadow-write `signals._lr_prop_p` on every scored
NFL prop so Week 3 can gate PRIME behind p_lr >= 0.60 once n >= 800.

Training source: nfl_pipeline_props where result IN ('Win','Loss').
Push filtered out. Target: 1 if result='Win' else 0.

Features (all columns already on nfl_pipeline_props):
  Numeric — median-imputed if missing on a row, all-NaN cols dropped:
    conviction, refit_conviction, book_line, book_over_odds,
    book_under_odds, player_l5_hit_count, player_l10_hit_count,
    player_season_hit_pct, player_l10_extreme_flag (bool→0/1)
  Categorical:
    prop_type one-hot (pass_yds_over/under, rush_yds_over/under, ...)
    direction_over binary

signals JSONB is NOT cracked open in v1.

Model shape: StandardScaler + LogisticRegression(C=1.0, max_iter=1000).
Time-based holdout: last full game_date as test, rest as train (Week 1's
data spans 3 dates — 9/10, 9/11, 9/13. Latest date 9/13 has 343 rows so
becomes the natural holdout).

Persists to models/nfl_prop_logreg.json.

USAGE:
    python nfl_prop_logreg_train.py                # all graded rows
    python nfl_prop_logreg_train.py --dry-run
"""
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL']; K = os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': f'Bearer {K}'}
MODELS_DIR = Path(__file__).parent / 'models'
MODELS_DIR.mkdir(exist_ok=True)

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

NUMERIC_FEATURES = [
    'conviction', 'refit_conviction',
    'book_line', 'book_over_odds', 'book_under_odds',
    'player_l5_hit_count', 'player_l10_hit_count', 'player_season_hit_pct',
    'player_l10_extreme_flag',   # boolean → 0/1
]

# Prop-type registry — all NFL markets we currently offer. Any prop_type
# outside this list is skipped from training (malformed / one-off).
PROP_TYPES = [
    'pass_yds_over', 'pass_yds_under',
    'pass_tds_over', 'pass_tds_under',
    'pass_attempts_over', 'pass_attempts_under',
    'pass_completions_over', 'pass_completions_under',
    'pass_interceptions_over', 'pass_interceptions_under',
    'rush_yds_over', 'rush_yds_under',
    'rush_attempts_over', 'rush_attempts_under',
    'rush_tds_over', 'rush_tds_under',
    'reception_yds_over', 'reception_yds_under',
    'receptions_over', 'receptions_under',
    'anytime_td_over', 'anytime_td_under',
]


def _to_float(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try: return float(v)
    except (TypeError, ValueError): return None


def train(dry_run: bool = False):
    print('== NFL prop logreg train ==')

    # Pull ALL graded rows (Win/Loss). Week 1 corpus is small (~439);
    # paginate defensively.
    rows = []
    for off in range(0, 20000, 1000):
        r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props',
            params={'select': 'prop_type,direction,conviction,refit_conviction,'
                              'book_line,book_over_odds,book_under_odds,'
                              'player_l5_hit_count,player_l10_hit_count,'
                              'player_season_hit_pct,player_l10_extreme_flag,'
                              'result,game_date',
                    'result': 'in.(Win,Loss)',
                    'limit': 1000, 'offset': off, 'order': 'game_date.asc'},
            headers=H, timeout=30)
        chunk = r.json() if isinstance(r.json(), list) else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
    print(f'  pulled {len(rows)} resolved props (Win/Loss)')

    if len(rows) < 200:
        print(f'  ⚠ n_train too thin (<200 after filter) — writing STUB model')
        # Stub: p=0.5 for everyone. Real training after Week 2.
        stub = {
            'version': datetime.now(timezone.utc).isoformat(),
            'stub': True,
            'reason': f'insufficient graded rows ({len(rows)})',
            'features': [],
            'prop_types_onehot': PROP_TYPES,
            'coefficients': [],
            'intercept': 0.0,
            'imputer_medians': [],
            'scaler_mean': [],
            'scaler_scale': [],
            'meta': {
                'n_train': 0, 'n_test': 0,
                'test_accuracy': 0.5, 'baseline_accuracy': 0.5,
                'lift_pp': 0.0,
                'train_ts': datetime.now(timezone.utc).isoformat(),
            },
        }
        out_path = MODELS_DIR / 'nfl_prop_logreg.json'
        if not dry_run:
            out_path.write_text(json.dumps(stub, indent=2))
            print(f'  ✓ saved STUB {out_path}')
        return

    # Feature registry: numeric + direction + prop_type one-hots
    all_features = list(NUMERIC_FEATURES) + ['direction_over']
    all_features += [f'ptype_{pt}' for pt in PROP_TYPES]

    X_rows, y_rows, date_rows = [], [], []
    for r in rows:
        pt = r.get('prop_type') or ''
        if pt not in PROP_TYPES: continue
        direction = (r.get('direction') or '').lower()
        row = []
        for f in NUMERIC_FEATURES:
            v = _to_float(r.get(f))
            row.append(float('nan') if v is None else v)
        row.append(1.0 if direction == 'over' else 0.0)
        for onehot_pt in PROP_TYPES:
            row.append(1.0 if pt == onehot_pt else 0.0)
        X_rows.append(row)
        y_rows.append(1 if r.get('result') == 'Win' else 0)
        date_rows.append(r.get('game_date') or '')

    X = np.array(X_rows); y = np.array(y_rows); dates = np.array(date_rows)
    print(f'  X shape: {X.shape} · base win rate: {y.mean()*100:.1f}%')

    # Drop all-NaN columns (rare — mostly one-hots for unseen prop_types,
    # and NFL Week 1's still-empty player_l*/season_hit_pct columns).
    valid_col_mask = ~np.all(np.isnan(X), axis=0)
    dropped = [f for f, keep in zip(all_features, valid_col_mask) if not keep]
    if dropped: print(f'  ⚠ dropping all-NaN features ({len(dropped)}): {dropped}')
    all_features = [f for f, keep in zip(all_features, valid_col_mask) if keep]
    X = X[:, valid_col_mask]

    imputer = SimpleImputer(strategy='median')
    X_imp = imputer.fit_transform(X)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_imp)

    # Holdout strategy: prefer a time-based split (latest full game_date
    # as test) — but only when no single date dominates the sample. Week 1
    # NFL is a degenerate case: ~78% of graded rows land on Sunday, so
    # a strict time-split leaves 96 train / 343 test and the model has
    # nothing to learn from. Fall back to random 70/30 whenever the
    # earliest N-1 dates hold <30% of the data.
    uniq_dates = sorted(set(dates.tolist()))
    can_time_split = False
    if len(uniq_dates) >= 2:
        holdout_date = uniq_dates[-1]
        pre_share = float((dates != holdout_date).mean())
        can_time_split = pre_share >= 0.30
    if can_time_split:
        test_mask = (dates == holdout_date)
        train_mask = ~test_mask
        X_train, X_test = X_scaled[train_mask], X_scaled[test_mask]
        y_train, y_test = y[train_mask], y[test_mask]
        split_desc = f'time-based (test = {holdout_date})'
    else:
        rng = np.random.RandomState(42)
        idx = rng.permutation(len(y))
        X_scaled = X_scaled[idx]; y = y[idx]
        cut = int(len(y) * 0.7)
        X_train, X_test = X_scaled[:cut], X_scaled[cut:]
        y_train, y_test = y[:cut], y[cut:]
        reason = 'single-date sample' if len(uniq_dates) < 2 else \
                 f'single date dominates ({100*(1-pre_share):.0f}% on {holdout_date})'
        split_desc = f'random 70/30 ({reason})'

    print(f'  split: {split_desc} · n_train={len(y_train)} n_test={len(y_test)}')
    if len(y_test) < 20:
        print(f'  ⚠ test slice tiny (n={len(y_test)}) — accuracy stats are noisy')

    lr = LogisticRegression(max_iter=1000, C=1.0)
    lr.fit(X_train, y_train)
    train_acc = lr.score(X_train, y_train)
    test_acc  = lr.score(X_test, y_test) if len(y_test) else 0.0
    baseline  = y_test.mean() if len(y_test) else 0.0
    lift_pp   = (test_acc - baseline) * 100
    print(f'\n  Train acc: {train_acc*100:.1f}%   Test acc: {test_acc*100:.1f}%')
    print(f'  Baseline (win rate on test slice): {baseline*100:.1f}%')
    print(f'  Lift over baseline: {lift_pp:+.1f}pp')

    # Discipline check on holdout
    probs = lr.predict_proba(X_test)[:, 1] if len(y_test) else np.array([])
    def _bucket(lo, hi, label):
        if not len(probs): return
        mask = (probs >= lo) & (probs < hi)
        n = int(mask.sum())
        if n == 0:
            print(f'    {label:20s} (p={lo:.2f}-{hi:.2f}): n=0')
            return
        wins = int(((y_test == 1) & mask).sum())
        print(f'    {label:20s} (p={lo:.2f}-{hi:.2f}): {wins}/{n} = {100*wins/n:.1f}%')
    print(f'\n  Discipline buckets on holdout:')
    _bucket(0.60, 1.01, 'GATE (p >= 0.60)')
    _bucket(0.45, 0.55, 'COIN FLIP')
    _bucket(0.00, 0.40, 'FADE (p <= 0.40)')
    # Also standard tier bins for continuity with MLB reporting
    print(f'  Tier bins:')
    _bucket(0.70, 1.01, 'PRIME')
    _bucket(0.60, 0.70, 'STRONG')
    _bucket(0.50, 0.60, 'LEAN')
    _bucket(0.40, 0.50, 'COIN')
    _bucket(0.00, 0.40, 'FADE')

    # Top features by |coef|
    coefs_ranked = sorted(zip(all_features, lr.coef_[0]), key=lambda x: -abs(x[1]))
    print(f'\n  Top 12 features by |coefficient|:')
    for f, c in coefs_ranked[:12]:
        dir_txt = 'BOOSTS win' if c > 0 else 'reduces win'
        print(f'    {f:34s}  coef={c:>+6.3f}  ({dir_txt})')

    # Refit on full data
    lr_full = LogisticRegression(max_iter=1000, C=1.0)
    lr_full.fit(X_scaled, y)

    model_out = {
        'version': datetime.now(timezone.utc).isoformat(),
        'features': all_features,
        'prop_types_onehot': PROP_TYPES,
        'coefficients': lr_full.coef_[0].tolist(),
        'intercept': float(lr_full.intercept_[0]),
        'imputer_medians': imputer.statistics_.tolist(),
        'scaler_mean': scaler.mean_.tolist(),
        'scaler_scale': scaler.scale_.tolist(),
        'meta': {
            'n_train': int(len(y_train)),
            'n_test': int(len(y_test)),
            'test_accuracy': float(test_acc),
            'baseline_accuracy': float(baseline),
            'lift_pp': float(lift_pp),
            'split_desc': split_desc,
            'train_ts': datetime.now(timezone.utc).isoformat(),
        },
    }
    out_path = MODELS_DIR / 'nfl_prop_logreg.json'
    if dry_run:
        print(f'\n  [DRY] would write {out_path}')
    else:
        out_path.write_text(json.dumps(model_out, indent=2))
        print(f'\n  ✓ saved {out_path} ({len(all_features)} features, '
              f'test_acc={test_acc*100:.1f}%, lift={lift_pp:+.1f}pp)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    train(dry_run=args.dry_run)
