"""Invariants every published receipt must satisfy, at the one chokepoint.

══ 2026-10-30 · WHY THIS IS ONE MODULE AND NOT FOUR PATCHES ═══════════════
ANDY: "we need to fix the 4 things wrong, espesillay so these same issue
keep happening."

Tonight's board had four defects and every one of them is the same shape — a
surface wrote a receipt that contradicted itself, and nothing between the
composer and the table objected:

  1. Houston -6.5 and Detroit -8.5 captured with pick_odds = NULL. A graded
     play with no price gets priced at a guess, and flat -110 inflates every
     figure it touches (feedback_flat_110_inflates_every_ml_figure).
  2. daily_degen receipt said "3 legs" while its cache payload held 2.
  3. An NBA `total` fade receipt was published for a market measured
     negative and already in FADE_BANNED (45.5%, ROI -13.2%, n=33) — the
     ban lives in the composer, so a stale payload walked straight past it.
  4. Houston -6.5 published at STRONG while our own projection had it short
     of the line.

`public_receipt.capture()` already refuses a line-bearing market with no
line (`unactionable`). That guard is the right shape and the right place —
it is simply the only one of its kind. Four more defects means four more
rules, and they belong beside it rather than in each composer, because a
guard on one of eight sources is not a guard
(feedback_guard_belongs_at_the_chokepoint): the 10-22 future-game rule had
to be moved here for exactly this reason after 237 bad rows got in through
the one backfill path that never received it.

WHAT THIS DELIBERATELY DOES NOT DO
It does not judge whether a pick is GOOD. Tier, edge and coherence are the
engine's job, and this module has no model. It only refuses a row that is
internally inconsistent — a record that cannot be graded honestly whatever
the pick turns out to be.

Severity is split on purpose:
  * REFUSE  — the row cannot be graded honestly. Never written.
  * WARN    — the row is gradeable but suspect. Written, and printed loudly,
              because a surface that silently drops picks is the bug that
              hid MLB's empty prop pool for a season.
"""
from __future__ import annotations

from typing import Any, Optional

#: markets whose receipt is meaningless without a price. A moneyline or a
#: spread settles at a number someone laid; "no odds" is not a bet.
PRICE_REQUIRED_MARKETS = {
    'ml', 'rl', 'spread', 'puckline', 'pl', 'total', 'prop',
    'parlay', 'chalk_parlay', 'potd', 'dotd', 'fight',
}

#: A price outside this band is almost certainly a parse error rather than a
#: real quote. +10000 longshots exist, but not on these surfaces.
PRICE_SANITY = (-100000.0, 100000.0)

#: ══ 2026-11-04 · ANDY: "STOP PUBLISHING -200000!!!!" ═══════════════════
#: A -20000 moneyline is not a parse error, which is why PRICE_SANITY let it
#: through. Validated against two independent sources before concluding
#: that: line_history -20000 vs ncaab_game_context -20000 on the same game,
#: with the dog sides agreeing exactly at +3300/+3500. Books really do post
#: these on early-season mismatches.
#:
#: Once backfill_ncaab_receipt_prices filled the NULLs, 98 priced ML
#: receipts turned out to sit past -300, and 60 of them carry the word
#: "lean" in their own label. 21 are on a PUBLISHABLE tier (STRONG 18,
#: PRIME 1, LEAN 2) and 3 are on the sweat_card itself, so this was not
#: only coverage.
#:
#: -300 is NOT A NEW RULE. It is the number already used in four places:
#:   defensive_gates._ml_odds_too_juiced(max_juice=-300)
#:   generate_sharp_card.SOLE_PICK_ML_JUICE_MAX = -300
#:   apply_ml_juice_cap_to_card.py (card + Sharp, -300)
#:   jerry_anchor_potd POTD ML gate, -250 (stricter, and it stays stricter)
#: Breakeven at -300 is 75%; at -20000 it is 99.5%, which is not a bet.
#:
#: Graded REFUSE on a play surface and WARN on coverage, matching the
#: `_is_play` split this file already uses: game_read is an opinion on every
#: game and is deliberately not a PLAY_SURFACE, so refusing it would delete
#: coverage. The label claiming a "lean" at -20000 is a separate defect,
#: fixed at the source rather than by rewriting a frozen column here.
ML_JUICE_MAX = -300.0

