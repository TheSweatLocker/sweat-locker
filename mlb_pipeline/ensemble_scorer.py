"""Ensemble Scorer v2 — auto-discovered evidence-weighted decision engine.

v2 (2026-08-16): rebuilt to iterate `signal_sources` rows instead of
hardcoding signal handlers. Every signal is a plug-in row in the table.
Adding one now = INSERT row, no code change to this file.

Architecture:
  For each game:
    1. Fetch every signal_sources row for the sport (universal + sport-specific)
    2. For each source:
       a. If class is expression-based (model/pitcher/offense/etc.):
          - Evaluate condition_expr against ctx. Skip if False.
          - Evaluate side_expr → candidate label.
          - Evaluate strength_expr → [0, 1].
       b. If class is handler-based (split/scenario/external_pick):
          - Dispatch to _handler_split / _handler_scenario / _handler_external.
          - Handler fetches supplementary tables + returns list[Opinion].
    3. Weight each opinion by its historical hit_rate (from signal_registry
       or the row's inline hit_rate_pct/sample_n).
    4. Aggregate per (market, candidate) with class-balance rule.
    5. Score three separate market decisions (ml / rl / total) and a top pick.

Output: PerGameDecision with three DecisionPerMarket + top_market pointer +
full audit trail (which sources fired, each contribution, prose narration
Jerry can quote).

Sport-universal: only signal_sources rows for the target sport are loaded,
so NFL/NCAAF/UFC drop in by seeding their own rows.

CLI:
  python ensemble_scorer.py --sport MLB --date 2026-08-16
  python ensemble_scorer.py --sport MLB --date 2026-08-16 --limit 3
  python ensemble_scorer.py --sport MLB --date 2026-08-16 --game-id XYZ --verbose
"""
from __future__ import annotations
import argparse, os, sys, json, math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Callable
from datetime import date
from collections import defaultdict

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

_SB = os.environ.get('SUPABASE_URL')
_KEY = os.environ.get('SUPABASE_KEY')
_H_READ = {'apikey': _KEY, 'Authorization': f'Bearer {_KEY}'} if _KEY else {}

from signal_expr import evaluate, evaluate_bool, evaluate_str, evaluate_float, render_prose, AttrDict


# ═══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

BREAKEVEN = 0.524  # -110 breakeven

# Candidate labels by market (per-sport where markets differ)
CANDIDATES_BY_MARKET = {
    'ml':    ['HOME_ML', 'AWAY_ML'],
    'rl':    ['HOME_RL', 'AWAY_RL'],
    'total': ['OVER', 'UNDER'],
    'fight': ['FIGHTER_A_ML', 'FIGHTER_B_ML'],   # UFC / MMA
}

# Sport → which markets are scored. Team sports get ml/rl/total.
# Combat sports get fight only (props layer handles method/rounds/etc.).
MARKETS_BY_SPORT = {
    'MLB':   ['ml', 'rl', 'total'],
    'NFL':   ['ml', 'rl', 'total'],
    'NCAAF': ['ml', 'rl', 'total'],
    'NCAAB': ['ml', 'rl', 'total'],
    'NBA':   ['ml', 'rl', 'total'],
    'NHL':   ['ml', 'rl', 'total'],
    'UFC':   ['fight'],
}

# Tier thresholds — v2 defaults, tune after backtest.
# Note: LEAN.min_score can be overridden at runtime via ensemble_health
# soft_tighten status (see _current_health_state).
#
# 2026-08-19 retune: post-calibration score distribution over 3d (8/17-8/19)
# was 0 PRIME / 3 STRONG / 29 LEAN / 0 PASS. Top score = 1.05 (TOR RL). Old
# STRONG bar 1.2 was set for pre-calibration signal environment when raw
# scores stacked higher; with calibrated weights (edge_weight scales
# contributions by historical hit_rate above breakeven), the distribution
# compressed and effectively nothing hits STRONG. Retune:
#   PRIME 2.0 → 1.5    (still rare, still requires 3+ classes + 0.5 margin)
#   STRONG 1.2 → 0.7   (14 STRONG on 3d vs 3 before — real tier hierarchy)
#   LEAN 0.5 → 0.3     (12 LEAN + 6 PASS vs 29 LEAN — filters lowest-conviction)
# Class + margin gates unchanged — those enforce breadth-of-agreement,
# threshold retune only reflects the compressed score scale post-calibration.
TIER_THRESHOLDS = {
    'PRIME':  {'min_score': 1.5, 'min_classes': 3, 'min_margin': 0.5},
    'STRONG': {'min_score': 0.7, 'min_classes': 2, 'min_margin': 0.3},
    'LEAN':   {'min_score': 0.3, 'min_classes': 1, 'min_margin': 0.1},
}

_HEALTH_STATE_CACHE: dict = {}


def _current_health_state(sport: str) -> dict:
    """Read latest ensemble_health row for a sport (cached per-run).

    Returns {'status_flag': str, 'lean_threshold_override': float|None,
             'suppressed': bool}. Defaults to healthy when no row present."""
    if sport in _HEALTH_STATE_CACHE:
        return _HEALTH_STATE_CACHE[sport]
    default = {'status_flag': 'healthy', 'lean_threshold_override': None, 'suppressed': False}
    if not _SB:
        _HEALTH_STATE_CACHE[sport] = default
        return default
    try:
        r = requests.get(f'{_SB}/rest/v1/ensemble_health'
                         f'?sport=eq.{sport}&order=computed_date.desc&limit=1'
                         '&select=status_flag,lean_threshold_override,cold_streak_days',
                         headers=_H_READ, timeout=5)
        rows = r.json() if r.status_code == 200 else []
        if not rows:
            _HEALTH_STATE_CACHE[sport] = default
            return default
        row = rows[0]
        state = {
            'status_flag': row.get('status_flag') or 'healthy',
            'lean_threshold_override': row.get('lean_threshold_override'),
            'suppressed': row.get('status_flag') == 'hard_suppress',
        }
        _HEALTH_STATE_CACHE[sport] = state
        return state
    except Exception:
        _HEALTH_STATE_CACHE[sport] = default
        return default

# Class-balance rule: no single class contributes more than 40% of the
# winning candidate's total score. Prevents sharp-only or model-only
# picks from earning tier without diverse evidence.
MAX_CLASS_SHARE = 0.40

# 2026-08-26 aggregate fade cap. Per-class cap alone doesn't restrain
# auto-fade stacks that split across pitcher/weather/offense classes —
# Rockies UNDER 9.0 PRIME 97 had 4 fades collectively at ~62% share,
# each below their own class 40% budget. Cap total fade contribution at
# FADE_MAX_SHARE of adjusted_total per side. See _score_market.
FADE_MAX_SHARE = 0.35

# Handler class markers
HANDLER_CLASSES = {'split', 'scenario', 'external_pick'}


# ═══════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Opinion:
    """One source's opinion on one candidate."""
    signal_key: str           # unique source key
    signal_class: str         # class from signal_sources
    side: str                 # candidate label
    strength: float           # [0, 1]
    hit_rate: Optional[float] # historical hit rate as fraction (0.62 = 62%)
    sample_n: int
    tier: Optional[str]       # VALIDATED/DISCOVERY/UNVALIDATED/ANTI_VALIDATED
    display_prose: str        # reader-friendly narration
    # 2026-08-21: is_recent enables the cold-start ramp-up prior. Signals
    # created within RAMP_UP_DAYS get a competitive weight (0.50) instead
    # of the untested-signal floor (0.20) so newly-shipped analytics can
    # actually influence picks before accumulating 50+ graded observations.
    is_recent: bool = False


@dataclass
class Contribution:
    """One source's weighted contribution to a candidate's score."""
    signal_key: str
    signal_class: str
    side: str
    weight: float
    strength: float
    n: int
    contribution: float
    display_prose: str
    # 2026-08-31: passthrough of source hit_rate so downstream stake-sizing
    # logic (compute_recommended_stake in game_context.py) can gate 2u LOCK
    # promotion on "signal has ≥75% historical bucket on n≥30".
    hit_rate: Optional[float] = None


@dataclass
class MarketDecision:
    """One market's decision (ml, rl, or total)."""
    market: str                # 'ml' | 'rl' | 'total'
    pick: Optional[str]        # candidate label or None (no pick)
    display_label: Optional[str]
    side: Optional[str]        # HOME/AWAY/OVER/UNDER
    line: Optional[float]
    tier: str                  # PRIME/STRONG/LEAN/PASS
    conviction: int            # 0-100
    score: float
    margin: float              # score vs runner-up
    contributions: list = field(default_factory=list)
    class_share: dict = field(default_factory=dict)  # {class_name: total_contribution}
    # 2026-08-21: Top contributions on the runner-up side within THIS market
    # (e.g., AWAY_RL signals when HOME_RL wins the RL market). Powers the
    # "losing_market_notes" chip on game detail so signals that fire on a
    # losing side still surface as context — matches the Rockies ATS_cold
    # scenario where 39.4% season cover pct fires FADE-home-spread but gets
    # outvoted by HOME_RL signals on the same market.
    runner_up_side: Optional[str] = None
    runner_up_contributions: list = field(default_factory=list)

    def prose_signals(self, max_shown: int = 5) -> list[str]:
        """Ordered list of reader-friendly signal quotes Jerry can use."""
        if not self.contributions: return []
        supporting = sorted(
            [c for c in self.contributions if c.side == self.pick and c.contribution > 0],
            key=lambda c: -c.contribution,
        )
        return [c.display_prose for c in supporting[:max_shown] if c.display_prose]


