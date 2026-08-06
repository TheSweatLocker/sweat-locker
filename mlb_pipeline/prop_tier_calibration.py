"""Prop tier calibration — fade + juice + goldmine (2026-08-03).

Sprint-3 response to the 14-day audit that showed props hitting 46% on
the Sweat Card top_8 while totals hit 83%. Provides three helpers any
prop stage can import: should_fade, juice_cap, is_goldmine_skip.

DYNAMIC CALIBRATION (2026-08-03 v2, family_prior 2026-08-05 v3):
FADE_COMBOS, GOLDMINE_SKIP_COMBOS, HISTORICAL_HIT_RATES, and
FAMILY_BASE_RATES all start from the 8/5 audit snapshot but refresh
from live prop_bucket_roi on every module import (~50ms). Buckets
climb back over 50% → exit fade list. Family rates roll up via
weighted mean across tiers — so as new grades arrive nightly the
prior naturally shifts without manual editing.

Refresh cycle (every import):
  - Adds combo to FADE_COMBOS when hit_rate < 45% AND n >= 15
  - Removes combo from FADE_COMBOS when hit_rate ≥ 55%
  - Adds combo to GOLDMINE_SKIP when SKIP tier hit_rate ≥ 65% AND n >= 15
  - Removes combo from GOLDMINE_SKIP when SKIP hit_rate < 55%
  - Rebuilds FAMILY_BASE_RATES weighted-mean across tiers per family
  - Fallback to static tables if DB unreachable (never break the pipeline)

Buckets between 45-55% W (grey zone) neither fade nor promote — noise.
"""
from __future__ import annotations
from typing import Optional
import os


# Thresholds for auto-add / auto-remove (dynamic calibration)
FADE_ENTER_HIT_PCT = 45.0    # bucket becomes fade when hit% falls below this
FADE_EXIT_HIT_PCT = 55.0     # bucket exits fade when hit% climbs above this
GOLDMINE_ENTER_HIT_PCT = 65.0
GOLDMINE_EXIT_HIT_PCT = 55.0
MIN_N_ACTIONABLE = 15         # need at least this much sample to add/remove


# ── FAMILY BASE RATES (2026-08-05 v3 audit — n=1237 graded, 30d) ──
# Anchors conviction to historical family hit rate. A PRIME 90 in hits_over
# is fundamentally different from PRIME 90 in ha_over — the priors matter.
# Bayesian: raw conviction blended with family_prior gives adjusted output.
#
# Also used to permanently suppress dead families (outs_over @ 22.7%).
FAMILY_BASE_RATES = {
    'outs_under': (79.6, 93),   # 🎯 goldmine — default boost
    'hits_over':  (64.1, 345),  # winner, largest volume
    'bb_over':    (58.8, 131),  # solid
    'bb_under':   (56.4, 133),  # solid
    'ks_over':    (52.7, 91),   # break-even
    'ks_under':   (51.1, 180),  # break-even
    'ha_under':   (48.9, 88),
    'er_over':    (48.3, 60),
    'hits_under': (60.2, 103),  # from earlier audit
    'ha_over':    (42.9, 70),   # loser
    'er_under':   (37.5, 24),   # loser
    'outs_over':  (22.7, 22),   # DEAD family — hard suppress
}

# Family-level hard suppression (never publish regardless of tier/conviction).
# outs_over hits 22.7% at n=22 with avg delta -9.77 (actual outs are 10 FEWER
# than line consistently) — the market is fundamentally mispriced for us.
FAMILY_SUPPRESS = {'outs_over'}


# ── HIGH-CONVICTION MULTI-SIGNAL GATE (2026-08-05) ──
# Audit finding: PRIME 100 hits 50% (n=20) while STRONG 60-69 hits 60.8%
# (n=186). Higher conviction is INVERSELY correlated with hit rate at the
# top — single-signal legacy PRIMEs are inflated by the 4200-line hand-tuned
# scorer's tendency to weight one loud signal (hot L7, park factor) as
# proof. Multi-signal confirmation from the refit model is required to
# keep conviction above 90.
HIGH_CONVICTION_THRESHOLD = 90     # conviction level that requires backing
HIGH_CONVICTION_REFIT_MIN = 70     # refit must be >= this to hold ≥90
HIGH_CONVICTION_CAP = 85           # otherwise conviction drops to STRONG max


