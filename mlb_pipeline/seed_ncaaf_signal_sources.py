"""Seed signal_sources with NCAAF signal catalog (2026-08-16).

Mirrors NFL structure since NCAAF ctx has similar shape (spread/total,
model preds, cohorts). SP+ / returning production / weather / rest are
the NCAAF-specific data.

CLI:
  python seed_ncaaf_signal_sources.py
  python seed_ncaaf_signal_sources.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone
from pathlib import Path

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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

NCAAF_SIGNALS = [
    # Model class
    {
        'signal_key': 'ncaaf_projected_spread',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.projected_spread is not None and abs(float(ctx.projected_spread)) >= 3.0',
        'side_expr': '"HOME_ML" if float(ctx.projected_spread) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.projected_spread)) / 14.0, 1.0)',
        'display_prose_template': 'model projects {projected_spread}-point edge',
    },
    {
        'signal_key': 'ncaaf_projected_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.projected_total is not None and ctx.close_total is not None and abs(float(ctx.projected_total) - float(ctx.close_total)) >= 2.5',
        'side_expr': '"OVER" if float(ctx.projected_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.projected_total) - float(ctx.close_total)) / 8.0, 1.0)',
        'display_prose_template': 'model sees {projected_total} points vs market {close_total}',
    },

    # SP+ / efficiency (NCAAF-specific)
    {
        'signal_key': 'ncaaf_sp_plus_edge_home',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_sp_plus is not None and ctx.away_sp_plus is not None and (float(ctx.home_sp_plus) - float(ctx.away_sp_plus)) >= 10',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min((float(ctx.home_sp_plus) - float(ctx.away_sp_plus)) / 25.0, 1.0)',
        'display_prose_template': '{home_team} SP+ rating dominates ({home_sp_plus} vs {away_sp_plus})',
    },
    {
        'signal_key': 'ncaaf_sp_plus_edge_away',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_sp_plus is not None and ctx.away_sp_plus is not None and (float(ctx.away_sp_plus) - float(ctx.home_sp_plus)) >= 10',
        'side_expr': '"AWAY_ML"',
        'strength_expr': 'min((float(ctx.away_sp_plus) - float(ctx.home_sp_plus)) / 25.0, 1.0)',
        'display_prose_template': '{away_team} SP+ rating dominates ({away_sp_plus} vs {home_sp_plus})',
    },
    {
        'signal_key': 'ncaaf_sp_plus_matchup_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.sp_plus_matchup_total is not None and ctx.close_total is not None and abs(float(ctx.sp_plus_matchup_total) - float(ctx.close_total)) >= 5',
        'side_expr': '"OVER" if float(ctx.sp_plus_matchup_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.sp_plus_matchup_total) - float(ctx.close_total)) / 12.0, 1.0)',
        'display_prose_template': 'SP+ matchup projects {sp_plus_matchup_total} vs market {close_total}',
    },

    # Returning production
    {
        'signal_key': 'ncaaf_returning_prod_home',
        'class': 'situational', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_returning_production is not None and float(ctx.home_returning_production) >= 75',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} returns {home_returning_production}% of production — continuity edge',
    },
    {
        'signal_key': 'ncaaf_returning_prod_away',
        'class': 'situational', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_returning_production is not None and float(ctx.away_returning_production) >= 75',
        'side_expr': '"AWAY_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{away_team} returns {away_returning_production}% of production — continuity edge',
    },

    # Weather (outdoor stadiums only)
    {
        'signal_key': 'ncaaf_high_wind_under',
        'class': 'weather', 'market_scope': 'total',
        'condition_expr': 'ctx.wind_speed is not None and int(ctx.wind_speed) >= 15',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((int(ctx.wind_speed) - 10) / 20.0, 1.0)',
        'display_prose_template': '{wind_speed} mph wind — passing game hurt',
    },

    # Cohort
    {
        'signal_key': 'ncaaf_confluence_home',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) >= 2',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min(int(ctx.signal_confluence_net) / 5.0, 1.0)',
        'display_prose_template': 'cohort confluence favors home {signal_confluence_net}',
    },
    {
        'signal_key': 'ncaaf_confluence_away',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) <= -2',
        'side_expr': '"AWAY_ML"',
        'strength_expr': 'min(abs(int(ctx.signal_confluence_net)) / 5.0, 1.0)',
        'display_prose_template': 'cohort confluence favors away {signal_confluence_net}',
    },

    # RL / spread
    {
        'signal_key': 'ncaaf_home_spread_edge',
        'class': 'model', 'market_scope': 'rl',
        'condition_expr': 'ctx.projected_spread is not None and ctx.close_spread is not None and (float(ctx.projected_spread) - float(ctx.close_spread)) >= 3.0',
        'side_expr': '"HOME_RL"',
        'strength_expr': 'min((float(ctx.projected_spread) - float(ctx.close_spread)) / 8.0, 1.0)',
        'display_prose_template': 'model sees home covering the {close_spread} spread by {projected_spread}',
    },
    {
        'signal_key': 'ncaaf_away_spread_edge',
        'class': 'model', 'market_scope': 'rl',
        'condition_expr': 'ctx.projected_spread is not None and ctx.close_spread is not None and (float(ctx.close_spread) - float(ctx.projected_spread)) >= 3.0',
        'side_expr': '"AWAY_RL"',
        'strength_expr': 'min((float(ctx.close_spread) - float(ctx.projected_spread)) / 8.0, 1.0)',
        'display_prose_template': 'model sees away covering vs the {close_spread} market spread',
    },

    # Sport fade rules (documented per project_ncaaf_ready_809)
    {
        'signal_key': 'ncaaf_home_dog_letdown_fade',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.close_spread is not None and float(ctx.close_spread) > 0 and ctx.home_last_result_ats is not None and str(ctx.home_last_result_ats).upper() == "W"',
        'side_expr': '"AWAY_ML"',
        'strength_expr': '0.3',
        'display_prose_template': '{home_team} coming off ATS cover as a dog — historical letdown spot',
        'description': 'From project_ncaaf_ready_809 sharp fade rules.',
    },

    # Handler classes
    {
        'signal_key': 'sharp_split_triple_confirmed_ncaaf',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG', 'side_expr': '_HANDLER_LINE_FLAG', 'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'all three public-split sources agree sharp money is on this side',
    },
    {
        'signal_key': 'sharp_split_confirmed_ncaaf',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG', 'side_expr': '_HANDLER_LINE_FLAG', 'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'two split sources agree sharp money is here',
    },
    {
        'signal_key': 'sharp_scenario_match_ncaaf',
        'class': 'scenario', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_SCENARIO', 'side_expr': '_HANDLER_SCENARIO', 'strength_expr': '_HANDLER_SCENARIO',
        'display_prose_template': 'historical pattern hit {hit_rate}% in {sample_n} similar spots',
    },
    {
        'signal_key': 'external_handicapper_pick_ncaaf',
        'class': 'external_pick', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_EXTERNAL', 'side_expr': '_HANDLER_EXTERNAL', 'strength_expr': '_HANDLER_EXTERNAL',
        'display_prose_template': 'external analysts are on this side',
    },
]


def upsert(dry_run: bool = False):
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for s in NCAAF_SIGNALS:
        row = {
            'signal_key': s['signal_key'], 'sport': 'NCAAF',
            'market_scope': s.get('market_scope', 'multi'),
            'class': s['class'],
            'condition_expr': s['condition_expr'],
            'side_expr': s['side_expr'],
            'strength_expr': s.get('strength_expr', '0.5'),
            'weight_registry_key': s.get('weight_registry_key'),
            'hit_rate_pct': s.get('hit_rate_pct'),
            'sample_n': s.get('sample_n'),
            'display_prose_template': s.get('display_prose_template'),
            'description': s.get('description'),
            'enabled': s.get('enabled', True),
            'origin': 'SEEDED_NCAAF',
            'updated_at': now_iso,
        }
        payloads.append(row)
    all_keys = set()
    for p in payloads: all_keys.update(p.keys())
    normalized = [{k: p.get(k) for k in all_keys} for p in payloads]
    print(f'=== seeding NCAAF signal_sources · {len(normalized)} rows ===')
    for row in normalized: print(f'  {row["signal_key"]:<40} [{row["class"]:<12}] {row["market_scope"]:<5}')
    if dry_run: print('\n[DRY-RUN] no writes'); return
    written = 0
    for i in range(0, len(normalized), 100):
        pr = requests.post(f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport,market_scope',
                           headers=H_WRITE, json=normalized[i:i+100], timeout=15)
        if pr.status_code in (200, 201, 204): written += min(100, len(normalized)-i)
        else: print(f'  ✗ {pr.status_code}: {pr.text[:200]}')
    print(f'  ✓ wrote {written} NCAAF signals')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    upsert(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
