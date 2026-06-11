"""
signal_resolver.py — Aggregate every directional signal into ONE landing call.

Problem we're solving: the engine currently surfaces conflicting votes
(cohort says OVER, models say UNDER, props say OVER) and Jerry reads have
to pick one without explaining how they resolved the conflict. Users see
a wall of signals and lose trust.

This module accepts every available signal per game and outputs:
  - One direction (OVER / UNDER / HOME / AWAY / None)
  - One confidence tier (ELITE / STRONG / LEAN / LIGHT / SKIP)
  - One human-readable reason
  - Optional list of dissenting signals (for analyst-mode expand)

Tier definitions:
  ELITE  — 3/3 models + cohort engine + prop reverse all agree, all loud
  STRONG — Model majority (2/3 or 3/3) + cohort net loud in same direction
  LEAN   — Model majority + cohort lean OR cohort strong + models neutral
  LIGHT  — Single decisive signal (cohort STRONG_EDGE alone, prop HIGH alone)
           with no contradicting loud signal
  SKIP   — Signals contradict OR all neutral. No play.

Author note: this is the resolver layer the founder asked for on 6/10.
The engine still computes every individual signal as before; this layer
collapses them into a single shareable call.
"""
from typing import Dict, List, Optional
from collections import Counter


# Resolver thresholds — see comments for rationale
MODEL_DEADBAND = 0.3        # |model - line| < this counts as neutral

# 2026-06-10 night — RECALIBRATED after audit showed raw cohort net is
# mostly engine bias, not signal. The engine averages ~15.6 OVER cohorts
# and ~8.1 UNDER cohorts per game = +7.5 baseline tilt every night.
# Raw net 9-12 hits OVER at 55%; net 12-15 actually drops to 52% — NO
# monotonic signal in raw counts.
#
# What works: BASELINE-NORMALIZED DEVIATION.
#   deviation = (over_count - ENGINE_BASELINE_OVER) - (under_count - ENGINE_BASELINE_UNDER)
#   = how far this game's cohort signal sits above/below engine baseline tilt
#
# Audit hit rates on n=366 graded games by deviation bin:
#   dev -10 to -5  → 36% OVER (UNDER hits 64%)
#   dev -5 to -2   → 48% OVER
#   dev -2 to +2   → 51% OVER (baseline tilt, no edge)
#   dev +2 to +5   → 56% OVER
#   dev +5 to +10  → 74% OVER (real OVER signal)
#
# Thresholds chosen to match the 74% / 64% bands as STRONG (clear edge)
# and 56% as LEAN.
ENGINE_BASELINE_OVER = 15.6   # avg STRONG_EDGE+LOCK OVER cohorts per game
ENGINE_BASELINE_UNDER = 8.1   # avg STRONG_EDGE+LOCK UNDER cohorts per game

COHORT_DEV_LOUD = 5           # |deviation| >= 5 = real edge (74%/36% hit bands)
COHORT_DEV_LEAN = 2           # |deviation| >= 2 = lean (56%/48% bands)

PROP_DEADBAND = 0.4         # |prop_total_signal| < this counts as neutral


def _model_direction(model_val: Optional[float], close_total: Optional[float]) -> Optional[str]:
    """Return 'OVER' / 'UNDER' / None given a projection + line."""
    if model_val is None or close_total is None:
        return None
    delta = model_val - close_total
    if delta > MODEL_DEADBAND:
        return 'OVER'
    if delta < -MODEL_DEADBAND:
        return 'UNDER'
    return None


def _count_model_agreement(directions: List[Optional[str]]) -> Dict:
    """Return majority direction, count of each, and unanimity flag."""
    valid = [d for d in directions if d]
    if not valid:
        return {'majority': None, 'over': 0, 'under': 0, 'unanimous': False, 'voting': 0}
    counter = Counter(valid)
    over = counter['OVER']
    under = counter['UNDER']
    voting = len(valid)
    if over >= 2:
        majority = 'OVER'
    elif under >= 2:
        majority = 'UNDER'
    else:
        majority = None
    unanimous = (over == voting or under == voting)
    return {
        'majority': majority,
        'over': over,
        'under': under,
        'unanimous': unanimous,
        'voting': voting,
    }


