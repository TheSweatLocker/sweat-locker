"""NFL prop enricher (Sprint 1 Day 7 · 2026-08-03).

The bridge between the coverage sweeper (which writes COVERAGE stubs
from Odds API) and the Jerry synthesis layer (which needs enriched
signals). For each pending coverage row:

  1. Look up player position + team from nfl_data_py rosters
  2. Determine opp_team + home_away from matchup + player_team
  3. Call our projection layer (project_qb / project_receiver / project_rb)
  4. Fetch consensus projection from FantasyPros
  5. Compute consensus delta (validator flag when |Δ| > 15%)
  6. Compute book edge (projection vs book_line)
  7. Build signal bundle
  8. Apply hybrid PRIME gate (edge >= 15% AND ≥3 signals aligned)
  9. UPDATE the row with tier, conviction, projection, consensus, signals

Hybrid PRIME gate (per 2026-08-02 user decision):
  PRIME  = |edge| >= 15% AND signal_count_aligned >= 3
  STRONG = |edge| >= 10% AND signal_count_aligned >= 2
  LEAN   = |edge| >= 6% OR single_strong_signal
  SKIP   = |edge| < 6% or contradicting signals

Zero-hallucination architecture: every number in the signals dict comes
from either our projection (with input trace) or the book line (raw).
Nothing is invented. Jerry synth downstream validates every cited number
against this input dict.

Runs AFTER sweep_nfl_prop_coverage.py, BEFORE generate_nfl_prop_jerry_synthesis.

Usage:
    python generate_nfl_props.py [--date YYYY-MM-DD] [--week N] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, warnings, functools, math
from datetime import datetime, timedelta, timezone
from typing import Optional
from collections import defaultdict

import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore')

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Local imports
try:
    import nfl_data_py as nfl
    from project_nfl_stats import project_qb, project_receiver, project_rb, _completed_seasons
    from fetch_nfl_consensus import get_player_consensus, compute_delta
    STACK_AVAILABLE = True
except ImportError as e:
    print(f'⚠ stack missing: {e}')
    STACK_AVAILABLE = False

# Family → projection function router
PROJECTOR_BY_POSITION = {
    'QB': project_qb if STACK_AVAILABLE else None,
    'WR': project_receiver if STACK_AVAILABLE else None,
    'TE': project_receiver if STACK_AVAILABLE else None,
    'RB': project_rb if STACK_AVAILABLE else None,
}

# Prop family → stat key in projection output
PROP_TO_STAT = {
    'pass_yds':  'pass_yds',
    'pass_tds':  'pass_tds',
    'ints':      'ints',
    'pass_attempts': 'pass_attempts',
    'rush_yds':  'rush_yds',
    'rec_yds':   'rec_yds',
    'receptions': 'receptions',
    'anytime_td': 'anytime_td_prob',
}


@functools.lru_cache(maxsize=4)
def _roster_map(season: int) -> dict:
    """{player_display_name (lower): (position, team_abbr)} for a season."""
    if not STACK_AVAILABLE: return {}
    try:
        r = nfl.import_seasonal_rosters([season])
    except Exception:
        return {}
    out = {}
    for _, row in r.iterrows():
        name = (row.get('player_name') or '').strip()
        if not name: continue
        out[name.lower()] = (row.get('position'), row.get('team'))
    return out


def _lookup_player(player_name: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (position, team_abbr). Tries current + previous season."""
    now_year = datetime.now(timezone.utc).year
    for season in _completed_seasons(now_year) + [now_year - 1]:
        rm = _roster_map(season)
        entry = rm.get(player_name.lower())
        if entry:
            return entry
    return None, None