def apply_family_prior(prop_type: str, direction: str, raw_conviction: int) -> tuple[int, str]:
    """Blend raw conviction with historical family hit rate.

    Bayesian anchor:
      - Family hits >=60% → boost conviction toward that rate (encouraging plays)
      - Family hits 45-59% → hold conviction near projection edge
      - Family hits <45% → hard cap conviction (family is unreliable regardless)

    Return: (adjusted_conviction, reason).
    """
    if not prop_type: return raw_conviction, 'no_prop_type'
    family = prop_type.lower()
    # Convert 'bb' → 'bb_under'/'bb_over' shape by joining with direction if not present
    if '_' not in family and direction:
        family = f'{family}_{direction.lower()}'
    entry = FAMILY_BASE_RATES.get(family)
    if not entry:
        return raw_conviction, 'no_family_prior'
    hit_pct, n = entry
    if n < 20:
        return raw_conviction, 'family_sample_too_small'
    # Bayesian blend: 30% weight on family prior, 70% on raw conviction
    # Prior is expressed on the same 0-100 scale as conviction
    adjusted = int(raw_conviction * 0.7 + hit_pct * 0.3)
    # Hard-cap losing families
    if hit_pct <= 42:
        adjusted = min(adjusted, 42)
        return adjusted, f'family_hard_cap_{hit_pct}pct_hist'
    return adjusted, f'family_prior_blend_{hit_pct}pct'


# ── Historical hit rates by (prop_type, tier, direction) ──
# Sourced 2026-08-03 audit. n shown for context. Only entries with n >= 15
# are considered actionable; smaller samples are ignored.
HISTORICAL_HIT_RATES = {
    # LOSERS — block these
    ('outs_over', 'SKIP', 'over'):    (29.4, 51),
    ('er_under', 'SKIP', 'under'):    (24.1, 29),
    ('ha_over',  'LEAN', 'over'):     (33.3, 27),
    ('ha_over',  'SKIP', 'over'):     (37.5, 24),
    ('er_under', 'LEAN', 'under'):    (38.5, 13),
    ('bb_under', 'SKIP', 'under'):    (40.0, 25),
    ('er_under', 'STRONG', 'under'):  (40.0, 20),
    ('er_over',  'STRONG', 'over'):   (43.9, 41),  # over-labeled STRONG
    ('er_over',  'SKIP', 'over'):     (45.2, 31),
    ('ks_over',  'SKIP', 'over'):     (49.2, 130),
    ('ha_under', 'SKIP', 'under'):    (47.6, 105),
    ('ha_under', 'STRONG', 'under'):  (47.4, 38),

    # WINNERS — reference for tier promotion
    ('outs_under', 'SKIP', 'under'):  (75.9, 87),  # 🎯 hidden gold
    ('hits_under', 'SKIP', 'under'):  (72.0, 25),  # 🎯 hidden gold
    ('hits_over',  'LEAN', 'over'):   (71.4, 56),
    ('bb_under',   'STRONG', 'under'):(69.8, 63),
    ('hits_over',  'STRONG', 'over'): (67.7, 498),
    ('ks_over',    'PRIME', 'over'):  (67.2, 61),
    ('hits_over',  'PRIME', 'over'):  (66.3, 525),
    ('outs_under', 'PRIME', 'under'): (64.7, 17),
    ('er_under',   'PRIME', 'under'): (64.7, 17),
    ('outs_under', 'LEAN', 'under'):  (63.2, 38),
    ('hits_under', 'PRIME', 'under'): (60.2, 103),
}