def _classify_cohort_net(over_strong_count: int, under_strong_count: int) -> Dict:
    """Return direction (or None) and strength using BASELINE-NORMALIZED
    deviation. Raw count is dominated by engine bias (avg +7.5 OVER per
    game); deviation from baseline is the actual signal.

    Per 6/10 audit: deviation +5 → 74% OVER; deviation -5 → 36% OVER.
    """
    over_dev = over_strong_count - ENGINE_BASELINE_OVER
    under_dev = under_strong_count - ENGINE_BASELINE_UNDER
    # Net deviation: positive = more OVER signal than baseline, negative = more UNDER
    deviation = over_dev - under_dev

    # Also keep the raw gap for backward-compat / display
    gap = over_strong_count - under_strong_count

    if deviation >= COHORT_DEV_LOUD:
        return {'direction': 'OVER', 'strength': 'LOUD', 'gap': gap, 'deviation': round(deviation, 1)}
    if deviation <= -COHORT_DEV_LOUD:
        return {'direction': 'UNDER', 'strength': 'LOUD', 'gap': gap, 'deviation': round(deviation, 1)}
    if deviation >= COHORT_DEV_LEAN:
        return {'direction': 'OVER', 'strength': 'LEAN', 'gap': gap, 'deviation': round(deviation, 1)}
    if deviation <= -COHORT_DEV_LEAN:
        return {'direction': 'UNDER', 'strength': 'LEAN', 'gap': gap, 'deviation': round(deviation, 1)}
    return {'direction': None, 'strength': 'NEUTRAL', 'gap': gap, 'deviation': round(deviation, 1)}


def _classify_prop_reverse(pr_signal: Optional[Dict]) -> Dict:
    """Return direction (or None) and strength for prop reverse signal."""
    if not pr_signal:
        return {'direction': None, 'strength': 'NEUTRAL'}
    conf = pr_signal.get('confidence', 'NONE')
    ts = pr_signal.get('total_signal', 0)
    if conf == 'HIGH' and abs(ts) >= PROP_DEADBAND:
        return {'direction': 'OVER' if ts > 0 else 'UNDER', 'strength': 'LOUD'}
    if conf == 'MEDIUM' and abs(ts) >= PROP_DEADBAND:
        return {'direction': 'OVER' if ts > 0 else 'UNDER', 'strength': 'LEAN'}
    if conf == 'LOW' and abs(ts) >= 0.5:
        return {'direction': 'OVER' if ts > 0 else 'UNDER', 'strength': 'WEAK'}
    return {'direction': None, 'strength': 'NEUTRAL'}