#: Surfaces that publish a PLAY: the record is a claim we staked, so a row
#: that cannot be priced cannot be graded honestly and is refused.
#:
#: `game_read` is deliberately NOT here. It is COVERAGE -- an opinion on
#: every game, most of it never published as a play -- and refusing those
#: rows would delete the coverage record instead of fixing its price, which
#: is a worse trade than recording it priceless and flagging it. 406 of
#: 9,593 receipts since the 09-19 pricing floor carry NULL pick_odds and
#: ZERO are graded Win/Loss yet, so nothing published is wrong today; the
#: real repair is recovering the price at the composer (R58), and this guard
#: exists to stop NEW priceless PLAYS while that is done.
PLAY_SURFACES = {
    'sweat_card', 'sharp_card', 'potd', 'dotd', 'fade',
    'ledger', 'daily_degen', 'prop_jerry', 'ladder',
}


#: the number in an over/under label, for the rule-1b label check. Anchored
#: on the direction word so a team name containing digits cannot match.
_LABEL_NUM_RE = _re_compile = __import__('re').compile(
    r'\b(?:over|under|o|u)\s*(\d+(?:\.\d+)?)\b', __import__('re').I)


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _banned(sport, market) -> Optional[str]:
    """Is this (sport, market) one the FADE backtest measured NEGATIVE?

    Imported from generate_the_fade rather than restated so the ban cannot
    drift between the composer that applies it and the guard that enforces
    it. An import failure returns None — this module must never be the
    reason a surface loses its card.
    """
    try:
        from generate_the_fade import FADE_BANNED
    except Exception:
        return None
    key = (str(sport or '').upper(), str(market or '').lower())
    val = FADE_BANNED.get(key) if isinstance(FADE_BANNED, dict) else None
    return str(val) if val else None


#: markets whose lines books post in half-point increments. Measured
#: 2026-11-02: 17 of 27,432 receipts since 08-01 violate this, all of them
#: `total`, and all of them the engine's own projection.
LINE_HALF_POINT_MARKETS = ('spread', 'rl', 'total', 'puckline', 'pl')


# ══ 2026-11-02 · WHAT A RECEIPT PATCH CANNOT CHANGE ═══════════════════════
# A PATCH of a frozen column returns **HTTP 200 having changed nothing**, so
# a repair script reports success and writes no repair. That is
# feedback_fix_the_cache_not_the_frozen_receipt, and I walked into it again
# today trying to re-file 44 mis-dated NCAAB receipts onto their real play
# date: 44 of 44 came back 200 with the old date.
#
# The authority is freeze_receipt_identity in
# 20260918a_public_receipts.sql ("identity fields FROZEN after insert; only
# result / actual_value / graded_at may change"), amended by
# 20260918b_receipt_enrichment_allow.sql.
#
# `audit` is the trap inside the trap. 20260918b freezes it only once it is
# non-NULL:
#       IF OLD.audit IS NOT NULL THEN NEW.audit := OLD.audit;
# so the FIRST write lands and every correction after it is silently
# dropped. My re-file wrote `refiled_at` into 44 previously-NULL audit blobs
# while the date itself did not move, and those stamps can no longer be
# removed or corrected. They are internal provenance only (nothing in app/
# reads public_receipts), but they now describe a change that did not
# happen, which is exactly the defect in
# feedback_comment_asserting_a_measurement.
#
# So: check here BEFORE patching, and if a receipt genuinely must be
# re-issued, use void+replace
# (20261025a_receipt_void_allows_replacement.sql) — a decision about a
# published record, not a clerical repair.
FROZEN_RECEIPT_COLS = frozenset({
    'sport', 'surface', 'market', 'game_date', 'published_at', 'player_name',
    'prop_type', 'pick_side', 'pick_line', 'pick_odds',
})
#: Writable exactly once, then frozen. Treat a non-NULL value as final.
WRITE_ONCE_RECEIPT_COLS = frozenset({'audit'})
#: The only columns a grade is allowed to move.
MUTABLE_RECEIPT_COLS = frozenset({'result', 'actual_value', 'graded_at'})


