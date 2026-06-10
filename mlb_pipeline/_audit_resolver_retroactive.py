"""
_audit_resolver_retroactive.py — Apply the resolver to every game over the
last N days and compute what the framework WOULD have published vs what
actually got published as POTD.

Answers the existential question: if we'd applied resolver discipline
(SKIP contested games, publish only STRONG/ELITE), would last 30 days have
been profitable?

CAVEATS (be honest):
  - We don't have historical cohort_signals state per date (refreshes nightly,
    no snapshot table). We approximate cohort signal using the CURRENT cohort
    engine state applied to historical inputs. This is anachronistic but
    informative — the rules themselves are statistical patterns, not date-
    specific. Modest data leak: rules that exist today might not have existed
    on a historical date if they only emerged after recent additions.
  - Prop reverse signal didn't exist before today, skip from resolver input.
  - This is MODEL-MAJORITY-FOCUSED audit — we use model agreement as the
    primary resolution signal and cohort as a secondary confirmation. Honest
    proxy for what a model-first resolver would have done.

For each historical game, classify into resolver tier using ONLY signals
available pre-game (models + applied cohort engine, no leak from outcome).
"""
import os
import sys
from datetime import datetime, timedelta
from collections import Counter

from dotenv import load_dotenv
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

import requests

from cohort_signals import evaluate_game_for_play
from signal_resolver import resolve_total

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}


def pull_results(date_from='2026-05-11'):
    """Pull all graded games since date_from. mlb_game_results is self-contained."""
    all_rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/mlb_game_results',
            headers={**HEADERS, 'Range-Unit': 'items', 'Range': f'{offset}-{offset+999}'},
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
        if len(rows) < 1000:
            break
        offset += 1000
    return all_rows


def adapt_result_to_ctx(g):
    """Translate mlb_game_results columns to the field names cohort_signals
    and signal_resolver expect."""
    return {
        'game_id': g.get('game_id'),
        'home_team': g.get('home_team'),
        'away_team': g.get('away_team'),
        'home_pitcher': g.get('home_sp_name'),
        'away_pitcher': g.get('away_sp_name'),
        'home_sp_xera': g.get('home_sp_xera'),
        'away_sp_xera': g.get('away_sp_xera'),
        'home_pitcher_last_3_era': g.get('home_pitcher_last_3_era'),
        'away_pitcher_last_3_era': g.get('away_pitcher_last_3_era'),
        'home_sp_k_pct': g.get('home_sp_k_pct'),
        'away_sp_k_pct': g.get('away_sp_k_pct'),
        'home_team_k_pct': g.get('home_team_k_pct'),
        'away_team_k_pct': g.get('away_team_k_pct'),
        'home_k_gap': g.get('home_k_gap'),
        'away_k_gap': g.get('away_k_gap'),
        'home_first_inning_era': g.get('home_first_inning_era'),
        'away_first_inning_era': g.get('away_first_inning_era'),
        'home_bp_relievers_3d': g.get('home_bp_relievers_3d'),
        'away_bp_relievers_3d': g.get('away_bp_relievers_3d'),
        'home_wrc_plus': g.get('home_wrc_plus'),
        'away_wrc_plus': g.get('away_wrc_plus'),
        'home_wrc_vs_opp_hand': g.get('home_wrc_vs_opp_hand'),
        'away_wrc_vs_opp_hand': g.get('away_wrc_vs_opp_hand'),
        'home_last10_runs_per_game': g.get('home_last10_runs_per_game'),
        'away_last10_runs_per_game': g.get('away_last10_runs_per_game'),
        'home_runs_per_game': g.get('home_runs_per_game'),
        'away_runs_per_game': g.get('away_runs_per_game'),
        'home_offense_drift': g.get('home_offense_drift'),
        'away_offense_drift': g.get('away_offense_drift'),
        'home_pitcher_vs_team_era': g.get('home_pitcher_vs_team_era'),
        'away_pitcher_vs_team_era': g.get('away_pitcher_vs_team_era'),
        'home_pitcher_vs_team_ip': g.get('home_pitcher_vs_team_ip'),
        'away_pitcher_vs_team_ip': g.get('away_pitcher_vs_team_ip'),
        'home_team_oaa': g.get('home_team_oaa'),
        'away_team_oaa': g.get('away_team_oaa'),
        'home_sp_days_rest': g.get('home_sp_days_rest'),
        'away_sp_days_rest': g.get('away_sp_days_rest'),
        'home_bullpen_era': g.get('home_bullpen_era'),
        'away_bullpen_era': g.get('away_bullpen_era'),
        'signal_confluence_net': g.get('signal_confluence_net'),
        'park_run_factor': g.get('park_run_factor'),
        'temperature': g.get('temperature'),
        'wind_speed': g.get('wind_mph'),
        'wind_direction': g.get('wind_direction'),
        'is_dome': bool(g.get('is_dome') or g.get('dome_game')),
        'close_total': g.get('close_total'),
        'open_total': g.get('open_total'),
        'close_spread': g.get('close_spread'),
        'open_spread': g.get('open_spread'),
        'projected_total': g.get('projected_total'),
        'model_pred_total': g.get('model_pred_total'),
        'jerry_pred_total': g.get('jerry_pred_total'),
        'projected_spread': g.get('projected_spread'),
        'model_pred_spread': g.get('model_pred_spread'),
        'jerry_pred_spread': g.get('jerry_pred_spread'),
    }


