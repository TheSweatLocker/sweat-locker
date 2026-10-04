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


# ══ 2026-09-29 · MARKETS BARRED FROM BEING THE PUBLISHED TOP PICK ══
# Module-level so there is ONE definition. score_game reads it when choosing
# top_market, and pick_lock.preserve_published reads it so a pick published
# BEFORE a market was barred does not get restored forever — which is exactly
# what happened: 8 of 67 NCAAF rows kept a total pick through a full rebuild
# because the publish lock handed the old one back every run.
#
# Football totals measured 16-32 (33.3%, n=48, z=-2.65). Barred, not unscored —
# the market decision is still computed and stored for the shadow record.
TOP_PICK_BARRED = {'NFL': {'total'}, 'NCAAF': {'total'}}

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
    # 2026-09-03: passthrough of registry tier for signal-quality gate
    # (West Georgia +24.5 was a LEAN pick supported only by a rule with
    # tier=UNVALIDATED, sample_n=1 — that's noise, not signal).
    tier: Optional[str] = None


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


_PAGE = 1000


def fetch_all_rows(path: str, params: dict, page: int = _PAGE) -> list[dict]:
    """GET every row, not the first 1000.

    2026-09-24. Both scorers loaded signal_sources and signal_registry with
    a bare GET. PostgREST answers at most 1000 rows and says so only in a
    Content-Range header nobody read, so the 1195-row registry arrived as
    1000 rows and 195 calibrations were simply absent. A signal whose
    registry row is absent falls back to the no-record prior, so the
    truncation did not error or warn — it silently repriced signals:

      ncaaf_fbs_vs_fcs_early_season  63.8% n=69 VALIDATED   0.783 -> 0.150
      home_pitcher_owns_opp          46.2% n=26 ANTI        0.000 -> 0.150

    Proven signals ran at a fifth of their earned weight while a signal
    measured to be actively wrong got un-muted. 36 enabled signals were
    affected across MLB, NFL and NCAAF.

    Ordered by id so paging is stable — without ORDER BY, PostgREST may
    return overlapping or skipped rows across offsets.
    """
    out: list[dict] = []
    offset = 0
    while True:
        r = requests.get(f'{_SB}/rest/v1/{path}', headers=_H_READ,
                         params={**params, 'order': 'id.asc',
                                 'limit': page, 'offset': offset},
                         timeout=20)
        if r.status_code != 200:
            # Surface the failure instead of returning a short list that
            # reads as "this is all there is".
            print(f'  [ensemble] {path} page at offset {offset} failed '
                  f'{r.status_code}: {(r.text or "")[:120]}')
            return out
        batch = r.json()
        out.extend(batch)
        if len(batch) < page:
            return out
        offset += page


