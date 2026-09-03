"""NFL ML logistic-regression predictor + shadow-write.

Mirrors mlb_ml_logreg_predict.py for NFL. Predicts p_home_win for each
NFL game in the target window (default: next 8 days) and shadow-writes
into nfl_game_context.primary_play._logreg_shadow.

To go LIVE (replace tier assignment), integrate similar to defensive_gates
MLB override — deferred until Week 1 shadow data validates the model
holds up on 2026 games (training was on 2022-2025).

USAGE:
    python nfl_ml_logreg_predict.py                # today + 8d
    python nfl_ml_logreg_predict.py --date 2026-09-07
    python nfl_ml_logreg_predict.py --print-only
"""
import argparse, json, math, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    p = MODELS_DIR / 'nfl_ml_logreg.json'
    if not p.exists(): return None
    return json.loads(p.read_text())


def _to_float(v):
    if isinstance(v, bool): return 1.0 if v else 0.0
    try: return float(v)
    except (TypeError, ValueError): return None


def _sigmoid(z):
    if z >= 0: ez = math.exp(-z); return 1.0 / (1.0 + ez)
    ez = math.exp(z); return ez / (1.0 + ez)


def predict_home_win(ctx: dict, model: dict) -> dict:
    features = model['features']
    z = model['intercept']
    for i, f in enumerate(features):
        v = _to_float(ctx.get(f))
        if v is None: v = model['imputer_medians'][i]
        scale = model['scaler_scale'][i]
        scaled = (v - model['scaler_mean'][i]) / scale if scale != 0 else 0
        z += model['coefficients'][i] * scaled
    p = _sigmoid(z)
    if p >= 0.65:   tier, side = 'PRIME',  'HOME'
    elif p >= 0.55: tier, side = 'STRONG', 'HOME'
    elif p >= 0.45: tier, side = 'COIN',   'NONE'
    elif p >= 0.35: tier, side = 'STRONG', 'AWAY'
    else:           tier, side = 'PRIME',  'AWAY'
    return {'p_home_win': round(p, 4), 'suggested_side': side, 'suggested_tier': tier,
            'model_version': model.get('version', '')}


def run(target_date=None, dry_run=False, print_only=False):
    model = _load_model()
    if model is None:
        print('  ✗ model not found — run nfl_ml_logreg_train.py first'); return
    ver = model.get('version', '?')[:16]
    print(f'  model loaded: {ver} · {len(model["features"])} features · '
          f'test_acc {model.get("meta", {}).get("test_accuracy", 0)*100:.1f}%')

    if target_date is None:
        target_date = datetime.now(timezone.utc).date().isoformat()
    horizon = (datetime.fromisoformat(target_date).date() + timedelta(days=8)).isoformat()

    features = model['features']
    ctx_probe = requests.get(f'{SB}/rest/v1/nfl_game_context?select=*&limit=1', headers=H_R, timeout=10)
    ctx_cols = set(ctx_probe.json()[0].keys()) if ctx_probe.status_code == 200 and ctx_probe.json() else set()
    ctx_features = [f for f in features if f in ctx_cols]
    print(f'  ctx has {len(ctx_features)}/{len(features)} model features (missing get median-imputed)')

    select_str = 'game_id,home_team,away_team,game_date,primary_play,' + ','.join(ctx_features)
    r = requests.get(f'{SB}/rest/v1/nfl_game_context',
        params={'select': select_str, 'and': f'(game_date.gte.{target_date},game_date.lte.{horizon})',
                'limit': 50, 'order': 'game_date.asc'},
        headers=H_R, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ ctx fetch {r.status_code}: {r.text[:200]}'); return
    ctxs = r.json()
    print(f'  {len(ctxs)} NFL games in window {target_date}→{horizon}')

    updates = []
    for ctx in ctxs:
        pred = predict_home_win(ctx, model)
        pp = ctx.get('primary_play') if isinstance(ctx.get('primary_play'), dict) else {}
        cur_type = str(pp.get('type', '')).lower()
        cur_side = str(pp.get('side', '')).upper() if cur_type == 'ml' else None
        cur_tier = pp.get('tier') if cur_type == 'ml' else None
        agrees = cur_side and cur_side == pred['suggested_side']
        marker = '✓ agrees' if agrees else ('⚠ conflicts' if cur_side else '- non-ml current')
        print(f'  {ctx.get("game_date","?"):>10s}  {(ctx.get("away_team") or "?")[:20]:20s} @ {(ctx.get("home_team") or "?")[:20]:20s}  '
              f'LR: {pred["suggested_side"]:4s} p={pred["p_home_win"]:.2f} ({pred["suggested_tier"]:6s})  '
              f'| current: {(cur_side or "-"):4s}/{(cur_tier or "-"):8s}  {marker}')
        if print_only or dry_run: continue
        new_pp = dict(pp)
        new_pp['_logreg_shadow'] = pred
        updates.append({'game_id': ctx['game_id'], 'primary_play': new_pp})

    if updates and not dry_run and not print_only:
        succ = 0
        for u in updates:
            pr = requests.patch(f'{SB}/rest/v1/nfl_game_context?game_id=eq.{u["game_id"]}',
                headers=H_W, json={'primary_play': u['primary_play']}, timeout=15)
            if pr.status_code in (200, 204): succ += 1
        print(f'\n  ✓ shadow-wrote {succ}/{len(updates)} predictions')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--print-only', action='store_true')
    args = ap.parse_args()
    run(target_date=args.date, dry_run=args.dry_run, print_only=args.print_only)