def check_receipt_patch(body: dict, existing: Optional[dict] = None
                        ) -> list[str]:
    """Reasons `body` would not take as a PATCH on public_receipts.

    Empty list means every key is writable. Pass `existing` (the row as
    stored) so the write-once columns can be judged — without it, a
    write-once column is only reported as needing that check, because
    whether it lands depends on the stored value rather than the payload.

    Call this instead of discovering the freeze from a 200 that changed
    nothing.
    """
    out: list[str] = []
    for k in sorted(body):
        if k in FROZEN_RECEIPT_COLS:
            out.append(f'{k}: frozen by freeze_receipt_identity — a PATCH '
                       f'returns 200 and changes nothing. Use void+replace '
                       f'if the row must be re-issued.')
        elif k in WRITE_ONCE_RECEIPT_COLS:
            if existing is None:
                out.append(f'{k}: write-once (frozen once non-NULL). Pass '
                           f'the stored row to check whether it is already '
                           f'set.')
            elif existing.get(k) is not None:
                out.append(f'{k}: already set, so this write is silently '
                           f'dropped. Its first value is final.')
    return out


def check_row(row: dict, surface: str) -> tuple[Optional[str], Optional[str]]:
    """-> (severity, reason). severity is 'REFUSE', 'WARN' or None."""
    market = str(row.get('market') or '').lower()
    sport = str(row.get('sport') or '').upper()

    # ══ 2026-11-05 · AN EARLY *WARN* WAS HIDING A LATER *REFUSE* ═══════
    # Every rule here used to `return` on the spot, including the ones that
    # only WARN. So a row that was BOTH priceless and carrying a fabricated
    # line returned ('WARN', 'no pick_odds') from rule (1) and never reached
    # rule (1b) at all -- and priceless is exactly the population that has
    # fabricated lines. Replaying the new label check over 42,405 receipts
    # found 2 rows, against the 14 the same condition matches when measured
    # directly: the other 12 were short-circuited by a WARN.
    #
    # A gate must fail CLOSED. So a WARN is now REMEMBERED and the rules
    # keep running; the most severe finding wins, and the WARN is returned
    # only if nothing refuses
    # ([[feedback_a_display_label_became_a_gating_tier]] on gates failing
    # closed, [[feedback_fix_every_writer_to_the_table]] on one guard not
    # being a guard).
    _warn = None

    # (1) A PLAY WITH NO PRICE. Mirrors `unactionable`, for the price.
    if market in PRICE_REQUIRED_MARKETS:
        odds = _f(row.get('pick_odds'))
        _is_play = surface in PLAY_SURFACES
        if odds is None:
            _m1 = (f'{market} receipt with no pick_odds — a graded play '
                   f'with no price is priced at a guess')
            if _is_play:
                return 'REFUSE', _m1
            _warn = _warn or _m1
        elif not (PRICE_SANITY[0] <= odds <= PRICE_SANITY[1]) or odds == 0:
            _m1 = f'pick_odds {odds:g} is not a plausible price'
            if _is_play:
                return 'REFUSE', _m1
            _warn = _warn or _m1
        # A real price can still be an impossible bet. See ML_JUICE_MAX.
        elif market == 'ml' and odds < ML_JUICE_MAX:
            _be = 100.0 * abs(odds) / (abs(odds) + 100.0)
            _m1 = (f'ml at {odds:g} needs {_be:.1f}% to break even — past '
                   f'the {ML_JUICE_MAX:g} juice cap this repo already '
                   f'enforces on the card, the Sharp and the LR override')
            if _is_play:
                return 'REFUSE', _m1
            _warn = _warn or _m1

    # (1b) A LINE NO BOOK EVER POSTED.
    #
    # ══ 2026-11-02 · NCAAB WAS PUBLISHING ITS OWN PROJECTION AS A LINE ══
    # From the 11-01 audit: 57 live NCAAB game_read receipts, every one of
    # them priceless, and sixteen carrying lines like
    #
    #     Under 157.83333333333334
    #     Under 154.16666666666666
    #     Under 149.25
    #
    # No book posts a total to fourteen decimal places. Those are the
    # engine's own projected_total written into the field that is supposed
    # to hold the market's number, which makes the receipt a record of a bet
    # that could not be placed. Same family as
    # project_jerry_published_fake_lines_1019, where 48% of game_read
    # spreads were numbers nobody posted, up to 5 points off.
    #
    # MEASURED BEFORE SHIPPING, across all 27,432 receipts since 08-01:
    # exactly 17 rows (0.06%) carry a pick_line that is not a multiple of
    # 0.5 -- 16 NCAAB totals and 1 MLB total, 1 of the 17 graded. Nothing
    # else in the table violates it, so this refuses the defect and
    # essentially nothing else, and it cannot become a permanently-red
    # guard (feedback_a_detector_must_know_the_intentional_case).
    #
    # Scoped to the GAME markets the measurement covered. Books post these
    # in half-point increments; a prop can be a 0.5 too but no prop row in
    # the table violates this, so there is nothing to claim about them.
    #
    # REFUSE on every surface, not just play surfaces. Unlike a missing
    # price -- which leaves a read gradeable as a win or loss and is
    # therefore only a WARN off a play surface, rule (1) -- a fabricated
    # line means the row does not describe a real bet at all, so there is
    # nothing a reader could do with it.
    if market in LINE_HALF_POINT_MARKETS:
        _ln = _f(row.get('pick_line'))
        if _ln is not None and abs(_ln * 2 - round(_ln * 2)) > 1e-6:
            return ('REFUSE',
                    f'pick_line {_ln!r} is not a half-point line — no book '
                    f'posts a {market} at that number, so this is a '
                    f'projection written into the market field')
        # ══ 2026-11-05 · THE FAKE NUMBER CAN HIDE IN THE LABEL ═════════
        # The check above keys on pick_line, so a projection written into
        # pick_label while pick_line stays NULL walks straight past it --
        # and _void_fabricated_line_receipts_1102.py, which used the same
        # key, could not see them either.
        #
        # MEASURED across all 42,405 receipts since 09-01: 14 rows carry a
        # non-half-point number in the label with pick_line NULL, 4 of them
        # still ungraded. 'Under 155.7', 'Under 154.75', 'Under 155.3' --
        # the same NCAAB projected totals as the pick_line cases, just
        # stored one field over.
        #
        # 14 rows out of 42,405 is 0.03%, so like its sibling this refuses
        # the defect and essentially nothing else, and cannot become a
        # permanently-red guard
        # ([[feedback_a_detector_must_know_the_intentional_case]]).
        if _ln is None:
            _m = _LABEL_NUM_RE.search(str(row.get('pick_label') or ''))
            if _m:
                _lv = _f(_m.group(1))
                if (_lv is not None
                        and abs(_lv * 2 - round(_lv * 2)) > 1e-6):
                    return ('REFUSE',
                            f'pick_label names {_lv!r} and pick_line is '
                            f'NULL — no book posts a {market} at that '
                            f'number, so the label carries a projection '
                            f'the line field never recorded')

    # (2) A MARKET MEASURED NEGATIVE, enforced where every source passes.
    # Only on the surface the ban was measured for; a game read is allowed
    # to hold an opinion about a market we refuse to PUBLISH as a play.
    if surface == 'fade':
        why = _banned(sport, market)
        if why:
            return 'REFUSE', f'{sport} {market} is in FADE_BANNED ({why})'

    # (3) A MULTI-LEG TICKET MUST AGREE WITH ITS OWN LEG COUNT.
    # daily_degen published "3 legs" over a 2-leg payload, so the record
    # claimed a parlay nobody could reconstruct or grade.
    if market in ('parlay', 'chalk_parlay'):
        audit = row.get('audit') if isinstance(row.get('audit'), dict) else {}
        claimed = _f(audit.get('n_legs'))
        actual = audit.get('legs')
        actual_n = float(len(actual)) if isinstance(actual, list) else None
        if claimed is not None and actual_n is not None and claimed != actual_n:
            return 'REFUSE', (f'ticket claims {claimed:g} legs but carries '
                              f'{actual_n:g} — ungradeable as published')
        label = str(row.get('pick_label') or '')
        if actual_n is not None and 'leg' in label:
            import re as _re
            m = _re.search(r'(\d+)\s*legs?', label)
            if m and float(m.group(1)) != actual_n:
                return 'REFUSE', (f'label says {m.group(1)} legs but payload '
                                  f'carries {actual_n:g}')

    # (4) A SIDE-BEARING MARKET WITH NO SIDE is gradeable only by guessing.
    if market in ('ml', 'rl', 'spread', 'puckline', 'pl') and not row.get('pick_side'):
        _warn = _warn or f'{market} receipt carries no pick_side'

    # ══ (5) 2026-10-31 · A DECLARED NO-PLAY IS NOT A PLAY (R121/R122) ══
    # tier=PASS is the engine saying it has no opinion worth acting on, and
    # SKIP is the same statement. Published as a receipt with a line, a
    # price and a result, a PASS becomes a graded bet: 12 of them had
    # already settled into the read record (against COVERAGE 92, LEAN 33,
    # STRONG 25), inflating the denominator with non-plays.
    #
    # It also put us on BOTH sides of two games on 10-31. Mississippi
    # State @ Texas and Minnesota @ Indiana each published a fade on the
    # favourite -- the public was on the dog at 70/68 and 69/65
    # tickets/money, which is the validated side -- against a tier=PASS
    # read on the dog at conviction 40, on a sport where conviction is an
    # anti-signal (NCAAF top decile 36.67%, ROI -33.76%, n=30). Both
    # graded. One had to lose.
    #
    # REFUSING THE RECEIPT DOES NOT HIDE THE READ. GameDetailV2 renders a
    # read from the sport context's primary_play, not from public_receipts,
    # and GameDetailV2.tsx:1060 already draws a COVERAGE/PASS/SKIP label
    # dim and low-conviction. So the read stays on screen, exactly as
    # written, and simply stops being scored as a bet nobody was told to
    # make.
    #
    # Scoped to reads. A PASS should never reach a play surface at all, and
    # if one ever does, rule (1) and the tier gates upstream are the place
    # that gets argued about -- not here.
    if surface not in PLAY_SURFACES:
        tier = str(row.get('tier') or '').upper()
        if tier in ('PASS', 'SKIP'):
            return 'REFUSE', (f'tier={tier} is a declared no-play — it must '
                              f'not publish as a priced, graded receipt')

    # Nothing refused. Surface the remembered WARN, if any.
    if _warn:
        return 'WARN', _warn
    return None, None


