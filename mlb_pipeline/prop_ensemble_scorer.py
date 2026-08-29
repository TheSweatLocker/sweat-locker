"""Prop ensemble scorer v1 (2026-08-17) — sport-universal, shadow-mode.

Analog of ensemble_scorer.py for props. Iterates signal_sources rows
where subject_scope IN ('prop', 'player_prop') and evaluates each
against today's props.

Reuses the game ensemble's TIER_THRESHOLDS, edge_weight, MAX_CLASS_SHARE,
and the signal_registry hit_rate lookup — plug-in signals get the same
treatment across game and prop surfaces.

**Shadow-mode only** — writes to prop_playbook_decisions, does NOT touch
mlb_pipeline_props.tier or .conviction. Legacy scoring stays
authoritative until 14d of shadow data shows playbook meets or beats
legacy hit-rate. See project_prop_playbook_design_817.

CLI:
  python prop_ensemble_scorer.py                        # all sports, today
  python prop_ensemble_scorer.py --sport MLB
  python prop_ensemble_scorer.py --sport NFL --date 2026-09-07
  python prop_ensemble_scorer.py --dry-run              # print, don't write
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
# 2026-08-19: Upsert on natural key (sport,game_date,player,prop_type,
# direction,prop_line) added in migration 20260819_prop_playbook_decisions_unique.
# Previously INSERT-only, so every rescore doubled row count (248 rows for
# 124 props on 8/18 after morning rescore). merge-duplicates now safe.
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Reuse game ensemble's edge weighting + class-balance rule
from ensemble_scorer import MAX_CLASS_SHARE, edge_weight

# Prop-specific tier thresholds (2026-08-17). Game ensemble aggregates
# across ML/RL/Total candidates so top scores frequently hit 1.0-3.0.
# Props evaluate 1 prop_row against ~15 signals of which typically 3-6
# fire, and each seed signal's contribution is small (~0.10). Real
# multi-signal props today land 0.25-0.50 range — tuning tier bars
# to match that distribution.
#
# 2026-08-19 recal: 7d shadow showed playbook downgraded 7 legacy STRONG
# picks to LEAN — those 7 went 6-1 (86%) yesterday. Downgrade cluster
# scored 0.17-0.23, below the old 0.25 STRONG bar. Lowering STRONG bar
# to 0.18 (still req 2 classes + 0.10 margin) captures the miss cluster
# without opening the gate wide. Kept LEAN + PRIME bars unchanged; PRIME
# threshold gets its own audit once we have 30d of playbook data.
# 2026-08-24: PRIME threshold raised from 0.40 → 0.60 based on 30d
# BACK PRIME backtest showing non-monotonic hit rate:
#   score 0.40-0.50: 54.5% (n=22, +0.92u)
#   score 0.50-0.60: 62.5% (n=16, +3.10u)
#   score 0.60-0.70: 50.0% (n=20, -0.90u)  ← DEAD ZONE
#   score 0.70+:     70.0% (n=10, +3.37u)
# Raising min_score to 0.60 kills the below-0.60 rollercoaster while
# keeping the strong 0.70+ zone tiering as PRIME. Below-0.60 winners
# fall to STRONG (still actionable) — user still gets exposure but at
# lower unit size. Reweight after 30d of the tightened cohort.
PROP_TIER_THRESHOLDS = {
    'PRIME':  {'min_score': 0.60, 'min_classes': 3, 'min_margin': 0.20, 'min_signals': 4},
    'STRONG': {'min_score': 0.18, 'min_classes': 2, 'min_margin': 0.10, 'min_signals': 3},
    'LEAN':   {'min_score': 0.05, 'min_classes': 1, 'min_margin': 0.03, 'min_signals': 1},
}
# 2026-08-28: `min_signals` gate added alongside `min_classes`. The old
# breadth-only check let a STRONG through with just 2 signals if they
# happened to span 2 classes — coverage_audit watchdog flagged 5 such
# picks tonight (Emerson Hancock ks_under, Jared Jones er_under, etc.)
# as thin. Depth gate now enforces the SIGNAL_FRAMEWORK "3+ signals for
# STRONG" reality-floor.

# Sport → prop table + game context table
PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
    'NHL': 'nhl_pipeline_props',
    'NBA': 'nba_pipeline_props',
}
CTX_TABLE = {
    'MLB': 'mlb_game_context',
    'NFL': 'nfl_game_context',
    'NHL': 'nhl_game_context',
    'NBA': 'nba_game_context',
}


# ═══════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PropContribution:
    signal_key: str
    signal_class: str
    side: str          # BACK | FADE
    weight: float
    strength: float
    n: int
    contribution: float
    display_prose: str


@dataclass
class PropDecision:
    sport: str
    game_date: str
    game_id: Optional[str]
    player_name: str
    prop_type: str
    direction: str      # over | under
    prop_line: Optional[float]
    tier: str           # PRIME | STRONG | LEAN | PASS
    conviction: int
    side: str           # BACK | FADE | PASS
    score: float
    margin: float
    contributions: list[PropContribution]
    class_share: dict


# ═══════════════════════════════════════════════════════════════════════
# CACHES (per-run) — signal_sources + signal_registry
# ═══════════════════════════════════════════════════════════════════════

_SOURCES_CACHE: dict = {}  # keyed by sport
_REGISTRY_CACHE: dict = {}

# 2026-08-22 META FIX (silent-bug audit finding #20):
# Log signal_expr eval failures once per (signal_key, kind) per run so
# broken signals become visible instead of silently returning None.
_LOGGED_EVAL_ERRORS: set = set()


def _record_eval_error(signal_key: str, kind: str, exc: Exception, source_row: dict) -> None:
    """Log a signal_expr eval failure exactly once per (signal, kind) per run."""
    dedup_key = (signal_key, kind, type(exc).__name__)
    if dedup_key in _LOGGED_EVAL_ERRORS:
        return
    _LOGGED_EVAL_ERRORS.add(dedup_key)
    expr = (source_row.get(f'{kind.split("_")[0]}_expr') or
            source_row.get('condition_expr') or '')
    print(f'  ⚠ signal[{signal_key}] {kind} eval failed: '
          f'{type(exc).__name__}: {str(exc)[:100]}  expr={str(expr)[:100]}')


def _load_prop_sources(sport: str) -> list[dict]:
    """Fetch prop signal_sources rows for a sport (cached per-run)."""
    if sport in _SOURCES_CACHE:
        return _SOURCES_CACHE[sport]
    try:
        r = requests.get(f'{SB}/rest/v1/signal_sources',
                         headers=H_READ,
                         params={'sport': f'eq.{sport}',
                                 'enabled': 'eq.true',
                                 'subject_scope': 'in.(prop,player_prop)',
                                 'select': '*'},
                         timeout=10)
        rows = r.json() if r.status_code == 200 else []
    except Exception:
        rows = []
    _SOURCES_CACHE[sport] = rows
    return rows


def _load_signal_registry() -> dict:
    """(signal_key, sport) → {hit_rate_pct, sample_n, tier}. Cached."""
    if _REGISTRY_CACHE:
        return _REGISTRY_CACHE
    try:
        r = requests.get(f'{SB}/rest/v1/signal_registry',
                         headers=H_READ,
                         params={'select': 'signal_key,sport,hit_rate_pct,sample_n,tier'},
                         timeout=10)
        rows = r.json() if r.status_code == 200 else []
    except Exception:
        rows = []
    for row in rows:
        _REGISTRY_CACHE[(row['signal_key'], row.get('sport') or 'MLB')] = row
    return _REGISTRY_CACHE


def _resolve_weight(source_row: dict) -> tuple[Optional[float], int, Optional[str]]:
    """Return (hit_rate_fraction, sample_n, tier) from registry lookup
    with inline row fallback. Same logic as game ensemble."""
    reg = _load_signal_registry()
    key = source_row.get('signal_key')
    sport = source_row.get('sport') or 'MLB'
    r = reg.get((key, sport))
    if r and r.get('hit_rate_pct') is not None:
        hr = float(r['hit_rate_pct']) / 100.0
        n = int(r.get('sample_n') or 0)
        tier = r.get('tier')
        return hr, n, tier
    # Fallback to inline row fields (backfill_signal_tiers pattern)
    inline_hr = source_row.get('hit_rate_pct')
    inline_n = source_row.get('sample_n') or 0
    if inline_hr is not None:
        return float(inline_hr) / 100.0, int(inline_n), None
    return None, 0, None


# ═══════════════════════════════════════════════════════════════════════
# SIGNAL EVALUATION
# ═══════════════════════════════════════════════════════════════════════

def _safe_eval(expr: str, env: dict):
    """Evaluate condition/side/strength expressions in a restricted namespace.
    Same pattern as the game ensemble — no imports, only builtins."""
    if not expr: return None
    return eval(expr, {'__builtins__': __builtins__, 'min': min, 'max': max,
                       'abs': abs, 'int': int, 'float': float, 'str': str,
                       'sum': sum, 'len': len, 'any': any, 'all': all,
                       'round': round, 'None': None, 'True': True, 'False': False,
                       'isinstance': isinstance, 'dict': dict, 'list': list, 'bool': bool},
                env)


def _coerce_prop(p: dict) -> dict:
    """Normalize prop row for signal evaluation. PostgREST usually parses
    JSONB into dicts but sometimes returns strings; parse if needed."""
    signals = p.get('signals')
    if isinstance(signals, str):
        try: p['signals'] = json.loads(signals)
        except Exception: p['signals'] = {}
    return p


def _matches_market(source_row: dict, prop: dict) -> bool:
    """Filter signals by market_scope. '*' matches all props.
    'hits' matches hits_over/hits_under. 'pitcher' matches bb/ha/ks/outs/er."""
    scope = (source_row.get('market_scope') or '').lower()
    if not scope or scope == '*': return True
    prop_type = (prop.get('prop_type') or '').lower()
    if scope == 'pitcher':
        return any(prop_type.startswith(p) for p in ('bb_', 'ha_', 'ks_', 'outs_', 'er_'))
    if scope == 'hits':
        return prop_type.startswith('hits_')
    if scope == prop_type: return True
    if prop_type.startswith(scope + '_'): return True
    return False


def _evaluate_signal(source_row: dict, ctx: dict, p: dict) -> Optional[PropContribution]:
    """Run one signal against one prop. Returns a Contribution or None."""
    if not _matches_market(source_row, p): return None

    # Environment for expressions: ctx + p available as top-level names
    p = _coerce_prop(p)
    env = {'ctx': _CtxProxy(ctx), 'p': p}
    signal_key = source_row.get('signal_key') or '?'
    try:
        matched = _safe_eval(source_row.get('condition_expr') or '', env)
    except Exception as _e:
        # 2026-08-22 META FIX: log eval failures once per signal per run.
        # Prior code silently swallowed every broken expression — a single
        # `ctx.foo` where the ctx column is named `ctx.foo_bar` returned
        # None with no trace. The silent-bug audit found 19 dead signals
        # hidden this way (findings #4-#18). Log makes future breakage
        # visible without spamming (dedup via _LOGGED_EVAL_ERRORS set).
        _record_eval_error(signal_key, 'condition', _e, source_row)
        return None
    if not matched: return None

    try:
        side_raw = _safe_eval(source_row.get('side_expr') or '""', env)
        strength = _safe_eval(source_row.get('strength_expr') or '0.5', env)
    except Exception as _e:
        _record_eval_error(signal_key, 'side_or_strength', _e, source_row)
        return None

    side = str(side_raw).upper() if side_raw else 'PASS'
    if side not in ('BACK', 'FADE'): return None

    try: strength = max(0.0, min(1.0, float(strength)))
    except (TypeError, ValueError): return None
    if strength <= 0: return None

    hr, n, reg_tier = _resolve_weight(source_row)
    w = edge_weight(hr, n, reg_tier)
    if w <= 0: return None

    # Render prose from template.
    # 2026-08-23: signal_sources templates like projection_edge_supports use
    # tokens like `{_edge_at_book}` which live at p["signals"]["_edge_at_book"],
    # not at the top level of p. Previously .format() KeyError'd and shipped
    # the raw template to users (visible bug: "{_edge_at_book} edge at book
    # line" in the app). Flatten p["signals"] into the format namespace so
    # any signals-JSONB field is a valid template token. Signals fields take
    # precedence over ctx/p top-level in the rare collision case (signal
    # authors wrote the template with signals fields in mind).
    prose = source_row.get('display_prose_template') or ''
    _sig = p.get('signals') if isinstance(p.get('signals'), dict) else {}
    _fmt = {**ctx, **p, **_sig}
    # 2026-08-23: matched-pitcher helpers. Templates for pitcher_recent_cold,
    # pitcher_xera_high, pitcher_vs_team_tagged_history previously hardcoded
    # threshold values (">=5.50", ">=5.0") in prose because the signal fires
    # on EITHER home or away pitcher and the template can't conditional on
    # which side. Result: Bryce Miller card showed "Pitcher L3 ERA 5.50+"
    # (generic threshold) instead of his real 8.04. Fix: inject computed
    # `pitcher_l3_era` / `pitcher_xera` / `pitcher_vs_team_era` fields based
    # on which pitcher the prop belongs to, so authors can write
    # {pitcher_l3_era:.2f} and get the real value.
    _player = p.get('player_name', '')
    _home_p = ctx.get('home_pitcher', '')
    _away_p = ctx.get('away_pitcher', '')
    if _player and _player == _home_p:
        _fmt['pitcher_l3_era']       = ctx.get('home_pitcher_last_3_era')
        _fmt['pitcher_xera']         = ctx.get('home_sp_xera')
        _fmt['pitcher_vs_team_era']  = ctx.get('home_pitcher_vs_team_era')
        _fmt['pitcher_first_inn_era'] = ctx.get('home_first_inning_era')
    elif _player and _player == _away_p:
        _fmt['pitcher_l3_era']       = ctx.get('away_pitcher_last_3_era')
        _fmt['pitcher_xera']         = ctx.get('away_sp_xera')
        _fmt['pitcher_vs_team_era']  = ctx.get('away_pitcher_vs_team_era')
        _fmt['pitcher_first_inn_era'] = ctx.get('away_first_inning_era')
    # Round any numeric values to 2 decimals so raw floats like 0.3499999
    # don't leak. Preserves strings + None as-is.
    for _k, _v in list(_fmt.items()):
        if isinstance(_v, float):
            _fmt[_k] = round(_v, 2)
    try:
        prose = prose.format(**_fmt)
    except (KeyError, IndexError, ValueError):
        pass  # missing key — try per-token substitution below so partial
              # substitutions still happen instead of shipping raw template
    # Belt-and-suspenders: any {token} that survived (either because format
    # raised or because a token had special format chars) gets replaced with
    # its lookup value or stripped. Prior version shipped raw `{_edge_at_book}`
    # to users on the app when the format namespace didn't include it.
    if '{' in prose:
        import re
        def _sub(m):
            key = m.group(1).split(':')[0].split('!')[0]
            v = _fmt.get(key)
            if v is None: return ''  # unknown token → drop it
            return str(v)
        prose = re.sub(r'\{([^{}]+)\}', _sub, prose)
        # Clean up double-spaces + dangling em-dashes/hyphens left by drops
        prose = re.sub(r'\s+', ' ', prose)
        prose = re.sub(r'\s*[—–-]\s*$', '', prose).strip()
        # If prose collapsed to just punctuation, fall back to signal_key
        if not prose or all(c in ' -—–.,;:•' for c in prose):
            prose = source_row.get('signal_key', '')

    return PropContribution(
        signal_key=source_row.get('signal_key', ''),
        signal_class=source_row.get('class', ''),
        side=side,
        weight=w, strength=strength, n=n,
        contribution=round(w * strength, 4),
        display_prose=prose[:180],
    )


class _CtxProxy:
    """Wraps ctx dict so `ctx.field_name` works in condition_expr (mirrors
    game ensemble's ctx access pattern). Returns None for missing fields."""
    def __init__(self, ctx: dict): self._d = ctx or {}
    def __getattr__(self, name): return self._d.get(name)


# ═══════════════════════════════════════════════════════════════════════
# TOP-LEVEL: score one prop
# ═══════════════════════════════════════════════════════════════════════

def score_prop(sport: str, ctx: dict, prop: dict) -> PropDecision:
    """Score a single prop across all applicable prop signals.

    Returns PropDecision with tier=PASS if no signals fire strongly
    enough. Legacy fields (tier, conviction, refit_conviction) are
    NOT read here — they can enter as their own plug-in signals via
    signal_sources rows (refit_conviction_strong etc.)."""
    sources = _load_prop_sources(sport)
    contribs: list[PropContribution] = []
    for src in sources:
        c = _evaluate_signal(src, ctx, prop)
        if c is not None:
            contribs.append(c)

    if not contribs:
        return PropDecision(
            sport=sport, game_date=prop.get('game_date',''),
            game_id=prop.get('game_id'), player_name=prop.get('player_name',''),
            prop_type=prop.get('prop_type',''), direction=prop.get('direction',''),
            prop_line=prop.get('prop_line'),
            tier='PASS', conviction=50, side='PASS', score=0.0, margin=0.0,
            contributions=[], class_share={},
        )

    # Aggregate per side (BACK vs FADE)
    per_side: dict[str, list[PropContribution]] = defaultdict(list)
    for c in contribs:
        per_side[c.side].append(c)

    scored: list[tuple[str, float, list[PropContribution], dict, int]] = []
    for side in ('BACK', 'FADE'):
        chips = per_side.get(side, [])
        raw_total = sum(c.contribution for c in chips)
        class_share: dict[str, float] = defaultdict(float)
        for c in chips:
            class_share[c.signal_class] += c.contribution
        adjusted = raw_total
        if raw_total > 0:
            max_allowed = raw_total * MAX_CLASS_SHARE
            for cls, share in class_share.items():
                if share > max_allowed:
                    adjusted -= (share - max_allowed) * 0.5
        classes_fired = sum(1 for v in class_share.values() if v > 0)
        scored.append((side, adjusted, chips, dict(class_share), classes_fired))

    scored.sort(key=lambda t: -t[1])
    winner_side, win_score, win_chips, win_shares, win_classes = scored[0]
    runner_score = scored[1][1] if len(scored) > 1 else 0.0
    margin = win_score - runner_score

    tier = 'PASS'
    win_unique_signals = len({c.signal_key for c in win_chips if c.signal_key})
    if win_score >= PROP_TIER_THRESHOLDS['LEAN']['min_score']:
        tier = 'LEAN'
        for candidate_tier in ('PRIME', 'STRONG'):
            th = PROP_TIER_THRESHOLDS[candidate_tier]
            if (win_score >= th['min_score']
                    and win_classes >= th['min_classes']
                    and win_unique_signals >= th.get('min_signals', 1)
                    and margin >= th['min_margin']):
                tier = candidate_tier
                break

    # 2026-08-19: PRIME breadth lane (mirrors game ensemble). A prop with
    # 5+ agreeing source classes AND margin ≥ 0.15 earns PRIME even at
    # score 0.30-0.40. Rewards broad signal confluence over single-source
    # magnitude — a Ks-under with matchup, splits, park, ump, refit all
    # pointing same way should be PRIME even if no single lens screams.
    if tier == 'STRONG' and win_score >= 0.30 and win_classes >= 5 and margin >= 0.15:
        tier = 'PRIME'

    # 2026-08-19: prop conviction rewritten to match game conviction
    # widening (mirror of ensemble_scorer.py). Prior formula clustered
    # 55-70 because it only used win_score. Now also weights margin and
    # class-confluence — a prop with a big single-source signal (score
    # 0.6, margin 0.6, only 1 class) gets 70, but a prop backed by 4+
    # classes with same margin gets ~85. Reflects data-foundation
    # strength, not just raw score magnitude.
    #   base:          raw prop score          →  50-80 (score cap 0.75)
    #   margin_boost:  decisiveness            →  +0-15
    #   classes_boost: source class confluence →  +0-10 (1 class = 0)
    base = 50 + min(win_score * 40, 30)
    margin_boost = min(max(margin, 0) * 25, 15)
    classes_boost = min(max(0, win_classes - 1) * 3, 10)
    conviction = int(round(base + margin_boost + classes_boost))
    conviction = max(45, min(97, conviction))

    # 2026-08-21: BOOK-EDGE RECALIBRATION (ported from legacy
    # generate_props.py::recalibrate_props_with_book_lines).
    # Audit revealed that playbook was blindly promoting props to PRIME
    # based on refit_conviction=100 signals WITHOUT checking whether the
    # book had already priced in the same signal. Legacy scorer had this
    # check and correctly demoted; playbook overrode legacy and lost.
    # Specific case: Grayson Rodriguez outs_under 15.5 — projection 13.8
    # vs line 15.5 = only +1.7 outs edge (thin). Legacy demoted conv
    # 64→48 (SKIP). Playbook cranked to PRIME 95 anyway → picked BACK →
    # Grayson pitched 21 outs → LOSS.
    #
    # 21-day backtest of legacy-vs-playbook disagreements confirmed:
    #   Legacy STRONG → Playbook LEAN: 9-1 (90% win) — playbook demoted winners
    #   Legacy SKIP → Playbook LEAN/STRONG: 24-29 (45% win) — playbook picked losers
    #
    # This function replicates legacy's edge-band multiplier: if projection
    # vs book edge is THIN (0.5 outs for K's, 1.0 for outs, etc), the
    # conviction gets multiplied down. Same math legacy has used since
    # 2026-05-29 with proven predictive value.
    conviction = _apply_book_edge_calibration(conviction, prop, winner_side)

    # Re-derive tier from post-recal conviction. If book demote pushed us
    # below LEAN, drop to PASS (equivalent to legacy's SKIP after recal).
    if conviction < 55:
        tier = 'PASS'
    elif tier == 'PRIME' and conviction < 75:
        tier = 'STRONG'
    elif tier == 'STRONG' and conviction < 60:
        tier = 'LEAN'

    # 2026-08-22 COLD-STREAK GUARD: if the player has been ice cold recently
    # AND the season hit rate is low AND we're saying BACK on the OVER — that
    # combination was landing as LEAN c=55 tonight (Drew Anderson outs_over
    # 14.5 with L10=1/10, season=3.3% got LEAN, Ryan Johnson outs_over 15.5
    # at L10=2/10 season=13.3% also LEAN). The scorer treated these as
    # "no signal" (defaulted mid-band) instead of "strong FADE signal".
    #
    # Fix: force PASS when cold-and-cold-and-BACKing-OVER hits all three.
    # Downstream Jerry synth will then either skip (PASS tier is not in
    # tier-gate) or render a template chip noting the cold streak.
    try:
        l10_raw = prop.get('player_l10_hit_count')
        season_raw = prop.get('player_season_hit_pct')
        direction = (prop.get('direction') or '').lower()
        if (l10_raw is not None and season_raw is not None
                and float(l10_raw) <= 2
                and float(season_raw) < 25
                and winner_side == 'BACK'
                and direction == 'over'):
            tier = 'PASS'
            conviction = max(30, conviction - 25)
            winner_side = 'PASS'
    except (TypeError, ValueError):
        pass  # missing L10 data — normal path

    # 2026-08-22 ROOKIE FLOOR GUARD: if the prop has NO L10 hit count AND
    # NO season hit rate populated at all, the player has no MLB game log
    # to grade the line against. This is a rookie / callup case (tonight:
    # Andrew Painter er_over 2.5 landed as PRIME conv=94 with zero crossing
    # history — ensemble signals were fine but we had no way to know how
    # often he actually hits 2.5+ ER because he's never pitched enough MLB
    # innings). Cap tier at LEAN — signals still support the pick,
    # but PRIME/STRONG shouldn't ride on projections alone without a
    # historical baseline. LEAN keeps it in the pool without top-tier
    # over-confidence. Enrichment path (backfill_prop_lookback) tries
    # exact + diacritic-normalized names — if BOTH still return empty,
    # it's genuinely a no-history player, not a lookup miss.
    try:
        l10_val = prop.get('player_l10_hit_count')
        season_val = prop.get('player_season_hit_pct')
        if l10_val is None and season_val is None and tier in ('PRIME', 'STRONG'):
            tier = 'LEAN'
            conviction = min(conviction, 60)
    except Exception:
        pass

    # 2026-08-29 HITS_OVER 0.5 SHARP EXCLUSION: batter hits O 0.5 is
    # Ledger material only (parlay legs), never a Sharp Card standalone.
    # These props hit ~60-70% at real juice (-180 to -280) = break-even
    # at BEST; only positive-EV as combined parlay legs where correlated
    # hits multiply. Force COVERAGE regardless of scorer output — the
    # Ledger generator picks them up separately as parlay candidates.
    # Prior enforcement lived in backfill_prop_lookback.py but that runs
    # AFTER the scorer, so scorer output kept overwriting the demotion.
    # Row still lands in DB (graded, tracked); just filtered off Sharp
    # Card (which selects tier IN ('PRIME','STRONG')).
    if prop.get('prop_type') == 'hits_over' and (prop.get('direction') or '').lower() == 'over':
        try:
            if float(prop.get('prop_line') or 99) <= 0.5:
                tier = 'COVERAGE'
                conviction = 0
                winner_side = 'PASS'
        except (TypeError, ValueError):
            pass

    return PropDecision(
        sport=sport, game_date=prop.get('game_date',''),
        game_id=prop.get('game_id'), player_name=prop.get('player_name',''),
        prop_type=prop.get('prop_type',''), direction=prop.get('direction',''),
        prop_line=prop.get('prop_line'),
        tier=tier, conviction=conviction, side=winner_side,
        score=round(win_score, 3), margin=round(margin, 3),
        contributions=sorted(win_chips, key=lambda c: -c.contribution),
        class_share=win_shares,
    )


# Ported from generate_props.py::EDGE_BANDS (line 3329). Same tuned bands
# legacy has used since 2026-05-29. Each entry: (edge_threshold, multiplier, label).
# Walked top-down: first threshold met wins. Anything below smallest → 0.30 (no edge).
_BOOK_EDGE_BANDS = {
    'ks':   [(1.5, 1.00, 'real edge'), (1.0, 0.90, 'moderate edge'),
             (0.5, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
    'ha':   [(1.5, 1.00, 'real edge'), (1.0, 0.90, 'moderate edge'),
             (0.5, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
    'outs': [(3.0, 1.00, 'real edge'), (2.0, 0.90, 'moderate edge'),
             (1.0, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
    'bb':   [(0.5, 1.00, 'real edge'), (0.3, 0.90, 'moderate edge'),
             (0.1, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
    'er':   [(1.0, 1.00, 'real edge'), (0.5, 0.90, 'moderate edge'),
             (0.3, 0.75, 'thin edge'), (0.0, 0.55, 'minimal edge')],
}
_PROP_PROJ_KEY = {
    'ks': '_projected_ks', 'bb': '_projected_bb',
    'ha': '_projected_hits', 'outs': '_projected_outs', 'er': '_projected_er',
}


def _apply_book_edge_calibration(conviction: int, prop: dict, winner_side: str) -> int:
    """Demote conviction when book edge on the picked direction is thin.
    Returns the recalibrated conviction. If we can't compute (missing
    projection or book line), returns conviction unchanged.

    Reads prop.signals dict which is populated by legacy scorer with
    projection values (_projected_ks etc.) — since legacy runs first in
    the pipeline, these fields are available for us to use.
    """
    ptype = prop.get('prop_type') or ''
    group = ptype.split('_')[0]  # ks / bb / ha / outs / er
    if group not in _BOOK_EDGE_BANDS:
        return conviction
    signals = prop.get('signals') or {}
    proj = signals.get(_PROP_PROJ_KEY.get(group))
    if proj is None:
        return conviction  # no projection available — can't calibrate
    book_line = prop.get('book_line') or prop.get('prop_line')
    if book_line is None:
        return conviction
    try:
        proj_f = float(proj); book_f = float(book_line)
    except (TypeError, ValueError):
        return conviction
    direction = (prop.get('direction') or '').lower()
    # Edge from bettor's perspective on the picked direction.
    # If we're picking UNDER, positive edge = book line > projection.
    # If we're picking OVER, positive edge = projection > book line.
    is_under = direction == 'under'
    edge = (book_f - proj_f) if is_under else (proj_f - book_f)
    bands = _BOOK_EDGE_BANDS[group]
    mult = 0.30
    for thr, m, _ in bands:
        if edge >= thr:
            mult = m
            break
    if mult >= 1.0:
        return conviction  # no penalty
    new_conv = max(0, int(round(conviction * mult)))
    return new_conv


# ═══════════════════════════════════════════════════════════════════════
# WRITER
# ═══════════════════════════════════════════════════════════════════════

def _american_to_implied_prob(odds) -> Optional[float]:
    """American odds → implied probability (0-100). Fair value, no vig removed."""
    if odds is None: return None
    try: o = int(odds)
    except (TypeError, ValueError): return None
    return 100.0 * (100.0 / (o + 100)) if o >= 0 else 100.0 * (abs(o) / (abs(o) + 100.0))


def _compute_edge_and_rec(decision: PropDecision, prop: dict) -> tuple:
    """Prop Playbook V2 market-check gate (2026-08-20 per user vision).

    Compare our playbook_conviction (0-100) to the book's implied probability.
    If our score materially beats implied, that's edge — recommend BACK.
    If the market has priced our edge away, no bet regardless of raw score.

    Recommendation ladder (user-approved thresholds):
      BACK      edge_pp >= +8  AND playbook_conviction >= 65
      LEAN      edge_pp >= +3  AND playbook_conviction >= 55
      NO_PLAY   else (still shown in DB for audit but hidden from UI)

    Returns (market_implied_prob, edge_pp, recommendation).

    2026-08-20 bug fix: decision.side is 'BACK' or 'FADE' (verdict), NOT
    'over' or 'under'. Direction of the prop line comes from prop.direction
    (over/under) — that's what selects which book odds side to use. Prior
    version compared decision.side.lower() to 'over'/'under' which never
    matched → all 30 fresh V2 rows landed with implied=None → all NO_PLAY.

    2026-08-24 SECOND BUG FIX — FADE edge_pp was reading the WRONG side's
    odds. Original assumption: "prop.direction is what we're betting." That's
    true for BACK decisions but FALSE for FADE — a FADE reverses direction.
    Bug caused ALL FADE decisions in prop_playbook_decisions to have wrong
    market_implied_prob + wrong edge_pp. Empirical proof: 30d grading
    showed higher FADE edge_pp = WORSE hit rate (36.4% at 30+ edge vs
    54.8% at 0-10 edge). Not a signal-quality problem — a math artifact
    from reading opposite-side odds on FADE. See
    [[project_playbook_fade_broken_824]].
    """
    # Which direction are we actually BACKING? BACK = listed direction,
    # FADE = opposite direction. edge_pp must be computed vs the odds of
    # the direction we're betting, not the listed one.
    listed_direction = (prop.get('direction') or '').lower()
    verdict = (getattr(decision, 'side', 'BACK') or 'BACK').upper()
    if verdict == 'FADE':
        backed_direction = 'under' if listed_direction == 'over' else 'over'
    else:
        backed_direction = listed_direction
    over_o = prop.get('book_over_odds')
    under_o = prop.get('book_under_odds')
    odds = over_o if backed_direction == 'over' else under_o if backed_direction == 'under' else None
    implied = _american_to_implied_prob(odds)
    if implied is None or decision.conviction is None:
        return (None, None, 'NO_PLAY')
    edge_pp = round(decision.conviction - implied, 2)
    conv = decision.conviction
    if edge_pp >= 8 and conv >= 65:
        rec = 'BACK'
    elif edge_pp >= 3 and conv >= 55:
        rec = 'LEAN'
    else:
        rec = 'NO_PLAY'
    return (round(implied, 2), edge_pp, rec)


def write_decision(prop: dict, decision: PropDecision, dry_run: bool = False) -> bool:
    """Insert one row into prop_playbook_decisions.

    Captures legacy tier/conviction/refit alongside playbook output
    so audit can compare them without joins.

    2026-08-20: writes V2 fields (market_implied_prob, edge_pp,
    recommendation) alongside legacy. Once app cutover flips, these
    become the authoritative UI outputs and legacy_* fields become
    audit-only."""
    market_implied, edge_pp, rec = _compute_edge_and_rec(decision, prop)
    payload = {
        'sport': decision.sport,
        'game_date': decision.game_date,
        'game_id': decision.game_id,
        'player_name': decision.player_name,
        'prop_type': decision.prop_type,
        'direction': decision.direction,
        'prop_line': decision.prop_line,
        'playbook_tier': decision.tier,
        'playbook_conviction': decision.conviction,
        'playbook_side': decision.side,
        'playbook_score': decision.score,
        'playbook_margin': decision.margin,
        'playbook_sources': [
            {'signal_key': c.signal_key, 'class': c.signal_class,
             'side': c.side, 'weight': round(c.weight, 3), 'n': c.n,
             'strength': round(c.strength, 3),
             'contribution': round(c.contribution, 3),
             'prose': c.display_prose}
            for c in decision.contributions[:8]
        ],
        'legacy_tier': prop.get('tier'),
        # Cast to int — schema is INT but prop table stores conviction as numeric
        'legacy_conviction': int(float(prop['conviction'])) if prop.get('conviction') is not None else None,
        'legacy_refit_conviction': int(float(prop['refit_conviction'])) if prop.get('refit_conviction') is not None else None,
        # V2 outputs (nullable until migration lands)
        'market_implied_prob': market_implied,
        'edge_pp': edge_pp,
        'recommendation': rec,
    }
    if dry_run: return True
    # Try full V2 payload first. If migration 20260820_prop_playbook_v2_fields
    # hasn't been applied, strip V2 fields and retry so shadow writes keep
    # working. Once user applies migration, V2 fields land + retry never fires.
    try:
        pr = requests.post(
            f'{SB}/rest/v1/prop_playbook_decisions'
            f'?on_conflict=sport,game_date,player_name,prop_type,direction,prop_line',
            headers=H_WRITE, json=payload, timeout=10)
        if pr.status_code == 400 and 'recommendation' in (pr.text or ''):
            # V2 migration not applied yet — retry without new columns
            legacy_payload = {k: v for k, v in payload.items()
                              if k not in ('market_implied_prob','edge_pp','recommendation')}
            pr = requests.post(
                f'{SB}/rest/v1/prop_playbook_decisions'
                f'?on_conflict=sport,game_date,player_name,prop_type,direction,prop_line',
                headers=H_WRITE, json=legacy_payload, timeout=10)
        if pr.status_code not in (200, 201, 204):
            print(f'    ✗ write {decision.player_name} {decision.prop_type}: {pr.status_code} {pr.text[:150]}')
            return False
        return True
    except Exception as e:
        print(f'    ✗ write failed: {e}')
        return False


# ═══════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════

def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _load_decisions_as_props(sport: str, game_date: str) -> list[dict]:
    """Reconstruct minimal prop dicts from existing prop_playbook_decisions.

    Used by --refresh-existing to re-score decisions whose source prop has
    been pruned from the live props table. Every decision row already
    carries the identifying fields (player, prop_type, direction, line,
    game_id) needed to run the scorer — we just wrap them in prop-shaped
    dicts so score_prop() can consume them unchanged.
    """
    # NOTE: prop_playbook_decisions carries only the identifying fields plus
    # scoring output. It doesn't carry player_team or book odds (those live on
    # mlb_pipeline_props). That's fine for re-scoring: signals read team from
    # ctx.home_pitcher/away_pitcher matching, and the scorer's book-edge step
    # is a NO-OP when odds are absent. Downstream write_decision just persists
    # tier/conviction — book odds columns default to NULL.
    r = requests.get(f'{SB}/rest/v1/prop_playbook_decisions',
                     headers=H_READ,
                     params={'sport': f'eq.{sport}',
                             'game_date': f'eq.{game_date}',
                             'select': 'game_id,player_name,prop_type,direction,prop_line'},
                     timeout=30)
    if r.status_code != 200:
        print(f'  [{sport}] refresh-existing query failed {r.status_code}: {r.text[:200]}')
        return []
    rows = r.json() or []
    return [{**row, 'game_date': game_date} for row in rows]


def run_for_sport(sport: str, game_date: str, dry_run: bool = False,
                  limit: Optional[int] = None, refresh_existing: bool = False) -> dict:
    table = PROPS_TABLE.get(sport)
    ctx_table = CTX_TABLE.get(sport)
    if not table or not ctx_table:
        print(f'  [{sport}] no props/ctx table registered — skip')
        return {'scored': 0, 'passed': 0, 'ranked': {}}

    # Load ctx bundle per game_id
    r = requests.get(f'{SB}/rest/v1/{ctx_table}?game_date=eq.{game_date}&select=*',
                     headers=H_READ, timeout=15)
    ctxs = {c.get('game_id'): c for c in (r.json() if r.status_code == 200 else [])}

    # Load props — from live table + (optionally) union with existing decisions
    r = requests.get(f'{SB}/rest/v1/{table}',
                     headers=H_READ,
                     params={'game_date': f'eq.{game_date}',
                             'select': '*', 'limit': limit or 500},
                     timeout=30)
    live_props = r.json() if r.status_code == 200 else []

    if refresh_existing:
        # Union live props with previously-decided props whose source row
        # may have been pruned. Dedup on natural key so we don't score twice.
        decision_props = _load_decisions_as_props(sport, game_date)
        seen = {(p.get('game_id'), p.get('player_name'), p.get('prop_type'),
                 p.get('direction'), p.get('prop_line')) for p in live_props}
        added = 0
        for dp in decision_props:
            key = (dp.get('game_id'), dp.get('player_name'), dp.get('prop_type'),
                   dp.get('direction'), dp.get('prop_line'))
            if key not in seen:
                live_props.append(dp)
                seen.add(key)
                added += 1
        print(f'  [{sport}] refresh-existing added {added} orphaned decisions to score set')
    props = live_props

    if not props:
        print(f'  [{sport}] no props on {game_date}')
        return {'scored': 0, 'passed': 0, 'ranked': {}}

    n_sources = len(_load_prop_sources(sport))
    print(f'  [{sport}] scoring {len(props)} props against {n_sources} prop signals')

    scored = 0; passed = 0
    ranked = defaultdict(int)
    for prop in props:
        ctx = ctxs.get(prop.get('game_id')) or {}
        try:
            d = score_prop(sport, ctx, prop)
        except Exception as e:
            print(f'    ✗ score failed for {prop.get("player_name")} {prop.get("prop_type")}: {e}')
            continue
        if d.tier == 'PASS':
            passed += 1
            # 2026-08-21: still write PASS decisions so stale prior rows
            # get OVERWRITTEN. Bug caught during audit: book_recalibration
            # correctly demoted Tanner Gordon outs_under 14.5 from PRIME
            # to PASS, but writer skipped → the old PRIME 97 row persisted
            # in DB and Sharp Card kept surfacing the losing pick. Now the
            # PASS overwrites the PRIME → app hides it (per NO_PLAY logic).
            write_decision(prop, d, dry_run=dry_run)
            continue
        write_decision(prop, d, dry_run=dry_run)
        scored += 1
        ranked[d.tier] += 1
        # Log strong picks so operator can eyeball parity vs legacy
        if d.tier in ('PRIME', 'STRONG'):
            legacy = f"legacy=[{prop.get('tier') or '-'}/{prop.get('refit_conviction') or prop.get('conviction') or '-'}]"
            top_signals = ' | '.join(c.signal_key for c in d.contributions[:3])
            print(f'    {d.tier:<6} {d.side:<4} {d.player_name[:22]:<22} {d.prop_type:<10} '
                  f'{d.direction:<5} conv={d.conviction} score={d.score}  {legacy}  [{top_signals}]')
    return {'scored': scored, 'passed': passed, 'ranked': dict(ranked)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=list(PROPS_TABLE.keys()))
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--limit', type=int)
    p.add_argument('--refresh-existing', action='store_true',
                   help='Also re-score any prop_playbook_decisions rows whose '
                        'source prop was pruned. Use after porting a new signal '
                        'mid-day so it retroactively touches already-scored props.')
    args = p.parse_args()

    gd = args.date or _et_today()
    sports = [args.sport] if args.sport else list(PROPS_TABLE.keys())
    mode = ' [REFRESH-EXISTING]' if args.refresh_existing else ''
    print(f'=== prop_ensemble_scorer · {gd} · {"/".join(sports)}{" [DRY]" if args.dry_run else ""}{mode} ===\n')

    total = {'scored': 0, 'passed': 0}
    for sport in sports:
        result = run_for_sport(sport, gd, dry_run=args.dry_run, limit=args.limit,
                                refresh_existing=args.refresh_existing)
        print(f'  [{sport}] scored={result["scored"]} passed={result["passed"]} ranked={result["ranked"]}\n')
        total['scored'] += result['scored']
        total['passed'] += result['passed']

    print(f'  total: {total["scored"]} decisions written, {total["passed"]} PASS')


if __name__ == '__main__':
    main()