@dataclass
class PerGameDecision:
    """Full ensemble output for one game — three markets + top pick."""
    game_id: str
    sport: str
    home_team: str
    away_team: str
    ml: MarketDecision
    rl: MarketDecision
    total: MarketDecision
    top_market: str            # 'ml' | 'rl' | 'total'

    def top(self) -> MarketDecision:
        return getattr(self, self.top_market)


# ═══════════════════════════════════════════════════════════════════════
# CACHES (per-run) — signal_sources, signal_registry, external_source_track_record
# ═══════════════════════════════════════════════════════════════════════

_SOURCES_CACHE: Optional[list] = None
_REGISTRY_CACHE: Optional[dict] = None
_TRACK_CACHE: Optional[dict] = None


def _load_sources(sport: str) -> list[dict]:
    """Load all enabled signal_sources for a sport (+ universal '*')."""
    global _SOURCES_CACHE
    if _SOURCES_CACHE is None:
        if not _SB:
            _SOURCES_CACHE = []
            return _SOURCES_CACHE
        try:
            r = requests.get(f'{_SB}/rest/v1/signal_sources',
                             headers=_H_READ,
                             params={'select': '*', 'enabled': 'eq.true'},
                             timeout=10)
            _SOURCES_CACHE = r.json() if r.status_code == 200 else []
        except Exception:
            _SOURCES_CACHE = []
    return [s for s in _SOURCES_CACHE if s.get('sport') in (sport, '*')]


# 2026-08-26: max age for a signal_registry row to be considered valid.
# Prevents stale ANTI_VALIDATED verdicts from continuing to auto-fade
# signals long after the graded pattern shifted. Audit finding: weekly-only
# refit + `|| echo non-fatal` on nightly rescore meant tiers could be up to
# 7 days stale. Registry rows older than this cutoff are IGNORED — signal
# falls back to inline hit_rate_pct or the no-registry default.
MAX_REGISTRY_AGE_DAYS = 14


def _load_registry() -> dict:
    """Load signal_registry keyed by (signal_name, sport) tuple.

    2026-08-21 CROSS-SPORT CONTAMINATION FIX: prior version keyed by
    signal_name alone. Many signals exist for multiple sports (e.g.
    away_ats_cold_on_road for MLB AND NHL). Last-loaded wins meant
    MLB scorer often got NHL weights.

    2026-08-26 STALENESS FILTER: drop rows whose last_computed_at is
    older than MAX_REGISTRY_AGE_DAYS. Stale rows produce stale weights.
    """
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_REGISTRY_AGE_DAYS)
    dropped_stale = 0
    out: dict = {}
    if _SB:
        try:
            r = requests.get(f'{_SB}/rest/v1/signal_registry',
                             headers=_H_READ,
                             params={'select': 'signal_name,sport,tier,recommended_weight,'
                                               'hit_rate,sample_n,last_computed_at,updated_at'},
                             timeout=10)
            for row in (r.json() if r.status_code == 200 else []):
                # Staleness filter
                ts_str = row.get('last_computed_at') or row.get('updated_at')
                if ts_str:
                    try:
                        s = str(ts_str).replace('Z', '+00:00')
                        ts = datetime.fromisoformat(s)
                        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                        if ts < cutoff:
                            dropped_stale += 1
                            continue
                    except (ValueError, TypeError):
                        pass
                key = (row['signal_name'], row.get('sport') or '*')
                out[key] = row
        except Exception:
            pass
    if dropped_stale > 0:
        print(f'  [ensemble] dropped {dropped_stale} stale registry rows (>{MAX_REGISTRY_AGE_DAYS}d)')
    _REGISTRY_CACHE = out
    return out


def _load_track_records() -> dict:
    """Load external handicapper track records.

    2026-08-16 bug fix: the table column is `n_picks` (from
    20260731_jerry_synthesis_tables.sql) — earlier code asked for
    `n_graded` which doesn't exist, and the PostgREST 400 silently
    zeroed the whole lookup. Also normalize n from wins+losses+pushes
    so the ensemble can compute proper weights from n_dec = wins+losses.
    """
    global _TRACK_CACHE
    if _TRACK_CACHE is not None:
        return _TRACK_CACHE
    out: dict = {}
    if _SB:
        try:
            r = requests.get(f'{_SB}/rest/v1/external_source_track_record',
                             headers=_H_READ,
                             params={'select': 'source,sport,surface,window_days,hit_rate,n_picks,n_wins,n_losses,n_pushes'},
                             timeout=10)
            for row in (r.json() if r.status_code == 200 else []):
                key = (row['source'], row['sport'], row['surface'])
                prio = {30: 3, 90: 2, 9999: 1}.get(row.get('window_days'), 0)
                # Normalize n_graded (wins + losses, excluding pushes) so
                # downstream weight math is consistent with hit_rate meaning.
                row['n_graded'] = int(row.get('n_wins') or 0) + int(row.get('n_losses') or 0)
                existing = out.get(key)
                if existing is None or prio > existing.get('_prio', 0):
                    row['_prio'] = prio
                    out[key] = row
        except Exception:
            pass
    _TRACK_CACHE = out
    return out


def _is_recent_signal(source_row: dict) -> bool:
    """True when a signal_sources row was created within the ramp-up
    window (RAMP_UP_DAYS). Used by edge_weight to grant a competitive
    prior to freshly-shipped signals while they accumulate a graded
    track record. Robust to missing/malformed created_at."""
    from datetime import datetime, timezone, timedelta
    created_at = source_row.get('created_at')
    if not created_at: return False
    try:
        # PostgREST returns ISO 8601 with Z or +00:00; both handled here.
        s = str(created_at).replace('Z', '+00:00')
        ts = datetime.fromisoformat(s)
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=RAMP_UP_DAYS)
        return ts >= cutoff
    except Exception:
        return False


def _resolve_weight(source_row: dict) -> tuple[Optional[float], int, Optional[str]]:
    """Get (hit_rate as fraction, n, tier) for a signal_sources row.

    Priority (in order):
      1. inline hit_rate_pct/sample_n on the row
      2. signal_registry lookup by (weight_registry_key, sport)
      3. signal_registry lookup by (signal_key, sport)
      4. None (floor weight)

    2026-08-21: sport is now part of the lookup key (was just signal_name).
    Prevents NHL/NFL registry rows from bleeding into MLB score weights.
    """
    inline_hr = source_row.get('hit_rate_pct')
    inline_n = source_row.get('sample_n')
    if inline_hr is not None:
        try: hr = float(inline_hr) / 100.0
        except (TypeError, ValueError): hr = None
        return (hr, int(inline_n or 0), None)

    registry = _load_registry()
    sport = source_row.get('sport') or '*'
    for lookup_key in (source_row.get('weight_registry_key'), source_row.get('signal_key')):
        if not lookup_key: continue
        # 2026-08-26: sport-specific ONLY. The `sport='*'` fallback silently
        # borrowed a different sport's weights when the sport-specific row
        # was missing (audit finding — same class of bug as the 8/21 cross-
        # sport contamination fix but on the fallback path). A missing
        # sport-specific row should mean "no registry data yet," not "use
        # some other sport's numbers."
        reg = registry.get((lookup_key, sport))
        if reg:
            hr = reg.get('hit_rate')
            if hr is not None:
                try: hr = float(hr) / 100.0
                except (TypeError, ValueError): hr = None
            return (hr, int(reg.get('sample_n') or 0), reg.get('tier'))

    return (None, 0, None)


# ═══════════════════════════════════════════════════════════════════════
# WEIGHT COMPUTATION
# ═══════════════════════════════════════════════════════════════════════

# Ramp-up prior for freshly-shipped signals. Sits roughly at the weight a
# mature signal would earn at 58-59% hit rate — competitive with graded
# analytics without dominating. Drops to registry-derived weight once the
# signal ages past RAMP_UP_DAYS (proven or not).
RAMP_UP_PRIOR = 0.50
RAMP_UP_DAYS = 30

# 2026-08-22 SAMPLE FLOOR — signals with tiny historical samples produce
# noise, not evidence. Cleveland @ Colorado tonight showcased the bug:
# `hitters_park_over` (Coors 118) got weight 0.0 because n=12 hr=0.5 fell
# below -110 breakeven (52.4%), silently zeroing the strongest physical
# park factor in MLB. Meanwhile `rockies_under_season_form` (n=11 hr=72.7%)
# got weight 0.54 because a tiny-sample looked like a huge edge.
# Below this threshold, use RAMP_UP_PRIOR instead of the noisy edge
# formula — 25 aligns with the ANTI_VALIDATED flip minimum (only signals
# with real evidence are trusted for flipping).
SAMPLE_MIN_N = 25


