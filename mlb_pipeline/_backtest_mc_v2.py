"""MC v2 vs v1 backtest — Rich MC vs Thin MC on graded MLB games.

Compares:
  v1_thin  — old Poisson-on-projections (uses model_pred_{home,away}_runs +
             Poisson noise; direction echoed v4)
  v2_rich  — new per-inning simulator with 10 multipliers (SP form, BP gas,
             offense drift, hand splits, park, weather, pitcher-vs-team
             mastery, umpire, defense, plus base pitcher quality)
  actual   — mlb_game_results.home_win / total_result

Reports:
  - Directional hit rate (which side did MC's higher-prob-side land on)
  - Confidence calibration (when MC says 65%, did outcome hit 65%?)
  - MC-vs-market gap size predictive power
  - Ablation-ready structure (opt-flag each multiplier separately in v2)

Filters:
  - Games from 2026-05-30 onwards (L10 RPG populated — needed for offense drift)
  - Both SPs have xERA (needed for pitcher quality mult)
  - Both bullpens have era (needed for BP quality)
  - Graded (home_score not null)

USAGE:
    python _backtest_mc_v2.py                    # full backtest
    python _backtest_mc_v2.py --since 2026-06-15 # tighter window
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from monte_carlo import simulate_game
from monte_carlo_win_prob import simulate_total, simulate_side


def fetch_games(since: str) -> list:
    """Pull graded games with all required MC inputs."""
    rows = []
    off = 0
    filt = (f'&home_score=not.is.null&game_date=gte.{since}'
            f'&home_sp_xera=not.is.null&away_sp_xera=not.is.null'
            f'&home_bullpen_era=not.is.null&away_bullpen_era=not.is.null'
            f'&home_last10_runs_per_game=not.is.null')
    while True:
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_results?select=*{filt}'
            f'&order=game_date.desc&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def run_v1_thin(g: dict) -> dict:
    """Old thin MC — Poisson on projected home/away runs."""
    ct = _f(g.get('close_total'))
    mh = _f(g.get('model_pred_home_runs'))
    ma = _f(g.get('model_pred_away_runs'))
    if mh is None or ma is None:
        jh = _f(g.get('jerry_pred_home_runs'))
        ja = _f(g.get('jerry_pred_away_runs'))
        mh, ma = jh, ja
    if mh is None or ma is None:
        pt = _f(g.get('projected_total'))
        if pt is not None: mh, ma = pt/2, pt/2
    if mh is None or ma is None:
        return {}
    side = simulate_side(mh, ma, n=3000, seed=42)
    tot = simulate_total(mh, ma, ct, n=3000, seed=42) if ct is not None else {}
    return {'p_home_win': side['p_home_win'],
            'p_over': tot.get('p_over') if ct is not None else None,
            'mu_total': tot.get('mean_total') if ct is not None else (mh + ma)}


def run_v2_rich(g: dict) -> dict:
    """New rich MC — per-inning simulator.

    Bridge the mlb_game_results schema (uses home_sp_name) to what
    simulate_game expects (home_pitcher) so the same simulator works
    for both live (mlb_game_context) and backtest (mlb_game_results).
    """
    ct = _f(g.get('close_total'))
    # Alias sp_name -> pitcher so simulate_game's guard doesn't return None
    g_bridged = dict(g)
    g_bridged.setdefault('home_pitcher', g.get('home_sp_name'))
    g_bridged.setdefault('away_pitcher', g.get('away_sp_name'))
    sim = simulate_game(g_bridged, n_iter=3000, line=ct, seed=42)
    if not sim: return {}
    return {'p_home_win': sim.get('p_home_win'),
            'p_over': sim.get('p_over'),
            'mu_total': sim.get('mu_total')}


def grade(mc_result: dict, actual: dict) -> dict:
    """Return per-surface grade: 'W'/'L'/'P' each for side + total."""
    out = {}
    hw = actual.get('home_win')
    if hw is not None and mc_result.get('p_home_win') is not None:
        p = mc_result['p_home_win']
        mc_pick = 'home' if p > 0.5 else 'away'
        outcome = 'home' if hw else 'away'
        out['side'] = 'W' if mc_pick == outcome else 'L'
        out['side_conf'] = max(p, 1-p)
    tr = (actual.get('total_result') or '').lower()   # results table stores 'Over'/'Under' title-case
    ct = _f(actual.get('close_total'))
    if tr and mc_result.get('p_over') is not None and ct is not None:
        p = mc_result['p_over']
        if tr == 'push':
            out['total'] = 'P'
        else:
            mc_pick = 'over' if p > 0.5 else 'under'
            out['total'] = 'W' if mc_pick == tr else 'L'
            out['total_conf'] = max(p, 1-p)
    return out


def summarize(tallies: dict, name: str) -> None:
    print(f'\n=== {name} ===')
    for surface in ('side', 'total'):
        w = tallies[f'{surface}_W']
        l = tallies[f'{surface}_L']
        p = tallies[f'{surface}_P']
        n = w + l
        pct = round(100 * w / max(1, n), 1)
        print(f'  {surface:<6}  {w}-{l}-{p}  = {pct}% (n={n})')
    # Confidence buckets
    for surface in ('side', 'total'):
        buckets = tallies.get(f'{surface}_conf_buckets', {})
        if buckets:
            print(f'  {surface} confidence calibration:')
            for band in sorted(buckets.keys()):
                w, l = buckets[band]
                n = w + l
                pct = round(100 * w / max(1, n), 1)
                print(f'    {band}: {w}-{l} = {pct}% (n={n})')


def audit(games: list) -> None:
    v1 = defaultdict(int); v1['side_conf_buckets'] = defaultdict(lambda: [0,0]); v1['total_conf_buckets'] = defaultdict(lambda: [0,0])
    v2 = defaultdict(int); v2['side_conf_buckets'] = defaultdict(lambda: [0,0]); v2['total_conf_buckets'] = defaultdict(lambda: [0,0])
    disagree_v2_right = 0
    disagree_v1_right = 0
    both_agree = 0

    for i, g in enumerate(games):
        if i and i % 100 == 0:
            print(f'  ...{i}/{len(games)}', file=sys.stderr)
        actual = {'home_win': g.get('home_win'), 'total_result': g.get('total_result'),
                  'close_total': g.get('close_total')}
        r1 = run_v1_thin(g); r2 = run_v2_rich(g)
        g1 = grade(r1, actual); g2 = grade(r2, actual)
        for k, v in g1.items():
            if k.endswith('_conf'):
                surface = k.replace('_conf', '')
                band = '50-59%' if v < 0.60 else '60-69%' if v < 0.70 else '70-79%' if v < 0.80 else '80+%'
                grade_v = g1.get(surface)
                if grade_v == 'W': v1[f'{surface}_conf_buckets'][band][0] += 1
                elif grade_v == 'L': v1[f'{surface}_conf_buckets'][band][1] += 1
            elif v in ('W','L','P'):
                v1[f'{k}_{v}'] += 1
        for k, v in g2.items():
            if k.endswith('_conf'):
                surface = k.replace('_conf', '')
                band = '50-59%' if v < 0.60 else '60-69%' if v < 0.70 else '70-79%' if v < 0.80 else '80+%'
                grade_v = g2.get(surface)
                if grade_v == 'W': v2[f'{surface}_conf_buckets'][band][0] += 1
                elif grade_v == 'L': v2[f'{surface}_conf_buckets'][band][1] += 1
            elif v in ('W','L','P'):
                v2[f'{k}_{v}'] += 1

        # Disagreement analysis on side
        side1 = g1.get('side'); side2 = g2.get('side')
        if side1 and side2:
            v1_pick = 'home' if r1['p_home_win'] > 0.5 else 'away'
            v2_pick = 'home' if r2['p_home_win'] > 0.5 else 'away'
            if v1_pick != v2_pick:
                if side2 == 'W' and side1 == 'L': disagree_v2_right += 1
                elif side1 == 'W' and side2 == 'L': disagree_v1_right += 1
            else:
                both_agree += 1

    summarize(v1, 'v1 THIN (Poisson-on-projections)')
    summarize(v2, 'v2 RICH (per-inning simulator)')
    print(f'\n=== DISAGREEMENT ANALYSIS ({disagree_v2_right + disagree_v1_right + both_agree} scoreable) ===')
    print(f'  both agree on side:     {both_agree}')
    print(f'  disagree, v2 right:     {disagree_v2_right}')
    print(f'  disagree, v1 right:     {disagree_v1_right}')
    total_disagree = disagree_v2_right + disagree_v1_right
    if total_disagree:
        v2_win_rate = round(100 * disagree_v2_right / total_disagree, 1)
        print(f'  v2 win rate on disagreements: {v2_win_rate}% (n={total_disagree})')


def run(since: str = '2026-05-30') -> None:
    print(f'=== MC v2 backtest · since {since} ===')
    games = fetch_games(since)
    print(f'  eligible games: {len(games)}')
    if not games:
        return
    audit(games)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-05-30',
                    help='Start date YYYY-MM-DD (default 2026-05-30)')
    args = ap.parse_args()
    run(since=args.since)


if __name__ == '__main__':
    main()