# FADE COMBOS (2026-08-03 user directive): "Don't suppress garbage — let
# Jerry FADE the other side, market has priced it in." A prop_type at
# 29% hit rate is a 71% FADE signal. Flip the direction, publish as the
# opposite side at a tier proportional to the inverse hit rate.
#
# Format: (prop_type_family, tier, direction) → (fade_tier, inverse_pct, n)
# fade_tier assigned by inverse rate:
#   >=70% → STRONG,  60-69% → LEAN,  55-59% → LEAN (light),  <55% → skip (fade too soft)
FADE_COMBOS = {
    ('outs_over', 'SKIP', 'over'):    ('STRONG', 70.6, 51),  # over 29% → under 71%
    ('er_under',  'SKIP', 'under'):   ('STRONG', 75.9, 29),  # under 24% → over 76%
    ('ha_over',   'LEAN', 'over'):    ('STRONG', 66.7, 27),  # over 33% → under 67%
    ('ha_over',   'SKIP', 'over'):    ('LEAN',   62.5, 24),  # over 38% → under 63%
    ('bb_under',  'SKIP', 'under'):   ('LEAN',   60.0, 25),  # under 40% → over 60%
    ('er_under',  'STRONG', 'under'): ('LEAN',   60.0, 20),  # under 40% → over 60%
    ('er_over',   'STRONG', 'over'):  ('LEAN',   56.1, 41),  # over 44% → under 56% (fade too soft, but flag)
}

# GOLDMINE — SKIP tier that beats PRIME. Promote to STRONG when Jerry BACKs.
GOLDMINE_SKIP_COMBOS = {
    ('outs_under', 'SKIP', 'under'),   # 76% n=87
    ('hits_under', 'SKIP', 'under'),   # 72% n=25
}


def _refresh_from_live_data():
    """Refresh FADE_COMBOS + GOLDMINE_SKIP_COMBOS + HISTORICAL_HIT_RATES from
    the live prop_bucket_roi table. Runs once per import — silent no-op if
    Supabase unreachable (keeps static defaults). Called at module load.

    Auto-add: bucket hit_rate < 45% at n >= 15 → add to FADE_COMBOS
    Auto-remove: bucket hit_rate ≥ 55% → remove from FADE_COMBOS
    Auto-add goldmine: SKIP tier hit_rate ≥ 65% at n >= 15 → add
    Auto-remove goldmine: SKIP hit_rate < 55% → remove

    Between 45-55% is grey zone — noise, no action.
    """
    try:
        import requests
        SB = os.environ.get('SUPABASE_URL')
        KEY = os.environ.get('SUPABASE_KEY')
        if not (SB and KEY): return
        # Read ALL sports — the (prop_type, tier, direction) keys don't collide
        # because prop_type strings differ per sport (MLB 'ks_over', NFL
        # 'pass_yds_over', etc). One universal table serves every sport.
        r = requests.get(f'{SB}/rest/v1/prop_bucket_roi',
                         headers={'apikey': KEY, 'Authorization': f'Bearer {KEY}'},
                         params={'select': 'sport,prop_type,tier,direction,hit_rate,sample_n'},
                         timeout=10)
        if r.status_code != 200: return
        rows = r.json()
        if not isinstance(rows, list): return
    except Exception:
        return  # fall back to static tables

    added_fade = removed_fade = added_gold = removed_gold = 0
    live_seen = set()
    # Aggregation buckets for FAMILY_BASE_RATES rebuild
    # {family_direction: [ (hit_pct, n), ... ]}
    family_agg: dict = {}

    for row in rows:
        pt = (row.get('prop_type') or '').lower()
        tier = (row.get('tier') or '').upper()
        direction = (row.get('direction') or '').lower()
        hit = row.get('hit_rate')
        n = row.get('sample_n') or 0
        if hit is None or n < MIN_N_ACTIONABLE: continue
        # prop_type in bucket_roi is stored as family (bb, ks) not family_direction
        # so we reconstruct the full form for matching HISTORICAL_HIT_RATES
        full_pt = pt if '_' in pt else f'{pt}_{direction}'
        key = (full_pt, tier, direction)
        live_seen.add(key)

        # Update HISTORICAL_HIT_RATES with fresh data
        HISTORICAL_HIT_RATES[key] = (float(hit), int(n))

        # Aggregate for family base-rate refresh (all tiers rolled up)
        family_agg.setdefault(full_pt, []).append((float(hit), int(n)))

        # Fade rules
        if hit < FADE_ENTER_HIT_PCT:
            if key not in FADE_COMBOS:
                inverse_pct = 100.0 - float(hit)
                if inverse_pct >= 70: fade_tier = 'STRONG'
                elif inverse_pct >= 60: fade_tier = 'LEAN'
                else: fade_tier = 'LEAN'
                FADE_COMBOS[key] = (fade_tier, round(inverse_pct, 1), int(n))
                added_fade += 1
        elif hit >= FADE_EXIT_HIT_PCT:
            if key in FADE_COMBOS:
                del FADE_COMBOS[key]
                removed_fade += 1

        # Goldmine rules (SKIP tier only)
        if tier == 'SKIP':
            if hit >= GOLDMINE_ENTER_HIT_PCT:
                if key not in GOLDMINE_SKIP_COMBOS:
                    GOLDMINE_SKIP_COMBOS.add(key)
                    added_gold += 1
            elif hit < GOLDMINE_EXIT_HIT_PCT:
                if key in GOLDMINE_SKIP_COMBOS:
                    GOLDMINE_SKIP_COMBOS.discard(key)
                    removed_gold += 1

    # Refresh FAMILY_BASE_RATES from aggregation. Weighted mean across
    # tiers: hit_pct = Σ(hit_i * n_i) / Σ(n_i). Requires total n ≥ 20
    # (same floor apply_family_prior enforces). Overwrites the static
    # 8/5 snapshot with fresh weighted numbers each import.
    families_refreshed = 0
    for family, buckets in family_agg.items():
        total_n = sum(n for _, n in buckets)
        if total_n < 20: continue
        weighted_hit = sum(h * n for h, n in buckets) / total_n
        prev = FAMILY_BASE_RATES.get(family)
        FAMILY_BASE_RATES[family] = (round(weighted_hit, 1), int(total_n))
        if prev is None or abs(prev[0] - weighted_hit) > 1.0:
            families_refreshed += 1

    if added_fade or removed_fade or added_gold or removed_gold or families_refreshed:
        print(f'[prop_tier_calibration] refreshed from live data: '
              f'+{added_fade} fade / -{removed_fade} fade · '
              f'+{added_gold} goldmine / -{removed_gold} goldmine · '
              f'{families_refreshed} family rates updated')


