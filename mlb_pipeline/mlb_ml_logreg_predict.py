"""MLB ML logistic-regression predictor + shadow-mode writer.

Loads the trained model saved by mlb_ml_logreg_train.py. For each of
today's games, computes p_home_win and shadow-writes the prediction
alongside the current primary_play so we can grade both systems live
before cutting over.

USAGE:
    python mlb_ml_logreg_predict.py                   # today, shadow-write
    python mlb_ml_logreg_predict.py --date 2026-09-04
    python mlb_ml_logreg_predict.py --dry-run         # print, don't write
    python mlb_ml_logreg_predict.py --print-only      # no DB call at all

Shadow field written to mlb_game_context.primary_play._logreg_shadow:
    {
      "p_home_win": 0.72,
      "suggested_side": "HOME",
      "suggested_tier": "PRIME",
      "model_version": "2026-09-03T..."
    }

Downstream (comparison audit, resolution, dashboard) reads this field
to grade the logreg suggestions against actual outcomes vs current
primary_play picks.
"""
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

import math
import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL']; K = os.environ['SUPABASE_KEY']
H_R = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}
MODELS_DIR = Path(__file__).parent / 'models'

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _load_model():
    p = MODELS_DIR / 'mlb_ml_logreg.json'
    if not p.exists(): return None
    return json.loads(p.read_text())


def _to_float(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z); return 1.0 / (1.0 + ez)
    ez = math.exp(z); return ez / (1.0 + ez)


def predict_home_win(ctx: dict, model: dict) -> dict:
    """Return {p_home_win, suggested_side, suggested_tier}."""
    features = model['features']
    coefs = model['coefficients']
    intercept = model['intercept']
    medians = model['imputer_medians']
    means = model['scaler_mean']
    scales = model['scaler_scale']
    # Build feature vector, impute NaN with training-time median, then z-score
    z_sum = intercept
    for i, f in enumerate(features):
        v = _to_float(ctx.get(f))
        if v is None: v = medians[i]
        scaled = (v - means[i]) / scales[i] if scales[i] != 0 else 0
        z_sum += coefs[i] * scaled
    p = _sigmoid(z_sum)
    # Tier binning matches the training report
    if p >= 0.65:   tier, side = 'PRIME',  'HOME'
    elif p >= 0.55: tier, side = 'STRONG', 'HOME'
    elif p >= 0.45: tier, side = 'COIN',   'NONE'
    elif p >= 0.35: tier, side = 'STRONG', 'AWAY'
    else:           tier, side = 'PRIME',  'AWAY'
    return {
        'p_home_win': round(p, 4),
        'suggested_side': side,
        'suggested_tier': tier,
        'model_version': model.get('version', ''),
    }


def run(target_date: str = None, dry_run: bool = False, print_only: bool = False):
    model = _load_model()
    if model is None:
        print('  ✗ model not found — run mlb_ml_logreg_train.py first'); return
    ver = model.get('version', '?')[:16]
    n_feat = len(model.get('features', []))
    test_acc = model.get('meta', {}).get('test_accuracy', 0) * 100
    print(f'  model loaded: {ver} · {n_feat} features · test_acc {test_acc:.1f}%')

    if target_date is None:
        target_date = datetime.now(timezone.utc).date().isoformat()
    # Pull today's contexts with all model features.
    # mlb_game_context has DIFFERENT columns than mlb_game_results (where
    # model was trained). Probe schema first + include only ctx-available
    # features in the SELECT. Features missing from ctx are imputed at
    # predict time using training-set medians.
    features = model['features']
    ctx_probe = requests.get(f'{SB}/rest/v1/mlb_game_context?select=*&limit=1', headers=H_R, timeout=10)
    ctx_cols = set(ctx_probe.json()[0].keys()) if ctx_probe.status_code == 200 and ctx_probe.json() else set()
    ctx_features = [f for f in features if f in ctx_cols]
    print(f'  ctx has {len(ctx_features)}/{len(features)} model features (missing get median-imputed)')
    select_str = 'game_id,home_team,away_team,primary_play,' + ','.join(ctx_features)
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
        params={'select': select_str, 'game_date': f'eq.{target_date}', 'limit': 50},
        headers=H_R, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ ctx fetch {r.status_code}: {r.text[:150]}'); return
    ctxs = r.json()
    print(f'  {len(ctxs)} games for {target_date}')

    updates = []
    for ctx in ctxs:
        pred = predict_home_win(ctx, model)
        pp = ctx.get('primary_play')
        if not isinstance(pp, dict): pp = {}
        current_side = str(pp.get('side', '')).upper() if pp.get('type') == 'ml' else None
        current_tier = pp.get('tier') if pp.get('type') == 'ml' else None
        agrees = current_side and current_side == pred['suggested_side']
        marker = '✓ agrees' if agrees else '⚠ conflicts' if current_side else '- current not ml'
        print(f'  {ctx["away_team"][:20]:20s} @ {ctx["home_team"][:20]:20s}  '
              f'logreg: {pred["suggested_side"]:4s} p={pred["p_home_win"]:.2f} ({pred["suggested_tier"]:6s})  '
              f'| current: {(current_side or "-"):4s}/{(current_tier or "-"):8s}  {marker}')
        if print_only or dry_run: continue

        # Shadow-write into primary_play._logreg_shadow via a partial update
        new_pp = dict(pp)
        new_pp['_logreg_shadow'] = pred
        updates.append({'game_id': ctx['game_id'], 'primary_play': new_pp})

    if updates and not dry_run and not print_only:
        # Batch update via individual PATCH (safer than upsert to preserve other cols)
        succ = 0
        for u in updates:
            pr = requests.patch(f'{SB}/rest/v1/mlb_game_context?game_id=eq.{u["game_id"]}',
                headers=H_W, json={'primary_play': u['primary_play']}, timeout=15)
            if pr.status_code in (200, 204): succ += 1
            else: print(f'    ✗ patch {u["game_id"][:12]} failed: {pr.status_code} {pr.text[:100]}')
        print(f'\n  ✓ shadow-wrote {succ}/{len(updates)} predictions')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--print-only', action='store_true')
    args = ap.parse_args()
    run(target_date=args.date, dry_run=args.dry_run, print_only=args.print_only)
