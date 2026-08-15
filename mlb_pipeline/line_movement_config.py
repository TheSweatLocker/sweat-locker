"""Per-sport line-movement cadence + threshold config (2026-08-15).

Universal driver for capture_opening_lines.py, freeze_closing_lines.py,
detect_line_movement.py, and classify_line_moves.py.

CADENCE RATIONALE — line movement is only useful if we snapshot at the
right density for the sport's market rhythm:

  MLB     — 15 games/day, thin market, sharp $ moves overnight the day
            before first pitch. Books post ~24h out. Peak sharp = evening
            of T-1 → 4h pre. Peak public = 2h pre → first pitch.
  NFL     — 16 games/week (Sun), thick market, opens Sun night for next
            week. Sharp $ Tue-Thu. Public Sat-Sun morning. Longer window
            = coarser polling early, tight polling on game day.
  NCAAF   — 40-60 games/Sat, opens Sun/Mon like NFL. Sharper mid-week
            (Wed-Thu) than NFL because lower limits get max'd faster.
  NCAAB   — 30-70 mid-week games, opens night before, very sharp market
            (analytics-driven). Late steam common when book limits go up
            close to tip.
  NHL     — 15/night, opens day-before, softer market than NBA/MLB.
  UFC     — Weekly fight card. Fights open T-7d to T-14d. Wide moves due
            to thin market. Sharp $ moves week of, retail on fight day.

MOVE-SIZE THRESHOLDS — what counts as a "meaningful" move differs by sport:

  MLB total 0.5 = normal (multiple books do this in noise)
  NFL total 0.5 = notable (thick market rarely moves this much w/o cause)
  NFL spread 0.5 = big deal (key numbers matter — 3, 7, 10)
  NCAAB total 1.0 = normal (higher-total volatility)

`min_move_size` is the delta that qualifies for classification. Smaller
moves get filtered as noise.
"""
from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────
# Cadence: hours pre-event to fire specific ops
# ──────────────────────────────────────────────────────────────────────
#   opening_offset_hrs — when to capture "opening line" snapshot (approx
#     matches when books first post the line — sport-specific)
#   close_offset_min   — minutes BEFORE first-pitch/kickoff/tip to freeze
#     the true closing line
#   poll_intervals     — list of (from_hrs_pre, to_hrs_pre, interval_min)
#     tuples describing polling density in each window.
#     Example: (24, 4, 60) = every 60 min from T-24h to T-4h.
#              (4, 0.25, 15) = every 15 min from T-4h to T-15min.

SPORT_CONFIG = {
    'MLB': {
        'opening_offset_hrs': 20,         # capture opening line T-20h
        'close_offset_min':   5,          # freeze close T-5min
        'poll_intervals': [
            (24, 4, 60),                  # T-24h → T-4h every 60 min
            (4,  0.083, 15),              # T-4h → T-5min every 15 min
        ],
        'min_move_size': {
            'spread': 0.5,
            'total':  0.5,
            'ml':     10,                 # american odds points
        },
        'sharp_money_threshold':   60,    # money% ≥ 60 = sharp lean
        'public_bets_threshold':   65,    # bets%  ≥ 65 = public lean
        'divergence_threshold':    20,    # |money% - bets%| ≥ 20 = split
    },

    'NFL': {
        'opening_offset_hrs': 6*24,       # opens Sun night for next week
        'close_offset_min':   15,
        'poll_intervals': [
            (168, 48, 6*60),              # week-out → T-48h every 6h
            (48,  6,  60),                # T-48h → T-6h every 60 min
            (6,   0.25, 15),              # T-6h → T-15min every 15 min
        ],
        'min_move_size': {
            'spread': 0.5,                # KEY NUMBER TERRITORY (3/7/10)
            'total':  0.5,
            'ml':     10,
        },
        'sharp_money_threshold':   58,    # NFL market is thicker — lower bar
        'public_bets_threshold':   68,    # NFL public heavier on favs
        'divergence_threshold':    18,
    },

    'NCAAF': {
        'opening_offset_hrs': 6*24,       # opens Sun/Mon for Sat
        'close_offset_min':   15,
        'poll_intervals': [
            (144, 72, 6*60),              # 6d-out → T-72h every 6h
            (72,  6,  60),                # T-72h → T-6h every 60 min
            (6,   0.25, 15),              # T-6h → T-15min every 15 min
        ],
        'min_move_size': {
            'spread': 1.0,                # thinner limits, wider moves normal
            'total':  1.0,
            'ml':     10,
        },
        'sharp_money_threshold':   60,
        'public_bets_threshold':   70,
        'divergence_threshold':    20,
    },

    'NCAAB': {
        'opening_offset_hrs': 18,         # opens night before typically
        'close_offset_min':   10,
        'poll_intervals': [
            (24, 4, 120),                 # T-24h → T-4h every 2h
            (4,  0.25, 15),               # T-4h → T-15min every 15 min
        ],
        'min_move_size': {
            'spread': 1.0,                # NCAAB total volatility higher
            'total':  1.0,
            'ml':     10,
        },
        'sharp_money_threshold':   60,
        'public_bets_threshold':   65,
        'divergence_threshold':    20,
    },

    'NHL': {
        'opening_offset_hrs': 18,
        'close_offset_min':   10,
        'poll_intervals': [
            (24, 3, 120),                 # T-24h → T-3h every 2h
            (3,  0.25, 30),               # T-3h → T-15min every 30 min
        ],
        'min_move_size': {
            'spread': 0.5,                # puck line
            'total':  0.5,
            'ml':     10,
        },
        'sharp_money_threshold':   58,
        'public_bets_threshold':   65,
        'divergence_threshold':    18,
    },

    'UFC': {
        'opening_offset_hrs': 14*24,      # opens T-14d, but volume light early
        'close_offset_min':   30,         # tighter than team sports
        'poll_intervals': [
            (336, 48, 24*60),             # T-14d → T-48h daily
            (48,  6,  60),                # T-48h → T-6h every 60 min
            (6,   0.5, 30),               # T-6h → T-30min every 30 min
        ],
        'min_move_size': {
            'ml':     15,                 # UFC is ML-only; wider moves normal
        },
        'sharp_money_threshold':   55,    # UFC public softer; sharp lower bar
        'public_bets_threshold':   70,
        'divergence_threshold':    20,
    },
}


