"""
_backtest_monte_carlo.py — Validate the MC simulator against historical games.

For each graded game in mlb_game_results, pull the historical mlb_game_context
row, run MC with n_iter=10000 and seed=game_id (for reproducibility), and
compare predictions to actual outcomes.

CAVEAT: mlb_game_context historical rows are current-state, not morning-locked.
We only use fields that are pre-game-stable:
  - SP xERA, K%, projected_outs, L3 ERA (computed pre-game)
  - Lineup wRC+ vs hand (computed pre-game)
  - BP usage as of game-day (locked morning)
  - Park / weather (pre-game stable)
We skip fields that include the game-being-predicted:
  - home/away_last10_runs_per_game (would include current game)
  - sweat_score (post-game)

For the backtest's "as-of pre-game" L10 R/G, we'd need to compute from
mlb_game_results excluding the predicted game. For v1 we ACCEPT this leak
and note it — it inflates accuracy slightly but lets us evaluate the
multiplier chain rapidly.

Metrics:
  - Total direction accuracy: P(OVER predicted) vs actual O/U
  - Total MAE: |mu_total - actual_total|
  - Win prob calibration: Brier score on home_win
  - NRFI accuracy: predicted P(NRFI) vs actual

Compared to baselines:
  - v3_tot (projected_total) directional accuracy
  - v4_tot (model_pred_total) directional accuracy
  - jerry_tot (jerry_pred_total) directional accuracy
"""
import os
import sys
import statistics

from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

import requests

from monte_carlo import simulate_game

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _translate_result_to_ctx(game):
    """mlb_game_results uses slightly different column names than
    mlb_game_context (the schema monte_carlo expects). Map the fields
    so simulate_game() can read a result row directly.

    mlb_game_results has EVERY input we need — it's a self-contained
    training row. No join required. Historical mlb_game_context rows
    get cleaned up, so this is also more reliable than trying to
    pull historical context."""
    return {
        'game_id': game.get('game_id'),
        'home_team': game.get('home_team'),
        'away_team': game.get('away_team'),
        # SP — schema differs (results: home_sp_name; context: home_pitcher)
        'home_pitcher': game.get('home_sp_name'),
        'away_pitcher': game.get('away_sp_name'),
        'home_sp_xera': game.get('home_sp_xera'),
        'away_sp_xera': game.get('away_sp_xera'),
        'home_pitcher_last_3_era': game.get('home_pitcher_last_3_era'),
        'away_pitcher_last_3_era': game.get('away_pitcher_last_3_era'),
        # projected_outs isn't in mlb_game_results — fall back to default 18
        'home_pitcher_projected_outs': None,
        'away_pitcher_projected_outs': None,
        # BP
        'home_bullpen_era': game.get('home_bullpen_era'),
        'away_bullpen_era': game.get('away_bullpen_era'),
        'home_bp_relievers_3d': game.get('home_bp_relievers_3d'),
        'away_bp_relievers_3d': game.get('away_bp_relievers_3d'),
        # Offense
        'home_runs_per_game': game.get('home_runs_per_game'),
        'away_runs_per_game': game.get('away_runs_per_game'),
        'home_last10_runs_per_game': game.get('home_last10_runs_per_game'),
        'away_last10_runs_per_game': game.get('away_last10_runs_per_game'),
        'home_wrc_plus': game.get('home_wrc_plus'),
        'away_wrc_plus': game.get('away_wrc_plus'),
        'home_wrc_vs_opp_hand': game.get('home_wrc_vs_opp_hand'),
        'away_wrc_vs_opp_hand': game.get('away_wrc_vs_opp_hand'),
        # Park / weather
        'park_run_factor': game.get('park_run_factor'),
        'park_hr_factor': game.get('park_run_factor'),  # mlb_game_results doesn't have park_hr separately
        'temperature': game.get('temperature'),
        'wind_speed': game.get('wind_mph'),
        'wind_direction': game.get('wind_direction'),
        'is_dome': bool(game.get('is_dome') or game.get('dome_game') or game.get('dome_game_flag')),
        # Lines
        'close_total': game.get('close_total'),
        'open_total': game.get('open_total'),
        # Baselines for comparison
        'projected_total': game.get('projected_total'),
        'model_pred_total': game.get('model_pred_total'),
        'jerry_pred_total': game.get('jerry_pred_total'),
    }