#: Two surfaces quoting the same bet this far apart in IMPLIED
#: PROBABILITY are disagreeing, not reading two different books.
#: Calibrated on the October live rows -- see the note in the loop.
PRICE_GAP_PP = 1.0


def check_line_coherence(rows: list, surface: str,
                         quiet: bool = False) -> list:
    """Flag a receipt whose NUMBER disagrees with one already published.

    == 2026-10-30 . THE SAME SIDE WENT OUT AT TWO DIFFERENT NUMBERS =====
    Lakers @ Golden State shipped as Warriors +1.5 (-108) on the Fade at
    13:51 and Warriors +3.5 (-115) on the Sweat Card at 21:53. Orlando @
    Toronto shipped Magic +5.5 at -105 and again at -110. Nobody
    mis-derived it: every composer reads close_spread at its own run time
    and that column moves with the money.

    WARN, never REFUSE. Refusing the second receipt would leave a pick
    visible on a surface with NO receipt behind it, which is worse than a
    disagreeing one -- an unrecorded play cannot be graded at all
    (feedback_green_step_that_writes_nothing). The cure is composers
    calling public_receipt.frozen_quote before they display a number; this
    is how we SEE the ones that have not been wired yet.

    One batched read per capture, keyed on the game_ids in the payload.
    """
    import os

    import requests

    sb = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    if not sb or not key:
        return []
    hdr = {'apikey': key, 'Authorization': f'Bearer {key}'}
    LINE_MKT = {'rl', 'spread', 'puckline', 'pl', 'total'}
    # 'potd' is not a market, it is a SLOT. The same bet goes out as
    # market='potd' on the POTD surface and market='ml' on the card, so
    # comparing the raw key makes one play look like two bets and the
    # disagreement never surfaces. Marshall ML went out at -145 (potd) and
    # -142 (sweat_card) this morning for exactly that reason.
    MKT_ALIAS = {'potd': 'ml', 'moneyline': 'ml',
                 'spread': 'rl', 'puckline': 'rl', 'pl': 'rl'}
    PRICED_MKT = LINE_MKT | {'ml'}

    def _mkt(v):
        m = str(v or '').lower()
        return MKT_ALIAS.get(m, m)

    def _imp(o):
        # Implied win probability, so a gap means the same thing at every
        # price: ten cents at -110 is noise, ten cents at -400 is not.
        try:
            o = float(o)
        except (TypeError, ValueError):
            return None
        if o == 0:
            return None
        return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)

    want = [r for r in rows
            if _mkt(r.get('market')) in PRICED_MKT
            and r.get('game_id') is not None
            and (r.get('pick_line') is not None
                 or r.get('pick_odds') is not None)]
    if not want:
        return []
    gids = sorted({str(r['game_id']) for r in want})
    found = []
    try:
        enc = ','.join(f'"{g}"' for g in gids)
        r = requests.get(
            f'{sb}/rest/v1/public_receipts', headers=hdr, timeout=20,
            params={'game_id': f'in.({enc})',
                    'select': 'game_id,surface,market,pick_side,pick_line,'
                              'pick_odds,result,published_at',
                    'order': 'published_at.asc'})
        prior = r.json() if r.status_code == 200 else []
    except Exception:                                      # noqa: BLE001
        return []
    if not isinstance(prior, list):
        return []
    for row in want:
        gid = str(row['game_id'])
        mkt = _mkt(row.get('market'))
        side = str(row.get('pick_side') or '').upper()
        for p in prior:
            if str(p.get('game_id')) != gid:
                continue
            if _mkt(p.get('market')) != mkt:
                continue
            if str(p.get('result') or '').upper() in ('VOID', 'VOIDED'):
                continue
            if side and str(p.get('pick_side') or '').upper() != side:
                continue
            # LINE first: taking a different number is the worse error, and
            # it is the one that made this check exist. Compared only when
            # BOTH carry a line -- a missing line is its own defect, and
            # calling that a disagreement would bury the real ones.
            if (p.get('pick_line') is not None
                    and row.get('pick_line') is not None):
                try:
                    same_line = abs(float(p['pick_line'])
                                    - float(row['pick_line'])) < 0.01
                except (TypeError, ValueError):
                    same_line = False
                if not same_line:
                    msg = (f'{row.get("pick_label")!r} publishes '
                           f'{row["pick_line"]} but {p.get("surface")} '
                           f'already published {p["pick_line"]} on the same '
                           f'side of this game at '
                           f'{str(p.get("published_at"))[:19]} -- call '
                           f'public_receipt.frozen_quote() before displaying '
                           f'a number')
                    found.append((row, msg))
                    if not quiet:
                        print(f'  ! line incoherence [{surface}]: {msg}')
                    break
            # PRICE second. 24 of 60 multi-surface bets in October disagreed
            # on the line; 14 more agreed on the line and disagreed on the
            # PRICE, and a moneyline carries no line at all -- so a
            # line-only test cannot see either group. The threshold is
            # calibrated on those rows: ordinary book-to-book variation
            # (-110/-112, -142/-145) sits at 0.2-0.7pp of implied
            # probability, the genuine disagreements (-105/-110, -138/-148,
            # -200/-190) start at 1.15pp. 1.0pp separates them.
            a, b = _imp(p.get('pick_odds')), _imp(row.get('pick_odds'))
            if a is None or b is None:
                break
            gap = abs(a - b) * 100.0
            if gap < PRICE_GAP_PP:
                break
            msg = (f'{row.get("pick_label")!r} publishes '
                   f'{row.get("pick_odds")} but {p.get("surface")} already '
                   f'published {p.get("pick_odds")} on the same bet at '
                   f'{str(p.get("published_at"))[:19]} '
                   f'({gap:.2f}pp of implied probability apart) -- call '
                   f'public_receipt.frozen_quote() before displaying a price')
            found.append((row, msg))
            if not quiet:
                print(f'  ! price incoherence [{surface}]: {msg}')
            break
    return found

