"""Shadow inference for the v7 total model.

Reads today's slate from mlb_game_context, scores each game with v7 GB,
writes predictions to jerry_cache under key v7_total_shadow_<date>.

Run from the daily cron AFTER mlb_game_context is fully populated for the day.
Pairs with audit_v7_shadow.py to grade nightly performance vs composite.

Usage:
    python shadow_total_inference.py            # Today
    python shadow_total_inference.py 2026-06-24  # Specific date
"""
import os
import sys
import json
import pickle
from datetime import date
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H_R = {'apikey': SK, 'Authorization': f'Bearer {SK}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal,resolution=merge-duplicates'}

MODEL_PATH = Path(__file__).parent / 'models' / 'total_v7_gb.pkl'


def fnum(v):
    try: return float(v)
    except (TypeError, ValueError): return None


# Median fallback values learned from training data (729 games)
# Used when an upstream snapshot field is null so we still produce a score
# rather than skipping the game silently.
DEFAULTS = {
    'home_sp_xera': 4.20, 'away_sp_xera': 4.20,
    'home_pitcher_last_3_era': 4.20, 'away_pitcher_last_3_era': 4.20,
    'home_bullpen_era': 4.10, 'away_bullpen_era': 4.10,
    'home_wrc_plus': 100, 'away_wrc_plus': 100,
    'park_run_factor': 100, 'temperature': 72,
    'projected_total': None,  # if proj is null we MUST skip — it's the model gap anchor
}


