"""Sportsbook classification for sharp/public divergence signals (2026-08-07).

Sport-universal — the same books cover MLB/NFL/NCAAF/NCAAB/NBA/UFC via
The Odds API. Ships alongside sharp_divergence.py which reads per-book
prices from *_line_history tables (mlb_line_history for now, nfl/ncaaf/
etc. to follow when those pipelines add line history).

Classification rationale:
- SHARP: reduced-juice / offshore books that price to true probability
  (Pinnacle / Circa are the gold standard but not in TOA US region;
  LowVig, BetOnline, BetUS, MyBookie are reduced-juice offshore books
  that historically lead US retail by 10-30 min on true price)
- MID: retail books that follow sharp lead within an hour but hold their
  own opinion — DraftKings, BetMGM, Caesars are aggressive at pricing
  but publicly hedged
- PUBLIC: high-volume retail with pronounced public bias — books that
  move heavily on ticket count rather than dollar-weighted flow

Weights used by divergence detector:
- sharp_weight = 1.0 for aggregating sharp side movement
- public_weight = 1.0 for aggregating public side movement
- mid = 0.5 (contributes to both sides at half weight — noise)

Extend BOOK_TIER as new books appear in *_line_history feeds. Unknown
books default to 'mid' and log to console.
"""
from __future__ import annotations

# Canonical book classification. Keys match the source_book strings that
# appear in *_line_history rows (Odds API bookmaker title).
BOOK_TIER = {
    # ── SHARP (reduced-juice / offshore sharp-adjacent) ──────────────
    'LowVig.ag':      'sharp',
    'BetOnline.ag':   'sharp',
    'BetUS':          'sharp',
    'MyBookie.ag':    'sharp',
    'BetAnything':    'sharp',   # offshore reduced-juice
    'Pinnacle':       'sharp',   # gold standard when present
    'Circa':          'sharp',   # Vegas sharp reference
    'Circa Sports':   'sharp',   # alias

    # ── MID (US retail majors — aggressive but publicly hedged) ──────
    'DraftKings':     'mid',
    'FanDuel':        'mid',
    'BetMGM':         'mid',
    'Caesars':        'mid',
    'BetRivers':      'mid',
    'Hard Rock Bet':  'mid',
    'Hard Rock Bet (OH)': 'mid',
    'ESPN BET':       'mid',
    'Fanatics':       'mid',
    'betPARX':        'mid',
    'ReBet':          'mid',   # 2026-08-07 first appearance in tonight's slate
    'theScore Bet':   'mid',   # 2026-08-07 first appearance in tonight's slate

    # ── PUBLIC (recreational — square money flow dominant) ───────────
    'Bovada':         'public',
    'Bally Bet':      'public',
    'MGM':            'public',
    'PointsBet (US)': 'public',
    'WynnBET':        'public',
    'Unibet':         'public',
    'SI Sportsbook':  'public',
    'Barstool':       'public',
    'SuperBook':      'public',
    'TwinSpires':     'public',
    'Fliff':          'public',   # social/exchange, high square
}

# Sport-universal detector thresholds. Tuned conservatively at launch;
# calibrate against backtest hit rate before adjusting.
DIVERGENCE_MIN_SHARP_BOOKS = 2  # need at least this many sharp books for a signal
DIVERGENCE_MIN_PUBLIC_BOOKS = 2
DIVERGENCE_MIN_LINE_DELTA = 0.25  # for totals/spreads
DIVERGENCE_MIN_ML_DELTA_CENTS = 10  # for ML (± 10 cents = material)


def classify(book_name: str) -> str:
    """Return 'sharp' | 'mid' | 'public' for a source_book string.

    Unknown books default to 'mid' — safer to include as neutral than
    to arbitrarily classify. New books surface in the divergence audit
    via 'unknown book' warnings.
    """
    return BOOK_TIER.get(book_name, 'mid')


def is_sharp(book_name: str) -> bool:
    return classify(book_name) == 'sharp'


def is_public(book_name: str) -> bool:
    return classify(book_name) == 'public'


def is_mid(book_name: str) -> bool:
    return classify(book_name) == 'mid'


if __name__ == '__main__':
    # Quick self-check + list
    from collections import Counter
    tiers = Counter(BOOK_TIER.values())
    print(f'BOOK_TIER registry: {len(BOOK_TIER)} books')
    for tier, n in tiers.most_common():
        books = sorted(k for k,v in BOOK_TIER.items() if v == tier)
        print(f'  {tier:6s} ({n:2d}): {", ".join(books)}')