def resolve_total(
    *,
    close_total: Optional[float],
    v3_total: Optional[float] = None,
    v4_total: Optional[float] = None,
    jerry_total: Optional[float] = None,
    cohort_over_strong_count: int = 0,
    cohort_under_strong_count: int = 0,
    prop_reverse: Optional[Dict] = None,
) -> Dict:
    """Resolve a single landing call for the game total.

    Returns a dict:
        {
            'direction': 'OVER' | 'UNDER' | None,
            'tier':      'ELITE' | 'STRONG' | 'LEAN' | 'LIGHT' | 'SKIP',
            'reason':    one sentence explaining the resolution,
            'dissent':   list of {signal, direction, reason} for analyst expand,
            'signals':   {models, cohort, props} raw classifications,
        }
    """
    # Per-signal classifications
    v3_d = _model_direction(v3_total, close_total)
    v4_d = _model_direction(v4_total, close_total)
    j_d = _model_direction(jerry_total, close_total)
    models = _count_model_agreement([v3_d, v4_d, j_d])
    cohort = _classify_cohort_net(cohort_over_strong_count, cohort_under_strong_count)
    props = _classify_prop_reverse(prop_reverse)

    signals = {'models': models, 'cohort': cohort, 'props': props}

    # ───────────── Resolution rules (priority order) ─────────────

    # 1. SKIP: no signals at all
    if not models['majority'] and not cohort['direction'] and not props['direction']:
        return _build_result(None, 'SKIP', 'No directional signal in models, cohort, or props.', [], signals)

    # 2. SKIP: model majority vs cohort LOUD opposite — true conflict, contested
    if models['majority'] and cohort['direction']:
        if models['majority'] != cohort['direction'] and cohort['strength'] == 'LOUD':
            dissent = _build_dissent_list(signals)
            return _build_result(
                None, 'SKIP',
                f"Model majority says {models['majority']} but cohort engine says {cohort['direction']} "
                f"with {abs(cohort.get('deviation', 0))}-cohort gap. Contested — no play.",
                dissent, signals)

    # 3. ELITE: all 3 models unanimous + cohort LOUD aligned + props loud aligned
    if (models['unanimous'] and models['voting'] == 3
            and cohort['direction'] == models['majority'] and cohort['strength'] == 'LOUD'
            and props['direction'] == models['majority'] and props['strength'] in ('LOUD', 'LEAN')):
        return _build_result(
            models['majority'], 'ELITE',
            f"All 3 models, cohort engine (+{abs(cohort.get('deviation', 0))} net), and prop pipeline "
            f"all point {models['majority']}.",
            [], signals)

    # 4. STRONG: model majority + cohort LOUD same direction
    if models['majority'] and cohort['direction'] == models['majority'] and cohort['strength'] == 'LOUD':
        reason = f"{models['voting']}/{models['voting']}" if models['unanimous'] else f"{max(models['over'], models['under'])}/{models['voting']}"
        return _build_result(
            models['majority'], 'STRONG',
            f"Model majority ({reason}) + cohort engine "
            f"(+{abs(cohort.get('deviation', 0))} net STRONG_EDGE) aligned {models['majority']}.",
            _build_dissent_list(signals, exclude_aligned=True), signals)

    # 5. LEAN: model majority + cohort LEAN same direction
    if models['majority'] and cohort['direction'] == models['majority'] and cohort['strength'] == 'LEAN':
        return _build_result(
            models['majority'], 'LEAN',
            f"Model majority + cohort lean (+{abs(cohort.get('deviation', 0))} net) "
            f"both point {models['majority']}.",
            _build_dissent_list(signals, exclude_aligned=True), signals)

    # 6. LEAN: model unanimity + cohort neutral
    if models['unanimous'] and models['voting'] == 3 and not cohort['direction']:
        return _build_result(
            models['majority'], 'LEAN',
            f"All 3 models point {models['majority']}, cohort engine neutral.",
            _build_dissent_list(signals, exclude_aligned=True), signals)

    # 7. LIGHT: cohort LOUD alone (no model majority, no contradicting props)
    if cohort['strength'] == 'LOUD' and not models['majority']:
        if not props['direction'] or props['direction'] == cohort['direction']:
            return _build_result(
                cohort['direction'], 'LIGHT',
                f"Cohort engine decisive (+{abs(cohort.get('deviation', 0))} net STRONG_EDGE {cohort['direction']}) "
                f"with models split.",
                _build_dissent_list(signals, exclude_aligned=True), signals)

    # 8. LIGHT: prop reverse HIGH alone (no contradicting cohort/models)
    if props['strength'] == 'LOUD' and not models['majority'] and not cohort['direction']:
        return _build_result(
            props['direction'], 'LIGHT',
            f"Prop pipeline alone points {props['direction']} (HIGH confidence).",
            _build_dissent_list(signals, exclude_aligned=True), signals)

    # 9. LIGHT: model majority, cohort neutral, no contradicting props
    if models['majority']:
        if not props['direction'] or props['direction'] == models['majority']:
            voting_str = f"{max(models['over'], models['under'])}/{models['voting']}"
            return _build_result(
                models['majority'], 'LIGHT',
                f"{voting_str} models lean {models['majority']}, cohort/props neutral.",
                _build_dissent_list(signals, exclude_aligned=True), signals)

    # 10. SKIP: catch-all for anything that didn't resolve cleanly
    return _build_result(
        None, 'SKIP',
        "Signals don't agree cleanly — no clear landing.",
        _build_dissent_list(signals), signals)


def _build_dissent_list(signals: Dict, exclude_aligned: bool = False) -> List[Dict]:
    """Build the analyst-mode dissent list.
    If exclude_aligned=True, only include signals that DISAGREE with the resolved direction.
    """
    out = []
    models = signals['models']
    cohort = signals['cohort']
    props = signals['props']

    if models['voting'] and (models['over'] > 0 or models['under'] > 0):
        if not exclude_aligned or not models['unanimous']:
            out.append({
                'signal': 'models',
                'detail': f"{models['voting']} models voting (OVER {models['over']} / UNDER {models['under']})",
                'direction': models['majority'],
            })

    if cohort['direction']:
        out.append({
            'signal': 'cohort',
            'detail': f"net +{abs(cohort.get('deviation', 0))} {cohort['direction']} ({cohort['strength']})",
            'direction': cohort['direction'],
        })

    if props['direction']:
        out.append({
            'signal': 'props',
            'detail': f"prop pipeline {props['direction']} ({props['strength']})",
            'direction': props['direction'],
        })

    return out


