"""Ensemble Scorer — evidence-weighted decision engine (2026-08-16).

The Sweat Locker's moat: we collect every projection, split, cohort match,
sharp signal, and external pick. The old `compute_primary_play` used a
hand-tuned decision tree on a subset of that data. This module replaces
that with a proper weighted-ensemble scorer that uses EVERY signal we
have, with each signal weighted by its own proven historical track record.

Architecture:
  For each game, enumerate candidates (HOME_ML, AWAY_ML, OVER, UNDER,
  HOME_RL, AWAY_RL). For each candidate, gather every source that has
  an opinion — model prediction, cohort match, sharp scenario, public
  split classification, external pick, signal-registry signal. Weight
  each opinion by the source's hit rate over its own historical window
  (from signal_registry, external_source_track_record, sharp_scenarios,
  or the cohort's own recorded hit_rate). Sum to a candidate score.
  Pick the candidate with the highest positive score. Tier from score +
  count of VALIDATED sources agreeing. Return the full breakdown.

Sport-universal:
  * Core (this module) is sport-agnostic — takes a struct of opinions,
    returns a decision.
  * Sport-specific adapters (gather_opinions_mlb, _nfl, _ncaaf, _ufc)
    know how to read each sport's game_context table and translate the
    raw fields into standardized Opinion records.
  * Only MLB adapter shipped in this pass — NFL/NCAAF/UFC drop in with
    identical output shape.

Backtest before ship:
  backtest_ensemble_scorer.py replays the last 30-60d of resolved games
  and compares per-tier hit rate + ROI vs current compute_primary_play.
  Ship only if it beats baseline.

Not shipped yet:
  * External picks per-handicapper weighting (data is in external_picks
    + external_source_track_record but adapter for gathering per-game
    picks not built here — first pass focuses on model + cohort +
    sharp-split + line-movement signals).
  * Prop-market candidates (ML/RL/Total only in v1).
"""
from __future__ import annotations
import math, os, json, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from datetime import date

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


# ═══════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════

# Standard candidate labels — sport-agnostic
CANDIDATES_TEAM = ['HOME_ML', 'AWAY_ML', 'HOME_RL', 'AWAY_RL', 'OVER', 'UNDER']
CANDIDATES_FIGHT = ['FIGHTER_A_ML', 'FIGHTER_B_ML']

# Breakeven for -110 juice
BREAKEVEN = 0.524


@dataclass
class Opinion:
    """One source's opinion about one candidate.

    - side: which candidate this opinion supports (must be in CANDIDATES_*).
    - strength: normalized to [-1, +1]. +1 = maximally confident this side
      wins. Negative would mean 'confident against' (rare, used for
      cross-check gates).
    - source: source identifier (e.g. 'mc', 'panel', 'v4', 'cohort:home_bp',
      'oddscrowd_split', 'sharp_scenario_home_dog_+7', 'external:actionpicks').
    - hit_rate: source's historical hit rate as a fraction (0.62 = 62%).
      None = no proven track record yet.
    - sample_n: sample size behind hit_rate. Bigger n = more trustworthy.
    - tier: signal_registry tier if applicable (VALIDATED / DISCOVERY /
      UNVALIDATED / ANTI_VALIDATED). None if not in registry.
    - note: short human-readable note ("MC 62% OVER").
    """
    side: str
    strength: float
    source: str
    hit_rate: Optional[float] = None
    sample_n: int = 0
    tier: Optional[str] = None
    note: str = ''


@dataclass
class Contribution:
    """One source's weighted contribution to a candidate's score."""
    source: str
    side: str
    weight: float          # derived from hit_rate + sample_n + tier
    strength: float        # raw strength [-1, +1]
    n: int
    contribution: float    # weight * strength * sample-dampener
    note: str


