"""NCAAB MC model calibration backtest (2026-08-14).

Session S4b · what we can actually measure with the data we have.

WHY THIS SCOPE (limitation disclosure)
  Our historical ncaab_game_results are outcome-only backfills — home/away
  final scores and win/loss, but NO historical market lines or historical
  KenPom features. Odds API historical is paid-tier; we haven't stored
  historical KenPom snapshots (started 2026-08-14).

  So a true 4-lens ATS backtest against 2024-25 spreads isn't possible.

  What IS possible: pull KenPom's 2024-25 EOY ratings today (one API
  call), reconstruct MC per historical game, and grade the MC LENS
  against actual outcomes on:
    * Direction accuracy — did MC pick the winner?
    * Margin calibration — RMSE of predicted vs actual margin
    * Total calibration — RMSE of predicted vs actual total
    * Bias — does MC systematically over/underrate favorites?

  This validates MC MATH (the sim engine we ship Nov 3). It does NOT
  measure market edge — that requires historical lines.

WHAT WE LEARN
  H1  Overall direction hit rate (MC picks winner)
  H2  Direction hit rate by predicted margin bucket (0-3, 3-6, 6-10, 10+)
  H3  Margin RMSE overall + by predicted margin bucket
  H4  Total RMSE + bias (systematic over/under)
  H5  MC HIGH-CONF (|margin|>5 AND stddev<13.5) direction hit rate
      -> validates HIGH-CONF threshold

CLI
  python ncaab_backtest_mc_calibration.py                    # season 2024-25
  python ncaab_backtest_mc_calibration.py --season 2024-25
  python ncaab_backtest_mc_calibration.py --json out.json
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
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
KENPOM_KEY = os.environ.get('KENPOM_KEY') or os.environ.get('EXPO_PUBLIC_KENPOM_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

LEAGUE_AVG_EFF = 106.0
HFA_POINTS = 3.5
SIGMA_TEAM_SCORE = 9.5
N_SIMS = 2000

CACHE_PATH = Path(__file__).parent / '_kenpom_cache_2024-25.json'


def _fetch_or_cache_ratings(season_year: int) -> dict:
    """{team_name: {adj_oe, adj_de, tempo, adj_em}} — cache 24h to be nice to KenPom."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    if not KENPOM_KEY:
        print('  ✗ KENPOM_KEY missing — cannot fetch ratings')
        return {}
    print(f'  fetching KenPom y={season_year} (paid API — cached to {CACHE_PATH.name})')
    r = requests.get('https://kenpom.com/api.php',
                     params={'endpoint': 'ratings', 'y': str(season_year)},
                     headers={'Authorization': f'Bearer {KENPOM_KEY}'},
                     timeout=30)
    if r.status_code != 200:
        print(f'  ✗ KenPom {r.status_code}: {r.text[:200]}')
        return {}
    ratings = {}
    for t in r.json() or []:
        name = t.get('TeamName')
        if not name: continue
        oe = t.get('AdjOE'); de = t.get('AdjDE')
        tempo = t.get('AdjTempo') or t.get('Tempo')
        em = t.get('AdjEM')
        if oe is None or de is None or tempo is None: continue
        ratings[name] = {
            'adj_oe': float(oe), 'adj_de': float(de),
            'tempo': float(tempo), 'adj_em': float(em) if em is not None else None}
    CACHE_PATH.write_text(json.dumps(ratings, indent=2))
    return ratings


def _load_season_results(season: str) -> list:
    """All completed games with final scores."""
    all_rows = []; offset = 0
    fields = 'game_id,game_date,home_team,away_team,home_score,away_score,home_win'
    while True:
        r = requests.get(
            f'{SB}/rest/v1/ncaab_game_results?select={fields}'
            f'&season=eq.{season}&home_score=not.is.null'
            f'&order=game_date.asc&limit=1000&offset={offset}',
            headers=H_READ, timeout=45)
        if r.status_code != 200:
            print(f'  ✗ {r.status_code}'); break
        batch = r.json() or []
        all_rows.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    return all_rows


def simulate_mc(h_oe, h_de, a_oe, a_de, pace):
    """MC prediction. Neutral site assumed for backtest (many CBB games are)
    to keep the calibration honest — HFA would inflate margin systematically
    without game-level neutral-flag data."""
    home_eff = h_oe * (a_de / LEAGUE_AVG_EFF)
    away_eff = a_oe * (h_de / LEAGUE_AVG_EFF)
    home_expected = home_eff * pace / 100.0 + HFA_POINTS  # keep HFA — most games ARE at home
    away_expected = away_eff * pace / 100.0

    rng = np.random.default_rng()
    hs = np.maximum(rng.normal(home_expected, SIGMA_TEAM_SCORE, N_SIMS), 40.0)
    as_ = np.maximum(rng.normal(away_expected, SIGMA_TEAM_SCORE, N_SIMS), 40.0)
    margins = hs - as_
    totals = hs + as_
    return {
        'mc_p_home': float(np.mean(margins > 0)),
        'mc_expected_margin': float(np.mean(margins)),
        'mc_expected_total': float(np.mean(totals)),
        'mc_stddev_margin': float(np.std(margins)),
        'mc_confidence_high': bool(abs(np.mean(margins)) > 5.0 and np.std(margins) < 13.5),
    }


