#!/usr/bin/env python3
"""Seed penalty-tendency signals for NFL + NCAAF (2026-08-28).

Reads ctx.{home,away}_penalties_pg and ctx.{home,away}_penalty_yds_pg
fields, populated by the respective game_context builders from
nfl_team_stats (NFL) / ncaaf_team_stats after CFBD /stats/season pull
(NCAAF).

Each sport gets:
  - a "sloppy game" flag when both teams average >7 penalties/g
    (or NCAAF >8/g — CFB runs looser)
  - a "disciplined edge" flag on ATS when one team is <5 penalties/g
    while the opponent is >7 (undisciplined opponent gives up drives)

Undisciplined teams historically under-perform ATS by ~5pp. These
signals should be treated as light — market_scope='rl' for the
ATS-edge signals, 'total' for the sloppy-game flag.

Idempotent via on_conflict=signal_key,sport.
Run: python _seed_penalty_signals_2026-08-28.py
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


SIGNALS = [
    # ─── NFL ──────────────────────────────────────────────────────
    {
        'signal_key':   'nfl_penalty_home_undisciplined',
        'sport':        'NFL', 'class': 'discipline', 'market_scope': 'rl',
        'condition_expr': ('ctx.home_penalties_pg is not None and float(ctx.home_penalties_pg) >= 7.5'),
        'side_expr':    "'AWAY_RL'",
        'strength_expr': 'min(1.0, (float(ctx.home_penalties_pg) - 6.5) / 3.0) + 0.4',
        'display_prose_template': 'home averages {home_penalties_pg} penalties/game — undisciplined',
        'description': 'NFL — fade home ATS when home team averages 7.5+ penalties/g.',
        'enabled': True, 'origin': 'seed_penalty_2026-08-28',
    },
    {
        'signal_key':   'nfl_penalty_away_undisciplined',
        'sport':        'NFL', 'class': 'discipline', 'market_scope': 'rl',
        'condition_expr': ('ctx.away_penalties_pg is not None and float(ctx.away_penalties_pg) >= 7.5'),
        'side_expr':    "'HOME_RL'",
        'strength_expr': 'min(1.0, (float(ctx.away_penalties_pg) - 6.5) / 3.0) + 0.4',
        'display_prose_template': 'away averages {away_penalties_pg} penalties/game — undisciplined',
        'description': 'NFL — back home ATS when away team averages 7.5+ penalties/g.',
        'enabled': True, 'origin': 'seed_penalty_2026-08-28',
    },
    {
        'signal_key':   'nfl_penalty_sloppy_both',
        'sport':        'NFL', 'class': 'discipline', 'market_scope': 'total',
        'condition_expr': ('ctx.home_penalties_pg is not None and ctx.away_penalties_pg is not None '
                           'and (float(ctx.home_penalties_pg) + float(ctx.away_penalties_pg)) / 2.0 >= 7.0'),
        'side_expr':    "'OVER'",
        'strength_expr': ('min(1.0, ((float(ctx.home_penalties_pg) + float(ctx.away_penalties_pg)) / 2.0 - 6.5) / 2.0) + 0.4'),
        'display_prose_template': 'both teams heavy on penalties — extended drives push OVER',
        'description': 'NFL — sloppy penalty games lean OVER (defensive holds extend drives).',
        'enabled': True, 'origin': 'seed_penalty_2026-08-28',
    },

    # ─── NCAAF ────────────────────────────────────────────────────
    {
        'signal_key':   'ncaaf_penalty_home_undisciplined',
        'sport':        'NCAAF', 'class': 'discipline', 'market_scope': 'rl',
        'condition_expr': ('ctx.home_penalties_pg is not None and float(ctx.home_penalties_pg) >= 8.0'),
        'side_expr':    "'AWAY_RL'",
        'strength_expr': 'min(1.0, (float(ctx.home_penalties_pg) - 7.0) / 3.0) + 0.4',
        'display_prose_template': 'home averages {home_penalties_pg} penalties/game — undisciplined',
        'description': 'NCAAF — fade home ATS when home averages 8+ penalties/g (CFB runs looser than NFL).',
        'enabled': True, 'origin': 'seed_penalty_2026-08-28',
    },
    {
        'signal_key':   'ncaaf_penalty_away_undisciplined',
        'sport':        'NCAAF', 'class': 'discipline', 'market_scope': 'rl',
        'condition_expr': ('ctx.away_penalties_pg is not None and float(ctx.away_penalties_pg) >= 8.0'),
        'side_expr':    "'HOME_RL'",
        'strength_expr': 'min(1.0, (float(ctx.away_penalties_pg) - 7.0) / 3.0) + 0.4',
        'display_prose_template': 'away averages {away_penalties_pg} penalties/game — undisciplined',
        'description': 'NCAAF — back home ATS when away averages 8+ penalties/g.',
        'enabled': True, 'origin': 'seed_penalty_2026-08-28',
    },
    {
        'signal_key':   'ncaaf_penalty_sloppy_both',
        'sport':        'NCAAF', 'class': 'discipline', 'market_scope': 'total',
        'condition_expr': ('ctx.home_penalties_pg is not None and ctx.away_penalties_pg is not None '
                           'and (float(ctx.home_penalties_pg) + float(ctx.away_penalties_pg)) / 2.0 >= 7.5'),
        'side_expr':    "'OVER'",
        'strength_expr': ('min(1.0, ((float(ctx.home_penalties_pg) + float(ctx.away_penalties_pg)) / 2.0 - 7.0) / 2.0) + 0.4'),
        'display_prose_template': 'both teams heavy on penalties — extended drives push OVER',
        'description': 'NCAAF — sloppy penalty games lean OVER.',
        'enabled': True, 'origin': 'seed_penalty_2026-08-28',
    },
]


def main():
    print(f'=== seed penalty signals · {len(SIGNALS)} rows ===')
    for row in SIGNALS:
        r = requests.post(f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport',
                          headers=H, json=row, timeout=15)
        status = 'OK' if r.status_code in (200, 201, 204) else f'FAIL {r.status_code}'
        print(f'  {status} {row["signal_key"]}')
        if r.status_code not in (200, 201, 204):
            print(f'    {r.text[:250]}')
    print('DONE')


if __name__ == '__main__':
    main()