@dataclass
class Decision:
    """Final output of the scorer for one game.

    - pick: candidate label (e.g. 'OVER', 'HOME_ML')
    - display_label: human-readable ('Over 8.5', 'Dodgers ML')
    - type: 'ml' | 'rl' | 'total' | 'fight'
    - side: 'HOME' | 'AWAY' | 'OVER' | 'UNDER' | 'A' | 'B' | None
    - line: numeric line if applicable (total or spread)
    - tier: 'PRIME' | 'STRONG' | 'LEAN' | 'PASS'
    - conviction: 0-100
    - score: raw ensemble score (for debugging / thresholding)
    - contributions: list of Contribution — the audit trail of why this
      pick was made. Jerry narrates this list.
    - competing_score: score of the second-best candidate (for margin display)
    """
    pick: str
    display_label: str
    type: str
    side: Optional[str]
    line: Optional[float]
    tier: str
    conviction: int
    score: float
    contributions: list = field(default_factory=list)
    competing_score: float = 0.0

    def to_primary_play_dict(self) -> dict:
        """Convert to the primary_play dict shape mlb_game_context stores."""
        return {
            'type': self.type,
            'tier': self.tier,
            'label': self.display_label,
            'side': self.side,
            'line': self.line,
            'conviction': self.conviction,
            'score': round(self.score, 2),
            'sub': self._compose_sub(),
            'audit_note': f'ensemble_scorer v1 · {len(self.contributions)} sources fired · score={self.score:.2f} · margin={self.score - self.competing_score:.2f}',
            '_ensemble_sources': [
                {'source': c.source, 'side': c.side, 'weight': round(c.weight, 2),
                 'n': c.n, 'contribution': round(c.contribution, 2), 'note': c.note}
                for c in self.contributions[:8]
            ],
        }

    def _compose_sub(self) -> str:
        """One-line summary of the top-3 supporting signals."""
        supporting = [c for c in self.contributions if c.side == self.pick and c.contribution > 0]
        supporting.sort(key=lambda c: -c.contribution)
        if not supporting:
            return f'{self.pick} — ensemble score {self.score:.2f}'
        top = supporting[:3]
        parts = [c.note for c in top if c.note]
        return f'{self.pick}: ' + ' · '.join(parts)


# ═══════════════════════════════════════════════════════════════════════
# WEIGHT COMPUTATION
# ═══════════════════════════════════════════════════════════════════════

def edge_weight(hit_rate: Optional[float], n: int,
                tier: Optional[str] = None,
                baseline: float = BREAKEVEN) -> float:
    """Translate (hit_rate, sample_n, tier) -> weight in [0, 1].

    Weight = 0 when source is at or below breakeven (no edge).
    Weight scales linearly from breakeven to +12pp above breakeven.
    Weight is dampened by sample size via ln(1+n)/ln(101) — a source
    with n=100 gets full weight; n=10 gets ~0.5x weight; n=1 gets ~0.15.

    Tier override: ANTI_VALIDATED forces weight to 0 regardless of hit_rate
    (the pattern is a fade signal, not evidence to follow). UNVALIDATED
    with no hit rate defaults to a small floor (0.15) so untested signals
    still contribute a whisper — but never more than a proven one.
    """
    if tier == 'ANTI_VALIDATED':
        return 0.0
    if hit_rate is None or n <= 0:
        # No historical evidence — small floor if signal is at least on
        # the registry (i.e. we know about it), otherwise zero.
        return 0.15 if tier in ('DISCOVERY', 'UNVALIDATED', 'VALIDATED') else 0.0

    edge_pp = hit_rate - baseline
    if edge_pp <= 0:
        return 0.0
    edge_component = min(edge_pp / 0.12, 1.0)  # cap at +12pp above breakeven
    n_component = math.log1p(n) / math.log(101)  # n=100 -> 1.0
    n_component = min(n_component, 1.0)
    return round(edge_component * n_component, 4)


def score_contributions(opinions: list[Opinion]) -> dict[str, list[Contribution]]:
    """Group opinions by candidate side and compute weighted contributions."""
    per_side: dict[str, list[Contribution]] = {}
    for op in opinions:
        w = edge_weight(op.hit_rate, op.sample_n, op.tier)
        if w == 0 and op.strength == 0:
            continue
        # Sample-dampener applied both to weight (in edge_weight) and to
        # contribution — a whisper source shouldn't overwhelm a proven one.
        # Contribution sign follows strength (positive = supports side).
        contribution = round(w * op.strength, 4)
        chip = Contribution(
            source=op.source, side=op.side, weight=w, strength=op.strength,
            n=op.sample_n, contribution=contribution, note=op.note,
        )
        per_side.setdefault(op.side, []).append(chip)
    return per_side


# ═══════════════════════════════════════════════════════════════════════
# DECISION
# ═══════════════════════════════════════════════════════════════════════

# Tier thresholds (calibrated to backtest — v1 defaults, will tune)
TIER_THRESHOLDS = {
    'PRIME':  {'min_score': 2.5,  'min_validated_sources': 2},
    'STRONG': {'min_score': 1.5,  'min_validated_sources': 1},
    'LEAN':   {'min_score': 0.6,  'min_validated_sources': 0},
}


