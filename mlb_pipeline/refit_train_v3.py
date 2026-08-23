"""Refit v3 (2026-08-23) — signal_registry sign-constrained retrain.

v2 kept the L1 lasso + correlated-drop pipeline but still emitted sign-
flipped coefficients when the empirical hit rate said one direction and
the model fit the other. That's what the registry-guarded filter caught
tonight (8 sign flips across 5 prop families).

v3 fixes the ROOT cause: after the L1 fit, look up every non-zero coef
in signal_registry and ZERO any coefficient whose sign disagrees with
the empirical hit rate at |edge_pp| >= 3.0. Also drop ANTI_VALIDATED
features entirely — they've been proven wrong.

v3 approach:
  1. Same training pipeline as v2 (paginate 120d, L1 fit, corr-drop)
  2. NEW: load signal_registry as {(prop_type, signal_name): edge_pp, tier}
  3. NEW: after fit, iterate coefficients:
     - If registry has this signal at |edge_pp| >= 3.0
       AND sign(coef) != sign(edge_pp): ZERO the coef
     - If tier == ANTI_VALIDATED: ZERO the coef
     - Else: keep coef as-is
  4. NEW: aliases table so `last7_control → l7_control` gets matched
  5. Recompute score_min / score_max on surviving non-zero coefs
  6. Write prop_refit_weights_v3.json

Output goes alongside v2 (does not overwrite). apply_prop_refit.py loads
v3 in preference to v2 when the file exists. Roll back = delete v3.json.

Usage:
    python refit_train_v3.py                       # train + write v3.json
    python refit_train_v3.py --dry-run             # train + print, don't write
    python refit_train_v3.py --compare-v2          # also emit v3-vs-v2 diff
"""
import argparse, os, json, sys
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta

import requests
import numpy as np

for line in Path(__file__).parent.joinpath('.env').read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())
sys.stdout.reconfigure(encoding='utf-8')

url = os.environ['SUPABASE_URL']; key = os.environ['SUPABASE_KEY']
h = {'apikey': key, 'Authorization': f'Bearer {key}'}

from sklearn.linear_model import LogisticRegression

# Same alias table as apply_prop_refit.py — ensures training + inference
# agree on which signal_name maps to which registry row.
_COEF_ALIASES = {
    'last7_control': 'l7_control',
    'last7_walks':   'l7_walks',
}

# Domain-knowledge POSITIVE-expected signals (kept from v2 for warnings —
# now v3 also cross-checks against signal_registry which is stronger)
POSITIVE_EXPECTED = {
    'bb_under/under': ['clean_start', 'book_recalibration', 'aggressive_opp',
                       'bb_rate', 'l5_confirm', 'l7_control'],
    'bb_over/over':   ['l7_walks', 'wild_start', 'l5_confirm'],
    'ks_over/over':   ['book_recalibration', 'l5_confirm', 'l7_ks', 'ks_rate'],
    'ks_under/under': ['book_recalibration', 'l5_confirm', 'weak_opp_k'],
    'hits_over/over': ['book_recalibration', 'l5_confirm'],
    'hits_under/under': ['book_recalibration', 'l5_confirm'],
    'outs_over/over': ['book_recalibration', 'l5_confirm', 'l7_ip'],
    'outs_under/under': ['book_recalibration', 'l5_confirm'],
}


def drop_correlated_features(X, keys, threshold=0.90):
    if X.shape[1] < 2: return X, keys, []
    keep = list(range(X.shape[1])); dropped = []
    for i in range(X.shape[1]):
        if i not in keep: continue
        for j in range(i+1, X.shape[1]):
            if j not in keep: continue
            both = np.sum((X[:, i] == 1) & (X[:, j] == 1))
            either = np.sum((X[:, i] == 1) | (X[:, j] == 1))
            if either == 0: continue
            overlap = both / either
            if overlap > threshold:
                fires_i = X[:, i].sum(); fires_j = X[:, j].sum()
                drop_idx = j if fires_j <= fires_i else i
                if drop_idx == i: dropped.append(keys[i]); keep.remove(i); break
                else: dropped.append(keys[j]); keep.remove(j)
    return X[:, keep], [keys[i] for i in keep], dropped


