"""Tier-discipline publishing gate for total picks.

Derived from walk-forward tier-sliced backtest (n=872 graded games).
The gate decides whether a game is card-eligible based on:

  - ALL-3 model unity (v3+v4+jerry must agree on direction)
  - Composite gap magnitude (gap_proj must clear specific bands)
  - Direction-specific rules (OVER vs UNDER have different sweet spots)

Validated tier hit rates (walk-forward, season-to-date):

  OVER side:
    PRIME-OVER  : all-3 OVER + gap 2.0-3.0  → 71% (n=24)
    LEAN-OVER   : all-3 OVER + gap 0.5-1.5  → 65% (n=17)
    ELITE-OVER  : all-3 OVER + gap 1.5+ + snc>=3 → 67% (n=6, small)

  UNDER side:
    ELITE-UNDER : gap_proj <= -3.0 (any model agreement) → 64% (n=25)
    PRIME-UNDER : all-3 UNDER + gap <= -2.0  → 100% (n=2, tiny)

  Foundation:
    ALL-3 unanimous (any direction) → 58% (n=78)

Filter rules (in order of priority):

  PUBLISH = ELITE if:
    - Side+magnitude qualifies as one of the 71%/64% tiers above

  PUBLISH = STRONG if:
    - ALL-3 unanimous + gap_composite magnitude >= 1.5

  PUBLISH = LEAN if:
    - ALL-3 unanimous + gap magnitude 0.5-1.5

  SKIP if:
    - 2-of-3 only (coinflip in backtest)
    - mild magnitude (< 0.5 gap)
    - middle UNDER (gap -1.5 to -3.0)  [42-44% historical loser]

  IGNORE: signal_confluence_net.
    Walk-forward shows 0.9% feature importance + no monotonic predictive
    relationship. Was previously a primary resolver driver. Now context only.
"""
from dataclasses import dataclass
from typing import Optional, List, Dict, Any


# ── Dynamic weight adjustment (2026-08-07 GAP 5) ─────────────────────
# Multiplies baseline composite weights by tracker's recommended_weight,
# so degrading models auto-downweight and recovering models auto-upweight
# without manual reweight commits.
#
# Baseline weights come from backtest (stable, tuned). Multiplier comes
# from model_track_records.recommended_weight which is 0.3-2.5 range
# centered at 1.0 (0.3 = deeply losing, 1.0 = neutral, 2.5 = elite).
#
# Applying: w_final = w_baseline * tracker_multiplier
# Composite formulas already normalize by sum-of-weights, so the ratios
# are what matter.
#
# Cache: (sport, market) → {model_name: multiplier}. Loaded once per
# process; refresh via _refresh_tracker_multipliers().
_TRACKER_MULT_CACHE: Dict[tuple, Dict[str, float]] = {}

# Map composite-formula inputs → tracker model_name. Extensible per sport.
# Composite input → (sport, market, tracker_model_name)
_COMPOSITE_INPUT_TO_TRACKER = {
    # Spread composite
    ('MLB', 'spread', 'v4'):     ('MLB', 'ML', 'MODEL_SPREAD'),
    ('MLB', 'spread', 'jerry'):  ('MLB', 'ML', 'RESOLVER_SIDE'),
    # Total composite
    ('MLB', 'total', 'v4'):      ('MLB', 'TOTAL', 'MODEL_TOTAL'),
    ('MLB', 'total', 'jerry'):   ('MLB', 'TOTAL', 'RESOLVER'),
    # v3 and panel not separately tracked; multiplier defaults to 1.0
}


def _load_tracker_multipliers(sport: str, market: str) -> Dict[str, float]:
    """Fetch tracker recommended_weight per model for a (sport, market).
    Returns {model_name: multiplier}. Empty if tracker unreachable —
    composite falls back to baseline weights (safe).
    """
    key = (sport, market)
    if key in _TRACKER_MULT_CACHE:
        return _TRACKER_MULT_CACHE[key]

    import os, requests
    SB = os.environ.get('SUPABASE_URL')
    KEY = os.environ.get('SUPABASE_KEY')
    if not SB or not KEY:
        # Don't cache when env missing — allow retry once env loads
        return {}
    try:
        r = requests.get(
            f'{SB}/rest/v1/model_track_records',
            headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'},
            params={
                'sport': f'eq.{sport}',
                'market': f'eq.{market}',
                'select': 'model_name,bucket_window,recommended_weight,sample_n',
                'limit': '200',
            }, timeout=8,
        )
        rows = r.json() if r.status_code == 200 else []
    except Exception:
        # Don't cache network failures
        return {}

    # Prefer recency: 14d > 30d > 90d > lifetime, min n>=25
    priority = ['14d', '30d', '90d', 'lifetime']
    by_model: Dict[str, Dict[str, dict]] = {}
    for row in rows:
        m = row.get('model_name'); w = row.get('bucket_window')
        if not m or not w: continue
        by_model.setdefault(m, {})[w] = row

    out: Dict[str, float] = {}
    for model, windows in by_model.items():
        chosen = None
        for w in priority:
            r = windows.get(w)
            if r and (r.get('sample_n') or 0) >= 25:
                chosen = r; break
        if chosen is None:
            for w in priority:
                r = windows.get(w)
                if r: chosen = r; break
        if chosen is None: continue
        try:
            out[model] = float(chosen.get('recommended_weight') or 1.0)
        except (TypeError, ValueError):
            out[model] = 1.0
    _TRACKER_MULT_CACHE[key] = out
    return out