def decide(opinions: list[Opinion], ctx: dict,
            candidate_set: list[str] = CANDIDATES_TEAM) -> Optional[Decision]:
    """Score all candidates, return the winning Decision or None."""
    per_side = score_contributions(opinions)
    if not per_side:
        return None

    # Sum score per candidate side
    scored: dict[str, tuple[float, list[Contribution], int]] = {}
    for side in candidate_set:
        chips = per_side.get(side, [])
        total = sum(c.contribution for c in chips)
        validated = sum(1 for c in chips
                        if c.contribution > 0
                        and any(op.side == side and op.source == c.source
                                and op.tier == 'VALIDATED'
                                for op in opinions))
        scored[side] = (total, chips, validated)

    # Rank
    ranked = sorted(scored.items(), key=lambda kv: -kv[1][0])
    if not ranked:
        return None
    winner, (win_score, win_chips, win_validated) = ranked[0]
    runner_up_score = ranked[1][1][0] if len(ranked) > 1 else 0.0
    margin = win_score - runner_up_score

    if win_score < TIER_THRESHOLDS['LEAN']['min_score']:
        return None  # no candidate cleared the LEAN floor — PASS

    # Assign tier
    tier = 'LEAN'
    for candidate_tier in ('PRIME', 'STRONG'):
        th = TIER_THRESHOLDS[candidate_tier]
        if (win_score >= th['min_score']
                and win_validated >= th['min_validated_sources']
                and margin >= 0.4):  # margin gate — no razor-thin PRIMEs
            tier = candidate_tier
            break

    # Conviction 0-100 mapping (score 0 = 50, score 3+ = 90)
    conviction = int(round(50 + min(win_score * 13, 40)))
    conviction = max(50, min(95, conviction))

    # Build display label from candidate + ctx
    display_label, type_, side, line = _label_from_candidate(winner, ctx)

    return Decision(
        pick=winner,
        display_label=display_label,
        type=type_,
        side=side,
        line=line,
        tier=tier,
        conviction=conviction,
        score=round(win_score, 2),
        contributions=sorted(win_chips, key=lambda c: -c.contribution),
        competing_score=round(runner_up_score, 2),
    )


def _label_from_candidate(candidate: str, ctx: dict) -> tuple[str, str, Optional[str], Optional[float]]:
    """Turn a candidate label + game context into (display, type, side, line)."""
    home = ctx.get('home_team') or 'HOME'
    away = ctx.get('away_team') or 'AWAY'
    close_spread = ctx.get('close_spread')  # negative = home favored
    close_total = ctx.get('close_total')

    if candidate == 'HOME_ML':
        return (f'{home} ML', 'ml', 'HOME', None)
    if candidate == 'AWAY_ML':
        return (f'{away} ML', 'ml', 'AWAY', None)
    if candidate == 'HOME_RL':
        line = None
        if close_spread is not None:
            try: line = float(close_spread)
            except (TypeError, ValueError): pass
        return (f'{home} {line:+g}' if line is not None else f'{home} RL', 'rl', 'HOME', line)
    if candidate == 'AWAY_RL':
        line = None
        if close_spread is not None:
            try: line = -float(close_spread)
            except (TypeError, ValueError): pass
        return (f'{away} {line:+g}' if line is not None else f'{away} RL', 'rl', 'AWAY', line)
    if candidate == 'OVER':
        return (f'Over {close_total}' if close_total is not None else 'Over', 'total', 'OVER', close_total)
    if candidate == 'UNDER':
        return (f'Under {close_total}' if close_total is not None else 'Under', 'total', 'UNDER', close_total)
    return (candidate, 'unknown', None, None)


# ═══════════════════════════════════════════════════════════════════════
# SIGNAL REGISTRY + TRACK RECORD LOOKUPS (cached per run)
# ═══════════════════════════════════════════════════════════════════════

_REGISTRY_CACHE: Optional[dict] = None
_TRACK_CACHE: Optional[dict] = None


def _load_registry() -> dict:
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    out: dict = {}
    if _SB:
        try:
            r = requests.get(f'{_SB}/rest/v1/signal_registry',
                             headers=_H_READ,
                             params={'select': 'signal_name,tier,recommended_weight,hit_rate,sample_n'},
                             timeout=10)
            for row in (r.json() if r.status_code == 200 else []):
                out[row['signal_name']] = row
        except Exception:
            pass
    _REGISTRY_CACHE = out
    return out


