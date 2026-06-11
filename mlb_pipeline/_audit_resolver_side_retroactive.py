"""Validate resolve_side() against last 30d ML outcomes."""
import sys, os
from dotenv import load_dotenv
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')
import requests
from cohort_signals import evaluate_game_for_play
from signal_resolver import resolve_side
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


def count_loud(ctx, play, direction):
    """Include LEAN tier for ML/RL (their STRONG_EDGE counts are too thin)."""
    m = evaluate_game_for_play(ctx, play, direction) or []
    return len([x for x in m
                if x.get('tier') in ('LOCK', 'STRONG_EDGE', 'LEAN')
                and not x.get('id', '').endswith('|any')])


def main():
    games = pull_results()
    print(f'Pulled {len(games)} graded games')

    tier_stats = {t: {'n': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
                  for t in ('ELITE', 'STRONG', 'LEAN', 'LIGHT', 'SKIP')}

    skipped = 0

    for g in games:
        ctx = _translate_result_to_ctx(g)
        if not ctx.get('home_pitcher') or not ctx.get('away_pitcher'):
            skipped += 1
            continue
        if g.get('home_score') is None or g.get('away_score') is None:
            skipped += 1
            continue
        actual_home_win = bool(g.get('home_win'))

        ml_home = sum(count_loud(ctx, p, 'home') for p in ('v3_ml','v4_ml','jerry_ml','conf_ml'))
        ml_away = sum(count_loud(ctx, p, 'away') for p in ('v3_ml','v4_ml','jerry_ml','conf_ml'))
        rl_home = sum(count_loud(ctx, p, 'home') for p in ('v3_rl','v4_rl'))
        rl_away = sum(count_loud(ctx, p, 'away') for p in ('v3_rl','v4_rl'))

        # Models: spread fields, projected vs close
        result = resolve_side(
            close_spread=ctx.get('close_spread'),
            v3_spread=ctx.get('projected_spread'),
            v4_spread=ctx.get('model_pred_spread'),
            jerry_spread=ctx.get('jerry_pred_spread'),
            ml_home_cohort_count=ml_home,
            ml_away_cohort_count=ml_away,
            rl_home_cohort_count=rl_home,
            rl_away_cohort_count=rl_away,
            confluence_net=ctx.get('signal_confluence_net'),
            prop_reverse=None,
        )

        tier = result['tier']
        direction = result['direction']
        tier_stats[tier]['n'] += 1
        if tier == 'SKIP' or direction is None:
            continue

        won = (direction == 'HOME' and actual_home_win) or \
              (direction == 'AWAY' and not actual_home_win)
        if won:
            tier_stats[tier]['wins'] += 1
            # Use actual ML odds if available
            try:
                if direction == 'HOME':
                    ml = float(g.get('home_ml_close') or g.get('home_ml_open') or -110)
                else:
                    ml = float(g.get('away_ml_close') or g.get('away_ml_open') or -110)
                if ml > 0:
                    tier_stats[tier]['pnl'] += ml
                else:
                    tier_stats[tier]['pnl'] += 100 * 100 / abs(ml)
            except Exception:
                tier_stats[tier]['pnl'] += 91
        else:
            tier_stats[tier]['losses'] += 1
            tier_stats[tier]['pnl'] -= 100

    print()
    print('═' * 80)
    print('  RESOLVE_SIDE RETROACTIVE AUDIT (n=30 days)')
    print('═' * 80)
    print(f'{"TIER":<10}{"PICKS":<8}{"W":<5}{"L":<5}{"HIT %":<10}{"P&L":<10}{"ROI"}')
    print('-' * 80)
    for tier in ('ELITE', 'STRONG', 'LEAN', 'LIGHT', 'SKIP'):
        s = tier_stats[tier]
        bets = s['wins'] + s['losses']
        if tier == 'SKIP':
            print(f'{tier:<10}{s["n"]:<8}-  -  -  (no bets)')
            continue
        if bets == 0:
            continue
        hit = 100 * s['wins'] / bets
        roi = 100 * s['pnl'] / (bets * 100)
        print(f'{tier:<10}{s["n"]:<8}{s["wins"]:<5}{s["losses"]:<5}{hit:<10.1f}${s["pnl"]:<8.0f}{roi:+.1f}%')


if __name__ == '__main__':
    main()