def edge_weight(hit_rate: Optional[float], n: int,
                tier: Optional[str] = None,
                is_recent: bool = False) -> float:
    """Translate (hit_rate, n, tier) into weight in [0, 1].

    ANTI_VALIDATED → 0 (fade signal, not evidence).
    Below breakeven → 0.
    Otherwise: linear scale from breakeven to +12pp * sample dampener.

    2026-08-21: is_recent enables the cold-start ramp-up prior. A signal
    added in the last RAMP_UP_DAYS with no registry track record gets
    RAMP_UP_PRIOR (~mature 58% signal weight) instead of the 0.20 floor
    so newly-shipped analytics can actually compete with older signals
    before accumulating a graded sample. After RAMP_UP_DAYS the earned
    (or lack of earned) rate takes over — a fresh signal that fires
    strongly early and turns out bad drops to 0 once registry data
    catches up.

    2026-08-26: use ENSEMBLE_WEIGHT_VERSION=v2 to route to the Bayesian
    posterior implementation (edge_weight_v2) instead. v1 remains the
    default until A/B backtest promotes v2.
    """
    if os.environ.get('ENSEMBLE_WEIGHT_VERSION', 'v1') == 'v2':
        return edge_weight_v2(hit_rate, n, tier, is_recent)
    if tier == 'ANTI_VALIDATED':
        return 0.0
    if hit_rate is None or n <= 0:
        if is_recent:
            return RAMP_UP_PRIOR
        # No proven track record — small floor if registry knows about it
        return 0.20 if tier in ('DISCOVERY', 'UNVALIDATED', 'VALIDATED') else 0.15
    # 2026-08-22: sample floor — n < SAMPLE_MIN_N produces noise, not
    # evidence. Below the threshold, ignore the edge formula and use
    # ramp-up prior instead. See constant docstring for CLE @ COL example.
    if n < SAMPLE_MIN_N:
        return RAMP_UP_PRIOR
    edge_pp = hit_rate - BREAKEVEN
    if edge_pp <= 0:
        return 0.0
    # 2026-08-26 log-compression (audit recommendation). Old:
    #   edge_component = min(edge_pp / 0.12, 1.0)  # linear saturation
    # A single mature signal at 12pp edge could supply 1.0 contribution
    # alone, dominating a pick. New curve retains 0.86 at 12pp and 0.36
    # at 3pp — smaller signals contribute meaningfully, big signals
    # don't singlehandedly win markets.
    edge_component = 1.0 - math.exp(-edge_pp / 0.06)
    n_component = min(math.log1p(n) / math.log(101), 1.0)
    return round(edge_component * n_component, 4)


# 2026-08-26 Bayesian posterior weight — replaces the hand-tuned
# RAMP_UP_PRIOR = 0.50 fallback path that dominated ~73% of signals
# (all UNVALIDATED tier). Under v1, an UNVALIDATED signal with hr=0.35
# n=8 got the same 0.50 weight as a signal with hr=0.65 n=8 — the
# empirical evidence was ignored in favor of a constant.
#
# v2 uses a Beta(alpha_0, beta_0) prior centered on the -110 breakeven
# rate (0.524), updated by observed wins/losses to a posterior mean that
# shrinks toward breakeven as n shrinks and toward the observed rate as
# n grows. Small-sample "lucky" signals get lower weight than they did
# under v1; small-sample "unlucky" signals get demoted to 0 (which was
# the correct answer under v1 too but got masked by the RAMP_UP_PRIOR
# override).
#
# Prior strength = 20 pseudo-samples. Chosen so that a signal with
# n=8 observed still leans heavily on the prior (posterior weight
# fraction is 20/28 = 71% prior); by n=100 the prior weight drops to
# 20/120 = 17%. This matches typical MLB signal maturation curves.
BAYES_PRIOR_STRENGTH = 20
BAYES_PRIOR_MEAN = BREAKEVEN  # 0.524


def edge_weight_v2(hit_rate: Optional[float], n: int,
                   tier: Optional[str] = None,
                   is_recent: bool = False) -> float:
    """Bayesian posterior version of edge_weight.

    Same interface + same ANTI_VALIDATED gate. Different math for the
    (hit_rate is not None) case: posterior mean via Beta prior instead
    of the SAMPLE_MIN_N=25 hard cutoff to RAMP_UP_PRIOR.

    Weight formula:
      posterior_mean = (alpha_0 + wins) / (alpha_0 + beta_0 + n)
        where wins = hit_rate * n; alpha_0 = 0.524 * prior_strength;
        beta_0 = 0.476 * prior_strength.
      edge_pp = posterior_mean - BREAKEVEN
      if edge_pp <= 0: 0
      else: min(edge_pp / 0.12, 1.0) * n_component
        where n_component uses (n + prior_strength) so signals with
        combined evidence >= 100 saturate.
    """
    if tier == 'ANTI_VALIDATED':
        return 0.0
    if hit_rate is None or n <= 0:
        # Same cold-start behavior as v1 — no observations, no update.
        if is_recent:
            return RAMP_UP_PRIOR
        return 0.20 if tier in ('DISCOVERY', 'UNVALIDATED', 'VALIDATED') else 0.15

    # Bayesian posterior mean
    wins = hit_rate * n
    alpha_0 = BAYES_PRIOR_MEAN * BAYES_PRIOR_STRENGTH
    beta_0 = (1 - BAYES_PRIOR_MEAN) * BAYES_PRIOR_STRENGTH
    posterior_mean = (alpha_0 + wins) / (alpha_0 + beta_0 + n)

    edge_pp = posterior_mean - BREAKEVEN
    if edge_pp <= 0:
        return 0.0
    # 2026-08-26: same log-compression as v1 — no single signal
    # dominates via linear saturation.
    edge_component = 1.0 - math.exp(-edge_pp / 0.06)
    # n_component uses effective sample = n + prior_strength so the
    # curve is smooth (no discontinuity at SAMPLE_MIN_N). Signals with
    # 100+ effective sample saturate.
    n_eff = n + BAYES_PRIOR_STRENGTH
    n_component = min(math.log1p(n_eff) / math.log(101 + BAYES_PRIOR_STRENGTH), 1.0)
    return round(edge_component * n_component, 4)


# ═══════════════════════════════════════════════════════════════════════
# HANDLER-CLASS DISPATCH
# ═══════════════════════════════════════════════════════════════════════

def _handler_split(source_row: dict, ctx: dict) -> list[Opinion]:
    """Fetch line_movement_flags for the game and emit one Opinion per
    flag whose classification matches the source_row's signal_key intent
    (triple_confirmed / confirmed / lean)."""
    gid = ctx.get('game_id')
    if not gid or not _SB: return []
    try:
        r = requests.get(f'{_SB}/rest/v1/line_movement_flags',
                         headers=_H_READ,
                         params={'game_id': f'eq.{gid}',
                                 'select': 'market,side,classification,money_pct,bets_pct,handle_pct,bettors_pct'},
                         timeout=8)
        flags = r.json() if r.status_code == 200 else []
    except Exception:
        flags = []
    if not flags: return []

    signal_key = source_row.get('signal_key', '')
    prose_tmpl = source_row.get('display_prose_template') or ''
    hr, n, tier = _resolve_weight(source_row)
    is_recent = _is_recent_signal(source_row)

    out: list[Opinion] = []
    for flag in flags:
        cls = str(flag.get('classification') or '')
        if not cls or cls in ('PATTERN_ONLY', 'NEUTRAL', 'SOURCES_SPLIT'):
            continue
        # 2026-08-31: QUARANTINE — 30d audit showed SHARP_MOVE_CONFIRMED
        # (2-source) losing 42% (14-19) and fade-side winning 57.6%.
        # SHARP_MOVE_LEAN (1-source) losing 34% and fade winning 65.6%.
        # TRIPLE_CONFIRMED still correctly aligned (60% wins). Root cause
        # is one of the split sources having money%/bets% inverted; while
        # investigation is open, drop 2-source and 1-source confidence
        # to 0 so ensemble no longer pushes picks toward the losing side.
        if signal_key == 'sharp_split_confirmed' and '_TRIPLE_CONFIRMED' not in cls:
            continue  # QUARANTINE 2-source SHARP_MOVE_CONFIRMED
        # Match source_row to classification tier
        if signal_key == 'sharp_split_triple_confirmed' and '_TRIPLE_CONFIRMED' not in cls:
            continue

        market = str(flag.get('market') or '').lower()
        flag_side = str(flag.get('side') or '').upper()
        invert = cls.startswith('RLM') or cls.startswith('PUBLIC_MOVE')
        cand = _flag_to_candidate(market, flag_side, invert)
        if not cand: continue

        strength = 0.9 if '_TRIPLE_CONFIRMED' in cls else 0.7 if '_CONFIRMED' in cls else 0.4
        prose = prose_tmpl or f'{cls} on this side'
        out.append(Opinion(
            signal_key=signal_key, signal_class='split', side=cand,
            strength=strength, hit_rate=hr, sample_n=n, tier=tier,
            display_prose=prose, is_recent=is_recent,
        ))
    return out