def _load_track_records() -> dict:
    global _TRACK_CACHE
    if _TRACK_CACHE is not None:
        return _TRACK_CACHE
    out: dict = {}
    if _SB:
        try:
            r = requests.get(f'{_SB}/rest/v1/external_source_track_record',
                             headers=_H_READ,
                             params={'select': 'source,sport,surface,window_days,hit_rate,n_graded'},
                             timeout=10)
            for row in (r.json() if r.status_code == 200 else []):
                # Prefer 30d window (freshest sample); fall back to 90d / lifetime
                key = (row['source'], row['sport'], row['surface'])
                prio = {30: 3, 90: 2, 9999: 1}.get(row.get('window_days'), 0)
                existing = out.get(key)
                if existing is None or prio > existing['_prio']:
                    row['_prio'] = prio
                    out[key] = row
        except Exception:
            pass
    _TRACK_CACHE = out
    return out


def _registry_lookup(name: str) -> tuple[Optional[float], int, Optional[str]]:
    """Return (hit_rate as fraction, sample_n, tier) for a signal name."""
    row = _load_registry().get(name)
    if not row:
        return (None, 0, None)
    hr = row.get('hit_rate')
    if hr is not None:
        try: hr = float(hr) / 100.0  # registry stores %; scorer wants fraction
        except (TypeError, ValueError): hr = None
    return (hr, int(row.get('sample_n') or 0), row.get('tier'))


def _track_lookup(source: str, sport: str, market: str) -> tuple[Optional[float], int]:
    """Return (hit_rate as fraction, n_graded) for a source × sport × market."""
    # Try market-specific first, fall back to 'ALL' surface
    for surface in (market.lower(), 'ALL'):
        row = _load_track_records().get((source.lower(), sport, surface))
        if row:
            hr = row.get('hit_rate')
            if hr is not None:
                try: hr = float(hr) / 100.0
                except (TypeError, ValueError): hr = None
            return (hr, int(row.get('n_graded') or 0))
    return (None, 0)


# ═══════════════════════════════════════════════════════════════════════
# MLB ADAPTER — turn mlb_game_context row into a list of Opinions
# ═══════════════════════════════════════════════════════════════════════

def _mc_probs(ctx: dict) -> Optional[dict]:
    v = ctx.get('mc_probabilities')
    if isinstance(v, str):
        try: return json.loads(v)
        except Exception: return None
    return v if isinstance(v, dict) else None


def _opinion_from_model(source_name: str, prob_home: Optional[float],
                        market: str, side_side: dict[bool, str],
                        registry_key: str,
                        strength_scale: float = 1.0) -> Optional[Opinion]:
    """Common helper: turn a model's home-win probability into an Opinion
    for that side. If prob_home < 0.5, opinion is for the AWAY side.
    Strength = |prob - 0.5| * 2 (0 = coinflip, 1 = certain), scaled."""
    if prob_home is None:
        return None
    try: p = float(prob_home)
    except (TypeError, ValueError): return None
    side = side_side[p >= 0.5]
    strength = min(abs(p - 0.5) * 2 * strength_scale, 1.0)
    if strength == 0:
        return None
    hr, n, tier = _registry_lookup(registry_key)
    return Opinion(side=side, strength=strength, source=source_name,
                   hit_rate=hr, sample_n=n, tier=tier,
                   note=f'{source_name} {int(p*100)}% {side}')


