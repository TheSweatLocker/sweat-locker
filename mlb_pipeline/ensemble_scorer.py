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


def _load_registry() -> dict:
    """Load signal_registry keyed by (signal_name, sport) tuple.

    2026-08-21 CROSS-SPORT CONTAMINATION FIX: prior version keyed by
    signal_name alone. Many signals exist for multiple sports (e.g.
    away_ats_cold_on_road for MLB AND NHL). Last-loaded wins meant
    MLB scorer often got NHL weights. Example caught: away_ats_cold_on_road
    MLB=56.1% n=41 vs NHL=36.9% n=575 → scorer used NHL number → signal
    was silently treated as anti-signal on MLB games for weeks.
    """
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    out: dict = {}
    if _SB:
        try:
            r = requests.get(f'{_SB}/rest/v1/signal_registry',
                             headers=_H_READ,
                             params={'select': 'signal_name,sport,tier,recommended_weight,hit_rate,sample_n'},
                             timeout=10)
            for row in (r.json() if r.status_code == 200 else []):
                key = (row['signal_name'], row.get('sport') or '*')
                out[key] = row
        except Exception:
            pass
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
        # Try sport-specific first, then wildcard
        reg = registry.get((lookup_key, sport)) or registry.get((lookup_key, '*'))
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

def edge_weight(hit_rate: Optional[float], n: int,
                tier: Optional[str] = None) -> float:
    """Translate (hit_rate, n, tier) into weight in [0, 1].

    ANTI_VALIDATED → 0 (fade signal, not evidence).
    Below breakeven → 0.
    Otherwise: linear scale from breakeven to +12pp * sample dampener."""
    if tier == 'ANTI_VALIDATED':
        return 0.0
    if hit_rate is None or n <= 0:
        # No proven track record — small floor if registry knows about it
        return 0.20 if tier in ('DISCOVERY', 'UNVALIDATED', 'VALIDATED') else 0.15
    edge_pp = hit_rate - BREAKEVEN
    if edge_pp <= 0:
        return 0.0
    edge_component = min(edge_pp / 0.12, 1.0)
    n_component = min(math.log1p(n) / math.log(101), 1.0)
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

    out: list[Opinion] = []
    for flag in flags:
        cls = str(flag.get('classification') or '')
        if not cls or cls in ('PATTERN_ONLY', 'NEUTRAL', 'SOURCES_SPLIT'):
            continue
        # Match source_row to classification tier
        if signal_key == 'sharp_split_triple_confirmed' and '_TRIPLE_CONFIRMED' not in cls:
            continue
        if signal_key == 'sharp_split_confirmed' and ('_TRIPLE_CONFIRMED' in cls or '_CONFIRMED' not in cls):
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
            display_prose=prose,
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
            tier=top['tier'], display_prose=prose,
        ))
    return out


def _handler_external(source_row: dict, ctx: dict) -> list[Opinion]:
    """External handicapper picks for this game, each weighted by that
    handicapper's own track record (external_source_track_record)."""
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

        # Fade flag on the source means we invert
        invert = bool(p.get('fade_flag'))
        cand = _flag_to_candidate(market, pick_side, invert)
        if not cand: continue

        # Look up source track record
        rec = tracks.get((src, sport, surface)) or tracks.get((src, sport, 'ALL'))
        hr = rec.get('hit_rate') if rec else None
        if hr is not None:
            try: hr = float(hr) / 100.0
            except (TypeError, ValueError): hr = None
        n = int(rec.get('n_graded', 0)) if rec else 0
        tier = 'VALIDATED' if (hr and hr >= 0.57 and n >= 50) \
               else 'DISCOVERY' if (hr and hr >= 0.55 and n >= 20) \
               else 'UNVALIDATED'

        out.append(Opinion(
            signal_key=f'external:{src}',
            signal_class='external_pick', side=cand, strength=0.5,
            hit_rate=hr, sample_n=n, tier=tier,
            display_prose=f'{src} is on this side ({int((hr or 0)*100)}% {n}-pick track)',
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
        if tier == 'ANTI_VALIDATED' and hr is not None and n >= 25 and hr <= 0.47:
            flip_map = {'HOME_ML':'AWAY_ML','AWAY_ML':'HOME_ML',
                        'HOME_RL':'AWAY_RL','AWAY_RL':'HOME_RL',
                        'OVER':'UNDER','UNDER':'OVER'}
            flipped_side = flip_map.get(side)
            if flipped_side:
                fade_hr = 1.0 - hr
                # Promote to VALIDATED if fade edge is meaningful (>= 55%)
                # AND sample is decent, else DISCOVERY.
                fade_tier = 'VALIDATED' if (fade_hr >= 0.55 and n >= 50) else 'DISCOVERY'
                fade_prose = f'[fade] {prose or source["signal_key"]}'
                out.append(Opinion(
                    signal_key=f'{source["signal_key"]}__fade',
                    signal_class=cls,
                    side=flipped_side, strength=strength,
                    hit_rate=fade_hr, sample_n=n, tier=fade_tier,
                    display_prose=fade_prose,
                ))
                continue  # skip emitting the original (zero-weight) opinion

        out.append(Opinion(
            signal_key=source['signal_key'],
            signal_class=cls,
            side=side, strength=strength,
            hit_rate=hr, sample_n=n, tier=tier,
            display_prose=prose or source['signal_key'],
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
        w = edge_weight(op.hit_rate, op.sample_n, op.tier)
        if w == 0 and op.strength == 0:
            continue
        c = Contribution(
            signal_key=op.signal_key, signal_class=op.signal_class,
            side=op.side, weight=w, strength=op.strength, n=op.sample_n,
            contribution=round(w * op.strength, 4),
            display_prose=op.display_prose,
        )
        per_side[op.side].append(c)

    if not per_side:
        return _no_pick(market, ctx)

    # Sum per candidate + apply class-balance
    scored: list[tuple[str, float, list[Contribution], dict, int]] = []
    for cand in candidates:
        chips = per_side.get(cand, [])
        raw_total = sum(c.contribution for c in chips)
        class_share: dict[str, float] = defaultdict(float)
        for c in chips:
            class_share[c.signal_class] += c.contribution
        # Class-balance penalty: cap any single class at MAX_CLASS_SHARE of total
        # 2026-08-21: changed from soft (overflow*0.5) to HARD cap. Soft penalty
        # let scenario class hit ~70% of Braves 8/20 pick score (0.75/0.98)
        # despite 40% cap — half the overflow slipped through. Hard cap makes
        # the class-diversity gate actually enforced.
        adjusted_total = raw_total
        if raw_total > 0:
            max_allowed = raw_total * MAX_CLASS_SHARE
            for cls_name, share in class_share.items():
                if share > max_allowed:
                    overflow = share - max_allowed
                    adjusted_total -= overflow  # HARD cap (was overflow*0.5)
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
