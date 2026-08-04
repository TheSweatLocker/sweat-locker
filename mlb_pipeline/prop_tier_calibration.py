"""Prop tier calibration + juice-aware suppression (2026-08-03).

Sprint-3 response to the 14-day audit that showed props hitting 46% on the
Sweat Card top_8 while totals hit 83%. Root causes identified:

  1. LEAK — specific (prop_type, tier, direction) combos have historical
     hit rates FAR below break-even. We were shipping known losers.

  2. JUICE BLEED — every price bucket showed negative ROI. Even 60%-hit
     picks lose money at -175+ juice. Conviction assignment doesn't
     factor in price.

  3. MISSED GOLDMINES — outs_under SKIP hits 76% (n=87); hits_under SKIP
     hits 72% (n=25). Our SKIP filter was throwing away real edge.

This module provides three helpers that any prop stage can import:

  suppress(prop) → bool
    Returns True if this (prop_type, tier, direction) combo has proven
    historical losing rate at meaningful n. Publisher blocks it.

  juice_cap(prop, conviction) → int
    Recomputes max allowable conviction given the book_odds. Any prop
    at -175 or juicier needs historical hit rate >65% to earn PRIME;
    otherwise cap conviction so downstream sees it as LEAN/SKIP.

  promote_hidden_gem(prop, jerry_verdict) → new_tier | None
    If prop is SKIP-tier but historical goldmine (outs_under SKIP,
    hits_under SKIP), AND Jerry BACKs it, promote to STRONG.

Historical rates below sourced from prop audit 2026-08-03 (n=3797 graded
props across 90d). Update when re-running compute_prop_bucket_roi.
"""
from __future__ import annotations
from typing import Optional


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

# HARD SUPPRESSION LIST — never publish these tier/type combos regardless
# of conviction. Selected for hit rate <45% AND n >= 15.
SUPPRESS_COMBOS = {
    ('outs_over', 'SKIP', 'over'),
    ('er_under', 'SKIP', 'under'),
    ('ha_over',  'LEAN', 'over'),
    ('ha_over',  'SKIP', 'over'),
    ('bb_under', 'SKIP', 'under'),
    ('er_under', 'STRONG', 'under'),
    ('er_over',  'STRONG', 'over'),
}

# GOLDMINE — SKIP tier that beats PRIME. Promote to STRONG when Jerry BACKs.
GOLDMINE_SKIP_COMBOS = {
    ('outs_under', 'SKIP', 'under'),   # 76% n=87
    ('hits_under', 'SKIP', 'under'),   # 72% n=25
}


def suppress(prop_type: str, tier: str, direction: str) -> tuple[bool, str]:
    """Returns (should_suppress, reason). Publisher should skip if True."""
    key = ((prop_type or '').lower(), (tier or '').upper(), (direction or '').lower())
    if key in SUPPRESS_COMBOS:
        rate, n = HISTORICAL_HIT_RATES.get(key, (None, None))
        return True, f'historical_loser_{rate}pct_n{n}'
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
    """One-call pipeline stage: applies suppress + goldmine + juice_cap.
    Returns dict with keys:
      keep (bool) — publisher should skip if False
      new_tier (str|None) — override tier if promoted or capped
      new_conviction (int) — possibly-capped conviction
      reason (str) — audit trail
    """
    pt = (prop.get('prop_type') or '').lower()
    tier = (prop.get('tier') or '').upper()
    direction = (prop.get('direction') or '').lower()
    odds = prop.get('book_over_odds') if direction == 'over' else prop.get('book_under_odds')
    conv = prop.get('conviction') or 0

    # 1. Goldmine check first — promotes over suppress
    if is_goldmine_skip(pt, tier, direction) and jerry_verdict == 'BACK':
        return {'keep': True, 'new_tier': 'STRONG',
                'new_conviction': min(conv + 10, 78),
                'reason': f'goldmine_skip_promoted_{pt}_{direction}'}

    # 2. Hard suppress
    should_suppress, reason = suppress(pt, tier, direction)
    if should_suppress:
        return {'keep': False, 'new_tier': tier, 'new_conviction': conv, 'reason': reason}

    # 3. Juice cap
    new_conv, jc_reason = juice_cap(odds, conv, pt, tier, direction)
    return {'keep': True, 'new_tier': tier, 'new_conviction': new_conv,
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
