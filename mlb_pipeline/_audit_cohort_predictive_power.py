"""
Test whether raw cohort net actually predicts game outcomes,
and whether baseline-normalized net does better.

The question: when the cohort engine says "+11 net OVER," does the
game actually go OVER more often than baseline 52%? Or is +11 just
the engine's resting bias?

Bin games by cohort net (raw and baseline-adjusted), compute actual
OVER hit rate in each bin, see if there's signal.
"""
import sys
import os
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

import requests
from cohort_signals import evaluate_game_for_play

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}


def adapt(g):
    return {
        'home_team': g.get('home_team'), 'away_team': g.get('away_team'),
        'home_pitcher': g.get('home_sp_name'), 'away_pitcher': g.get('away_sp_name'),
        'home_sp_xera': g.get('home_sp_xera'), 'away_sp_xera': g.get('away_sp_xera'),
        'home_pitcher_last_3_era': g.get('home_pitcher_last_3_era'),
        'away_pitcher_last_3_era': g.get('away_pitcher_last_3_era'),
        'home_sp_k_pct': g.get('home_sp_k_pct'), 'away_sp_k_pct': g.get('away_sp_k_pct'),
        'home_team_k_pct': g.get('home_team_k_pct'), 'away_team_k_pct': g.get('away_team_k_pct'),
        'home_k_gap': g.get('home_k_gap'), 'away_k_gap': g.get('away_k_gap'),
        'home_first_inning_era': g.get('home_first_inning_era'),
        'away_first_inning_era': g.get('away_first_inning_era'),
        'home_bp_relievers_3d': g.get('home_bp_relievers_3d'),
        'away_bp_relievers_3d': g.get('away_bp_relievers_3d'),
        'home_wrc_plus': g.get('home_wrc_plus'), 'away_wrc_plus': g.get('away_wrc_plus'),
        'home_wrc_vs_opp_hand': g.get('home_wrc_vs_opp_hand'),
        'away_wrc_vs_opp_hand': g.get('away_wrc_vs_opp_hand'),
        'home_last10_runs_per_game': g.get('home_last10_runs_per_game'),
        'away_last10_runs_per_game': g.get('away_last10_runs_per_game'),
        'home_runs_per_game': g.get('home_runs_per_game'),
        'away_runs_per_game': g.get('away_runs_per_game'),
        'home_pitcher_vs_team_era': g.get('home_pitcher_vs_team_era'),
        'away_pitcher_vs_team_era': g.get('away_pitcher_vs_team_era'),
        'home_pitcher_vs_team_ip': g.get('home_pitcher_vs_team_ip'),
        'away_pitcher_vs_team_ip': g.get('away_pitcher_vs_team_ip'),
        'home_team_oaa': g.get('home_team_oaa'), 'away_team_oaa': g.get('away_team_oaa'),
        'home_sp_days_rest': g.get('home_sp_days_rest'),
        'away_sp_days_rest': g.get('away_sp_days_rest'),
        'home_bullpen_era': g.get('home_bullpen_era'),
        'away_bullpen_era': g.get('away_bullpen_era'),
        'signal_confluence_net': g.get('signal_confluence_net'),
        'park_run_factor': g.get('park_run_factor'),
        'temperature': g.get('temperature'),
        'wind_speed': g.get('wind_mph'), 'wind_direction': g.get('wind_direction'),
        'close_total': g.get('close_total'), 'open_total': g.get('open_total'),
        'close_spread': g.get('close_spread'), 'open_spread': g.get('open_spread'),
        'projected_total': g.get('projected_total'),
        'model_pred_total': g.get('model_pred_total'),
        'jerry_pred_total': g.get('jerry_pred_total'),
    }


def cnt(ctx, direction):
    m = evaluate_game_for_play(ctx, 'v3_tot', direction) or []
    return len([x for x in m
                if x.get('tier') in ('LOCK', 'STRONG_EDGE')
                and not x.get('id', '').endswith('|any')])


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


