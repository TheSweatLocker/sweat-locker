"""NFL 5-lens backtest (2026-08-14).

Session E validation script. Reconstructs the NFL 5-lens primary_play
resolver against historical games, grades against actual outcomes,
writes rule_shadow_log rows so Session C's auto-promotion can decide
whether NFL_ANTI_CONSENSUS_FADE clears its gate.

DATA SOURCE

  nfl_data_py — schedules, pbp, weekly team stats. Same source
  nfl_v4_train.py already uses. Available seasons: 2020-2024 (full)
  and 2025 (as-graded — regular season completed Feb 2026).

FEATURE RECONSTRUCTION per game (pregame-only, no leakage):

  Rolling L4 team EPA up to (season, week-1) — same shape as V4 training
  Rest days, division flag, roof, weather — from schedule
  Panel projections — deferred (Sleeper/ESPN not queryable historically)
    Backtest runs on 4 lenses (MC, EPA, V3, V4) which is sufficient for
    the anti-consensus rule since it needs a 3-2 split of AVAILABLE lenses.
    The production 5-lens version will have Panel Week 1+.

LENS SIMULATION per game:

  MC          : normal-dist sim from team EPA + HFA (nfl_mc_simulator logic)
  EPA         : proj_spread ≈ epa_delta × pace_factor
  V3          : rest/weather/division adjustments on the above
  V4          : XGBoost predictions from trained model (models/*.pkl)
  Panel       : ⚠ skipped in historical backtest (data not available)

RULES SIMULATED:

  NFL_ANTI_CONSENSUS_FADE — the 3-2 lens split pattern with MC in minority
    Grades: pick the majority side as ML. Compare vs actual game winner.
    Win = majority side won outright. Loss = majority side lost.

  NFL_MC_HIGH_CONF_CHIP — MC >=70% + ≥1 other lens agrees
    Grades: pick MC's side as ML. Compare vs actual.

BACKTEST WRITE PATH

Each fired rule → one rule_shadow_log row with:
  rule_mode='shadow'
  applied=false
  actual_outcome + would_have_hit backfilled inline (we already know)
  event_ts = game_date + 4h so backfill queries find them

CLI:
    python nfl_5lens_backtest.py [--seasons 2020,2021,2022,2023,2024]
                                  [--rules NFL_ANTI_CONSENSUS_FADE,...]
                                  [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, math, random
from datetime import datetime, timedelta, timezone
from typing import Optional
from collections import Counter, defaultdict

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_WRITE = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
           'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# NFL constants — same as nfl_mc_simulator.py
LEAGUE_AVG_PPG = 22.5
HFA_POINTS = 2.5
GAME_STDDEV = 10.5
N_MC_SIMS = 500   # smaller for backtest speed; still stable win-prob within ±2%
MIN_GAMES_FOR_STATS = 4


def _mc_sim(home_off: float, away_off: float, home_def: float, away_def: float,
             n_sims: int = N_MC_SIMS) -> dict:
    home_expected = LEAGUE_AVG_PPG + home_off + away_def + HFA_POINTS
    away_expected = LEAGUE_AVG_PPG + away_off + home_def
    home_expected = max(home_expected, 10.0)
    away_expected = max(away_expected, 10.0)
    home_wins = 0; margin_sum = 0.0; margin_sq = 0.0
    for _ in range(n_sims):
        h = max(random.gauss(home_expected, GAME_STDDEV), 0)
        a = max(random.gauss(away_expected, GAME_STDDEV), 0)
        m = h - a
        margin_sum += m; margin_sq += m*m
        if h > a: home_wins += 1
    mean_m = margin_sum / n_sims
    var_m = (margin_sq / n_sims) - mean_m*mean_m
    return {
        'mc_p_home': home_wins / n_sims,
        'mc_expected_margin': mean_m,
        'mc_confidence_high': abs(mean_m) > 6.0 and math.sqrt(max(var_m, 0)) < 10.0,
    }


def load_v4_models() -> Optional[dict]:
    import pickle
    root = Path(__file__).parent / 'models'
    sp = root / 'nfl_v4_spread.pkl'; tp = root / 'nfl_v4_total.pkl'
    if not (sp.exists() and tp.exists()): return None
    with open(sp, 'rb') as f: s = pickle.load(f)
    with open(tp, 'rb') as f: t = pickle.load(f)
    return {'spread': s, 'total': t, 'feature_cols': s.get('feature_cols', [])}


def build_features_from_row(row: dict, feature_cols: list) -> list:
    """Assemble V4 feature vector from a joined schedule+epa row.
    Missing values default to 0 — matches nfl_v4_inference.py behavior."""
    vals = {c: row.get(c, 0) for c in feature_cols}
    return [vals[c] if vals[c] is not None else 0 for c in feature_cols]


def _side_of(spread_val: Optional[float]) -> Optional[str]:
    if spread_val is None: return None
    try: return 'H' if float(spread_val) > 0 else 'A'
    except (TypeError, ValueError): return None


def backtest(seasons: list, rules: list, dry_run: bool = False) -> dict:
    import pandas as pd
    import numpy as np
    import nfl_data_py as nfl

    print(f'=== NFL 5-lens backtest · seasons {seasons} · rules {rules} ===')

    # 1. Load schedules + pbp for the requested seasons
    print('  loading schedules...')
    schedules = nfl.import_schedules(seasons)
    schedules = schedules[schedules['home_score'].notna() &
                          schedules['away_score'].notna()].copy()
    print(f'    {len(schedules)} completed games')

    print('  loading pbp for EPA aggregation...')
    try:
        pbp = nfl.import_pbp_data(seasons, downcast=True)
    except Exception as e:
        print(f'  pbp load failed: {e}'); return {}

    # Aggregate rolling L4 EPA — offense
    off = (pbp.groupby(['season', 'week', 'posteam'])
             .agg(off_epa=('epa', 'mean'),
                  pass_epa=('pass_epa', 'mean'),
                  rush_epa=('rush_epa', 'mean'))
             .reset_index())
    off = off.sort_values(['posteam', 'season', 'week'])
    for col in ('off_epa', 'pass_epa', 'rush_epa'):
        off[f'{col}_l4'] = (off.groupby(['posteam', 'season'])[col]
                              .transform(lambda s: s.rolling(4, min_periods=1).mean()))

    # Defense EPA
    dfe = (pbp.groupby(['season', 'week', 'defteam'])
             .agg(def_epa=('epa', 'mean'))
             .reset_index())
    dfe = dfe.sort_values(['defteam', 'season', 'week'])
    dfe['def_epa_l4'] = (dfe.groupby(['defteam', 'season'])['def_epa']
                          .transform(lambda s: s.rolling(4, min_periods=1).mean()))

    # For each game, we need PREVIOUS week's L4 (no leakage). Shift week down 1.
    off['week_next'] = off['week'] + 1
    dfe['week_next'] = dfe['week'] + 1

    # Merge into schedules
    schedules = schedules.merge(
        off[['season', 'week_next', 'posteam', 'off_epa_l4', 'pass_epa_l4', 'rush_epa_l4']]
          .rename(columns={'posteam': 'home_team', 'week_next': 'week',
                            'off_epa_l4': 'home_off_epa_l4',
                            'pass_epa_l4': 'home_pass_epa_l4',
                            'rush_epa_l4': 'home_rush_epa_l4'}),
        on=['season', 'week', 'home_team'], how='left')
    schedules = schedules.merge(
        off[['season', 'week_next', 'posteam', 'off_epa_l4', 'pass_epa_l4', 'rush_epa_l4']]
          .rename(columns={'posteam': 'away_team', 'week_next': 'week',
                            'off_epa_l4': 'away_off_epa_l4',
                            'pass_epa_l4': 'away_pass_epa_l4',
                            'rush_epa_l4': 'away_rush_epa_l4'}),
        on=['season', 'week', 'away_team'], how='left')
    schedules = schedules.merge(
        dfe[['season', 'week_next', 'defteam', 'def_epa_l4']]
          .rename(columns={'defteam': 'home_team', 'week_next': 'week',
                            'def_epa_l4': 'home_def_epa_l4'}),
        on=['season', 'week', 'home_team'], how='left')
    schedules = schedules.merge(
        dfe[['season', 'week_next', 'defteam', 'def_epa_l4']]
          .rename(columns={'defteam': 'away_team', 'week_next': 'week',
                            'def_epa_l4': 'away_def_epa_l4'}),
        on=['season', 'week', 'away_team'], how='left')

    # Situational features (V3 territory)
    schedules['home_short_week'] = (schedules['home_rest'] <= 4).astype(int)
    schedules['away_short_week'] = (schedules['away_rest'] <= 4).astype(int)
    schedules['home_bye'] = (schedules['home_rest'] >= 7).astype(int)
    schedules['away_bye'] = (schedules['away_rest'] >= 7).astype(int)
    schedules['is_dome'] = schedules['roof'].isin(['dome', 'closed']).astype(int) if 'roof' in schedules.columns else 0
    schedules['is_playoff'] = (schedules['game_type'] != 'REG').astype(int) if 'game_type' in schedules.columns else 0
    for col in ('wind', 'temp', 'home_rest', 'away_rest'):
        if col in schedules.columns:
            schedules[col] = schedules[col].fillna(0)

    # 2. Load V4 models
    models = load_v4_models()
    if not models:
        print('  ⚠ V4 models not found — run nfl_v4_train.py first. Backtest will use MC+EPA+V3 only.')
    v4_spread_model = models['spread']['model'] if models else None
    v4_total_model = models['total']['model'] if models else None
    v4_feature_cols = models['feature_cols'] if models else []

    # 3. Iterate games — reconstruct lenses + simulate rules
    fired_rows = []
    n_ac_fires = 0; n_ac_wins = 0; n_ac_losses = 0
    n_mchc_fires = 0; n_mchc_wins = 0; n_mchc_losses = 0

    for _, g in schedules.iterrows():
        home_off = g.get('home_off_epa_l4', 0) or 0
        away_off = g.get('away_off_epa_l4', 0) or 0
        home_def_raw = g.get('home_def_epa_l4', 0) or 0
        away_def_raw = g.get('away_def_epa_l4', 0) or 0
        # Convert def_epa (points allowed above avg) to same sign convention as MC uses
        # (positive home_def means home defense is BAD from opponent's perspective — allows more)
        # Approximate — for backtest purposes exact sign matters less than direction.
        mc = _mc_sim(home_off * 5, away_off * 5, home_def_raw * 5, away_def_raw * 5)
        # EPA lens: home advantage = home_off - away_def - (away_off - home_def) + HFA
        epa_spread = ((home_off - away_def_raw) - (away_off - home_def_raw)) * 3.5 + 2.5
        # V3 lens: EPA + situational deltas
        v3_spread = epa_spread
        rest_diff = float(g.get('home_rest', 0) or 0) - float(g.get('away_rest', 0) or 0)
        v3_spread += rest_diff * 0.4
        if g.get('home_bye'): v3_spread += 1.5
        if g.get('away_bye'): v3_spread -= 1.5
        # V4 lens
        v4_spread = None
        if v4_spread_model is not None:
            try:
                feats = build_features_from_row(g.to_dict(), v4_feature_cols)
                import numpy as _np
                v4_spread = float(v4_spread_model.predict(_np.array([feats]))[0])
            except Exception:
                v4_spread = None

        # Assemble sides
        mc_side = 'H' if mc['mc_p_home'] >= 0.5 else 'A'
        lens_sides = {
            'mc':    mc_side,
            'epa':   _side_of(epa_spread),
            'v3':    _side_of(v3_spread),
            'v4':    _side_of(v4_spread),
        }
        lens_sides = {k: v for k, v in lens_sides.items() if v is not None}
        if len(lens_sides) < 3: continue

        # Actual outcome
        home_won = g['home_score'] > g['away_score']
        actual = 'H' if home_won else ('A' if g['home_score'] < g['away_score'] else 'T')
        if actual == 'T': continue

        # ── NFL_ANTI_CONSENSUS_FADE simulation ──────────────────────
        # Rule: if MC is in minority of a 3-N or 4-N split (N>=3),
        # take the majority side.
        if 'NFL_ANTI_CONSENSUS_FADE' in rules:
            counts = Counter(lens_sides.values())
            top_side, top_n = counts.most_common(1)[0]
            if top_n >= 3 and lens_sides['mc'] != top_side:
                # Rule fires — majority side is the pick
                pick_side = top_side
                won = (pick_side == actual)
                n_ac_fires += 1
                if won: n_ac_wins += 1
                else: n_ac_losses += 1
                fired_rows.append({
                    'event_ts': f"{g['gameday']}T20:00:00+00:00" if 'gameday' in g and g['gameday'] else None,
                    'sport': 'NFL',
                    'game_date': str(g['gameday']) if 'gameday' in g and g['gameday'] else None,
                    'game_id': str(g['game_id']) if 'game_id' in g else f"{g['season']}_{g['week']}_{g['away_team']}@{g['home_team']}",
                    'rule_name': 'NFL_ANTI_CONSENSUS_FADE',
                    'rule_mode': 'shadow',
                    'target_table': 'nfl_game_context',
                    'target_id': str(g.get('game_id') or f"backfill_{g['season']}_{g['week']}"),
                    'proposed_action': f'primary_play=LEAN {"HOME" if pick_side == "H" else "AWAY"} ML',
                    'before_state': {'primary_play': None},
                    'after_state': {'primary_play_type': 'ml', 'tier': 'LEAN',
                                    'pick_side': pick_side},
                    'applied': False,
                    'actual_outcome': 'Win' if won else 'Loss',
                    'would_have_hit': won,
                    'outcome_backfilled_at': datetime.now(timezone.utc).isoformat(),
                    'context': {'lens_sides': lens_sides,
                                'majority': top_side,
                                'split': dict(counts),
                                'season': int(g['season']), 'week': int(g['week']),
                                'backtest': True},
                })

        # ── NFL_MC_HIGH_CONF_CHIP simulation ──────────────────────
        if 'NFL_MC_HIGH_CONF_CHIP' in rules and mc['mc_confidence_high']:
            mc_pct = mc['mc_p_home'] if mc_side == 'H' else (1 - mc['mc_p_home'])
            if mc_pct >= 0.70:
                supporting = sum(1 for s in lens_sides.values() if s == mc_side)
                if supporting >= 2:
                    pick_side = mc_side
                    won = (pick_side == actual)
                    n_mchc_fires += 1
                    if won: n_mchc_wins += 1
                    else: n_mchc_losses += 1
                    fired_rows.append({
                        'event_ts': f"{g['gameday']}T20:00:00+00:00" if 'gameday' in g and g['gameday'] else None,
                        'sport': 'NFL',
                        'game_date': str(g['gameday']) if 'gameday' in g and g['gameday'] else None,
                        'game_id': str(g['game_id']) if 'game_id' in g else f"{g['season']}_{g['week']}_{g['away_team']}@{g['home_team']}",
                        'rule_name': 'NFL_MC_HIGH_CONF_CHIP',
                        'rule_mode': 'shadow',
                        'target_table': 'nfl_game_context',
                        'target_id': str(g.get('game_id') or f"backfill_{g['season']}_{g['week']}"),
                        'proposed_action': f'primary_play=STRONG {"HOME" if pick_side == "H" else "AWAY"} ML',
                        'before_state': {'primary_play': None},
                        'after_state': {'primary_play_type': 'ml',
                                        'tier': 'PRIME' if supporting >= 4 else 'STRONG',
                                        'pick_side': pick_side},
                        'applied': False,
                        'actual_outcome': 'Win' if won else 'Loss',
                        'would_have_hit': won,
                        'outcome_backfilled_at': datetime.now(timezone.utc).isoformat(),
                        'context': {'mc_pct': mc_pct, 'supporting': supporting,
                                    'season': int(g['season']), 'week': int(g['week']),
                                    'backtest': True},
                    })

    # 4. Report
    print(f'\n{"═"*60}')
    print(f'  NFL_ANTI_CONSENSUS_FADE: {n_ac_fires} fires · {n_ac_wins}-{n_ac_losses} '
          f'({(100*n_ac_wins/(n_ac_fires) if n_ac_fires else 0):.1f}%)')
    print(f'  NFL_MC_HIGH_CONF_CHIP:   {n_mchc_fires} fires · {n_mchc_wins}-{n_mchc_losses} '
          f'({(100*n_mchc_wins/(n_mchc_fires) if n_mchc_fires else 0):.1f}%)')
    print(f'{"═"*60}')

    # 5. Write shadow log
    if dry_run:
        print(f'\n[DRY] would write {len(fired_rows)} shadow log rows')
    else:
        print(f'\nwriting {len(fired_rows)} shadow log rows...')
        for i in range(0, len(fired_rows), 100):
            chunk = fired_rows[i:i+100]
            pr = requests.post(f'{SB}/rest/v1/rule_shadow_log',
                headers=H_WRITE, json=chunk, timeout=20)
            if pr.status_code not in (200, 201, 204):
                print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')
        print(f'  ✓ wrote')

    return {
        'anti_consensus': {'fires': n_ac_fires, 'wins': n_ac_wins, 'losses': n_ac_losses},
        'mc_high_conf':   {'fires': n_mchc_fires, 'wins': n_mchc_wins, 'losses': n_mchc_losses},
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seasons', default='2020,2021,2022,2023,2024')
    p.add_argument('--rules', default='NFL_ANTI_CONSENSUS_FADE,NFL_MC_HIGH_CONF_CHIP')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    seasons = [int(s.strip()) for s in args.seasons.split(',')]
    rules = [r.strip() for r in args.rules.split(',')]
    backtest(seasons, rules, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
