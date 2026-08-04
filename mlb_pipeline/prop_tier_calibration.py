"""Prop tier calibration — fade + juice + goldmine (2026-08-03).

Sprint-3 response to the 14-day audit that showed props hitting 46% on
the Sweat Card top_8 while totals hit 83%. Provides three helpers any
prop stage can import: should_fade, juice_cap, is_goldmine_skip.

DYNAMIC CALIBRATION (2026-08-03 v2): The FADE_COMBOS, GOLDMINE_SKIP_COMBOS,
and HISTORICAL_HIT_RATES tables below are the STARTING POINT sourced from
today's 90d audit (n=3797). At import time, this module refreshes them
from live prop_bucket_roi data via load_live_calibration() — so as
buckets climb back over 50% they naturally exit the fade list, and new
losers can auto-enter without manual editing.

Refresh cycle:
  - Live check on every pipeline import (~50ms overhead)
  - Adds combo to FADE_COMBOS when hit_rate < 45% AND n >= 15
  - Removes combo from FADE_COMBOS when hit_rate ≥ 55%
  - Adds combo to GOLDMINE_SKIP when SKIP tier hit_rate ≥ 65% AND n >= 15
  - Removes combo from GOLDMINE_SKIP when SKIP hit_rate < 55%
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

    if added_fade or removed_fade or added_gold or removed_gold:
        print(f'[prop_tier_calibration] refreshed from live data: '
              f'+{added_fade} fade / -{removed_fade} fade · '
              f'+{added_gold} goldmine / -{removed_gold} goldmine')


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

    # 3. Juice cap (no fade, no goldmine — just check if price is a trap)
    new_conv, jc_reason = juice_cap(odds, conv, pt, tier, direction)
    return {'keep': True, 'flip_direction': False, 'new_direction': direction,
            'new_tier': tier, 'new_conviction': new_conv,
            'reason': jc_reason or 'no_calibration_needed'}


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
    ]
    for t in tests:
        r = apply_calibration(t, jerry_verdict='BACK')
        print(f'{t["prop_type"]:<12} {t["tier"]:<6} {t["direction"]:<5}  odds={t.get("book_over_odds") or t.get("book_under_odds")}  conv={t["conviction"]} → keep={r["keep"]}, tier={r["new_tier"]}, conv={r["new_conviction"]}  ({r["reason"]})')