def _handler_scenario(source_row: dict, ctx: dict) -> list[Opinion]:
    """Sharp scenario matches with per-scenario hit_rate + n from
    sharp_scenario_game_matches.

    2026-08-21 FIX (Braves-ChiSox 8/20 audit): scenarios were triple-counting
    the same underlying public-split pattern. `whale_div15+_under`,
    `bets_55-64_under`, `bets_40-54_under` all fired on the same
    (money>>bets on UNDER) pattern → 0.75 of 0.98 total OVER-side score
    came from 3 correlated derivatives of one signal. Fix: group scenarios
    by (market, family) — one strongest per family. Also n_min gate raised:
    scenarios with n<30 get strength floored to 0.35 (was full)."""
    gid = ctx.get('game_id')
    game_date = ctx.get('game_date')
    if not gid: return []
    try:
        from sharp_scenario_lookup import matches_for_game
        matches = matches_for_game(gid, game_date)
    except Exception:
        matches = []

    def _family(key: str) -> str:
        """Group scenario keys that measure the same underlying pattern.
        Prevents triple-counting (bets_40-54 + bets_55-64 + whale_div15 all
        fire on same public-split moment)."""
        k = key.lower()
        if k.startswith('whale_') or k.startswith('square_'): return 'divergence'
        if k.startswith('bets_'): return 'bets_bucket'
        if k.startswith('money_') and 'mc' not in k: return 'money_bucket'
        if k.startswith('grid_'): return 'money_x_bets_grid'
        if k.startswith('balanced_'): return 'balanced'
        if k.startswith('money65+_mc') or k.startswith('triple_consensus'): return 'money_x_model'
        if k.startswith('rlm_'): return 'rlm'
        return k  # unknown families stay as themselves

    prose_tmpl = source_row.get('display_prose_template') or 'historical pattern hit {hit_rate}% in {sample_n} spots'
    # Build all candidate Opinions first, then dedupe per (market, family)
    per_family: dict[tuple, list[dict]] = {}
    for m in matches:
        side = str(m.get('side') or '').upper()
        market = str(m.get('market') or '').lower()
        bof = m.get('back_or_fade')
        if bof == 'NEUTRAL' or not side: continue
        invert = bof == 'FADE'
        cand = _flag_to_candidate(market, side, invert)
        if not cand: continue
        hr = m.get('hit_rate')
        try: hr = float(hr) / 100.0 if hr is not None else None
        except (TypeError, ValueError): hr = None
        if invert and hr is not None:
            hr = 1.0 - hr
        n = int(m.get('n') or 0)
        confidence = m.get('hint_confidence') or 50
        strength = min(float(confidence) / 100.0, 1.0)
        # 2026-08-21 VERIFIER ROLE: scenarios were dominating 76% of scores
        # (Braves 8/20: 0.75/0.98 from 3 correlated scenario chips).
        # Scenarios now capped hard so they can only CONFIRM other signals,
        # never lead. n>=30: max 0.25. n<30: max 0.15. This drops peak
        # scenario contribution from ~0.36 → ~0.10 per chip.
        if n >= 30:
            strength = min(strength, 0.25)
        else:
            strength = min(strength, 0.15)
        tier = 'DISCOVERY' if n >= 30 else 'UNVALIDATED'
        scenario_key = str(m.get('scenario_key') or 'unnamed')
        # Score for family-dedupe: bigger sample × edge = stronger evidence.
        # Winning scenario in each family is the one with best edge×n.
        edge_pp = (hr - 0.524) if hr is not None else 0.0
        family_score = max(0.0, edge_pp) * min(n, 100)
        per_family.setdefault((market, cand, _family(scenario_key)), []).append({
            'signal_key': f'{source_row["signal_key"]}:{scenario_key}',
            'cand': cand, 'hr': hr, 'n': n, 'strength': strength, 'tier': tier,
            'scenario_key': scenario_key, 'family_score': family_score,
        })

    is_recent = _is_recent_signal(source_row)
    out: list[Opinion] = []
    for (market, cand, family), opinions in per_family.items():
        # Keep the strongest opinion per (market, cand, family)
        top = max(opinions, key=lambda x: x['family_score'])
        scen_ctx = AttrDict({'hit_rate': round((top['hr'] or 0) * 100, 1),
                             'sample_n': top['n'], 'scenario': top['scenario_key']})
        prose = render_prose(prose_tmpl, scen_ctx)
        out.append(Opinion(
            signal_key=top['signal_key'], signal_class='scenario', side=top['cand'],
            strength=top['strength'], hit_rate=top['hr'], sample_n=top['n'],
            tier=top['tier'], display_prose=prose, is_recent=is_recent,
        ))
    return out


_SOURCE_PERSONA = {
    'sbr':        'SBR',
    'tonyspicks': 'TON',
    'pickswise':  'PWS',
    'betfirm':    'BFM',
    'oddscrowd':  'OC',
    'covers':     'COV',
    'action':     'ACT',
    'vsin':       'VSN',
    'dimers':     'DIM',
    'bettingpros':'BTP',
    'docsports':  'DOC',
    'peterson':   'PET',
    'fadereport': 'FR',
    'cleatz':     'CZ',
    'scoresandodds':'SO',
    'so':         'SO',
    'pickdawgz':  'PDZ',
    'bfo':        'BFO',
}


def _persona(src: str) -> str:
    """Map raw handicapper source names to abbrev codes (ToS-scrub
    feedback 8/21). Never surface raw provider names in user prose."""
    return _SOURCE_PERSONA.get((src or '').lower(), (src or '').upper()[:4])


def _handler_external(source_row: dict, ctx: dict) -> list[Opinion]:
    """External handicapper picks for this game, each weighted by that
    handicapper's own track record (external_source_track_record).

    2026-08-26 fade-flip: a source with a demonstrated LOSING record on
    this surface is a contrarian signal, not agreement. When
    hit_rate ≤ 0.35 with n ≥ 10, flip the candidate to the OPPOSITE
    side and weight against fade_hr = 1 − hr. Prevents 0-for-14
    handicappers from propping up our losing side with RAMP_UP_PRIOR
    (0.50) weight when n < SAMPLE_MIN_N (25).

    Triggered by Rockies UNDER PRIME 97 (2026-08-26): sbr 0-22 total
    + tonyspicks 0-14 total both landed on UNDER contributing 0.25 each,
    while MC projected 65% OVER and jerry_pred_total was 12.08 vs
    line 9.0.
    """
    gid = ctx.get('game_id')
    game_date = ctx.get('game_date')
    if not gid or not _SB: return []
    try:
        r = requests.get(f'{_SB}/rest/v1/external_picks',
                         headers=_H_READ,
                         params={'game_id': f'eq.{gid}',
                                 'select': 'source,surface,pick_side,pick_line,fade_flag'},
                         timeout=8)
        picks = r.json() if r.status_code == 200 else []
    except Exception:
        picks = []
    if not picks: return []

    sport = ctx.get('sport') or 'MLB'
    tracks = _load_track_records()
    out: list[Opinion] = []
    for p in picks:
        src = (p.get('source') or '').lower()
        surface = (p.get('surface') or '').lower()
        pick_side = (p.get('pick_side') or '').upper()
        if not src or not surface or not pick_side: continue

        # Convert surface → market
        market = surface if surface in ('ml', 'rl', 'total') else None
        if market is None: continue

        # 2026-08-26 data-quality guard. External sources like SBR store
        # market money% splits ("over 65% / under 35%") as "picks" without
        # a pick_line — the ingest treats the majority side as their pick.
        # These aren't actual handicapper picks: (1) they're bookmaker
        # aggregate data, not opinions; (2) the resolver marks them all
        # as Loss because it can't compare actual to a null line, which
        # then flows into the fade-flip below and drives OPPOSITE-side
        # picks off bad data. Skip totals/RL picks with no pick_line —
        # ML markets don't need a line, so ML passes through.
        if market in ('total', 'rl') and p.get('pick_line') is None:
            continue

        # Fade flag on the source means we invert
        invert = bool(p.get('fade_flag'))
        cand = _flag_to_candidate(market, pick_side, invert)
        if not cand: continue

        # Look up source track record — prefer surface-specific over ALL
        rec = tracks.get((src, sport, surface)) or tracks.get((src, sport, 'ALL'))
        hr = rec.get('hit_rate') if rec else None
        if hr is not None:
            try: hr = float(hr) / 100.0
            except (TypeError, ValueError): hr = None
        n = int(rec.get('n_graded', 0)) if rec else 0
        persona = _persona(src)

        # Fade-flip: known cold source's pick is a contrarian signal
        if hr is not None and hr <= 0.35 and n >= 10:
            flip = {'HOME_ML':'AWAY_ML','AWAY_ML':'HOME_ML',
                    'HOME_RL':'AWAY_RL','AWAY_RL':'HOME_RL',
                    'OVER':'UNDER','UNDER':'OVER'}
            flipped = flip.get(cand)
            if flipped:
                fade_hr = 1.0 - hr
                fade_tier = ('VALIDATED' if fade_hr >= 0.65 and n >= 20
                             else 'DISCOVERY' if fade_hr >= 0.60 and n >= 10
                             else 'UNVALIDATED')
                wins = int(rec.get('n_wins') or 0) if rec else 0
                losses = int(rec.get('n_losses') or 0) if rec else 0
                out.append(Opinion(
                    signal_key=f'external:{src}__fade',
                    signal_class='external_pick', side=flipped, strength=0.5,
                    hit_rate=fade_hr, sample_n=n, tier=fade_tier,
                    display_prose=f'Fade {persona}: {wins}-{losses} on {market.upper()} picks',
                ))
                continue  # emit fade only, skip the losing-side opinion

        tier = 'VALIDATED' if (hr and hr >= 0.57 and n >= 50) \
               else 'DISCOVERY' if (hr and hr >= 0.55 and n >= 20) \
               else 'UNVALIDATED'

        wins = int(rec.get('n_wins') or 0) if rec else 0
        losses = int(rec.get('n_losses') or 0) if rec else 0
        rec_str = f'{wins}-{losses}' if (wins or losses) else f'{n} picks'
        out.append(Opinion(
            signal_key=f'external:{src}',
            signal_class='external_pick', side=cand, strength=0.5,
            hit_rate=hr, sample_n=n, tier=tier,
            display_prose=f'{persona} is on this side ({rec_str} on {market.upper()})',
        ))
    return out


