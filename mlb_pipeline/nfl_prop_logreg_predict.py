"""NFL PROP logistic-regression predictor + shadow-mode writer.

Port of mlb_prop_logreg_predict.py. For every upcoming (game_date >=
today) NFL prop, compute p_hit from models/nfl_prop_logreg.json and
shadow-write it into nfl_pipeline_props.signals._lr_prop_p (0-1 float,
4 decimals). Also stashes the fuller {p_hit, suggested_tier,
model_version} under signals._lr_prop_shadow for observability.

NOT wired to gate anything today — this only populates the shadow
field per Andy's directive. Real promotion (gating PRIME behind
p_lr >= 0.60) happens in a follow-up once n>=800 graded rows exist.

Idempotent: each call re-computes and overwrites, preserving all
other signals.* keys.

USAGE:
    python nfl_prop_logreg_predict.py                  # today + upcoming
    python nfl_prop_logreg_predict.py --date 2026-09-21
    python nfl_prop_logreg_predict.py --dry-run
    python nfl_prop_logreg_predict.py --print-only
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

# Must match training-time NUMERIC_FEATURES ordering.
NUMERIC_FEATURES = [
    'conviction', 'refit_conviction',
    'book_line', 'book_over_odds', 'book_under_odds',
    'player_l5_hit_count', 'player_l10_hit_count', 'player_season_hit_pct',
    'player_l10_extreme_flag',
]


def _load_model():
    p = MODELS_DIR / 'nfl_prop_logreg.json'
    if not p.exists(): return None
    return json.loads(p.read_text())


def _to_float(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try: return float(v)
    except (TypeError, ValueError): return None


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z); return 1.0 / (1.0 + ez)
    ez = math.exp(z); return ez / (1.0 + ez)


def build_prop_features(prop: dict, model: dict) -> list:
    """Emit values in the SAME order the model was fit — walk
    features[] from the model file, look up each name."""
    features = model['features']
    row = []
    for f in features:
        if f == 'direction_over':
            row.append(1.0 if (prop.get('direction') or '').lower() == 'over' else 0.0)
        elif f.startswith('ptype_'):
            pt = f[len('ptype_'):]
            row.append(1.0 if prop.get('prop_type') == pt else 0.0)
        else:
            # numeric feature straight off the row
            row.append(_to_float(prop.get(f)))
    return row


def predict_hit(prop: dict, model: dict) -> dict:
    # STUB model — cold-start not-ready-to-train fallback.
    if model.get('stub'):
        return {'p_hit': 0.5, 'suggested_tier': 'COIN',
                'model_version': model.get('version', ''), 'stub': True}
    features   = model['features']
    coefs      = model['coefficients']
    intercept  = model['intercept']
    medians    = model['imputer_medians']
    means      = model['scaler_mean']
    scales     = model['scaler_scale']

    raw = build_prop_features(prop, model)
    z_sum = intercept
    for i, val in enumerate(raw):
        if val is None: val = medians[i]
        scaled = (val - means[i]) / scales[i] if scales[i] != 0 else 0
        z_sum += coefs[i] * scaled
    p = _sigmoid(z_sum)
    # Same tier bins as MLB
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
        print('  ✗ model not found — run nfl_prop_logreg_train.py first'); return
    ver = model.get('version', '?')[:16]
    if model.get('stub'):
        print(f'  ⚠ STUB model loaded ({ver}) — writing p=0.5 for observation. '
              f'Reason: {model.get("reason", "?")}')
    else:
        test_acc = model.get('meta', {}).get('test_accuracy', 0) * 100
        lift = model.get('meta', {}).get('lift_pp', 0)
        print(f'  model loaded: {ver} · {len(model["features"])} features · '
              f'test_acc {test_acc:.1f}% (lift {lift:+.1f}pp)')

    if target_date is None:
        target_date = datetime.now(timezone.utc).date().isoformat()

    # Pull upcoming props (game_date >= target_date). Paginate.
    props = []
    select_cols = ('id,prop_type,direction,player_name,book_line,book_over_odds,'
                   'book_under_odds,conviction,refit_conviction,'
                   'player_l5_hit_count,player_l10_hit_count,player_season_hit_pct,'
                   'player_l10_extreme_flag,tier,game_date,signals')
    for off in range(0, 20000, 1000):
        r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props',
            params={'select': select_cols,
                    'game_date': f'gte.{target_date}',
                    'limit': 1000, 'offset': off, 'order': 'game_date.asc'},
            headers=H_R, timeout=30)
        if r.status_code != 200:
            print(f'  ✗ props fetch {r.status_code}: {r.text[:150]}'); return
        chunk = r.json()
        if not isinstance(chunk, list) or not chunk: break
        props.extend(chunk)
        if len(chunk) < 1000: break
    print(f'  {len(props)} upcoming props (from {target_date})')
    if not props:
        return

    from collections import Counter
    tier_dist = Counter()
    p_hist = Counter()  # rough bins for logging
    updates = []
    interesting = []
    for prop in props:
        pred = predict_hit(prop, model)
        tier_dist[pred['suggested_tier']] += 1
        # crude histogram
        bin_key = f'{int(pred["p_hit"]*10)/10:.1f}'
        p_hist[bin_key] += 1

        cur_tier = (prop.get('tier') or '').upper()
        if pred['suggested_tier'] in ('PRIME','STRONG') and cur_tier == 'SKIP':
            if len(interesting) < 5:
                interesting.append(('UPGRADE', prop, pred))
        elif pred['suggested_tier'] == 'FADE' and cur_tier in ('PRIME','STRONG'):
            if len(interesting) < 5:
                interesting.append(('FADE-DEMOTE', prop, pred))

        if not (print_only or dry_run):
            sigs = prop.get('signals') or {}
            if not isinstance(sigs, dict): sigs = {}
            # Primary shadow field per Andy's spec.
            sigs['_lr_prop_p'] = pred['p_hit']
            # Fuller companion payload for observability.
            sigs['_lr_prop_shadow'] = pred
            updates.append({'id': prop['id'], 'signals': sigs})

    print(f'\n  Suggested-tier distribution: {dict(tier_dist)}')
    print(f'  p_hit histogram (bin lower-edge): '
          f'{dict(sorted(p_hist.items()))}')
    if interesting:
        print(f'\n  Notable divergences (LR vs current tier):')
        for kind, p, pred in interesting:
            print(f'    [{kind:12s}] {(p.get("player_name") or "?")[:22]:22s} '
                  f'{p.get("prop_type",""):26s} cur={p.get("tier","?"):8s} '
                  f'conv={p.get("conviction","?")} → LR p={pred["p_hit"]:.3f} '
                  f'({pred["suggested_tier"]})')

    if updates and not dry_run and not print_only:
        succ = 0
        for u in updates:
            pr = requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{u["id"]}',
                headers=H_W, json={'signals': u['signals']}, timeout=15)
            if pr.status_code in (200, 204): succ += 1
        print(f'\n  ✓ shadow-wrote {succ}/{len(updates)} predictions to '
              f'signals._lr_prop_p')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--print-only', action='store_true')
    args = ap.parse_args()
    run(target_date=args.date, dry_run=args.dry_run, print_only=args.print_only)
