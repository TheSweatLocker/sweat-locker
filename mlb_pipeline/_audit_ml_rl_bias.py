"""
_audit_ml_rl_bias.py — Same bias diagnosis we did for totals, now for ML/RL.

Total's diagnosis surfaced a 2.24:1 OVER:UNDER tier ratio causing the
resolver to ride engine bias. Question: does ML/RL have a similar bias
(HOME vs AWAY, or FAV vs DOG)?

For each game:
  - Count STRONG_EDGE+LOCK cohorts firing for v3_ml home / away
  - Same for v4_ml, conf_ml, v3_rl, v4_rl
  - Cross-reference with actual home_win / run_line_result
  - Bin games by cohort net deviation, see if it predicts outcomes

Output: per-play-type baseline averages + predictive-power bins.
"""
import sys
import os
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

import requests
from cohort_signals import evaluate_game_for_play
from _backtest_monte_carlo import _translate_result_to_ctx

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}


def pull_results(date_from='2026-05-11'):
    all_rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results',
            headers={**HEADERS, 'Range-Unit': 'items', 'Range': f'{offset}-{offset+999}'},
            params={'select': '*', 'game_date': f'gte.{date_from}',
                    'home_score': 'not.is.null', 'order': 'game_date.asc'},
        )
        rows = r.json() if r.status_code in (200, 206) else []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return all_rows


def cnt(ctx, play, direction):
    m = evaluate_game_for_play(ctx, play, direction) or []
    return len([x for x in m
                if x.get('tier') in ('LOCK', 'STRONG_EDGE')
                and not x.get('id', '').endswith('|any')])


