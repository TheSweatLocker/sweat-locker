"""NCAAB 2-lens backtest against 2024-25 season (2026-08-14).

Session S4b of NCAAB 5-lens build. Validates the KenPom + MC lens pair
against 1000+ completed games from 2024-25 season. Panel + Jerry are
NEW post-launch — we don't have historical data for them, so this
backtest reports the FLOOR performance (2 lenses only). Production
adds Panel + Jerry as accretive 3rd + 4th lenses.

WHAT WE MEASURE
  H1  MC + KenPom agree on side       → hit rate (validates 2-lens confluence)
  H2  MC + KenPom split               → who wins? (validates dissent policy)
  H3  MC HIGH-CONF firing rate + hit  → calibrates HIGH-CONF threshold
  H4  spread_edge >= 2.0 + agreement  → validates PRIME ML gate
  H5  Total: both lenses agree O/U + edge >= 3 → validates STRONG total
  H6  Total: both agree + edge >= 5   → validates PRIME total
  H7  Tier-simulated: 2-lens PRIME vs STRONG vs LEAN (as resolver would tier)

INPUT
  ncaab_game_results (2024-25 season, has pick-time KenPom features +
  market lines + final scores + pre-graded spread/total results)

OUTPUT
  Console report + optional JSON dump

CLI
  python ncaab_backtest_2lens.py                        # summary
  python ncaab_backtest_2lens.py --json out.json       # dump per-game
  python ncaab_backtest_2lens.py --season 2024-25      # explicit season
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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

LEAGUE_AVG_EFF = 106.0
HFA_POINTS = 3.5
SIGMA_TEAM_SCORE = 9.5
N_SIMS = 2000  # reduced from prod 10k for speed x1000 games


def _load_season(season: str) -> list:
    """Return all ncaab_game_results for season with complete backtest fields."""
    fields = ('game_id,home_team,away_team,home_score,away_score,home_win,'
              'close_spread,close_total,open_spread,open_total,'
              'projected_spread,projected_total,'
              'home_adj_em,away_adj_em,home_adj_oe,home_adj_de,'
              'away_adj_oe,away_adj_de,pace_avg,'
              'spread_result,total_result')
    all_rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/ncaab_game_results?select={fields}'
            f'&season=eq.{season}'
            f'&home_score=not.is.null&close_spread=not.is.null'
            f'&close_total=not.is.null&home_adj_oe=not.is.null'
            f'&pace_avg=not.is.null'
            f'&order=game_date.asc'
            f'&limit=1000&offset={offset}',
            headers=H_READ, timeout=45)
        if r.status_code != 200:
            print(f'  ✗ fetch fail {r.status_code}: {r.text[:200]}')
            break
        batch = r.json() or []
        all_rows.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    return all_rows


def simulate_mc(home_oe, home_de, away_oe, away_de, pace, close_total, neutral=False):
    """MC prediction matching production ncaab_mc_simulator."""
    home_eff = home_oe * (away_de / LEAGUE_AVG_EFF)
    away_eff = away_oe * (home_de / LEAGUE_AVG_EFF)
    home_expected = home_eff * pace / 100.0 + (0.0 if neutral else HFA_POINTS)
    away_expected = away_eff * pace / 100.0

    rng = np.random.default_rng()
    home_scores = np.maximum(rng.normal(home_expected, SIGMA_TEAM_SCORE, N_SIMS), 40.0)
    away_scores = np.maximum(rng.normal(away_expected, SIGMA_TEAM_SCORE, N_SIMS), 40.0)
    margins = home_scores - away_scores
    totals = home_scores + away_scores
    return {
        'mc_p_home': float(np.mean(margins > 0)),
        'mc_expected_margin': float(np.mean(margins)),
        'mc_expected_total': float(np.mean(totals)),
        'mc_stddev_margin': float(np.std(margins)),
        'mc_p_over': float(np.mean(totals > float(close_total))) if close_total else None,
        'mc_confidence_high': bool(
            abs(np.mean(margins)) > 5.0 and np.std(margins) < 13.5),
    }


def grade_game(row: dict) -> dict:
    """Return grading breakdown per hypothesis for one game."""
    # Basic
    home_score = row['home_score']; away_score = row['away_score']
    actual_margin = home_score - away_score
    actual_total = home_score + away_score
    close_spread = float(row['close_spread'])
    close_total = float(row['close_total'])
    proj_spread = float(row['projected_spread']) if row.get('projected_spread') is not None else None

    # KenPom lens votes
    kenpom_side = None
    kenpom_edge = None
    if proj_spread is not None:
        kenpom_side = 'home' if proj_spread > 0 else 'away'
        kenpom_edge = abs(proj_spread + close_spread)  # MLB convention

    # MC lens
    mc = simulate_mc(
        row['home_adj_oe'], row['home_adj_de'],
        row['away_adj_oe'], row['away_adj_de'],
        row['pace_avg'], close_total, neutral=False)
    mc_side = 'home' if mc['mc_p_home'] > 0.5 else 'away'

    # Actual side result (who covered the spread)
    # close_spread is home perspective: negative = home favored
    # home covered if actual_margin > -close_spread
    covered_side = 'home' if (actual_margin + close_spread > 0) else 'away'
    home_moneyline_won = actual_margin > 0

    # Total result
    over_hit = actual_total > close_total

    # Lens agreement
    lens_agree = (kenpom_side == mc_side) if kenpom_side else False
    majority_side = kenpom_side if lens_agree else None

    # Total agreement
    kenpom_total_edge = None
    kenpom_total_side = None
    if row.get('projected_total') is not None:
        kenpom_total_edge = float(row['projected_total']) - close_total
        kenpom_total_side = 'over' if kenpom_total_edge > 0 else 'under'
    mc_total_edge = mc['mc_expected_total'] - close_total
    mc_total_side = 'over' if mc_total_edge > 0 else 'under'
    total_agree = (kenpom_total_side == mc_total_side) if kenpom_total_side else False
    total_edge_max = max(abs(kenpom_total_edge) if kenpom_total_edge else 0,
                         abs(mc_total_edge))

    return {
        'game_id': row['game_id'],
        'lens_agree_side': lens_agree,
        'majority_side': majority_side,
        'kenpom_side': kenpom_side, 'mc_side': mc_side,
        'covered_side': covered_side, 'home_ml_won': home_moneyline_won,
        'kenpom_edge': kenpom_edge,
        'mc_confidence_high': mc['mc_confidence_high'],
        'mc_expected_margin': mc['mc_expected_margin'],
        'kenpom_total_side': kenpom_total_side, 'mc_total_side': mc_total_side,
        'total_agree': total_agree, 'total_edge_max': total_edge_max,
        'over_hit': over_hit,
        'total_edge_kenpom': kenpom_total_edge,
        'total_edge_mc': mc_total_edge,
    }


def report(graded: list):
    n = len(graded)
    print(f'\n=== NCAAB 2-LENS BACKTEST · n={n} games ===\n')

    def pct(hits, total):
        return f'{hits}/{total} ({100*hits/total:.1f}%)' if total else '0/0 (—)'

    # H1: side agreement + majority side coverage
    agreed = [g for g in graded if g['lens_agree_side']]
    agreed_covered = sum(1 for g in agreed if g['majority_side'] == g['covered_side'])
    print(f'H1  Both lenses agree on side       : {pct(len(agreed), n)}')
    print(f'    when agreed, majority covers    : {pct(agreed_covered, len(agreed))}')
    print(f'    (baseline: 50% if random / spread market efficient)')
    print()

    # H2: split — who wins?
    split = [g for g in graded if not g['lens_agree_side'] and g['kenpom_side'] and g['mc_side']]
    kenpom_wins = sum(1 for g in split if g['kenpom_side'] == g['covered_side'])
    mc_wins = sum(1 for g in split if g['mc_side'] == g['covered_side'])
    print(f'H2  Lens split                       : n={len(split)}')
    print(f'    KenPom side covers              : {pct(kenpom_wins, len(split))}')
    print(f'    MC side covers                  : {pct(mc_wins, len(split))}')
    print()

    # H3: MC HIGH-CONF
    hc = [g for g in graded if g['mc_confidence_high']]
    hc_covered = sum(1 for g in hc if g['mc_side'] == g['covered_side'])
    print(f'H3  MC HIGH-CONF firing rate         : {pct(len(hc), n)}')
    print(f'    when HIGH-CONF, MC side covers  : {pct(hc_covered, len(hc))}')
    print()

    # H4: agreement + spread edge >= 2.0 (PRIME ML gate simulation)
    for min_edge in (2.0, 2.5, 3.0):
        strong = [g for g in agreed if g.get('kenpom_edge') and g['kenpom_edge'] >= min_edge]
        strong_hit = sum(1 for g in strong if g['majority_side'] == g['covered_side'])
        print(f'H4  Agreement + kenpom_edge >= {min_edge:.1f}   : {pct(strong_hit, len(strong))}')
    print()

    # H5: Total both agree + edge >= 3
    for min_te in (3.0, 5.0, 7.0):
        te = [g for g in graded if g['total_agree'] and g['total_edge_max'] >= min_te]
        te_hit = sum(1 for g in te if (g['kenpom_total_side'] == 'over') == g['over_hit'])
        print(f'H5  Total agree + max_edge >= {min_te:.1f}    : {pct(te_hit, len(te))}')
    print()

    # H7: tier-simulated buckets (mirrors resolver logic)
    tiers = defaultdict(lambda: {'total': 0, 'covered': 0})
    for g in graded:
        # Simulate resolver tier for ML pick
        if not g['lens_agree_side'] or not g.get('kenpom_edge'):
            continue
        edge = g['kenpom_edge']
        # 2-lens equivalent of "3/4 agreement":  both agree (2/2 = 100%)
        if edge >= 3.0 and g['mc_confidence_high']:
            tier = 'PRIME'
        elif edge >= 2.0:
            tier = 'STRONG'
        elif edge >= 1.0:
            tier = 'LIGHT'
        else:
            continue
        tiers[tier]['total'] += 1
        if g['majority_side'] == g['covered_side']:
            tiers[tier]['covered'] += 1

    print('H7  Simulated resolver tiers (ML side, 2-lens floor)')
    for tier in ('PRIME', 'STRONG', 'LIGHT'):
        t = tiers[tier]
        print(f'    {tier:8}                       : {pct(t["covered"], t["total"])}')
    print()

    # Global baselines for context
    covered_home = sum(1 for g in graded if g['covered_side'] == 'home')
    over_rate = sum(1 for g in graded if g['over_hit'])
    print(f'BASELINES (context):')
    print(f'    Home cover rate (2024-25)       : {pct(covered_home, n)}')
    print(f'    OVER rate (2024-25)             : {pct(over_rate, n)}')


def run(season: str, json_out: Optional[str] = None):
    print(f'=== ncaab_backtest_2lens · season {season} ===')
    rows = _load_season(season)
    print(f'  loaded {len(rows)} completed games with full backtest data')
    if not rows: return

    graded = []
    for i, row in enumerate(rows):
        if i % 200 == 0 and i: print(f'  ... graded {i}')
        try:
            graded.append(grade_game(row))
        except Exception as e:
            print(f'  ° skip {row.get("game_id")}: {e}')

    report(graded)

    if json_out:
        Path(json_out).write_text(json.dumps(graded, indent=2, default=str))
        print(f'\n  wrote per-game: {json_out}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', default='2024-25')
    p.add_argument('--json', default=None)
    args = p.parse_args()
    run(args.season, args.json)


if __name__ == '__main__':
    main()
