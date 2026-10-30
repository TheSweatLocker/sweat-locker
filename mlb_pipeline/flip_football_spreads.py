#!/usr/bin/env python3
"""Flip our own SHORT NCAAF spread pick at the SOURCE, before it is published.

══ WHY ══════════════════════════════════════════════════════════════════
Our short football spread pick is the worst thing we publish. Measured
2026-10-23 on the surfaces' own receipts:

    Sharp, football spreads |line| <= 7, AS PUBLISHED   4-18-1   18.2%  -65.3% ROI
    the identical 22 picks, FLIPPED                    18-4-1   81.8%  +56.2% ROI
    Jerry (game_read) NCAAF short, FLIPPED             35-18    66.0%  +26.1% ROI  n=53
      split-half: early 61.8% (n=34) · late 61.7% (n=47)  <- stable, not a streak
    Jerry NCAAF short, CI[52.6, 77.3]                  clears the 52.38% breakeven

Scope is deliberately narrow, and each exclusion is a measurement:

    NCAAF long (>7)  flipped 49.8% on n=227  -> a coin flip on most of the
                     volume. Excluded. Flipping it would dilute the signal.
    NFL short        flipped 53.6% on n=28, CI[35.8,70.5] -> spans breakeven
                     both ways. Excluded until it does not.
    totals / ML      not measured here at all. Untouched.

══ WHY AT THE SOURCE ════════════════════════════════════════════════════
Flipping inside the Sharp/Sweat Card composers instead would leave Jerry's
read saying "Texas -6.5" while the card said "Ole Miss +3.5" on the same
screen. Andy called that exact class of contradiction a smoking gun on
2026-10-23 (the Giants CLV card). Flipping primary_play once means the read,
the Sharp, the Sweat Card and the receipt all agree, and the grader records
the side we actually published -- feedback_grade_the_side_we_published.

══ THE TWO SAFETY GATES ═════════════════════════════════════════════════
1. A GAME THAT ALREADY HAS A RECEIPT IS NEVER TOUCHED.
   public_receipts.pick_line is immutable by DB trigger, by design, because
   it is what makes the record trustworthy. If a receipt already names the
   original side, flipping primary_play would show one number and grade
   another. So this script refuses those games outright and names them.
   On the 2026-10-24 slate that protected 4 of 11 (LSU, Wisconsin, Texas,
   Utah) -- and Texas's receipt already said -8.5 against a primary_play of
   -6.5, so that row was divergent before anything here ran.

2. A FLIP NEEDS A POSTED PRICE.
   The flipped side is priced from line_history, which stores one row per
   (book, side, line) with a real American price. 2+ books must post the
   exact flipped number or the pick is left alone. Quoting our own stale line
   on the other side is what project_jerry_published_fake_lines_1019 found on
   48% of game_read spreads, and publishing a number nobody can bet is the
   defect being fixed this same day.

POTD is excluded separately, in jerry_anchor_potd._load_primary_play_candidates,
on Andy's instruction that POTD is not to change. A flipped pick carries
`_flipped_from`, and that is what POTD skips on.

Usage:
    python flip_football_spreads.py                      # dry run
    python flip_football_spreads.py --apply
    python flip_football_spreads.py --date 2026-10-24 --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import requests

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        if '=' in _ln and not _ln.strip().startswith('#'):
            _k, _v = _ln.strip().split('=', 1)
            os.environ.setdefault(_k, _v)

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = dict(H, **{'Content-Type': 'application/json',
                'Prefer': 'return=representation'})

#: Books that must post a usable flipped number.
MIN_BOOKS = 2

# ══ 2026-10-24 · NFL ADDED, ALL BANDS. I REFUSED THIS AND I WAS WRONG ══
# ANDY, in week 7: "NFL enignw went like 2-9-1 last wekee ... you refuse to
# change anything but hcoos to go into this wekekend with no change and expect
# different results."
#
# He was right. I had been quoting "NFL short flipped is 53.6% on n=28" and
# treating NFL as unproven. That figure was the SHORT band only, on the
# narrower game_read population — the wrong sample to refuse on. Measured
# across every graded NFL spread receipt, deduped, all surfaces:
#
#   as published            22-46            32.4%
#   FLIPPED, all bands      46-22   67.6%  ROI +29.1%  n=68  CI[55.8, 77.6]
#     early half            21-8    72.4%              n=29  CI[54.3, 85.3]
#     late half             25-14   64.1%              n=39  CI[48.4, 77.3]
#     short <=7             24-15   61.5%              n=39
#     long  >7               9-5    64.3%              n=14
#     last two weeks        25-14   64.1%              n=39
#
# The interval clears the 52.38% breakeven, it holds in BOTH halves, and the
# two most recent weeks confirm it. Published NFL spreads went 4-9 (30.8%)
# last week and 9-15-2 (37.5%) the week before — not variance, a trend.
#
# BANDS ARE PER SPORT, because the sports disagree and each one gets its own
# measurement:
#   NCAAF  long (>7) flipped is 49.0% on n=253 — a coin flip on most of the
#          volume, so NCAAF stays short-only.
#   NFL    long flipped is 64.3% (n=14) and short 61.5% (n=39), with the
#          combined set at 67.6% on n=68. No band exclusion is justified.
#: sport -> max |line| to flip, or None for every band.
SPORTS = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context'}
# ══ 2026-10-24 · ALL BANDS, BOTH SPORTS. ANDY'S CALL, AND HE IS RIGHT ══
# Jerry's NCAAF went 0-5 yesterday. Three of the five were spreads and all
# three lost, so a flip would have gone 3-0 on the live slate.
#
# ANDY: "NCAAF 0-5 is crazy work and you sitll are heistant like obviosuly
# anything we do will be better that what is happening ... i want ot flip all
# those 20 plays today."
#
# My objection was that NCAAF long (>7) flipped measures 49.0% on n=253 — a
# coin flip. That is still true and it is stated once here rather than argued
# again: on the long band the flip is roughly NEUTRAL, not an improvement
# (ours 51.0%, flipped 49.0%). It is the short band that carries the edge,
# 66.0% against our 34%.
#
# Excluding long was defensible when the alternative was leaving good picks
# alone. It is not defensible as a reason to publish nothing while the surface
# goes 0-5, and the decision is his. All bands flip.
# ══ THE BAND IS MEASURED BY WALKING THE CUTOFF, NOT ASSERTED ══
# 2026-10-24. This was None/None ("flip every band") for one slate, on the
# reasoning that NCAAF 0-5 could not get worse. That is not a measurement, and
# the measurement turned out to disagree in BOTH directions.
#
# NCAAF flipped, graded receipts, cutoff walked rather than guessed:
#     |line| <=  6.5   69.4%  n= 62  CI[57.0, 79.4]  clears
#     |line| <=  7     71.0%  n= 69  CI[59.4, 80.4]  clears  <- peak
#     |line| <=  7.5   68.4%  n= 79  CI[57.5, 77.6]  clears
#     |line| <=  8.5   65.9%  n= 91  CI[55.7, 74.8]  clears
#     |line| <=  9.5   63.1%  n=103  CI[53.5, 71.8]  clears
#     |line| <= 10.5   62.0%  n=121  CI[53.1, 70.1]  clears  <- last one
# and pooled both sports the same shape holds (61.6% n=172 at 10.5).
#
# The edge DECAYS, it does not cliff. An earlier cut at 7 reported ">7 is
# 49.0% n=243" and that number was the trap this file's own history warns
# about: it averaged the real 8.5-10.5 edge together with noise at 13.5-20+,
# so it understated one half and overstated the other. Band every spread
# finding -- one number over two bands is a number about neither.
#
# Past 10.5 there is no measured edge in EITHER direction, which is a reason
# to publish nothing, not a reason to flip. A flipped pick carries a read
# saying "we are fading our own number", and we cannot support that sentence
# on a coin flip.
MAX_MAGNITUDE_BY_SPORT = {'NCAAF': 10.5, 'NFL': 10.5}

# ══ 2026-10-26 · B209 · NFL SPREADS ARE REFUSED ON CURATED SURFACES ══════
# Measured on every graded NFL spread receipt (n=85), priced from each
# receipt's own pick_odds rather than a flat -110:
#
#     sharp_card   5-13  27.8%  ROI -47.31%
#     sweat_card   5-13  27.8%  ROI -47.40%
#     game_read   21-28  42.9%  ROI -17.89%   <- different surface, untouched
#
# Breakeven is 52.38%. Both curated surfaces lose about half of stake, and
# the engine's own audit_note already says so: "this is a measured loser,
# not variance... the tier was never earned."
#
# Counterfactual of removing them:
#     Sweat Card  617-437 +92.56u  ->  612-424 +101.09u
#     The Sharp   395-300  +6.25u  ->  390-287  +14.77u
#
# The Sharp's whole lifetime profit is +6.25u and this one market costs it
# -8.51u, so the cut more than doubles the surface.
#
# NFL MONEYLINE is NOT banned (+10.7%) -- cap spreads, never ML
# (project_nfl_curated_spread_is_the_leak_1018).
#
# This lives here, next to the band, because both composers already import
# from this module: a second copy of the threshold is how
# generate_sweat_card.py:1042 ended up with a silently dead flip after a
# rename.
#
# REVERT: set this to set().
CURATED_SPREAD_BANNED_SPORTS = {'NFL'}
OPP = {'HOME': 'AWAY', 'AWAY': 'HOME'}


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)) \
        .date().isoformat()


def _get(path, params):
    r = requests.get(f'{SB}/rest/v1/{path}', params=params, headers=H,
                     timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ GET {path} HTTP {r.status_code}: {r.text[:200]}')
        return None
    return r.json()


def amer_prob(o):
    o = float(o)
    return (-o / (-o + 100.0)) if o < 0 else (100.0 / (o + 100.0))


def posted_quotes(sport: str, date: str) -> dict:
    """(game_id, side, line) -> (median posted price, n books).

    Keyed on line_history's own game_id, which for NCAAF is built from team
    names; the caller maps to it by team codes rather than assuming the two
    tables agree (project_ncaaf_five_team_name_mismatch_1021).
    """
    rows = []
    off = 0
    while off < 12000:
        b = _get('line_history',
                 {'select': 'game_id,side,line,price,book,captured_at',
                  'sport': f'eq.{sport}', 'market': 'eq.spread',
                  'commence_time': f'gte.{date}',
                  'order': 'captured_at.desc',
                  'limit': '1000', 'offset': str(off)})
        if not b:
            break
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    seen, pool, teams = set(), {}, {}
    for r in rows:
        gid = str(r.get('game_id') or '')
        ln, pr = f(r.get('line')), f(r.get('price'))
        if ln is None or pr is None:
            continue
        k = (gid, str(r.get('book')), str(r.get('side')).lower(), ln)
        if k in seen:           # ordered desc, first seen is newest
            continue
        seen.add(k)
        pool.setdefault((gid, str(r.get('side')).lower(), ln), []).append(pr)
        parts = gid.split('_')
        if len(parts) >= 3:
            teams[(parts[-2], parts[-1])] = gid
    out = {}
    for k, prices in pool.items():
        if len(prices) < MIN_BOOKS:
            continue
        ranked = sorted(prices, key=amer_prob)
        # a REAL quote from one book, never an average of two
        out[k] = (ranked[len(ranked) // 2], len(prices))
    return out, teams


#: How many points worse than the flip number we will still accept.
#: The thesis was measured at OUR number, so a board materially short of it is
#: a different bet; a board AHEAD of it is the same bet on better terms.
FLIP_LINE_TOLERANCE = 0.5


def best_flip_quote(quotes: dict, lh_gid, side: str, flip_line: float):
    """Best POSTED quote for the flipped side, or None.

    ══ 2026-10-23 · AN EXACT-LINE MATCH REJECTED A BETTER NUMBER ══
    The first version required the board to post our flipped number exactly.
    That threw away Georgia Tech -5.5 -> Boston College, where the flip number
    was +5.5 and the board posts +6.5 -- a full point MORE in our favour, on
    the same side, refused for not matching. Andy: "If we knwo thes epicks are
    btter i want to flip them to users, youre saying only flip one?" He was
    right; refusing a better price is indefensible.

    So: take the most favourable posted line for the side we want, and accept
    it when it is no more than FLIP_LINE_TOLERANCE worse than the number the
    thesis was measured at. Higher is always better for the holder, so the
    comparison is a plain signed one and needs no per-sport convention.

    Still refuses a board that has moved materially AGAINST the flip -- Texas
    -6.5 flips to Ole Miss +6.5 while the board posts only +3.5, three points
    short, which is a different bet from the one that was measured.
    """
    if not lh_gid:
        return None
    opts = [(ln, v) for (gid, sd, ln), v in quotes.items()
            if gid == lh_gid and sd == side.lower()]
    if not opts:
        return None
    ln, (price, books) = max(opts, key=lambda z: z[0])   # most points for us
    if ln < flip_line - FLIP_LINE_TOLERANCE:
        return None
    return ln, price, books


def receipted(sport: str, date: str) -> set:
    """game_ids whose spread side is already published. Never touched.

    == 2026-10-30 . A PARLAY LEG IS A PUBLISHED SIDE ==================
    This filtered the query on sport=eq.{sport}, and a parlay receipt
    carries sport='MULTI' while its LEGS carry their own sport. So a Daily
    Degen leg was invisible here -- which is what happened tonight: the
    Degen published Sacramento State +8.5 and Tulane -8.5 as NCAAF legs,
    this returned an empty set for NCAAF, and nothing stopped the Sharp
    publishing the other side of both. Same blind spot
    consistency_watchdog had to fix in its cross-surface check tonight.

    Legs carry their own game_id, so they match exactly rather than by
    name. The query no longer filters by sport at all: top-level rows are
    matched on the receipt's sport, legs on the LEG's sport.

    A VOIDed receipt does not count -- a withdrawn side is not published.
    """
    rows = _get('public_receipts',
                {'select': 'game_id,market,sport,result,audit',
                 'game_date': f'eq.{date}', 'limit': '1000'}) or []
    want = str(sport or '').upper()
    out = set()
    for r in rows:
        if str(r.get('result') or '').upper() in ('VOID', 'VOIDED'):
            continue
        audit = r.get('audit') if isinstance(r.get('audit'), dict) else {}
        legs = audit.get('legs')
        if isinstance(legs, list) and legs:
            for leg in legs:
                if not isinstance(leg, dict):
                    continue
                if str(leg.get('sport') or '').upper() != want:
                    continue
                if str(leg.get('type') or '').lower() not in ('rl', 'spread'):
                    continue
                if leg.get('game_id') is not None:
                    out.add(str(leg['game_id']))
            continue
        if str(r.get('sport') or '').upper() != want:
            continue
        if str(r.get('market') or '').lower() in ('rl', 'spread'):
            if r.get('game_id') is not None:
                out.add(str(r['game_id']))
    return out


def run(sport: str, date: str, apply: bool,
        over_receipts: bool = False) -> int:
    tbl = SPORTS[sport]
    games = _get(tbl, {'select': 'game_id,game_date,home_team,away_team,'
                                 'close_spread,primary_play,pick_locked_at',
                       'game_date': f'eq.{date}', 'limit': '300'}) or []
    quotes, teammap = posted_quotes(sport, date)
    has_receipt = receipted(sport, date)
    print(f'=== flip_football_spreads · {sport} · {date} · '
          f'{"APPLY" if apply else "DRY RUN"} ===')
    print(f'  games={len(games)}  posted-quote keys={len(quotes)}  '
          f'games already receipted={len(has_receipt)}')
    flipped = 0
    skip = {}

    def no(reason):
        skip[reason] = skip.get(reason, 0) + 1

    for g in games:
        pp = g.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except ValueError:
                pp = None
        if not isinstance(pp, dict):
            continue
        if str(pp.get('type') or '').lower() not in ('rl', 'spread'):
            no('not a spread pick')
            continue
        ours = f(pp.get('line'))
        side = str(pp.get('side') or '').upper()
        if ours is None or side not in OPP:
            no('no usable line/side')
            continue
        _maxm = MAX_MAGNITUDE_BY_SPORT.get(sport)
        if _maxm is not None and abs(ours) > _maxm:
            no(f'{sport} spread beyond the measured band (>{_maxm:g}) — '
               f'the flip clears breakeven to 10.5 (62.0% n=121) and past '
               f'that has no edge either way')
            continue
        # ══ FLIP A CALL, NEVER MANUFACTURE ONE ══
        # A PASS tier is the engine saying "no play" and COVERAGE is
        # information rather than a conviction call. Inverting those would not
        # be fading our own read, it would be inventing a bet where we told
        # the user there wasn't one -- a different and much worse thing than
        # the 66% measurement supports. On the 10-24 slate this held back
        # Arkansas State +2.5, Kansas +2.5 and Navy +2.5, all PASS.
        # ══ ANY PUBLISHED PICK IS FLIPPABLE ══
        # This used to refuse PASS and COVERAGE on the reasoning that
        # inverting a "no play" invents a bet. That reasoning was wrong: a
        # PASS pick IS published — it carries a receipt and renders on game
        # detail, exactly as the NYG +7 PASS pick did. Inverting it is
        # inverting something users already see, not conjuring one.
        # A pick with no side or no line is still refused above; that is the
        # real "nothing to flip" case.
        tier = str(pp.get('tier') or '').upper()
        gid = str(g['game_id'])
        # ══ A FLIPPED NO-PLAY MAKES THE READ CONTRADICT THE BADGE ══
        # The read generator branches on tier: COVERAGE/PASS/SKIP produce
        # "your prose must explain the PASS — do NOT argue for a side". Flip a
        # PASS and Jerry argues no-play underneath a badge showing a play --
        # the exact contradiction Andy flagged, only manufactured by us.
        # Promoting the tier to make the prose cooperate would invent a
        # conviction the engine never had. So a no-play stays a no-play, and
        # the comment above about PASS being "flippable" is wrong on this
        # point: it is publishable, but not flippable without the read.
        if tier in ('PASS', 'SKIP', 'COVERAGE', ''):
            no(f'tier={tier or "none"} is a no-play — flipping it would leave '
               f'the read arguing PASS under a flipped badge')
            continue
        # ══ THE FROZEN RECEIPT, AND WHICH WAY THE ERROR CUTS ══
        # public_receipts.pick_line is immutable by DB trigger, so a game that
        # already published a spread receipt keeps grading the ORIGINAL side
        # while the badge, the read and the Sharp all move to the flipped one.
        # That is a real divergence and it is why this is opt-in.
        #
        # It is accepted only because of the DIRECTION: the original side is
        # the one measured at 34.0% (0-5 on 10-23), so the record grades our
        # WORSE side while users see the better one. Our published record
        # understates us -- the same safe direction refresh_offmarket_lines.py
        # already takes. The reverse (grading the flipped side while showing
        # the original) would inflate the record and is never acceptable.
        #
        # The forward fix is ordering, not this flag: flip at source BEFORE
        # the receipt writer runs and nothing diverges. See B169.
        if gid in has_receipt and not over_receipts:
            no('RECEIPT ALREADY PUBLISHED — would show one number, grade '
               'another (--over-receipts accepts that, record-understating)')
            continue
        if pp.get('_flipped_from'):
            no('already flipped')
            continue
        # ══ A DELIBERATE UN-FLIP MUST NOT BE RE-FLIPPED ══
        # --unflip --beyond-band records _unflipped_from when we decide a
        # pick sits outside the measured band. This script runs unattended
        # from the Saturday cron with --apply, so without this guard it would
        # re-flip that pick the moment the slate is unlocked, undoing the
        # decision with no log anyone reads. Arizona -10 is the live example:
        # |line| 10.0 is INSIDE the 10.5 band, so the band check alone does
        # not save it -- it was un-flipped because the flipped number lands
        # at +11.5, outside the band.
        if pp.get('_unflipped_from'):
            no('deliberately un-flipped (outside the measured band) — '
               'not re-flipping')
            continue
        if g.get('pick_locked_at'):
            no('pick is LOCKED — run lock_football_slate.py --unlock first')
            continue
        opp = OPP[side]
        new_line = -ours
        team = str(g['home_team'] if opp == 'HOME' else g['away_team'])
        lh = teammap.get((str(g['away_team']), str(g['home_team'])))
        q = best_flip_quote(quotes, lh, opp, new_line)
        if not q:
            no(f'no {MIN_BOOKS}+ books post a line within '
               f'{FLIP_LINE_TOLERANCE:g} of the flipped number')
            continue
        # ══ BAND THE LINE WE LAND ON, NOT THE ONE WE STARTED FROM ══
        # best_flip_quote deliberately accepts a line BETTER than the flip
        # number (that is what recovered Boston College +6.5 against a +5.5
        # flip number), so the result can be carried past the band by a
        # favourable quote. Checking only `ours` above let Arizona -10 become
        # Iowa State +11.5 on the Sweat Card -- outside the band, and exactly
        # the pick that had just been removed as unsupported.
        if _maxm is not None and abs(q[0]) > _maxm:
            no(f'flipped line {q[0]:+g} lands outside the {_maxm:g} band '
               f'even though {ours:+g} was inside it')
            continue
        new_line, price, nbooks = q
        new = dict(pp)
        new['side'] = opp
        new['line'] = new_line
        new['label'] = f'{team} {new_line:+g}'
        new['_flipped_from'] = {
            'side': side, 'line': ours, 'label': pp.get('label'),
            # True means a published receipt is frozen on the ORIGINAL side,
            # so the record grades that side and understates this pick.
            'receipt_frozen_on_original': gid in has_receipt,
            'tier': pp.get('tier'), 'sub': pp.get('sub'),
            'at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'basis': ('NCAAF short spreads flipped 66.0% n=53 CI[52.6,77.3]; '
                      'NFL spreads flipped 67.6% n=68 CI[55.8,77.6], both '
                      'halves holding'),
        }
        new['_flip_price'] = price
        new['_flip_books'] = nbooks
        new['sub'] = (f'fades our own {pp.get("label")} read — our short '
                      f'NCAAF spread calls hit 34% straight and 66% inverted '
                      f'(n=53)')
        print(f'  {"FLIP " if apply else "[DRY]"} {g["away_team"]} @ '
              f'{g["home_team"]}: {pp.get("label")} -> {new["label"]} '
              f'@ {price:+g} ({nbooks}bk)')
        if apply:
            r = requests.patch(f'{SB}/rest/v1/{tbl}',
                               params={'game_id': f'eq.{gid}'},
                               headers=HW,
                               data=json.dumps({'primary_play': new}),
                               timeout=30)
            if r.status_code not in (200, 204):
                print(f'      ⚠ PATCH HTTP {r.status_code}: {r.text[:200]}')
                continue
            # A 200 is not a write. Read it back and confirm the label moved,
            # because enforce_pick_lock silently reverts a locked row.
            back = _get(tbl, {'select': 'primary_play',
                              'game_id': f'eq.{gid}', 'limit': '1'})
            got = (back or [{}])[0].get('primary_play') or {}
            if str(got.get('label')) != new['label']:
                print(f'      ⚠ REVERTED — label is still '
                      f'{got.get("label")!r}; the lock trigger refused it')
                no('refused by lock trigger')
                continue
        flipped += 1
    print(f'  flipped: {flipped}')
    if skip:
        for k, v in sorted(skip.items(), key=lambda z: -z[1]):
            print(f'    skipped · {k}: {v}')
    return flipped


def unflip(sport: str, date: str, apply: bool,
           beyond_band: bool = False) -> int:
    """Restore flipped picks from their own recorded _flipped_from.

    ══ WHY RESTORE RATHER THAN RE-DERIVE ══
    _flipped_from carries the exact side, line, label, tier and sub the engine
    produced. Re-deriving them from close_spread would mean re-establishing a
    per-sport orientation sign that has been found inverted between sports
    before, to recover something already written down. So this reads the
    record, never recomputes it.

    With --beyond-band only picks OUTSIDE the measured band are restored,
    which is the real use: the band moved from "everything" to 10.5 once the
    cutoff was walked, and the picks past it carry a read asserting "we are
    fading our own number" over a market where neither side has a measured
    edge. Undoing those removes a claim we cannot support; it is not a vote
    of confidence in the engine.
    """
    tbl = SPORTS[sport]
    rows = _get(tbl, {'select': 'game_id,game_date,home_team,away_team,'
                                'primary_play,pick_locked_at',
                      'game_date': f'eq.{date}', 'limit': '400'}) or []
    band = MAX_MAGNITUDE_BY_SPORT.get(sport)
    print(f'=== unflip · {sport} · {date} · '
          f'{"APPLY" if apply else "DRY RUN"}'
          + (f' · only |line| > {band:g}' if beyond_band and band else '')
          + ' ===')
    n = 0
    for g in rows:
        pp = g.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except ValueError:
                pp = None
        if not isinstance(pp, dict):
            continue
        fr = pp.get('_flipped_from')
        if not isinstance(fr, dict):
            continue
        cur = f(pp.get('line'))
        if beyond_band and band is not None and cur is not None                 and abs(cur) <= band:
            continue
        if g.get('pick_locked_at'):
            print(f'  SKIP (locked) {g["away_team"]} @ {g["home_team"]} — '
                  f'run lock_football_slate.py --unlock first')
            continue
        new = {k: v for k, v in pp.items() if k != '_flipped_from'}
        for k_new, k_old in (('side', 'side'), ('line', 'line'),
                             ('label', 'label'), ('tier', 'tier'),
                             ('sub', 'sub')):
            if fr.get(k_old) is not None:
                new[k_new] = fr[k_old]
        # Keep the audit trail: the flip HAPPENED and was published for a
        # while, so erasing every trace of it would make the read history
        # unexplainable.
        new['_unflipped_from'] = {
            'side': pp.get('side'), 'line': pp.get('line'),
            'label': pp.get('label'),
            'at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'why': (f'|line| outside the measured flip band '
                    f'(<= {band:g}); no edge in either direction, so the '
                    f'read must not claim a fade' if band else 'manual'),
        }
        print(f'  {"UNFLIP" if apply else "[DRY]  "} {g["away_team"]} @ '
              f'{g["home_team"]}: {pp.get("label")} -> {new.get("label")}')
        if not apply:
            n += 1
            continue
        up = requests.patch(f'{SB}/rest/v1/{tbl}', headers=HW, timeout=30,
                            params={'game_id': f'eq.{g["game_id"]}'},
                            data=json.dumps({'primary_play': new}))
        if up.status_code not in (200, 204):
            print(f'      PATCH HTTP {up.status_code}: {up.text[:160]}')
            continue
        # A 200 is not a write (feedback_204_is_not_a_write).
        back = _get(tbl, {'select': 'primary_play',
                          'game_id': f'eq.{g["game_id"]}', 'limit': '1'}) or []
        got = (back[0].get('primary_play') or {}) if back else {}
        if isinstance(got, dict) and got.get('label') == new.get('label')                 and not got.get('_flipped_from'):
            n += 1
        else:
            print(f'      REVERTED by the pick lock — still '
                  f'{got.get("label")!r}')
    print(f'  {"unflipped" if apply else "would unflip"}: {n}')
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(SPORTS), action='append')
    ap.add_argument('--date', default=None)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--unflip', action='store_true',
                    help='restore flipped picks from _flipped_from')
    ap.add_argument('--beyond-band', action='store_true',
                    help='with --unflip, only picks outside the measured '
                         'band for their sport')
    ap.add_argument('--over-receipts', action='store_true',
                    help='flip even where a spread receipt is '
                         'frozen on the original side; the '
                         'record then understates the pick')
    a = ap.parse_args()
    date = a.date or et_today()
    total = 0
    for sport in (a.sport or sorted(SPORTS)):
        if a.unflip:
            total += unflip(sport, date, a.apply, a.beyond_band)
        else:
            total += run(sport, date, a.apply, a.over_receipts)
    return 0


if __name__ == '__main__':
    sys.exit(main())