def score_game(model, features, ctx):
    """Compute v7 features from a game-context row, return prob_OVER.
    Falls back to training-set medians for missing inputs (except projected_total)."""
    def get(field):
        v = fnum(ctx.get(field))
        if v is not None:
            return v, False
        if field in DEFAULTS and DEFAULTS[field] is not None:
            return DEFAULTS[field], True
        return None, True

    h_xera, _ = get('home_sp_xera')
    a_xera, _ = get('away_sp_xera')
    h_l3, _ = get('home_pitcher_last_3_era')
    a_l3, _ = get('away_pitcher_last_3_era')
    h_bp, _ = get('home_bullpen_era')
    a_bp, _ = get('away_bullpen_era')
    h_wrc, _ = get('home_wrc_plus')
    a_wrc, _ = get('away_wrc_plus')
    park, _ = get('park_run_factor')
    temp, _ = get('temperature')
    proj = fnum(ctx.get('projected_total'))
    line = fnum(ctx.get('close_total')) or fnum(ctx.get('open_total')) or fnum(ctx.get('current_total'))

    if proj is None or line is None:
        return {'error': 'missing_anchor',
                'missing': [n for n, v in [('projected_total', proj), ('line', line)] if v is None]}

    imputed = []
    for fname, val in [('home_sp_xera', ctx.get('home_sp_xera')), ('away_sp_xera', ctx.get('away_sp_xera')),
                        ('home_pitcher_last_3_era', ctx.get('home_pitcher_last_3_era')),
                        ('away_pitcher_last_3_era', ctx.get('away_pitcher_last_3_era')),
                        ('home_bullpen_era', ctx.get('home_bullpen_era')), ('away_bullpen_era', ctx.get('away_bullpen_era')),
                        ('home_wrc_plus', ctx.get('home_wrc_plus')), ('away_wrc_plus', ctx.get('away_wrc_plus')),
                        ('park_run_factor', ctx.get('park_run_factor')), ('temperature', ctx.get('temperature'))]:
        if fnum(val) is None:
            imputed.append(fname)

    feature_map = {
        'sp_xera_min': min(h_xera, a_xera),
        'sp_xera_max': max(h_xera, a_xera),
        'sp_l3_min': min(h_l3, a_l3),
        'sp_l3_max': max(h_l3, a_l3),
        'bp_avg': (h_bp + a_bp) / 2,
        'wrc_avg': (h_wrc + a_wrc) / 2,
        'park_run_factor': park,
        'temperature': temp,
        'gap_proj': proj - line,
        'day_of_week': 0,  # filled below
        'line': line,
    }
    # Compute day_of_week from game_date
    gd = ctx.get('game_date')
    if gd:
        from datetime import datetime
        try:
            feature_map['day_of_week'] = datetime.fromisoformat(gd).weekday()
        except Exception:
            feature_map['day_of_week'] = 0

    X = [[feature_map[f] for f in features]]
    proba = model.predict_proba(X)[0]
    p_over = float(proba[1])

    if p_over <= 0.40:
        rec = 'UNDER (conf)'
    elif p_over >= 0.60:
        rec = 'OVER (conf)'
    elif p_over <= 0.50:
        rec = 'UNDER (lean)'
    else:
        rec = 'OVER (lean)'

    return {
        'p_over': round(p_over, 3),
        'p_under': round(1 - p_over, 3),
        'recommendation': rec,
        'features_used': {k: round(v, 3) if isinstance(v, float) else v for k, v in feature_map.items()},
        'imputed_fields': imputed,
    }


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    print(f'=== v7 shadow inference for {target} ===')

    # Load model
    with open(MODEL_PATH, 'rb') as f:
        payload = pickle.load(f)
    model = payload['model']
    features = payload['features']
    print(f'Loaded model: trained on {payload.get("trained_on", "?")} games, holdout {payload.get("holdout_acc", "?")}%')
    print(f'  conf-UNDER hit rate: {payload.get("conf_under_hit_rate", "?")}% (n={payload.get("conf_under_n", "?")})')
    print(f'  conf-OVER  hit rate: {payload.get("conf_over_hit_rate", "?")}% (n={payload.get("conf_over_n", "?")}) [no edge — do not publish]')

    # Pull today's games
    r = requests.get(
        f'{SU}/rest/v1/mlb_game_context?game_date=eq.{target}&select=*&order=close_total.desc',
        headers=H_R, timeout=15,
    )
    games = r.json()
    print(f'Games on {target}: {len(games)}')

    predictions = {}
    skipped = []
    for g in games:
        if not isinstance(g, dict):
            continue
        gid = str(g.get('game_id') or '?')
        result = score_game(model, features, g)
        if 'error' in result:
            skipped.append(f'{g.get("away_team","?")[:18]}@{g.get("home_team","?")[:18]}: {result["error"]}')
            continue
        predictions[gid] = {
            'away_team': g.get('away_team'),
            'home_team': g.get('home_team'),
            'line': g.get('close_total') or g.get('open_total'),
            'composite_proj': g.get('projected_total'),
            'v7_p_over': result['p_over'],
            'v7_recommendation': result['recommendation'],
            'imputed_fields': result.get('imputed_fields', []),
        }

    print()
    print(f'Scored: {len(predictions)}  Skipped: {len(skipped)}')
    if skipped:
        print('Skipped games:')
        for s in skipped:
            print(f'  {s}')

    print()
    print(f'{"Matchup":>40s} | {"line":>5s} | {"composite":>10s} | {"v7 p_over":>9s} | recommendation')
    print('-' * 100)
    for gid, p in predictions.items():
        m = f'{p["away_team"][:18]}@{p["home_team"][:18]}'
        print(f'  {m:>38s} | {str(p["line"]):>5s} | {str(p["composite_proj"]):>10s} | {p["v7_p_over"]:>9.2f} | {p["v7_recommendation"]}')

    # Write to jerry_cache
    cache_key = f'v7_total_shadow_{target}'
    cache_row = {
        'cache_key': cache_key,
        'sport': 'mlb',
        'game_id': cache_key,  # required NOT NULL; use cache_key as proxy for shadow batch
        'narrative': f'v7 shadow predictions for {target} ({len(predictions)} games)',
        'data': {
            'model_meta': {
                'trained_on': payload.get('trained_on'),
                'holdout_acc': payload.get('holdout_acc'),
                'conf_under_hit_rate': payload.get('conf_under_hit_rate'),
                'conf_under_n': payload.get('conf_under_n'),
                'trained_at': payload.get('trained_at'),
            },
            'predictions_by_game_id': predictions,
            'skipped': skipped,
            'inference_date': target,
        },
    }
    r = requests.post(
        f'{SU}/rest/v1/jerry_cache?on_conflict=cache_key',
        json=cache_row, headers=H_W, timeout=15,
    )
    if r.status_code in (200, 201, 204):
        print()
        print(f'✓ Wrote shadow predictions to jerry_cache[{cache_key}]')
    else:
        print()
        print(f'WARN write failed: {r.status_code} {r.text[:200]}')


if __name__ == '__main__':
    main()