def get_config(sport: str) -> dict:
    """Return config for a sport (uppercase). Falls back to MLB if unknown."""
    return SPORT_CONFIG.get((sport or '').upper(), SPORT_CONFIG['MLB'])


def is_move_meaningful(sport: str, market: str, delta_abs: float) -> bool:
    """Whether a line move of `delta_abs` on this market/sport is worth classifying."""
    cfg = get_config(sport)
    thresh = cfg.get('min_move_size', {}).get((market or '').lower(), 0.5)
    return abs(delta_abs) >= thresh


def classify_split(sport: str, money_pct: float | None, bets_pct: float | None,
                   line_moved_toward_side: bool) -> str:
    """Classify a line move from a SINGLE source's split. Returns one of:
    SHARP_MOVE | PUBLIC_MOVE | RLM | CONSENSUS | NEUTRAL.

    For the multi-source confirmed variant use combine_classifications().
    """
    if money_pct is None or bets_pct is None:
        return 'NEUTRAL'
    cfg = get_config(sport)
    sharp_thr = cfg['sharp_money_threshold']
    pub_thr   = cfg['public_bets_threshold']
    div_thr   = cfg['divergence_threshold']
    if not line_moved_toward_side:
        # Line moved AWAY from the side that has public $ → classic RLM
        if bets_pct >= pub_thr:
            return 'RLM'
        return 'NEUTRAL'
    if money_pct >= sharp_thr and bets_pct < pub_thr:
        return 'SHARP_MOVE'
    if bets_pct >= pub_thr and money_pct < sharp_thr:
        return 'PUBLIC_MOVE'
    if money_pct >= sharp_thr and bets_pct >= pub_thr:
        return 'CONSENSUS'
    if abs(money_pct - bets_pct) >= div_thr:
        return 'SHARP_MOVE' if money_pct > bets_pct else 'PUBLIC_MOVE'
    return 'NEUTRAL'


# ──────────────────────────────────────────────────────────────────────
# Multi-source combination (cross-verification quality gate)
# ──────────────────────────────────────────────────────────────────────
#
# We have TWO public split sources (OddsCrowd + Fadereport) which
# routinely disagree on any given game. If we surfaced "SHARP: money% 68"
# to users from just one source, we'd be asserting false certainty.
# Solution: use cross-source AGREEMENT as the quality gate.
#
# Combined classification hierarchy (loudest → quietest):
#   *_CONFIRMED — both sources agree, high confidence, surface numbers
#   *_LEAN      — only one source available OR only one says the tag,
#                 muted badge, DO NOT surface individual %s
#   SOURCES_SPLIT — sources disagree on direction (one says sharp, other
#                   says public) — grey badge, "sources disagree"
#   PATTERN_ONLY  — no split data available at all, raw pattern only
#   NEUTRAL       — both sources present but neither meaningful

_LOUD_TAGS = {'SHARP_MOVE', 'PUBLIC_MOVE', 'RLM', 'CONSENSUS'}


def combine_classifications(oddscrowd_cls: str | None,
                             fadereport_cls: str | None) -> tuple[str, str]:
    """Combine two source classifications into (final_tag, confidence).

    Returns (classification, confidence_level) where confidence is one of:
      'CONFIRMED'    — both sources agree on a loud tag
      'LEAN'         — only one loud tag; the other is missing or NEUTRAL
      'SPLIT'        — sources disagree on which loud tag applies
      'PATTERN_ONLY' — neither source available
      'NEUTRAL'      — both sources say NEUTRAL
    """
    oc = oddscrowd_cls or None
    fr = fadereport_cls or None
    both_missing = oc is None and fr is None
    if both_missing:
        return ('PATTERN_ONLY', 'PATTERN_ONLY')
    # Only one source available
    if oc is None or fr is None:
        present = oc or fr
        if present in _LOUD_TAGS:
            return (present + '_LEAN', 'LEAN')
        return ('NEUTRAL', 'NEUTRAL')
    # Both sources present
    if oc == fr:
        if oc in _LOUD_TAGS:
            return (oc + '_CONFIRMED', 'CONFIRMED')
        return ('NEUTRAL', 'NEUTRAL')
    # They differ — check for split conflicts
    oc_loud = oc in _LOUD_TAGS
    fr_loud = fr in _LOUD_TAGS
    if oc_loud and fr_loud:
        # Both loud but disagree → true SOURCES_SPLIT
        return ('SOURCES_SPLIT', 'SPLIT')
    if oc_loud or fr_loud:
        # One loud, other neutral → lean toward the loud one
        loud = oc if oc_loud else fr
        return (loud + '_LEAN', 'LEAN')
    return ('NEUTRAL', 'NEUTRAL')