def _opp_from_matchup(matchup: str, player_team_abbr: Optional[str]) -> tuple[Optional[str], str]:
    """Extract opp_team abbreviation + home/away from 'Away Team @ Home Team' matchup.
    Player team → opp team. Returns (opp_abbr, home_away)."""
    if not matchup or not player_team_abbr: return None, 'UNKNOWN'
    parts = matchup.split(' @ ')
    if len(parts) != 2: return None, 'UNKNOWN'
    away_full, home_full = parts[0].strip(), parts[1].strip()

    # Reverse-lookup: which full name matches our player_team_abbr?
    # nfl_data_py rosters give abbr like 'KC' — need to map to 'Kansas City Chiefs'
    # Quick approach: any team whose last word matches abbr's expansion
    # For MVP, use loose containment: 'KC' vs 'Kansas City' — no dice.
    # Real fix: import_team_desc() gives {team_abbr: full_name}
    try:
        td = nfl.import_team_desc()
        team_map = {row['team_abbr']: row['team_name'] for _, row in td.iterrows()}
    except Exception:
        team_map = {}
    full = team_map.get(player_team_abbr, '')
    if full and full == away_full:
        # Player is on away team, opp = home team
        home_abbr = next((k for k, v in team_map.items() if v == home_full), None)
        return home_abbr, 'AWAY'
    if full and full == home_full:
        away_abbr = next((k for k, v in team_map.items() if v == away_full), None)
        return away_abbr, 'HOME'
    # Fallback: last-word match
    for k, v in team_map.items():
        last = v.split()[-1] if v else ''
        if last and last == away_full.split()[-1]:
            if k == player_team_abbr:
                home_abbr = next((kk for kk, vv in team_map.items() if vv.split()[-1] == home_full.split()[-1]), None)
                return home_abbr, 'AWAY'
        if last and last == home_full.split()[-1]:
            if k == player_team_abbr:
                away_abbr = next((kk for kk, vv in team_map.items() if vv.split()[-1] == away_full.split()[-1]), None)
                return away_abbr, 'HOME'
    return None, 'UNKNOWN'


def _prop_family_from_type(prop_type: str) -> tuple[str, str]:
    """Split 'pass_yds_over' → ('pass_yds', 'over')."""
    if prop_type.endswith('_over'):
        return prop_type[:-5], 'over'
    if prop_type.endswith('_under'):
        return prop_type[:-6], 'under'
    return prop_type, 'over'


def _compute_tier(directional_edge_pct: float, aligned_signal_count: int, signal_contradict: bool) -> tuple[str, int]:
    """Hybrid PRIME gate per 2026-08-02 spec. Returns (tier, conviction).

    IMPORTANT: `directional_edge_pct` is signed vs the direction being scored.
    Negative value = we're on the wrong side (fade) → SKIP regardless of magnitude.
    Only positive edges can earn a tier.
    """
    if signal_contradict:
        return 'SKIP', 0
    if directional_edge_pct <= 0:
        return 'SKIP', 0  # wrong side of the projection
    edge = directional_edge_pct
    if edge >= 15 and aligned_signal_count >= 3:
        return 'PRIME', min(95, 75 + int(edge / 2))
    if edge >= 10 and aligned_signal_count >= 2:
        return 'STRONG', min(85, 65 + int(edge / 2))
    if edge >= 6:
        return 'LEAN', min(75, 55 + int(edge / 2))
    return 'SKIP', 0