HANDLERS: dict[str, Callable[[dict, dict], list[Opinion]]] = {
    'split': _handler_split,
    'scenario': _handler_scenario,
    'external_pick': _handler_external,
}


def _flag_to_candidate(market: str, side: str, invert: bool = False) -> Optional[str]:
    """Map (market, side, invert) → standardized candidate label."""
    side = side.upper()
    m = market.lower()
    if invert:
        side = {'HOME': 'AWAY', 'AWAY': 'HOME',
                'OVER': 'UNDER', 'UNDER': 'OVER'}.get(side, side)
    if m == 'ml':
        return f'{side}_ML' if side in ('HOME', 'AWAY') else None
    if m in ('rl', 'runline', 'spread'):
        return f'{side}_RL' if side in ('HOME', 'AWAY') else None
    if m == 'total':
        return side if side in ('OVER', 'UNDER') else None
    return None


# ═══════════════════════════════════════════════════════════════════════
# CORE: gather + score
# ═══════════════════════════════════════════════════════════════════════

def _enrich_ctx_for_sport(sport: str, ctx: dict) -> dict:
    """Add sport-specific derived fields before signal evaluation.

    UFC: pull fighter stats from ufc_fighter_stats (SLpM, str_def, td_acc,
    td_def, total_fights) and flatten as ctx.slpm_a, ctx.str_def_a, etc.
    UFC signal_sources rows reference these directly, so this enrichment
    is what lets striking/grappling signals actually fire.
    """
    if sport.upper() != 'UFC' or not _SB:
        return ctx
    fa = ctx.get('fighter_a')
    fb = ctx.get('fighter_b')
    if not fa or not fb:
        return ctx
    enriched = dict(ctx)
    for suffix, name in (('a', fa), ('b', fb)):
        try:
            r = requests.get(f'{_SB}/rest/v1/ufc_fighter_stats',
                             headers=_H_READ,
                             params={'fighter_name': f'ilike.%{name.split(chr(32))[-1]}%',
                                     'select': 'slpm,str_acc,str_def,sapm,td_avg,td_acc,td_def,'
                                               'sub_avg,total_wins,total_losses,wins_by_dec,'
                                               'finishing_rate,reach,height,stance',
                                     'limit': '1'},
                             timeout=5)
            data = r.json() if r.status_code == 200 else []
            if data:
                stats = data[0]
                for k, v in stats.items():
                    enriched[f'{k}_{suffix}'] = v
        except Exception:
            pass
    return enriched


def _fade_consensus_ok(ctx: dict, source: dict, orig_side: str,
                        flipped_side: str, cls: str) -> bool:
    """Consensus check before emitting an auto-fade opinion.

    The auto-fade mechanism flips an ANTI_VALIDATED signal from `orig_side`
    to `flipped_side` on the theory that a historically losing signal is
    actually a contrarian signal. But if Monte Carlo + market money + our
    own runs projection all AGREE with `orig_side` by a meaningful margin,
    the auto-fade is fighting live model consensus. Suppress the flip in
    those cases — the audit called this out as the mechanism that produced
    Rockies UNDER PRIME 97 while jerry_pred=12.15 and MC 70% OVER.

    Returns True if the fade is allowed to emit; False to suppress.

    Rule: block the flip if ≥2 of {MC, OC money%, jerry_pred vs line}
    agree with orig_side by ≥15pp / 1.5 units. If ctx signals are missing
    we allow the flip (backward compatibility for sports where these
    fields aren't populated).

    2026-08-26 EXCEPTION: if a compound signal (jerry_panel_agree_over_fade,
    jerry_proj_agree_home_ml_fade, etc.) is present in the same market
    with a VALIDATED tier from empirical grading, it OVERRIDES the block —
    that pattern historically fades exactly this consensus. Prevents
    ensemble from ignoring proven fade patterns just because live models
    happen to agree with the consensus that historically loses.
    """
    # Compound-fade override: check if any of the seeded fade patterns
    # would fire on this game. Signal_key convention is *_fade — the seed
    # is present in signal_registry with real hit_rate; if hit_rate is
    # >=0.60 with n>=15, respect it as override.
    #
    # We check by looking at ctx flags for jerry+panel agree over,
    # jerry+proj home spread agree, etc. — signals that specifically
    # exist to fade this exact consensus type.
    try:
        market_lower = None
        if orig_side in ('OVER', 'UNDER'): market_lower = 'total'
        elif orig_side in ('HOME_ML', 'AWAY_ML'): market_lower = 'ml'
        elif orig_side in ('HOME_RL', 'AWAY_RL'): market_lower = 'rl'

        if market_lower == 'total' and orig_side == 'OVER':
            # Any of jerry+panel, jerry+panel+MC over-agreement means
            # a proven fade signal wants UNDER. Let the fade emit.
            ct = ctx.get('close_total')
            jp = ctx.get('jerry_pred_total')
            pp = ctx.get('panel_implied_total')
            if ct is not None and jp is not None and pp is not None:
                try:
                    if (float(jp) - float(ct) > 0.5 and
                            float(pp) - float(ct) > 0.5):
                        return True  # compound fade active, allow flip
                except (TypeError, ValueError):
                    pass
        if market_lower == 'ml' and orig_side == 'HOME_ML':
            js = ctx.get('jerry_pred_spread'); ps = ctx.get('projected_spread')
            cs = ctx.get('close_spread')
            if js is not None and ps is not None and cs is not None:
                try:
                    if ((float(js) + float(cs)) > 0.5 and
                            (float(ps) + float(cs)) > 0.5):
                        return True  # jerry_proj home ML consensus historically fades
                except (TypeError, ValueError):
                    pass
        if market_lower == 'rl' and orig_side == 'HOME_RL':
            js = ctx.get('jerry_pred_spread'); ps = ctx.get('projected_spread')
            cs = ctx.get('close_spread')
            if js is not None and ps is not None and cs is not None:
                try:
                    if ((float(js) + float(cs)) > 0.5 and
                            (float(ps) + float(cs)) > 0.5):
                        return True  # jerry_proj home RL consensus historically fades
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    market = None
    if orig_side in ('OVER', 'UNDER'): market = 'total'
    elif orig_side in ('HOME_ML', 'AWAY_ML'): market = 'ml'
    elif orig_side in ('HOME_RL', 'AWAY_RL'): market = 'rl'
    if not market:
        return True  # unknown market, allow

    votes_for_orig = 0
    votes_checked = 0

    # 1) Monte Carlo agreement with orig_side
    mc = ctx.get('mc_probabilities') if isinstance(ctx.get('mc_probabilities'), dict) else None
    if mc:
        try:
            if market == 'total':
                p = mc.get('mc_p_over') if orig_side == 'OVER' else mc.get('mc_p_under')
                if p is not None:
                    votes_checked += 1
                    if float(p) >= 0.575:  # +15pp over 42.5% breakeven for MC probability
                        votes_for_orig += 1
            elif market == 'ml':
                p = mc.get('mc_p_home_win') if orig_side == 'HOME_ML' else mc.get('mc_p_away_win')
                if p is not None:
                    votes_checked += 1
                    if float(p) >= 0.575:
                        votes_for_orig += 1
        except (TypeError, ValueError):
            pass

    # 2) OddsCrowd money% agreement with orig_side
    oc = ctx.get('oddscrowd_snapshot') if isinstance(ctx.get('oddscrowd_snapshot'), dict) else None
    if oc:
        seg = oc.get(market) if isinstance(oc.get(market), dict) else None
        if seg and seg.get('pick'):
            try:
                oc_side_raw = str(seg.get('pick', '')).upper()
                oc_money = float(seg.get('money') or 0)
                oc_matches_orig = (
                    (market == 'total' and oc_side_raw == orig_side) or
                    (market in ('ml', 'rl') and (
                        (oc_side_raw == 'HOME' and orig_side.startswith('HOME')) or
                        (oc_side_raw == 'AWAY' and orig_side.startswith('AWAY'))
                    ))
                )
                if oc_matches_orig:
                    votes_checked += 1
                    if oc_money >= 65:  # sharp/public 65%+ on orig side
                        votes_for_orig += 1
                else:
                    votes_checked += 1  # OC votes for flipped, count against
            except (TypeError, ValueError):
                pass

    # 3) Jerry projected total vs close_total (for total market only)
    if market == 'total':
        try:
            jpred = ctx.get('jerry_pred_total')
            cline = ctx.get('close_total')
            if jpred is not None and cline is not None:
                diff = float(jpred) - float(cline)
                votes_checked += 1
                if orig_side == 'OVER' and diff >= 1.5:
                    votes_for_orig += 1
                elif orig_side == 'UNDER' and diff <= -1.5:
                    votes_for_orig += 1
        except (TypeError, ValueError):
            pass

    # Block the flip when ≥2 signals side with orig_side. Requires at
    # least 2 votes checked so single-signal cases don't false-positive.
    if votes_checked >= 2 and votes_for_orig >= 2:
        return False
    return True


