"""
Train a regression-based total projection model from logged game results.

Uses Ridge regression on features that showed predictive value in audit:
- home/away runs per game, wRC+, K%, xERA
- park factor, temperature
- market line (close_total) as anchor feature

Saves model to mlb_pipeline/models/total_model_v5.pkl

Usage:
  python mlb_pipeline/train_total_model.py              # train
  python mlb_pipeline/train_total_model.py --audit      # CV performance only
"""
import os
import argparse
import pickle
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
os.makedirs(MODELS_DIR, exist_ok=True)

FEATURES = [
    'home_runs_per_game', 'away_runs_per_game',
    'home_sp_xera', 'away_sp_xera',
    'home_wrc_plus', 'away_wrc_plus',
    'home_team_k_pct', 'away_team_k_pct',
    'park_run_factor', 'temperature',
    'close_total',
]


def fetch_training_data():
    all_games = []
    offset = 0
    while True:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results?season=eq.2026&home_score=not.is.null&select=*&limit=1000&offset={offset}',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        )
        batch = r.json()
        if not batch or not isinstance(batch, list):
            break
        all_games.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return all_games


def build_dataset(games):
    rows = []
    for g in games:
        if any(g.get(f) is None for f in FEATURES):
            continue
        try:
            row = [float(g[f]) for f in FEATURES]
            actual = float(g['home_score']) + float(g['away_score'])
            rows.append((row, actual))
        except:
            continue
    return rows


def audit(rows):
    import numpy as np
    from sklearn.linear_model import Ridge
    from sklearn.model_selection import KFold

    X = np.array([r[0] for r in rows])
    y = np.array([r[1] for r in rows])
    lines_idx = FEATURES.index('close_total')
    lines = X[:, lines_idx]

    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    preds = np.zeros(len(y))
    for tr, te in kf.split(X):
        m = Ridge(alpha=1.0)
        m.fit(X[tr], y[tr])
        preds[te] = m.predict(X[te])

    mae = np.mean(np.abs(preds - y))
    market_mae = np.mean(np.abs(lines - y))
    print(f'5-fold CV MAE: {mae:.2f} runs (market baseline: {market_mae:.2f})')

    for thresh in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
        c = w = 0
        for i in range(len(preds)):
            d = preds[i] - lines[i]
            if abs(d) < thresh or y[i] == lines[i]:
                continue
            if (d > 0) == (y[i] > lines[i]):
                c += 1
            else:
                w += 1
        total = c + w
        pct = c / total * 100 if total else 0
        print(f'  {thresh}+ delta: {c}-{w} ({pct:.1f}%)')


def train_and_save(rows):
    import numpy as np
    from sklearn.linear_model import Ridge

    X = np.array([r[0] for r in rows])
    y = np.array([r[1] for r in rows])

    model = Ridge(alpha=1.0)
    model.fit(X, y)

    path = os.path.join(MODELS_DIR, 'total_model_v5.pkl')
    with open(path, 'wb') as f:
        pickle.dump({
            'model': model,
            'features': FEATURES,
            'trained_at': datetime.now().isoformat(),
            'n_samples': len(rows),
        }, f)
    print(f'\n✅ Saved to {path}')
    print(f'Trained on {len(rows)} games')
    print(f'\nFeature weights:')
    for f, c in sorted(zip(FEATURES, model.coef_), key=lambda x: -abs(x[1])):
        print(f'  {f:<28} {c:+.4f}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit', action='store_true', help='Just audit, don\'t save model')
    args = parser.parse_args()

    print('Fetching training data...')
    games = fetch_training_data()
    rows = build_dataset(games)
    print(f'Complete rows: {len(rows)}')

    if len(rows) < 50:
        print('Not enough data to train.')
        exit(1)

    audit(rows)

    if not args.audit:
        train_and_save(rows)
