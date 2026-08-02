"""Cohort hit-rate lookup (2026-08-01 · Jerry Brain cross-sport helper).

Reads mlb_tier_calibration (shared cohort hit-rate table populated by
{sport}_cohort_backfill.py scripts) so Jerry synth for NFL / NCAAF /
NCAAB can cite backtested cohort win rates when generating game reads.

Populated data as of 8/1/2026:
  MLB   — cohort-attributed via attribution_cohorts_v5_recency.json
  NFL   — 696 rows (4 seasons, incl heavy_home_dog 65.4% n=81)
  NCAAF — populated when ncaaf_cohort_backfill.py runs
  NCAAB — populated when ncaab_cohort_backfill.py runs

Sport-universal: works for any sport that writes to mlb_tier_calibration
with its sport tag. Follows the load/attribute/format contract from
bucket_roi_lookup.py + totals_cohort_attribution.py.

Usage:
    from cohort_hit_rates_lookup import load_cohort_rates, format_for_prompt

    rates = load_cohort_rates('NFL')
    fired = ['nfl_heavy_home_dog', 'nfl_short_week']
    prompt_text = format_for_prompt(fired, rates)
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Smart-injection gates (mirror Path B safeguards from bucket_roi_lookup)
MIN_SAMPLE_N = 25          # too-thin cohorts suppressed
MIN_EDGE_PP  = 5.0         # <5pp off 50% = noise, don't inject


def load_cohort_rates(sport: str) -> dict:
    """Return {cohort_name: {hits, total, hit_rate, computed_date}}.

    Sport tag is case-insensitive on read (some scripts wrote lowercase,
    others uppercase — legacy inconsistency).
    """
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    if not url or not key: return {}
    h = {'apikey': key, 'Authorization': f'Bearer {key}'}
    # PostgREST OR filter to catch both case variants
    r = requests.get(f'{url}/rest/v1/mlb_tier_calibration',
                     headers=h,
                     params={
                         'or': f'(sport.eq.{sport.lower()},sport.eq.{sport.upper()})',
                         'select': 'tier,hits,total,hit_rate,computed_date,window_label',
                         'limit': '500',
                     }, timeout=15)
    if r.status_code != 200: return {}
    data = r.json() if isinstance(r.json(), list) else []
    # Prefer 'std' window when multiple exist for same cohort
    by_cohort: dict = {}
    for row in data:
        name = row.get('tier')
        if not name: continue
        prev = by_cohort.get(name)
        if prev and prev.get('window_label') == 'std' and row.get('window_label') != 'std':
            continue
        by_cohort[name] = row
    return by_cohort


def _is_worth_injecting(row: dict) -> bool:
    n = row.get('total') or 0
    if n < MIN_SAMPLE_N: return False
    hit = row.get('hit_rate')
    if hit is None: return False
    return abs(float(hit) * 100 - 50) >= MIN_EDGE_PP


def format_for_prompt(fired_cohorts: list, rates: dict, max_lines: int = 5) -> str:
    """Human-readable cohort block for Jerry prompt.

    fired_cohorts: list of cohort names that fired for THIS game (from the
                    sport-specific cohort tagger — e.g., nfl_game_context
                    computes which of the 10 NFL cohorts apply to a game).
    rates:         dict from load_cohort_rates().
    max_lines:     cap to prevent prompt bloat (Path B guardrail).

    Returns terse "no meaningful edge" string if no cohorts meet gates.
    """
    interesting = []
    for name in fired_cohorts:
        row = rates.get(name)
        if not row: continue
        if not _is_worth_injecting(row): continue
        pct = float(row['hit_rate']) * 100
        interesting.append({
            'cohort': name,
            'pct': pct,
            'n': row['total'],
            'hits': row['hits'],
            'edge': pct - 50,
        })
    if not interesting:
        return 'No meaningful historical cohort edges firing for this game.'
    # Sort by strength (deviation from 50%)
    interesting.sort(key=lambda x: -abs(x['edge']))
    lines = ['HISTORICAL COHORT HIT RATES (backtested):']
    for c in interesting[:max_lines]:
        mark = 'BACK-side signal' if c['edge'] >= 5 else ('FADE-side signal' if c['edge'] <= -5 else 'neutral')
        lines.append(
            f"  · {c['cohort']}: {c['pct']:.1f}% ({c['hits']}-{c['n']-c['hits']}) "
            f"n={c['n']}  → {mark}"
        )
    lines.append('IMPORTANT: These are historical bucket rates for the whole cohort, '
                 'not this specific game\\'s probability. Use as context, not as mandate.')
    return '\n'.join(lines)