def gather_opinions(sport: str, ctx: dict) -> list[Opinion]:
    """Iterate all enabled signal_sources for the sport, evaluate each
    against ctx (or dispatch to handler), return every Opinion emitted."""
    sources = _load_sources(sport)
    if not sources:
        return []
    # Enrich ctx with sport-specific derived fields (fighter stats for UFC etc.)
    ctx = _enrich_ctx_for_sport(sport, ctx)
    ctx_attr = AttrDict(ctx)
    out: list[Opinion] = []

    for source in sources:
        cls = source.get('class', '')
        # Handler-based
        if cls in HANDLERS:
            try:
                out.extend(HANDLERS[cls](source, ctx))
            except Exception:
                pass
            continue

        # Expression-based
        condition = source.get('condition_expr', '')
        if not evaluate_bool(condition, ctx_attr):
            continue

        side = evaluate_str(source.get('side_expr', ''), ctx_attr)
        if not side:
            continue

        strength = evaluate_float(source.get('strength_expr', '0.5'), ctx_attr, default=0.5)
        if strength <= 0:
            continue

        hr, n, tier = _resolve_weight(source)
        is_recent = _is_recent_signal(source)
        prose = render_prose(source.get('display_prose_template') or '', ctx_attr)

        # 2026-08-20: ANTI_VALIDATED FADE mode. Signals with proven fade
        # edge (hr well below breakeven with meaningful n) are inverted
        # instead of being zeroed out — we bet the OTHER side. Example:
        # home_ats_hot_at_home fires on side=HOME_RL with hr=37.3% n=59.
        # Prior behavior: edge_weight returned 0, signal contributed
        # nothing. Now: flip side to AWAY_RL, use fade_hr = 1 - hr = 62.7%,
        # tier promoted to VALIDATED so edge_weight assigns real weight.
        # This exploits real edges the ensemble was silently discarding.
        # Guard: only flip when n >= 25 AND hr <= 0.47 — thin-sample fades
        # aren't reliable enough to bet, and marginal negatives (48-52%)
        # aren't clear edges.
        #
        # 2026-08-23 double-fade guard. Surfaced by user's audit of tonight's
        # PRIME MLs: h2h_home_dominant_fade__fade was firing HOME_ML +0.14
        # on Marlins/Dodgers. Chain was:
        #   1. base h2h_home_dominant (HOME_ML)
        #   2. manual h2h_home_dominant_fade (AWAY_ML, already the correct
        #      contrarian bet, registered ANTI_VALIDATED @ 45%)
        #   3. auto-fade sees (2) is ANTI → creates *_fade__fade (HOME_ML)
        # Result: the fade of a fade lands back on the ORIGINAL side, silently
        # propping up chalk conviction. Fix: skip auto-fade for signals whose
        # key already ends in '_fade' (or '__fade'). The manual fade was
        # intentional; if it too is ANTI_VALIDATED, edge_weight returns 0
        # (line 420) and the opinion contributes nothing — which is the right
        # answer for a signal that has no edge in either direction.
        _sk = source.get('signal_key', '') or ''
        if _sk.endswith('_fade') or _sk.endswith('__fade'):
            pass  # don't auto-fade a fade — falls through to zero-weight emit
        elif tier == 'ANTI_VALIDATED' and hr is not None and n >= 25 and hr <= 0.47:
            # 2026-08-26 UFC gap fix: fight market was missing from flip_map,
            # so every ANTI signal on a UFC card silently emitted zero-weight
            # instead of flipping to the other fighter.
            flip_map = {'HOME_ML':'AWAY_ML','AWAY_ML':'HOME_ML',
                        'HOME_RL':'AWAY_RL','AWAY_RL':'HOME_RL',
                        'OVER':'UNDER','UNDER':'OVER',
                        'FIGHTER_A_ML':'FIGHTER_B_ML','FIGHTER_B_ML':'FIGHTER_A_ML'}
            flipped_side = flip_map.get(side)
            if flipped_side is None:
                # New market label added to CANDIDATES_BY_MARKET without
                # updating flip_map. Log so this can't recur silently.
                print(f'⚠ auto-fade: unrecognized side {side!r} for signal '
                      f'{source.get("signal_key")} — flip_map needs updating')
            if flipped_side and _fade_consensus_ok(ctx, source, side, flipped_side, cls):
                fade_hr = 1.0 - hr
                # Promote to VALIDATED if fade edge is meaningful (>= 55%)
                # AND sample is decent, else DISCOVERY.
                fade_tier = 'VALIDATED' if (fade_hr >= 0.55 and n >= 50) else 'DISCOVERY'
                raw = prose or source["signal_key"]
                if raw and raw[0].isalpha() and raw[0].islower():
                    raw = raw[0].upper() + raw[1:]
                fade_prose = f'Fade: {raw}'
                out.append(Opinion(
                    signal_key=f'{source["signal_key"]}__fade',
                    signal_class=cls,
                    side=flipped_side, strength=strength,
                    hit_rate=fade_hr, sample_n=n, tier=fade_tier,
                    display_prose=fade_prose, is_recent=is_recent,
                ))
                continue  # skip emitting the original (zero-weight) opinion

        out.append(Opinion(
            signal_key=source['signal_key'],
            signal_class=cls,
            side=side, strength=strength,
            hit_rate=hr, sample_n=n, tier=tier,
            display_prose=prose or source['signal_key'],
            is_recent=is_recent,
        ))
    return out