def _composite_input_multiplier(sport: str, composite_market: str,
                                composite_input: str) -> float:
    """Get the tracker-based multiplier for a composite formula input.
    Returns 1.0 if no tracker mapping or data missing."""
    mapping = _COMPOSITE_INPUT_TO_TRACKER.get((sport, composite_market, composite_input))
    if not mapping: return 1.0
    tracker_sport, tracker_market, tracker_model = mapping
    weights = _load_tracker_multipliers(tracker_sport, tracker_market)
    return weights.get(tracker_model, 1.0)


def refresh_tracker_cache():
    """Manually clear the cache — call after compute_model_track_records
    runs so composite formulas pick up the new weights."""
    _TRACKER_MULT_CACHE.clear()


@dataclass
class TierVerdict:
    tier: str          # 'ELITE' | 'STRONG' | 'LEAN' | 'SKIP'
    direction: Optional[str]  # 'OVER' | 'UNDER' | None
    reason: str
    composite_gap: Optional[float]
    models_agree: int  # how many of v3/v4/jerry agreed
    historical_hit_rate: Optional[float]  # expected hit rate from backtest


def _models_direction(proj_total, v4_total, jerry_total, line):
    """Count how many models lean each direction."""
    overs = 0
    unders = 0
    for m in (proj_total, v4_total, jerry_total):
        if m is None or line is None:
            continue
        gap = float(m) - float(line)
        if gap > 0.3:
            overs += 1
        elif gap < -0.3:
            unders += 1
    return overs, unders


def weighted_composite_total(v3, v4, jerry_deb, ctx):
    """Condition-aware weighted composite for total.

    Ships 2026-07-21 replacing flat equal-weight v3+jerry. Weights derived
    from 60d retro backtest (n=753) across 4 game buckets:
      - Ace matchup (both SPs xERA<=3.5): 72% hit (n=26) with jerry-heavy 0.4/0/0.6
      - Pitcher park (PRF<=96) or dome: 60.2% with 0.4/0/0.6
      - Hitter park (PRF>=105): 60.5% with 0.5/0.1/0.4
      - Default: 58.6% with 0.5/0.2/0.3 (sample-robust across n=297)

    Beats current Composite NEW (equal-weight v3+jerry-deb 57.9%) by up to
    +14 percentage points in the ace-matchup bucket. See
    project_model_reweight_721 memory for full backtest receipts.

    Returns (composite_total, weights_used_tuple).
    """
    ctx = ctx or {}
    prf = ctx.get('park_run_factor') or 100
    home_xera = ctx.get('home_sp_xera')
    away_xera = ctx.get('away_sp_xera')
    is_ace = (home_xera is not None and float(home_xera) <= 3.5 and
              away_xera is not None and float(away_xera) <= 3.5)
    is_dome = bool(ctx.get('is_dome') or ctx.get('dome_game') or ctx.get('dome_game_flag'))

    if is_ace:
        w = (0.4, 0.0, 0.6)   # ace-matchup jerry-heavy (72% n=26)
    elif prf <= 96 or is_dome:
        w = (0.4, 0.0, 0.6)   # pitcher-friendly jerry-heavy (60.2% n=133)
    elif prf >= 105:
        w = (0.5, 0.1, 0.4)   # hitter park (60.5% n=43)
    else:
        w = (0.5, 0.2, 0.3)   # sample-robust default (58.6% n=297)

    # 2026-08-07 GAP 5 — apply tracker multipliers to v4 + jerry weights
    # for total composite. v4 already low here (0.0-0.2) so multiplier
    # matters less than on spread, but recovery still auto-picks up.
    mult_v4 = _composite_input_multiplier('MLB', 'total', 'v4')
    mult_jerry = _composite_input_multiplier('MLB', 'total', 'jerry')
    w_adjusted = (w[0], w[1] * mult_v4, w[2] * mult_jerry)

    parts = [(w_adjusted[0], v3), (w_adjusted[1], v4), (w_adjusted[2], jerry_deb)]
    used = [(wi, x) for wi, x in parts if x is not None and wi > 0]
    if not used:
        return None, w
    norm = sum(wi for wi, _ in used)
    return sum(wi * float(x) for wi, x in used) / norm, w


