"""Historical similar-game search (2026-08-07).

Given a game, retrieve the K most similar historical games from
mlb_game_results (sport-universal — NFL/NCAAF/etc. tables plug in via
a per-sport feature registry). Report actual outcomes so we can say:
"in the 30 most similar spots, HOME ML hit 62%, OVER hit 55%."

Complementary to cohort_signals.py which does DISCRETE bucket matching
('v3_tot_loud + home_is_dog'). Similar-game does CONTINUOUS nearest-
neighbor — captures gradient similarity (park 108 vs 96, xERA 2.5 vs
4.0) that discrete cohorts flatten.

Method:
1. Load all resolved historical games with outcome + feature set
2. Extract a normalized feature vector per game (per-sport registry)
3. Compute L2 distance from target game to every historical game
4. Return top-K closest, plus outcome summaries

Sport-universal by SPORT_FEATURE_REGISTRY. MLB features today —
NFL/NCAAF/etc. add their own field list when those sports go live.

Interface:
    from similar_games import find_similar
    result = find_similar(target_game_dict, k=30, sport='MLB')
    # → {
    #   'target_summary': str,
    #   'neighbors': [{game_id, matchup, distance, outcome_dict}, ...],
    #   'outcome_summary': {
    #     'n': int, 'ml_home_pct': float, 'total_over_pct': float,
    #     'rl_home_covered_pct': float, ...
    #   }
    # }

CLI:
    python similar_games.py --game-id <gid> [--k 30]
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
from collections import defaultdict
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
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


# Sport → (results_table, features_dict). Each feature entry:
#   field_name: (weight, is_diff_pair, opposite_field)
# weight = how much this feature counts in distance calc
# is_diff_pair = if True, use ABSOLUTE difference (home vs away is
#                symmetric — a home-favored game is similar to another
#                home-favored game regardless of which teams).
# Add sports here as they come online.
SPORT_FEATURE_REGISTRY = {
    'MLB': {
        'table': 'mlb_game_results',
        # Historical (results) table sometimes uses different column
        # names than the context (target) table. Map results-side name
        # → canonical (context) name so we normalize during load.
        'results_field_aliases': {
            'wind_mph': 'wind_speed',   # results.wind_mph maps to context.wind_speed
            'dome_game': 'is_dome',     # results.dome_game maps to context.is_dome
        },
        # Only fields that exist in BOTH mlb_game_context and
        # mlb_game_results with matching semantics. K% and last_3_era
        # exist in context under longer names; skipped for now.
        'features': {
            # Market context — most predictive
            'close_total':         {'weight': 1.5, 'kind': 'raw'},
            'close_spread':        {'weight': 1.5, 'kind': 'raw'},
            # Environment
            'park_run_factor':     {'weight': 1.0, 'kind': 'raw'},
            'temperature':         {'weight': 0.5, 'kind': 'raw'},
            'wind_speed':          {'weight': 0.7, 'kind': 'raw'},
            'is_dome':             {'weight': 0.8, 'kind': 'bool'},
            # Starting pitching quality
            'home_sp_xera':        {'weight': 1.2, 'kind': 'raw'},
            'away_sp_xera':        {'weight': 1.2, 'kind': 'raw'},
            # Offense
            'home_wrc_plus':       {'weight': 1.0, 'kind': 'raw'},
            'away_wrc_plus':       {'weight': 1.0, 'kind': 'raw'},
            # Bullpen
            'home_bullpen_era':    {'weight': 0.7, 'kind': 'raw'},
            'away_bullpen_era':    {'weight': 0.7, 'kind': 'raw'},
        },
        'outcome_fields': [
            'home_score', 'away_score', 'total_runs', 'home_win',
            'home_spread_covered', 'total_result', 'run_line_result',
        ],
    },
    # NFL/NCAAF/NCAAB/NBA slots to add when those sports go live —
    # same schema, different feature list.
}


def _to_num(v, kind='raw'):
    if v is None: return None
    if kind == 'bool':
        try: return 1.0 if bool(v) else 0.0
        except (TypeError, ValueError): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _compute_norms(games: list, feature_names: list) -> dict:
    """Per-feature mean + std for z-score normalization. Computed over
    the historical corpus so we're comparing apples to apples."""
    norms = {}
    for f in feature_names:
        vals = [g.get(f'_{f}') for g in games if g.get(f'_{f}') is not None]
        if len(vals) < 5:
            norms[f] = (0.0, 1.0)
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        std = math.sqrt(var) if var > 0 else 1.0
        norms[f] = (mean, std)
    return norms


def _extract_features(g: dict, spec: dict) -> None:
    """Populate _feature values on game dict from spec. Mutates g in place."""
    for name, cfg in spec['features'].items():
        v = _to_num(g.get(name), cfg.get('kind', 'raw'))
        g[f'_{name}'] = v


