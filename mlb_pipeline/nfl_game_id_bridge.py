"""nfl_game_id_bridge — helpers for the NFL game_id schism.

2026-09-15 · centralizes the workaround for [[project_nfl_game_id_mismatch_911]].

`nfl_game_context` uses the Odds API event hash (`5ad8135dc2b5f27de0b777acd317855a`).
`nfl_game_results` uses schedule-format ids (`20260915_DEN_KC`).
Zero overlap on raw `game_id` join → every naive ctx→results join
silently no-ops. Fix everywhere is: match by (game_date, home_team,
away_team) composite with a small date-window tolerance.

This module wraps that pattern so new consumers don't have to
re-derive it. Sibling helper for NCAAF exposed via `sport='NCAAF'`.

Usage:

    from nfl_game_id_bridge import ctx_id_to_results_id

    # For a single ctx game_id
    results_id = ctx_id_to_results_id(
        ctx_game_id='5ad8135dc2b5f27de0b777acd317855a',
        ctx_game_date='2026-09-15',
        home_team='KC', away_team='DEN',
    )
    # results_id = '20260915_DEN_KC'  or None if no match

    # For a batch of ctx rows
    from nfl_game_id_bridge import bulk_ctx_to_results_ids
    mapping = bulk_ctx_to_results_ids(
        ctx_rows=[
            {'game_id': 'abc...', 'game_date': '2026-09-15', 'home_team': 'KC', 'away_team': 'DEN'},
            ...
        ],
        sport='NFL',
    )
    # mapping = {'abc...': '20260915_DEN_KC', ...}

See also grade_jerry_reads.py:203-234 for the reference tuple-lookup
pattern, and resolve_nfl_results.py for another patched consumer.
"""
from __future__ import annotations
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

for _env in (Path(__file__).parent / '.env', Path(__file__).parent.parent / '.env'):
    if _env.exists():
        for line in _env.read_text().split('\n'):
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

import requests

_SB = os.environ.get('SUPABASE_URL')
_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')
_H = {'apikey': _KEY or '', 'Authorization': f'Bearer {_KEY or ""}'}

_RESULTS_TABLE_BY_SPORT = {
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
}


def ctx_id_to_results_id(
    ctx_game_id: str,
    ctx_game_date: str,
    home_team: str,
    away_team: str,
    sport: str = 'NFL',
    date_window_days: int = 1,
) -> Optional[str]:
    """Return the results-table game_id matching the given ctx row, or None.

    Matches on (game_date ±date_window_days, home_team, away_team). The
    window absorbs timezone drift where ctx.game_date and results.game_date
    can disagree by 1 day for late-night games.

    Returns None when no match — caller decides whether to retry with a
    wider window, log a warning, or fall through.
    """
    if not (_SB and _KEY):
        return None
    tbl = _RESULTS_TABLE_BY_SPORT.get((sport or '').upper())
    if not tbl or not ctx_game_date or not home_team or not away_team:
        return None
    try:
        d = date.fromisoformat(ctx_game_date)
        lo = (d - timedelta(days=date_window_days)).isoformat()
        hi = (d + timedelta(days=date_window_days)).isoformat()
        r = requests.get(
            f'{_SB}/rest/v1/{tbl}',
            headers=_H,
            params={
                'select': 'game_id',
                'game_date': f'gte.{lo}',
                'and': f'(game_date.lte.{hi},home_team.eq.{home_team},away_team.eq.{away_team})',
                'limit': '2',
            },
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list) or not rows:
            return None
        return rows[0].get('game_id')
    except Exception:
        return None


def bulk_ctx_to_results_ids(
    ctx_rows: list,
    sport: str = 'NFL',
    date_window_days: int = 1,
) -> dict:
    """Given a list of ctx rows (with game_id, game_date, home_team, away_team),
    return a dict mapping ctx game_id → results game_id (or None per row).

    Uses one bulk fetch of the results-table date-window then joins in Python.
    Cheaper than per-row queries when the ctx batch is >~5 rows.
    """
    if not ctx_rows or not (_SB and _KEY):
        return {}
    tbl = _RESULTS_TABLE_BY_SPORT.get((sport or '').upper())
    if not tbl:
        return {}
    dates = [r.get('game_date') for r in ctx_rows if r.get('game_date')]
    if not dates:
        return {}
    dmin = min(dates); dmax = max(dates)
    lo = (date.fromisoformat(dmin) - timedelta(days=date_window_days)).isoformat()
    hi = (date.fromisoformat(dmax) + timedelta(days=date_window_days)).isoformat()
    try:
        r = requests.get(
            f'{_SB}/rest/v1/{tbl}',
            headers=_H,
            params={
                'select': 'game_id,game_date,home_team,away_team',
                'game_date': f'gte.{lo}',
                'and': f'(game_date.lte.{hi})',
                'limit': '2000',
            },
            timeout=15,
        )
        res_rows = r.json() if r.status_code == 200 else []
        by_composite = {
            (row.get('home_team'), row.get('away_team')): row.get('game_id')
            for row in res_rows if isinstance(row, dict)
        }
    except Exception:
        return {}
    return {
        r['game_id']: by_composite.get((r.get('home_team'), r.get('away_team')))
        for r in ctx_rows if r.get('game_id')
    }


if __name__ == '__main__':
    # Smoke test: MNF DEN@KC 2026-09-15 (known case from the schism audit)
    rid = ctx_id_to_results_id(
        ctx_game_id='5ad8135dc2b5f27de0b777acd317855a',
        ctx_game_date='2026-09-15',
        home_team='KC', away_team='DEN',
        sport='NFL',
    )
    print(f'MNF DEN@KC 2026-09-15 → results_id={rid}')
