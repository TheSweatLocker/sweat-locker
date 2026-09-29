"""NFL spread logistic regression trainer — mirrors nfl_ml_logreg_train.py
pattern but predicts "did home cover the closing spread?" instead of
"did home win outright?".

Motivation: existing LR ML shadow only fires on moneyline; Sharp Card
lands 1-3 spread picks/Sunday because the ensemble has no dedicated
spread signal. Spreads are the biggest bet type — this closes a gap.

Target: nfl_game_results.spread_result in ('home_covered','away_covered').
'push' / 'no_line' / null filtered out.

USAGE:
    python nfl_spread_logreg_train.py                # train & save
    python nfl_spread_logreg_train.py --dry-run
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

# Base features stored on nfl_game_results.  Delta features (line movement,
# total movement) are engineered in-code from open/close pairs below.
FEATURE_CANDIDATES = [
    'close_spread', 'open_spread', 'close_total', 'open_total',
    'close_home_ml', 'close_away_ml', 'open_home_ml', 'open_away_ml',
    'home_rest', 'away_rest',
    'div_game',
]

# Engineered delta feature names appended after base features load.
DELTA_FEATURES = ['spread_move', 'total_move']

TRAIN_SEASONS = (2022, 2023, 2024)
TEST_SEASONS  = (2025,)


def _probe_valid_cols() -> set:
    r = requests.get(f'{SB}/rest/v1/nfl_game_results?select=*&limit=1', headers=H, timeout=10)
    if r.status_code != 200 or not r.json(): return set()
    return set(r.json()[0].keys())


def _to_float(v):
    if isinstance(v, bool): return 1.0 if v else 0.0
    try: return float(v)
    except (TypeError, ValueError): return None


def _engineer_deltas(rec: dict) -> dict:
    cs = _to_float(rec.get('close_spread'))
    os_ = _to_float(rec.get('open_spread'))
    ct = _to_float(rec.get('close_total'))
    ot = _to_float(rec.get('open_total'))
    rec['spread_move'] = (cs - os_) if (cs is not None and os_ is not None) else None
    rec['total_move']  = (ct - ot)  if (ct is not None and ot  is not None) else None
    return rec


def train(dry_run: bool = False):
    print(f'== NFL spread logreg train ==')
    valid_cols = _probe_valid_cols()
    base_features = [c for c in FEATURE_CANDIDATES if c in valid_cols]
    features = base_features + DELTA_FEATURES
    print(f'  {len(base_features)}/{len(FEATURE_CANDIDATES)} base features exist')
    print(f'  + {len(DELTA_FEATURES)} engineered delta features')

    select_str = 'game_id,game_date,season,game_type,spread_result,' + ','.join(base_features)
    rows = []
    for off in range(0, 10000, 500):
        r = requests.get(f'{SB}/rest/v1/nfl_game_results',
            params={'select': select_str,
                    'home_score': 'not.is.null',
                    'spread_result': 'in.(home_covered,away_covered)',
                    'close_spread': 'not.is.null',
                    'game_type': 'eq.REG',
                    'limit': 500, 'offset': off, 'order': 'game_date.asc'},
            headers=H, timeout=30)
        chunk = r.json() if isinstance(r.json(), list) else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 500: break
    print(f'  pulled {len(rows)} resolved REG games with spread_result & close_spread')
    if len(rows) < 300:
        print('  insufficient data (<300 games) - aborting'); return

    # Season filter: train 2022-2024, test 2025.  Anything outside window dropped.
    all_rows = []
    for r in rows:
        s = r.get('season')
        if s not in TRAIN_SEASONS and s not in TEST_SEASONS: continue
        _engineer_deltas(r)
        all_rows.append(r)
    print(f'  after season filter (train {TRAIN_SEASONS} + test {TEST_SEASONS}): {len(all_rows)}')

    def _to_xy(records):
        Xr, yr = [], []
        for rec in records:
            sr = rec.get('spread_result')
            if sr not in ('home_covered', 'away_covered'): continue
            row_vals = [float('nan') if _to_float(rec.get(f)) is None else _to_float(rec.get(f))
                        for f in features]
            Xr.append(row_vals)
            yr.append(1 if sr == 'home_covered' else 0)
        return np.array(Xr), np.array(yr)

    train_records = [r for r in all_rows if r.get('season') in TRAIN_SEASONS]
    test_records  = [r for r in all_rows if r.get('season') in TEST_SEASONS]
    X_train_raw, y_train = _to_xy(train_records)
    X_test_raw,  y_test  = _to_xy(test_records)
    print(f'  train rows: {len(y_train)}  home-covered rate: {y_train.mean()*100:.1f}%')
    print(f'  test  rows: {len(y_test)}   home-covered rate: {y_test.mean()*100:.1f}%')

    # Drop all-NaN columns (measured on TRAIN so eval mirrors deployment)
    valid_col_mask = ~np.all(np.isnan(X_train_raw), axis=0)
    dropped = [f for f, keep in zip(features, valid_col_mask) if not keep]
    if dropped: print(f'  dropping all-NaN features: {dropped}')
    features = [f for f, keep in zip(features, valid_col_mask) if keep]
    X_train_raw = X_train_raw[:, valid_col_mask]
    X_test_raw  = X_test_raw[:,  valid_col_mask]

    imputer = SimpleImputer(strategy='median')
    X_train_imp = imputer.fit_transform(X_train_raw)
    X_test_imp  = imputer.transform(X_test_raw)
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train_imp)
    X_test_scaled  = scaler.transform(X_test_imp)

    lr = LogisticRegression(max_iter=1000, C=1.0)
    lr.fit(X_train_scaled, y_train)
    train_acc = lr.score(X_train_scaled, y_train)
    test_acc  = lr.score(X_test_scaled,  y_test)
    baseline  = 0.5  # spread designed near-50/50; use flat baseline per spec
    empirical_test_home_rate = y_test.mean()
    print(f'\n  Train acc: {train_acc*100:.1f}%   Test acc: {test_acc*100:.1f}%')
    print(f'  Baseline: {baseline*100:.1f}%   Lift: {(test_acc - baseline)*100:+.1f}pp')
    print(f'  (empirical home-cover rate on test: {empirical_test_home_rate*100:.1f}%)')

    probs = lr.predict_proba(X_test_scaled)[:, 1]
    print(f'\n  Tiered predictions on 2025 holdout:')
    tier_report = []
    for lo, hi, tier, side in [(0.60, 1.01, 'PRIME',  'HOME'),
                                (0.55, 0.60, 'STRONG', 'HOME'),
                                (0.45, 0.55, 'COIN',   'NONE'),
                                (0.40, 0.45, 'STRONG', 'AWAY'),
                                (0.00, 0.40, 'PRIME',  'AWAY')]:
        mask = (probs >= lo) & (probs < hi)
        n = int(mask.sum())
        if n == 0:
            print(f'    {tier}_{side:4s}: 0 picks in [{lo:.2f},{hi:.2f})')
            continue
        if side == 'HOME': wins = int(((y_test == 1) & mask).sum())
        elif side == 'AWAY': wins = int(((y_test == 0) & mask).sum())
        else: wins = 0
        hit = 100*wins/n if n else 0
        print(f'    {tier}_{side:4s} [{lo:.2f},{hi:.2f}): {wins}/{n} = {hit:.1f}%')
        tier_report.append({'tier': tier, 'side': side, 'lo': lo, 'hi': hi, 'n': n, 'wins': wins, 'hit_pct': hit})

    # Discipline summary Andy asked for: p>=.55 HOME, p<=.45 AWAY
    m_home = probs >= 0.55
    m_away = probs <= 0.45
    if m_home.sum():
        hits_h = int(((y_test == 1) & m_home).sum())
        print(f'\n  DISCIPLINE  HOME p>=.55 : {hits_h}/{int(m_home.sum())} = {100*hits_h/m_home.sum():.1f}%')
    else:
        print(f'\n  DISCIPLINE  HOME p>=.55 : 0 picks')
    if m_away.sum():
        hits_a = int(((y_test == 0) & m_away).sum())
        print(f'  DISCIPLINE  AWAY p<=.45 : {hits_a}/{int(m_away.sum())} = {100*hits_a/m_away.sum():.1f}%')
    else:
        print(f'  DISCIPLINE  AWAY p<=.45 : 0 picks')

    print(f'\n  Top features by |coefficient|:')
    for f, c in sorted(zip(features, lr.coef_[0]), key=lambda x: -abs(x[1]))[:10]:
        print(f'    {f:20s} {c:>+7.3f}')

    # Refit on full window (train + test) for the shipped weights
    X_full_raw = np.vstack([X_train_raw, X_test_raw])
    y_full     = np.concatenate([y_train, y_test])
    imputer_full = SimpleImputer(strategy='median')
    X_full_imp = imputer_full.fit_transform(X_full_raw)
    scaler_full = StandardScaler()
    X_full_scaled = scaler_full.fit_transform(X_full_imp)
    lr_full = LogisticRegression(max_iter=1000, C=1.0)
    lr_full.fit(X_full_scaled, y_full)

    out = {
        'version': datetime.now(timezone.utc).isoformat(),
        'features': features,
        'coefficients': lr_full.coef_[0].tolist(),
        'intercept': float(lr_full.intercept_[0]),
        'imputer_medians': imputer_full.statistics_.tolist(),
        'scaler_mean': scaler_full.mean_.tolist(),
        'scaler_scale': scaler_full.scale_.tolist(),
        'meta': {
            'test_accuracy': float(test_acc),
            'baseline_accuracy': float(baseline),
            'lift_pp': float((test_acc - baseline) * 100),
            'n_train': int(len(y_train)),
            'n_test':  int(len(y_test)),
            'train_seasons': list(TRAIN_SEASONS),
            'test_seasons':  list(TEST_SEASONS),
            'empirical_test_home_cover_rate': float(empirical_test_home_rate),
            'target': 'home_covered_close_spread',
            'tier_report': tier_report,
        },
    }
    out_path = MODELS_DIR / 'nfl_spread_logreg.json'
    if dry_run:
        print(f'\n  [DRY] would write {out_path}')
    else:
        out_path.write_text(json.dumps(out, indent=2))
        print(f'\n  saved {out_path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    train(dry_run=args.dry_run)