def screen(rows: list, surface: str, quiet: bool = False) -> tuple[list, list]:
    """Split rows into (keep, refused) and print every decision loudly."""
    keep, refused, warned = [], [], []
    for r in rows or []:
        sev, why = check_row(r, surface)
        if sev == 'REFUSE':
            refused.append((r, why))
        else:
            if sev == 'WARN':
                warned.append((r, why))
            keep.append(r)
    if refused and not quiet:
        print(f'  *** receipt_invariants ({surface}): REFUSED {len(refused)} '
              f'row(s) ***')
        for r, why in refused[:8]:
            print(f'      {r.get("sport")} {r.get("market")} '
                  f'{r.get("pick_label")!r}: {why}')
    if warned and not quiet:
        print(f'  ! receipt_invariants ({surface}): {len(warned)} warning(s)')
        for r, why in warned[:6]:
            print(f'      {r.get("sport")} {r.get("market")} '
                  f'{r.get("pick_label")!r}: {why}')
    return keep, [r for r, _w in refused]


if __name__ == '__main__':
    cases = [
        ({'sport': 'NBA', 'market': 'rl', 'pick_label': 'Houston Rockets -6.5',
          'pick_odds': None, 'pick_side': 'AWAY'}, 'sweat_card', 'REFUSE'),
        ({'sport': 'NBA', 'market': 'rl', 'pick_label': 'Houston Rockets -6.5',
          'pick_odds': -110, 'pick_side': 'AWAY'}, 'sweat_card', None),
        ({'sport': 'NBA', 'market': 'total', 'pick_label': 'Under 224.5',
          'pick_odds': -110}, 'fade', 'REFUSE'),
        ({'sport': 'NBA', 'market': 'total', 'pick_label': 'Under 224.5',
          'pick_odds': -110}, 'game_read', None),
        ({'sport': 'MULTI', 'market': 'parlay', 'pick_odds': 622,
          'pick_label': 'daily_degen (3 legs)',
          'audit': {'n_legs': 3, 'legs': [1, 2]}}, 'daily_degen', 'REFUSE'),
        ({'sport': 'NHL', 'market': 'ml', 'pick_label': 'Seattle Kraken ML',
          'pick_odds': -115}, 'game_read', 'WARN'),
        # a priceless COVERAGE row warns, it is not deleted
        ({'sport': 'NBA', 'market': 'rl', 'pick_label': 'Lakers ATS',
          'pick_odds': None, 'pick_side': 'HOME'}, 'game_read', 'WARN'),
        # R121/R122: the two 10-31 reads that sat opposite our own fade.
        ({'sport': 'NCAAF', 'market': 'rl', 'pick_label': 'Minnesota +20.5',
          'pick_odds': -108, 'pick_side': 'AWAY', 'tier': 'PASS'},
         'game_read', 'REFUSE'),
        ({'sport': 'NCAAF', 'market': 'rl',
          'pick_label': 'Mississippi State +20.5', 'pick_odds': -110,
          'pick_side': 'AWAY', 'tier': 'SKIP'}, 'game_read', 'REFUSE'),
        # the tiers that ARE an opinion still publish
        ({'sport': 'NCAAF', 'market': 'rl', 'pick_label': 'Minnesota +20.5',
          'pick_odds': -108, 'pick_side': 'AWAY', 'tier': 'COVERAGE'},
         'game_read', None),
        ({'sport': 'NCAAF', 'market': 'rl', 'pick_label': 'Minnesota +20.5',
          'pick_odds': -108, 'pick_side': 'AWAY', 'tier': 'LEAN'},
         'game_read', None),
        # and a PASS on a PLAY surface is not this rule's business
        ({'sport': 'NCAAF', 'market': 'rl', 'pick_label': 'Texas -20.5',
          'pick_odds': -110, 'pick_side': 'HOME', 'tier': 'PASS'},
         'fade', None),
    ]
    ok = True
    for row, surf, want in cases:
        got, why = check_row(row, surf)
        flag = 'ok ' if got == want else 'FAIL'
        if got != want:
            ok = False
        print(f'  {flag} [{surf}] {row.get("pick_label")!r} -> {got} '
              f'(wanted {want}){"  " + str(why) if why else ""}')

    # ── check_receipt_patch ───────────────────────────────────────────────
    # The case that matters is the one that cost 44 rows today: game_date
    # looks like an ordinary column and is not one.
    print('\ncheck_receipt_patch:')
    patch_cases = [
        # (body, existing, expect_blocked_keys)
        ({'game_date': '2026-11-02'}, None, ['game_date']),
        ({'result': 'WIN', 'graded_at': 'now'}, None, []),
        ({'audit': {'x': 1}}, {'audit': None}, []),
        ({'audit': {'x': 1}}, {'audit': {'already': True}}, ['audit']),
        ({'audit': {'x': 1}}, None, ['audit']),
        ({'pick_odds': -120, 'result': 'LOSS'}, None, ['pick_odds']),
        ({'game_date': '2026-11-02', 'audit': {}}, {'audit': {'a': 1}},
         ['audit', 'game_date']),
    ]
    for body, existing, want_keys in patch_cases:
        reasons = check_receipt_patch(body, existing)
        got_keys = sorted(r.split(':')[0] for r in reasons)
        good = got_keys == sorted(want_keys)
        if not good:
            ok = False
        print(f'  {"ok  " if good else "FAIL"} {sorted(body)} '
              f'existing={existing} -> blocked {got_keys} '
              f'(wanted {sorted(want_keys)})')

    print('\nself-test', 'PASSED' if ok else 'FAILED')
    raise SystemExit(0 if ok else 1)
