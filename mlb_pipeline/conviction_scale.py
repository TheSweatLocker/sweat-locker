"""Per-sport conviction scale, so every sport can reach a gate.

══ 2026-10-30 · ANDY: "NBA and NHL conviction needs to be established so we
   can tune as we go... it can be biased, that's all I'm asking" ══

Raw `conviction` is not comparable across sports. Measured on all 2,198
jerry_reads:

    sport     n     p50  p75  p90  p95  max   reaches the old 70 gate
    MLB     822      62   75   87   92  100   34.9%
    NCAAF   666      59   73   80   84  100   30.9%
    NFL     170      55   63   70   75   86   10.6%
    NHL     293      59   60   63   65   78    2.0%
    NBA     127      51   53   60   63   65    0 of 127

So a single absolute gate of 70 made POTD structurally unreachable for NBA and
nearly so for NHL, which are the two sports that play nightly. A 64 in NHL sits
at its 90th percentile; a 64 in MLB is below its median. Same number, opposite
meaning.

This maps each sport's OWN distribution onto one comparable 0-100 axis:
p50 -> 50, p75 -> 70, p90 -> 85, max -> 100, linear between the anchors. A
sport's top-of-range pick therefore reads as high conviction regardless of the
raw scale it happens to use.

WHAT THIS IS AND IS NOT
  * It IS a rescaling so every sport is ELIGIBLE and can be tuned from live
    results. Andy's call, made explicitly on 10-30 and deliberately not
    gated on sample size: NBA/NHL are nightly and need takes now.
  * It is NOT a claim that high conviction wins. It does not on NCAAF —
    NCAAF's own top decile is 36.67% / ROI -33.76% on n=30
    (project_conviction_inverted_outside_mlb_1030). MLB's top quartile is
    72.97% / +22.79%. So the scale is applied where it is asked for and the
    ANCHORS stay tunable here, in one place, as per-sport records come in.
  * NCAAF and MLB keep their existing absolute behaviour by default —
    see POTD_MIN_SCALED / SPORT_OVERRIDE below — because nothing asked for
    those to move and NCAAF's direction is the one measured negative.

TUNING: edit ANCHORS when a sport's distribution shifts (re-run
_conv_dist.py), and POTD_MIN_SCALED to move every sport's bar at once.
"""
from __future__ import annotations

# sport -> (p50, p75, p90, max) of that sport's own conviction distribution.
# Measured 2026-10-30 on jerry_reads. Re-measure and edit as seasons move.
ANCHORS = {
    'MLB':   (62.0, 75.0, 87.0, 100.0),
    'NCAAF': (59.0, 73.0, 80.0, 100.0),
    'NFL':   (55.0, 63.0, 70.0, 86.0),
    'NHL':   (59.0, 60.0, 63.0, 78.0),
    'NBA':   (51.0, 53.0, 60.0, 65.0),
    'NCAAB': (55.0, 62.0, 70.0, 90.0),   # provisional until NCAAB has reads
    'UFC':   (25.0, 32.0, 72.0, 87.0),
}

# Where the anchors land on the shared axis.
_AXIS = (50.0, 70.0, 85.0, 100.0)

#: One bar for every sport, on the SCALED axis. 70 == that sport's own p75.
POTD_MIN_SCALED = 70.0

#: Sports that keep an absolute raw gate instead of the scaled one. MLB and
#: NCAAF are excluded from rescaling on purpose: MLB is the only sport where
#: conviction measurably earns its gate, and NCAAF is the only one where it is
#: measurably inverted. Neither was asked to change.
SPORT_ABSOLUTE_GATE = {'MLB': 70, 'NCAAF': 70}


def scaled(sport: str, conviction) -> float | None:
    """Raw conviction -> 0-100 comparable across sports. None if unusable."""
    try:
        c = float(conviction)
    except (TypeError, ValueError):
        return None
    if c != c:
        return None
    a = ANCHORS.get(str(sport or '').upper())
    if not a:
        return c          # unknown sport: pass the raw number through
    pts = [0.0] + list(a)
    axis = [0.0] + list(_AXIS)
    if c <= pts[0]:
        return 0.0
    for i in range(1, len(pts)):
        if c <= pts[i]:
            lo_p, hi_p = pts[i - 1], pts[i]
            lo_a, hi_a = axis[i - 1], axis[i]
            if hi_p == lo_p:
                return hi_a
            return lo_a + (hi_a - lo_a) * (c - lo_p) / (hi_p - lo_p)
    return 100.0


def potd_eligible(sport: str, conviction) -> bool:
    """Is this read eligible for POTD under its own sport's bar?"""
    sp = str(sport or '').upper()
    if sp in SPORT_ABSOLUTE_GATE:
        try:
            return float(conviction) >= SPORT_ABSOLUTE_GATE[sp]
        except (TypeError, ValueError):
            return False
    s = scaled(sp, conviction)
    return s is not None and s >= POTD_MIN_SCALED