def _load_sources(sport: str) -> list[dict]:
    """Load all enabled signal_sources for a sport (+ universal '*')."""
    global _SOURCES_CACHE
    if _SOURCES_CACHE is None:
        if not _SB:
            _SOURCES_CACHE = []
            return _SOURCES_CACHE
        try:
            _SOURCES_CACHE = fetch_all_rows(
                'signal_sources', {'select': '*', 'enabled': 'eq.true'})
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

    2026-09-24 TRUNCATION: this used a bare GET and so saw only the first
    1000 of 1195 rows. See fetch_all_rows for what that cost.

    2026-09-24 SCOPE COLLISION: the 2026-08-21 note above fixed exactly
    this bug one dimension up — keying by signal_name alone let NHL weights
    land on MLB signals. The same "last-loaded wins" remains at
    market_scope, which is part of the table's real unique key
    (signal_name, sport, market_scope). 78 name+sport pairs hold more than
    one row, e.g. long_rest_home_ats MLB is 57.0% VALIDATED on `spread` and
    48.1% UNVALIDATED on `rl` — a 0.5-vs-0.0 weight decided by row order.
    Callers here look up by name+sport only, so rather than guess a scope we
    keep the best-evidenced row (largest graded sample) and make the choice
    deterministic instead of incidental.
    """
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_REGISTRY_AGE_DAYS)
    dropped_stale = 0
    collisions = 0
    out: dict = {}
    if _SB:
        try:
            for row in fetch_all_rows(
                    'signal_registry',
                    {'select': 'signal_name,sport,market_scope,tier,'
                               'recommended_weight,hit_rate,sample_n,'
                               'last_computed_at,updated_at'}):
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
                prior = out.get(key)
                if prior is not None:
                    collisions += 1
                    # Keep whichever scope carries the larger graded sample.
                    # A row with no sample_n loses to one that has any.
                    if int(row.get('sample_n') or 0) <= int(prior.get('sample_n') or 0):
                        continue
                out[key] = row
        except Exception:
            pass
    if dropped_stale > 0:
        print(f'  [ensemble] dropped {dropped_stale} stale registry rows (>{MAX_REGISTRY_AGE_DAYS}d)')
    if collisions > 0:
        print(f'  [ensemble] {collisions} registry rows collided on '
              f'(signal_name, sport); kept the larger graded sample')
    print(f'  [ensemble] registry loaded {len(out)} signals')
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


# 2026-09-26 · ONE NAME PER SOURCE, AND IT IS THE ONE USERS ALREADY SEE.
#
# Andy: "New undefined acronym, old baseball jargon. 'PDZ is on this side
# (304-203 on RL)' — 'PDZ' is new (Vandy had 'COV'), and 'RL' is still the
# run line in an NCAAF read."
#
# Two naming systems were shipping on the SAME card. The External
# Handicappers section renders app/lib/sourcePersona.ts — "The Ticket",
# "The Dog", "The Volume" — while the pick's own sub line rendered the
# 3-letter scrub codes from _SOURCE_PERSONA. So a user read "The Dog" in
# one panel and "PDZ" in another, for the same handicapper, with nothing
# connecting them and no definition of either code.
#
# The "The X" names are already the approved user-facing labels
# (project_the_x_naming_convention_824) and are equally ToS-safe — they
# leak no vendor name. So prose uses them, and the 3-letter codes go back
# to being what they were meant to be: internal keys.
_SOURCE_DISPLAY = {
    'action': 'The Book',        'dimers': 'The Grinder',
    'covers': 'The Volume',      'vsin': 'The Pulse',
    'pickswise': 'The Chalk',    'pickdawgz': 'The Dog',
    'bettingpros': 'The Spread', 'docsports': 'The Lock',
    'cbs': 'The Consensus',      'oddsshark': 'The Line',
    'fangraphs': 'The Nerd',     'ballparkpal': 'The Park',
    'scp': 'The Fade',           'sbr': 'The Room',
    'betfirm': 'The Sharp',      'tonyspicks': 'The Play',
    'oddscrowd': 'The Money',    'fadereport': 'The Splits',
    'cleatz': 'The Signal',      'scoresandodds': 'The Ticket',
    'so': 'The Ticket',
}


def _persona(src: str) -> str:
    """User-facing handicapper name (ToS-scrub feedback 8/21).

    Returns the same "The X" label the app renders in the External
    Handicappers panel, so one source cannot appear under two names on
    one card. Falls back to "The Source" rather than a bare vendor
    string — never surface raw provider names in user prose.
    """
    key = (src or '').lower().strip()
    return _SOURCE_DISPLAY.get(key, 'The Source')


def _persona_code(src: str) -> str:
    """Internal 3-letter code. For logs and keys, never for user prose."""
    return _SOURCE_PERSONA.get((src or '').lower(), (src or '').upper()[:4])


# 2026-09-26 · market codes are INTERNAL, not display text.
# Andy: "COV is on this side (177-147 on RL) — 'RL' is the run line.
# Should be ATS." He is right: `rl` is used across the codebase as a
# generic spread-ish code, and these prose builders printed it raw with
# .upper(), so a college football card told users about the run line.
# The code stays sport-agnostic; only the label is resolved per sport.
_MARKET_LABEL = {
    'MLB':   {'rl': 'RL', 'spread': 'RL', 'ml': 'ML', 'total': 'O/U'},
    'NHL':   {'rl': 'PL', 'spread': 'PL', 'ml': 'ML', 'total': 'O/U'},
    'NFL':   {'rl': 'ATS', 'spread': 'ATS', 'ml': 'ML', 'total': 'O/U'},
    'NCAAF': {'rl': 'ATS', 'spread': 'ATS', 'ml': 'ML', 'total': 'O/U'},
    'NBA':   {'rl': 'ATS', 'spread': 'ATS', 'ml': 'ML', 'total': 'O/U'},
    'NCAAB': {'rl': 'ATS', 'spread': 'ATS', 'ml': 'ML', 'total': 'O/U'},
}


def _market_label(market, sport) -> str:
    """Display label for a market code in a given sport.

    Falls back to the raw code upper-cased, which is the old behaviour —
    a sport we have not mapped yet is no worse off than before.
    """
    m = str(market or '').lower()
    table = _MARKET_LABEL.get(str(sport or '').upper(), {})
    return table.get(m, m.upper())


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

        # ══ 2026-10-03 · fade_flag IS A STRING, NOT A BOOLEAN ══
        # This read `bool(p.get('fade_flag'))`. The column holds 'neutral',
        # 'boost', 'trust' or 'fade' — and every non-empty string is truthy,
        # so the side was inverted on 11,646 of 11,646 external picks. Only
        # 17 rows in the whole table actually say 'fade'.
        #
        # That is why a card could read "The Volume is on this side" while the
        # External Handicappers panel, which renders the raw pick, showed that
        # same source on the OTHER side. The panel was right.
        _ff = str(p.get('fade_flag') or '').strip().lower()
        invert = (_ff == 'fade')
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
                    display_prose=f'Fade {persona}: {wins}-{losses} on '
                                 f'{_market_label(market, sport)} picks',
                ))
                continue  # emit fade only, skip the losing-side opinion

        tier = 'VALIDATED' if (hr and hr >= 0.57 and n >= 50) \
               else 'DISCOVERY' if (hr and hr >= 0.55 and n >= 20) \
               else 'UNVALIDATED'

        wins = int(rec.get('n_wins') or 0) if rec else 0
        losses = int(rec.get('n_losses') or 0) if rec else 0
        rec_str = f'{wins}-{losses}' if (wins or losses) else f'{n} picks'
        # If the pick was inverted, the source is on the OPPOSITE side and
        # saying "is on this side" is simply false. The hit-rate fade path
        # below already words this correctly; this path did not.
        _prose = (f'Fade {persona}: {rec_str} on {_market_label(market, sport)}'
                  if invert else
                  f'{persona} is on this side '
                  f'({rec_str} on {_market_label(market, sport)})')
        out.append(Opinion(
            signal_key=f'external:{src}' + ('__fadeflag' if invert else ''),
            signal_class='external_pick', side=cand, strength=0.5,
            hit_rate=hr, sample_n=n, tier=tier,
            display_prose=_prose,
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
    # ══ 2026-10-03 · THE SPORT HAS TO REACH THE HANDLERS ══
    # _handler_external read `ctx.get('sport') or 'MLB'`, and NO game_context
    # table has a `sport` column — verified on all six. So every handler-based
    # signal believed every game was MLB, in every sport. Consequences, all
    # user-visible: external track records were looked up against the source's
    # MLB record, _market_label printed "RL" on NFL/NCAAF cards, and the
    # cold-source fade-flip was decided on an MLB hit rate.
    #
    # It also explains why the 2026-09-26 fix for "RL is the run line in an
    # NCAAF read" never took: the label map was right, the sport fed into it
    # was always 'MLB'. A correct lookup table cannot save a wrong key.
    ctx = {**ctx, 'sport': sport}
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
                # 2026-09-17 fade prose fix (Andy audit: Syracuse@Pitt read
                # "Under 51.5: Fade: Model sees 54.01 vs market 51.50" was
                # backwards — pick is UNDER but sub argues the OVER case).
                # Prior template quoted the ORIGINAL signal's prose verbatim
                # with a "Fade:" prefix. Users read it as "we picked X but
                # the reasoning here argues for Y." Rewrite to lead with the
                # fade CONCLUSION instead of the original signal claim.
                pct = int(round(fade_hr * 100))
                # Side-aware narrative: what direction is the fade going TO?
                side_narrative = {
                    'OVER':      'Historical trend: similar model-lows hit OVER',
                    'UNDER':     'Historical trend: similar model-highs hit UNDER',
                    'HOME_ML':   'Historical trend: similar spots hit HOME ML',
                    'AWAY_ML':   'Historical trend: similar spots hit AWAY ML',
                    'HOME_RL':   'Historical trend: similar spots hit HOME +pts',
                    'AWAY_RL':   'Historical trend: similar spots hit AWAY +pts',
                }.get(flipped_side, 'Historical trend fades this signal')
                # 2026-09-26: two different fade signals produced the SAME
                # generic label with different rates, so a card read
                # "similar spots hit HOME +pts 61% (n=64) · similar spots
                # hit HOME +pts 57% (n=107)" back to back — identical claim,
                # two answers. They are genuinely different cohorts; the
                # label just never said which. Name the cohort so the two
                # lines are distinguishable, and so a real duplicate can be
                # collapsed downstream by exact match.
                _cohort = _humanize_signal_key(source['signal_key'])
                fade_prose = (f'{side_narrative} {pct}% of the time '
                              f'(n={n}{", " + _cohort if _cohort else ""})')
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
            # 2026-09-16 humanize the signal_key fallback. Prior code fell
            # back to the raw signal_key, then _compose_ensemble_sub
            # applied .capitalize() → primary_play sub read as
            # "NO +8.5: Nfl_away_spread_edge_panel" leaking internal
            # naming through to users. Now: strip sport prefix, replace
            # underscores with spaces, sentence-case. Same signal
            # identity, readable prose.
            display_prose=prose or _humanize_signal_key(source['signal_key']),
            is_recent=is_recent,
        ))
    return out


# 2026-09-16 signal_key → reader-friendly prose fallback.
# Called when display_prose_template is null so the primary_play sub
# text doesn't leak raw underscored uppercased signal_key strings.
def _humanize_signal_key(key: str) -> str:
    if not key: return ''
    s = key
    # Strip sport prefix (nfl_, ncaaf_, mlb_, nba_, nhl_, ncaab_, ufc_)
    for pfx in ('nfl_', 'ncaaf_', 'mlb_', 'nba_', 'nhl_', 'ncaab_', 'ufc_'):
        if s.lower().startswith(pfx):
            s = s[len(pfx):]
            break
    # Common suffix normalize
    s = s.replace('_panel', ' (panel)').replace('_lens', ' (lens)')
    # 2026-09-26: a trailing numeric pair is a RANGE, not two words.
    # heavy_home_dog_7_13 was humanising to "Heavy home dog 7 13", which
    # reads as a typo in user prose where it now appears as the cohort
    # name distinguishing two otherwise identical trend lines.
    import re as _re
    s = _re.sub(r'_(\d+)_(\d+)$', r'_\1-\2', s)
    s = s.replace('_', ' ').strip()
    # Sentence case
    if s and s[0].isalpha():
        s = s[0].upper() + s[1:]
    return s


def _score_market(market: str, opinions: list[Opinion], ctx: dict,
                   lean_override: Optional[float] = None,
                   sport: Optional[str] = None) -> MarketDecision:
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
            tier=op.tier,          # 2026-09-03: for signal-quality gate
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
    # ══ 2026-10-02 · A SIGNAL AND ITS OWN MIRROR ARE ONE OBSERVATION ══
    # Measured on 35 NHL games: nhl_home_pp_vs_weak_pk (HOME_ML) and
    # nhl_away_pp_vs_weak_pk__fade (HOME_ML) fire on the SAME games and BOTH
    # push HOME, 35 of 35 — the two most frequent NHL signals, counted twice.
    #
    # The chain: nhl_away_pp_vs_weak_pk is ANTI_VALIDATED on AWAY_ML, so
    # auto-fade flips it to HOME_ML. The result asserts that the AWAY team
    # holding a power-play advantage is a reason to back HOME, stacked on top
    # of the direct home signal. Same shape as the goalie pair,
    # home_goalie_elite_gsaa 67.7% / away_goalie_elite_gsaa 33.3%, which sum
    # to ~101% on near-identical samples because they are one fact read from
    # both ends.
    #
    # These fell to 'unique_' + key, so two spellings of one comparison
    # landed in two different families and both survived the dedupe below.
    # Normalising the home/away token (and any fade suffix) puts them in ONE
    # family, and the existing "keep the highest contribution per family"
    # rule then collapses them with no new machinery.
    #
    # This can only ever fire within a SINGLE side — per_side is keyed by
    # candidate — which is exactly the double-count condition. A genuine
    # home/away pair that disagrees still contributes to both sides, as it
    # should. Pairs with distinct venue suffixes (home_ats_cold_at_home vs
    # away_ats_cold_on_road) normalise to different keys and are untouched.
    def _mirror_norm(k: str) -> str:
        for suf in ('__fade', '_fade'):
            if k.endswith(suf):
                k = k[: -len(suf)]
                break
        for a, b in (('_home_', '_SIDE_'), ('_away_', '_SIDE_')):
            k = k.replace(a, b)
        for pre in ('home_', 'away_'):
            if k.startswith(pre):
                k = 'SIDE_' + k[len(pre):]
                break
        # The side token is a SUFFIX as often as an infix —
        # ncaaf_ol_weight_adv_home / ncaaf_ol_weight_adv_away__fade are the
        # NCAAF instance of this bug (both HOME_RL on the 10-02 Pitt @ VT
        # card) and infix handling alone left them in separate families.
        for suf in ('_home', '_away'):
            if k.endswith(suf):
                k = k[: -len(suf)] + '_SIDE'
                break
        return k

    def _fam(sig_key: str) -> str:
        k = (sig_key or '').lower()
        for name, matcher in _FAMILY_PATTERNS:
            if matcher(k): return name
        # Toggle exists so the mirror collapse can be A/B'd against itself in
        # one process — comparing to the stored primary_play does NOT isolate
        # it, because that value has been through every defensive gate and
        # this function runs before them.
        if os.environ.get('ENSEMBLE_MIRROR_DEDUPE', '1') == '0':
            return 'unique_' + k
        return 'mirror_' + _mirror_norm(k)

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

    # 2026-09-03 SIGNAL-QUALITY GATE. Root cause of West Georgia +24.5
    # being surfaced as a LEAN pick on a 1-signal ensemble score of 0.20:
    # the sole supporting signal was `ncaaf_penalty_home_undisciplined`
    # with tier=UNVALIDATED, sample_n=1, hit_rate=100% (that 1 historical
    # game happened to win — statistical noise, not evidence).
    #
    # Gate: LEAN picks must have AT LEAST ONE supporting chip that is
    # either (a) VALIDATED / DISCOVERY tier OR (b) sample_n >= 15.
    # Everything else (all UNVALIDATED with tiny samples) → COVERAGE
    # (visible in game detail for audit, not a card-eligible pick).
    #
    # PRIME/STRONG already have multi-signal / high-score thresholds
    # that make single-noise-signal survival impossible; gate only
    # bites at the LEAN floor where thin data leaked through.
    if tier == 'LEAN':
        credible = False
        for chip in win_chips:
            t = (chip.tier or '').upper()
            if t in ('VALIDATED', 'DISCOVERY'):
                credible = True; break
            if (chip.n or 0) >= 15:
                credible = True; break
        if not credible:
            tier = 'COVERAGE'

    # 2026-08-19: PRIME breadth lane. Traditional PRIME requires score≥1.5
    # (rare — today's Toronto pick at 1.39/1.04/9-sources is just shy).
    # Second lane: a pick with 5+ independent source classes agreeing AND
    # margin ≥ 0.7 also earns PRIME even if score is 1.2-1.5. Rewards
    # broad multi-source confluence, not just single-source magnitude.
    # This is the "solid pick with solid foundation of data" upgrade —
    # user asked ensemble to signal high conviction when it's earned.
    # ══ 2026-09-29 · THIS LANE PROMOTED THE WORST BUCKET ══
    # Measured on all 317 graded football side picks of 2026, ATS by distinct
    # source-class count — the exact knob this lane and classes_boost use:
    #
    #     <=2 classes (no boost)          87-56   60.8%   n=143   z=+2.02
    #      3-4 classes (+2..+4)           60-48   55.6%   n=108
    #      5+  classes (+6..+10, PRIME)   35-31   53.0%   n=66    z=+0.10
    #
    # HONEST READING OF THAT, added after running the significance test:
    # the 5+ bucket is NOT losing money. 53.0% sits just above the 52.4%
    # breakeven and z=+0.10 means it is indistinguishable from breakeven. The
    # <=2 bucket at z=+2.02 is the only bucket that is significantly ABOVE it.
    # So the claim this change rests on is MISALLOCATION, not loss: the boost
    # pays its largest confidence bonus to the segment with the least
    # demonstrated edge, and pays nothing to the one segment that has real edge.
    # That is reason enough to stop paying it, and NOT reason enough to invert
    # it or to suppress those picks.
    #
    # Monotonically INVERSE. The picks that receive the largest confidence
    # bonus are the least accurate, and this lane promotes that same 53% bucket
    # to the top tier. "Broad multi-source confluence" turns out to be
    # overfitting: each additional cohort is a narrower pattern that does not
    # generalise, and the ensemble counted agreement between them as
    # corroboration.
    #
    # Disabled rather than deleted so the intent and the measurement stay
    # together. Re-enable only if class count ever measures positive.
    _CLASS_CONFLUENCE_PROMOTION = False   # measured inverse 2026-09-29
    if (_CLASS_CONFLUENCE_PROMOTION and tier == 'STRONG'
            and win_score >= 1.2 and win_classes >= 5 and margin >= 0.7):
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
    # ══ 2026-09-29 · classes_boost ZEROED — it was inversely calibrated ══
    # See the table in the promotion comment above. The <=2-class bucket, which
    # receives NO boost, is the best performer at 60.8% (n=143); the 5+ bucket,
    # which receives the maximum +10, is the worst at 53.0% (n=66). This term
    # was adding up to 10 points of displayed confidence in inverse proportion
    # to accuracy.
    #
    # Zeroed, not inverted. Inverting would fit a 66-pick bucket, which is the
    # mistake this repo has already made twice (SP+ K=0.85, the retracted NFL
    # prop edge). Removing an unearned bonus needs no new claim; paying a bonus
    # for being wrong does.
    #
    # NOT A FULL RECALIBRATION. win_score, which drives `base` (the 50-80 term),
    # is ALSO non-monotonic on the same sample: 0.0-0.3 -> 64.2% (n=95),
    # 1.0-1.5 -> 46.0% (n=50). So conviction still does not rank pick quality
    # and must not be trusted for stake sizing until `base` has its own study.
    # This change removes the one term measured cleanly inverse; it does not
    # make the number meaningful.
    classes_boost = 0
    conviction = int(round(base + margin_boost + classes_boost))
    conviction = max(45, min(97, conviction))

    display_label, side, line = _label_from_candidate(winner_cand, ctx, sport=sport)

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


def _label_from_candidate(candidate: str, ctx: dict,
                          sport: Optional[str] = None) -> tuple[str, Optional[str], Optional[float]]:
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
    # 2026-09-11 SIGN FIX. NFL close_spread is AWAY-perspective (positive =
    # away dog); every other sport is HOME-perspective. Convert to HOME
    # perspective here so HOME_RL always displays home's line and AWAY_RL
    # always displays away's line — regardless of sport. Was shipping
    # "PHI +5.5" for -5.5 home favorite before this fix.
    # 2026-09-26: sport now arrives as an ARGUMENT, with the ctx stamp only
    # as a fallback. It used to be read solely from ctx['_sport'], stamped
    # by score_game — so any caller that did not stamp it silently got the
    # non-NFL branch and an INVERTED line. Proven on NO @ BAL
    # (close_spread 8.5):
    #
    #     with _sport='NFL'   ->  'BAL -8.5'   correct
    #     stamp missing       ->  'BAL +8.5'   exactly the stored bad value
    #
    # 11 NFL rows since 09-19 carry a line that disagrees with the
    # convention, ~5 of them pure sign flips of this shape. A pick whose
    # own line has the wrong sign is unbettable and ungradeable, and it
    # failed silently because guessing a convention is indistinguishable
    # from knowing one.
    sport = str(sport or ctx.get('_sport') or '').upper()
    try:
        raw_sp = float(close_spread) if close_spread is not None else None
    except (TypeError, ValueError):
        raw_sp = None
    if not sport and raw_sp is not None and candidate in ('HOME_RL', 'AWAY_RL'):
        # Do not guess. NFL and every other sport store close_spread with
        # OPPOSITE polarity, so a missing sport is a coin flip on the sign.
        print(f'  ⚠ _label_from_candidate: no sport for {candidate} with '
              f'close_spread={raw_sp} — spread sign cannot be resolved')
    if raw_sp is not None and sport == 'NFL':
        home_line = -raw_sp  # flip to home perspective for NFL
    else:
        home_line = raw_sp   # MLB / NCAAF / NBA / NCAAB / NHL already home-perspective
    if candidate == 'HOME_RL':
        line = home_line
        return (f'{home} {line:+g}' if line is not None else f'{home} RL', 'HOME', line)
    if candidate == 'AWAY_RL':
        line = -home_line if home_line is not None else None
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
    # 2026-09-11 SIGN CONVENTION LANDMINE. NFL close_spread is AWAY-perspective
    # (positive = away is dog) while every other sport is HOME-perspective
    # (negative = home is fav). _label_from_candidate needs to know so it
    # doesn't ship "PHI +5.5" when PHI is -5.5 home fav. Stamp the sport onto
    # ctx once here; label builder reads it downstream. Copy first so caller's
    # ctx dict isn't mutated.
    if _ctx_needs_copy:
        ctx = dict(ctx); _ctx_needs_copy = False
    ctx['_sport'] = (sport or '').upper()

    health = _current_health_state(sport)
    if health.get('suppressed'):
        return None  # caller falls back to legacy compute_primary_play

    lean_override = health.get('lean_threshold_override')

    opinions = gather_opinions(sport, ctx)

    # Score only markets applicable to this sport (UFC = fight only, etc.)
    markets = MARKETS_BY_SPORT.get(sport.upper(), ['ml', 'rl', 'total'])
    ml_dec = _score_market('ml', opinions, ctx, lean_override=lean_override, sport=sport) if 'ml' in markets else _no_pick('ml', ctx)
    rl_dec = _score_market('rl', opinions, ctx, lean_override=lean_override, sport=sport) if 'rl' in markets else _no_pick('rl', ctx)
    total_dec = _score_market('total', opinions, ctx, lean_override=lean_override, sport=sport) if 'total' in markets else _no_pick('total', ctx)
    # Combat sports: fight-market decision replaces ML
    if 'fight' in markets:
        ml_dec = _score_market('fight', opinions, ctx, lean_override=lean_override, sport=sport)
        # Convert fight to 'ml' shape for downstream since app reads primary_play.type=='ml' universally
        ml_dec.market = 'ml'

    # 2026-09-14 LR-ML PROMOTION. Per NFL Week 1 audit + Andy directive:
    # when LR ML shadow is STRONG/PRIME AND agrees with our ML pick's
    # side, boost ML conviction by +8 before the top-market race. This
    # fixes the KC ML case: LR called KC ML STRONG (p_home=0.575), but
    # ensemble picked a total COVERAGE. LR-strong-ML shouldn't lose the
    # tiebreak to a low-tier total when the LR model has been proven the
    # single strongest signal this season (10-6 SU / 6-2 PRIME @ 75% W1).
    #
    # Only boosts when LR agrees with ml_dec.side — never manufactures
    # a pick out of nothing, only elevates one we were already going to
    # publish. Cost: negligible (one lazy LR predict call per game).
    if ml_dec.pick is not None and ml_dec.side:
        try:
            from defensive_gates import (
                _lr_predict_ml,
                _LR_MODEL_MLB_ML, _LR_MODEL_NFL_ML, _LR_MODEL_NCAAF_ML,
                _LR_MODEL_NHL_ML, _LR_MODEL_NBA_ML,
            )
            _MODEL = {'MLB': _LR_MODEL_MLB_ML, 'NFL': _LR_MODEL_NFL_ML,
                      'NCAAF': _LR_MODEL_NCAAF_ML, 'NHL': _LR_MODEL_NHL_ML,
                      'NBA': _LR_MODEL_NBA_ML}.get(sport.upper())
            if _MODEL is not None:
                _pred = _lr_predict_ml(ctx, model=_MODEL)
                if isinstance(_pred, dict):
                    _lr_tier = _pred.get('suggested_tier')
                    _lr_side = _pred.get('suggested_side')
                    if _lr_tier in ('STRONG', 'PRIME') and _lr_side == ml_dec.side:
                        ml_dec.conviction = min(100, (ml_dec.conviction or 0) + 8)
        except Exception:
            pass  # never break scoring if LR import/predict raises

    # ══ 2026-09-29 · FOOTBALL TOTALS BARRED FROM THE TOP PICK ══
    # Measured on every graded football pick of 2026 (NFL weeks 1-3, NCAAF
    # through week 4):
    #
    #     all football totals   16-32   33.3%   n=48   -17.5u at -110
    #       NCAAF               14-29   32.6%   n=43
    #       NFL                  2-3    40.0%   n=5
    #
    # Bad at every conviction level (31.0% n=29, 37.5% n=16, 33.3% n=3) and bad
    # in BOTH directions — NCAAF OVER 4-14 (22.2%), UNDER 10-15 (40.0%). A
    # one-sided miss would be a bias to correct; losing on both sides means the
    # total projection carries no usable information about which way to go.
    # FADING every one of these picks would have gone 32-16 (66.7%).
    #
    # For scale: football SIDES over the same period went 182-135 (57.4%,
    # +30.4u). Totals were eating 58% of what the side engine earned.
    #
    # BARRED, NOT UNSCORED. total_dec is still computed and still written, so
    # the shadow record keeps accumulating and this can be lifted on evidence
    # rather than on a hunch — the same discipline the repo applies to every
    # other suppression. Removing 'total' from MARKETS_BY_SPORT would have
    # thrown the measurement away along with the losses.
    #
    # MLB/NHL/NBA/NCAAB are untouched: this was measured on football only, and
    # MLB totals are a different engine on a different sample.
    _barred = set(TOP_PICK_BARRED.get(str(sport or '').upper(), set()))

    # ══ 2026-09-29 · MONEYLINE ON AN UNDERDOG IS BARRED TOO ══
    # Andy after NCAAF week 4: "no favoring spread dogs, feel like that killed us
    # in ncaaf this weekend." Measured across every graded football side pick of
    # 2026 — and the instinct is right about the damage but wrong about the
    # target, which changes the fix:
    #
    #     ML   on underdog   1-6    14.3%  n=7    z=-2.02   <- the actual leak
    #     ML   on favourite  52-25  67.5%  n=77   z=+2.66   <- best segment we have
    #     SPREAD on underdog 38-33  53.5%  n=71   z=+0.19   <- fine, above breakeven
    #     SPREAD on favourite 76-52 59.4%  n=128  z=+1.58
    #
    # So SPREAD dogs are NOT the problem and are deliberately left alone —
    # suppressing them would remove 71 picks running slightly profitable. The
    # problem is taking a DOG on the MONEYLINE: 1-6 overall and 0-5 once the dog
    # is 3+ points. Week 4 examples that lost: Oklahoma ML at +12.5 tagged
    # PRIME/87, Western Michigan ML at +8.5 at conv 85.
    #
    # That is structurally wrong rather than unlucky. An ML on a double-digit dog
    # needs roughly 25-30% to break even at its price; publishing it as the
    # PRIMARY play asserts the opposite. Extends the documented finding that the
    # engine has no price discipline on this path
    # (project_nfl_fav_ml_price_discipline_927).
    #
    # Barred from the TOP PICK only, exactly like totals — ml_dec is still scored
    # and written, so the shadow record keeps accruing and this lifts on evidence.
    # n=7 is thin; z=-2.02 is what justifies acting now rather than waiting, and
    # the 0-5 on 3+ point dogs is the part that is hard to explain as variance.
    try:
        _ml_side = str(getattr(ml_dec, 'side', '') or '').upper()
        _cs = ctx.get('close_spread')
        if ml_dec.pick is not None and _ml_side in ('HOME', 'AWAY') and _cs is not None:
            # Normalise to a HOME handicap. The two sports store spread with
            # OPPOSITE sign conventions, both deliberate and both documented in
            # their odds pulls: NFL flips to nflverse (positive = home FAVOURITE),
            # NCAAF keeps CFBD (positive = home DOG). Measured against results:
            # NFL 44/0 on one formula, NCAAF 287/10 on the other. Getting this
            # backwards would bar exactly the favourites we want to keep.
            _hl = float(_cs) if str(sport).upper() == 'NCAAF' else -float(_cs)
            _home_is_fav = _hl < 0
            _picked_dog = ((_home_is_fav and _ml_side == 'AWAY')
                           or ((not _home_is_fav) and _ml_side == 'HOME'))
            if _picked_dog and str(sport).upper() in ('NFL', 'NCAAF'):
                _barred.add('ml')
    except (TypeError, ValueError):
        pass  # no line, or unparseable — leave ml eligible

    # Determine top market (highest conviction with a pick)
    picks = [(m, d) for m, d in [('ml', ml_dec), ('rl', rl_dec), ('total', total_dec)]
             if d.pick is not None]
    _eligible = [(m, d) for m, d in picks if m not in _barred]
    if _eligible:
        # Fall back to the full list only if EVERY eligible market passed, so a
        # barred market never wins while a real side pick exists.
        top_market = max(_eligible, key=lambda p: p[1].conviction)[0]
    elif picks:
        top_market = max(picks, key=lambda p: p[1].conviction)[0]
        if top_market in _barred:
            print(f'    [totals-bar] {sport}: only a total scored; '
                  f'publishing it as the top pick would be the measured -17.5u '
                  f'path, but there is nothing else — keeping it visible')
    else:
        top_market = 'total'  # arbitrary default when all pass

    # 2026-09-09 MARKET-SELECTION STABILITY GATE.
    # Bug fixed: when two markets score similar conviction (e.g. ML=82 and
    # Total=82), any feature-value drift between crons flipped the winner.
    # Users saw picks oscillate across the same day (HOU@PHI: Over 8.5
    # in AM cron → PHL -1.5 at 2pm → Over 8.5 again post-close-line lock).
    # This is the root of the badge/detail-drift class of bugs.
    #
    # Fix: if a prior primary_play exists on ctx AND the new top-market
    # only edges it out by less than STABILITY_MARGIN conviction points,
    # KEEP the prior market. Requires the prior market to still be a
    # valid pick (not passed by any gate this run). Anything ≥ margin
    # points better wins normally — real signal shifts still get through.
    STABILITY_MARGIN = 5
    prior_pp = ctx.get('primary_play')
    if isinstance(prior_pp, dict):
        prior_market = str(prior_pp.get('type', '')).lower()
        # 2026-09-29: must not restore a BARRED market. Caught by testing —
        # NE @ BUF had ml (COVERAGE/61) and rl (LEAN/65) both picking, the bar
        # correctly chose rl, and then this gate handed it straight back to
        # 'total' because the row's PRIOR pick was Under 49.5. The stability
        # gate is about not oscillating between two legitimate markets; a
        # market we have measured at 33.3% is not one of them, and "it was the
        # pick last run" is exactly how a suppressed market would persist
        # forever.
        if (prior_market in ('ml', 'rl', 'total') and prior_market != top_market
                and prior_market not in _barred):
            prior_dec = {'ml': ml_dec, 'rl': rl_dec, 'total': total_dec}[prior_market]
            new_dec = {'ml': ml_dec, 'rl': rl_dec, 'total': total_dec}[top_market]
            if (prior_dec is not None and prior_dec.pick is not None
                    and (new_dec.conviction or 0) - (prior_dec.conviction or 0) < STABILITY_MARGIN):
                top_market = prior_market

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