def _build_result(direction, tier, reason, dissent, signals) -> Dict:
    return {
        'direction': direction,
        'tier': tier,
        'reason': reason,
        'dissent': dissent,
        'signals': signals,
    }


# ────────────────────────────────────────────────────────────────────────
# SIDE RESOLVER (added 2026-06-10 night).
#
# ML/RL cohort engine has structural HOME bias — 4 STRONG_EDGE+LOCK home
# rules vs 0 AWAY rules at the loud tier (per veto audit n=540). The
# AWAY rules never qualified for STRONG_EDGE, not because recency veto
# stripped them (only 5 ML/RL rules vetoed total, none at high tier).
#
# Fix: include LEAN-tier matches in the count, use baseline-normalized
# deviation just like the total resolver. Audit on n=383:
#   ML avg HOME cohorts/game: 2.22 (incl LEAN)
#   ML avg AWAY cohorts/game: 1.05 (incl LEAN)
#   baseline home-win rate: 51%
#
# Baseline-normalized deviation predicts outcomes:
#   dev -5 to -2 → 35% home (AWAY hits 65%, n=74)
#   dev -2 to +2 → 53% home (at baseline)
#   dev +2 to +5 → 70% home (n=47)
#
# Cohort counts on ML are ~7x smaller than totals (avg 2 vs 15 per
# direction), so thresholds are scaled down.

ML_BASELINE_HOME = 2.22
ML_BASELINE_AWAY = 1.05
RL_BASELINE_HOME = 1.5   # to be calibrated similarly when RL audit lands
RL_BASELINE_AWAY = 1.5

SIDE_DEV_LOUD = 2.0       # |dev| >= 2 (70%/35% hit bands)
SIDE_DEV_LEAN = 1.0       # |dev| >= 1 (~58%/45% bands)


def _classify_side_cohort(home_count: int, away_count: int,
                          baseline_home: float, baseline_away: float) -> Dict:
    """Mirror of _classify_cohort_net but for ML/RL HOME vs AWAY counts."""
    home_dev = home_count - baseline_home
    away_dev = away_count - baseline_away
    deviation = home_dev - away_dev  # positive = HOME signal, negative = AWAY
    gap = home_count - away_count

    if deviation >= SIDE_DEV_LOUD:
        return {'direction': 'HOME', 'strength': 'LOUD', 'gap': gap,
                'deviation': round(deviation, 1)}
    if deviation <= -SIDE_DEV_LOUD:
        return {'direction': 'AWAY', 'strength': 'LOUD', 'gap': gap,
                'deviation': round(deviation, 1)}
    if deviation >= SIDE_DEV_LEAN:
        return {'direction': 'HOME', 'strength': 'LEAN', 'gap': gap,
                'deviation': round(deviation, 1)}
    if deviation <= -SIDE_DEV_LEAN:
        return {'direction': 'AWAY', 'strength': 'LEAN', 'gap': gap,
                'deviation': round(deviation, 1)}
    return {'direction': None, 'strength': 'NEUTRAL', 'gap': gap,
            'deviation': round(deviation, 1)}


def _model_side_direction(model_signed_spread: Optional[float]) -> Optional[str]:
    """For ML/RL, a model's spread projection where positive = home favored.
    Returns 'HOME' / 'AWAY' / None based on direction and the 0.5 deadband.
    """
    if model_signed_spread is None or abs(model_signed_spread) < 0.5:
        return None
    return 'HOME' if model_signed_spread > 0 else 'AWAY'