def _distance(target_g: dict, hist_g: dict, spec: dict, norms: dict) -> Optional[float]:
    """Weighted Euclidean distance. Returns None if too many features
    are missing to compare reliably."""
    total, missing = 0.0, 0
    total_weight = 0.0
    for name, cfg in spec['features'].items():
        t = target_g.get(f'_{name}')
        h = hist_g.get(f'_{name}')
        if t is None or h is None:
            missing += 1
            continue
        mean, std = norms.get(name, (0, 1))
        std = std if std > 0 else 1.0
        z_t = (t - mean) / std
        z_h = (h - mean) / std
        w = cfg.get('weight', 1.0)
        total += w * (z_t - z_h) ** 2
        total_weight += w
    if total_weight == 0: return None
    # Require at least half the features to be comparable
    if missing > len(spec['features']) // 2: return None
    return math.sqrt(total / total_weight)


def load_history(sport: str, exclude_game_id: Optional[str] = None) -> list:
    """Pull resolved games with outcomes populated.

    Handles column-name differences between context and results tables
    via spec['results_field_aliases'] (reverse-map: results-side name
    → canonical name from features registry). Fetch under results name,
    store under canonical so downstream comparison is uniform.
    """
    spec = SPORT_FEATURE_REGISTRY.get(sport.upper())
    if not spec: return []
    # Reverse the alias map: canonical → results-side (for the SELECT
    # we need results-side names; for storing we use canonical).
    aliases = spec.get('results_field_aliases', {})  # results_name → canonical
    canon_to_results = {v: k for k, v in aliases.items()}

    all_rows = []
    offset = 0
    # Build select using results-side names
    select_cols = ['game_id', 'game_date', 'home_team', 'away_team']
    for canon in spec['features'].keys():
        select_cols.append(canon_to_results.get(canon, canon))
    select_cols += spec['outcome_fields']

    while True:
        r = requests.get(
            f'{SB}/rest/v1/{spec["table"]}',
            headers={**H, 'Range': f'{offset}-{offset+999}'},
            params={
                'home_score': 'not.is.null',
                'away_score': 'not.is.null',
                'select': ','.join(sorted(set(select_cols))),
                'order': 'game_date.asc',
            },
            timeout=30,
        )
        if r.status_code not in (200, 206): break
        batch = r.json() or []
        if not batch: break
        all_rows.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    if exclude_game_id:
        all_rows = [g for g in all_rows if g.get('game_id') != exclude_game_id]

    # Normalize aliased fields to canonical names before feature extract
    for g in all_rows:
        for results_name, canon in aliases.items():
            if results_name in g and canon not in g:
                g[canon] = g[results_name]
        _extract_features(g, spec)
    return all_rows


def _outcome_summary(neighbors: list, sport: str) -> dict:
    """Aggregate outcomes across neighbors. Computes derived results
    from raw scores + close lines to avoid relying on possibly-null
    pre-computed fields (total_result, home_spread_covered)."""
    if not neighbors: return {'n': 0}
    n_total = len(neighbors)
    ml_dec = home_wins = 0        # ML pushes rare but exist (extras with tied score never happens in MLB)
    tot_dec = tot_over = tot_push = 0
    rl_dec = home_covered = 0
    total_runs_sum = 0.0
    margin_sum = 0.0
    total_runs_n = 0
    for g in neighbors:
        hs = g.get('home_score'); as_ = g.get('away_score')
        if hs is None or as_ is None: continue
        try: hs = int(hs); as_ = int(as_)
        except (TypeError, ValueError): continue
        tot = hs + as_
        total_runs_sum += tot
        margin_sum += (hs - as_)
        total_runs_n += 1
        # ML
        if hs != as_:
            ml_dec += 1
            if hs > as_: home_wins += 1
        # Total (need close_total)
        ct = g.get('close_total')
        if ct is not None:
            try:
                ct_f = float(ct)
                if tot > ct_f: tot_dec += 1; tot_over += 1
                elif tot < ct_f: tot_dec += 1
                else: tot_push += 1
            except (TypeError, ValueError): pass
        # RL (need close_spread; +1.5/-1.5)
        cs = g.get('close_spread')
        if cs is not None and hs != as_:
            try:
                cs_f = float(cs)
                # spread stored as home spread (- = home favorite)
                home_margin = hs - as_
                covered = home_margin + cs_f > 0
                rl_dec += 1
                if covered: home_covered += 1
            except (TypeError, ValueError): pass

    return {
        'n': n_total,
        'ml_home_pct': round(100 * home_wins / ml_dec, 1) if ml_dec else None,
        'ml_n': ml_dec,
        'rl_home_covered_pct': round(100 * home_covered / rl_dec, 1) if rl_dec else None,
        'rl_n': rl_dec,
        'total_over_pct': round(100 * tot_over / tot_dec, 1) if tot_dec else None,
        'total_n': tot_dec,
        'total_push_n': tot_push,
        'avg_total_runs': round(total_runs_sum / total_runs_n, 2) if total_runs_n else None,
        'avg_margin': round(margin_sum / total_runs_n, 2) if total_runs_n else None,
    }