def gather_opinions_mlb(ctx: dict) -> list[Opinion]:
    """Turn a full mlb_game_context row into every Opinion the ensemble
    can read. Sport-specific — knows MLB column names + registry keys."""
    ops: list[Opinion] = []

    close_total = ctx.get('close_total')
    close_spread = ctx.get('close_spread')

    # ── MODELS: ML direction (via spread — negative = home favored) ──
    for source_name, spread_field, reg_key in [
        ('MC', None, 'mc_ml_high_conf'),  # MC handled via probabilities below
        ('Panel', 'panel_implied_margin', 'panel_ml'),
        ('V4', 'model_pred_spread', 'v4_ml'),
        ('V3', 'projected_spread', 'v3_ml'),
        ('JerryPred', 'jerry_pred_spread', 'jerry_pred_ml'),
    ]:
        if spread_field is None:
            continue
        v = ctx.get(spread_field)
        if v is None:
            continue
        try: spread = float(v)
        except (TypeError, ValueError): continue
        # Model says home favored if implied_margin > 0
        side = 'HOME_ML' if spread > 0 else 'AWAY_ML' if spread < 0 else None
        if side is None:
            continue
        strength = min(abs(spread) / 3.0, 1.0)  # 3+ run edge = max strength
        hr, n, tier = _registry_lookup(reg_key)
        ops.append(Opinion(
            side=side, strength=strength, source=source_name,
            hit_rate=hr, sample_n=n, tier=tier,
            note=f'{source_name} sees {side.replace("_ML","")} by {abs(spread):.1f}',
        ))

    # ── MODELS: MC via probability ──
    mc = _mc_probs(ctx) or {}
    op = _opinion_from_model('MC', mc.get('mc_home_win_prob'), 'ml',
                              {True: 'HOME_ML', False: 'AWAY_ML'},
                              'mc_ml_high_conf', strength_scale=1.5)
    if op: ops.append(op)

    # ── MODELS: totals ──
    if close_total is not None:
        for source_name, total_field, reg_key in [
            ('Panel', 'panel_implied_total', 'panel_implied_total'),
            ('V4', 'model_pred_total', 'v4_projected_total'),
            ('V3', 'projected_total', 'v4_projected_total'),
            ('JerryPred', 'jerry_pred_total', 'jerry_pred_total'),
        ]:
            v = ctx.get(total_field)
            if v is None: continue
            try:
                model_total = float(v)
                ct = float(close_total)
            except (TypeError, ValueError): continue
            delta = model_total - ct
            if abs(delta) < 0.3: continue
            side = 'OVER' if delta > 0 else 'UNDER'
            strength = min(abs(delta) / 2.0, 1.0)
            hr, n, tier = _registry_lookup(reg_key)
            ops.append(Opinion(
                side=side, strength=strength, source=source_name,
                hit_rate=hr, sample_n=n, tier=tier,
                note=f'{source_name} {model_total:.1f} vs {ct:.1f} ({delta:+.1f})',
            ))

    # ── MC totals via probability ──
    mc_over = mc.get('mc_over_prob')
    if mc_over is not None:
        op = _opinion_from_model('MC', mc_over, 'total',
                                  {True: 'OVER', False: 'UNDER'},
                                  'mc_total_high_conf', strength_scale=1.5)
        if op: ops.append(op)

    # ── PUBLIC SPLITS (OC + FR + Cleatz classification) ──
    line_flags = _fetch_line_flags(ctx.get('game_id'), ctx.get('game_date'))
    for flag in line_flags:
        cls = str(flag.get('classification') or '')
        if not cls or cls in ('PATTERN_ONLY', 'NEUTRAL'):
            continue
        flag_side = str(flag.get('side') or '').upper()
        # Convert flag side -> candidate side
        # RLM: line moved against public side — sharp is on the OPPOSITE
        # SHARP_MOVE/CONSENSUS: sharp is on the LISTED side
        # PUBLIC_MOVE: public is on listed -> sharp is on OPPOSITE
        market = str(flag.get('market') or '').lower()
        invert = cls.startswith('RLM') or cls.startswith('PUBLIC_MOVE')
        cand = _flag_to_candidate(market, flag_side, invert)
        if not cand: continue

        # Weight by classification tier
        if '_TRIPLE_CONFIRMED' in cls:
            reg_key = 'cross_source_sharp_confirmed'
            strength = 0.9
        elif '_CONFIRMED' in cls:
            reg_key = 'cross_source_sharp_confirmed'
            strength = 0.7
        else:  # _LEAN
            reg_key = 'cross_source_sharp_confirmed'
            strength = 0.4

        hr, n, tier = _registry_lookup(reg_key)
        ops.append(Opinion(
            side=cand, strength=strength, source=f'line_flag_{cls}',
            hit_rate=hr, sample_n=n, tier=tier,
            note=f'{cls} -> {flag_side}',
        ))

    # ── SHARP SCENARIOS (per-game matches) ──
    try:
        from sharp_scenario_lookup import matches_for_game
        matches = matches_for_game(ctx.get('game_id'), ctx.get('game_date'))
        for m in matches:
            side = str(m.get('side') or '').upper()
            market = str(m.get('market') or '').lower()
            bof = m.get('back_or_fade')
            if bof == 'NEUTRAL' or not side:
                continue
            invert = bof == 'FADE'
            cand = _flag_to_candidate(market, side, invert)
            if not cand: continue
            hr = m.get('hit_rate')
            if hr is not None:
                try: hr = float(hr) / 100.0
                except (TypeError, ValueError): hr = None
            n = int(m.get('n') or 0)
            confidence = m.get('hint_confidence') or 50
            strength = min(confidence / 100.0, 1.0)
            ops.append(Opinion(
                side=cand, strength=strength, source=f'scenario:{m.get("scenario_key")}',
                hit_rate=hr, sample_n=n, tier='DISCOVERY' if n >= 15 else 'UNVALIDATED',
                note=f'scenario {m.get("scenario_key")} {int((hr or 0)*100)}% n={n}',
            ))
    except Exception:
        pass

    # ── COHORT SIGNALS (from signal_confluence_* on ctx) ──
    # signal_confluence_home / signal_confluence_away are net signal counts.
    # signal_confluence_net is home - away. This is a rollup of many cohorts.
    net = ctx.get('signal_confluence_net')
    if net is not None:
        try: net = int(net)
        except (TypeError, ValueError): net = None
    if net is not None and abs(net) >= 2:
        side = 'HOME_ML' if net > 0 else 'AWAY_ML'
        strength = min(abs(net) / 6.0, 1.0)
        ops.append(Opinion(
            side=side, strength=strength, source='confluence_net',
            hit_rate=None, sample_n=0, tier='UNVALIDATED',
            note=f'cohort confluence {net:+d}',
        ))

    return ops