def resolve_side(
    *,
    close_spread: Optional[float] = None,
    v3_spread: Optional[float] = None,
    v4_spread: Optional[float] = None,
    jerry_spread: Optional[float] = None,
    ml_home_cohort_count: int = 0,
    ml_away_cohort_count: int = 0,
    rl_home_cohort_count: int = 0,
    rl_away_cohort_count: int = 0,
    confluence_net: Optional[int] = None,
    prop_reverse: Optional[Dict] = None,
) -> Dict:
    """Resolve single landing call for the game side (ML / RL direction).

    Direction in the output is 'HOME' or 'AWAY' or None.

    Logic parallels resolve_total: aggregate model spread votes + cohort
    engine net (baseline-normalized) + confluence + prop_reverse side
    signal into one tier call.
    """
    # Model spread direction votes
    v3_d = _model_side_direction(v3_spread)
    v4_d = _model_side_direction(v4_spread)
    j_d = _model_side_direction(jerry_spread)
    models = _count_model_agreement([v3_d, v4_d, j_d])
    # Adjust majority labels (resolve_total uses OVER/UNDER; here HOME/AWAY)
    # _count_model_agreement returns over/under counts — rebuild:
    model_votes = [d for d in (v3_d, v4_d, j_d) if d]
    home_votes = sum(1 for d in model_votes if d == 'HOME')
    away_votes = sum(1 for d in model_votes if d == 'AWAY')
    voting = len(model_votes)
    if home_votes >= 2:
        majority = 'HOME'
    elif away_votes >= 2:
        majority = 'AWAY'
    else:
        majority = None
    unanimous = (home_votes == voting or away_votes == voting) and voting > 0
    models = {'majority': majority, 'home': home_votes, 'away': away_votes,
              'unanimous': unanimous, 'voting': voting}

    # ML cohort (primary side signal)
    ml_cohort = _classify_side_cohort(
        ml_home_cohort_count, ml_away_cohort_count,
        ML_BASELINE_HOME, ML_BASELINE_AWAY,
    )
    rl_cohort = _classify_side_cohort(
        rl_home_cohort_count, rl_away_cohort_count,
        RL_BASELINE_HOME, RL_BASELINE_AWAY,
    )

    # Confluence: positive = home, negative = away. >= 4 magnitude is loud.
    confl_dir = None
    confl_strength = 'NEUTRAL'
    if confluence_net is not None:
        try:
            cn = int(confluence_net)
            if cn >= 4:
                confl_dir = 'HOME'
                confl_strength = 'LOUD'
            elif cn >= 2:
                confl_dir = 'HOME'
                confl_strength = 'LEAN'
            elif cn <= -4:
                confl_dir = 'AWAY'
                confl_strength = 'LOUD'
            elif cn <= -2:
                confl_dir = 'AWAY'
                confl_strength = 'LEAN'
        except (TypeError, ValueError):
            pass
    confluence = {'direction': confl_dir, 'strength': confl_strength}

    # Prop reverse side signal
    props = {'direction': None, 'strength': 'NEUTRAL'}
    if prop_reverse:
        side_s = prop_reverse.get('side_signal') or 0
        conf_lbl = prop_reverse.get('confidence', 'NONE')
        if conf_lbl == 'HIGH' and abs(side_s) >= 0.5:
            props = {'direction': 'HOME' if side_s > 0 else 'AWAY', 'strength': 'LOUD'}
        elif conf_lbl == 'MEDIUM' and abs(side_s) >= 0.4:
            props = {'direction': 'HOME' if side_s > 0 else 'AWAY', 'strength': 'LEAN'}

    signals = {'models': models, 'ml_cohort': ml_cohort, 'rl_cohort': rl_cohort,
               'confluence': confluence, 'props': props}

    # ───── Resolution rules (priority order) ─────

    # SKIP: no signals at all
    if (not models['majority'] and not ml_cohort['direction']
            and not confluence['direction'] and not props['direction']):
        return _build_result(None, 'SKIP',
                             'No directional signal in models, ML cohort, confluence, or props.',
                             [], signals)

    # SKIP: model majority + ML cohort LOUD opposite — contested
    if models['majority'] and ml_cohort['direction']:
        if models['majority'] != ml_cohort['direction'] and ml_cohort['strength'] == 'LOUD':
            return _build_result(None, 'SKIP',
                                 f"Model majority says {models['majority']} but ML cohort engine says "
                                 f"{ml_cohort['direction']} (+{abs(ml_cohort['deviation'])} above baseline). Contested.",
                                 [], signals)

    # ELITE: unanimous models + ML cohort LOUD + confluence aligned + props aligned
    if (models['unanimous'] and models['voting'] >= 3
            and ml_cohort['direction'] == models['majority']
            and ml_cohort['strength'] == 'LOUD'
            and confluence['direction'] == models['majority']
            and props.get('direction') == models['majority']):
        return _build_result(
            models['majority'], 'ELITE',
            f"All 3 models, ML cohort (+{abs(ml_cohort['deviation'])} dev), confluence, "
            f"and prop pipeline all point {models['majority']}.",
            [], signals)

    # STRONG: model majority + ML cohort LOUD same direction
    if (models['majority'] and ml_cohort['direction'] == models['majority']
            and ml_cohort['strength'] == 'LOUD'):
        return _build_result(
            models['majority'], 'STRONG',
            f"Model majority ({max(models['home'], models['away'])}/{models['voting']}) "
            f"+ ML cohort (+{abs(ml_cohort['deviation'])} dev) both point {models['majority']}.",
            [], signals)

    # LEAN: model majority + ML cohort LEAN same direction
    if (models['majority'] and ml_cohort['direction'] == models['majority']
            and ml_cohort['strength'] == 'LEAN'):
        return _build_result(
            models['majority'], 'LEAN',
            f"Model majority + ML cohort lean (+{abs(ml_cohort['deviation'])} dev) "
            f"both point {models['majority']}.",
            [], signals)

    # LEAN: model unanimity + confluence LOUD same direction
    if (models['unanimous'] and models['voting'] >= 3
            and confluence['direction'] == models['majority']
            and confluence['strength'] == 'LOUD'):
        return _build_result(
            models['majority'], 'LEAN',
            f"All 3 models point {models['majority']}, confluence (+{confluence_net}) confirms.",
            [], signals)

    # LIGHT: ML cohort LOUD alone (models silent)
    if ml_cohort['strength'] == 'LOUD' and not models['majority']:
        return _build_result(
            ml_cohort['direction'], 'LIGHT',
            f"ML cohort engine alone points {ml_cohort['direction']} "
            f"(+{abs(ml_cohort['deviation'])} dev above baseline).",
            [], signals)

    # LIGHT: model majority + ML cohort or confluence weakly agrees
    if models['majority']:
        if (ml_cohort['direction'] == models['majority']
                or confluence['direction'] == models['majority']
                or props.get('direction') == models['majority']):
            return _build_result(
                models['majority'], 'LIGHT',
                f"Model majority + one supporting signal point {models['majority']}.",
                [], signals)

    # Default SKIP
    return _build_result(None, 'SKIP',
                         "Signals don't agree cleanly — no clear landing.",
                         [], signals)


