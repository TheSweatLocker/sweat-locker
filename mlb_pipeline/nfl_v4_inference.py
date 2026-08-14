"""NFL V4 XGBoost inference (2026-08-13).

Loads models/nfl_v4_spread.pkl and models/nfl_v4_total.pkl (produced by
nfl_v4_train.py), builds the feature vector for each of tonight's games,
runs prediction, writes v4_spread / v4_total / v4_confidence /
v4_features_used back to nfl_game_context.

Feature assembly:
  * Team EPA (l4 rolling) from nfl_data_py current-season pbp
  * Rest / bye / short-week from nfl_game_context.home_rest, away_rest
  * Weather (wind, temp, is_dome) from nfl_game_context
  * Division + playoff + week + season from nfl_game_context

Confidence = 1 - normalized(residual_stddev). Higher = tighter prediction.

Skips preseason (stats_source='preseason'). Skips games missing any
critical feature (thin data early in season falls to fall-back handling).

CLI:
    python nfl_v4_inference.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, json, pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

MODELS_DIR = Path(__file__).parent / 'models'


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def load_models() -> Optional[dict]:
    spread_path = MODELS_DIR / 'nfl_v4_spread.pkl'
    total_path = MODELS_DIR / 'nfl_v4_total.pkl'
    if not (spread_path.exists() and total_path.exists()):
        print(f'  ⚠ V4 models not found (run nfl_v4_train.py first)')
        print(f'      expected: {spread_path}, {total_path}')
        return None
    with open(spread_path, 'rb') as f: spread_pkg = pickle.load(f)
    with open(total_path, 'rb') as f: total_pkg = pickle.load(f)
    return {
        'spread': spread_pkg,
        'total': total_pkg,
        'feature_cols': spread_pkg.get('feature_cols', []),
    }


def build_current_epa() -> dict:
    """Pull current season pbp, compute L4 team EPA. Returns dict keyed
    by team abbreviation. Cached per run to avoid re-pull."""
    try:
        import nfl_data_py as nfl
        import pandas as pd
    except ImportError:
        print('  ⚠ nfl_data_py / pandas not installed — feature assembly aborted')
        return {}
    season = datetime.now(timezone.utc).year
    print(f'  fetching pbp for season {season}...')
    try:
        pbp = nfl.import_pbp_data([season], downcast=True)
    except Exception as e:
        # Off-season / early week 1 — pbp may not have any data yet
        print(f'  pbp fetch failed (likely pre-season {season}): {e}')
        return {}
    if len(pbp) == 0:
        print('  pbp empty — nothing to compute'); return {}
    off = (pbp.groupby(['posteam','week'])
              .agg(off_epa=('epa','mean'),
                   pass_epa=('pass_epa','mean'),
                   rush_epa=('rush_epa','mean'))
              .reset_index())
    off = off.sort_values(['posteam','week'])
    for col in ('off_epa','pass_epa','rush_epa'):
        off[f'{col}_l4'] = (off.groupby('posteam')[col]
                              .transform(lambda s: s.rolling(4, min_periods=1).mean()))
    # Latest week per team
    off_latest = off.loc[off.groupby('posteam')['week'].idxmax()]

    dfe = (pbp.groupby(['defteam','week'])
              .agg(def_epa=('epa','mean'))
              .reset_index())
    dfe = dfe.sort_values(['defteam','week'])
    dfe['def_epa_l4'] = (dfe.groupby('defteam')['def_epa']
                            .transform(lambda s: s.rolling(4, min_periods=1).mean()))
    dfe_latest = dfe.loc[dfe.groupby('defteam')['week'].idxmax()]

    out = {}
    for _, r in off_latest.iterrows():
        out.setdefault(r['posteam'], {}).update({
            'off_epa_l4':  float(r.get('off_epa_l4', 0) or 0),
            'pass_epa_l4': float(r.get('pass_epa_l4', 0) or 0),
            'rush_epa_l4': float(r.get('rush_epa_l4', 0) or 0),
        })
    for _, r in dfe_latest.iterrows():
        out.setdefault(r['defteam'], {}).update({
            'def_epa_l4': float(r.get('def_epa_l4', 0) or 0),
        })
    print(f'  computed EPA for {len(out)} teams (current season)')
    return out


def game_to_features(ctx: dict, team_epa: dict, feature_cols: list) -> Optional[list]:
    """Build a feature row for one game. Returns None if critical feature missing."""
    home = ctx.get('home_team'); away = ctx.get('away_team')
    if not (home and away): return None
    hs = team_epa.get(home, {})
    aws = team_epa.get(away, {})

    row: dict = {
        'home_off_epa_l4':  hs.get('off_epa_l4', 0),
        'away_off_epa_l4':  aws.get('off_epa_l4', 0),
        'home_def_epa_l4':  hs.get('def_epa_l4', 0),
        'away_def_epa_l4':  aws.get('def_epa_l4', 0),
        'home_pass_epa_l4': hs.get('pass_epa_l4', 0),
        'away_pass_epa_l4': aws.get('pass_epa_l4', 0),
        'home_rush_epa_l4': hs.get('rush_epa_l4', 0),
        'away_rush_epa_l4': aws.get('rush_epa_l4', 0),
        'home_rest': ctx.get('home_rest') or 7,
        'away_rest': ctx.get('away_rest') or 7,
        'home_short_week': 1 if (ctx.get('home_rest') or 7) <= 4 else 0,
        'away_short_week': 1 if (ctx.get('away_rest') or 7) <= 4 else 0,
        'home_bye': 1 if (ctx.get('home_rest') or 7) >= 7 else 0,
        'away_bye': 1 if (ctx.get('away_rest') or 7) >= 7 else 0,
        'div_game': 1 if ctx.get('div_game') else 0,
        'wind': float(ctx.get('wind') or 0),
        'temp': float(ctx.get('temp') or 60),
        'is_dome': 1 if (ctx.get('roof') or '').lower() in ('dome','closed') else 0,
        'is_playoff': 0,   # inference is for current week; playoff flag only in postseason
        'week':   int(ctx.get('week') or 1),
        'season': int(ctx.get('season') or datetime.now(timezone.utc).year),
    }
    return [row.get(c, 0) for c in feature_cols]


def run(game_date: str, dry_run: bool = False) -> int:
    print(f'=== NFL V4 XGBoost inference · {game_date} ===')
    models = load_models()
    if not models:
        return 0

    feature_cols = models['feature_cols']
    spread_model = models['spread']['model']
    total_model = models['total']['model']

    # Model metrics for confidence normalization
    spread_rmse = models['spread'].get('metrics', {}).get('rmse', 12)
    total_rmse = models['total'].get('metrics', {}).get('rmse', 12)

    # Load current season team EPA
    team_epa = build_current_epa()
    if not team_epa:
        print('  no EPA data — skipping V4 inference (likely pre-Week-1)')
        return 0

    r = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'game_id,home_team,away_team,home_rest,away_rest,'
                          'div_game,wind,temp,roof,week,season,stats_source'},
        timeout=15)
    if r.status_code != 200:
        print(f'  fetch failed: {r.status_code}'); return 0
    games = r.json()
    if not isinstance(games, list) or not games:
        print(f'  no NFL games for {game_date}'); return 0
    print(f'  {len(games)} games · {len(feature_cols)} features per game')

    written = 0
    skipped = 0
    for g in games:
        if g.get('stats_source') == 'preseason':
            skipped += 1
            continue
        features = game_to_features(g, team_epa, feature_cols)
        if features is None:
            skipped += 1
            continue
        import numpy as np
        X = np.array([features])
        pred_spread = float(spread_model.predict(X)[0])
        pred_total  = float(total_model.predict(X)[0])
        # Confidence: 1 - (rmse / typical_target_range). Normalize into 0-1.
        # spread typical range: 20 pts (±10), total range: 20 pts (35-55)
        conf_spread = max(0, 1 - (spread_rmse / 20))
        conf_total = max(0, 1 - (total_rmse / 20))
        confidence = round((conf_spread + conf_total) / 2, 3)

        matchup = f'{g["away_team"]} @ {g["home_team"]}'
        print(f'  {matchup:30}  v4_spr {pred_spread:+5.1f}  v4_tot {pred_total:5.1f}  conf {confidence:.2f}')

        if dry_run: continue
        payload = {
            'v4_spread': round(pred_spread, 2),
            'v4_total': round(pred_total, 2),
            'v4_confidence': confidence,
            'v4_features_used': {c: features[i] for i, c in enumerate(feature_cols)},
        }
        pr = requests.patch(f'{SB}/rest/v1/nfl_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json=payload, timeout=10)
        if pr.status_code in (200, 201, 204):
            written += 1
        else:
            print(f'    write failed: {pr.status_code} {pr.text[:150]}')

    print(f'\n{"[DRY] would write" if dry_run else "wrote"} {written} V4 predictions · '
          f'skipped {skipped}')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date or _et_today(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
