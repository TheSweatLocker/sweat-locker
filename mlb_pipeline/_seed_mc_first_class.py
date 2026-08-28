#!/usr/bin/env python3
"""Seed MC as a FIRST-CLASS weighted opinion in signal_sources (2026-08-28).

Root fix for the 8/28 lotto audit finding: ensemble was publishing PRIME/
STRONG picks that MC disagreed with by 15-30pp on 11 of 13 games. Cause:
MC only existed as a FADE CHECK (`should_flip_pick_via_consensus`) and as
CONFLUENCE MULTIPLIERS (mc_panel_agree_under, etc.). MC had no standalone
strong voice in the initial scoring — cohort + panel + team_form could
outvote it.

This seeds 4 new signal_sources rows that give MC a direct opinion when
confidence is high (>=58%):
  - mc_strong_home_ml: fires when mc_p_home_win >= 0.58 → HOME_ML with weight
  - mc_strong_away_ml: fires when mc_p_away_win >= 0.58 → AWAY_ML with weight
  - mc_strong_over: fires when mc_p_over >= 0.58 → OVER with weight
  - mc_strong_under: fires when mc_p_under >= 0.58 → UNDER with weight

Strength scales linearly: 0.5 at 58%, 1.0 at 75%+. Signals are tagged
with class='model' and start enabled + weight-registry-linked so they
graduate from RAMP_UP_PRIOR to measured tier once 30d of grades land.

Idempotent — safe to re-run. Delete file after seeding (matches project
convention: seed scripts are one-shot).

Run: python _seed_mc_first_class.py
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
H   = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
       'Content-Type': 'application/json', 'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Strength: 0.5 at 58%, scales to 1.0 at 75%+
def cond(mc_field: str) -> str:
    return (
        f'ctx.mc_probabilities is not None and '
        f'(ctx.mc_probabilities or {{}}).get("{mc_field}") is not None and '
        f'float((ctx.mc_probabilities or {{}}).get("{mc_field}")) >= 0.58'
    )

def strength(mc_field: str) -> str:
    # 0.5 at 58%, scales to 1.0 at 75%+
    return (
        f'min(max((float((ctx.mc_probabilities or {{}}).get("{mc_field}") or 0) - 0.58) / 0.17, 0.0), 1.0) + 0.5'
    )

ROWS = [
    {
        'signal_key':  'mc_strong_home_ml',
        'sport':       'MLB',
        'class':       'model',
        'market_scope': 'ml',
        'condition_expr': cond('mc_p_home_win'),
        'side_expr':   "'HOME_ML'",
        'strength_expr': strength('mc_p_home_win'),
        'display_prose_template': 'Monte Carlo sim has home team win probability at 58%+',
        'description': 'MC first-class opinion — HOME ML when sim probability >= 58%. Root fix for 8/28 ensemble/MC divergence.',
        'enabled':     True,
        'origin':      'seed_mc_first_class_2026-08-28',
    },
    {
        'signal_key':  'mc_strong_away_ml',
        'sport':       'MLB',
        'class':       'model',
        'market_scope': 'ml',
        'condition_expr': cond('mc_p_away_win'),
        'side_expr':   "'AWAY_ML'",
        'strength_expr': strength('mc_p_away_win'),
        'display_prose_template': 'Monte Carlo sim has away team win probability at 58%+',
        'description': 'MC first-class opinion — AWAY ML when sim probability >= 58%. Root fix for 8/28 ensemble/MC divergence.',
        'enabled':     True,
        'origin':      'seed_mc_first_class_2026-08-28',
    },
    {
        'signal_key':  'mc_strong_over',
        'sport':       'MLB',
        'class':       'model',
        'market_scope': 'total',
        'condition_expr': cond('mc_p_over'),
        'side_expr':   "'OVER'",
        'strength_expr': strength('mc_p_over'),
        'display_prose_template': 'Monte Carlo sim has OVER hitting 58%+ of trials',
        'description': 'MC first-class opinion — OVER when sim probability >= 58%. Root fix for 8/28 ensemble/MC divergence.',
        'enabled':     True,
        'origin':      'seed_mc_first_class_2026-08-28',
    },
    {
        'signal_key':  'mc_strong_under',
        'sport':       'MLB',
        'class':       'model',
        'market_scope': 'total',
        'condition_expr': cond('mc_p_under'),
        'side_expr':   "'UNDER'",
        'strength_expr': strength('mc_p_under'),
        'display_prose_template': 'Monte Carlo sim has UNDER hitting 58%+ of trials',
        'description': 'MC first-class opinion — UNDER when sim probability >= 58%. Root fix for 8/28 ensemble/MC divergence.',
        'enabled':     True,
        'origin':      'seed_mc_first_class_2026-08-28',
    },
]


def main():
    print(f'=== seed_mc_first_class ({len(ROWS)} rows) ===')
    for row in ROWS:
        r = requests.post(
            f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport',
            headers=H, json=row, timeout=15,
        )
        status = 'OK' if r.status_code in (200, 201, 204) else f'FAIL {r.status_code}'
        print(f'  {status} {row["signal_key"]}')
        if r.status_code not in (200, 201, 204):
            print(f'    {r.text[:250]}')
    print('DONE')


if __name__ == '__main__':
    main()
