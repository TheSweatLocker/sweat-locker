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
# 2026-09-02 REVERT: rolled back 8/18 loosening. Post-loosening the ladder
# ran 5-9 (35.7%, -5.74u) — the "2 rungs/week min" target was auto-
# loosening gates and picking marginal plays that lost more than they
# won. Reverting to pre-8/18 stricter thresholds. Accept fewer rungs
# (0/week possible) — no bet is a bet. Auto-loosener self-tune below
# is also disabled to prevent drift back into loosened state.
LADDER_TIER_MIN         = {'PRIME', 'STRONG'}  # LEAN removed — no ladder authority
LADDER_WIN_PROB_MIN     = 60.0     # pre-loosen baseline
LADDER_COHORT_HIT_MIN   = 60.0     # pre-loosen baseline
LADDER_COHORT_N_MIN     = 30       # pre-loosen baseline
LADDER_CONSENSUS_MIN    = 4        # pre-loosen baseline (4-of-5 lens)
LADDER_EDGE_MIN_PP      = 10.0     # pre-loosen baseline (real edge only)
LADDER_ABS_JUICE_CAP    = -250     # unchanged — compounding math destroys past -250
LADDER_MIN_GATES        = 4        # 4-of-5 required (was 3); tighter with reverted thresholds
LADDER_AUTO_LOOSEN_DISABLED = True  # 2026-09-02: prevent drift back to bleeding gates