def main():
    games = pull_results()
    print(f'Pulled {len(games)} graded games')

    # Per-play-type baselines
    plays = ('v3_ml', 'v4_ml', 'conf_ml', 'v3_rl', 'v4_rl')
    by_play = {p: {'home_sum': 0, 'away_sum': 0, 'n': 0} for p in plays}

    # For predictive power test
    ml_data = []   # (home_total_strong, away_total_strong, home_won)
    rl_data = []   # (home_total_strong, away_total_strong, home_covered_rl)

    for g in games:
        ctx = _translate_result_to_ctx(g)
        if not ctx.get('home_pitcher') or not ctx.get('away_pitcher'):
            continue
        actual_home_win = bool(g.get('home_win'))
        rl_result = g.get('run_line_result')

        # ML cohort counts (sum across all ML play types: v3_ml + v4_ml + conf_ml + jerry_ml)
        ml_home = 0
        ml_away = 0
        for p in ('v3_ml', 'v4_ml', 'conf_ml'):
            h = cnt(ctx, p, 'home')
            a = cnt(ctx, p, 'away')
            ml_home += h
            ml_away += a
            by_play[p]['home_sum'] += h
            by_play[p]['away_sum'] += a
            by_play[p]['n'] += 1

        # RL cohort counts
        rl_home = 0
        rl_away = 0
        for p in ('v3_rl', 'v4_rl'):
            h = cnt(ctx, p, 'home')
            a = cnt(ctx, p, 'away')
            rl_home += h
            rl_away += a
            by_play[p]['home_sum'] += h
            by_play[p]['away_sum'] += a
            by_play[p]['n'] += 1

        ml_data.append((ml_home, ml_away, actual_home_win))
        rl_data.append((rl_home, rl_away, rl_result))

    # Per-play-type baselines
    print()
    print('═══════════════════════════════════════════════════════════════')
    print('  PER-PLAY-TYPE BASELINE COHORT COUNTS')
    print('═══════════════════════════════════════════════════════════════')
    print(f"{'PLAY':<12}{'AVG HOME':<11}{'AVG AWAY':<11}{'NET (H-A)':<11}{'RATIO H:A'}")
    print('-' * 70)
    for p in plays:
        d = by_play[p]
        n = d['n']
        if n == 0:
            continue
        ah = d['home_sum'] / n
        aa = d['away_sum'] / n
        ratio = (ah / aa) if aa > 0 else float('inf')
        net = ah - aa
        print(f"{p:<12}{ah:<11.2f}{aa:<11.2f}{net:+.2f}     {ratio:.2f}:1")

    print()
    print(f"AGGREGATE BIAS CHECK:")
    print(f"  Total ML home cohorts/game: {sum(by_play[p]['home_sum'] for p in ('v3_ml','v4_ml','conf_ml')) / by_play['v3_ml']['n']:.2f}")
    print(f"  Total ML away cohorts/game: {sum(by_play[p]['away_sum'] for p in ('v3_ml','v4_ml','conf_ml')) / by_play['v3_ml']['n']:.2f}")
    print(f"  Total RL home cohorts/game: {sum(by_play[p]['home_sum'] for p in ('v3_rl','v4_rl')) / by_play['v3_rl']['n']:.2f}")
    print(f"  Total RL away cohorts/game: {sum(by_play[p]['away_sum'] for p in ('v3_rl','v4_rl')) / by_play['v3_rl']['n']:.2f}")

    # Predictive power test for ML
    print()
    print('═══════════════════════════════════════════════════════════════')
    print('  ML COHORT NET vs ACTUAL HOME-WIN RATE')
    print('═══════════════════════════════════════════════════════════════')
    print(f"  Note: cohorts here are aggregated across v3_ml + v4_ml + conf_ml")
    print(f"  baseline 'home cohorts per game' is the average across all games")
    print()

    avg_h = sum(d[0] for d in ml_data) / len(ml_data)
    avg_a = sum(d[1] for d in ml_data) / len(ml_data)
    print(f"  ML avg HOME cohorts: {avg_h:.2f}")
    print(f"  ML avg AWAY cohorts: {avg_a:.2f}")
    print(f"  Baseline net: {avg_h - avg_a:+.2f}")
    print(f"  Actual home-win rate: {100*sum(d[2] for d in ml_data) / len(ml_data):.1f}%")
    print()

    # Bins on RAW net (home - away)
    print('RAW ML COHORT NET (home - away):')
    print(f'{"BIN":<24}{"GAMES":<8}{"HOME WINS":<11}{"HOME WIN %":<12}')
    print('-' * 65)
    raw_bins = [(-100, -3, 'net ≤ -3 (AWAY skew)'),
                (-3, -1, 'net -3 to -1'),
                (-1, 1, 'net -1 to +1 (near baseline)'),
                (1, 3, 'net +1 to +3'),
                (3, 6, 'net +3 to +6 (HOME skew)'),
                (6, 100, 'net ≥ +6 (HEAVY HOME)')]
    for low, high, label in raw_bins:
        bin_data = [d for d in ml_data if low <= (d[0] - d[1]) < high]
        n = len(bin_data)
        if n == 0:
            continue
        wins = sum(1 for d in bin_data if d[2])
        pct = 100 * wins / n
        print(f'{label:<24}{n:<8}{wins:<11}{pct:.1f}%')

    print()
    # Bins on BASELINE-NORMALIZED deviation
    print(f'BASELINE-NORMALIZED ML DEVIATION (subtract baseline {avg_h:.1f}/{avg_a:.1f}):')
    print(f'{"BIN":<28}{"GAMES":<8}{"HOME WINS":<11}{"HOME WIN %":<12}')
    print('-' * 65)
    dev_bins = [(-100, -3, 'dev ≤ -3 (AWAY signal)'),
                (-3, -1, 'dev -3 to -1'),
                (-1, 1, 'dev -1 to +1 (at baseline)'),
                (1, 3, 'dev +1 to +3'),
                (3, 6, 'dev +3 to +6 (HOME signal)'),
                (6, 100, 'dev ≥ +6')]
    for low, high, label in dev_bins:
        bin_data = []
        for d in ml_data:
            deviation = (d[0] - avg_h) - (d[1] - avg_a)
            if low <= deviation < high:
                bin_data.append(d)
        n = len(bin_data)
        if n == 0:
            continue
        wins = sum(1 for d in bin_data if d[2])
        pct = 100 * wins / n
        flag = ' ← signal' if abs(pct - 50) >= 8 and n >= 15 else ''
        print(f'{label:<28}{n:<8}{wins:<11}{pct:.1f}%{flag}')

    # Same for RL
    print()
    print('═══════════════════════════════════════════════════════════════')
    print('  RL COHORT NET vs RUN-LINE OUTCOMES')
    print('═══════════════════════════════════════════════════════════════')
    avg_h_rl = sum(d[0] for d in rl_data) / len(rl_data)
    avg_a_rl = sum(d[1] for d in rl_data) / len(rl_data)
    home_covered = sum(1 for d in rl_data if d[2] == 'home')
    away_covered = sum(1 for d in rl_data if d[2] == 'away')
    pushes = sum(1 for d in rl_data if d[2] in ('push', None))
    print(f"  RL avg HOME cohorts: {avg_h_rl:.2f}")
    print(f"  RL avg AWAY cohorts: {avg_a_rl:.2f}")
    print(f"  Baseline net: {avg_h_rl - avg_a_rl:+.2f}")
    print(f"  Actual RL home-cover: {home_covered}/{len(rl_data)} = {100*home_covered/len(rl_data):.1f}%")
    print(f"  Actual RL away-cover: {away_covered}/{len(rl_data)} = {100*away_covered/len(rl_data):.1f}%")
    print(f"  Pushes: {pushes}")


if __name__ == '__main__':
    main()
