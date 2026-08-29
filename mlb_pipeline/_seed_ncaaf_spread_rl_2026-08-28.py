#!/usr/bin/env python3
"""Seed + patch NCAAF spread-market signals (2026-08-28).

Root fix for Week 1 audit finding: 44/56 NCAAF picks were ML, only 2 RL,
including 30-point favorites where the spread is the only interesting side.

Two things this script does:

1) PATCH the sign convention on `ncaaf_home_spread_edge` and
   `ncaaf_away_spread_edge`. Prior formula assumed both `projected_spread`
   and `close_spread` used the same sign convention. They don't:
     - close_spread: book convention → negative when home favored (e.g. -30.5)
     - projected_spread: model convention → positive when home favored (e.g. +26.26)
   The old `(projected_spread - close_spread) >= 3` was `26.26 - (-30.5) = 56.76`
   for every home favorite, always firing HOME_RL regardless of edge.
   Correct: home edge = `projected_spread + close_spread` (both on same scale).

2) SEED three new RL-scoped model signals — the original `ncaaf_projected_spread`
   and `ncaaf_sp_plus_edge_*` were market_scope=ml (only emit ML picks).
   Names suggest spread signals. Now companion _rl versions exist so the
   RL market gets scored from the same model inputs.

Idempotent via on_conflict=signal_key,sport. Safe to re-run.
Run: python _seed_ncaaf_spread_rl_2026-08-28.py
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
H_UPSERT = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
            'Content-Type': 'application/json',
            'Prefer': 'resolution=merge-duplicates,return=minimal'}
H_PATCH = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
           'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# ─── PATCHES to existing rows (fix sign convention) ───────────────────
PATCHES = [
    {
        'signal_key': 'ncaaf_home_spread_edge',
        'condition_expr': 'ctx.projected_spread is not None and ctx.close_spread is not None and (float(ctx.projected_spread) + float(ctx.close_spread)) >= 3.0',
        'strength_expr':  'min((float(ctx.projected_spread) + float(ctx.close_spread)) / 8.0, 1.0)',
        'display_prose_template': 'model favors home by {projected_spread} vs market {close_spread} spread',
    },
    {
        'signal_key': 'ncaaf_away_spread_edge',
        'condition_expr': 'ctx.projected_spread is not None and ctx.close_spread is not None and (-(float(ctx.projected_spread) + float(ctx.close_spread))) >= 3.0',
        'strength_expr':  'min((-(float(ctx.projected_spread) + float(ctx.close_spread))) / 8.0, 1.0)',
        'display_prose_template': 'model less bullish on home vs the market {close_spread} spread',
    },
]

# ─── SEEDS for new RL-scoped model signals ────────────────────────────
SEEDS = [
    {
        'signal_key': 'ncaaf_projected_spread_rl',
        'sport': 'NCAAF', 'class': 'model', 'market_scope': 'rl', 'enabled': True,
        'condition_expr': 'ctx.projected_spread is not None and ctx.close_spread is not None and abs(float(ctx.projected_spread) + float(ctx.close_spread)) >= 3.0',
        'side_expr': '"HOME_RL" if (float(ctx.projected_spread) + float(ctx.close_spread)) > 0 else "AWAY_RL"',
        'strength_expr': 'min(abs(float(ctx.projected_spread) + float(ctx.close_spread)) / 8.0, 1.0)',
        'display_prose_template': 'model projects {projected_spread} vs market {close_spread}',
        'description': 'NCAAF model spread edge — emits RL when |model - book| >= 3 pts. 2026-08-28 companion to _ml version.',
        'origin': 'seed_ncaaf_spread_rl_2026-08-28',
    },
    {
        'signal_key': 'ncaaf_sp_plus_edge_home_rl',
        'sport': 'NCAAF', 'class': 'model', 'market_scope': 'rl', 'enabled': True,
        'condition_expr': 'ctx.home_sp_plus is not None and ctx.away_sp_plus is not None and ctx.close_spread is not None and (float(ctx.home_sp_plus) - float(ctx.away_sp_plus) + float(ctx.close_spread)) >= 3.0',
        'side_expr': '"HOME_RL"',
        'strength_expr': 'min((float(ctx.home_sp_plus) - float(ctx.away_sp_plus) + float(ctx.close_spread)) / 10.0, 1.0)',
        'display_prose_template': 'SP+ rates home {home_sp_plus} vs away {away_sp_plus} — favors home spread',
        'description': 'NCAAF SP+ edge — HOME_RL when SP+ margin exceeds market spread by 3+.',
        'origin': 'seed_ncaaf_spread_rl_2026-08-28',
    },
    {
        'signal_key': 'ncaaf_sp_plus_edge_away_rl',
        'sport': 'NCAAF', 'class': 'model', 'market_scope': 'rl', 'enabled': True,
        'condition_expr': 'ctx.home_sp_plus is not None and ctx.away_sp_plus is not None and ctx.close_spread is not None and (float(ctx.away_sp_plus) - float(ctx.home_sp_plus) - float(ctx.close_spread)) >= 3.0',
        'side_expr': '"AWAY_RL"',
        'strength_expr': 'min((float(ctx.away_sp_plus) - float(ctx.home_sp_plus) - float(ctx.close_spread)) / 10.0, 1.0)',
        'display_prose_template': 'SP+ rates away {away_sp_plus} vs home {home_sp_plus} — favors away spread',
        'description': 'NCAAF SP+ edge — AWAY_RL when SP+ margin favors away by 3+ vs market.',
        'origin': 'seed_ncaaf_spread_rl_2026-08-28',
    },
]


def main():
    print(f'=== seed_ncaaf_spread_rl · {len(PATCHES)} patches + {len(SEEDS)} seeds ===')
    for row in PATCHES:
        key = row.pop('signal_key')
        r = requests.patch(f'{SB}/rest/v1/signal_sources?sport=eq.NCAAF&signal_key=eq.{key}',
                           headers=H_PATCH, json=row, timeout=15)
        status = 'OK' if r.status_code in (200, 201, 204) else f'FAIL {r.status_code}'
        print(f'  PATCH {status} {key}')
    for row in SEEDS:
        r = requests.post(f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport',
                          headers=H_UPSERT, json=row, timeout=15)
        status = 'OK' if r.status_code in (200, 201, 204) else f'FAIL {r.status_code}'
        print(f'  SEED  {status} {row["signal_key"]}')
        if r.status_code not in (200, 201, 204):
            print(f'    {r.text[:250]}')
    print('DONE')


if __name__ == '__main__':
    main()