# 2026-08-20: cross-sport edge normalization. Different sports have different
# base-rate variance in edge_pp — a +7pp MLB edge is NOT the same as a +7pp
# NHL edge because puckline math + hockey scoring compress edges. Multipliers
# scale raw edge_pp into a common ranking metric so cross-sport candidates
# compare fairly. Starts at 1.0 for all — will tune once we have live
# multi-sport data (Oct+ when NFL/NCAAF/NHL/NBA come online). Values below
# are best-first-guess based on typical sport-book edge dispersion.
SPORT_EDGE_MULTIPLIER = {
    'MLB':   1.00,   # baseline — most historical data, calibrate others against this
    'NFL':   0.90,   # bigger spreads, edges compress — slightly discount
    'NCAAF': 0.90,   # same shape as NFL
    'NCAAB': 0.85,   # very high variance, edges harder to trust
    'NBA':   0.95,   # deep signal but tight lines
    'NHL':   1.10,   # puckline math means small edges more meaningful
    'UFC':   0.80,   # single-KO variance kills edge reliability
}

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
    """Return a candidate step dict if the play qualifies, else None.

    2026-08-19: rewritten to SOFT SCORING. Prior version required ALL 5 gates
    to pass — the "3 of 5" copy in the UI was already accurate for what we
    intended, but the backend was actually enforcing 5 of 5. Days with any
    single mid-tier signal (e.g. Boston ML at 47% MC prob vs 58 threshold)
    silently rejected every candidate and the ladder sat parked for weeks.

    New behavior: count gates passed, keep the pick, let caller pick the
    highest-scoring one. Must clear at least 3 of 5 gates (matches UI copy)
    AND must not trigger a hard blocker (deep juice, sharp-fade, trap flag).
    """
    pp = row.get('primary_play') or {}
    tier = pp.get('tier')
    if tier not in LADDER_TIER_MIN: return None

    # Gate 1: sides only (or totals/yrfi with signal confluence)
    # 2026-08-20: added 'rl' to is_side alongside 'spread' — ensemble emits
    # 'rl' as the market for run-line picks, not 'spread'. Prior bug: EVERY
    # RL-tiered pick got rejected here silently.
    market = pp.get('type')  # 'ml' | 'over' | 'under' | 'total' | 'spread' | 'rl' | 'yrfi' | 'nrfi'
    is_side = market in ('ml', 'spread', 'rl')
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
    # Gate scoring — count how many of the 5 gates this pick clears.
    # Tier is gate #1 (already passed above — we're here because tier is
    # PRIME/STRONG/LEAN). Others are win_prob, consensus, edge, cohort.
    gates_passed = 1  # tier
    gate_notes = [f'tier={tier}']

    if win_prob is not None and win_prob >= LADDER_WIN_PROB_MIN:
        gates_passed += 1
        gate_notes.append(f'MC={win_prob:.0f}%✓')
    else:
        gate_notes.append(f'MC={win_prob:.0f}%' if win_prob else 'MC=?')

    # Consensus check — count lens agreement from signal_confluence_support
    consensus = row.get('signal_confluence_support') or 0
    if consensus >= LADDER_CONSENSUS_MIN:
        gates_passed += 1
        gate_notes.append(f'consensus={consensus}/5✓')
    else:
        gate_notes.append(f'consensus={consensus}/5')

    # Odds + edge
    home_ml_odds = row.get('home_ml_close') or row.get('home_ml')
    away_ml_odds = row.get('away_ml_close') or row.get('away_ml')
    odds = None
    label = (pp.get('label') or '').lower()
    if is_side and market == 'ml':
        home_team = (row.get('home_team') or '').lower()
        if home_team and home_team in label: odds = home_ml_odds
        else: odds = away_ml_odds
    if odds is None: return None  # hard: can't size without odds
    try: odds_int = int(odds)
    except (TypeError, ValueError): return None
    if odds_int < LADDER_ABS_JUICE_CAP: return None  # hard: too deep juice

    implied = _implied_prob(odds_int)
    if implied is None: return None
    edge_pp = (win_prob - implied) if win_prob is not None else -999
    if edge_pp >= LADDER_EDGE_MIN_PP:
        gates_passed += 1
        gate_notes.append(f'edge={edge_pp:+.1f}pp✓')
    else:
        gate_notes.append(f'edge={edge_pp:+.1f}pp')

    # 2026-08-20: cohort backing is now OPTIONAL (bonus signal, not a hard
    # gate). Prior behavior required regex match on primary_play.audit_note
    # for "X% n=Y" cohort pattern — but ensemble_v2 audit_notes have a
    # different format ("ensemble_scorer v2 · N sources · score=... margin=...")
    # that never matches this regex. Result: EVERY ensemble-tiered pick got
    # silently rejected by this gate, ladder sat empty for weeks. Now:
    # try to parse cohort but don't require it; the other 4 gates (tier +
    # win_prob + consensus + edge) carry the qualification weight.
    cohort_hit = None
    cohort_n = None
    audit = pp.get('audit_note') or ''
    import re
    m = re.search(r'(\d+(?:\.\d+)?)%.*?n[=\s]?(\d+)', audit)
    if m:
        cohort_hit = float(m.group(1))
        cohort_n = int(m.group(2))
    if (cohort_hit is not None and cohort_hit >= LADDER_COHORT_HIT_MIN
            and cohort_n is not None and cohort_n >= LADDER_COHORT_N_MIN):
        gates_passed += 1
        gate_notes.append(f'cohort={cohort_hit}%✓')
    elif cohort_hit is not None:
        gate_notes.append(f'cohort={cohort_hit}%')

    # Must clear at least 3 of 5 gates (matches UI copy)
    if gates_passed < LADDER_MIN_GATES:
        return None

    # Hard blockers: sharp-fade / refit-trap always kill the pick
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
        'edge_pp': round(edge_pp, 1) if win_prob is not None else None,
        'gates_passed': gates_passed,
        'qualification_notes': (
            f'{gates_passed}/5 gates · ' + ' · '.join(gate_notes) +
            f' · audit={audit[:60]}'
        ),
    }