# Refresh on import — silent if DB unreachable
_refresh_from_live_data()


def should_fade(prop_type: str, tier: str, direction: str) -> tuple[bool, Optional[str], Optional[int], str]:
    """Check if this combo is a historical loser that should be FADED (flip direction).
    Returns (is_fade, fade_tier, fade_conviction, reason).
    Fade_tier is what to publish the FLIPPED direction as."""
    key = ((prop_type or '').lower(), (tier or '').upper(), (direction or '').lower())
    if key in FADE_COMBOS:
        fade_tier, inverse_pct, n = FADE_COMBOS[key]
        # Conviction: 70+ → 78, 60-69 → 65, 55-59 → 55
        if inverse_pct >= 70: fade_conv = 78
        elif inverse_pct >= 60: fade_conv = 65
        else: fade_conv = 55
        original_pct = 100 - inverse_pct
        reason = f'fade_signal_{prop_type}_{direction}_hit_only_{original_pct:.1f}pct_at_this_tier_n{n}'
        return True, fade_tier, fade_conv, reason
    return False, None, None, ''


# Kept for backward-compat but now maps to fade
def suppress(prop_type: str, tier: str, direction: str) -> tuple[bool, str]:
    """DEPRECATED — use should_fade() instead. Never returns True now,
    since suppression policy replaced with fade policy per user directive
    2026-08-03 ('don't suppress garbage — let Jerry FADE the other side')."""
    return False, ''


def is_goldmine_skip(prop_type: str, tier: str, direction: str) -> bool:
    """True if this is a SKIP-tier bucket that's actually a historical winner.
    Publisher should KEEP + promote these when Jerry backs."""
    key = ((prop_type or '').lower(), (tier or '').upper(), (direction or '').lower())
    return key in GOLDMINE_SKIP_COMBOS


# ── FORM-DRIFT JUICE TRAP (2026-08-05) ──────────────────────────────
# 90d audit of ER OVER by (L3_ERA - xERA) drift × book juice bucket:
#   drift ≥3 · juice -140 to -110: 33.3% hit (n=21)  ← TRAP
#   drift ≥3 · juice -175 to -140: 46.7% hit (n=15)  ← below breakeven
#   drift ≥3 · fair/plus odds:     66.7% hit (n=18)  ← edge survives
# Book prices in the reversion at moderate juice — Painter (-115),
# Kremer (-125) both lost 8/5 fitting this exact profile.
# Same trap on ER UNDER when pitcher is IN form (L3 ≤ xERA):
#   ALIGNED · any juice: 30.8% hit (n=26)
FORM_DRIFT_TRAP_JUICE_MIN = -175
FORM_DRIFT_TRAP_JUICE_MAX = -110
FORM_DRIFT_MIN_DELTA = 3.0        # L3_ERA - xERA gap that qualifies
FORM_DRIFT_TRAP_CAP = 50           # LEAN max