def find_similar(target: dict, k: int = 30, sport: str = 'MLB',
                 history: Optional[list] = None) -> dict:
    """Core interface. See module docstring for return shape."""
    spec = SPORT_FEATURE_REGISTRY.get(sport.upper())
    if not spec:
        return {'error': f'sport not registered: {sport}'}
    if history is None:
        history = load_history(sport, exclude_game_id=target.get('game_id'))
    if not history:
        return {'error': 'no history available'}

    _extract_features(target, spec)
    norms = _compute_norms(history, list(spec['features'].keys()))

    scored = []
    for g in history:
        d = _distance(target, g, spec, norms)
        if d is None: continue
        scored.append((d, g))
    scored.sort(key=lambda x: x[0])

    top = [g for _, g in scored[:k]]
    top_distances = [d for d, _ in scored[:k]]

    # Compact neighbor list for return
    neighbor_list = []
    for (d, g) in scored[:k]:
        neighbor_list.append({
            'game_id': g.get('game_id'), 'date': g.get('game_date'),
            'matchup': f"{g.get('away_team', '?')} @ {g.get('home_team', '?')}",
            'distance': round(d, 3),
            'score': f"{g.get('away_score')}-{g.get('home_score')}",
            'total_result': g.get('total_result'),
            'home_win': g.get('home_win'),
            'covered': g.get('home_spread_covered'),
            'close_total': g.get('close_total'),
            'close_spread': g.get('close_spread'),
        })

    return {
        'target_summary': (f"{target.get('away_team','?')} @ {target.get('home_team','?')} "
                           f"(spread {target.get('close_spread')}, total {target.get('close_total')})"),
        'neighbors': neighbor_list,
        'outcome_summary': _outcome_summary(top, sport),
        'avg_distance': round(sum(top_distances) / len(top_distances), 3) if top_distances else None,
    }


def _fetch_target_game(game_id: str) -> Optional[dict]:
    """Pull target game from mlb_game_context (which has the pre-game
    feature values). Enriches with what's needed for feature vector."""
    spec = SPORT_FEATURE_REGISTRY['MLB']
    select_cols = ['game_id', 'game_date', 'home_team', 'away_team']
    select_cols += list(spec['features'].keys())
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context',
        headers=H,
        params={'game_id': f'eq.{game_id}',
                'select': ','.join(sorted(set(select_cols))),
                'limit': 1},
        timeout=15,
    )
    if r.status_code != 200: return None
    rows = r.json() or []
    return rows[0] if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--game-id', required=True)
    ap.add_argument('--k', type=int, default=30)
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--show-neighbors', type=int, default=10)
    args = ap.parse_args()

    target = _fetch_target_game(args.game_id)
    if not target:
        print(f'target game {args.game_id} not found in mlb_game_context')
        return
    print(f'target: {target.get("away_team")} @ {target.get("home_team")}  ({target.get("game_date")})')
    print(f'   close_total={target.get("close_total")}  close_spread={target.get("close_spread")}  park={target.get("park_run_factor")}')
    print(f'   home xERA={target.get("home_sp_xera")} away xERA={target.get("away_sp_xera")}')
    print()
    print('loading history + computing...')
    result = find_similar(target, k=args.k, sport=args.sport)
    if 'error' in result:
        print(f'ERROR: {result["error"]}')
        return
    s = result['outcome_summary']
    print()
    print(f'=== outcome summary of top {s["n"]} most similar games ===')
    ml = f'{s["ml_home_pct"]}% (n={s["ml_n"]})' if s.get('ml_home_pct') is not None else 'n/a'
    rl = f'{s["rl_home_covered_pct"]}% (n={s["rl_n"]})' if s.get('rl_home_covered_pct') is not None else 'n/a'
    to = f'{s["total_over_pct"]}% (n={s["total_n"]}, pushes {s["total_push_n"]})' if s.get('total_over_pct') is not None else 'n/a'
    print(f'  ML  home wins:      {ml}')
    print(f'  RL  home covered:   {rl}')
    print(f'  TOT over:           {to}')
    if s.get('avg_total_runs') is not None:
        print(f'  avg total runs: {s["avg_total_runs"]}  |  avg margin (home): {s["avg_margin"]:+.2f}')
    print(f'  avg feature distance: {result["avg_distance"]}')
    print()
    print(f'=== top {min(args.show_neighbors, len(result["neighbors"]))} closest neighbors ===')
    for n in result['neighbors'][:args.show_neighbors]:
        marker = ''
        if n['home_win']: marker += ' HW'
        if n['covered']: marker += ' HCov'
        if n['total_result']: marker += f' T{n["total_result"][0]}'
        print(f'  d={n["distance"]:.2f}  {n["date"]}  {n["matchup"][:38]:38s}  {n["score"]}  spr={n["close_spread"]:>4}  tot={n["close_total"]}{marker}')


if __name__ == '__main__':
    main()