def weighted_composite_spread(v3_spread, v4_spread, jerry_spread, panel_margin=None):
    """Reweighted composite spread for ML/side decisions.

    2026-08-07 REWEIGHT — v4 dropped from 0.3-0.5 to 0.1
    Cause: v4 (MODEL_SPREAD) has degraded from 58.4% ATS at set-time
    (2026-07-21) to 44.4% ML in last 14 days (n=189). At the previous
    v4 weight of 0.3-0.5, v4 was actively pulling picks the wrong
    direction — CURRENT config graded at 53.4% / +1.9% ROI on the
    2026-07-09 → 2026-08-06 test set (311 games).

    Reweight per backtest_composite_weights.py — 60/40 chronological
    OOS split, 776 games:
      * Panel-included: v3=0.1, v4=0.1, jerry=0.4, panel=0.4 → 58.2% test
      * Panel-free:     v3=0.4, v4=0.1, jerry=0.5             → 47.8% test (n=12, thin)

    Delta vs CURRENT weights (test-set, panel-variant n=299):
      +5.0 pp hit rate (53.2% → 58.2%)
      +9.2 pp ROI (~+1.9% → +11.1%)

    V4 kept at 0.1 (not 0) so recovery is auto-picked up when compute_
    model_track_records upgrades v4's recommended_weight. Panel weight
    retained at 0.4 — panel hit rate on the panel-branch subset is
    still positive.

    Prior weights (2026-07-21, kept for audit trail):
      * Panel: v3=0.1, v4=0.5, jerry=0.0, panel=0.4 → 62.0% n=187 (in-sample)
      * No-panel: v3=0.5, v4=0.3, jerry=0.2 → 60.4% n=462 (in-sample)

    Returns weighted spread where + = HOME advantage.
    Falls back to whichever lenses are non-null.
    """
    # 2026-08-07 GAP 5 — apply tracker multipliers to baseline weights.
    # Baseline weights (backtest-tuned) get multiplied by each model's
    # tracker recommended_weight so degradation/recovery auto-adjusts.
    # E.g. if MODEL_SPREAD tracker weight drops to 0.5 (deeply losing),
    # v4's effective weight becomes 0.1 * 0.5 = 0.05. If it recovers to
    # 1.5, effective weight becomes 0.15.
    mult_v4 = _composite_input_multiplier('MLB', 'spread', 'v4')
    mult_jerry = _composite_input_multiplier('MLB', 'spread', 'jerry')

    if panel_margin is not None:
        # Panel-included variant (2026-08-07 reweight) — 58.2% test OOS
        w_v3, w_v4, w_jerry, w_panel = 0.1, 0.1 * mult_v4, 0.4 * mult_jerry, 0.4
        parts = [(w_v3, v3_spread), (w_v4, v4_spread),
                 (w_jerry, jerry_spread), (w_panel, panel_margin)]
    else:
        # Panel-free default (2026-08-07 reweight)
        w_v3, w_v4, w_jerry = 0.4, 0.1 * mult_v4, 0.5 * mult_jerry
        parts = [(w_v3, v3_spread), (w_v4, v4_spread), (w_jerry, jerry_spread)]

    used = [(wi, x) for wi, x in parts if x is not None and wi > 0]
    if not used:
        return None
    norm = sum(wi for wi, _ in used)
    return sum(wi * float(x) for wi, x in used) / norm


# 2026-08-08 Phase 2A: sharp fade flag (bucket-based cap).
# 2026-08-09 Phase 2B: sharp fade RULES (pattern-based cap).
try:
    from sharp_fade_flag import compute_sharp_fade_flag as _sharp_fade_flag
except Exception:
    _sharp_fade_flag = None
try:
    from sharp_fade_rules import compute_fade_context as _sharp_fade_context
except Exception:
    _sharp_fade_context = None