def form_drift_juice_trap(prop: dict) -> tuple[bool, str]:
    """Return (should_cap, reason) if the prop is a form-drift ER over/under
    priced in the trap-juice band. Only fires for er_over/er_under.

    ER OVER trap: L3 ERA is elevated by ≥3 vs xERA (loud "he's collapsing")
    AND book juice is in -175..-110 → book has priced the collapse; hit rate
    drops to 33-47%.

    ER UNDER trap: L3 ERA is aligned/below xERA (loud "he's elite") AND book
    juice is in -175..-110 → book has priced the elite form; hit rate 31%.
    """
    pt = (prop.get('prop_type') or '').lower()
    direction = (prop.get('direction') or '').lower()
    if pt not in ('er_over', 'er_under'):
        return False, ''

    signals = prop.get('signals') or {}
    if not isinstance(signals, dict):
        return False, ''

    # Parse L3 ERA and xERA out of the signal text values
    import re as _re
    l3 = xera = None
    l3_re = _re.compile(r'L3\s*ERA\s*(\d+(?:\.\d+)?)')
    xe_re = _re.compile(r'xERA\s*(\d+(?:\.\d+)?)')
    for v in signals.values():
        if not isinstance(v, str): continue
        m = l3_re.search(v)
        if m and l3 is None:
            try: l3 = float(m.group(1))
            except ValueError: pass
        m = xe_re.search(v)
        if m and xera is None:
            try: xera = float(m.group(1))
            except ValueError: pass
    if l3 is None or xera is None:
        return False, ''

    odds = prop.get('book_over_odds') if direction == 'over' else prop.get('book_under_odds')
    if odds is None:
        return False, ''
    try: odds = int(odds)
    except (TypeError, ValueError): return False, ''

    # Trap zone: juice in the priced-in band
    if not (FORM_DRIFT_TRAP_JUICE_MIN <= odds <= FORM_DRIFT_TRAP_JUICE_MAX):
        return False, ''

    drift = l3 - xera
    if direction == 'over' and drift >= FORM_DRIFT_MIN_DELTA:
        return True, f'form_drift_er_over_L3{l3}_xERA{xera}_drift+{drift:.1f}_juice{odds}_hist33pct_n21'
    if direction == 'under' and drift <= -0.5:  # pitcher in form; audit showed 31% (n=26)
        return True, f'form_alignment_er_under_L3{l3}_xERA{xera}_drift{drift:+.1f}_juice{odds}_hist31pct_n26'

    return False, ''


def er_under_fair_price_trap(prop: dict) -> tuple[bool, str]:
    """Return (should_hard_suppress, reason) for ER UNDER at plus-money with
    pitcher in form. 90d audit says this is nearly automatic loss.

    ALIGNED (L3≤xERA) + FAIR odds > -110:   0-8   (0%)
    STRONG_FORM       + FAIR odds > -110:   1-8   (11%)
    Combined FAIR trap: 1-16 (6%) at n=17.

    Interpretation: market pays +odds on ER UNDER despite the pitcher being
    in form → market has a specific reason (matchup, park, weather, bullpen)
    → trust the market. Nearly automatic loss.
    """
    if (prop.get('prop_type') or '').lower() != 'er_under': return False, ''
    if (prop.get('direction') or '').lower() != 'under': return False, ''
    odds = prop.get('book_under_odds')
    if odds is None: return False, ''
    try: odds = int(odds)
    except (TypeError, ValueError): return False, ''
    # Fair-or-better only (> -110 = plus-money or thin juice)
    if odds <= -110: return False, ''

    signals = prop.get('signals') or {}
    if not isinstance(signals, dict): return False, ''

    import re as _re
    l3 = xera = None
    l3_re = _re.compile(r'L3\s*ERA\s*(\d+(?:\.\d+)?)')
    xe_re = _re.compile(r'xERA\s*(\d+(?:\.\d+)?)')
    for v in signals.values():
        if not isinstance(v, str): continue
        m = l3_re.search(v)
        if m and l3 is None:
            try: l3 = float(m.group(1))
            except ValueError: pass
        m = xe_re.search(v)
        if m and xera is None:
            try: xera = float(m.group(1))
            except ValueError: pass
    if l3 is None or xera is None: return False, ''

    # Aligned or in-form pitcher (drift ≤ 0)
    drift = l3 - xera
    if drift > 0: return False, ''

    return True, f'er_under_fair_price_trap_L3{l3}_xERA{xera}_drift{drift:+.1f}_juice{odds}_hist6pct_n17'


