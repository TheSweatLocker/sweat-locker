"""Seed signal_sources with NCAAB game-market signal catalog (2026-08-16).

NCAAB context table exists (20260521_ncaab_game_context.sql) with 30+
efficiency/tempo fields from KenPom + Bart-Torvik. This just wires
signals into the ensemble.

Season starts Nov 3 2026. Foundation ships now so we have runway.

Signal classes:
  * model:       projected total/spread, model_pred, panel, MC
  * efficiency:  adj_em gap, adj_oe/de mismatches
  * tempo:       pace edge, high-pace matchups
  * shooting:    eFG% offense vs defense mismatches
  * situational: rest advantage, home court
  * cohort:      confluence net home/away
  * handlers:    splits, scenarios, external picks

CLI:
  python seed_ncaab_signal_sources.py
  python seed_ncaab_signal_sources.py --dry-run
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

NCAAB_SIGNALS = [
    # ── MODEL CLASS ─────────────────────────────────────────────────
    {
        'signal_key': 'ncaab_projected_spread',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.projected_spread is not None and abs(float(ctx.projected_spread)) >= 3.0',
        'side_expr': '"HOME_ML" if float(ctx.projected_spread) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.projected_spread)) / 12.0, 1.0)',
        'display_prose_template': 'model projects {projected_spread}-point edge',
    },
    {
        'signal_key': 'ncaab_projected_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.projected_total is not None and ctx.close_total is not None and abs(float(ctx.projected_total) - float(ctx.close_total)) >= 4.0',
        'side_expr': '"OVER" if float(ctx.projected_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.projected_total) - float(ctx.close_total)) / 10.0, 1.0)',
        'display_prose_template': 'model sees {projected_total} points vs market {close_total}',
    },
    {
        'signal_key': 'ncaab_panel_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.panel_pred_total is not None and ctx.close_total is not None and abs(float(ctx.panel_pred_total) - float(ctx.close_total)) >= 4.0',
        'side_expr': '"OVER" if float(ctx.panel_pred_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.panel_pred_total) - float(ctx.close_total)) / 10.0, 1.0)',
        'display_prose_template': 'external panel projects {panel_pred_total} vs market {close_total}',
    },

    # ── EFFICIENCY CLASS (KenPom-style) ─────────────────────────────
    {
        'signal_key': 'ncaab_home_efficiency_edge',
        'class': 'efficiency', 'market_scope': 'ml',
        'condition_expr': 'ctx.adj_em_gap is not None and float(ctx.adj_em_gap) >= 8.0',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min(float(ctx.adj_em_gap) / 20.0, 1.0)',
        'display_prose_template': '{home_team} adj efficiency +{adj_em_gap} points better than {away_team}',
    },
    {
        'signal_key': 'ncaab_away_efficiency_edge',
        'class': 'efficiency', 'market_scope': 'ml',
        'condition_expr': 'ctx.adj_em_gap is not None and float(ctx.adj_em_gap) <= -8.0',
        'side_expr': '"AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.adj_em_gap)) / 20.0, 1.0)',
        'display_prose_template': '{away_team} adj efficiency +{adj_em_gap} points better',
    },
    {
        'signal_key': 'ncaab_offensive_mismatch_home',
        'class': 'efficiency', 'market_scope': 'total',
        'condition_expr': 'ctx.home_adj_oe is not None and ctx.away_adj_de is not None and (float(ctx.home_adj_oe) - float(ctx.away_adj_de)) >= 15',
        'side_expr': '"OVER"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} offense ({home_adj_oe}) shreds {away_team} defense ({away_adj_de})',
    },
    {
        'signal_key': 'ncaab_defensive_matchup_under',
        'class': 'efficiency', 'market_scope': 'total',
        'condition_expr': 'ctx.home_adj_de is not None and ctx.away_adj_de is not None and float(ctx.home_adj_de) <= 95 and float(ctx.away_adj_de) <= 95',
        'side_expr': '"UNDER"',
        'strength_expr': '0.4',
        'display_prose_template': 'both defenses elite ({home_adj_de} / {away_adj_de} adj) — grind game',
    },

    # ── TEMPO CLASS ─────────────────────────────────────────────────
    {
        'signal_key': 'ncaab_slow_pace_under',
        'class': 'tempo', 'market_scope': 'total',
        'condition_expr': 'ctx.pace_avg is not None and float(ctx.pace_avg) <= 64',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((70 - float(ctx.pace_avg)) / 6.0, 1.0)',
        'display_prose_template': 'both teams play slow ({pace_avg} possessions/40) — low-total pace',
    },
    {
        'signal_key': 'ncaab_fast_pace_over',
        'class': 'tempo', 'market_scope': 'total',
        'condition_expr': 'ctx.pace_avg is not None and float(ctx.pace_avg) >= 72',
        'side_expr': '"OVER"',
        'strength_expr': 'min((float(ctx.pace_avg) - 68) / 6.0, 1.0)',
        'display_prose_template': 'both teams push tempo ({pace_avg} possessions/40) — high-total race',
    },

    # ── SHOOTING (eFG%) ─────────────────────────────────────────────
    {
        'signal_key': 'ncaab_home_shooting_edge',
        'class': 'shooting', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_efg_o is not None and ctx.away_efg_d is not None and (float(ctx.home_efg_o) - float(ctx.away_efg_d)) >= 5.0',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} eFG% offense ({home_efg_o}) beats {away_team} eFG% def ({away_efg_d})',
    },
    {
        'signal_key': 'ncaab_away_shooting_edge',
        'class': 'shooting', 'market_scope': 'ml',
        'condition_expr': 'ctx.away_efg_o is not None and ctx.home_efg_d is not None and (float(ctx.away_efg_o) - float(ctx.home_efg_d)) >= 5.0',
        'side_expr': '"AWAY_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{away_team} eFG% offense ({away_efg_o}) beats {home_team} eFG% def ({home_efg_d})',
    },

    # ── SITUATIONAL ─────────────────────────────────────────────────
    {
        'signal_key': 'ncaab_home_rest_edge',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_days_rest is not None and ctx.away_days_rest is not None and (int(ctx.home_days_rest) - int(ctx.away_days_rest)) >= 3',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.3',
        'display_prose_template': '{home_team} {home_days_rest} days rest vs {away_days_rest}',
    },
    {
        'signal_key': 'ncaab_home_court_strong',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.projected_spread is not None and float(ctx.projected_spread) >= 0 and ctx.projected_spread is not None',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.2',
        'display_prose_template': 'home court gives {home_team} baseline edge',
        'enabled': False,  # too generic — enable only after backfill validates
    },

    # ── COHORT ──────────────────────────────────────────────────────
    {
        'signal_key': 'ncaab_confluence_home',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) >= 2',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min(int(ctx.signal_confluence_net) / 5.0, 1.0)',
        'display_prose_template': 'cohort confluence favors home ({signal_confluence_net})',
    },
    {
        'signal_key': 'ncaab_confluence_away',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) <= -2',
        'side_expr': '"AWAY_ML"',
        'strength_expr': 'min(abs(int(ctx.signal_confluence_net)) / 5.0, 1.0)',
        'display_prose_template': 'cohort confluence favors away ({signal_confluence_net})',
    },

    # ── RL / SPREAD ─────────────────────────────────────────────────
    {
        'signal_key': 'ncaab_home_spread_edge',
        'class': 'model', 'market_scope': 'rl',
        'condition_expr': 'ctx.projected_spread is not None and ctx.close_spread is not None and (float(ctx.projected_spread) + float(ctx.close_spread)) >= 3.0',
        'side_expr': '"HOME_RL"',
        'strength_expr': 'min((float(ctx.projected_spread) + float(ctx.close_spread)) / 8.0, 1.0)',
        'display_prose_template': 'model sees home covering the {close_spread} spread',
    },
    {
        'signal_key': 'ncaab_away_spread_edge',
        'class': 'model', 'market_scope': 'rl',
        'condition_expr': 'ctx.projected_spread is not None and ctx.close_spread is not None and (float(ctx.projected_spread) + float(ctx.close_spread)) <= -3.0',
        'side_expr': '"AWAY_RL"',
        'strength_expr': 'min(abs(float(ctx.projected_spread) + float(ctx.close_spread)) / 8.0, 1.0)',
        'display_prose_template': 'model sees away covering vs the {close_spread} market spread',
    },

    # ── HANDLERS ────────────────────────────────────────────────────
    {
        'signal_key': 'sharp_split_triple_confirmed_ncaab',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG', 'side_expr': '_HANDLER_LINE_FLAG', 'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'all three public-split sources agree sharp money is on this side',
    },
    {
        'signal_key': 'sharp_split_confirmed_ncaab',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG', 'side_expr': '_HANDLER_LINE_FLAG', 'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'two split sources agree sharp money is here',
    },
    {
        'signal_key': 'sharp_scenario_match_ncaab',
        'class': 'scenario', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_SCENARIO', 'side_expr': '_HANDLER_SCENARIO', 'strength_expr': '_HANDLER_SCENARIO',
        'display_prose_template': 'historical pattern hit {hit_rate}% in {sample_n} similar spots',
    },
    {
        'signal_key': 'external_handicapper_pick_ncaab',
        'class': 'external_pick', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_EXTERNAL', 'side_expr': '_HANDLER_EXTERNAL', 'strength_expr': '_HANDLER_EXTERNAL',
        'display_prose_template': 'external analysts are on this side',
    },
]


def upsert(dry_run: bool = False):
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for s in NCAAB_SIGNALS:
        row = {
            'signal_key': s['signal_key'], 'sport': 'NCAAB',
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
            'origin': 'SEEDED_NCAAB',
            'updated_at': now_iso,
        }
        payloads.append(row)
    all_keys = set()
    for p in payloads: all_keys.update(p.keys())
    normalized = [{k: p.get(k) for k in all_keys} for p in payloads]
    print(f'=== seeding NCAAB signal_sources · {len(normalized)} rows ===')
    for row in normalized: print(f'  {row["signal_key"]:<40} [{row["class"]:<12}] {row["market_scope"]}')
    if dry_run: print('\n[DRY-RUN] no writes'); return
    written = 0
    for i in range(0, len(normalized), 100):
        pr = requests.post(f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport,market_scope',
                           headers=H_WRITE, json=normalized[i:i+100], timeout=15)
        if pr.status_code in (200, 201, 204): written += min(100, len(normalized)-i)
        else: print(f'  ✗ {pr.status_code}: {pr.text[:200]}')
    print(f'  ✓ wrote {written} NCAAB signals')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    upsert(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
