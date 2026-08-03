"""NFL consensus projection scraper (Sprint 1 Day 6 · 2026-08-03).

Fetches per-player projections from FantasyPros — which itself aggregates
ESPN, CBS, Yahoo, NFL.com projections into a consensus. Serves as the
independent validator layer for our roll-our-own projection.

Purpose: Hybrid projection layer (per user decision 2026-08-02) — when
our projection disagrees with consensus by >15%, flag the delta so Jerry
reasons about it explicitly in synthesis. Prevents overconfidence in a
possibly-broken model.

Sources tried in order:
  1. FantasyPros week-N projections page (HTML scrape, primary)
  2. FantasyPros season projections (fallback if week page unavailable)

COVERAGE LIMITATION (2026-08-03): FantasyPros free tier hard-caps HTML
rendering at 10 rows per position per page. Full player list is lazy-
loaded via authenticated AJAX. This means consensus data covers ONLY
the top 10 QB/WR/RB — which happens to cover >75% of players who get
actual book prop lines (starters and workhorses). Deep-bench players
without consensus data just skip validator flagging; Jerry still gets
our own projection + book line + all other signals.

Future upgrade paths (when we want full coverage):
  - Rotoworld / RotoBaller scrape (also thin free tiers)
  - ESPN Fantasy API (requires session token discovery)
  - Paid: FantasyPros API subscription (~$40/yr for developer access)
  - Paid: 4for4 or Draft Sharks projection API

Fails gracefully — if no consensus available, returns {'error': ...} and
downstream sets consensus_delta=null. Not a blocker.

Cache: FantasyPros pages are stable within a week — pull once per position
per week, cache in-memory for the pipeline run.

Usage:
    from fetch_nfl_consensus import get_consensus, compute_delta
    con = get_consensus('QB', week=1)
    delta = compute_delta(our_proj={'pass_yds': 229.5},
                          consensus={'pass_yds': 245.2})
"""
from __future__ import annotations
import re, sys, functools
from datetime import datetime, timezone
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


UA = {'User-Agent': 'Mozilla/5.0 (compatible; SweatLocker/1.0; +https://sweatlocker.pro)'}

# Column order in FantasyPros tables per position (verified 2026-08-03).
# Position-specific because table columns differ per position.
COLUMNS = {
    'QB': ['pass_attempts', 'completions', 'pass_yds', 'pass_tds', 'ints',
           'rush_attempts', 'rush_yds', 'rush_tds', 'fumbles_lost', 'fantasy_points'],
    'RB': ['rush_attempts', 'rush_yds', 'rush_tds', 'receptions', 'rec_yds',
           'rec_tds', 'fumbles_lost', 'fantasy_points'],
    'WR': ['receptions', 'rec_yds', 'rec_tds', 'rush_attempts', 'rush_yds',
           'rush_tds', 'fumbles_lost', 'fantasy_points'],
    'TE': ['receptions', 'rec_yds', 'rec_tds', 'fumbles_lost', 'fantasy_points'],
}

BASE_URL = 'https://www.fantasypros.com/nfl/projections'


@functools.lru_cache(maxsize=32)
def _fetch_page(position: str, week: Optional[int] = None) -> Optional[str]:
    """Fetch and cache the raw HTML for a position + week."""
    pos = position.lower()
    url = f'{BASE_URL}/{pos}.php'
    if week:
        url += f'?week={week}'
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code == 200:
            return r.text
    except requests.RequestException:
        pass
    return None


def get_consensus(position: str, week: Optional[int] = None) -> dict:
    """Fetch consensus projections for all players at a position for a week.

    Returns:
        {player_name (str): {'team': str, 'projections': {stat_name: value}}}
        Or {'error': str} if the fetch/parse failed.
    """
    position = position.upper()
    if position not in COLUMNS:
        return {'error': f'unsupported_position_{position}'}

    html = _fetch_page(position, week)
    if not html:
        return {'error': f'fetch_failed_{position}_week{week}'}

    tbody_match = re.search(r'<tbody[^>]*>(.*?)</tbody>', html, re.DOTALL)
    if not tbody_match:
        return {'error': f'no_tbody_{position}_week{week}'}
    body = tbody_match.group(1)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', body, re.DOTALL)

    out = {}
    col_names = COLUMNS[position]

    for row in rows:
        # Player name + team
        name_m = re.search(r'fp-player-name="([^"]+)"[^>]*>[^<]+</a>\s*([A-Z]{2,3})', row)
        if not name_m:
            continue
        player = name_m.group(1).strip()
        team = name_m.group(2).strip()

        # Numeric cells
        cell_values = re.findall(r'<td class="center"[^>]*>([\d.]+)</td>', row)
        if len(cell_values) < len(col_names):
            continue

        try:
            projections = {col_names[i]: float(cell_values[i]) for i in range(len(col_names))}
        except (ValueError, IndexError):
            continue

        out[player] = {'team': team, 'projections': projections,
                       'source': f'fantasypros_{position.lower()}_week{week or "season"}'}

    if not out:
        return {'error': f'no_rows_parsed_{position}_week{week}'}
    return out