def cap_unsupported_high_conviction(raw_conviction: int,
                                    refit_conviction: Optional[float]) -> tuple[int, str]:
    """Cap conviction >= 90 unless refit model corroborates.

    Audit 2026-08-05: PRIME 100 hit 50% (n=20) vs STRONG 60-69 at 60.8%
    (n=186). Legacy hand-tuned scorer inflates single-signal PRIMEs.
    Requiring refit backing (a model trained on outcomes, not weights)
    filters the inflation.

    Returns (adjusted_conviction, reason). Empty reason = no cap applied.
    """
    if raw_conviction < HIGH_CONVICTION_THRESHOLD:
        return raw_conviction, ''
    if refit_conviction is None:
        return HIGH_CONVICTION_CAP, f'high_conv{raw_conviction}_no_refit_cap{HIGH_CONVICTION_CAP}'
    try:
        rc = float(refit_conviction)
    except (TypeError, ValueError):
        return HIGH_CONVICTION_CAP, f'high_conv{raw_conviction}_bad_refit_cap{HIGH_CONVICTION_CAP}'
    if rc < HIGH_CONVICTION_REFIT_MIN:
        return HIGH_CONVICTION_CAP, f'high_conv{raw_conviction}_refit{rc:.0f}_below_gate{HIGH_CONVICTION_REFIT_MIN}_cap{HIGH_CONVICTION_CAP}'
    return raw_conviction, ''


def _implied_prob(odds: Optional[int]) -> Optional[float]:
    if odds is None: return None
    try: o = int(odds)
    except (TypeError, ValueError): return None
    return 100.0 / (o + 100) if o >= 0 else abs(o) / (abs(o) + 100.0)


def juice_cap(book_odds: Optional[int], conviction: int, prop_type: str,
              tier: str, direction: str) -> tuple[int, str]:
    """Recompute conviction ceiling based on juice + historical hit rate.

    Rule: implied_prob(odds) is the break-even threshold. If our historical
    hit rate for this (prop_type, tier, direction) is below implied by more
    than a small buffer, the price is a trap regardless of conviction.

    Returns (new_conviction, reason). reason='' if no cap applied.
    """
    implied = _implied_prob(book_odds)
    if implied is None:
        return conviction, ''
    key = ((prop_type or '').lower(), (tier or '').upper(), (direction or '').lower())
    hist = HISTORICAL_HIT_RATES.get(key)
    if hist is None:
        # No historical data — apply generic juice cap
        # -200+ juice with no history = cap at 65 (LEAN max)
        if book_odds is not None and book_odds < -180:
            return min(conviction, 65), 'juice<-180_no_history_cap65'
        return conviction, ''

    hist_rate_pct, n = hist
    hist_rate = hist_rate_pct / 100.0

    # If historical rate falls short of implied prob by 3pp+ → trap price
    if hist_rate < implied - 0.03:
        # Cap at 55 (LIGHT tier)
        return min(conviction, 55), f'trap_price_implied{implied*100:.1f}pct_vs_hist{hist_rate_pct}pct'
    # If historical rate is at or above implied → keep conviction
    return conviction, ''


