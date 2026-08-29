#!/usr/bin/env python3
"""Seed signals that read the new *_team_defense_stats tables (2026-08-28).

Companions to nfl_team_defense_backfill.py + ncaaf_team_defense_backfill.py.
Requires migrations 20260828c (NFL) + 20260828d (NCAAF) applied and
backfill runs completed — otherwise conditions evaluate to None and
signals just don't fire.

NFL — 4 signals reading nfl_team_defense_stats fields joined to ctx:
  - nfl_def_matchup_pass_top     opp DEF is top-5 vs pass  → fade OFF pass
  - nfl_def_matchup_pass_bottom  opp DEF is bottom-5 vs pass → OVER lean
  - nfl_def_matchup_rush_top     opp DEF is top-5 vs rush → fade rush
  - nfl_def_ppg_extreme_low      opp DEF <18 PPG → UNDER lean

NCAAF — 4 signals reading ncaaf_team_defense_stats fields joined to ctx:
  - ncaaf_def_pass_epa_elite     opp DEF pass EPA ≤ -0.10/play
  - ncaaf_def_rush_epa_elite     opp DEF rush EPA ≤ -0.10/play
  - ncaaf_def_ppg_elite          opp DEF ≤ 17 PPG → UNDER lean
  - ncaaf_def_success_leaky      opp DEF success ≥ 0.48 → OVER lean

Note: signals read from ctx.{home,away}_def_ppg etc., which requires
game_context builders to enrich rows from the *_team_defense_stats
tables. That enrichment wiring is a follow-up — this seed just gets
the signal defs in place so the ctx enricher change lights them up
immediately.

Idempotent via on_conflict=signal_key,sport.
Run: python _seed_defense_stats_signals_2026-08-28.py
"""
from __future__ import annotations
import os, sys
from pathlib import Path

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

import requests
SB  = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
     'Content-Type': 'application/json',
     'Prefer': 'resolution=merge-duplicates,return=minimal'}


# ─── NFL ──────────────────────────────────────────────────────────────
NFL = [
    {
        'signal_key': 'nfl_def_matchup_pass_top',
        'sport': 'NFL', 'class': 'matchup', 'market_scope': 'total',
        'condition_expr': ('ctx.home_def_pass_ypg is not None and ctx.away_def_pass_ypg is not None and '
                           'min(float(ctx.home_def_pass_ypg), float(ctx.away_def_pass_ypg)) <= 200.0'),
        'side_expr': "'UNDER'",
        'strength_expr': ('max(0.4, min(1.0, (220.0 - min(float(ctx.home_def_pass_ypg), float(ctx.away_def_pass_ypg))) / 40.0))'),
        'display_prose_template': 'one team ranks top-5 vs the pass — capped passing game',
        'description': 'NFL — either defense in this game limits passing yardage to elite range (≤200 pass_ypg allowed).',
        'enabled': True,
        'origin': 'seed_defense_stats_2026-08-28',
    },
    {
        'signal_key': 'nfl_def_matchup_pass_bottom',
        'sport': 'NFL', 'class': 'matchup', 'market_scope': 'total',
        'condition_expr': ('ctx.home_def_pass_ypg is not None and ctx.away_def_pass_ypg is not None and '
                           'max(float(ctx.home_def_pass_ypg), float(ctx.away_def_pass_ypg)) >= 260.0'),
        'side_expr': "'OVER'",
        'strength_expr': ('max(0.4, min(1.0, (max(float(ctx.home_def_pass_ypg), float(ctx.away_def_pass_ypg)) - 240.0) / 40.0))'),
        'display_prose_template': 'one defense in the bottom tier vs the pass — leaky secondary',
        'description': 'NFL — a defense that gives up 260+ pass_ypg opens the OVER.',
        'enabled': True,
        'origin': 'seed_defense_stats_2026-08-28',
    },
    {
        'signal_key': 'nfl_def_matchup_rush_top',
        'sport': 'NFL', 'class': 'matchup', 'market_scope': 'total',
        'condition_expr': ('ctx.home_def_rush_ypg is not None and ctx.away_def_rush_ypg is not None and '
                           'min(float(ctx.home_def_rush_ypg), float(ctx.away_def_rush_ypg)) <= 95.0'),
        'side_expr': "'UNDER'",
        'strength_expr': ('max(0.4, min(1.0, (110.0 - min(float(ctx.home_def_rush_ypg), float(ctx.away_def_rush_ypg))) / 30.0))'),
        'display_prose_template': 'one defense ranks top-5 vs the rush — clock-eating shootout unlikely',
        'description': 'NFL — a defense stopping the run (≤95 rush_ypg) suppresses total.',
        'enabled': True,
        'origin': 'seed_defense_stats_2026-08-28',
    },
    {
        'signal_key': 'nfl_def_ppg_extreme_low',
        'sport': 'NFL', 'class': 'matchup', 'market_scope': 'total',
        'condition_expr': ('ctx.home_def_ppg is not None and ctx.away_def_ppg is not None and '
                           '(float(ctx.home_def_ppg) + float(ctx.away_def_ppg)) / 2.0 <= 18.0'),
        'side_expr': "'UNDER'",
        'strength_expr': ('max(0.5, min(1.0, (20.0 - (float(ctx.home_def_ppg) + float(ctx.away_def_ppg)) / 2.0) / 5.0))'),
        'display_prose_template': 'both defenses ≤18 PPG allowed — grinder',
        'description': 'NFL — both defenses in the elite PPG-allowed range indicates a low-scoring game.',
        'enabled': True,
        'origin': 'seed_defense_stats_2026-08-28',
    },
]

