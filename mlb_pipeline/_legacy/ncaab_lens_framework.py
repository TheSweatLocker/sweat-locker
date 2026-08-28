"""NCAAB 4-lens scoring framework — scaffold (2026-08-19).

Skeleton for the four-lens system planned for Nov 3, 2026 launch. Each
lens takes a game_context row and returns a normalized LensOpinion:
side (HOME/AWAY/OVER/UNDER/PASS), confidence (0-1), rationale, and the
signals that fired. The Sharp Card scorer combines these into a final
tier (PRIME/STRONG/LEAN/PASS) via weighted vote — implementation
lives elsewhere; this file is the *contract*.

WHY FOUR LENSES (v1.0)
    Reduces model risk. If a single model has a bad week, the other three
    catch it. Cross-lens agreement is itself a signal: 4/4 agreement is
    the PRIME gate; 2/4 splits are auto-LEAN or auto-PASS.

    Matches the MLB Sharp Card pattern (multi-lens consensus, cadence
    downstream filter) which lifted MLB hit rate 4-6pp on backtest.

THE FOUR LENSES
    1. EfficiencyLens   — kenpom+torvik+haslam panel (adj_em edge).
                          Owner of "which team is objectively better."
                          Reads: ncaab_team_efficiency (materialized panel).

    2. FormLens         — recent momentum: L10 ATS, L10 ML, home/road
                          splits, streak coding. Reads live game_context
                          fields (home_ml_l10_at_home, away_ats_last5, ...).

    3. PaceLens         — total-only lens. Combined tempo, blended
                          off/def efficiency mismatch. Filters total picks
                          UNDER a stddev band around blended projection.

    4. CohortLens       — historical priors: em_gap size, road_fav_10+,
                          season phase (early/conf/tourney), rest edge,
                          ranked-vs-unranked. Reads ncaab_cohort_stats +
                          mlb_tier_calibration (sport='NCAAB').

FILL-IN SEQUENCE
    Session S6 (mid Sept) — EfficiencyLens.score()  (data ready today)
    Session S7 (late Sept) — FormLens.score()       (needs 2 wks of games)
    Session S8 (early Oct) — PaceLens.score()       (needs live tempo)
    Session S9 (mid Oct)   — CohortLens.score()     (needs backfilled cohorts)

    Everything below is DELIBERATELY unimplemented. The class contracts
    are the deliverable. Fill in .score() during those sessions — the
    scorer + ensemble already know how to consume LensOpinion.

INVARIANTS
    * Every .score() MUST return a LensOpinion — never raise.
    * side='PASS' + confidence=0.0 is the correct "no opinion" response.
    * confidence normalized to [0, 1] regardless of internal math.
    * rationale is a Jerry-ready sentence (or None if PASS).
    * No lens may reference component rating systems by name in any
      user-visible field — call it "efficiency model" or "efficiency
      panel" per project rule feedback_no_kenpom_attribution.md.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal


# ═══════════════════════════════════════════════════════════════════════
# Contract
# ═══════════════════════════════════════════════════════════════════════

Side = Literal['HOME_ML', 'AWAY_ML', 'HOME_RL', 'AWAY_RL',
               'OVER', 'UNDER', 'PASS']


@dataclass
class LensOpinion:
    """A single lens's read on one game.

    lens_name    Identifies which lens produced this. Used by the
                 ensemble scorer for weighting.
    side         Pick side. 'PASS' when the lens has no read.
    confidence   [0.0, 1.0]. 0 for PASS, 1 for maximum conviction.
                 Ensemble converts to a weighted vote share.
    rationale    Jerry-ready sentence describing WHY. None on PASS.
                 Must NOT name any component rating system.
    signals_hit  List of signal_key values that fired inside this lens.
                 Feeds the audit trail so we can trace which signal
                 drove which lens.
    metadata     Free-form dict for lens-specific diagnostics (adj_em_gap,
                 stddev, cohort_hit_rate, etc.). Ensemble ignores; audit
                 tools use.
    """
    lens_name: str
    side: Side
    confidence: float
    rationale: Optional[str] = None
    signals_hit: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class BaseLens:
    """All four lenses inherit from this.

    Subclasses MUST implement .score(ctx) and MUST NOT raise. A lens
    that can't decide should return LensOpinion(side='PASS', confidence=0.0).
    """
    name: str = 'base'

    def score(self, ctx: dict) -> LensOpinion:
        raise NotImplementedError

    # Utility used by every subclass
    def _pass(self, reason: str = '') -> LensOpinion:
        return LensOpinion(lens_name=self.name, side='PASS',
                            confidence=0.0, rationale=None,
                            metadata={'pass_reason': reason} if reason else {})


# ═══════════════════════════════════════════════════════════════════════
# Lens 1 — Efficiency (3-source panel)
# ═══════════════════════════════════════════════════════════════════════

class EfficiencyLens(BaseLens):
    """Panel-based objective-quality lens.

    Reads ncaab_team_efficiency (panel-materialized) OR the live panel
    stashed in ctx.panel_prediction. Emits ML/RL opinion driven by
    panel_adj_em gap. Filters when systems_available < 2.

    Thresholds (starting point — recalibrate against ncaab_signal_registry
    once we have 30d of live production data):
        |em_gap| < 3.0                     → PASS (pickem, no edge)
        3.0 <= |em_gap| < 6.0              → LEAN  (conf ~0.4)
        6.0 <= |em_gap| < 10.0             → STRONG (conf ~0.6-0.75)
        |em_gap| >= 10.0                   → PRIME candidate (conf ~0.9)

    Fires signals: ncaab_home_efficiency_edge, ncaab_away_efficiency_edge
    (already seeded in signal_sources).
    """
    name = 'efficiency'

    def score(self, ctx: dict) -> LensOpinion:
        # TODO(sess-S6): implement in mid-September once ncaab_team_efficiency
        # has been materialized against 2024-25 + rolling 2025-26 data.
        # Read panel_adj_em for home + away, compute em_gap, gate on
        # systems_available >= 2, translate to LensOpinion per the
        # thresholds in the docstring.
        return self._pass('not_yet_implemented')


# ═══════════════════════════════════════════════════════════════════════
# Lens 2 — Form (recent momentum)
# ═══════════════════════════════════════════════════════════════════════

class FormLens(BaseLens):
    """Recent-form lens.

    Reads live game_context fields populated by
    enrich_ncaab_team_trends.py + pull_ncaab_teamrankings_trends.py:
        ctx.home_ml_l10_at_home    (0-10)
        ctx.away_ml_l10_on_road    (0-10)
        ctx.home_ats_last5         (0-5)
        ctx.away_ats_last5         (0-5)
        ctx.home_over_last5        (0-5)
        ctx.away_over_last5        (0-5)
        ctx.home_streak            ('W3', 'L2', etc.)

    Fires signals: home_ml_hot_at_home, away_ml_hot_on_road,
    home_ats_hot_at_home, away_ats_hot_on_road,
    home_team_over_trend_season, away_team_over_trend_season,
    both_teams_over_trend, both_teams_under_trend, plus their cold/fade
    counterparts.

    Confidence source: strength of the momentum agreement across
    ML + ATS + O/U axes. Full 3-axis agreement → STRONG; single-axis
    → LEAN.
    """
    name = 'form'

    def score(self, ctx: dict) -> LensOpinion:
        # TODO(sess-S7): implement in late September. Wait for 2 weeks of
        # 2025-26 games so L10 windows are meaningful (opening week L10
        # is really L1-L5, high variance).
        return self._pass('not_yet_implemented')


# ═══════════════════════════════════════════════════════════════════════
# Lens 3 — Pace (total-only)
# ═══════════════════════════════════════════════════════════════════════

class PaceLens(BaseLens):
    """Total-only tempo/efficiency lens.

    Combines home + away tempo + panel off/def ratings into a projected
    total, compares to close_total, emits OVER/UNDER when edge >= band
    (default 3.5 points). Never emits ML/RL — those are for Efficiency
    and Form to fight over.

    Fires signals: ncaab_slow_pace_under, ncaab_fast_pace_over,
    ncaab_projected_total, ncaab_panel_total,
    ncaab_offensive_mismatch_home, ncaab_defensive_matchup_under.

    Blender formula (from ncaab_panel_predictor.py):
        home_expect = panel_home_off * (panel_away_def / league_avg_eff)
                      * pace / 100 + HFA
        away_expect = panel_away_off * (panel_home_def / league_avg_eff)
                      * pace / 100
        projected_total = home_expect + away_expect
        edge            = abs(projected_total - close_total)
    """
    name = 'pace'

    def score(self, ctx: dict) -> LensOpinion:
        # TODO(sess-S8): implement in early October. Depends on live
        # close_total from ncaab_odds_pull, which starts Nov cadence but
        # gets a preseason test-fire mid-October.
        return self._pass('not_yet_implemented')


# ═══════════════════════════════════════════════════════════════════════
# Lens 4 — Cohort (historical priors)
# ═══════════════════════════════════════════════════════════════════════

class CohortLens(BaseLens):
    """Historical-cohort prior lens.

    For each game, identify which historical cohorts apply
    (kp_elite_fav_su_20+, road_fav_10+, high_pace_avg75+, home_rest_edge,
    ranked_vs_unranked, season_phase_early, etc.), look up hit rate in
    ncaab_cohort_stats + mlb_tier_calibration (sport='NCAAB'), and emit
    a side + confidence when at least one cohort with n >= 30 shows
    hit_rate >= 60% (STRONG) or hit_rate <= 40% (FADE the implied side).

    Confidence = weighted avg of |hit_rate - 0.5| across firing cohorts,
    scaled by sample sizes. Gate: at least one n >= 30 cohort must fire.

    Fires signals: ncaab_confluence_home, ncaab_confluence_away (via the
    signal_confluence_net rollup fed by cohort agreement counts).
    """
    name = 'cohort'

    def score(self, ctx: dict) -> LensOpinion:
        # TODO(sess-S9): implement mid-October once ncaab_cohort_backfill
        # has run against 2024-25 + we've validated at least the SU
        # cohorts land at expected win rates.
        return self._pass('not_yet_implemented')


# ═══════════════════════════════════════════════════════════════════════
# Ensemble stub — implemented by scorer, not this file
# ═══════════════════════════════════════════════════════════════════════

def ensemble_lens_stack() -> list[BaseLens]:
    """Canonical Nov-3-launch lens ordering.

    Returned in the exact order the scorer uses for tie-breaking on
    equal-confidence disagreements: Efficiency wins over Form wins over
    Cohort. PaceLens only participates in total decisions.
    """
    return [EfficiencyLens(), FormLens(), CohortLens(), PaceLens()]


# ═══════════════════════════════════════════════════════════════════════
# Sanity check — importable, no side effects
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # Smoke: every lens must return a LensOpinion on empty ctx and not raise
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try: sys.stdout.reconfigure(encoding='utf-8')
        except Exception: pass
    stub_ctx = {'game_id': 'test', 'home_team': 'A', 'away_team': 'B'}
    for lens in ensemble_lens_stack():
        op = lens.score(stub_ctx)
        assert isinstance(op, LensOpinion), f'{lens.name} did not return LensOpinion'
        assert op.side in ('HOME_ML','AWAY_ML','HOME_RL','AWAY_RL','OVER','UNDER','PASS')
        assert 0.0 <= op.confidence <= 1.0
        print(f'  {lens.name:<12} PASS-check ok -> side={op.side} conf={op.confidence}')
    print('\nAll 4 lenses import + return valid LensOpinion (scaffold complete).')