def main():
    games = pull_results()
    print(f'Pulled {len(games)} graded games')

    # Compute per-game cohort counts and outcomes
    data = []  # list of (over_count, under_count, actual_over, line, total)
    for g in games:
        ctx = adapt(g)
        if not ctx.get('home_pitcher') or not ctx.get('away_pitcher'):
            continue
        line = ctx.get('close_total') or ctx.get('open_total')
        if line is None:
            continue
        actual = (g.get('home_score') or 0) + (g.get('away_score') or 0)
        if actual == line:
            continue  # push, drop
        o = cnt(ctx, 'over')
        u = cnt(ctx, 'under')
        data.append((o, u, actual > line, line, actual))

    print(f'  {len(data)} valid games (excluded pushes / missing SPs)')

    # Engine baseline
    n = len(data)
    avg_o = sum(d[0] for d in data) / n
    avg_u = sum(d[1] for d in data) / n
    print(f'  Engine baseline: avg {avg_o:.1f} OVER cohorts / {avg_u:.1f} UNDER cohorts')
    print(f'  Engine baseline net: +{avg_o - avg_u:.1f} OVER per game')
    print()

    # Bin games by raw net and by baseline-deviation net
    def bin_by_net(getter, label, bins):
        print(f'═══════════════════════════════════════════════════════════════════════════════')
        print(f'  {label}')
        print(f'═══════════════════════════════════════════════════════════════════════════════')
        binned = defaultdict(lambda: {'n': 0, 'overs': 0})
        for d in data:
            metric = getter(d)
            placed = False
            for b in bins:
                low, high, name = b
                if low <= metric < high:
                    binned[name]['n'] += 1
                    binned[name]['overs'] += int(d[2])
                    placed = True
                    break
            if not placed:
                # off-chart
                if metric < bins[0][0]:
                    binned[bins[0][2]]['n'] += 1
                    binned[bins[0][2]]['overs'] += int(d[2])
                else:
                    binned[bins[-1][2]]['n'] += 1
                    binned[bins[-1][2]]['overs'] += int(d[2])
        print(f'{"BIN":<28}{"GAMES":<8}{"OVERS":<8}{"OVER %":<10}{"vs 52% BASE"}')
        print('-' * 80)
        for b in bins:
            name = b[2]
            s = binned[name]
            if s['n'] == 0:
                continue
            pct = 100 * s['overs'] / s['n']
            diff = pct - 52.0
            flag = ' ← signal' if abs(diff) >= 8 and s['n'] >= 15 else ''
            print(f'{name:<28}{s["n"]:<8}{s["overs"]:<8}{pct:<10.1f}{diff:+.1f}{flag}')
        print()

    # Raw net bins
    raw_bins = [
        (-100, -10, 'raw net ≤ -10 UNDER'),
        (-10, -5, 'raw net -10 to -5'),
        (-5, 0, 'raw net -5 to 0'),
        (0, 5, 'raw net 0 to 5'),
        (5, 9, 'raw net 5 to 9 (engine baseline)'),
        (9, 12, 'raw net 9 to 12'),
        (12, 15, 'raw net 12 to 15'),
        (15, 100, 'raw net ≥ 15'),
    ]
    bin_by_net(lambda d: d[0] - d[1], 'RAW COHORT NET — actual OVER hit rate', raw_bins)

    # Baseline-adjusted: how much is this game above baseline?
    dev_bins = [
        (-100, -10, 'dev ≤ -10'),
        (-10, -5, 'dev -10 to -5 (real UNDER signal)'),
        (-5, -2, 'dev -5 to -2'),
        (-2, 2, 'dev -2 to +2 (at baseline)'),
        (2, 5, 'dev +2 to +5'),
        (5, 10, 'dev +5 to +10 (real OVER signal)'),
        (10, 100, 'dev ≥ +10'),
    ]
    bin_by_net(
        lambda d: (d[0] - avg_o) - (d[1] - avg_u),
        f'BASELINE-NORMALIZED NET (subtract engine baseline {avg_o:.1f}/{avg_u:.1f}) — actual OVER hit rate',
        dev_bins,
    )


if __name__ == '__main__':
    main()