def _log_sharp_fade(ctx: Optional[dict], market: str, side: Optional[str],
                     tier: str, source: str) -> Optional[dict]:
    """Combined Phase 2A (bucket cap) + Phase 2B (rule cap) sharp fade guard.

    Returns the *strongest* cap directive when multiple guards fire:
      2 rules ACTIVE  → CAP_TO_READ_49
      1 rule ACTIVE OR bucket cap → CAP_TO_LEAN_55

    Returns None or a flag dict for the tier-gate wrapper to apply.
    """
    if not ctx or not side or tier == 'SKIP': return None
    combined_flag = None
    max_severity = 0   # 0=none, 1=LEAN, 2=READ

    # ---- Phase 2A: bucket flag ----
    if _sharp_fade_flag:
        oc = ctx.get('oddscrowd_snapshot')
        try:
            bflag = _sharp_fade_flag(oc, market, side)
        except Exception:
            bflag = None
        if bflag and bflag.get('flag') in ('ALIGNS_WITH_FADE_SIDE', 'OPPOSES_FADE_SIDE'):
            cap = bflag.get('cap_directive')
            tag = '  🚨 CAP-TO-LEAN' if cap else ''
            print(f'  📊 [{source}] sharp-fade {bflag["flag"]}: {bflag["reason"]}{tag}')
            if cap:
                combined_flag = bflag
                max_severity = 1  # bucket cap → LEAN

    # ---- Phase 2B: pattern rules ----
    if _sharp_fade_context:
        try:
            fctx = _sharp_fade_context(ctx, market, side)
        except Exception:
            fctx = None
        if fctx and fctx.get('triggers'):
            for t in fctx['triggers']:
                mode = t.get('mode', 'LOG')
                mark = '🚨' if mode == 'ACTIVE' else ('📌' if mode == 'LOG' else '💤')
                print(f'  {mark} [{source}] rule {t["rule"]}({mode}): {t["reason"]}')
            cap = fctx.get('cap_directive')
            if cap == 'CAP_TO_READ_49':
                combined_flag = {'cap_directive': cap, 'reason':
                    f'2+ ACTIVE rules fired: {[t["rule"] for t in fctx["triggers"] if t.get("mode")=="ACTIVE"]}',
                    'triggers': fctx['triggers']}
                max_severity = 2  # rule stack → READ
            elif cap == 'CAP_TO_LEAN_55' and max_severity < 1:
                combined_flag = {'cap_directive': cap, 'reason':
                    f'1 ACTIVE rule fired: {next(t["rule"] for t in fctx["triggers"] if t.get("mode")=="ACTIVE")}',
                    'triggers': fctx['triggers']}
                max_severity = 1

    return combined_flag


def _apply_sharp_fade_cap(verdict: 'TierVerdict', flag: dict) -> 'TierVerdict':
    """Downgrade verdict tier based on cap_directive:
      CAP_TO_LEAN_55 → cap to LEAN
      CAP_TO_READ_49 → cap to LEAN (we treat as same until READ is a real tier)
    Preserves direction. Idempotent for already-low tiers.
    """
    if not flag or not flag.get('cap_directive'): return verdict
    directive = flag['cap_directive']
    # Determine target tier
    if directive == 'CAP_TO_READ_49':
        # 2+ ACTIVE rules — most aggressive. LEAN today (READ tier not
        # yet a first-class TierVerdict value in this module); downstream
        # sweat_card treats LEAN as the floor for publishing anyway.
        target = 'LEAN'
        prefix = 'Sharp Phase 2B (2+ rules)'
    else:
        target = 'LEAN'
        prefix = 'Sharp Phase 2A/2B (1 rule/bucket)'
    if verdict.tier in (None, 'SKIP', 'LEAN'): return verdict
    reason_suffix = f' [{prefix} cap: {flag.get("reason", "")[:140]}]'
    return TierVerdict(
        tier=target, direction=verdict.direction,
        reason=(verdict.reason or '') + reason_suffix,
        composite_gap=verdict.composite_gap,
        models_agree=verdict.models_agree,
        historical_hit_rate=verdict.historical_hit_rate,
    )


