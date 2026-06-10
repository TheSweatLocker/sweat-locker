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
COHORT_NET_LOUD = 5         # |over_count - under_count| >= 5 = loud
COHORT_NET_LEAN = 3         # 3-4 = lean
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
    """Return direction (or None) and strength label for the cohort net."""
    gap = over_strong_count - under_strong_count
    if gap >= COHORT_NET_LOUD:
        return {'direction': 'OVER', 'strength': 'LOUD', 'gap': gap}
    if gap <= -COHORT_NET_LOUD:
        return {'direction': 'UNDER', 'strength': 'LOUD', 'gap': gap}
    if gap >= COHORT_NET_LEAN:
        return {'direction': 'OVER', 'strength': 'LEAN', 'gap': gap}
    if gap <= -COHORT_NET_LEAN:
        return {'direction': 'UNDER', 'strength': 'LEAN', 'gap': gap}
    return {'direction': None, 'strength': 'NEUTRAL', 'gap': gap}


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
                f"with {abs(cohort['gap'])}-cohort gap. Contested — no play.",
                dissent, signals)

    # 3. ELITE: all 3 models unanimous + cohort LOUD aligned + props loud aligned
    if (models['unanimous'] and models['voting'] == 3
            and cohort['direction'] == models['majority'] and cohort['strength'] == 'LOUD'
            and props['direction'] == models['majority'] and props['strength'] in ('LOUD', 'LEAN')):
        return _build_result(
            models['majority'], 'ELITE',
            f"All 3 models, cohort engine (+{abs(cohort['gap'])} net), and prop pipeline "
            f"all point {models['majority']}.",
            [], signals)

    # 4. STRONG: model majority + cohort LOUD same direction
    if models['majority'] and cohort['direction'] == models['majority'] and cohort['strength'] == 'LOUD':
        reason = f"{models['voting']}/{models['voting']}" if models['unanimous'] else f"{max(models['over'], models['under'])}/{models['voting']}"
        return _build_result(
            models['majority'], 'STRONG',
            f"Model majority ({reason}) + cohort engine "
            f"(+{abs(cohort['gap'])} net STRONG_EDGE) aligned {models['majority']}.",
            _build_dissent_list(signals, exclude_aligned=True), signals)

    # 5. LEAN: model majority + cohort LEAN same direction
    if models['majority'] and cohort['direction'] == models['majority'] and cohort['strength'] == 'LEAN':
        return _build_result(
            models['majority'], 'LEAN',
            f"Model majority + cohort lean (+{abs(cohort['gap'])} net) "
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
                f"Cohort engine decisive (+{abs(cohort['gap'])} net STRONG_EDGE {cohort['direction']}) "
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
            'detail': f"net +{abs(cohort['gap'])} {cohort['direction']} ({cohort['strength']})",
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


# Future: resolve_side() with parallel logic for ML/RL once we have:
#   - confluence_net (current ML signal)
#   - ML cohort engine (added 6/9 OAA cohorts at 90.9%)
#   - prop reverse side_signal
# Same tier hierarchy, same resolver shape.


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
