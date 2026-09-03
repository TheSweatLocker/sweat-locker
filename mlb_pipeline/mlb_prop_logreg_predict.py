"""MLB PROP logistic-regression predictor + shadow-mode writer.

Mirrors mlb_ml_logreg_predict.py for the prop pipeline. For each of
today's resolved-or-live props, compute p_hit + suggested tier and
shadow-write into mlb_pipeline_props.signals._logreg_shadow.

USAGE:
    python mlb_prop_logreg_predict.py                  # today
    python mlb_prop_logreg_predict.py --date 2026-09-04
    python mlb_prop_logreg_predict.py --dry-run
    python mlb_prop_logreg_predict.py --print-only
"""
import argparse, json, math, os, sys
from datetime import datetime, timezone
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
    p = MODELS_DIR / 'mlb_prop_logreg.json'
    if not p.exists(): return None
    return json.loads(p.read_text())


def _to_float(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z); return 1.0 / (1.0 + ez)
    ez = math.exp(z); return ez / (1.0 + ez)


def build_prop_features(prop: dict, model: dict) -> list:
    """Build feature vector for a prop row matching training-time order."""
    NUMERIC_FEATURES = [
        'book_over_odds', 'book_under_odds', 'book_line', 'prop_line',
        'conviction', 'refit_conviction',
        'player_l5_hit_count', 'player_l10_hit_count', 'player_season_hit_pct',
    ]
    features = model['features']
    prop_types_onehot = model['prop_types_onehot']

    # Build in same order as training
    row = []
    for f in NUMERIC_FEATURES:
        if f not in features: continue
        v = _to_float(prop.get(f))
        row.append(v)
    if 'direction_over' in features:
        row.append(1.0 if (prop.get('direction') or '').lower() == 'over' else 0.0)
    if 'stack_alert_bool' in features:
        row.append(1.0 if prop.get('stack_alert') else 0.0)
    for pt in prop_types_onehot:
        if f'ptype_{pt}' in features:
            row.append(1.0 if prop.get('prop_type') == pt else 0.0)
    return row


def predict_hit(prop: dict, model: dict) -> dict:
    features = model['features']
    coefs = model['coefficients']
    intercept = model['intercept']
    medians = model['imputer_medians']
    means = model['scaler_mean']
    scales = model['scaler_scale']

    raw = build_prop_features(prop, model)
    z_sum = intercept
    for i, val in enumerate(raw):
        if val is None: val = medians[i]
        scaled = (val - means[i]) / scales[i] if scales[i] != 0 else 0
        z_sum += coefs[i] * scaled
    p = _sigmoid(z_sum)
    # Match training-time tier bins
    if p >= 0.70:   tier = 'PRIME'
    elif p >= 0.60: tier = 'STRONG'
    elif p >= 0.50: tier = 'LEAN'
    elif p >= 0.40: tier = 'COIN'
    else:           tier = 'FADE'
    return {
        'p_hit': round(p, 4),
        'suggested_tier': tier,
        'model_version': model.get('version', ''),
    }


def run(target_date: str = None, dry_run: bool = False, print_only: bool = False):
    model = _load_model()
    if model is None:
        print('  ✗ model not found — run mlb_prop_logreg_train.py first'); return
    ver = model.get('version', '?')[:16]
    test_acc = model.get('meta', {}).get('test_accuracy', 0) * 100
    print(f'  model loaded: {ver} · {len(model["features"])} features · test_acc {test_acc:.1f}%')

    if target_date is None:
        target_date = datetime.now(timezone.utc).date().isoformat()

    # Pull today's props (all tiers — including SKIP so we can compare on the whole slate)
    r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
        params={'select': 'id,prop_type,direction,player_name,prop_line,book_line,'
                          'book_over_odds,book_under_odds,conviction,refit_conviction,'
                          'player_l5_hit_count,player_l10_hit_count,player_season_hit_pct,'
                          'stack_alert,tier,signals',
                'game_date': f'eq.{target_date}', 'limit': 500},
        headers=H_R, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ props fetch {r.status_code}: {r.text[:150]}'); return
    props = r.json()
    print(f'  {len(props)} props for {target_date}')

    # Summarize predictions by new-tier vs current-tier
    from collections import Counter
    new_tier_dist = Counter()
    upgrades = 0; downgrades = 0
    updates = []
    interesting = []
    for prop in props:
        pred = predict_hit(prop, model)
        new_tier_dist[pred['suggested_tier']] += 1
        cur_tier = (prop.get('tier') or '').upper()
        # Track big divergences
        if pred['suggested_tier'] == 'PRIME' and cur_tier == 'SKIP':
            upgrades += 1
            if len(interesting) < 5:
                interesting.append(('UPGRADE',prop,pred))
        elif pred['suggested_tier'] == 'FADE' and cur_tier in ('PRIME','STRONG'):
            downgrades += 1
            if len(interesting) < 5:
                interesting.append(('FADE-DEMOTE',prop,pred))

        if not (print_only or dry_run):
            sigs = prop.get('signals') or {}
            if not isinstance(sigs, dict): sigs = {}
            sigs['_logreg_shadow'] = pred
            updates.append({'id': prop['id'], 'signals': sigs})

    print(f'\n  New-tier distribution: {dict(new_tier_dist)}')
    print(f'  UPGRADEs (SKIP → PRIME): {upgrades}   DEMOTEs (PRIME/STRONG → FADE): {downgrades}')
    if interesting:
        print(f'\n  Notable divergences:')
        for kind, p, pred in interesting:
            print(f'    [{kind:15s}] {p.get("player_name","?")[:20]:20s} {p.get("prop_type"):12s} '
                  f'cur={p.get("tier","?"):8s} conv={p.get("conviction","?")} → '
                  f'LR p={pred["p_hit"]:.2f} ({pred["suggested_tier"]})')

    if updates and not dry_run and not print_only:
        succ = 0
        for u in updates:
            pr = requests.patch(f'{SB}/rest/v1/mlb_pipeline_props?id=eq.{u["id"]}',
                headers=H_W, json={'signals': u['signals']}, timeout=15)
            if pr.status_code in (200, 204): succ += 1
        print(f'\n  ✓ shadow-wrote {succ}/{len(updates)} predictions')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--print-only', action='store_true')
    args = ap.parse_args()
    run(target_date=args.date, dry_run=args.dry_run, print_only=args.print_only)