def evaluate_total(
    *,
    line: float,
    proj_total: Optional[float],
    v4_total: Optional[float],
    jerry_total: Optional[float],
    panel_implied_total: Optional[float] = None,
    ctx: Optional[dict] = None,
) -> TierVerdict:
    """Apply tier-discipline rules to decide whether a total pick publishes.

    2026-07-21 PANEL REMOVED FROM TOTALS.
    30d audit (n=254): Panel totals 51.2% overall, 43.2% when loud (gap≥2).
    Panel is bimodal on totals — 29.6% of games off by ≥5 runs. The 2026-06-24
    "Panel beats Composite 54-46 on disagreement" backtest was small-sample tail
    variance; real 30d shows Panel-loud-disagree publishes at 43-47%. Removed
    Panel-NEU coinflip trap, Panel-disagree flip-to-LEAN, and 4-way ELITE Panel
    boost. Panel retained on sides gate (evaluate_ml) where 30d audit confirms
    63-64% at |margin|≥1.0 — real edge. See project_30d_lens_audit_718 +
    project_panel_totals_drop_721.

    panel_implied_total parameter kept in signature for backward compat (call
    sites pass it) but is now IGNORED for total decisions.
    """
    _ = panel_implied_total  # backward-compat; unused
    if line is None or proj_total is None:
        return TierVerdict('SKIP', None, 'missing anchor data (line/proj)', None, 0, None)

    line = float(line)
    proj_total = float(proj_total)
    v4 = float(v4_total) if v4_total is not None else None
    jerry_raw = float(jerry_total) if jerry_total is not None else None

    # 2026-07-18 JERRY DEBIAS + v4 REMOVAL FROM COMPOSITE
    # ================================================================
    # 30-day audit ran 7/18 (n=359 graded games):
    #   Composite (v3+v4+jerry) baseline:        50.2%
    #   Composite (v3+v4+jerry-debias):          51.4% (+1.2pp)
    #   Composite v3+jerry-debias (drop v4):    56.1% (+5.9pp) ⭐
    #   v3 alone:                               52.7%
    #   v4 alone on totals:                     46.1% (below coinflip)
    #   jerry-debiased alone:                   53.6%
    #
    # v4 on totals is genuinely broken over 30d — it's the ONLY lens
    # that fails to beat coinflip. Removing v4 from Composite while
    # keeping it in _models_direction (consensus counting) delivers
    # the biggest lift on the audit.
    #
    # Jerry debias: subtract 0.62 (its 30d avg drift above line).
    #
    # v4 is preserved for:
    #   - _models_direction (overs/unders consensus counting)
    #   - spread lens (v4 is 54% on sides — usable)
    #   - display/audit
    # Only composite_avg + gap calculation drops v4.
    #
    # See: project_composite_debias_finding_712 memory.
    JERRY_BIAS_ADJUSTMENT = 0.62
    jerry = (jerry_raw - JERRY_BIAS_ADJUSTMENT) if jerry_raw is not None else None

    # v4 stays in the consensus counter (for all-3-unity tier checks)
    overs, unders = _models_direction(proj_total, v4, jerry, line)
    has_v4_jerry = (v4 is not None) and (jerry is not None)

    # 2026-07-21 CONDITIONAL WEIGHTED COMPOSITE (60d reweight audit)
    # Bucket-specific optimum weights: ace matchup 72% n=26, pitcher park 60.2%,
    # hitter park 60.5%, default 58.6%. Beats flat v3+jerry-deb Composite (57.9%).
    # Falls back to prior flat composite if ctx unavailable.
    if ctx:
        composite_avg, _weights = weighted_composite_total(proj_total, v4, jerry, ctx)
        if composite_avg is None:
            composite_avg = proj_total
    else:
        composite_inputs = [x for x in (proj_total, jerry) if x is not None]
        composite_avg = sum(composite_inputs) / max(1, len(composite_inputs))
    gap = composite_avg - line

    # 2026-08-08 Phase 2A: log sharp-fade flag alignment. When Phase 2A cap
    # conditions met (see sharp_divergence_tracker), capture the flag and
    # apply cap-to-LEAN at final verdict below.
    provisional_dir = 'OVER' if gap > 0 else ('UNDER' if gap < 0 else None)
    _sharp_cap_flag = None
    if provisional_dir:
        _sharp_cap_flag = _log_sharp_fade(ctx, 'total', provisional_dir, 'PROVISIONAL', 'evaluate_total')

    def _cap(v):
        return _apply_sharp_fade_cap(v, _sharp_cap_flag)

    # Hard SKIPs first
    if abs(gap) < 0.5:
        return TierVerdict('SKIP', None, f'composite gap |{gap:+.2f}| < 0.5 — no signal', gap, max(overs, unders), None)

    # 2026-07-21 Panel-aware total logic REMOVED — see audit note in docstring.
    # The Panel-NEU coinflip trap, Panel-disagree LEAN flip, and 4-way ELITE
    # boost blocks all deleted. Panel signal on totals is 43-47% when loud.

    # ===== UNDER side =====
    if gap < 0:
        # 2026-07-21 4-WAY ELITE UNDER (Panel boost) REMOVED — Panel unreliable
        # on totals per audit; ELITE UNDER now purely proj-vs-line gap based.

        # ELITE UNDER: any setup with proj-vs-line gap <= -3.0
        if proj_total - line <= -3.0:
            return _cap(TierVerdict('ELITE', 'UNDER',
                f'proj_total - line = {proj_total - line:.2f} (elite under signal)',
                gap, unders, 0.64))

        # STRONG UNDER: needs all-3 + gap <= -1.5 (small sample but consistent)
        if unders >= 3 and gap <= -1.5:
            return _cap(TierVerdict('STRONG', 'UNDER',
                f'all-3 UNDER + composite gap {gap:.2f}',
                gap, unders, 0.60))

        # KILL zone: gap -1.5 to -3.0 historically LOSES (44%, 42%). Skip.
        if -3.0 < gap < -1.5:
            return TierVerdict('SKIP', None,
                f'gap {gap:.2f} sits in middle-UNDER loser band (42-44% hist)',
                gap, unders, None)

        # LEAN UNDER: all-3 + lighter gap
        if unders >= 3 and gap >= -1.5:
            return _cap(TierVerdict('LEAN', 'UNDER',
                f'all-3 UNDER + light gap {gap:.2f}',
                gap, unders, 0.57))

        return TierVerdict('SKIP', None,
            f'UNDER lean but no all-3 unity (only {unders}/3 agree)',
            gap, unders, None)

    # ===== OVER side =====
    if gap > 0:
        # 2026-07-21 4-WAY ELITE OVER (Panel boost) REMOVED — Panel unreliable
        # on totals per audit; ELITE OVER now purely proj-vs-line gap based.

        # PRIME OVER: gap 2.0-3.0 + all-3 (71% hist)
        if 2.0 <= gap < 3.0 and overs >= 3:
            return _cap(TierVerdict('PRIME', 'OVER',
                f'all-3 OVER + composite gap {gap:.2f} (prime band 71% hist)',
                gap, overs, 0.71))

        # ELITE OVER: very loud + all-3 (only sample-thin band)
        if gap >= 3.0 and overs >= 3:
            return _cap(TierVerdict('ELITE', 'OVER',
                f'all-3 OVER + elite gap {gap:.2f}',
                gap, overs, 0.62))

        # STRONG OVER: all-3 + gap 1.2-2.0 (57% hist - marginal)
        if 1.2 <= gap < 2.0 and overs >= 3:
            return _cap(TierVerdict('STRONG', 'OVER',
                f'all-3 OVER + composite gap {gap:.2f}',
                gap, overs, 0.57))

        # LEAN OVER: all-3 + gap 0.7-1.2 (56% hist)
        if 0.7 <= gap < 1.2 and overs >= 3:
            return _cap(TierVerdict('LEAN', 'OVER',
                f'all-3 OVER + light gap {gap:.2f}',
                gap, overs, 0.56))

        # mild magnitude (< 0.7) historically loses (48%) — skip
        if gap < 0.7:
            return TierVerdict('SKIP', None,
                f'gap {gap:.2f} < 0.7 (mild-OVER 48% hist loser)',
                gap, overs, None)

        # OVER lean without all-3 — skip
        return TierVerdict('SKIP', None,
            f'OVER lean but no all-3 unity (only {overs}/3 agree)',
            gap, overs, None)

    return TierVerdict('SKIP', None, 'no direction', gap, 0, None)