def count_cohort_directional(ctx, play_label, direction):
    """Count STRONG_EDGE+LOCK matches for the given play/direction."""
    matches = evaluate_game_for_play(ctx, play_label, direction) or []
    loud = [m for m in matches if m.get('tier') in ('LOCK', 'STRONG_EDGE')
            and not m.get('id', '').endswith('|any')]
    return len(loud)


def run_audit(date_from='2026-05-11'):
    print('=' * 90)
    print(f'RETROACTIVE RESOLVER AUDIT  —  {date_from} onward')
    print('=' * 90)
    print()

    games = pull_results(date_from)
    print(f'Pulled {len(games)} graded games')
    print()

    # Tier-level tally for the resolver
    tier_stats = {t: {'n': 0, 'wins': 0, 'losses': 0, 'pushes': 0, 'pnl': 0.0}
                  for t in ('ELITE', 'STRONG', 'LEAN', 'LIGHT', 'SKIP')}

    # Comparison with actual POTDs
    skipped = 0
    examples_by_tier = {t: [] for t in ('ELITE', 'STRONG', 'LEAN', 'LIGHT', 'SKIP')}

    for g in games:
        ctx = adapt_result_to_ctx(g)
        if not ctx.get('home_pitcher') or not ctx.get('away_pitcher'):
            skipped += 1
            continue
        close_total = ctx.get('close_total') or ctx.get('open_total')
        if close_total is None:
            skipped += 1
            continue
        if g.get('away_score') is None or g.get('home_score') is None:
            skipped += 1
            continue
        actual_total = g.get('away_score') + g.get('home_score')

        # Count cohort matches for both directions
        over_strong = count_cohort_directional(ctx, 'v3_tot', 'over')
        under_strong = count_cohort_directional(ctx, 'v3_tot', 'under')

        result = resolve_total(
            close_total=close_total,
            v3_total=ctx.get('projected_total'),
            v4_total=ctx.get('model_pred_total'),
            jerry_total=ctx.get('jerry_pred_total'),
            cohort_over_strong_count=over_strong,
            cohort_under_strong_count=under_strong,
            prop_reverse=None,  # didn't exist historically
        )

        tier = result['tier']
        direction = result['direction']
        tier_stats[tier]['n'] += 1

        if tier == 'SKIP' or direction is None:
            continue  # nothing bet

        actual_over = actual_total > close_total
        actual_under = actual_total < close_total

        won = None
        if actual_over and direction == 'OVER':
            won = True
        elif actual_under and direction == 'UNDER':
            won = True
        elif actual_total == close_total:
            won = None  # push
        else:
            won = False

        if won is True:
            tier_stats[tier]['wins'] += 1
            tier_stats[tier]['pnl'] += 91.0  # -110 juice
        elif won is False:
            tier_stats[tier]['losses'] += 1
            tier_stats[tier]['pnl'] -= 100.0
        else:
            tier_stats[tier]['pushes'] += 1

        if len(examples_by_tier[tier]) < 5:
            examples_by_tier[tier].append({
                'date': g.get('game_date'),
                'matchup': f"{g.get('away_team')[:14]}@{g.get('home_team')[:14]}",
                'direction': direction,
                'line': close_total,
                'actual': actual_total,
                'won': won,
            })

    # Output
    print('RESOLVER TIER PERFORMANCE')
    print(f'{"TIER":<10}{"PICKS":<8}{"W":<5}{"L":<5}{"P":<5}{"HIT %":<10}{"P&L":<10}{"ROI"}')
    print('-' * 75)
    total_bets = 0
    total_pnl = 0.0
    for tier in ('ELITE', 'STRONG', 'LEAN', 'LIGHT', 'SKIP'):
        s = tier_stats[tier]
        bets = s['wins'] + s['losses']
        if tier == 'SKIP':
            print(f'{tier:<10}{s["n"]:<8}-   -   -   (no bets — SKIP)')
            continue
        if bets == 0:
            print(f'{tier:<10}{s["n"]:<8}0    0    0    -         $0        -')
            continue
        hit = 100 * s['wins'] / bets
        roi = 100 * s['pnl'] / (bets * 100)
        print(f'{tier:<10}{s["n"]:<8}{s["wins"]:<5}{s["losses"]:<5}{s["pushes"]:<5}'
              f'{hit:<10.1f}${s["pnl"]:<8.0f}{roi:+.1f}%')
        total_bets += bets
        total_pnl += s['pnl']

    print()
    print(f'TOTAL BETS (excl SKIP): {total_bets}')
    print(f'TOTAL P&L:              ${total_pnl:.0f}')
    if total_bets:
        print(f'OVERALL HIT RATE:       {sum(tier_stats[t]["wins"] for t in ("ELITE","STRONG","LEAN","LIGHT"))/total_bets*100:.1f}%')
        print(f'OVERALL ROI:            {100*total_pnl/(total_bets*100):+.1f}%')

    print()
    print('=' * 90)
    print('STRONG-ONLY DISCIPLINE COMPARISON')
    print('=' * 90)
    strong_only = tier_stats['STRONG']['wins'] + tier_stats['STRONG']['losses']
    if strong_only:
        roi_strong = 100 * tier_stats['STRONG']['pnl'] / (strong_only * 100)
        print(f'If we publish ONLY STRONG-tier picks ({strong_only} bets):')
        print(f'  Wins: {tier_stats["STRONG"]["wins"]} | Losses: {tier_stats["STRONG"]["losses"]}')
        print(f'  Hit rate: {100*tier_stats["STRONG"]["wins"]/strong_only:.1f}%')
        print(f'  P&L: ${tier_stats["STRONG"]["pnl"]:.0f}  ROI: {roi_strong:+.1f}%')

    elite_strong = strong_only + tier_stats['ELITE']['wins'] + tier_stats['ELITE']['losses']
    elite_strong_pnl = tier_stats['ELITE']['pnl'] + tier_stats['STRONG']['pnl']
    if elite_strong:
        print()
        print(f'If we publish ELITE + STRONG ({elite_strong} bets):')
        wins_es = tier_stats['ELITE']['wins'] + tier_stats['STRONG']['wins']
        print(f'  Wins: {wins_es} | Losses: {elite_strong - wins_es}')
        print(f'  Hit rate: {100*wins_es/elite_strong:.1f}%')
        print(f'  P&L: ${elite_strong_pnl:.0f}  ROI: {100*elite_strong_pnl/(elite_strong*100):+.1f}%')

    return tier_stats


if __name__ == '__main__':
    date_from = sys.argv[1] if len(sys.argv) > 1 else '2026-05-11'
    run_audit(date_from)