def compute_delta(our_proj: dict, consensus: dict) -> dict:
    """Compare our projections vs consensus per stat.

    Returns {stat: {our, consensus, delta, delta_pct, flag}}.
    flag == True if |delta_pct| > 15% (validator threshold).
    """
    out = {}
    for stat, our_val in our_proj.items():
        if not isinstance(our_val, (int, float)): continue
        cons_val = consensus.get(stat)
        if cons_val is None or cons_val == 0:
            out[stat] = {'our': our_val, 'consensus': None, 'delta': None,
                         'delta_pct': None, 'flag': False,
                         'note': 'consensus_missing'}
            continue
        delta = our_val - cons_val
        delta_pct = (delta / cons_val) * 100 if cons_val else None
        flag = abs(delta_pct) > 15 if delta_pct is not None else False
        out[stat] = {
            'our': round(our_val, 1),
            'consensus': round(cons_val, 1),
            'delta': round(delta, 1),
            'delta_pct': round(delta_pct, 1) if delta_pct is not None else None,
            'flag': flag,
        }
    return out


def get_player_consensus(player_name: str, position: str,
                          week: Optional[int] = None) -> Optional[dict]:
    """Look up a single player's consensus projection. Returns the projections
    dict or None if not found."""
    con = get_consensus(position, week)
    if 'error' in con:
        return None
    # Exact match first
    if player_name in con:
        return con[player_name]['projections']
    # Case-insensitive lookup
    lc = player_name.lower()
    for p, data in con.items():
        if p.lower() == lc:
            return data['projections']
    # Last-name fuzzy match (for suffix drift like "Michael Pittman Jr." vs "Michael Pittman")
    last = player_name.split()[-1].lower()
    candidates = [(p, data) for p, data in con.items() if p.split()[-1].lower() == last]
    if len(candidates) == 1:
        return candidates[0][1]['projections']
    return None


if __name__ == '__main__':
    print('=== FantasyPros consensus smoke test ===')
    import time

    for pos in ('QB', 'WR', 'RB'):
        t0 = time.time()
        con = get_consensus(pos, week=1)
        dt = time.time() - t0
        if 'error' in con:
            print(f'\n[{pos}] ERROR: {con["error"]}')
            continue
        print(f'\n[{pos}] {len(con)} players parsed in {dt*1000:.0f}ms')
        # Show top 3
        for player, data in list(con.items())[:3]:
            proj_summary = ', '.join(f'{k}={v}' for k, v in list(data['projections'].items())[:5])
            print(f'  {player} ({data["team"]}): {proj_summary}')

    # Delta test — compare our Mahomes projection vs consensus
    print('\n=== Validator delta test ===')
    our_mahomes = {'pass_yds': 229.5, 'pass_tds': 1.6, 'ints': 0.4,
                   'rush_yds': 24.9, 'pass_attempts': 29.5}
    cons_mahomes = get_player_consensus('Patrick Mahomes', 'QB', week=1)
    if cons_mahomes:
        deltas = compute_delta(our_mahomes, cons_mahomes)
        print(f'Mahomes W1 delta vs consensus:')
        for stat, d in deltas.items():
            flag = ' 🚨FLAG' if d.get('flag') else ''
            print(f'  {stat:<16} ours={d["our"]:<8} consensus={d["consensus"]:<8} Δ={d["delta"]:<+7.1f} ({d["delta_pct"]:<+6.1f}%){flag}')
    else:
        print('Mahomes not found in consensus')