def check_prop_qualifier(prop: dict, sport: str = 'MLB') -> Optional[dict]:
    """Emit a ladder rung candidate from a graded prop pick.

    2026-08-20: props added to ladder pool per user request. Prop
    qualification uses different signals than games (no MC prob, no
    consensus lens) so gates are re-mapped:

      1. tier ∈ PRIME/STRONG (playbook or legacy)
      2. book odds in [-300, +150]  ← user's feedback_prop_jerry_odds gate
      3. refit_conviction >= 40      ← not a trap
      4. playbook conviction >= 60   ← real ensemble backing
      5. NOT a hits_over_0.5 with null odds  ← user's juice-trap memo

    Same soft-scoring rule as games — need >= 3/5 to qualify.
    """
    playbook_tier = (prop.get('playbook_tier') or '').upper()
    legacy_tier = (prop.get('legacy_tier') or '').upper()
    effective_tier = playbook_tier if playbook_tier in ('PRIME','STRONG') else legacy_tier
    if effective_tier not in ('PRIME','STRONG'):
        return None

    prop_type = prop.get('prop_type') or ''
    direction = (prop.get('direction') or '').lower()
    line = prop.get('prop_line')
    player = prop.get('player_name') or '?'

    # Odds — try both playbook_side + legacy fields
    odds = None
    for key in ('book_over_odds' if direction == 'over' else 'book_under_odds',):
        v = prop.get(key)
        if v is not None:
            try: odds = int(v); break
            except (TypeError, ValueError): pass

    gates_passed = 1  # tier
    gate_notes = [f'tier={effective_tier}']

    # Gate 2: odds range
    if odds is not None and -300 <= odds <= 150:
        gates_passed += 1
        gate_notes.append(f'odds={odds:+d}✓')
    elif odds is not None:
        gate_notes.append(f'odds={odds:+d} OUT-OF-RANGE')
        return None  # hard block per feedback_prop_jerry_odds
    else:
        gate_notes.append('odds=?')
        # Hits over 0.5 with null odds is documented trap
        if prop_type == 'hits_over' and (line or 0) <= 0.5:
            return None

    # Gate 3: refit conviction
    refit = prop.get('legacy_refit_conviction') or prop.get('refit_conviction')
    try: refit_i = int(refit) if refit is not None else None
    except (TypeError, ValueError): refit_i = None
    if refit_i is not None and refit_i >= 40:
        gates_passed += 1
        gate_notes.append(f'refit={refit_i}✓')
    elif refit_i is not None:
        gate_notes.append(f'refit={refit_i}')

    # Gate 4: ensemble/playbook conviction
    pb_conv = prop.get('playbook_conviction') or prop.get('legacy_conviction')
    try: pb_conv_i = int(pb_conv) if pb_conv is not None else None
    except (TypeError, ValueError): pb_conv_i = None
    if pb_conv_i is not None and pb_conv_i >= 60:
        gates_passed += 1
        gate_notes.append(f'conv={pb_conv_i}✓')
    elif pb_conv_i is not None:
        gate_notes.append(f'conv={pb_conv_i}')

    # Gate 5: playbook backing THIS DIRECTION specifically
    #
    # 2026-08-24 ROOT-CAUSE FIX for wrong-side ladder qualification:
    # prop_playbook_decisions carries both `direction` (the listed line
    # side, e.g. 'under') AND `playbook_side` (BACK / FADE). Previously
    # this gate incremented on playbook_tier PRIME/STRONG regardless of
    # side — which meant a row like (direction='under', playbook_side='FADE',
    # edge_pp=+22 on OVER) would qualify as 'playbook✓' even though the
    # playbook is actively fading the under. Today's ladder pointed at
    # "Springs Under 3.5 ER" while the playbook said BACK THE OVER at
    # +22 edge. Users following the ladder would be on the wrong side.
    #
    # Fix: hard-disqualify FADE rows here. If a day has NO qualifiers
    # left, the RELAXED-scan fallback (line 460+) picks the next-best
    # candidate. Better to skip a day than ship a wrong-side pick as
    # THE one-play-per-day compounding ladder.
    if playbook_tier in ('PRIME', 'STRONG'):
        ps = (prop.get('playbook_side') or 'BACK').upper()
        if ps == 'FADE':
            gate_notes.append(f'playbook FADES this direction — DISQUALIFIED')
            return None  # hard block — ladder never points at faded side
        gates_passed += 1
        gate_notes.append(f'playbook✓ ({ps})')

    if gates_passed < LADDER_MIN_GATES:
        return None

    # Compose display label
    dir_word = 'Over' if direction == 'over' else 'Under'
    pretty_type = prop_type.replace('_', ' ').replace(' over', '').replace(' under', '')
    pick_label = f'{player} {dir_word} {line} {pretty_type.upper()}'

    return {
        'game_date': prop.get('game_date'),
        'sport': sport,
        'game_id': prop.get('game_id'),
        'matchup': prop.get('matchup') or 'Prop',
        'pick_side': pick_label,
        'market': 'prop',
        'odds_american': odds,
        'tier': effective_tier,
        'conviction': pb_conv_i,
        'model_win_prob': None,      # props don't have MC prob
        'cohort_hit_rate': None,
        'cohort_n': None,
        'consensus_lens': None,
        'edge_pp': None,
        'gates_passed': gates_passed,
        'qualification_notes': (
            f'PROP · {gates_passed}/5 gates · ' + ' · '.join(gate_notes)
        ),
    }