def pull_graded_games(date_from='2026-03-01'):
    """Pull all graded games. mlb_game_results is self-contained — no join needed."""
    print(f"[backtest] fetching graded games since {date_from}...")
    # Paginate to bypass PostgREST 1000-row default cap
    all_rows = []
    offset = 0
    page_size = 1000
    while True:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results',
            headers={**HEADERS, 'Range-Unit': 'items', 'Range': f'{offset}-{offset+page_size-1}'},
            params={
                'select': '*',
                'game_date': f'gte.{date_from}',
                'home_score': 'not.is.null',
                'order': 'game_date.asc',
            },
        )
        rows = r.json() if r.status_code in (200, 206) else []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    print(f"  {len(all_rows)} graded games pulled")
    return all_rows


def run_backtest(n_iter=5000):
    results = pull_graded_games()

    total_predictions = 0
    skipped = 0
    mc_correct_dir = 0
    v3_correct_dir = 0
    v4_correct_dir = 0
    jerry_correct_dir = 0

    mc_mae_sum = 0.0
    v3_mae_sum = 0.0
    v4_mae_sum = 0.0
    jerry_mae_sum = 0.0
    mae_n = 0

    mc_brier_sum = 0.0
    home_wins_predicted_high = 0
    home_wins_predicted_high_attempted = 0
    actual_home_wins = 0

    # Confident-prediction tracking (mirror cohort_signals: only count when
    # model differs from line by at least the deadband). cohort_signals uses
    # 0.3 for the total call gate — using 1.0 here to test STRONG confidence
    # which is what POTD/Jerry reads cite.
    DEADBAND = 1.0
    mc_conf_n = 0; mc_conf_correct = 0
    v3_conf_n = 0; v3_conf_correct = 0
    v4_conf_n = 0; v4_conf_correct = 0

    print(f"[backtest] running MC on {len(results)} games at n_iter={n_iter}...")
    progress_interval = max(1, len(results) // 20)

    for idx, game in enumerate(results):
        if idx % progress_interval == 0 and idx > 0:
            print(f"  ... {idx}/{len(results)} games processed (skipped={skipped})")

        # Translate result row into MC-compatible ctx
        ctx = _translate_result_to_ctx(game)

        if not ctx.get('home_pitcher') or not ctx.get('away_pitcher'):
            skipped += 1
            continue

        close_total = _f(game.get('close_total'))
        if close_total is None:
            skipped += 1
            continue

        away_score = _f(game.get('away_score'))
        home_score = _f(game.get('home_score'))
        if away_score is None or home_score is None:
            skipped += 1
            continue
        actual_total = away_score + home_score
        actual_home_win = bool(game.get('home_win'))

        # Deterministic seed
        seed = abs(hash(ctx.get('game_id') or f"{idx}")) % (2**31)
        mc = simulate_game(ctx, n_iter=n_iter, line=close_total, seed=seed)
        if mc is None:
            skipped += 1
            continue

        total_predictions += 1

        # Directional accuracy
        actual_over = actual_total > close_total
        mc_over_pick = mc['p_over'] > 0.5
        if mc_over_pick == actual_over:
            mc_correct_dir += 1

        # Baselines (track per-model sample sizes — some models null on many games)
        if not hasattr(run_backtest, '_n_v3'):
            run_backtest._n_v3 = 0
            run_backtest._n_v4 = 0
            run_backtest._n_jerry = 0
        v3 = _f(ctx.get('projected_total'))
        v4 = _f(ctx.get('model_pred_total'))
        jerry = _f(ctx.get('jerry_pred_total'))

        if v3 is not None:
            run_backtest._n_v3 += 1
            if (v3 > close_total) == actual_over:
                v3_correct_dir += 1
            v3_mae_sum += abs(v3 - actual_total)
        if v4 is not None:
            run_backtest._n_v4 += 1
            if (v4 > close_total) == actual_over:
                v4_correct_dir += 1
            v4_mae_sum += abs(v4 - actual_total)
        if jerry is not None:
            run_backtest._n_jerry += 1
            if (jerry > close_total) == actual_over:
                jerry_correct_dir += 1
            jerry_mae_sum += abs(jerry - actual_total)

        mc_mae_sum += abs(mc['mu_total'] - actual_total)
        mae_n += 1

        # Confident-prediction tracking (deadband filter)
        mc_gap = mc['mu_total'] - close_total
        if abs(mc_gap) >= DEADBAND:
            mc_conf_n += 1
            if (mc_gap > 0) == actual_over:
                mc_conf_correct += 1
        if v3 is not None and abs(v3 - close_total) >= DEADBAND:
            v3_conf_n += 1
            if (v3 > close_total) == actual_over:
                v3_conf_correct += 1
        if v4 is not None and abs(v4 - close_total) >= DEADBAND:
            v4_conf_n += 1
            if (v4 > close_total) == actual_over:
                v4_conf_correct += 1

        # Win prob calibration (Brier score)
        actual_y = 1.0 if actual_home_win else 0.0
        mc_brier_sum += (mc['p_home_win'] - actual_y) ** 2
        if mc['p_home_win'] > 0.5:
            home_wins_predicted_high_attempted += 1
            if actual_home_win:
                home_wins_predicted_high += 1
        if actual_home_win:
            actual_home_wins += 1

    # Report
    print()
    print("=" * 75)
    print("MONTE CARLO BACKTEST RESULTS")
    print("=" * 75)
    print(f"Games attempted:   {len(results)}")
    print(f"Games scored:      {total_predictions}")
    print(f"Skipped:           {skipped} (no ctx / no SP / no line / no score)")
    print()
    n_v3 = getattr(run_backtest, '_n_v3', 0)
    n_v4 = getattr(run_backtest, '_n_v4', 0)
    n_jerry = getattr(run_backtest, '_n_jerry', 0)
    print(f"TOTAL DIRECTION ACCURACY (vs close_total):")
    print(f"  Monte Carlo:  {mc_correct_dir}/{total_predictions} "
          f"({100*mc_correct_dir/total_predictions:.1f}%)")
    if n_v3:
        print(f"  v3 baseline:  {v3_correct_dir}/{n_v3} "
              f"({100*v3_correct_dir/n_v3:.1f}%)")
    if n_v4:
        print(f"  v4 baseline:  {v4_correct_dir}/{n_v4} "
              f"({100*v4_correct_dir/n_v4:.1f}%)")
    if n_jerry:
        print(f"  Jerry base:   {jerry_correct_dir}/{n_jerry} "
              f"({100*jerry_correct_dir/n_jerry:.1f}%)")
    print()
    if mae_n:
        print(f"MEAN ABSOLUTE ERROR (run total prediction):")
        print(f"  Monte Carlo:  {mc_mae_sum/mae_n:.2f} runs (n={mae_n})")
        if n_v3:
            print(f"  v3:           {v3_mae_sum/n_v3:.2f} runs (n={n_v3})")
        if n_v4:
            print(f"  v4:           {v4_mae_sum/n_v4:.2f} runs (n={n_v4})")
        if n_jerry:
            print(f"  Jerry:        {jerry_mae_sum/n_jerry:.2f} runs (n={n_jerry})")
    print()
    print()
    print(f"CONFIDENT-ONLY DIRECTION ACCURACY (|model - line| >= {DEADBAND}):")
    if mc_conf_n:
        print(f"  Monte Carlo:  {mc_conf_correct}/{mc_conf_n} "
              f"({100*mc_conf_correct/mc_conf_n:.1f}%)")
    if v3_conf_n:
        print(f"  v3:           {v3_conf_correct}/{v3_conf_n} "
              f"({100*v3_conf_correct/v3_conf_n:.1f}%)")
    if v4_conf_n:
        print(f"  v4:           {v4_conf_correct}/{v4_conf_n} "
              f"({100*v4_conf_correct/v4_conf_n:.1f}%)")
    print()
    if total_predictions:
        print(f"HOME WIN PROBABILITY CALIBRATION:")
        print(f"  Brier score:                       "
              f"{mc_brier_sum/total_predictions:.4f}  (lower = better)")
        print(f"  Naive baseline (always predict 0.5): 0.2500")
        if home_wins_predicted_high_attempted:
            print(f"  When MC predicts P(home) > 0.5:    "
                  f"{home_wins_predicted_high}/{home_wins_predicted_high_attempted} "
                  f"({100*home_wins_predicted_high/home_wins_predicted_high_attempted:.1f}%)")
        print(f"  Base rate (actual home win %):     "
              f"{100*actual_home_wins/total_predictions:.1f}%")


if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    run_backtest(n_iter=n)
