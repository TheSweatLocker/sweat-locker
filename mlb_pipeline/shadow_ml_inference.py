"""Shadow inference for the v1 ML model.

Reads today's slate from mlb_game_context, scores each game with XGBoost,
writes predictions to jerry_cache under key ml_v1_shadow_<date>.
"""
import os
import sys
import pickle
from datetime import date, datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H_R = {'apikey': SK, 'Authorization': f'Bearer {SK}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal,resolution=merge-duplicates'}

MODEL_PATH = Path(__file__).parent / 'models' / 'ml_v1_xgb.pkl'

DEFAULTS = {
    'home_sp_xera': 4.20, 'away_sp_xera': 4.20,
    'home_pitcher_last_3_era': 4.20, 'away_pitcher_last_3_era': 4.20,
    'home_sp_k_pct': 22, 'away_sp_k_pct': 22,
    'home_bullpen_era': 4.10, 'away_bullpen_era': 4.10,
    'home_wrc_plus': 100, 'away_wrc_plus': 100,
    'home_ops': 0.720, 'away_ops': 0.720,
    'park_run_factor': 100,
}


def fnum(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def implied_p(ml):
    v = fnum(ml)
    if v is None: return None
    if v < 0: return -v / (-v + 100)
    return 100 / (v + 100)


def score_game(model, features, ctx):
    def get(field):
        v = fnum(ctx.get(field))
        if v is not None:
            return v
        return DEFAULTS.get(field)

    h_xera, a_xera = get('home_sp_xera'), get('away_sp_xera')
    h_l3, a_l3 = get('home_pitcher_last_3_era'), get('away_pitcher_last_3_era')
    h_kpct, a_kpct = get('home_sp_k_pct'), get('away_sp_k_pct')
    h_bp, a_bp = get('home_bullpen_era'), get('away_bullpen_era')
    h_wrc, a_wrc = get('home_wrc_plus'), get('away_wrc_plus')
    h_ops, a_ops = get('home_ops'), get('away_ops')
    park = get('park_run_factor')

    proj = fnum(ctx.get('projected_spread'))
    v4 = fnum(ctx.get('model_pred_spread'))
    jerry = fnum(ctx.get('jerry_pred_spread'))
    close_spread = fnum(ctx.get('close_spread'))
    home_ml = fnum(ctx.get('home_ml_close')) or fnum(ctx.get('home_ml_odds'))

    if proj is None or close_spread is None or home_ml is None:
        return {'error': 'missing_anchor',
                'missing': [n for n, v in [('projected_spread', proj), ('close_spread', close_spread), ('home_ml', home_ml)] if v is None]}

    # Use proj as fallback for v4 / jerry if they're missing
    v4 = v4 if v4 is not None else proj
    jerry = jerry if jerry is not None else proj
    spreads = [proj, v4, jerry]
    spread_avg = sum(spreads) / 3
    spread_std = (sum((s - spread_avg) ** 2 for s in spreads) / 3) ** 0.5

    gd = ctx.get('game_date')
    dow = 0
    if gd:
        try: dow = datetime.fromisoformat(gd).weekday()
        except Exception: pass

    feature_map = {
        'sp_xera_diff': a_xera - h_xera,
        'sp_l3_diff': a_l3 - h_l3,
        'sp_k_pct_diff': h_kpct - a_kpct,
        'bp_diff': a_bp - h_bp,
        'wrc_diff': h_wrc - a_wrc,
        'ops_diff': h_ops - a_ops,
        'spread_proj': proj,
        'market_p_home': implied_p(home_ml) or 0.5,
        'close_spread_signed': close_spread,
        'park_run_factor': park,
        'day_of_week': dow,
        'spread_avg': spread_avg,
        'spread_std': spread_std,
        'all3_home': int(proj > 0.3 and v4 > 0.3 and jerry > 0.3),
        'all3_away': int(proj < -0.3 and v4 < -0.3 and jerry < -0.3),
        'spread_v4': v4,
        'spread_jerry': jerry,
    }
    X = [[feature_map[f] for f in features]]
    proba = model.predict_proba(X)[0]
    p_home = float(proba[1])

    if p_home >= 0.60:
        rec = 'HOME (conf)'
    elif p_home <= 0.40:
        rec = 'AWAY (conf — log only, no edge)'
    elif p_home > 0.50:
        rec = 'HOME (lean — log only)'
    else:
        rec = 'AWAY (lean — log only)'

    return {
        'p_home': round(p_home, 3),
        'p_away': round(1 - p_home, 3),
        'recommendation': rec,
        'features_used': {k: round(v, 3) if isinstance(v, float) else v for k, v in feature_map.items()},
    }


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    print(f'=== ML v1 shadow inference for {target} ===')

    with open(MODEL_PATH, 'rb') as f:
        payload = pickle.load(f)
    model = payload['model']
    features = payload['features']
    print(f'Loaded model: trained on {payload.get("trained_on", "?")} games, holdout {payload.get("holdout_acc", "?")}%')
    print(f'  conf-HOME hit rate: {payload.get("conf_home_hit_rate", "?")}% (n={payload.get("conf_home_n", "?")})')
    print(f'  conf-AWAY hit rate: {payload.get("conf_away_hit_rate", "?")}% (n={payload.get("conf_away_n", "?")}) [no edge — log only]')

    r = requests.get(
        f'{SU}/rest/v1/mlb_game_context?game_date=eq.{target}&select=*&order=close_total.desc',
        headers=H_R, timeout=15,
    )
    games = r.json()
    print(f'Games on {target}: {len(games)}')

    predictions = {}
    skipped = []
    for g in games:
        if not isinstance(g, dict): continue
        gid = str(g.get('game_id') or '?')
        result = score_game(model, features, g)
        if 'error' in result:
            skipped.append(f'{g.get("away_team","?")[:18]}@{g.get("home_team","?")[:18]}: {result["error"]}')
            continue
        predictions[gid] = {
            'away_team': g.get('away_team'),
            'home_team': g.get('home_team'),
            'home_ml': g.get('home_ml_close') or g.get('home_ml_odds'),
            'away_ml': g.get('away_ml_close') or g.get('away_ml_odds'),
            'close_spread': g.get('close_spread'),
            'composite_spread': g.get('projected_spread'),
            'v1_p_home': result['p_home'],
            'v1_recommendation': result['recommendation'],
        }

    print(f'Scored: {len(predictions)}  Skipped: {len(skipped)}')
    print()
    print(f'{"Matchup":>40s} | {"close_sp":>8s} | {"home_ml":>8s} | {"v1 p_home":>10s} | recommendation')
    print('-' * 110)
    for gid, p in predictions.items():
        m = f'{p["away_team"][:18]}@{p["home_team"][:18]}'
        print(f'  {m:>38s} | {str(p["close_spread"]):>8s} | {str(p["home_ml"]):>8s} | {p["v1_p_home"]:>10.2f} | {p["v1_recommendation"]}')

    cache_key = f'ml_v1_shadow_{target}'
    cache_row = {
        'cache_key': cache_key,
        'sport': 'mlb',
        'game_id': cache_key,
        'narrative': f'v1 ML shadow predictions for {target} ({len(predictions)} games)',
        'data': {
            'model_meta': {
                'trained_on': payload.get('trained_on'),
                'holdout_acc': payload.get('holdout_acc'),
                'conf_home_hit_rate': payload.get('conf_home_hit_rate'),
                'conf_home_n': payload.get('conf_home_n'),
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
        print(f'OK Wrote shadow predictions to jerry_cache[{cache_key}]')
    else:
        print()
        print(f'WARN write failed: {r.status_code} {r.text[:200]}')


if __name__ == '__main__':
    main()