def _score_market(market: str, opinions: list[Opinion], ctx: dict,
                   lean_override: Optional[float] = None) -> MarketDecision:
    """Score one market (ml/rl/total) from the opinion pool.
    Filters opinions to those relevant to this market's candidates.

    lean_override: when set, raises the LEAN min_score threshold. Used
    by soft_tighten status when ensemble is on a cold streak."""
    candidates = CANDIDATES_BY_MARKET[market]
    market_ops = [op for op in opinions if op.side in candidates]

    if not market_ops:
        return _no_pick(market, ctx)

    # Aggregate per candidate
    per_side: dict[str, list[Contribution]] = defaultdict(list)
    for op in market_ops:
        w = edge_weight(op.hit_rate, op.sample_n, op.tier, is_recent=op.is_recent)
        if w == 0 and op.strength == 0:
            continue
        c = Contribution(
            signal_key=op.signal_key, signal_class=op.signal_class,
            side=op.side, weight=w, strength=op.strength, n=op.sample_n,
            contribution=round(w * op.strength, 4),
            display_prose=op.display_prose,
            hit_rate=op.hit_rate,  # 2026-08-31: for 2u LOCK stake gate
        )
        per_side[op.side].append(c)

    if not per_side:
        return _no_pick(market, ctx)

    # 2026-09-03 INTRA-CLASS FAMILY DEDUP — signals measuring the SAME
    # underlying info shouldn't be counted as independent votes. Root
    # cause of NCAAF 93% dog bias: SP+/model-based spread signals
    # (ncaaf_projected_spread_rl, ncaaf_home_spread_edge,
    #  ncaaf_sp_plus_edge_home/away_rl) all fire together on the same
    # side (SP+ projects tighter spreads → dog wins all 3). Counted as
    # 0.55+ contribution when really it's ~0.20 of unique info.
    #
    # Dedup: within each side, group by family (heuristic pattern-match
    # on signal_key), keep only the highest-contribution chip per family.
    # Other families still contribute independently.
    _FAMILY_PATTERNS = [
        # NCAAF SP+/projected-spread family — same underlying info
        ('sp_edge',      lambda k: 'spread_edge' in k or 'sp_plus_edge' in k or 'projected_spread' in k),
        # H2H family — head-to-head trends often correlate across metrics
        ('h2h',          lambda k: k.startswith('h2h_')),
        # Team-form-season (season-long ATS/OU trends)
        ('team_season',  lambda k: 'season' in k and ('ats' in k or 'over_trend' in k or 'under_trend' in k)),
        # Team-form recent (L10 ATS/OU)
        ('team_recent',  lambda k: any(t in k for t in ['ats_hot','ats_cold','over_trend','under_trend','ml_hot','ml_cold']) and 'season' not in k),
    ]
    def _fam(sig_key: str) -> str:
        k = (sig_key or '').lower()
        for name, matcher in _FAMILY_PATTERNS:
            if matcher(k): return name
        return 'unique_' + k  # unique family per non-matched signal

    for cand, chips in list(per_side.items()):
        by_fam: dict[str, list[Contribution]] = defaultdict(list)
        for c in chips:
            by_fam[_fam(c.signal_key)].append(c)
        # Keep only the highest-contribution chip per family; discard the rest
        deduped: list[Contribution] = []
        for fam_name, fam_chips in by_fam.items():
            if len(fam_chips) == 1:
                deduped.extend(fam_chips); continue
            # Multiple chips in the same family — keep top-contribution one
            top = max(fam_chips, key=lambda x: x.contribution)
            deduped.append(top)
        per_side[cand] = deduped

    # Sum per candidate + apply class-balance
    scored: list[tuple[str, float, list[Contribution], dict, int]] = []
    for cand in candidates:
        chips = per_side.get(cand, [])
        raw_total = sum(c.contribution for c in chips)
        class_share: dict[str, float] = defaultdict(float)
        for c in chips:
            class_share[c.signal_class] += c.contribution
        # Class-balance penalty: cap any single class at MAX_CLASS_SHARE of total
        # 2026-08-21: changed from soft (overflow*0.5) to HARD cap.
        # 2026-08-23: fixed-point cap. Prior version computed
        #   max_allowed = raw_total * 0.40
        # and subtracted the overflow. But because the overflow subtraction
        # ALSO shrinks the total, the capped class ended up ~56% of the new
        # adjusted_total — the 40% share never actually held.
        #
        # Surfaced by user's audit of tonight's PRIME MLs: cohort trio
        # (confluence_home_lean +0.83, sharp_confluence_alignment +0.59,
        # confluence_home_lean_as_fav +0.30 = +1.72) was contributing 55-90%
        # of every home-chalk pick's score despite class='cohort' being
        # nominally 40%-capped. Marlins/Dodgers/Red Sox PRIMEs were basically
        # "home team + cohort" without matchup edge.
        #
        # True cap: enforce cohort_effective / adjusted_total <= 0.40.
        # Algebra:
        #   cohort_effective <= 0.40 * (cohort_effective + others)
        #   cohort_effective * 0.60 <= 0.40 * others
        #   cohort_effective <= (0.40 / 0.60) * others = (2/3) * others
        # So the correct max_allowed_for_a_class is
        #   share_cap = (MAX_CLASS_SHARE / (1 - MAX_CLASS_SHARE)) * others_sum
        # For cohort=1.72, others=0.79 the new cap = 0.79 * (2/3) = 0.53,
        # dropping Marlins ML adjusted score from ~1.79 to ~1.32 — no longer
        # PRIME on cohort alone. Games with real distributed matchup edge
        # (Padres: cohort 0.42 + team_form 0.53 + offense 0.15 + model 0.13
        # = 1.23) barely move because their cohort share was already <40%.
        # 2026-08-26 multi-class cap math fix (audit finding).
        # Prior bug: iterated all classes computing `others_sum = raw_total
        # - share`, then subtracted overflow. If two classes each exceeded
        # the cap, they both used the ORIGINAL raw_total to compute
        # `others_sum`, so both settled at ~44% instead of the intended 40%.
        #
        # Fix: sort classes DESCENDING by share, iterate with a running
        # `working_total` and `working_class_share` that decreases as we cap
        # each over-cap class. Each class's `others_sum` reflects the
        # post-caps state.
        adjusted_total = raw_total
        # 2026-08-27 SCALE INDIVIDUAL CHIPS. Prior version only reduced
        # `adjusted_total` (a scalar) but left `chips[i].contribution`
        # untouched. So conviction/tier was correctly capped, but the
        # user-visible `_ensemble_sources` breakdown still showed the raw
        # (over-cap) contribution values. Signal audits then reported
        # "cohort 87.7% share" on a game that was already capped down —
        # confusing at best, and it left the SIDE-vote arithmetic in
        # per-candidate scoring using raw values too.
        # New: when a class is capped, scale each chip in that class by
        # (max_allowed / original_class_share) so downstream side-votes
        # AND audit displays both reflect effective contribution.
        chip_scale_factors: dict[str, float] = {}  # class_name -> scale
        if raw_total > 0:
            cap_ratio = MAX_CLASS_SHARE / (1.0 - MAX_CLASS_SHARE)
            working_class_share = dict(class_share)
            working_total = raw_total
            for cls_name in sorted(working_class_share.keys(),
                                   key=lambda k: -working_class_share[k]):
                share = working_class_share[cls_name]
                others_sum = working_total - share
                if others_sum <= 0:
                    max_allowed_effective = working_total * MAX_CLASS_SHARE
                else:
                    max_allowed_effective = others_sum * cap_ratio
                if share > max_allowed_effective:
                    overflow = share - max_allowed_effective
                    adjusted_total -= overflow
                    working_total -= overflow
                    working_class_share[cls_name] = max_allowed_effective
                    if share > 0:
                        chip_scale_factors[cls_name] = max_allowed_effective / share

        if chip_scale_factors:
            for c in chips:
                factor = chip_scale_factors.get(c.signal_class)
                if factor is not None:
                    c.contribution = round(c.contribution * factor, 4)
                    c.weight = round(c.weight * factor, 4)

        # 2026-08-26 aggregate FADE cap. Per-class cap doesn't restrain
        # a stack of auto-fades that split across classes (Rockies UNDER
        # PRIME 97 had 4 fades across pitcher/weather/offense — each got
        # its own 40% budget, collectively they dominated). Enforce that
        # the sum of __fade contributions across all classes stays under
        # FADE_MAX_SHARE of adjusted_total. Same algebra as per-class:
        #   fade_effective <= FMS * (fade_effective + others)
        #   fade_effective <= (FMS / (1-FMS)) * others_sum
        fade_share = sum(c.contribution for c in chips
                         if c.signal_key.endswith('__fade') or c.signal_key.endswith('_fade'))
        if fade_share > 0 and adjusted_total > 0:
            fade_cap_ratio = FADE_MAX_SHARE / (1.0 - FADE_MAX_SHARE)
            others_sum = adjusted_total - fade_share
            if others_sum > 0:
                max_fade_effective = others_sum * fade_cap_ratio
                if fade_share > max_fade_effective:
                    adjusted_total -= (fade_share - max_fade_effective)

        classes_fired = len([c for c in class_share.keys() if class_share[c] > 0])
        scored.append((cand, adjusted_total, chips, dict(class_share), classes_fired))

    scored.sort(key=lambda t: -t[1])
    winner_cand, win_score, win_chips, win_shares, win_classes = scored[0]
    runner_score = scored[1][1] if len(scored) > 1 else 0.0
    margin = win_score - runner_score
    # 2026-08-21: capture runner-up side + its top 3 contribs for
    # losing_market_notes surface (Rockies ATS_cold-style signals that
    # fire on the losing side of a market and were previously invisible).
    runner_side = scored[1][0] if len(scored) > 1 else None
    runner_chips = sorted(scored[1][2], key=lambda c: -c.contribution)[:3] if len(scored) > 1 else []

    # 2026-08-17: LEAN floor gate REMOVED. Ensemble must publish an opinion
    # on every game (Jerry-picks-every-game architecture — see
    # [[project-jerry-vs-sharp-card-817]]). Sharp Card filters PRIME/STRONG
    # only downstream. Fallback to legacy compute_primary_play only fires
    # when the opinion pool is literally empty (per_side dict was empty
    # → returned _no_pick above), NOT because signal strength was weak.
    # lean_override kept as informational (no longer gates PASS).
    _ = lean_override  # unused post-8/17 but preserved for future tightening

    # Tier assignment: must clear score, class count, AND margin
    tier = 'LEAN'
    for candidate_tier in ('PRIME', 'STRONG'):
        th = TIER_THRESHOLDS[candidate_tier]
        if (win_score >= th['min_score']
                and win_classes >= th['min_classes']
                and margin >= th['min_margin']):
            tier = candidate_tier
            break

    # 2026-08-19: PRIME breadth lane. Traditional PRIME requires score≥1.5
    # (rare — today's Toronto pick at 1.39/1.04/9-sources is just shy).
    # Second lane: a pick with 5+ independent source classes agreeing AND
    # margin ≥ 0.7 also earns PRIME even if score is 1.2-1.5. Rewards
    # broad multi-source confluence, not just single-source magnitude.
    # This is the "solid pick with solid foundation of data" upgrade —
    # user asked ensemble to signal high conviction when it's earned.
    if tier == 'STRONG' and win_score >= 1.2 and win_classes >= 5 and margin >= 0.7:
        tier = 'PRIME'

    # 2026-08-19: conviction rewritten to widen distribution + weight
    # margin/class-confluence. Prior formula was `50 + win_score*12` capped
    # at 95, which pinned nearly every pick into 52-62. User feedback:
    # "if we are listing the majority of plays at 55ish what are we doing…
    # looks cheap, feel like we never have conviction." Under the new
    # formula, a decisive multi-source pick (score 1.5, margin 1.0,
    # 6 classes) now scores ~88 instead of 68, while marginal picks
    # (score 0.2, margin 0.1, 2 classes) stay near 54 as they should.
    #   base:          raw win-score signal    →  50-80
    #   margin_boost:  how decisive the win    →  +0-15
    #   classes_boost: how many source classes →  +0-10
    base = 50 + min(win_score * 10, 30)
    margin_boost = min(max(margin, 0) * 20, 15)
    classes_boost = min(max(0, win_classes - 2) * 2, 10)
    conviction = int(round(base + margin_boost + classes_boost))
    conviction = max(45, min(97, conviction))

    display_label, side, line = _label_from_candidate(winner_cand, ctx)

    return MarketDecision(
        market=market,
        pick=winner_cand, display_label=display_label,
        side=side, line=line,
        tier=tier, conviction=conviction,
        score=round(win_score, 2), margin=round(margin, 2),
        contributions=sorted(win_chips, key=lambda c: -c.contribution),
        class_share=win_shares,
        runner_up_side=runner_side,
        runner_up_contributions=runner_chips,
    )