if __name__ == '__main__':
    # Smoke test on tonight's games
    import sys
    import os
    from dotenv import load_dotenv
    load_dotenv()
    sys.stdout.reconfigure(encoding='utf-8')
    import requests

    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')

    # Pull tonight's contexts + cohort engine + prop reverse
    r = requests.get(
        f'{url}/rest/v1/mlb_game_context',
        headers={'apikey': key, 'Authorization': f'Bearer {key}'},
        params=[('select', '*'), ('game_date', 'eq.2026-06-10')],
    )
    games = r.json()

    # Pull tonight's prop reverse signals
    import json as _json
    r2 = requests.get(
        f'{url}/rest/v1/jerry_cache',
        headers={'apikey': key, 'Authorization': f'Bearer {key}'},
        params=[('select', 'data'), ('cache_key', 'eq.prop_reverse_signals_2026-06-10')],
    )
    pr_data = r2.json()[0]['data'] if r2.json() else {}
    pr_signals = pr_data.get('signals') if isinstance(pr_data, dict) else {}

    from cohort_signals import evaluate_game_for_play

    print(f"{'GAME':<32}{'CALL':<8}{'TIER':<8}{'REASON'}")
    print('-' * 130)
    for g in games:
        away = (g.get('away_team') or '?')[:14]
        home = (g.get('home_team') or '?')[:14]
        matchup = f"{g.get('away_team')} @ {g.get('home_team')}"

        # Count cohort STRONG_EDGE+ matches each direction
        def count_strong(direction):
            m = evaluate_game_for_play(g, 'v3_tot', direction) or []
            return len([x for x in m if x.get('tier') in ('LOCK', 'STRONG_EDGE')
                        and not x.get('id', '').endswith('|any')])

        over_n = count_strong('over')
        under_n = count_strong('under')

        result = resolve_total(
            # Match the pipeline convention everywhere else (compute_primary_play,
            # build_lean, score_game): fall back to open_total when close_total
            # isn't set yet. Manual noon runs and stale-data slates won't have
            # close populated, but open is always good enough as the comparison
            # anchor for the resolver.
            close_total=(g.get('close_total') or g.get('open_total')),
            v3_total=g.get('projected_total'),
            v4_total=g.get('model_pred_total'),
            jerry_total=g.get('jerry_pred_total'),
            cohort_over_strong_count=over_n,
            cohort_under_strong_count=under_n,
            prop_reverse=pr_signals.get(matchup),
        )

        direction = result['direction'] or '-'
        tier = result['tier']
        print(f"{away}@{home:<15} {direction:<8}{tier:<8}{result['reason']}")