# ─────────────────────────────────────────────────────────────────────────
# ML / SIDE DISCIPLINE GATE (2026-06-24)
# ─────────────────────────────────────────────────────────────────────────
# POTD ML picks were bleeding alongside totals (CHC 6/20, SF 6/9, BAL 6/7,
# SF 6/6 — multiple resolver STRONG ML losses). Resolver tier alone isn't
# sufficient — same pattern as composite over-bias for totals.
#
# Backtest on 291 jerry_cache games:
#   Panel ML margin >= 3.0:  75% hit (n=12)
#   Panel ML margin 2-3:     62% hit (n=29)
#   Panel ML margin 1-2:     53% hit (n=91)
#   Panel ML margin 0.5-1:   57% hit (n=70)
#   Panel ML margin < 0.5:   40% hit (n=89) ← counter-signal, fade
#
# Gate rules (parallel structure to total gate):
#   ELITE: resolver ELITE + Panel margin >= 2.0 + composite spread agrees
#   STRONG: resolver STRONG + Panel margin >= 1.0 + composite spread agrees
#   LEAN: resolver STRONG + Panel margin >= 0.5
#   SKIP: resolver below STRONG, OR Panel margin < 0.5 (counter-signal),
#         OR Panel direction OPPOSITE resolver direction
#
# composite_spread positive = HOME wins by that margin