def _no_pick(market: str, ctx: dict) -> MarketDecision:
    return MarketDecision(
        market=market, pick=None, display_label=None,
        side=None, line=None,
        tier='PASS', conviction=50, score=0.0, margin=0.0,
    )


def _label_from_candidate(candidate: str, ctx: dict) -> tuple[str, Optional[str], Optional[float]]:
    home = ctx.get('home_team') or 'HOME'
    away = ctx.get('away_team') or 'AWAY'
    close_spread = ctx.get('close_spread')
    close_total = ctx.get('close_total')

    # UFC / MMA fight candidates
    if candidate == 'FIGHTER_A_ML':
        return (f'{ctx.get("fighter_a") or "Fighter A"} ML', 'A', None)
    if candidate == 'FIGHTER_B_ML':
        return (f'{ctx.get("fighter_b") or "Fighter B"} ML', 'B', None)

    if candidate == 'HOME_ML':
        return (f'{home} ML', 'HOME', None)
    if candidate == 'AWAY_ML':
        return (f'{away} ML', 'AWAY', None)
    if candidate == 'HOME_RL':
        try: line = float(close_spread)
        except (TypeError, ValueError): line = None
        return (f'{home} {line:+g}' if line is not None else f'{home} RL', 'HOME', line)
    if candidate == 'AWAY_RL':
        try: line = -float(close_spread)
        except (TypeError, ValueError): line = None
        return (f'{away} {line:+g}' if line is not None else f'{away} RL', 'AWAY', line)
    if candidate == 'OVER':
        try: line = float(close_total)
        except (TypeError, ValueError): line = None
        return (f'Over {line}' if line is not None else 'Over', 'OVER', line)
    if candidate == 'UNDER':
        try: line = float(close_total)
        except (TypeError, ValueError): line = None
        return (f'Under {line}' if line is not None else 'Under', 'UNDER', line)
    return (candidate, None, None)


# ═══════════════════════════════════════════════════════════════════════
# TOP-LEVEL: score a whole game across all 3 markets
# ═══════════════════════════════════════════════════════════════════════

def score_game(sport: str, ctx: dict) -> PerGameDecision:
    """Score a game across ML, RL, and Total. Returns three MarketDecisions
    + a top_market pointer to the highest-conviction one.

    2026-08-16: reads ensemble_health for the sport. If status is
    'hard_suppress' (rolling ROI has been negative 10+ days), returns
    None to trigger the legacy fallback in the game_context caller.
    If 'soft_tighten', passes a raised LEAN threshold to _score_market."""
    # 2026-08-27 CTX NORMALIZATION. When close_total / close_spread are
    # NULL (line hasn't closed yet or Odds API returned partial data —
    # MIL/NYM 8/27 had current_total=7.0 but close_total=NULL for the
    # whole slate), fall back to current_total / current_spread so
    # ensemble scoring still works. Otherwise every model signal that
    # depends on close_total returns 0 because the condition_expr
    # can't evaluate. Doesn't mutate ctx globally — we make a shallow
    # copy so the caller's dict stays as-is.
    _ctx_needs_copy = True
    if ctx.get('close_total') is None and ctx.get('current_total') is not None:
        ctx = dict(ctx); _ctx_needs_copy = False
        ctx['close_total'] = ctx['current_total']
    if ctx.get('close_spread') is None and ctx.get('current_spread') is not None:
        if _ctx_needs_copy:
            ctx = dict(ctx); _ctx_needs_copy = False
        ctx['close_spread'] = ctx['current_spread']

    health = _current_health_state(sport)
    if health.get('suppressed'):
        return None  # caller falls back to legacy compute_primary_play

    lean_override = health.get('lean_threshold_override')

    opinions = gather_opinions(sport, ctx)

    # Score only markets applicable to this sport (UFC = fight only, etc.)
    markets = MARKETS_BY_SPORT.get(sport.upper(), ['ml', 'rl', 'total'])
    ml_dec = _score_market('ml', opinions, ctx, lean_override=lean_override) if 'ml' in markets else _no_pick('ml', ctx)
    rl_dec = _score_market('rl', opinions, ctx, lean_override=lean_override) if 'rl' in markets else _no_pick('rl', ctx)
    total_dec = _score_market('total', opinions, ctx, lean_override=lean_override) if 'total' in markets else _no_pick('total', ctx)
    # Combat sports: fight-market decision replaces ML
    if 'fight' in markets:
        ml_dec = _score_market('fight', opinions, ctx, lean_override=lean_override)
        # Convert fight to 'ml' shape for downstream since app reads primary_play.type=='ml' universally
        ml_dec.market = 'ml'

    # Determine top market (highest conviction with a pick)
    picks = [(m, d) for m, d in [('ml', ml_dec), ('rl', rl_dec), ('total', total_dec)]
             if d.pick is not None]
    if picks:
        top_market = max(picks, key=lambda p: p[1].conviction)[0]
    else:
        top_market = 'total'  # arbitrary default when all pass

    return PerGameDecision(
        game_id=ctx.get('game_id', ''),
        sport=sport,
        home_team=ctx.get('home_team', ''),
        away_team=ctx.get('away_team', ''),
        ml=ml_dec, rl=rl_dec, total=total_dec,
        top_market=top_market,
    )


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def _fmt_market(md: MarketDecision) -> str:
    if md.pick is None:
        return f'  {md.market.upper():<5} PASS'
    return (f'  {md.market.upper():<5} {md.display_label:<25} '
            f'[{md.tier:<6} conv={md.conviction} score={md.score:.2f} margin={md.margin:+.2f}]')


def _fmt_decision(d: PerGameDecision, verbose: bool = False) -> str:
    lines = [f'{d.away_team} @ {d.home_team}']
    lines.append(_fmt_market(d.ml))
    lines.append(_fmt_market(d.rl))
    lines.append(_fmt_market(d.total))
    top = d.top()
    if top.pick:
        lines.append(f'  TOP:  {top.market.upper()} - {top.display_label}  ({top.tier}, conv={top.conviction})')
    if verbose:
        for market_name in ['ml', 'rl', 'total']:
            md = getattr(d, market_name)
            if md.pick is None: continue
            lines.append(f'  --- {market_name.upper()} contributions ---')
            for c in md.contributions[:6]:
                lines.append(f'    {c.signal_key:<40} [{c.signal_class:<12}] {c.side:<10} '
                             f'w={c.weight:.2f} n={c.n:<4} contrib={c.contribution:+.2f}')
                if c.display_prose:
                    lines.append(f'      "{c.display_prose}"')
            if md.class_share:
                lines.append(f'  class share: {", ".join(f"{k}={v:.2f}" for k,v in md.class_share.items() if v>0)}')
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB')
    p.add_argument('--date', default=date.today().isoformat())
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--game-id', default=None)
    p.add_argument('--verbose', action='store_true')
    args = p.parse_args()

    table = 'mlb_game_context' if args.sport == 'MLB' else f'{args.sport.lower()}_game_context'
    params = {'game_date': f'eq.{args.date}', 'select': '*'}
    if args.game_id:
        params['game_id'] = f'eq.{args.game_id}'
    r = requests.get(f'{_SB}/rest/v1/{table}', headers=_H_READ, params=params, timeout=20)
    rows = r.json() if r.status_code == 200 else []
    if args.limit: rows = rows[:args.limit]

    print(f'=== ensemble_scorer v2 · {args.sport} · {args.date} · {len(rows)} games ===\n')
    for ctx in rows:
        d = score_game(args.sport, ctx)
        print(_fmt_decision(d, verbose=args.verbose))
        print()


if __name__ == '__main__':
    main()