def _flag_to_candidate(market: str, side: str, invert: bool = False) -> Optional[str]:
    """Map (market, side, invert) -> standardized candidate label."""
    side = side.upper()
    m = market.lower()
    # Apply invert
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


def _fetch_line_flags(game_id: Optional[str], game_date: Optional[str]) -> list[dict]:
    """Read line_movement_flags for a game/date. Small query, cached
    per-run isn't necessary because scorer usually runs one game at a time."""
    if not game_id or not _SB:
        return []
    try:
        r = requests.get(f'{_SB}/rest/v1/line_movement_flags',
                         headers=_H_READ,
                         params={'game_id': f'eq.{game_id}',
                                 'select': 'market,side,classification,money_pct,bets_pct,handle_pct,bettors_pct'},
                         timeout=8)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════
# TOP-LEVEL API
# ═══════════════════════════════════════════════════════════════════════

_ADAPTERS = {
    'MLB': gather_opinions_mlb,
    # 'NFL':  gather_opinions_nfl,   # Phase 2
    # 'NCAAF': gather_opinions_ncaaf,
    # 'UFC':  gather_opinions_ufc,   # different candidate set (fight)
}


def score_game(sport: str, ctx: dict) -> Optional[Decision]:
    """Top-level: score one game context via the sport-appropriate adapter."""
    adapter = _ADAPTERS.get(sport.upper())
    if adapter is None:
        return None
    opinions = adapter(ctx)
    if not opinions:
        return None
    return decide(opinions, ctx, candidate_set=CANDIDATES_TEAM)


# ═══════════════════════════════════════════════════════════════════════
# CLI: score a single game for spot-checking
# ═══════════════════════════════════════════════════════════════════════

def _dump_decision(d: Optional[Decision]) -> None:
    if d is None:
        print('  -> PASS (no candidate cleared LEAN floor)')
        return
    print(f'  PICK: {d.display_label}  [{d.tier} · conv={d.conviction} · score={d.score:.2f}]')
    print(f'  margin over runner-up: {d.score - d.competing_score:.2f}')
    print(f'  contributions:')
    for c in d.contributions[:8]:
        print(f'    {c.source:<40} {c.side:<10} w={c.weight:.2f} n={c.n:<4} contrib={c.contribution:+.2f} · {c.note}')


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB')
    p.add_argument('--date', default=date.today().isoformat())
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--game-id', default=None)
    args = p.parse_args()

    table = 'mlb_game_context' if args.sport == 'MLB' else f'{args.sport.lower()}_game_context'
    params = {'game_date': f'eq.{args.date}', 'select': '*'}
    if args.game_id:
        params['game_id'] = f'eq.{args.game_id}'
    r = requests.get(f'{_SB}/rest/v1/{table}', headers=_H_READ, params=params, timeout=20)
    rows = r.json() if r.status_code == 200 else []
    if args.limit: rows = rows[:args.limit]

    print(f'=== ensemble_scorer · {args.sport} · {args.date} · {len(rows)} games ===\n')
    for ctx in rows:
        away = ctx.get('away_team', '?')
        home = ctx.get('home_team', '?')
        print(f'{away} @ {home}')
        d = score_game(args.sport, ctx)
        _dump_decision(d)
        print()


if __name__ == '__main__':
    main()