# ─── NCAAF ────────────────────────────────────────────────────────────
NCAAF = [
    {
        'signal_key': 'ncaaf_def_pass_epa_elite',
        'sport': 'NCAAF', 'class': 'matchup', 'market_scope': 'total',
        'condition_expr': ('ctx.home_def_pass_epa_allowed is not None and ctx.away_def_pass_epa_allowed is not None and '
                           'min(float(ctx.home_def_pass_epa_allowed), float(ctx.away_def_pass_epa_allowed)) <= -0.10'),
        'side_expr': "'UNDER'",
        'strength_expr': ('max(0.4, min(1.0, (-0.10 - min(float(ctx.home_def_pass_epa_allowed), float(ctx.away_def_pass_epa_allowed))) / 0.15))'),
        'display_prose_template': 'a defense grades elite vs the pass (EPA ≤ -0.10)',
        'description': 'NCAAF — an elite pass defense suppresses total.',
        'enabled': True,
        'origin': 'seed_defense_stats_2026-08-28',
    },
    {
        'signal_key': 'ncaaf_def_rush_epa_elite',
        'sport': 'NCAAF', 'class': 'matchup', 'market_scope': 'total',
        'condition_expr': ('ctx.home_def_rush_epa_allowed is not None and ctx.away_def_rush_epa_allowed is not None and '
                           'min(float(ctx.home_def_rush_epa_allowed), float(ctx.away_def_rush_epa_allowed)) <= -0.10'),
        'side_expr': "'UNDER'",
        'strength_expr': ('max(0.4, min(1.0, (-0.10 - min(float(ctx.home_def_rush_epa_allowed), float(ctx.away_def_rush_epa_allowed))) / 0.15))'),
        'display_prose_template': 'a defense grades elite vs the run (EPA ≤ -0.10)',
        'description': 'NCAAF — an elite rush defense suppresses total.',
        'enabled': True,
        'origin': 'seed_defense_stats_2026-08-28',
    },
    {
        'signal_key': 'ncaaf_def_ppg_elite',
        'sport': 'NCAAF', 'class': 'matchup', 'market_scope': 'total',
        'condition_expr': ('ctx.home_def_ppg is not None and ctx.away_def_ppg is not None and '
                           '(float(ctx.home_def_ppg) + float(ctx.away_def_ppg)) / 2.0 <= 17.0'),
        'side_expr': "'UNDER'",
        'strength_expr': ('max(0.5, min(1.0, (20.0 - (float(ctx.home_def_ppg) + float(ctx.away_def_ppg)) / 2.0) / 5.0))'),
        'display_prose_template': 'both defenses ≤17 PPG allowed — smash-mouth',
        'description': 'NCAAF — both defenses elite PPG-allowed indicates low-scoring style.',
        'enabled': True,
        'origin': 'seed_defense_stats_2026-08-28',
    },
    {
        'signal_key': 'ncaaf_def_success_leaky',
        'sport': 'NCAAF', 'class': 'matchup', 'market_scope': 'total',
        'condition_expr': ('ctx.home_def_success_rate_allowed is not None and ctx.away_def_success_rate_allowed is not None and '
                           'max(float(ctx.home_def_success_rate_allowed), float(ctx.away_def_success_rate_allowed)) >= 0.48'),
        'side_expr': "'OVER'",
        'strength_expr': ('max(0.4, min(1.0, (max(float(ctx.home_def_success_rate_allowed), float(ctx.away_def_success_rate_allowed)) - 0.45) / 0.10))'),
        'display_prose_template': 'a defense giving up success rate ≥ 48% — leaky',
        'description': 'NCAAF — a defense leaking success rate opens the OVER.',
        'enabled': True,
        'origin': 'seed_defense_stats_2026-08-28',
    },
]


def main():
    total = len(NFL) + len(NCAAF)
    print(f'=== seed defense_stats signals · {total} rows ({len(NFL)} NFL + {len(NCAAF)} NCAAF) ===')
    for row in NFL + NCAAF:
        r = requests.post(f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport',
                          headers=H, json=row, timeout=15)
        status = 'OK' if r.status_code in (200, 201, 204) else f'FAIL {r.status_code}'
        print(f'  {status} {row["signal_key"]}')
        if r.status_code not in (200, 201, 204):
            print(f'    {r.text[:250]}')
    print('DONE')


if __name__ == '__main__':
    main()