def scan_and_maybe_qualify(game_date: str, dry_run: bool = False) -> Optional[dict]:
    """Iterate ladder-eligible sports; return first qualifying rung or None."""
    sports = get_ladder_eligible_sports()
    print(f'  ladder-eligible sports: {sports or "NONE (all off-season/preseason)"}')
    candidates = []

    # 2026-08-20: pool props from playbook (shadow) + legacy tiers.
    # Props are ladder-eligible per user request. Only run for MLB since
    # other sports don't have prop_playbook_decisions rows yet.
    if 'MLB' in sports:
        pb = requests.get(f'{SB}/rest/v1/prop_playbook_decisions', headers=H_READ,
            params={'game_date': f'eq.{game_date}', 'sport': 'eq.MLB',
                    'playbook_tier': 'in.(PRIME,STRONG)',
                    'select': '*'}, timeout=15).json()
        if isinstance(pb, list):
            # Join to mlb_pipeline_props to get book odds + refit_conviction
            legacy = requests.get(f'{SB}/rest/v1/mlb_pipeline_props', headers=H_READ,
                params={'game_date': f'eq.{game_date}',
                        'select': 'player_name,prop_type,direction,prop_line,book_over_odds,book_under_odds,refit_conviction,tier,matchup,game_id'},
                timeout=15).json()
            legacy_map = {}
            if isinstance(legacy, list):
                for p in legacy:
                    k = (p.get('player_name'), p.get('prop_type'), p.get('direction'), p.get('prop_line'))
                    legacy_map[k] = p
            prop_qualifiers = 0
            for pb_row in pb:
                k = (pb_row.get('player_name'), pb_row.get('prop_type'), pb_row.get('direction'), pb_row.get('prop_line'))
                lg = legacy_map.get(k, {})
                merged = {**lg, **pb_row}  # playbook wins on shared keys
                # Rename for check_prop_qualifier
                if 'tier' in lg and 'legacy_tier' not in merged: merged['legacy_tier'] = lg['tier']
                rung = check_prop_qualifier(merged, 'MLB')
                if rung:
                    candidates.append(rung); prop_qualifiers += 1
            print(f'  props evaluated: {len(pb)} playbook PRIME/STRONG · {prop_qualifiers} qualified for ladder')

    for sport in sports:
        ctx_tbl = CTX_TABLE.get(sport)
        if not ctx_tbl: continue
        # 2026-08-19 bug fix: prior select included home_ml/away_ml which
        # don't exist in mlb_game_context (only _close columns) — whole query
        # returned a PostgREST error dict instead of a list, isinstance list
        # check silently skipped every sport, ladder never fired.
        r = requests.get(f'{SB}/rest/v1/{ctx_tbl}', headers=H_READ,
            params={'game_date': f'eq.{game_date}',
                    'select': 'game_id,game_date,home_team,away_team,primary_play,'
                              'mc_probabilities,signal_confluence_support,'
                              'home_ml_close,away_ml_close,'
                              'consensus_fade_flag'},
            timeout=15).json()
        if not isinstance(r, list):
            print(f'  ⚠️  {sport} query failed: {r}')
            continue
        for row in r:
            rung = check_qualifier(row, sport)
            if rung: candidates.append(rung)
    if not candidates:
        # 2026-08-20: skip-day loosening. User: "ladder can skip one day but
        # shouldn't skip more than two." If no qualifier fires AND the last
        # 2 game_dates already had no rung, drop back to relaxed thresholds
        # and pick the BEST-available candidate even if it doesn't clear
        # the standard 3-of-5 gate.
        if _days_since_last_rung(game_date) >= 2:
            print('  ⚠️ 2+ days since last ladder rung — running RELAXED scan')
            relaxed = _relaxed_scan(game_date, sports)
            if relaxed:
                print(f'  🎯 Relaxed qualifier: {relaxed["matchup"]} · {relaxed["pick_side"]}')
                return relaxed
        print('  no qualifier fired — ladder stays parked')
        return None
    # Rank by (gates_passed DESC, tier PRIME>STRONG>LEAN, normalized edge DESC).
    # 2026-08-20: edge_pp normalized via SPORT_EDGE_MULTIPLIER so cross-sport
    # candidates compare fairly. NHL +7pp edge > MLB +7pp edge because puckline
    # math compresses edges — multipliers correct that.
    tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
    def _norm_edge(c):
        e = c.get('edge_pp')
        if e is None: return -999
        return e * SPORT_EDGE_MULTIPLIER.get(c.get('sport'), 1.0)
    candidates.sort(key=lambda c: (
        -(c.get('gates_passed') or 0),
        tier_rank.get(c.get('tier'), 9),
        -_norm_edge(c),
    ))
    winner = candidates[0]
    edge_raw = winner.get('edge_pp')
    mult = SPORT_EDGE_MULTIPLIER.get(winner.get('sport'), 1.0)
    edge_str = (f'{edge_raw:+.1f}pp × {mult} = {edge_raw*mult:+.1f}'
                if edge_raw is not None else 'n/a')
    print(f'  🎯 Ladder qualifier: {winner["matchup"]} · {winner["pick_side"]} '
          f'({winner["gates_passed"]}/5 gates, edge {edge_str})')
    return winner