def apply_calibration(prop: dict, jerry_verdict: Optional[str] = None) -> dict:
    """One-call pipeline stage: goldmine promotion + fade-flip + juice_cap.
    Returns dict with keys:
      keep (bool) — always True now (fade policy replaced suppress)
      flip_direction (bool) — if True, publish opposite direction
      new_direction (str|None) — the flipped direction if fade
      new_tier (str) — possibly-promoted or fade-based tier
      new_conviction (int) — possibly-capped or fade-boosted conviction
      reason (str) — audit trail
    """
    pt = (prop.get('prop_type') or '').lower()
    tier = (prop.get('tier') or '').upper()
    direction = (prop.get('direction') or '').lower()
    odds = prop.get('book_over_odds') if direction == 'over' else prop.get('book_under_odds')
    conv = prop.get('conviction') or 0

    # 0. FAMILY HARD SUPPRESS (2026-08-05) — dead prop families never publish
    # regardless of tier/conviction. outs_over at 22.7% n=22 = graveyard.
    family_norm = pt if '_' in pt else f'{pt}_{direction}'
    if family_norm in FAMILY_SUPPRESS:
        return {'keep': False, 'flip_direction': False, 'new_direction': direction,
                'new_tier': tier, 'new_conviction': 0,
                'reason': f'family_dead_{family_norm}_hist_hit_below_25pct'}

    # 0.5 ER UNDER fair-price trap (2026-08-05 v2) — must run BEFORE fade
    # logic. Fair-priced er_under on an in-form pitcher hits 6% (1-16, n=17).
    # The "opposite side" (er_over) isn't necessarily good either — the
    # market has a specific reason for the +odds, and it's usually right.
    # Hard suppress instead of fade-flip.
    is_er_trap, er_trap_reason = er_under_fair_price_trap(prop)
    if is_er_trap:
        return {'keep': False, 'flip_direction': False, 'new_direction': direction,
                'new_tier': tier, 'new_conviction': 0,
                'reason': er_trap_reason}

    # 1. Goldmine check first — promotes over fade
    if is_goldmine_skip(pt, tier, direction) and jerry_verdict == 'BACK':
        return {'keep': True, 'flip_direction': False, 'new_direction': direction,
                'new_tier': 'STRONG', 'new_conviction': min(conv + 10, 78),
                'reason': f'goldmine_skip_promoted_{pt}_{direction}'}

    # 2. Fade signal — historical loser at this direction → flip and publish
    is_fade, fade_tier, fade_conv, fade_reason = should_fade(pt, tier, direction)
    if is_fade:
        new_direction = 'under' if direction == 'over' else 'over'
        return {'keep': True, 'flip_direction': True, 'new_direction': new_direction,
                'new_tier': fade_tier, 'new_conviction': fade_conv,
                'reason': fade_reason}

    # 3. Family base-rate prior (2026-08-05) — blend raw conviction with
    # historical family hit rate. Fixes the conviction-inflation issue
    # where PRIME 90 in hits_over (64% hist) was treated identically to
    # PRIME 90 in ha_over (43% hist).
    prior_conv, prior_reason = apply_family_prior(pt, direction, conv)

    # 3a. Form-drift juice trap (2026-08-05) — er_over with L3 ERA loudly
    # elevated vs xERA + juice in -175..-110 hits 33% (n=21). Book has
    # priced in the collapse; only fair-price fades survive.
    is_trap, trap_reason = form_drift_juice_trap(prop)
    if is_trap:
        conv = min(conv, FORM_DRIFT_TRAP_CAP)
        prior_conv = min(prior_conv, FORM_DRIFT_TRAP_CAP)

    # 3b. Multi-signal gate (2026-08-05) — legacy hand-tuned PRIMEs at
    # conviction >= 90 hit 50% vs STRONG 60-69 at 61%. Require refit model
    # backing to hold above 90; otherwise drop to STRONG max.
    refit_conv = prop.get('refit_conviction')
    capped_conv, cap_reason = cap_unsupported_high_conviction(prior_conv, refit_conv)

    # 4. Juice cap (uses possibly-adjusted conviction)
    new_conv, jc_reason = juice_cap(odds, capped_conv, pt, tier, direction)

    # Tier follows conviction: if we dropped below PRIME threshold from PRIME,
    # demote to STRONG so downstream renderers/UI don't mis-signal tier.
    new_tier = tier
    if tier == 'PRIME' and new_conv < 90:
        new_tier = 'STRONG'

    # Combine reasons
    reasons = [r for r in (trap_reason, prior_reason, cap_reason, jc_reason)
               if r and r != 'no_family_prior']
    reason_str = '·'.join(reasons) or 'no_calibration_needed'

    return {'keep': True, 'flip_direction': False, 'new_direction': direction,
            'new_tier': new_tier, 'new_conviction': new_conv,
            'reason': reason_str}