def evaluate_ml(
    *,
    resolver_tier: Optional[str],         # 'ELITE' | 'STRONG' | 'LEAN' | 'LIGHT' | 'SKIP'
    resolver_direction: Optional[str],    # 'HOME' | 'AWAY'
    composite_spread: Optional[float],    # avg of v3+v4+jerry spreads (+ = home wins)
    panel_implied_margin: Optional[float] = None,  # +home runs - away runs (Panel)
    ctx: Optional[dict] = None,            # 2026-08-08: pass ctx for sharp-fade log
) -> TierVerdict:
    """Apply tier-discipline rules to decide whether an ML/RL pick publishes.

    Returns SKIP for anything that doesn't pass the resolver + Panel + composite
    triple check. POTD selector reads tier verdict and rejects pick if SKIP.
    """
    if resolver_tier in (None, 'SKIP', 'LIGHT'):
        return TierVerdict('SKIP', None,
            f'resolver tier {resolver_tier} below STRONG threshold',
            None, 0, None)
    if resolver_direction not in ('HOME', 'AWAY'):
        return TierVerdict('SKIP', None, 'resolver has no clean direction',
            None, 0, None)

    # 2026-08-08 Phase 2A sharp-fade log + capture cap flag for final wrap
    _sharp_cap_flag = _log_sharp_fade(ctx, 'ml', resolver_direction, resolver_tier, 'evaluate_ml')

    def _cap(v):
        return _apply_sharp_fade_cap(v, _sharp_cap_flag)

    # Composite spread agreement — 2026-07-12: first check. When v3+v4+jerry
    # loudly agree AWAY from resolver by >= 1.0 runs, that's a real model-
    # consensus signal. Flip to composite as LEAN even if Panel margin is
    # tight (case: ATL/STL 7/12 — Panel pick'em but composite -1.5 all AWAY).
    composite_winner = None
    if composite_spread is not None:
        try:
            cs = float(composite_spread)
            if cs > 0.3:
                composite_winner = 'HOME'
            elif cs < -0.3:
                composite_winner = 'AWAY'
        except (TypeError, ValueError):
            pass
    if composite_winner and composite_winner != resolver_direction:
        cs_abs = abs(float(composite_spread))
        if cs_abs >= 1.0:
            return _cap(TierVerdict('LEAN', composite_winner,
                f'resolver {resolver_direction} but composite spread {composite_spread:+.2f} loud {composite_winner} — publish composite',
                None, 0, 0.55))
        return TierVerdict('SKIP', None,
            f'resolver {resolver_direction} but composite spread {composite_spread:+.2f} → {composite_winner} (thin flip)',
            None, 0, None)

    # Panel ML margin counter-signal — Panel margin < 0.5 = pick'em game,
    # historically Panel ML hits only 40% in that range. Only apply this
    # SKIP if composite ALSO wasn't loud in resolver's favor (already checked
    # above). Then check for Panel-loud opposite → flip to Panel as LEAN.
    if panel_implied_margin is not None:
        abs_margin = abs(panel_implied_margin)
        panel_winner = 'HOME' if panel_implied_margin > 0 else 'AWAY'
        if panel_winner != resolver_direction:
            if abs_margin >= 1.0:
                return _cap(TierVerdict('LEAN', panel_winner,
                    f'resolver {resolver_direction} but Panel margin {panel_implied_margin:+.1f} loud {panel_winner} — publish Panel (57% edge)',
                    None, 0, 0.57))
            return TierVerdict('SKIP', None,
                f'resolver {resolver_direction} but Panel margin {panel_implied_margin:+.1f} says {panel_winner} — thin flip',
                None, 0, None)
        # Panel agrees with resolver — but if margin is tight, still coinflip
        if abs_margin < 0.5:
            return TierVerdict('SKIP', None,
                f'Panel margin {panel_implied_margin:+.1f} too tight (40% hist on <0.5 margin)',
                None, 0, None)

    # ELITE: resolver ELITE + Panel margin >= 2 (when Panel available)
    if resolver_tier == 'ELITE':
        if panel_implied_margin is None or abs(panel_implied_margin) >= 2.0:
            return _cap(TierVerdict('ELITE', resolver_direction,
                f'resolver ELITE + Panel margin {panel_implied_margin or "n/a"}',
                None, 0, 0.75))
        return _cap(TierVerdict('STRONG', resolver_direction,
            f'resolver ELITE but Panel margin {panel_implied_margin:+.1f} only mild — downgrade to STRONG',
            None, 0, 0.62))

    # STRONG: resolver STRONG + Panel margin >= 1.0
    if resolver_tier == 'STRONG':
        if panel_implied_margin is None or abs(panel_implied_margin) >= 1.0:
            return _cap(TierVerdict('STRONG', resolver_direction,
                f'resolver STRONG + Panel margin {panel_implied_margin or "n/a"}',
                None, 0, 0.62))
        # Panel margin tight (0.5-1.0) — downgrade to LEAN
        return _cap(TierVerdict('LEAN', resolver_direction,
            f'resolver STRONG but Panel margin {panel_implied_margin:+.1f} mild — downgrade to LEAN',
            None, 0, 0.57))

    # LEAN: resolver LEAN passes through if Panel agrees in direction
    if resolver_tier == 'LEAN':
        return _cap(TierVerdict('LEAN', resolver_direction,
            f'resolver LEAN + Panel direction match',
            None, 0, 0.55))

    return TierVerdict('SKIP', None,
        f'unhandled resolver tier {resolver_tier}', None, 0, None)


# ─────────────────────────────────────────────────────────────────────────
# COHORT-FADE ML LANE (2026-07-21) — new
# ─────────────────────────────────────────────────────────────────────────
# From 14d cohort combination audit (n=150):
#   SPLIT_HOME_LEAN cohorts (cohort ratio 0.2 to 0.5 toward HOME) →
#     AWAY actually wins 62.5% (n=16). Fade to AWAY.
#   SPLIT_AWAY_LEAN cohorts (cohort ratio -0.5 to -0.2) →
#     HOME actually wins 61.4% (n=44). Fade to HOME.
#   LOUD_HOME / LOUD_AWAY / UNANIMOUS → reverts to coinflip. Do NOT fade.
#   COINFLIP (net weight <4) → no signal.
#
# This runs AFTER evaluate_ml. If the primary gate SKIPS, we try this lane
# to surface a mid-tier LEAN publish. Never publish as PRIME on a fade
# signal — the historical edge lives at 61% not 70%.
#
# Total plays surfaced by this lane in 14d: n=60 (SPLIT_AWAY=44, SPLIT_HOME=16).
# Recommended usage: LEAN publish only, no auto-parlay leg, no POTD gating.
# Ship as LEAN because 7d shadow validation is queued.

