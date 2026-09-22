"""cohort_evidence — one place that turns a cohort name into a live rate.

2026-09-22. Driver labels and pick `sub` strings used to carry their
backtest result frozen into an f-string:

    f'both OPS L14 <= .650 (...) - 64.7% UNDER n=34'
    f'... (60d MC x thin-juice hits 43% n=7)'

Those numbers were copied out of signal_registry (or an ad-hoc backtest)
when the line was written and then never moved again. Proven, not
assumed: matching the literals back against the live table, eight still
sit on a real row with the identical rate and n — ops_l14_dual_ice is
still exactly 64.7% n=34. The code did not invent them, it stopped
reading a source that was updating the whole time.

A frozen rate is worse than no rate. It reads as a live audited claim,
the user cannot tell it is stale, and it drifts further from the truth
every day the cohort keeps playing.

So labels no longer STATE evidence, they ASK for it.

WHY THIS IS ITS OWN MODULE. The first version of this lived inside
play_of_day.py. game_context.py needs the identical rule, and copying it
is exactly the mistake that cost a day: backfill_jerry_pick_alignment
carried a second implementation of enforce_primary_play_alignment, so
fixing the shared one left the copy wrong and four games kept shipping
a contradiction. One rule, one file, every caller imports it.

The n-gate is not a policy layered on top — it is the same lookup.
A cohort with no row, or under 30 graded games, returns an empty string
and the caller's sentence simply ends after the observation. That is
feedback_sample_size_with_pct enforcing itself rather than being
policed, and it is why two labels citing n=7 and n=10 went silent
without anyone writing a check for them.
"""
from __future__ import annotations

import os
import requests

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

MIN_N = 30          # feedback_sample_size_with_pct
_CACHE: dict[str, dict] = {}


def load(sport: str = 'MLB') -> dict:
    """{signal_name: (hit_rate, sample_n)} from signal_registry. Cached."""
    sport = (sport or 'MLB').upper()
    if sport in _CACHE:
        return _CACHE[sport]
    out: dict = {}
    if not SUPABASE_URL or not SUPABASE_KEY:
        _CACHE[sport] = out
        return out
    headers = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    try:
        for off in range(0, 4000, 1000):
            r = requests.get(
                f'{SUPABASE_URL}/rest/v1/signal_registry',
                headers=headers, timeout=20,
                params={'select': 'signal_name,hit_rate,sample_n',
                        'sport': f'eq.{sport}', 'limit': 1000, 'offset': off})
            if r.status_code != 200:
                break
            chunk = r.json()
            if not isinstance(chunk, list) or not chunk:
                break
            for row in chunk:
                nm = row.get('signal_name')
                if nm and row.get('hit_rate') is not None:
                    out[nm] = (float(row['hit_rate']), int(row.get('sample_n') or 0))
            if len(chunk) < 1000:
                break
    except requests.exceptions.RequestException as e:
        # Evidence is decoration on a driver that already fired on its own
        # condition. If the lookup fails the driver still counts — it just
        # stops making a claim it cannot currently support.
        print(f'  ⚠ signal_registry fetch failed ({e}) — labels will omit rates')
    _CACHE[sport] = out
    return out


def cohort(signal_name: str, direction: str = '', min_n: int = MIN_N,
           sport: str = 'MLB') -> str:
    """Live evidence suffix, e.g. ' — 64.7% UNDER (n=34)'.

    Empty string when the cohort has no row or is under-sampled, so the
    caller's sentence just ends after the observation.
    """
    rate, n = load(sport).get(signal_name, (None, 0))
    if rate is None or n < min_n:
        return ''
    dir_txt = f' {direction}' if direction else ''
    return f' — {rate:.1f}%{dir_txt} (n={n})'


def phrase(signal_name: str, direction: str = '', min_n: int = MIN_N,
           sport: str = 'MLB') -> str:
    """Same lookup, mid-sentence shape: '64.7% UNDER (n=34)' or ''.

    For callers that need the figure inside a clause rather than
    appended, e.g. "Fading the MC minority wins {phrase}".
    """
    rate, n = load(sport).get(signal_name, (None, 0))
    if rate is None or n < min_n:
        return ''
    dir_txt = f' {direction}' if direction else ''
    return f'{rate:.1f}%{dir_txt} (n={n})'