def grade(row: dict, ratings: dict):
    home = ratings.get(row['home_team'])
    away = ratings.get(row['away_team'])
    if not (home and away): return None
    mc = simulate_mc(home['adj_oe'], home['adj_de'],
                     away['adj_oe'], away['adj_de'],
                     (home['tempo'] + away['tempo']) / 2)
    actual_margin = row['home_score'] - row['away_score']
    actual_total = row['home_score'] + row['away_score']

    return {
        'game_id': row['game_id'],
        'predicted_margin': mc['mc_expected_margin'],
        'actual_margin': actual_margin,
        'margin_err': actual_margin - mc['mc_expected_margin'],
        'predicted_total': mc['mc_expected_total'],
        'actual_total': actual_total,
        'total_err': actual_total - mc['mc_expected_total'],
        'mc_side_correct': (mc['mc_expected_margin'] > 0) == (actual_margin > 0),
        'mc_confidence_high': mc['mc_confidence_high'],
        'mc_p_home': mc['mc_p_home'],
    }


def report(graded: list):
    n = len(graded)
    if not n:
        print('  no graded games'); return
    print(f'\n=== NCAAB MC CALIBRATION BACKTEST · n={n} games (2024-25) ===\n')

    def pct(h, t): return f'{h}/{t} ({100*h/t:.1f}%)' if t else '—'

    # H1: overall direction
    correct = sum(1 for g in graded if g['mc_side_correct'])
    print(f'H1  MC direction accuracy overall        : {pct(correct, n)}')
    print()

    # H2: direction by predicted margin bucket
    print('H2  Direction accuracy by predicted margin bucket')
    buckets = [(0, 3), (3, 6), (6, 10), (10, 20), (20, 100)]
    for lo, hi in buckets:
        sub = [g for g in graded if lo <= abs(g['predicted_margin']) < hi]
        s_correct = sum(1 for g in sub if g['mc_side_correct'])
        print(f'    |predicted| in [{lo:2},{hi:3}): {pct(s_correct, len(sub))}')
    print()

    # H3: margin RMSE
    errs = np.array([g['margin_err'] for g in graded])
    rmse = float(np.sqrt(np.mean(errs**2)))
    mae = float(np.mean(np.abs(errs)))
    mean_bias = float(np.mean(errs))
    print(f'H3  MARGIN calibration')
    print(f'    RMSE                                 : {rmse:.2f} pts')
    print(f'    MAE                                  : {mae:.2f} pts')
    print(f'    Bias (actual - predicted)            : {mean_bias:+.2f} pts (positive = MC underrates home)')
    print()

    # H4: total RMSE + bias
    t_errs = np.array([g['total_err'] for g in graded])
    t_rmse = float(np.sqrt(np.mean(t_errs**2)))
    t_mae = float(np.mean(np.abs(t_errs)))
    t_bias = float(np.mean(t_errs))
    print(f'H4  TOTAL calibration')
    print(f'    RMSE                                 : {t_rmse:.2f} pts')
    print(f'    MAE                                  : {t_mae:.2f} pts')
    print(f'    Bias (actual - predicted)            : {t_bias:+.2f} pts (positive = MC systematically UNDER)')
    print()

    # H5: HIGH-CONF firing rate + hit rate
    hc = [g for g in graded if g['mc_confidence_high']]
    hc_correct = sum(1 for g in hc if g['mc_side_correct'])
    print(f'H5  MC HIGH-CONF calibration')
    print(f'    firing rate                          : {pct(len(hc), n)}')
    print(f'    when fired, direction accuracy       : {pct(hc_correct, len(hc))}')
    print(f'    baseline (all games)                 : {pct(correct, n)}')
    print()

    # Bonus: bin by p_home probability (calibration curve)
    print('BONUS  Predicted p_home vs actual home win rate (calibration curve)')
    prob_bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in prob_bins:
        sub = [g for g in graded if lo <= g['mc_p_home'] < hi]
        home_wins = sum(1 for g in sub if g['actual_margin'] > 0)
        print(f'    p_home [{lo:.1f}, {hi:.1f})    : predicted={sum(g["mc_p_home"] for g in sub)/max(len(sub),1):.2%}, actual={pct(home_wins, len(sub))}')


def run(season: str, json_out: Optional[str] = None):
    print(f'=== ncaab_backtest_mc_calibration · season {season} ===')
    year = 2025 if season == '2024-25' else int(season.split('-')[1]) + 2000
    ratings = _fetch_or_cache_ratings(year)
    print(f'  ratings loaded: {len(ratings)} teams')
    if not ratings: return

    rows = _load_season_results(season)
    print(f'  results loaded: {len(rows)} completed games')

    graded = []
    skipped_name = 0
    for row in rows:
        g = grade(row, ratings)
        if g is None:
            skipped_name += 1
            continue
        graded.append(g)
    print(f'  graded: {len(graded)} (skipped {skipped_name} for team-name mismatch)')

    report(graded)

    if json_out:
        Path(json_out).write_text(json.dumps(graded, indent=2))
        print(f'\n  wrote per-game: {json_out}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', default='2024-25')
    p.add_argument('--json', default=None)
    args = p.parse_args()
    run(args.season, args.json)


if __name__ == '__main__':
    main()