def evaluate_ml_cohort_fade(cohort_signals):
    """Contrarian ML lane. See docstring at module block above.

    Args:
      cohort_signals: dict from game_read.cohort_signals.matched_plays with
                      keys v3_ml, v4_ml, conf_ml (each a list of cohort rules
                      containing 'direction' and 'delta').

    Returns:
      TierVerdict (LEAN, HOME/AWAY, ...) when SPLIT-lean bucket fires.
      None when LOUD/UNANIMOUS/COINFLIP (no signal).
    """
    if not cohort_signals:
        return None
    ml_buckets = ('v3_ml', 'v4_ml', 'conf_ml')
    home_w = away_w = 0.0
    for b in ml_buckets:
        rules = cohort_signals.get(b) or []
        if not isinstance(rules, list):
            continue
        for c in rules:
            if not isinstance(c, dict):
                continue
            d = (c.get('direction') or '').lower()
            try:
                delta = abs(float(c.get('delta') or 0))
            except (TypeError, ValueError):
                continue
            if d == 'home':
                home_w += delta
            elif d == 'away':
                away_w += delta

    total = home_w + away_w
    if total < 4:  # coinflip band — no meaningful firing
        return None

    ratio = (home_w - away_w) / total  # +1 fully home, -1 fully away

    # SPLIT_HOME_LEAN (mild home consensus) → fade to AWAY
    if 0.2 <= ratio <= 0.5:
        return TierVerdict(
            'LEAN', 'AWAY',
            f'cohort SPLIT_HOME_LEAN fade to AWAY (14d 62.5%, ratio {ratio:.2f}, n=16)',
            None, 0, 0.62,
        )
    # SPLIT_AWAY_LEAN (mild away consensus) → fade to HOME
    if -0.5 <= ratio <= -0.2:
        return TierVerdict(
            'LEAN', 'HOME',
            f'cohort SPLIT_AWAY_LEAN fade to HOME (14d 61.4%, ratio {ratio:.2f}, n=44)',
            None, 0, 0.61,
        )
    # LOUD (|ratio| > 0.5) or COINFLIP (|ratio| < 0.2) → no signal
    return None


# ─────────────────────────────────────────────────────────────────────────
# COHORT TIER-QUALITY GUARD (2026-07-21) — for UNDER decisions
# ─────────────────────────────────────────────────────────────────────────
# From 14d audit: LOUD_UNDER + STRONG_ONLY = 55.6% (n=45), but LOUD_UNDER +
# MIXED (STRONG + LEAN co-firing) = 35.7% (n=14). LEAN cohorts pollute the
# read. When we see LEAN cohorts stacked on top of STRONG_EDGE, downgrade
# one tier because the signal is diluted.

def _majority_tier_quality(cohorts):
    """Classify a list of matched cohorts by their strongest tier presence.

    Returns:
      'STRONG_ONLY' when 2+ STRONG_EDGE/PRIME/ELITE cohorts fire with 0 LEAN.
      'MIXED' when both STRONG and LEAN cohorts fire together (36% hist).
      'LEAN_ONLY' when only LEAN cohorts fire.
    """
    strong = 0
    lean = 0
    for c in cohorts or []:
        if not isinstance(c, dict):
            continue
        tier = (c.get('tier') or '').upper()
        if any(t in tier for t in ('STRONG', 'PRIME', 'ELITE')):
            strong += 1
        elif any(t in tier for t in ('LEAN', 'LIGHT', 'SOFT')):
            lean += 1
    if strong >= 2 and lean == 0:
        return 'STRONG_ONLY'
    if strong >= 1 and lean >= 1:
        return 'MIXED'
    return 'LEAN_ONLY'


if __name__ == '__main__':
    # Self-test against today's slate examples
    cases = [
        # PRIME OVER expected (LAD/MIN yesterday gap ~2.6)
        dict(line=9.5, proj_total=9.9, v4_total=11.53, jerry_total=14.9),
        # ELITE UNDER expected (rare big gap below line)
        dict(line=11.5, proj_total=8.0, v4_total=8.5, jerry_total=8.0),
        # SKIP — mild magnitude
        dict(line=8.5, proj_total=8.7, v4_total=8.9, jerry_total=8.6),
        # SKIP — middle UNDER loser band
        dict(line=10.0, proj_total=8.5, v4_total=8.3, jerry_total=8.0),
    ]
    for c in cases:
        v = evaluate_total(**c)
        print(f'line={c["line"]:>5} proj={c["proj_total"]:>5} v4={c["v4_total"]:>5} jerry={c["jerry_total"]:>5}')
        print(f'  -> {v.tier} {v.direction or ""} | {v.reason} | hist {v.historical_hit_rate}')
