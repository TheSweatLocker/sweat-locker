"""Totals cohort attribution helper (2026-08-01 · E-4).

Loaded once by Jerry synthesis + game reads scripts. Given a game
context row, returns the list of firing cohorts with their backtested
hit rates so Jerry's prompt cites concrete historical attribution
when synthesizing totals reads.

Usage:
    from totals_cohort_attribution import load_stats, attribute
    stats = load_stats('MLB')
    cohorts = attribute(game_ctx, stats)
    # -> [{'cohort': 'road_trip_long_away', 'direction': 'UNDER',
    #      'pct': 70.6, 'n': 17, 'description': '...'}]
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Import the sport-specific signal computer(s)
try:
    from totals_cohort_backfill import MLBTotalsSignals
    _MLB_SIGS = MLBTotalsSignals()
except ImportError:
    _MLB_SIGS = None


def load_stats(sport: str = 'MLB') -> dict:
    """Return {(cohort_name, direction): {pct_lifetime, n_lifetime, pct_30d, n_30d, desc}}"""
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    if not url or not key: return {}
    h = {'apikey': key, 'Authorization': f'Bearer {key}'}
    r = requests.get(f'{url}/rest/v1/totals_cohort_signals',
                     headers=h,
                     params={'sport': f'eq.{sport}', 'select': '*',
                             'limit': '200'}, timeout=15)
    if r.status_code != 200: return {}
    data = r.json() if isinstance(r.json(), list) else []
    stats = {}
    for row in data:
        stats[(row['cohort_name'], row['direction'])] = {
            'pct': row.get('lifetime_pct'),
            'n': row.get('lifetime_n'),
            'pct_30d': row.get('last_30d_pct'),
            'n_30d': row.get('last_30d_n'),
            'description': row.get('description'),
        }
    return stats


def attribute(ctx: dict, stats: dict, sport: str = 'MLB',
              min_n: int = 12, min_edge_pp: float = 5.0) -> list:
    """Return list of firing cohorts with meaningful backtested edge.

    Filters:
      min_n:       cohort must have ≥N historical firings (default 12)
      min_edge_pp: cohort must be ≥Xpp off 50% (default 5pp — filters noise
                   like a 50% or 47% hit rate that adds no signal to Jerry)

    Sorted by strength (deviation from 50%).
    """
    if sport != 'MLB' or _MLB_SIGS is None: return []
    fired = _MLB_SIGS.extract(ctx)
    out = []
    for cohort_name, direction, description in fired:
        s = stats.get((cohort_name, direction))
        if not s: continue
        n = s.get('n') or 0
        pct = s.get('pct')
        if n < min_n or pct is None: continue
        strength = abs(float(pct) - 50)
        if strength < min_edge_pp: continue
        out.append({
            'cohort': cohort_name,
            'direction': direction,
            'pct': float(pct),
            'n': int(n),
            'strength': strength,
            'description': description,
        })
    out.sort(key=lambda x: -x['strength'])
    return out


def format_for_prompt(cohorts: list) -> str:
    """Human-readable block for Jerry prompt injection."""
    if not cohorts: return 'No totals cohorts firing (either no signals matched or insufficient sample).'
    lines = ['TOTALS COHORTS FIRING (backtested):']
    for c in cohorts:
        lines.append(
            f"  · {c['cohort']} → {c['direction']} · "
            f"{c['pct']:.1f}% historical (n={c['n']}) · {c['description']}"
        )
    return '\n'.join(lines)