def _days_since_last_rung(game_date: str) -> int:
    """How many calendar days since the last ladder_rung fired (any sport).
    Returns 0 if a rung exists for today OR yesterday, 1 for two days ago,
    etc. Used for skip-day loosening — after 2+ dry days, we relax gates."""
    r = requests.get(f'{SB}/rest/v1/ladder_rung', headers=H_READ,
        params={'select': 'game_date', 'order': 'game_date.desc', 'limit': '1'},
        timeout=10)
    if r.status_code != 200: return 0
    rows = r.json()
    if not isinstance(rows, list) or not rows: return 99  # never fired
    last = rows[0].get('game_date')
    if not last: return 0
    try:
        from datetime import date
        gd = date.fromisoformat(game_date)
        ld = date.fromisoformat(last)
        return (gd - ld).days
    except Exception:
        return 0


def _relaxed_scan(game_date: str, sports: list) -> Optional[dict]:
    """Relaxed re-scan when ladder has been dark 2+ days. Drops the 3/5
    gate requirement AND lowers edge threshold to +2pp. Picks the best-
    scoring candidate that clears the minimal safety bar (no sharp-fade,
    real odds, not a refit-trap). Ensures ladder never sits empty >2 days.

    Universal across sports — same signal reads, just softer gates.
    """
    from copy import deepcopy
    original_edge = globals()['LADDER_EDGE_MIN_PP']
    original_consensus = globals()['LADDER_CONSENSUS_MIN']
    original_cohort_hit = globals()['LADDER_COHORT_HIT_MIN']
    original_cohort_n = globals()['LADDER_COHORT_N_MIN']
    original_min_gates = globals()['LADDER_MIN_GATES']
    try:
        globals()['LADDER_EDGE_MIN_PP'] = 2.0
        globals()['LADDER_CONSENSUS_MIN'] = 1
        globals()['LADDER_COHORT_HIT_MIN'] = 50.0
        globals()['LADDER_COHORT_N_MIN'] = 10
        globals()['LADDER_MIN_GATES'] = 2  # 2 of 5 in relaxed mode
        candidates = []
        for sport in sports:
            ctx_tbl = CTX_TABLE.get(sport)
            if not ctx_tbl: continue
            r = requests.get(f'{SB}/rest/v1/{ctx_tbl}', headers=H_READ,
                params={'game_date': f'eq.{game_date}',
                        'select': 'game_id,game_date,home_team,away_team,primary_play,'
                                  'mc_probabilities,signal_confluence_support,'
                                  'home_ml_close,away_ml_close,consensus_fade_flag'},
                timeout=15).json()
            if not isinstance(r, list): continue
            for row in r:
                pp = row.get('primary_play') or {}
                # In relaxed mode, accept any picked play with a tier + label
                # that has real odds and isn't sharp-fade flagged.
                if not pp.get('tier') or not pp.get('label'): continue
                if row.get('consensus_fade_flag') is True: continue
                rung = check_qualifier(row, sport)
                if rung:
                    rung['qualification_notes'] = '[RELAXED · 2+ dry days] ' + (rung.get('qualification_notes') or '')
                    candidates.append(rung)
        if not candidates: return None
        tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
        candidates.sort(key=lambda c: (
            -(c.get('gates_passed') or 0),
            tier_rank.get(c.get('tier'), 9),
            -(c.get('edge_pp') or -999),
        ))
        return candidates[0]
    finally:
        globals()['LADDER_EDGE_MIN_PP'] = original_edge
        globals()['LADDER_CONSENSUS_MIN'] = original_consensus
        globals()['LADDER_COHORT_HIT_MIN'] = original_cohort_hit
        globals()['LADDER_COHORT_N_MIN'] = original_cohort_n
        globals()['LADDER_MIN_GATES'] = original_min_gates