def enrich_prop(row: dict, projections_cache: dict) -> Optional[dict]:
    """Take a coverage stub, enrich with projection + consensus + signals + tier.
    Returns the update payload or None if unrecoverable (player not found)."""
    player = row['player_name']
    prop_type = row['prop_type']
    direction = row['direction']
    matchup = row.get('matchup') or ''
    book_line = row.get('book_line') or row.get('prop_line')

    # 1. Position + team
    position, team_abbr = _lookup_player(player)
    if not position or position not in PROJECTOR_BY_POSITION:
        return None  # not a projectable position (K, DEF, LB, etc.)

    projector = PROJECTOR_BY_POSITION[position]
    if not projector:
        return None

    # 2. Opp team + home/away
    opp_abbr, home_away = _opp_from_matchup(matchup, team_abbr)

    # 3. Projection (cache per player to reuse across over/under rows)
    cache_key = f'{player}_{opp_abbr}_{home_away}'
    if cache_key in projections_cache:
        proj_full = projections_cache[cache_key]
    else:
        try:
            if position == 'QB':
                proj_full = projector(player, opp_abbr or 'AVG', home_away=home_away)
            elif position in ('WR', 'TE'):
                proj_full = projector(player, opp_abbr or 'AVG', home_away=home_away)
            else:
                proj_full = projector(player, opp_abbr or 'AVG', home_away=home_away)
        except Exception as e:
            proj_full = {'error': str(e)}
        projections_cache[cache_key] = proj_full

    if 'error' in proj_full:
        return None

    # 4. Get the specific stat this prop cares about
    family, _ = _prop_family_from_type(prop_type)
    stat_key = PROP_TO_STAT.get(family)
    if not stat_key or stat_key not in proj_full:
        return None
    proj_entry = proj_full[stat_key]
    proj_value = proj_entry.get('value')
    if proj_value is None:
        return None

    # 5. Consensus (per position, cached)
    consensus_delta_data = None
    try:
        cons_full = get_player_consensus(player, position, week=None)
        if cons_full:
            # Compare only the stats we care about that are in both
            our_stats_for_delta = {stat_key: proj_value}
            if stat_key in cons_full:
                our_stats_for_delta = {stat_key: proj_value}
                consensus_delta_data = compute_delta(our_stats_for_delta, cons_full)
    except Exception:
        pass

    # 6. Book edge — different logic for probability props (anytime_td) vs stat lines
    def _american_to_prob(odds):
        if odds is None: return None
        if odds > 0: return 100.0 / (odds + 100.0)
        return abs(odds) / (abs(odds) + 100.0)

    if family == 'anytime_td':
        # proj_value is our estimated probability of scoring (0-1)
        # Compare vs implied probability from book odds
        odds_over = row.get('book_over_odds')
        implied = _american_to_prob(odds_over)
        if implied is None or implied == 0:
            edge = 0.0; edge_pct = 0.0
        else:
            edge = proj_value - implied
            edge_pct = (edge / implied) * 100
        # For anytime_td: our_prob > implied → OVER (Yes) has value
        directional_edge = edge_pct if direction == 'over' else -edge_pct
    elif book_line and book_line > 0:
        edge = proj_value - float(book_line)
        edge_pct = (edge / float(book_line)) * 100
        directional_edge = edge_pct if direction == 'over' else -edge_pct
    else:
        edge = 0.0
        edge_pct = 0.0
        directional_edge = 0.0

    # 7. Signal bundle
    inputs = proj_entry.get('inputs', {})
    signals = {
        'projection': f'Projected {family} {proj_value} vs line {book_line} ({directional_edge:+.1f}% {direction.upper()} edge)',
        'l5_form': f'L5 {family} avg {inputs.get("L5_avg","?")}' + (f' over {inputs.get("L5_games","?")} games' if inputs.get("L5_games") else ''),
        'opp_matchup': inputs.get('opp_D_note', 'no opp data'),
        # MLB-compat: _edge_pct is expected by the shared prop synth COVERAGE-tier
        # gate to decide whether coverage stubs earn a Jerry take. NFL enricher
        # normally tiers everything > COVERAGE, but leaving this key ensures
        # compatibility when synth runs before enricher on a race.
        '_edge_pct': round(directional_edge / 100, 3),
        '_projected_value': proj_value,
        '_book_line': book_line,
    }
    aligned = 0
    if directional_edge >= 6: aligned += 1  # book edge alone counts as 1 signal
    if inputs.get('opp_D_mult', 1.0) != 1.0:
        if (direction == 'over' and inputs['opp_D_mult'] > 1.0) or \
           (direction == 'under' and inputs['opp_D_mult'] < 1.0):
            aligned += 1
    if inputs.get('weather_mult', 1.0) != 1.0:
        if (direction == 'over' and inputs['weather_mult'] > 1.0) or \
           (direction == 'under' and inputs['weather_mult'] < 1.0):
            aligned += 1
            signals['weather'] = inputs.get('weather_note', '')

    # Consensus flag
    consensus_flag = False
    if consensus_delta_data and stat_key in consensus_delta_data:
        d = consensus_delta_data[stat_key]
        if d.get('consensus') is not None:
            signals['consensus'] = f'FP consensus {d["consensus"]} vs ours {d["our"]} ({d["delta_pct"]:+.1f}%)'
            if d.get('flag'):
                consensus_flag = True
                signals['consensus_disagreement'] = f'⚠ Our projection differs from consensus by {abs(d["delta_pct"]):.0f}% — reconcile'

    # Contradict check: our edge says one direction, consensus says the other
    signal_contradict = False
    if consensus_delta_data and stat_key in consensus_delta_data:
        d = consensus_delta_data[stat_key]
        if d.get('consensus') is not None and book_line:
            cons_edge = d['consensus'] - float(book_line)
            cons_dir_over = cons_edge > 0
            our_dir_over = edge > 0
            if consensus_flag and cons_dir_over != our_dir_over:
                signal_contradict = True

    # 8. Tier
    tier, conviction = _compute_tier(edge_pct if direction == 'over' else -edge_pct,
                                       aligned, signal_contradict)

    # Consensus disagreement caps PRIME → STRONG per hybrid gate
    if consensus_flag and tier == 'PRIME':
        tier = 'STRONG'
        conviction = min(conviction, 78)

    return {
        'position': position,
        'player_team': team_abbr,
        'opp_team': opp_abbr,
        'home_away': home_away,
        'projection': {'value': proj_value, 'inputs': inputs},
        'consensus': consensus_delta_data.get(stat_key) if consensus_delta_data else None,
        'consensus_delta': (consensus_delta_data.get(stat_key) or {}).get('delta_pct') if consensus_delta_data else None,
        'signals': signals,
        'tier': tier,
        'conviction': conviction,
    }