def load_signal_registry() -> dict:
    """Return {(market_scope, signal_name): {edge_pp, tier, hit_rate, sample_n}}."""
    r = requests.get(f'{url}/rest/v1/signal_registry',
                     headers=h,
                     params={'sport': 'eq.MLB',
                             'select': 'market_scope,signal_name,hit_rate,sample_n,tier,edge_pp',
                             'limit': 2000},
                     timeout=15)
    idx = {}
    for row in (r.json() or []):
        if not isinstance(row, dict): continue
        ms = row.get('market_scope'); sn = row.get('signal_name')
        if ms and sn: idx[(ms, sn)] = row
    return idx


def apply_sign_constraints(coef_dict: dict, prop_type: str,
                            registry: dict) -> tuple[dict, list]:
    """Zero out coefficients that disagree with signal_registry.

    Returns (constrained_coefs, list_of_zeroed_actions).
    """
    zeroed = []
    out = {}
    for sig_name, coef in coef_dict.items():
        # Use alias when the training feature name differs from registry
        registry_name = _COEF_ALIASES.get(sig_name, sig_name)
        row = registry.get((prop_type, registry_name))
        if not row:
            out[sig_name] = coef  # no registry entry — trust ML
            continue
        tier = row.get('tier') or ''
        edge_pp = row.get('edge_pp')
        if tier == 'ANTI_VALIDATED':
            zeroed.append({'signal': sig_name, 'orig_coef': coef,
                           'reason': 'ANTI_VALIDATED in registry'})
            out[sig_name] = 0.0
            continue
        try: e = float(edge_pp) if edge_pp is not None else None
        except (TypeError, ValueError): e = None
        if e is None or abs(e) < 3.0:
            out[sig_name] = coef  # neutral registry — trust ML
            continue
        # Registry has an opinion — enforce sign
        if (e >= 3.0 and coef < 0) or (e <= -3.0 and coef > 0):
            zeroed.append({'signal': sig_name, 'orig_coef': coef,
                           'reason': f'sign flip vs registry edge_pp={e}'})
            out[sig_name] = 0.0
            continue
        out[sig_name] = coef
    return out, zeroed


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--compare-v2', action='store_true')
    args = p.parse_args()

    print('== refit_train_v3 · signal_registry sign-constrained ==')
    registry = load_signal_registry()
    print(f'  signal_registry: {len(registry)} rows loaded')

    end = datetime.now(); start = end - timedelta(days=120)
    rows = []; offset = 0
    while True:
        chunk = requests.get(f'{url}/rest/v1/mlb_pipeline_props',
            headers={**h, 'Range': f'{offset}-{offset+999}', 'Range-Unit': 'items'},
            params={
                'select': 'prop_type,direction,conviction,signals,result',
                'and': f'(game_date.gte.{start.date().isoformat()},game_date.lte.{end.date().isoformat()})',
                'result': 'in.(Win,Loss)',
            }, timeout=45).json()
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        offset += 1000
        if offset > 30000: break
    print(f'  {len(rows)} training rows over 120d')

    GATED = {
        'bb_over/over', 'bb_under/under',
        'ks_over/over', 'ks_under/under',
        'outs_over/over', 'outs_under/under',
        'er_over/over', 'er_under/under',
        'ha_over/over', 'ha_under/under',
        'hits_over/over', 'hits_under/under',
    }
    combos = Counter((r['prop_type'], r['direction']) for r in rows)
    targets = [(pt, d) for (pt, d), n in combos.items() if n >= 60 and f'{pt}/{d}' in GATED]

    weights = {
        'trained_at': datetime.now().isoformat(),
        'training_window': '120d',
        'n_total_rows': len(rows),
        'method': ('sklearn.LogisticRegression(C=0.3, penalty=l1, solver=liblinear, '
                    'class_weight=None) + corr>0.9 feature drop + signal_registry '
                    'sign constraints + ANTI_VALIDATED zeroing'),
        'usage': ('apply_prop_refit reads this JSON in preference to v2 when present.'),
        'registry_priors_used': len(registry),
        'prop_types': {},
        'sign_flip_warnings': [],
        'registry_zeroed': [],
    }

    for pt, d in targets:
        subset = [r for r in rows if r['prop_type']==pt and r['direction']==d]
        keys_c = Counter()
        for r in subset:
            for k in (r.get('signals') or {}).keys():
                if not k.startswith('_'): keys_c[k] += 1
        keys = [k for k, n in keys_c.most_common() if n >= 8]
        if not keys: continue
        X = np.zeros((len(subset), len(keys))); y = np.zeros(len(subset))
        for i, r in enumerate(subset):
            sigs = r.get('signals') or {}
            for j, k in enumerate(keys):
                if k in sigs: X[i, j] = 1.0
            y[i] = 1.0 if r['result']=='Win' else 0.0

        X, keys, dropped = drop_correlated_features(X, keys, threshold=0.90)

        clf = LogisticRegression(max_iter=2000, C=0.3, penalty='l1',
                                  solver='liblinear', class_weight=None)
        clf.fit(X, y)
        raw_coef = {k: float(clf.coef_[0][i]) for i, k in enumerate(keys)}

        # NEW in v3: apply registry sign constraints
        coef_dict, zeroed = apply_sign_constraints(raw_coef, pt, registry)
        weights['registry_zeroed'].extend(
            [{'prop': f'{pt}/{d}', **z} for z in zeroed])

        # Recompute score bounds on surviving non-zero coefs (matches
        # inference-time behavior in apply_prop_refit.py when zeroing
        # happens — keeps rescaling consistent)
        surviving_coefs = np.array([coef_dict[k] for k in keys])
        fitted_scores = X @ surviving_coefs
        score_min = float(fitted_scores.min()) if len(fitted_scores) else 0.0
        score_max = float(fitted_scores.max()) if len(fitted_scores) else 0.0

        # Legacy sign-flip warnings (informational only in v3; registry
        # constraint already zeros the real offenders)
        warnings = []
        expected = POSITIVE_EXPECTED.get(f'{pt}/{d}', [])
        for signal_name in expected:
            for k in coef_dict:
                if signal_name in k.lower() and coef_dict[k] < -0.05:
                    warnings.append({'prop': f'{pt}/{d}', 'signal': k,
                                     'coef': coef_dict[k],
                                     'note': 'legacy warning — registry constraint should have caught this'})

        weights['prop_types'][f'{pt}/{d}'] = {
            'n_training': len(subset),
            'baseline_hit': float(y.mean()),
            'intercept': float(clf.intercept_[0]),
            'coefficients': coef_dict,
            'score_min': score_min,
            'score_max': score_max,
            'features_dropped_correlated': dropped,
            'features_zeroed_by_registry': [z['signal'] for z in zeroed],
        }
        weights['sign_flip_warnings'].extend(warnings)
        print(f'  {pt}/{d}: n={len(subset)}  keys={len(keys)}  zeroed_by_registry={len(zeroed)}')
        for z in zeroed:
            print(f'    ZERO  {z["signal"]:<22} orig_coef={z["orig_coef"]:+.3f}  {z["reason"]}')

    print(f'\n  {len(weights["prop_types"])} prop families fit')
    print(f'  total sign flips zeroed by registry: {len(weights["registry_zeroed"])}')

    if args.dry_run:
        print('\n  [DRY-RUN] not writing')
        return

    out = Path(__file__).parent / 'models' / 'prop_refit_weights_v3.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(weights, indent=2))
    print(f'\n  saved -> {out}')

    if args.compare_v2:
        v2p = Path(__file__).parent / 'models' / 'prop_refit_weights_v2.json'
        if v2p.exists():
            v2 = json.loads(v2p.read_text())
            print('\n=== v3 vs v2 coefficient diff ===')
            for fam in sorted(weights['prop_types']):
                v2_pt = (v2.get('prop_types') or {}).get(fam) or {}
                v3_pt = weights['prop_types'][fam]
                v2_c = v2_pt.get('coefficients') or {}
                v3_c = v3_pt.get('coefficients') or {}
                diffs = []
                for k in set(v2_c) | set(v3_c):
                    a = v2_c.get(k, 0.0); b = v3_c.get(k, 0.0)
                    if abs(a - b) >= 0.05:
                        diffs.append((k, a, b))
                if diffs:
                    print(f'\n{fam}:')
                    for k, a, b in sorted(diffs, key=lambda x: -abs(x[1]-x[2])):
                        print(f'  {k:<24} v2={a:+.3f} → v3={b:+.3f}')


if __name__ == '__main__':
    main()