if __name__ == '__main__':
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try: sys.stdout.reconfigure(encoding='utf-8')
        except Exception: pass
    # Smoke tests
    tests = [
        {'prop_type':'outs_over','tier':'SKIP','direction':'over','conviction':60,'book_over_odds':-140},
        {'prop_type':'outs_under','tier':'SKIP','direction':'under','conviction':50,'book_under_odds':-125},
        {'prop_type':'er_over','tier':'STRONG','direction':'over','conviction':75,'book_over_odds':-165},
        {'prop_type':'hits_over','tier':'PRIME','direction':'over','conviction':92,'book_over_odds':-350},
        {'prop_type':'ks_over','tier':'PRIME','direction':'over','conviction':90,'book_over_odds':-142},
        # HIGH-CONV GATE tests (2026-08-05)
        {'prop_type':'er_over','tier':'PRIME','direction':'over','conviction':100,'book_over_odds':-115, 'refit_conviction': None},  # no refit → cap
        {'prop_type':'er_over','tier':'PRIME','direction':'over','conviction':95,'book_over_odds':-125, 'refit_conviction': 55},     # low refit → cap
        {'prop_type':'bb_under','tier':'PRIME','direction':'under','conviction':98,'book_under_odds':-130, 'refit_conviction': 98.4}, # refit supported → keep
        {'prop_type':'hits_over','tier':'PRIME','direction':'over','conviction':82,'book_over_odds':-120, 'refit_conviction': None}, # below 90 → keep
        # FORM-DRIFT JUICE TRAP tests (2026-08-05) — tonight's actual losers
        {'prop_type':'er_over','tier':'PRIME','direction':'over','conviction':100,'book_over_odds':-115,
         'signals': {'l3':'L3 ERA 9.95 elevated','xera_high':'xERA 5.24'},'refit_conviction': None},  # Painter — should cap
        {'prop_type':'er_over','tier':'PRIME','direction':'over','conviction':98,'book_over_odds':-125,
         'signals': {'l3':'L3 ERA 10.29','xera_high':'xERA 4.94'},'refit_conviction': None},          # Kremer — should cap
        {'prop_type':'er_over','tier':'PRIME','direction':'over','conviction':95,'book_over_odds':-135,
         'signals': {'l3':'L3 ERA 10.22','xera_high':'xERA 5.86'},'refit_conviction': None},          # Taillon — should cap (still won by chance)
        {'prop_type':'er_over','tier':'PRIME','direction':'over','conviction':85,'book_over_odds':115,
         'signals': {'l3':'L3 ERA 9.00','xera_high':'xERA 4.50'},'refit_conviction': None},          # same drift + FAIR PRICE (+odds) → no trap
        # ER UNDER FAIR-PRICE TRAP tests (2026-08-05 v2)
        {'prop_type':'er_under','tier':'STRONG','direction':'under','conviction':70,'book_under_odds':105,
         'signals': {'l3':'L3 ERA 1.50','xera':'xERA 3.20'},'refit_conviction': None},               # strong form + +105 → HARD SUPPRESS
        {'prop_type':'er_under','tier':'STRONG','direction':'under','conviction':70,'book_under_odds':-140,
         'signals': {'l3':'L3 ERA 1.50','xera':'xERA 3.20'},'refit_conviction': None},               # same drift but -140 juice → NO trap
        {'prop_type':'er_under','tier':'STRONG','direction':'under','conviction':70,'book_under_odds':105,
         'signals': {'l3':'L3 ERA 9.50','xera':'xERA 4.20'},'refit_conviction': None},               # pitcher NOT in form (drift +5) + +105 → NO trap
    ]
    for t in tests:
        r = apply_calibration(t, jerry_verdict='BACK')
        refit = t.get('refit_conviction')
        print(f'{t["prop_type"]:<12} {t["tier"]:<6} {t["direction"]:<5}  raw={t["conviction"]}  refit={refit}  → keep={r["keep"]}  tier={r["new_tier"]:<6}  conv={r["new_conviction"]}  ({r["reason"]})')
