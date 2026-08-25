"""NHL cohort backfill (2026-08-19).

Mirrors ncaaf_cohort_backfill / nfl_cohort_backfill: compute historical
hit rates for NHL-specific situational cohorts using nhl_game_results
(1,335 games — 2024-25 season). Writes to mlb_tier_calibration with
sport='NHL' — same universal table + downstream consumers (audit
surfaces, ensemble weight adjust) as NCAAF/NFL.

Cohorts computed:

  REST/SCHEDULE:
    nhl_home_b2b            — home team on second night of back-to-back
    nhl_away_b2b            — away team on second night of back-to-back
    nhl_home_long_rest      — home team with 3+ days rest, away has ≤ 1
    nhl_away_long_rest      — away team with 3+ days rest, home has ≤ 1
    nhl_both_rested         — both teams with 2+ days rest
    nhl_both_b2b            — both teams on second night of back-to-back

  MATCHUP CONTEXT:
    nhl_divisional          — same division matchup (proxy: repeated matchup
                              in season with 3+ meetings)
    nhl_h2h_dominated       — one team won prior 3+ h2h meetings this season

  HOME-ICE:
    nhl_home_underdog       — home team with recent losing record (proxy)
    nhl_home_favored        — home team with recent winning record (proxy)

Cohorts betting HOME ML → graded via home_win. AWAY ML → inverse.
No close_puckline in nhl_game_results (2024-25 backfill), so no RL grading.
For "context" cohorts (b2b, rest) we grade whichever team is disadvantaged.

USAGE:
    python nhl_cohort_backfill.py                # backfill all
    python nhl_cohort_backfill.py --dry-run
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from pathlib import Path

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


def fetch_games() -> list[dict]:
    """All resolved NHL games in date order."""
    rows = []
    for off in range(0, 30000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/nhl_game_results?home_win=not.is.null'
            f'&order=game_date.asc,game_id.asc'
            f'&select=game_id,game_date,home_team,away_team,home_score,away_score,'
            f'total_goals,home_win,went_to_ot'
            f'&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        rows += chunk
        if len(chunk) < 1000: break
    return rows


def compute_rest_and_h2h(games: list[dict]) -> None:
    """In-place: add rest_days, back_to_back, h2h_meetings, h2h_home_wins."""
    last_played: dict[str, date] = {}
    h2h_wins: dict[tuple, dict] = defaultdict(lambda: {'total': 0, 'home_a_wins': 0, 'team_a': None})
    team_form: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))

    for g in games:
        d = date.fromisoformat(g['game_date']) if isinstance(g['game_date'], str) else g['game_date']
        h = g['home_team']; a = g['away_team']

        # Rest
        h_last = last_played.get(h); a_last = last_played.get(a)
        g['home_rest_days'] = (d - h_last).days if h_last else None
        g['away_rest_days'] = (d - a_last).days if a_last else None
        g['home_b2b'] = g['home_rest_days'] == 1
        g['away_b2b'] = g['away_rest_days'] == 1

        # H2H (season key)
        # Season = year for Oct-Dec games, year-1 for Jan-Jun games
        season = d.year if d.month >= 9 else d.year - 1
        mk = tuple(sorted([h, a]) + [season])
        rec = h2h_wins[mk]
        g['h2h_meetings_prior'] = rec['total']
        if rec['team_a'] is None:
            rec['team_a'] = tuple(sorted([h, a]))[0]
        g['h2h_prior_home_a_wins'] = rec['home_a_wins']

        # Team form proxy
        h_form = list(team_form[h])[-5:]
        a_form = list(team_form[a])[-5:]
        g['home_form_wins_l5'] = sum(1 for x in h_form if x)
        g['away_form_wins_l5'] = sum(1 for x in a_form if x)
        g['home_form_games_l5'] = len(h_form)
        g['away_form_games_l5'] = len(a_form)

        # Update
        last_played[h] = d; last_played[a] = d
        rec['total'] += 1
        # who won and were they team_a?
        team_a = rec['team_a']
        winner = h if g['home_win'] else a
        if winner == team_a:
            rec['home_a_wins'] += 1
        team_form[h].append(bool(g['home_win']))
        team_form[a].append(not bool(g['home_win']))


def cohorts_for(g: dict) -> list[tuple[str, str]]:
    """Return list of (cohort_name, pick_side) tuples this game belongs to.
    pick_side ∈ {'HOME_ML', 'AWAY_ML'}. Grading against home_win only."""
    tags = []
    hr = g.get('home_rest_days'); ar = g.get('away_rest_days')
    hb = g.get('home_b2b'); ab = g.get('away_b2b')

    if hb and not ab:
        tags.append(('nhl_home_b2b_fade', 'AWAY_ML'))  # fade the tired team
    if ab and not hb:
        tags.append(('nhl_away_b2b_fade', 'HOME_ML'))
    if hb and ab:
        tags.append(('nhl_both_b2b', 'HOME_ML'))  # informational — home has ice
    if hr is not None and ar is not None:
        if hr >= 3 and ar <= 1:
            tags.append(('nhl_home_long_rest', 'HOME_ML'))
        if ar >= 3 and hr <= 1:
            tags.append(('nhl_away_long_rest', 'AWAY_ML'))
        if hr >= 2 and ar >= 2:
            tags.append(('nhl_both_rested', 'HOME_ML'))  # info — HFA

    # Form-based
    h5 = g.get('home_form_wins_l5'); a5 = g.get('away_form_wins_l5')
    hg = g.get('home_form_games_l5'); ag = g.get('away_form_games_l5')
    if hg == 5 and ag == 5:
        if h5 >= 4 and a5 <= 1:
            tags.append(('nhl_hot_home_vs_cold_away', 'HOME_ML'))
        if a5 >= 4 and h5 <= 1:
            tags.append(('nhl_hot_away_vs_cold_home', 'AWAY_ML'))
        if h5 <= 1 and a5 <= 1:
            tags.append(('nhl_both_slumping', 'HOME_ML'))  # info — home ice
        if h5 >= 4 and a5 >= 4:
            tags.append(('nhl_both_hot', 'OVER'))  # totals

    # H2H
    prior_n = g.get('h2h_meetings_prior') or 0
    if prior_n >= 3:
        tags.append(('nhl_h2h_recent_matchup', 'HOME_ML'))  # info

    # Total-based (uses total_goals proxy vs 6.0)
    tot = g.get('total_goals')
    if tot is not None:
        # High-scoring pairing indicator (based on prior form)
        pass

    # Home-ice / HFA baseline
    tags.append(('nhl_all_games_baseline', 'HOME_ML'))

    return tags


def grade(pick: str, g: dict) -> str | None:
    home_won = bool(g.get('home_win'))
    tot = g.get('total_goals')
    if pick == 'HOME_ML': return 'W' if home_won else 'L'
    if pick == 'AWAY_ML': return 'L' if home_won else 'W'
    if pick == 'OVER'   :
        if tot is None: return None
        if tot > 6: return 'W'
        if tot < 6: return 'L'
        return 'P'
    if pick == 'UNDER'  :
        if tot is None: return None
        if tot < 6: return 'W'
        if tot > 6: return 'L'
        return 'P'
    return None


def build_rows(tallies: dict) -> list[dict]:
    today = date.today().isoformat()
    rows = []
    for name, wl in tallies.items():
        n = wl['W'] + wl['L']
        if n == 0: continue
        rows.append({
            'tier': name,
            'sport': 'NHL',
            'window_label': 'lifetime',
            'hits': wl['W'],
            'total': n,
            'hit_rate': round(wl['W'] / n, 4),
            'computed_date': today,
        })
    return rows


def upsert(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        for r in sorted(rows, key=lambda x: -x['hit_rate']):
            pct = r['hit_rate'] * 100
            star = ' ⭐' if pct >= 55 and r['total'] >= 30 else \
                   ' 🚨' if pct < 45 and r['total'] >= 30 else ''
            print(f"  [DRY] {r['tier']:32} {pct:5.1f}% ({r['hits']}/{r['total']}){star}")
        return len(rows)
    r = requests.post(
        f'{SB}/rest/v1/mlb_tier_calibration'
        f'?on_conflict=tier,window_label,computed_date',
        headers=H_WRITE, json=rows, timeout=30)
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(rows)


def run(dry_run: bool = False) -> None:
    print('=== NHL cohort backfill ===')
    games = fetch_games()
    print(f'  games loaded: {len(games)}')
    if not games: return

    compute_rest_and_h2h(games)

    tallies: dict = defaultdict(lambda: {'W': 0, 'L': 0, 'P': 0})
    for g in games:
        for name, pick in cohorts_for(g):
            r = grade(pick, g)
            if r: tallies[name][r[0]] += 1

    rows = build_rows(tallies)
    print(f'  cohorts computed: {len(rows)}')

    written = upsert(rows, dry_run=dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'\n{prefix}wrote {written} calibration rows to mlb_tier_calibration')

    print(f'\n=== Sorted cohort hit rates (NHL) ===')
    for r in sorted(rows, key=lambda x: -x['hit_rate']):
        pct = r['hit_rate'] * 100
        star = ' ⭐' if pct >= 55 and r['total'] >= 30 else \
               ' 🚨' if pct < 45 and r['total'] >= 30 else ''
        print(f"  {r['tier']:32} {pct:5.1f}%  ({r['hits']}/{r['total']}){star}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