def run(game_date: str | None = None, dry_run: bool = False) -> None:
    if not STACK_AVAILABLE:
        print('⛔ nfl_data_py + local modules not available — abort'); return

    gd = game_date or (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')
    print(f'=== generate_nfl_props · {gd} ===')

    r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props',
                     headers=H_READ,
                     params={'game_date': f'eq.{gd}', 'tier': 'eq.COVERAGE',
                             'select': 'id,game_id,player_name,prop_type,direction,prop_line,'
                                       'book_line,book_over_odds,book_under_odds,matchup'},
                     timeout=15).json()
    if not isinstance(r, list):
        print(f'  ⚠ pull failed: {r}'); return
    print(f'  {len(r)} pending COVERAGE rows')

    projections_cache = {}
    enriched = skipped = prime_ct = strong_ct = 0

    for row in r:
        payload = enrich_prop(row, projections_cache)
        if payload is None:
            skipped += 1
            continue

        # Human-readable log
        print(f'  ✓ {payload["position"]} {row["player_name"]:<25} {row["prop_type"]:<20} '
              f'line={row["book_line"]} → proj={payload["projection"]["value"]} '
              f'[{payload["tier"]}] conv={payload["conviction"]}')

        if payload['tier'] == 'PRIME': prime_ct += 1
        elif payload['tier'] == 'STRONG': strong_ct += 1

        if dry_run:
            enriched += 1; continue

        up = requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{row["id"]}',
                             headers=H_WRITE, json=payload, timeout=15)
        if up.status_code in (200, 204):
            enriched += 1
        else:
            print(f'    ⚠ update {up.status_code}: {up.text[:150]}')

    print(f'\n=== enriched {enriched} · skipped {skipped} (no position/projection) '
          f'· PRIME {prime_ct} · STRONG {strong_ct} ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)