def rank_key(sport: str, conviction) -> float:
    """Cross-sport ranking value. Raw conviction cannot be compared between
    sports, so POTD must pick its winner on the scaled axis or a 64 in NHL
    always loses to a 71 in NCAAF that means less."""
    s = scaled(sport, conviction)
    return s if s is not None else 0.0


if __name__ == '__main__':
    print('sport   raw -> scaled   (POTD eligible at '
          f'{POTD_MIN_SCALED:.0f} scaled)')
    for sp in ('MLB', 'NCAAF', 'NFL', 'NHL', 'NBA'):
        row = []
        for c in (50, 53, 56, 60, 64, 70, 75, 80, 87):
            row.append(f'{c}->{scaled(sp, c):.0f}'
                       + ('*' if potd_eligible(sp, c) else ' '))
        print(f'{sp:6s} ' + '  '.join(row))
    print('\n* = eligible.  Tonight: NHL 64, NBA 53, NCAAF 56')
    for sp, c in (('NHL', 64), ('NBA', 53), ('NCAAF', 56)):
        print(f'   {sp:6s} raw {c} -> scaled {scaled(sp, c):5.1f}  '
              f'eligible={potd_eligible(sp, c)}')


# ══════════════════════════════════════════════════════════════════════════
# 2026-10-30 · conviction_rank — a SEPARATE number that actually separates
#
# Andy: "NBA and NHL conviction needs to be established so we can tune as we
# go... we need NBA takes and reads and some need conviction."
#
# The compression is in the ensemble formula, which is sport-blind:
#     conviction = 50 + min(win_score*10, 30) + min(margin*20, 15)
# That saturates at win_score = 3.0. Measured spans of the ensemble's own
# score and margin, per sport, off stored primary_play rows:
#
#     sport   score p50   p90    max  | margin p90   max   | conv p50  max
#     NBA          0.07  0.11   0.20  |        0.11  0.20  |       52   65
#     NHL          0.17  0.73   1.28  |        0.48  1.20  |       59   78
#     NFL          0.21  0.98   2.25  |        0.86  1.34  |       55   86
#     NCAAF        0.43  1.43   3.51  |        1.23  2.50  |       61  100
#
# NCAAF reaches 3.51 and uses the whole range. NBA's MAXIMUM is 0.20, which
# buys 2 points of the available 30 — so every NBA pick lands on 51-53 and no
# NBA pick can ever be ranked above another.
#
# WHY THIS IS A NEW FIELD AND NOT A REWRITE OF `conviction`
# `conviction` is consumed as a WIN PROBABILITY, not as a display score:
# model_edge.edge_pp() is literally `conviction - implied_prob*100`, and that
# edge drives NO_EDGE_TIER_CAP, which is what demotes a pick to COVERAGE when
# the price is better than our number. Rescaling NBA conviction from 51 to 75
# would print a ~20pp edge on every NBA pick, switch off the cap that is
# currently telling the truth, and manufacture plays out of nothing. That is
# the exact failure this repo has paid for before.
#
# So: `conviction` stays the probability. `conviction_rank` is the per-sport
# 0-100 separation number — what POTD ranks on, what orders a card, and what
# weekly attribution tunes. Two numbers because they answer two questions.

#: sport -> (score_p90, margin_p90), measured 2026-10-30 on primary_play.
#: Re-run _score_spans.py and edit these as the seasons move.
SIGNAL_SPANS = {
    'NBA':   (0.11, 0.11),
    'NHL':   (0.73, 0.48),
    'NFL':   (0.98, 0.86),
    'NCAAF': (1.43, 1.23),
    'MLB':   (2.48, 1.50),
    'NCAAB': (0.73, 0.48),   # provisional, mirrors NHL until NCAAB has rows
}

#: p90 lands here, leaving headroom above it so an exceptional night can
#: still stand out instead of clipping with everything else at 100.
_P90_TARGET = 0.75
RANK_FLOOR, RANK_CEIL = 40.0, 97.0


def rank_from_signals(sport: str, win_score, margin) -> float | None:
    """Per-sport 0-100 separation score from the ensemble's own signals.

    p90 of a sport's score/margin maps to the same place for every sport, so
    a decisive NBA pick and a decisive NCAAF pick read alike. Weighted 2:1
    toward score over margin, matching the 30:15 split of the formula this
    replaces for ranking purposes.
    """
    try:
        s = float(win_score)
    except (TypeError, ValueError):
        s = 0.0
    try:
        m = max(0.0, float(margin))
    except (TypeError, ValueError):
        m = 0.0
    sp = str(sport or '').upper()
    s_p90, m_p90 = SIGNAL_SPANS.get(sp, (1.0, 1.0))
    s_n = min(1.0, (s / (s_p90 / _P90_TARGET)) if s_p90 > 0 else 0.0)
    m_n = min(1.0, (m / (m_p90 / _P90_TARGET)) if m_p90 > 0 else 0.0)
    combined = (2.0 * s_n + 1.0 * m_n) / 3.0
    return round(RANK_FLOOR + (RANK_CEIL - RANK_FLOOR) * combined, 1)
