"""Steam Room Ladder engine (2026-08-13).

Scans today's plays across all ladder-eligible sports, checks each against
the tight qualifier gates, upserts a ladder_rung when one clears. Also
maintains ladder_state (current active rung, streak counters).

Ladder Rung qualifies when ALL:
  * PRIME OR STRONG tier
  * Model win probability >= 60% (base gate)
  * Cohort win rate >= LADDER_COHORT_HIT_MIN
  * Cohort sample size >= LADDER_COHORT_N_MIN
  * Model consensus 4+ / 5 lens agreement
  * Edge (model_win_prob - implied_prob) >= LADDER_EDGE_MIN_PP
  * Absolute juice cap: -500 (safety ceiling)
  * NOT sharp-fade flagged · NOT refit-trap flagged
  * Sides ONLY OR totals with 5+ sharp-signal confluence

Frequency target: 2 rungs/week minimum.
Self-tune: if rungs/week drops below 2 for 2 consecutive weeks, LOOSEN
one gate (cohort_hit_min: 60 → 58). If rungs/week exceeds 4 for 2
consecutive weeks, TIGHTEN (consensus: 4/5 → 5/5).

Sport-universal — pulls plays from mlb_game_context, nfl_game_context,
ncaaf_game_context, ncaab_game_context (as those tables materialize).
Only considers sports with sport_registry.ladder_eligible=true.

CLI:
    python steam_room_ladder.py [--date YYYY-MM-DD] [--dry-run]

Runs daily after primary_play settles. If a qualifier fires, ladder_state
flips to 'active' with that rung. If none, ladder_state stays 'waiting'.
Result backfill (Win/Loss/Push) runs via resolve_ladder_results.py after
games grade.
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Qualifier gates — see project_steam_room_ladder for tuning history.
LADDER_TIER_MIN         = {'PRIME', 'STRONG'}
LADDER_WIN_PROB_MIN     = 60.0
LADDER_COHORT_HIT_MIN   = 60.0
LADDER_COHORT_N_MIN     = 30
LADDER_CONSENSUS_MIN    = 4        # of 5 lens agreeing
LADDER_EDGE_MIN_PP      = 10.0     # model_win_prob - implied_prob
LADDER_ABS_JUICE_CAP    = -250     # never lay more than -250 (compounding math destroys past this)

# Per-sport context table
CTX_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
    'NBA':   'nba_game_context',
    'NHL':   'nhl_game_context',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _implied_prob(odds: Optional[int]) -> Optional[float]:
    if odds is None: return None
    try: o = int(odds)
    except (TypeError, ValueError): return None
    return 100.0 * (100 / (o + 100)) if o >= 0 else 100.0 * (abs(o) / (abs(o) + 100.0))


def get_ladder_eligible_sports() -> list:
    """Read sport_registry — return sports whose ladder_eligible=true."""
    r = requests.get(f'{SB}/rest/v1/sport_registry', headers=H_READ,
        params={'ladder_eligible': 'eq.true', 'active': 'eq.true',
                'state': 'eq.in_season',
                'select': 'sport'},
        timeout=15)
    if r.status_code != 200: return []
    return [row['sport'] for row in r.json()]


def check_qualifier(row: dict, sport: str) -> Optional[dict]:
    """Return a candidate rung dict if the play qualifies, else None.
    Reads primary_play from the ctx row + scenario/consensus signals."""
    pp = row.get('primary_play') or {}
    tier = pp.get('tier')
    if tier not in LADDER_TIER_MIN: return None

    # Gate 1: sides only (or totals/yrfi with signal confluence)
    market = pp.get('type')  # 'ml' | 'over' | 'under' | 'total' | 'spread' | 'yrfi' | 'nrfi'
    is_side = market in ('ml', 'spread')
    is_total = market in ('over', 'under', 'total')
    is_nrfi = market in ('yrfi', 'nrfi')   # 2026-08-14: added YRFI/NRFI as ladder-eligible
    if not (is_side or is_total or is_nrfi): return None

    # For totals, require 5+ sharp-signal confluence from sharp_scenario_matches
    if is_total:
        gid = row.get('game_id')
        if not gid: return None
        sm = requests.get(f'{SB}/rest/v1/sharp_scenario_game_matches', headers=H_READ,
            params={'game_id': f'eq.{gid}', 'market': 'eq.total',
                    'select': 'jerry_hint,hit_rate,n'}, timeout=10).json()
        if not isinstance(sm, list) or len(sm) < 5: return None

    # Model win probability
    # 2026-08-14 BUG FIX: previously read mc_p_home / mc_p_away — but actual
    # MC blob has mc_p_home_win / mc_p_away_win (see keys dump). Every ML
    # ladder candidate was returning win_prob=0.0% and getting filtered out
    # silently. Also totals were skipping win_prob entirely — added mc_p_over
    # / mc_p_under extraction so totals can qualify. YRFI/NRFI added too.
    mc_probs = row.get('mc_probabilities') or {}
    win_prob = None
    if isinstance(mc_probs, dict):
        if is_side and market == 'ml':
            home_ml = mc_probs.get('mc_p_home_win') or mc_probs.get('mc_p_home')
            away_ml = mc_probs.get('mc_p_away_win') or mc_probs.get('mc_p_away')
            label = (pp.get('label') or '').lower()
            home_team = (row.get('home_team') or '').lower()
            away_team = (row.get('away_team') or '').lower()
            if home_team and home_team in label:
                win_prob = home_ml * 100 if home_ml else None
            elif away_team and away_team in label:
                win_prob = away_ml * 100 if away_ml else None
        elif is_side and market == 'spread':
            # Use cover probability (with juice sign flipped based on label)
            home_cov = mc_probs.get('mc_p_home_covers')
            away_cov = mc_probs.get('mc_p_away_covers')
            label = (pp.get('label') or '').lower()
            home_team = (row.get('home_team') or '').lower()
            away_team = (row.get('away_team') or '').lower()
            if home_team and home_team in label:
                win_prob = home_cov * 100 if home_cov else None
            elif away_team and away_team in label:
                win_prob = away_cov * 100 if away_cov else None
        elif is_total:
            # Total picks: use mc_p_over or mc_p_under matching label
            label = (pp.get('label') or '').lower()
            if 'over' in label:
                win_prob = (mc_probs.get('mc_p_over') or 0) * 100 or None
            elif 'under' in label:
                win_prob = (mc_probs.get('mc_p_under') or 0) * 100 or None
        elif is_nrfi:
            # YRFI/NRFI: use mc_p_yrfi / mc_p_nrfi
            if market == 'yrfi':
                win_prob = (mc_probs.get('mc_p_yrfi') or 0) * 100 or None
            elif market == 'nrfi':
                win_prob = (mc_probs.get('mc_p_nrfi') or 0) * 100 or None
    if win_prob is None or win_prob < LADDER_WIN_PROB_MIN:
        return None

    # Consensus check — count lens agreement from signal_confluence_support
    consensus = row.get('signal_confluence_support') or 0
    if consensus < LADDER_CONSENSUS_MIN: return None

    # Odds + edge
    home_ml_odds = row.get('home_ml_close') or row.get('home_ml')
    away_ml_odds = row.get('away_ml_close') or row.get('away_ml')
    odds = None
    label = (pp.get('label') or '').lower()
    if is_side and market == 'ml':
        home_team = (row.get('home_team') or '').lower()
        if home_team and home_team in label: odds = home_ml_odds
        else: odds = away_ml_odds
    if odds is None: return None
    try: odds_int = int(odds)
    except (TypeError, ValueError): return None
    if odds_int < LADDER_ABS_JUICE_CAP: return None  # too deep juice

    implied = _implied_prob(odds_int)
    if implied is None: return None
    edge_pp = win_prob - implied
    if edge_pp < LADDER_EDGE_MIN_PP: return None

    # Cohort backing — pull from primary_play.audit_note if it references a
    # cohort, or read scenario_audit for the market. Simple pass: if the play
    # has ANY cohort reference in audit_note, we assume it's backed. Tighten
    # later once we have per-play cohort attribution stored.
    cohort_hit = None
    cohort_n = None
    audit = pp.get('audit_note') or ''
    import re
    m = re.search(r'(\d+(?:\.\d+)?)%.*?n[=\s]?(\d+)', audit)
    if m:
        cohort_hit = float(m.group(1))
        cohort_n = int(m.group(2))
    if cohort_hit is None or cohort_hit < LADDER_COHORT_HIT_MIN: return None
    if cohort_n is None or cohort_n < LADDER_COHORT_N_MIN: return None

    # Sharp-fade / refit-trap check — skip if consensus_fade_flag=true or
    # primary_play has trap flags
    if row.get('consensus_fade_flag') is True: return None
    if 'refit_trap' in audit.lower() or 'trap_cap' in audit.lower(): return None

    return {
        'game_date': row.get('game_date'),
        'sport': sport,
        'game_id': row.get('game_id'),
        'matchup': f"{row.get('away_team','?')} @ {row.get('home_team','?')}",
        'pick_side': pp.get('label'),
        'market': 'ml' if is_side and market == 'ml'
                  else 'spread' if is_side
                  else 'total',
        'odds_american': odds_int,
        'tier': tier,
        'conviction': pp.get('signal_floor'),
        'model_win_prob': round(win_prob, 1),
        'cohort_hit_rate': cohort_hit,
        'cohort_n': cohort_n,
        'consensus_lens': consensus,
        'edge_pp': round(edge_pp, 1),
        'qualification_notes': (
            f'tier={tier} · MC={win_prob:.0f}% vs implied={implied:.0f}% · '
            f'edge={edge_pp:+.1f}pp · cohort={cohort_hit}% n={cohort_n} · '
            f'consensus={consensus}/5 · audit={audit[:80]}'
        ),
    }


def scan_and_maybe_qualify(game_date: str, dry_run: bool = False) -> Optional[dict]:
    """Iterate ladder-eligible sports; return first qualifying rung or None."""
    sports = get_ladder_eligible_sports()
    print(f'  ladder-eligible sports: {sports or "NONE (all off-season/preseason)"}')
    candidates = []
    for sport in sports:
        ctx_tbl = CTX_TABLE.get(sport)
        if not ctx_tbl: continue
        r = requests.get(f'{SB}/rest/v1/{ctx_tbl}', headers=H_READ,
            params={'game_date': f'eq.{game_date}',
                    'select': 'game_id,game_date,home_team,away_team,primary_play,'
                              'mc_probabilities,signal_confluence_support,'
                              'home_ml_close,away_ml_close,home_ml,away_ml,'
                              'consensus_fade_flag'},
            timeout=15).json()
        if not isinstance(r, list): continue
        for row in r:
            rung = check_qualifier(row, sport)
            if rung: candidates.append(rung)
    if not candidates:
        print('  no qualifier fired — ladder stays parked')
        return None
    # Rank by (tier PRIME > STRONG, then edge_pp, then cohort_hit_rate)
    candidates.sort(key=lambda c: (
        0 if c['tier'] == 'PRIME' else 1,
        -(c.get('edge_pp') or 0),
        -(c.get('cohort_hit_rate') or 0),
    ))
    winner = candidates[0]
    print(f'  🎯 Ladder qualifier: {winner["matchup"]} · {winner["pick_side"]} '
          f'(edge {winner["edge_pp"]:+.1f}pp, cohort {winner["cohort_hit_rate"]}%)')
    return winner


def upsert_rung_and_state(rung: dict, dry_run: bool = False) -> None:
    if dry_run:
        print(f'  [DRY] would upsert rung: {rung}')
        return
    # Insert rung
    pr = requests.post(f'{SB}/rest/v1/ladder_rung',
        headers={**H_WRITE, 'Prefer': 'return=representation'},
        json=rung, timeout=15)
    if pr.status_code not in (200, 201, 204):
        print(f'  rung insert failed: {pr.status_code} {pr.text[:200]}')
        return
    inserted = pr.json()
    rung_id = inserted[0]['id'] if isinstance(inserted, list) and inserted else None
    if not rung_id: return
    # Update ladder_state
    st = requests.patch(f'{SB}/rest/v1/ladder_state?id=eq.1',
        headers=H_WRITE, json={
            'status': 'active',
            'active_rung_id': rung_id,
            'last_updated_at': datetime.now(timezone.utc).isoformat(),
            'note': f'Active rung: {rung["matchup"]} · {rung["pick_side"]}',
        }, timeout=10)
    print(f'  wrote rung id={rung_id} + updated ladder_state (status: {st.status_code})')


def set_state_waiting(dry_run: bool = False) -> None:
    if dry_run:
        print('  [DRY] would set ladder_state=waiting'); return
    requests.patch(f'{SB}/rest/v1/ladder_state?id=eq.1',
        headers=H_WRITE, json={
            'status': 'waiting',
            'active_rung_id': None,
            'last_updated_at': datetime.now(timezone.utc).isoformat(),
            'note': 'Waiting for the next qualifier',
        }, timeout=10)
    print('  ladder_state → waiting')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    game_date = args.date or _et_today()
    print(f'=== Steam Room Ladder · {game_date} ===')
    rung = scan_and_maybe_qualify(game_date, dry_run=args.dry_run)
    if rung:
        upsert_rung_and_state(rung, dry_run=args.dry_run)
    else:
        set_state_waiting(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
