"""One-shot: fit + serialize per-prop-type signal coefficients to JSON.
Consumed by apply_prop_refit.py at run-time.

Output: mlb_pipeline/models/prop_refit_weights_v1.json
"""
import os, json, sys
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta

import requests
import numpy as np

for line in Path(r'C:\Users\gomez\SweatShop\mlb_pipeline\.env').read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())
sys.stdout.reconfigure(encoding='utf-8')
url = os.environ['SUPABASE_URL']; key = os.environ['SUPABASE_KEY']
h = {'apikey': key, 'Authorization': f'Bearer {key}'}

from sklearn.linear_model import LogisticRegression

end = datetime.now(); start = end - timedelta(days=60)
rows = requests.get(f'{url}/rest/v1/mlb_pipeline_props', headers=h, params={
    'select': 'prop_type,direction,conviction,signals,result',
    'and': f'(game_date.gte.{start.date().isoformat()},game_date.lte.{end.date().isoformat()})',
    'result': 'in.(Win,Loss)', 'limit': 10000}, timeout=45).json()
print(f'  {len(rows)} training rows over 60d')

# Only refit prop types where backtest showed refit >= current
GATED = {'hits_over/over', 'ks_over/over', 'outs_under/under', 'outs_over/over',
         'bb_under/under', 'hits_under/under', 'ks_under/under', 'bb_over/over'}
combos = Counter((r['prop_type'], r['direction']) for r in rows)
targets = [(p, d) for (p, d), n in combos.items() if n >= 40 and f'{p}/{d}' in GATED]

weights = {'trained_at': datetime.now().isoformat(),
           'training_window': '60d', 'n_total_rows': len(rows),
           'method': 'sklearn.LogisticRegression(C=0.5, class_weight=balanced)',
           'usage': ('apply_prop_refit reads this JSON and computes '
                     'refit_conviction per prop as normalized sum of '
                     'fired-signal coefficients rescaled to 0-100.'),
           'prop_types': {}}
for pt, d in targets:
    subset = [r for r in rows if r['prop_type']==pt and r['direction']==d]
    keys_c = Counter()
    for r in subset:
        for k in (r.get('signals') or {}).keys():
            if not k.startswith('_'): keys_c[k] += 1
    keys = [k for k, n in keys_c.most_common() if n >= 5]
    if not keys: continue
    X = np.zeros((len(subset), len(keys))); y = np.zeros(len(subset))
    for i, r in enumerate(subset):
        sigs = r.get('signals') or {}
        for j, k in enumerate(keys):
            if k in sigs: X[i, j] = 1.0
        y[i] = 1.0 if r['result']=='Win' else 0.0
    clf = LogisticRegression(max_iter=1000, C=0.5, class_weight='balanced')
    clf.fit(X, y)
    # Rescale coefficients: coefficient sum will be normalized to 0-100 range
    # at apply time. Store raw coefficients + intercept + min/max obs range.
    fitted_scores = X @ clf.coef_[0]
    weights['prop_types'][f'{pt}/{d}'] = {
        'n_training': len(subset),
        'baseline_hit': float(y.mean()),
        'intercept': float(clf.intercept_[0]),
        'coefficients': {k: float(clf.coef_[0][i]) for i, k in enumerate(keys)},
        'score_min': float(fitted_scores.min()),
        'score_max': float(fitted_scores.max()),
    }
    print(f'  fit {pt}/{d}: n={len(subset)} baseline={y.mean():.1%} keys={len(keys)}')

out = Path(r'C:\Users\gomez\SweatShop\mlb_pipeline\models\prop_refit_weights_v1.json')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(weights, indent=2))
print(f'\n  saved -> {out}')
print(f'  {len(weights["prop_types"])} prop types serialized')