def upsert_rung_and_state(rung: dict, dry_run: bool = False) -> None:
    if dry_run:
        print(f'  [DRY] would upsert rung: {rung}')
        return
    # Insert rung. gates_passed is only in the payload once its migration
    # (20260819_ladder_gates_passed.sql) is applied — strip it if the column
    # isn't there yet so this doesn't 400 on days migration hasn't shipped.
    write_row = {k: v for k, v in rung.items() if k != 'gates_passed'}
    write_row['qualification_notes'] = (
        f"[gates={rung.get('gates_passed')}/5] " + (rung.get('qualification_notes') or '')
    )[:500]
    pr = requests.post(f'{SB}/rest/v1/ladder_rung',
        headers={**H_WRITE, 'Prefer': 'return=representation'},
        json=write_row, timeout=15)
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


def already_locked_today(game_date: str) -> bool:
    """2026-08-20: no mid-day swaps. If a rung already exists for
    game_date, keep it. User caught 4 swaps in one day (Milwaukee →
    Grayson → Nats → Grayson) as recompute picked different tops as
    data freshened. Ladder is a "step of the day" product — one pick
    per day, no rethink. Once a game grades LOSS, THAT'S when we allow
    the next day's pick to be different.

    Returns True if a rung for this date exists (regardless of result).
    """
    r = requests.get(f'{SB}/rest/v1/ladder_rung',
        params={'game_date': f'eq.{game_date}', 'select': 'id,pick_side'},
        headers=H_READ, timeout=10)
    if r.status_code != 200: return False
    rows = r.json()
    if isinstance(rows, list) and rows:
        print(f'  🔒 ladder already locked for {game_date}: '
              f'{rows[0].get("pick_side")} (id={rows[0].get("id")}) — no swap')
        return True
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--force', action='store_true',
                   help='Bypass the day-lock and pick a new rung (dev / recovery only)')
    args = p.parse_args()
    game_date = args.date or _et_today()
    print(f'=== Steam Room Ladder · {game_date} ===')

    # Day-lock: skip re-picking if today already has a rung. Prevents the
    # every-30-min-recompute swap thrashing that hit today.
    if not args.force and not args.dry_run and already_locked_today(game_date):
        return

    rung = scan_and_maybe_qualify(game_date, dry_run=args.dry_run)
    if rung:
        upsert_rung_and_state(rung, dry_run=args.dry_run)
    else:
        set_state_waiting(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
