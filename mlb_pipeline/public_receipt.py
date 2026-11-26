"""Live receipt capture — write the evidence AT publish time.

Andy 2026-09-20: "someone could call me out on record we post and we
would have nothing to show."

Every receipt in public_receipts today (12,774 of them) is
`capture_mode='reconstructed'` — rebuilt after the fact by
backfill_public_receipts.py from mutable source tables. That was the
right emergency move and it is NOT the finished state:

  * reconstructed = an inference from rows that survived. If a source
    row was later mutated, deleted, or filtered out of a view, the
    receipt inherits that damage silently.
  * live = the pick as it went in front of a user, written in the same
    breath as the publish.

They look identical in a table and are worth very different amounts the
moment anyone disputes a record. Only a publisher is entitled to claim
'live', and it has to be an affirmative claim — `capture_mode` is
NEVER defaulted to 'live' anywhere.

FIRST-WRITE-WINS
Writes use `resolution=ignore-duplicates` on
(sport, surface, game_date, source_id), matching prop_publish_lock. The
live write happens at publish, before any backfill runs, so live always
wins the race and a later reconstruction can never overwrite it.

FAIL-SOFT BUT LOUD
A receipt failure must never block a publish — the user-facing card
matters more than the audit row. But it must never be silent either:
that is the exact `|| echo` failure class we spent 2026-09-20 removing.
Every failure prints and is counted in the returned tally.

USAGE
    from public_receipt import capture, sharp_card_rows
    capture(sharp_card_rows(items, today), surface='sharp_card')
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import namedtuple
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY'))
H_WRITE = {
    'apikey': KEY,
    'Authorization': f'Bearer {KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=ignore-duplicates,return=minimal',
}

UNIQUE_KEY = 'sport,surface,game_date,source_id'

# == 2026-10-28 . A SPREAD PRICE THAT NO BOOK POSTS MUST NOT FREEZE ==========
# pick_odds allows NULL -> value and then FREEZES (migration 20260918b), so a
# wrong price written once is wrong forever. Measured tonight: 8 rl/spread
# receipts carry a price impossible for that market, 7 already graded --
#
#     2026-10-28 NBA   Cleveland Cavaliers -9.5   -400   (ungraded)
#     2026-09-27 NFL   DET -9.5                   -300
#     2026-09-26 NCAAF Mississippi State -1.5     -230   WIN
#
# -- every one a moneyline captured against a spread pick after the pick
# moved. A WIN at -230 pays 0.43u where the real -110 pays 0.91u, so the
# published record understates its own returns, and none of it can be
# repaired.
#
# Bands are the SAME measured p1..p99 spread quotes repair_stale_read_prices
# uses, and they are per sport on purpose: MLB's run line and NHL's puckline
# are fixed at +-1.5 and legitimately price far wider than a football or
# basketball spread, where the line moves and the price stays near -110. A
# flat band would have destroyed 32 correct prices there.
#
# NULL, never a guessed -110: backfill_receipt_prices can still fill a NULL
# from line_history at the pick's own line, and a NULL is visibly absent
# where a stale number reads as real (feedback_gate_the_stored_row).
_SPREAD_BANDS = {
    'NFL': (-150, 130), 'NCAAF': (-150, 130), 'NBA': (-150, 130),
    'NCAAB': (-150, 130), 'MLB': (-400, 350), 'NHL': (-400, 350),
}
_SPREAD_MARKETS = ('rl', 'spread')
_INT_FIELDS = ('conviction', 'pick_odds')
_MARKET_MAX = 24

# ══ 2026-10-23 · B142 · A PICK ON THESE MARKETS IS NOTHING WITHOUT A NUMBER ══
# Measured since 09-01: 126 receipts carried one of these markets with
# pick_line NULL, and 114 of them were GRADED into the published record
# (60 WIN / 51 LOSS / 3 PUSH). "Over" with no number cannot be graded against
# anything we showed, so those outcomes were scored against a line the user
# never saw. Both write paths did it — 68 reconstructed, 58 live.
#
# Same family as feedback_consensus_line_must_be_posted: a missing line makes
# a fake price, and here it also made a fake result.
#
# ml is deliberately absent: a moneyline has no line and a NULL is correct.
_LINE_REQUIRED_MARKETS = ('total', 'rl', 'spread', 'puckline')

# Anchored at the END of the label on purpose. A loose \d+ search picks the
# 76 out of "Philadelphia 76ers -5.5" and stores a 76-point spread; the
# trailing token is the line in every label shape we emit ("Under 6.5",
# "Baylor -6", "Over 230.5"). A label with no trailing number ("Ottawa
# Senators RL", "Over") correctly yields nothing and the row is refused.
_TRAILING_LINE_RE = re.compile(r'(?:^|\s)([+-]?\d+(?:\.\d+)?)\s*$')
_LINE_SANITY_MAX = 400.0

# The number that FOLLOWS a market word ("ATS -3.5", "Under 148.5",
# "RL +1.5"). Anchored on the market word for the same reason
# _TRAILING_LINE_RE is anchored at the end: an unanchored \d+ finds the 76
# in "Philadelphia 76ers".
#
# `\s*` and NOT `[^0-9+-]*`: the permissive version skipped across " (Jerry "
# and pulled **55** out of "(Jerry 55/100)", so a bare
# "Golden State Warriors ATS (Jerry 55/100)" recovered a FIFTY-FIVE POINT
# SPREAD -- under _LINE_SANITY_MAX, so nothing downstream would have caught
# it. test_receipt_line_guard.py is what found it; the case is pinned there.
# The number must follow the market word with only whitespace between, and
# `(?![\d/])` refuses a fraction like 55/100 outright.
_AFTER_MARKET_LINE_RE = re.compile(
    r'\b(?:ats|spread|rl|pl|puckline|over|under|o/u)\b\s*'
    r'([-+]?\d+(?:\.\d+)?)(?![\d/])', re.I)


def recover_line(row: dict):
    """Fill pick_line from pick_label where the label carries the number.

    Returns the row. Some live sweat_card rows store "Under 6.5" in
    pick_label with pick_line NULL — the number was never missing, just
    never parsed into the column the grader and the UI both read.
    """
    if row.get('pick_line') is not None:
        return row
    if _effective_market(row) not in _LINE_REQUIRED_MARKETS:
        return row
    _lbl = str(row.get('pick_label') or '')
    m = _TRAILING_LINE_RE.search(_lbl)
    if not m:
        # 2026-11-16: the trailing anchor cannot see a number that is not
        # last, and a POTD label never ends in one -- "Golden State
        # Warriors ATS -3.5 (Jerry 55/100)" ends in ')'. So try the number
        # that FOLLOWS a market word. Still anchored on a market word, so
        # it can no more pick the 76 out of "76ers" than the trailing
        # anchor could.
        m = _AFTER_MARKET_LINE_RE.search(_lbl)
    if not m:
        return row
    try:
        v = float(m.group(1))
    except (TypeError, ValueError):
        return row
    if abs(v) <= _LINE_SANITY_MAX:
        row['pick_line'] = v
    return row


def unactionable(row: dict) -> bool:
    """True when this row claims a line-bearing market but carries no line.

    Reads the EFFECTIVE market, so a POTD or DotD spread is guarded like
    any other spread -- see `_effective_market`. A parlay or a fight is
    not, because neither has a single line to carry.
    """
    return (_effective_market(row) in _LINE_REQUIRED_MARKETS
            and row.get('pick_line') is None)


def sanitize(row: dict) -> dict:
    """Normalise one receipt row in place and return it.

    Shared by live capture AND backfill_public_receipts so the two can
    never drift. Both defects below were found in live data on
    2026-09-20:

      * INTEGER COERCION. conviction and pick_odds are integer columns
        but sources hand over floats (daily_degen passes avg_conviction
        as 79.0). PostgREST rejects with 22P02 and the batch is
        all-or-nothing, so one float killed all 148 rows.
      * MARKET CLAMP. Two rows carried an entire markdown write-up in
        `market` because a source had prose where a market code belonged.
        A field holding the wrong KIND of value quietly poisons every
        GROUP BY built on it. Clamp, and park the original in audit
        rather than dropping it.
    """
    for f in _INT_FIELDS:
        v = row.get(f)
        if v is None or isinstance(v, int):
            continue
        try:
            row[f] = int(round(float(v)))
        except (TypeError, ValueError):
            row[f] = None
    # refuse a spread price outside what that sport's spreads actually trade
    # at -- it is a moneyline that followed the pick, and pick_odds freezes
    _mk = str(row.get('market') or '').strip().lower()
    _od = row.get('pick_odds')
    if _mk in _SPREAD_MARKETS and isinstance(_od, int):
        _lo, _hi = _SPREAD_BANDS.get(
            str(row.get('sport') or '').strip().upper(), (-150, 130))
        if not (_lo <= _od <= _hi):
            row['pick_odds'] = None
            _aud = row.get('audit')
            if not isinstance(_aud, dict):
                _aud = {} if _aud is None else {'prior_audit': _aud}
            _aud['price_refused'] = _od
            _aud['price_refused_why'] = (
                f'{_od} is outside the measured {_mk} band [{_lo}, {_hi}] '
                f'for this sport — a moneyline on a spread pick')
            row['audit'] = _aud
    m = row.get('market')
    if isinstance(m, str) and (len(m) > _MARKET_MAX or '\n' in m):
        aud = row.get('audit')
        if isinstance(aud, dict):
            aud['market_raw'] = m[:400]
        else:
            row['audit'] = {'market_raw': m[:400]}
        # Take the first token that still has content after stripping
        # markdown. The earlier version took split()[0] unconditionally,
        # so "** ml  \n**side:** home" stripped to '' and fell through to
        # 'unknown' — discarding a perfectly recoverable 'ml'. Walk the
        # tokens instead; only give up if none survives.
        code = ''
        for tok in m.split():
            cleaned = tok.strip('*:|-# \t\n')
            if cleaned:
                code = cleaned[:_MARKET_MAX]
                break
        row['market'] = code.lower() or 'unknown'
    return row


def _sid(*parts: Any) -> str:
    """Stable source_id from its parts.

    Must be unique within (sport, surface, game_date): one game can carry
    both an ml and a total pick on the same card, so the market has to be
    part of the key or the second pick silently loses the conflict and is
    never recorded.
    """
    raw = ':'.join('' if p is None else str(p) for p in parts)
    if len(raw) <= 120:
        return raw
    return raw[:80] + ':' + hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]


H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


#: What _live_keys hands back. NAMED on purpose: this helper already
#: changed shape once and `backfill_public_receipts` kept the two-value
#: unpack, so its dedupe was dead for six days
#: ([[feedback_fix_every_writer_to_the_table]]). A short tuple raises
#: ValueError loudly; attribute access cannot be read positionally at all.
LiveKeys = namedtuple('LiveKeys', 'seen bets void_bets '
                                  'matchup_maps games_of_tuple '
                                  'void_tuples')

#: `market` values that name a CARD SLOT rather than a market.
_SLOT_MARKETS = frozenset({'potd', 'dotd', 'lock', 'dawg',
                           'bestbet', 'best_bet'})
#: spellings of one market, so two writers cannot disagree into two keys
_MARKET_ALIASES = {'spread': 'rl', 'puckline': 'rl', 'pl': 'rl',
                   'runline': 'rl', 'over/under': 'total'}


def canon_market(row) -> str:
    """The MARKET of a receipt, never a slot name.

    ══ 2026-11-25 · A SLOT NAME IS NOT A MARKET ════════════════════════
    `market` holds 'potd'/'dotd' for the card's anchor slots, so ONE
    wager was recorded twice under two markets and no key could see it:
    2026-11-24 `Under 213.5` as market='total' (id 5525534) and
    market='potd' (id 5525839), both WIN, so the card's only winner
    counted twice. Same shape on 2026-11-10, 'ml' beside 'dotd'.

    Read the market off the LABEL SHAPE when the stored value is a slot,
    which is the same fix `generate_sweat_card._card_market_of` applies
    to the card itself ([[feedback_parse_a_pick_by_shape_not_vocabulary]]).
    """
    m = str(row.get('market') or '').strip().lower()
    if m not in _SLOT_MARKETS:
        return _MARKET_ALIASES.get(m, m)
    lbl = ' '.join(str(row.get('pick_label') or '').split())
    lbl = re.sub(r'\s*\([^)]*\)\s*$', '', lbl).strip().lower()
    if re.match(r'^(over|under)\b', lbl):
        return 'total'
    if re.search(r'\bml$', lbl):
        return 'ml'
    if re.search(r'[+-]\d+(\.\d+)?$', lbl):
        return 'rl'
    return m


def norm_bet_label(row) -> str:
    """A receipt label stripped of the decoration writers disagree about."""
    s = ' '.join(str(row.get('pick_label') or '').split())
    # "(Jerry 56/100)", "(NHL LEAN 56/100)", "(resolver STRONG)"
    s = re.sub(r'\s*\((?:jerry|sweat|resolver|ncaab|ncaaf|nfl|nba|nhl|mlb)'
               r'[^)]*\)\s*$', '', s, flags=re.I)
    s = re.sub(r'\s*·.*$', '', s)          # "  ·  proj 8.8"
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _line_token(row) -> str:
    try:
        return f'{float(row.get("pick_line")):g}'
    except (TypeError, ValueError):
        return ''


def _norm_matchup(row) -> str:
    return ' '.join(str(row.get('matchup') or '').split()).lower()


def bet_tuple_no_game(row, sport=None, surf=None, gd=None):
    """A wager's identity WITHOUT its game: the key for a row that has no
    game_id and no matchup, which the backfill writes
    ('ml:|San Diego Padres ML', matchup=None, game_id=None). On its own it
    cannot distinguish one bet written twice from two games at the same
    label and line, so screen_for_write only trusts it when the live side
    holds exactly ONE game for the tuple -- see the use there.
    """
    surf = surf if surf is not None else row.get('surface')
    if str(surf or '') == 'ledger':
        return None
    lbl = norm_bet_label(row)
    if not lbl:
        return None
    return (str(sport if sport is not None else row.get('sport')),
            str(surf), str(gd if gd is not None else row.get('game_date')),
            canon_market(row), lbl, _line_token(row))


def bet_identity(row, sport=None, surf=None, gd=None, gid_of_matchup=None):
    """One wager's identity, independent of whatever source_id names it.

    ══ 2026-11-25 · A NULL game_id MADE THIS GUARD DEAD ON THE CARD ════
    This used to return None whenever `game_id` was NULL, on the stated
    grounds that such a row is a parlay slip. That is true of `ledger`
    and false of the Sweat Card, where the potd, dotd, prop and
    MLB-era total rows ALL carry NULL game_id -- 284 of the 400 newest
    `sweat_card` receipts. So the bet-level guard was switched off for
    exactly the rows that duplicate, and 46 wagers are in the table
    twice ([[feedback_a_null_never_reaches_a_gate]]).

    The game is now resolved in three steps, and the ORDER matters in
    both directions:
      1. `game_id` when the row has one.
      2. else the matchup, resolved through `gid_of_matchup` so a copy
         carrying only the name lands in the SAME key space as a copy
         carrying the id -- "game_id when present, else the name" is
         itself two key spaces, which is the mistake that cost a pair on
         the card's first fix ([[feedback_dedupe_key_shape_must_match]]).
      3. else None, and the caller must not judge the row. 2026-10-03
         published TWO `Under 7.0` bets with NULL game_id on different
         games; with no discriminator at all they are indistinguishable
         from one bet written twice, and merging them would delete a
         real wager. A guard that cannot tell says so.

    `ledger` always returns None: a slip's identity is its own source_id
    and two slips on one night legitimately share a label.
    """
    surf = surf if surf is not None else row.get('surface')
    if str(surf or '') == 'ledger':
        return None
    lbl = norm_bet_label(row)
    if not lbl:
        return None
    game = str(row['game_id']) if row.get('game_id') else None
    if game is None:
        mu = _norm_matchup(row)
        if mu:
            game = (gid_of_matchup or {}).get(mu) or mu
    if not game:
        return None
    sport = sport if sport is not None else row.get('sport')
    gd = gd if gd is not None else row.get('game_date')
    return (str(sport), str(surf), str(gd), canon_market(row),
            lbl, _line_token(row), game)


def _bet_key(sport, surf, gd, row, gid_of_matchup=None):
    """Back-compatible shim over bet_identity (one identity, one place)."""
    return bet_identity(row, sport=sport, surf=surf, gd=gd,
                        gid_of_matchup=gid_of_matchup)


def _live_keys(payload: list):
    """Keys already holding a NON-VOID receipt, or None if unknowable.

    ══ 2026-10-27 · WHY THIS EXISTS INSTEAD OF ?on_conflict= ══
    20261025a replaced the full UNIQUE CONSTRAINT on
    (sport, surface, game_date, source_id) with a PARTIAL unique index
    (WHERE result IS NULL OR lower(result) <> 'void') so a voided receipt
    could be replaced by the correct side. That is the right data rule and
    the 19 void+replacement pairs in the table depend on it.

    But Postgres can only infer a PARTIAL index as an ON CONFLICT arbiter
    when the statement also supplies the index predicate, and PostgREST's
    `on_conflict=` parameter carries column names only. So every live write
    returned 42P10 "no unique or exclusion constraint matching the ON
    CONFLICT specification" from 10-25 onward: public_receipts took no write
    for two days on EVERY surface while the cards published normally,
    because capture() is fail-soft and nobody reads the step log.

    Dropping the parameter clears 42P10, but a plain INSERT then raises
    23505 on the second publish of the day and the batch is all-or-nothing.
    So first-write-wins is enforced here: read the keys that are already
    live, skip them, insert the rest.

    Returns None when the read fails. The caller must then NOT pre-filter,
    because an empty set would read as "nothing exists yet" and would
    duplicate the entire card.

    == 2026-10-31 - A source_id IS NOT THE IDENTITY OF A BET ==
    First-write-wins on source_id alone let ONE wager be recorded twice:
    Orlando Magic +5.5 held 'rl:401909281:rl' from the live card and
    'rl:401909281' from the reconciler, both live, both WIN, so 2026-10-30
    read 7-0 in the ledger while the card blob said 5-0. _card_anchor now
    makes both writers agree, but source_id is frozen by 20260918b, so the
    five rows written under the old shape keep it -- and the next capture of
    one of those games would mint the canonical id beside the legacy one and
    duplicate it again.

    So return a SECOND set keyed on the bet itself:
    (sport, surface, date, game_id, market, normalised label, line). A bet
    already live under any source_id is already recorded. Only rows carrying
    a game_id take part -- a parlay's identity is its slip, not a game, and
    two slips on one night legitimately share a label.
    """
    if not payload:
        return LiveKeys(set(), set(), set(), {}, {}, set())
    seen = set()
    bets = set()
    void_bets = set()
    matchup_maps = {}
    games_of_tuple = {}
    void_tuples = set()
    groups = {(str(r.get('sport')), str(r.get('surface')),
               str(r.get('game_date'))) for r in payload}
    for sport, surf, gd in groups:
        off = 0
        live = []
        while True:
            try:
                resp = requests.get(
                    f'{SB}/rest/v1/public_receipts', headers=H_READ,
                    params={'select': 'source_id,result,game_id,market,'
                                      'pick_label,pick_line',
                            'sport': f'eq.{sport}', 'surface': f'eq.{surf}',
                            'game_date': f'eq.{gd}',
                            'limit': '1000', 'offset': str(off)},
                    timeout=45)
            except Exception as e:                          # noqa: BLE001
                print(f'  ⚠ receipts ({surf}): dedupe read raised: {e}')
                return None
            if resp.status_code != 200:
                print(f'  ⚠ receipts ({surf}): dedupe read HTTP '
                      f'{resp.status_code}: {resp.text[:160]}')
                return None
            try:
                batch = resp.json()
            except ValueError:
                return None
            if not isinstance(batch, list):
                return None
            live += batch
            if len(batch) < 1000:
                break
            off += 1000
        # Resolve matchup -> game_id across EVERY row for this
        # (sport, surface, date) -- the live rows and the payload both --
        # before keying anything. A copy carrying only the matchup and a
        # copy carrying the id must land in ONE key space, or the guard
        # compares them and finds nothing
        # ([[feedback_dedupe_key_shape_must_match]]).
        gid_of_matchup = {}
        for row in live + [r for r in payload
                           if (str(r.get('sport')), str(r.get('surface')),
                               str(r.get('game_date'))) == (sport, surf, gd)]:
            mu = _norm_matchup(row)
            if mu and row.get('game_id'):
                gid_of_matchup.setdefault(mu, str(row['game_id']))
        matchup_maps[(sport, surf, gd)] = gid_of_matchup
        for row in live:
            voided = str(row.get('result') or '').strip().lower() == 'void'
            if not voided:
                seen.add((sport, surf, gd, str(row.get('source_id'))))
            # ══ 2026-11-25 · A VOID ROW LICENSED AN ENDLESS RE-MINT ════
            # Voided rows used to be skipped here entirely, so the bet was
            # invisible to the guard and the next capture wrote it again.
            # Measured today: 28,950 duplicate rows, one wager
            # ('South Florida @ Saint Joseph's Under 155.5', source_id
            # 18881) present EIGHTY-EIGHT times, re-minted on every run
            # for a fortnight.
            #
            # Worse, it makes a dedupe impossible to land: voiding a
            # duplicate is exactly how dedupe_card_same_bet_twice.py and
            # the receipt cleanup correct the record, and the very next
            # capture would mint it straight back.
            #
            # The exemption existed so a voided receipt could be replaced
            # by the CORRECT SIDE, and that still works: a replacement
            # carries a different label or line, so a different bet key.
            # 297 of the table's void+live groups differ in label (real
            # replacements, unaffected); 106 are identical (the re-mints
            # this closes). Only an IDENTICAL wager is refused, and the
            # skip is logged rather than silent so a case we have not
            # thought of is visible instead of mysterious.
            bk = _bet_key(sport, surf, gd, row, gid_of_matchup)
            if bk:
                bets.add(bk)
                if voided:
                    void_bets.add(bk)
                bt = bet_tuple_no_game(row, sport, surf, gd)
                if bt:
                    games_of_tuple.setdefault(bt, set()).add(bk[-1])
                    if voided:
                        void_tuples.add(bt)
    return LiveKeys(seen, bets, void_bets, matchup_maps,
                    games_of_tuple, void_tuples)


#: markets whose published NUMBER has to be identical across surfaces
_LINE_MARKETS = {'rl', 'spread', 'puckline', 'pl', 'total'}


def frozen_quote(sport: str, game_id, market: str, side: str | None = None):
    """The line and price ALREADY published for this game+market+side.

    == 2026-10-30 . THREE SURFACES, THREE DIFFERENT NUMBERS ============
    ANDY, on tonight's dog spreads. Lakers @ Golden State went out as:

        10-29 22:41  game_read   Los Angeles Lakers ATS   line=None
        10-30 13:51  fade        Golden State Warriors +1.5  -108
        10-30 21:53  sweat_card  Golden State Warriors +3.5  -115

    Two surfaces on the same side at numbers two points apart, and a third
    on the OTHER side with no number at all. Orlando @ Toronto has the
    milder form: both surfaces said Magic +5.5 but priced it -105 and -110.

    Nobody mis-derived anything. Every composer reads close_spread at its
    own run time, and close_spread is a LIVE odds column that moves --
    89% of money came in on the Lakers and the spread walked 1.5 -> 3.5
    across the eight hours between the two publishes
    (project_nfl_stale_line_and_inverted_weights_1030 documents the same
    column freezing stale once a game leaves the feed).

    So a surface cannot publish a number by looking at the board; it has to
    publish the number THIS SLATE already committed to. This returns that
    commitment: the EARLIEST live receipt for the key, which is what the
    first user to look actually saw. Same first-publisher-wins rule
    publish_lock already uses for tier.

    VOIDed receipts are skipped -- a withdrawn number is not a commitment.
    Returns None when nothing is published yet, which means the caller is
    free to set the number and becomes the commitment for everyone after.
    """
    mkt = str(market or '').lower()
    if mkt not in _LINE_MARKETS or game_id is None:
        return None
    try:
        r = requests.get(
            f'{SB}/rest/v1/public_receipts', headers=H_READ, timeout=20,
            params={'game_id': f'eq.{game_id}',
                    'sport': f'eq.{str(sport or "").upper()}',
                    'select': 'surface,market,pick_side,pick_line,pick_odds,'
                              'result,published_at',
                    'order': 'published_at.asc'})
        rows = r.json() if r.status_code == 200 else []
    except Exception:                                      # noqa: BLE001
        return None
    if not isinstance(rows, list):
        return None
    want = str(side or '').upper() or None
    for x in rows:
        if str(x.get('market') or '').lower() != mkt:
            continue
        if str(x.get('result') or '').upper() in ('VOID', 'VOIDED'):
            continue
        if x.get('pick_line') is None:
            continue
        if want and str(x.get('pick_side') or '').upper() != want:
            continue
        return {
            'line': x.get('pick_line'), 'odds': x.get('pick_odds'),
            'surface': x.get('surface'), 'side': x.get('pick_side'),
            'published_at': x.get('published_at'),
        }
    return None

def screen_for_write(rows: list, label: str = '', quiet: bool = False):
    """The rows of `rows` that are not already recorded. (fresh, stats).

    ══ 2026-11-25 · THE GUARD HAS TO LIVE WHERE EVERY WRITER PASSES ════
    `public_receipts` has four inserters. Two run on a schedule --
    capture() for all eight surfaces and backfill_public_receipts.py
    nightly over a FOURTEEN DAY window -- and they screened their rows
    with two copies of the same logic, which is how one of them kept a
    two-value unpack of a helper that returns four and silently stopped
    deduping for six days. The other two (receipt_divergence,
    replace_bare_spread_receipts) are manual repair tools that void a row
    and replace it with a DIFFERENT side, which this screen permits.

    Returns None when the live read fails; the caller must then refuse to
    write, because an empty "already recorded" set reads as "nothing
    exists yet" and duplicates the whole card.
    """
    stats = {'already': 0, 'dup_bet': 0, 'void_blocked': 0, 'in_batch': 0}
    keys = _live_keys(rows)
    if keys is None:
        return None, stats
    fresh, batch_bets, batch_tuples = [], set(), set()
    for r in rows:
        grp = (str(r.get('sport')), str(r.get('surface')),
               str(r.get('game_date')))
        if grp + (str(r.get('source_id')),) in keys.seen:
            stats['already'] += 1
            continue
        bk = _bet_key(grp[0], grp[1], grp[2], r, keys.matchup_maps.get(grp))
        if bk and bk in keys.bets:
            # the same wager under another source_id — see _live_keys
            stats['dup_bet'] += 1
            if bk in keys.void_bets:
                stats['void_blocked'] += 1
            continue
        if bk is None:
            # ══ NO game_id AND NO matchup: the backfill's own rows ══════
            # Judge it on the game-less tuple, but ONLY when the live side
            # holds exactly one game for that tuple. With two it is
            # genuinely ambiguous -- 2026-10-03 published two `Under 7.0`
            # bets on different games -- and refusing then would delete a
            # real wager, which is worse than recording one twice. A guard
            # that cannot tell says so rather than guessing.
            bt = bet_tuple_no_game(r, grp[0], grp[1], grp[2])
            known = keys.games_of_tuple.get(bt) if bt else None
            if known and len(known) == 1:
                stats['dup_bet'] += 1
                if bt in keys.void_tuples:
                    stats['void_blocked'] += 1
                continue
            if known and len(known) > 1:
                stats['ambiguous'] = stats.get('ambiguous', 0) + 1
            if bt:
                if bt in batch_tuples:
                    stats['in_batch'] += 1
                    continue
                batch_tuples.add(bt)
            fresh.append(r)
            continue
        # ══ THE BATCH ITSELF COULD CARRY THE BET TWICE ══════════════════
        # Every check above compares against rows ALREADY in the table, so
        # one payload holding the same wager twice inserted it twice --
        # which is exactly what a card with one bet in two slots hands
        # this function. 2026-10-03 shows the signature: ids 810367 and
        # 810368, written in one batch at the same published_at. A guard
        # that only looks at the database cannot catch its own caller
        # ([[feedback_guard_belongs_at_the_chokepoint]]).
        if bk and bk in batch_bets:
            stats['in_batch'] += 1
            continue
        if bk:
            batch_bets.add(bk)
        fresh.append(r)
    if not quiet:
        tag = f' ({label})' if label else ''
        if stats['already']:
            print(f'  receipts{tag}: {stats["already"]} already recorded, '
                  f'left untouched')
        if stats['dup_bet']:
            print(f'  receipts{tag}: {stats["dup_bet"]} row(s) already live '
                  f'under a different source_id, not duplicated')
        if stats['void_blocked']:
            # Stated, never silent: the one case where refusing could be
            # wrong is a bet we voided and genuinely meant to republish.
            print(f'  receipts{tag}: {stats["void_blocked"]} of those are '
                  f'VOID in the table — refusing to re-mint an identical '
                  f'voided wager; a replacement on another side or line is '
                  f'unaffected')
        if stats['in_batch']:
            print(f'  receipts{tag}: {stats["in_batch"]} row(s) were the '
                  f'SAME wager twice within one payload, written once')
        if stats.get('ambiguous'):
            # Loud on purpose: these are the rows no key can judge, and
            # they are where a future duplicate would come from.
            print(f'  ⚠ receipts{tag}: {stats["ambiguous"]} row(s) carry no '
                  f'game_id and no matchup on a label that already spans '
                  f'2+ games — written, because refusing could delete a '
                  f'real wager. Set matchup on these at the source.')
    return fresh, stats


def capture(rows: Iterable[dict], surface: str, dry_run: bool = False,
            quiet: bool = False, mode: str = 'live') -> dict:
    """Write live receipts. Returns {'written': n, 'failed': n, 'skipped': n}.

    Never raises: a publisher must not lose its card because an audit row
    failed. Always prints on failure.
    """
    rows = [r for r in (rows or []) if r]
    out = {'written': 0, 'failed': 0, 'skipped': 0}
    if not rows:
        return out
    if not (SB and KEY):
        print(f'  ⚠ receipts ({surface}): SUPABASE env missing — {len(rows)} NOT captured')
        out['failed'] = len(rows)
        return out
    now = datetime.now(timezone.utc).isoformat()
    payload = []
    refused = []
    for r in rows:
        if not r.get('sport') or not r.get('game_date') or not r.get('source_id'):
            out['skipped'] += 1
            continue
        r.setdefault('surface', surface)
        r.setdefault('published_at', now)
        # The affirmative claim: this row was written in the same breath as
        # the publish. 2026-11-16: `mode` exists so a repair can come through
        # THIS chokepoint — invariant screen, first-write-wins, dedupe — and
        # still label itself honestly as 'reconstructed'. The alternative was
        # a parallel inserter, and a guard on one source of eight is not a
        # guard (feedback_guard_belongs_at_the_chokepoint). Default stays
        # 'live'; only a backfiller may pass anything else, and it must.
        r['capture_mode'] = mode
        r = sanitize(recover_line(r))
        # B142: refuse a line-bearing market with no line. A receipt is the
        # published record, and an ungradeable row in it becomes a fabricated
        # W/L the moment a grader guesses the line. Loud, never silent.
        if unactionable(r):
            refused.append(r)
            out['skipped'] += 1
            continue
        payload.append(r)
    # ══ 2026-10-30 · THE REST OF THE INVARIANTS, AT THIS SAME CHOKEPOINT ══
    # `unactionable` above refuses a line-bearing market with no line. It was
    # the only guard of its kind, and tonight's board produced four more
    # defects of exactly the same shape — a priceless play, a parlay whose
    # label disagreed with its own legs, and a fade on a market already
    # measured negative. They belong here with it rather than in each
    # composer: a guard on one of eight sources is not a guard
    # (feedback_guard_belongs_at_the_chokepoint).
    try:
        from receipt_invariants import screen as _screen
        payload, _inv_refused = _screen(payload, surface, quiet=quiet)
        if _inv_refused:
            out['skipped'] += len(_inv_refused)
            out['invariant_refused'] = len(_inv_refused)
        # 2026-10-30: the same side went out at two different numbers on two
        # surfaces eight hours apart (Warriors +1.5 then +3.5). WARN only --
        # refusing the second receipt would leave a pick visible with no
        # receipt behind it, which cannot be graded at all. The cure is
        # composers calling frozen_quote(); this is how the unwired ones
        # stay visible.
        try:
            from receipt_invariants import check_line_coherence as _coh
            _mismatch = _coh(payload, surface, quiet=quiet)
            if _mismatch:
                out['line_incoherent'] = len(_mismatch)
        except Exception as _ce:                           # noqa: BLE001
            print(f'  ! line-coherence check unavailable ({_ce})')
    except ImportError:
        # Loud: a missing guard must not read as a clean run.
        print(f'  !! receipt_invariants unavailable — {surface} rows written '
              f'UNCHECKED')

    if out['skipped'] and not quiet:
        print(f'  ⚠ receipts ({surface}): {out["skipped"]} row(s) not captured')
    if refused:
        out['unactionable'] = len(refused)
        print(f'  *** receipts ({surface}): REFUSED {len(refused)} row(s) — '
              f'line-bearing market with no pick_line ***')
        for r in refused[:8]:
            print(f'      {r.get("sport")} {r.get("market")} '
                  f'{r.get("game_date")} {r.get("matchup")} '
                  f'label={r.get("pick_label")!r} src={r.get("source_id")}')
    if not payload:
        return out
    if dry_run:
        out['written'] = len(payload)
        if not quiet:
            print(f'  [DRY] would capture {len(payload)} {mode} '
                  f'receipt(s) → {surface}')
        return out
    # First-write-wins enforced in code, not by ON CONFLICT — see
    # _live_keys for why the partial index of 20261025a cannot be an
    # arbiter here, and screen_for_write for the one screen every writer
    # shares.
    fresh, _st = screen_for_write(payload, label=surface, quiet=quiet)
    if fresh is None:
        print(f'  ⚠ receipts ({surface}): cannot verify what is already '
              f'recorded — refusing to write rather than duplicate the card')
        out['failed'] = len(payload)
        return out
    for _k in ('already', 'dup_bet', 'void_blocked', 'in_batch'):
        if _st.get(_k):
            out['in_batch_dup' if _k == 'in_batch' else _k] = _st[_k]
    if not fresh:
        return out

    def _send(batch):
        return requests.post(f'{SB}/rest/v1/public_receipts',
                             headers=H_WRITE, json=batch, timeout=60)

    try:
        resp = _send(fresh)
        if resp.status_code in (200, 201, 204):
            out['written'] = len(fresh)
            if not quiet:
                print(f'  🧾 captured {len(fresh)} {mode.upper()} '
                      f'receipt(s) → {surface}')
        elif resp.status_code == 409:
            # Raced another writer. The batch is all-or-nothing, so retry one
            # row at a time: a single collision must not cost the rest of the
            # card its receipts.
            ok = raced = failed = 0
            for r in fresh:
                rr = _send([r])
                if rr.status_code in (200, 201, 204):
                    ok += 1
                elif rr.status_code == 409:
                    raced += 1
                else:
                    failed += 1
                    print(f'  ⚠ receipts ({surface}) row HTTP '
                          f'{rr.status_code}: {rr.text[:160]}')
            out['written'] = ok
            out['already'] = out.get('already', 0) + raced
            out['failed'] = failed
            if not quiet:
                print(f'  🧾 captured {ok} LIVE receipt(s) → {surface} '
                      f'({raced} raced, {failed} failed)')
        else:
            out['failed'] = len(fresh)
            print(f'  ⚠ receipts ({surface}) HTTP {resp.status_code}: '
                  f'{resp.text[:240]}')
    except Exception as e:                                  # noqa: BLE001
        out['failed'] = len(fresh)
        print(f'  ⚠ receipts ({surface}) raised: {e}')
    return out


# ── surface adapters ────────────────────────────────────────────────

def _side_for_receipt(it: dict, market: str, prop_side):
    """The side taken, for NON-prop markets, so CLV can be computed.

    ══ 2026-10-19 · pick_side WAS NULL ON EVERY CARD SPREAD RECEIPT ══
    All 65 sharp_card and all 41 sweat_card spread receipts carried
    pick_side=NULL, while game_read carried it on 409 of 409. Closing line
    value needs the side: a line of -6.0 is a different bet depending on
    whether we took the home team or the away team, so without it a receipt
    cannot be compared to the close at all, and CLV was computable on only
    93 of 515 spread picks.

    The card item already HAS it (`side`: 'HOME'). Both writers simply never
    read it. Falls back to deriving it from the label against the matchup,
    then to None - never a guess.
    """
    if prop_side:
        return prop_side
    sd = str(it.get('side') or it.get('pick_side') or '').strip().upper()
    if sd in ('HOME', 'AWAY', 'OVER', 'UNDER'):
        return sd
    if str(market).lower() in ('total', 'totals', 'ou'):
        lab = str(it.get('label') or it.get('pick') or '').upper()
        if lab.startswith('OVER') or ' OVER ' in lab:
            return 'OVER'
        if lab.startswith('UNDER') or ' UNDER ' in lab:
            return 'UNDER'
        return None
    # team markets: match the label's team against the matchup's two sides.
    # PREFIX then WORD BOUNDARY, never a bare substring: NFL matchups use
    # 2-3 letter codes and 'NO' is inside 'Minnesota', 'LA' inside 'Atlanta',
    # so a substring test resolves the wrong side on real rows. A tie or an
    # ambiguous match returns None - a NULL side is recoverable, a wrong one
    # silently inverts the pick.
    import re as _re
    lab = str(it.get('label') or it.get('pick') or '')
    mu = str(it.get('game') or it.get('matchup') or '')
    if ' @ ' in mu and lab:
        away, home = (q.strip() for q in mu.split(' @ ', 1))
        head = lab.split('  ·')[0].strip().lower()

        def _score(team):
            t = str(team or '').strip().lower()
            if not t:
                return 0
            if head.startswith(t):
                return 2                      # "Baylor -6" vs home "Baylor"
            # ══ 2026-11-25 · THIS \b WAS A LITERAL BACKSPACE ══════════
            # Written as a real 0x08 byte rather than the regex word
            # boundary, so `head` could never contain it and this
            # branch returned 0 for every label. _side_for_receipt
            # therefore resolved a side ONLY when the label STARTS
            # with the team name, and scored 0-0 otherwise -- which
            # falls through to the caller's own fallback instead of
            # the answer this function had. Found 2026-11-25 by a
            # test that caught the same corruption in canon_market
            # one screen above; it is pre-existing, not from that
            # change. A regex that cannot match is invisible on read
            # ([[feedback_a_handler_sentinel_is_not_a_dead_expression]]).
            if _re.search(rf'\b{_re.escape(t)}\b', head):
                return 1                      # whole word elsewhere
            return 0
        sh, sa = _score(home), _score(away)
        if sh > sa:
            return 'HOME'
        if sa > sh:
            return 'AWAY'
    return None


def sharp_card_rows(items: list, game_date: str) -> list[dict]:
    """Shape published Sharp Card items into receipt rows.

    Item shape (verified against live jerry_cache.sharp_card_2026-09-20):
      sport, type, tier, conviction, pick, matchup, line, odds, units,
      reason, game_id  — prop items additionally carry `id`.
    """
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sport = str(it.get('sport') or '').upper()
        # ══ 2026-11-16 · TWO WRITERS, TWO WORDS FOR THE MARKET ══
        # The sides path emits `type` ('ml' / 'rl' / 'total' / 'spread').
        # The measured-cell prop path added on 2026-11-01 emits a wholly
        # different item shape — `market: 'prop'`, plus `game`, `why`,
        # `cell_roi`, `cell_n`, and NO `type` key at all:
        #
        #   10-28 item: type='prop'  market=None   conviction=72
        #   11-15 item: type=None    market='prop' conviction=None
        #
        # Reading only `type`, every one of those prop legs fell straight
        # through the `continue` below, so it was published to
        # jerry_cache (where the app reads it) and never got a receipt.
        # No receipt means no leg in daily_surface_records, which means
        # The Sharp's PUBLIC RECORD counted its sides and silently
        # dropped its props: last prop receipt 2026-10-28, while 8 prop
        # legs shipped in the 8 days to 11-15 alone. Measured over
        # 11-08..11-15 the gap ran the stated record 52.6% / +2.0% ROI
        # (n=19) against 46.4% / -10.2% (n=28) for what actually shipped.
        #
        # Accept either word. A receipt must describe what shipped, and
        # the writer that shipped it is free to name the field; this is
        # the reconciler, so the tolerance belongs here.
        # See feedback_three_writers_three_manual_markers,
        # feedback_a_display_label_became_an_identity_key.
        market = str(it.get('type') or it.get('market') or '').lower()
        if not sport or not market:
            print(f'  ! receipt skipped, no market: {str(it)[:120]}')
            continue
        # Anchor precedence matters. `game_id` was only added to Sharp
        # Card items on 2026-09-17, so every earlier totals pick carries
        # game_id=None with a generic label ("Under 8.0") — three
        # different games on one slate produced the SAME source_id and
        # two of the three lost the conflict silently. 11 of 410 picks
        # vanished that way. Fall back to the matchup, which identifies
        # the game even when game_id is absent, and only then to the
        # label.
        anchor = it.get('id') if market == 'prop' else it.get('game_id')
        anchor = (anchor or it.get('game_id') or it.get('id')
                  or it.get('matchup') or it.get('pick'))
        if not it.get('game_id') and not it.get('id'):
            # Generic labels repeat across games on the same slate; pin
            # the line too so two different totals never coincide.
            anchor = f'{anchor}|{it.get("pick")}|{it.get("line")}'
        out.append({
            'sport': sport,
            'surface': 'sharp_card',
            'game_date': game_date,
            'source_id': _sid(market, anchor),
            'source_table': 'jerry_cache.sharp_card',
            'game_id': it.get('game_id'),
            'matchup': it.get('matchup'),
            'market': market,
            'pick_label': it.get('pick'),
            'pick_line': it.get('line'),
            'pick_side': _side_for_receipt(it, market, None),
            'pick_odds': it.get('odds'),
            'tier': (it.get('tier') or None),
            'conviction': it.get('conviction'),
            'audit': {
                'units': it.get('units'),
                'reason': (it.get('reason') or '')[:600],
                'captured_by': 'generate_sharp_card',
                # 2026-10-06: `conviction` above is now the BASE score, so it
                # agrees with `tier` (it used to be refit, which is why The
                # Sharp showed conv 24 beside a STRONG tier while the Sweat
                # Card showed 65). refit is kept here rather than dropped:
                # it is a nearly independent score (r=+0.131 against base on
                # 386 graded rows) and the only way to settle live-or-die is
                # to keep scoring it on published picks.
                'refit_conviction': it.get('refit_conviction'),
            },
        })
    return out


_RESULT_MAP = {
    'win': 'WIN', 'w': 'WIN', 'hit': 'WIN',
    'loss': 'LOSS', 'l': 'LOSS', 'lose': 'LOSS', 'miss': 'LOSS',
    'push': 'PUSH', 'tie': 'PUSH',
    # ══ 2026-09-26 · PENDING IS NOT A RESULT ══
    # This used to store the string 'PENDING'. grade_public_receipts selects
    # `result=is.null` to find work, so a non-null sentinel meaning "not
    # graded yet" made the row invisible to the grader FOREVER — it looked
    # graded to the query and ungraded to a human. 103 receipts were stuck
    # that way, 90 of them Sweat Card, including a POTD (Chicago White Sox
    # ML, conviction 88) whose game had finished and which graded WIN as a
    # game_read on the same slate.
    #
    # Pending is the ABSENCE of a result, and NULL already means exactly
    # that. Two spellings for one state is what created the hole.
    'pending': None, 'no-pick': None, 'none': None, '': None,
}


def norm_result(v) -> str | None:
    """Source tables spell results however they like — daily_dawg says
    'Win'/'Loss'/'Push', daily_best_bet_history adds 'Pending'/'no-pick'.
    public_receipts is uppercase (WIN/LOSS/PUSH) with NULL for not-yet-
    graded. A record split across two spellings of the same outcome is a
    record nobody can total, so normalise at the boundary — and "pending"
    normalises to NULL, not to a fourth value (see _RESULT_MAP).
    """
    if v is None:
        return None
    return _RESULT_MAP.get(str(v).strip().lower(), str(v).strip().upper() or None)


#: Market words, as WORD-BOUNDED patterns mapped to the canonical code.
#:
#: == 2026-11-16 - WHY THESE ARE REGEXES AND NOT `in` CHECKS ==============
#: A substring test for 'ats' matches **Bearcats, Wildcats, Bobcats,
#: Wildcats** -- Arizona, Kentucky, Villanova, Kansas State and Cincinnati
#: all field one -- so `'ats' in label` would call a moneyline on the
#: Wildcats a SPREAD and then demand a line for it. Same family as the
#: prefix strip that named another school.
#: [[feedback_prefix_strip_can_name_another_school]]
#: [[feedback_parse_a_pick_by_shape_not_vocabulary]]
_MARKET_WORDS = (
    (re.compile(r'\b(?:over|under|o/u)\b', re.I),            'total'),
    (re.compile(r'\b(?:ats|spread)\b', re.I),                'spread'),
    (re.compile(r'\b(?:pl|puck\s*line|puckline)\b', re.I),   'puckline'),
    (re.compile(r'\b(?:rl|run\s*line)\b', re.I),             'rl'),
    (re.compile(r'\b(?:ml|moneyline|money\s*line)\b', re.I), 'ml'),
)

#: A team followed by a signed number and nothing else that names a market
#: ("Baylor -6", "Brooklyn Nets +4.5") is a spread. Checked LAST, after the
#: market words, so "Buffalo Sabres -1.5" is still read as the puckline it
#: says it is.
_BARE_SIGNED_RE = re.compile(r'[-+]\d+(?:\.\d+)?\s*(?:\(|$)')


def _bet_market(label: str) -> str | None:
    """The real market behind a display label, or None.

    "Braves ML (Jerry 88/100)" -> ml.  Recorded in audit, never written to
    `market`: POTD/DotD keep their own codes so the rows already using them
    stay countable. `_effective_market` below is what makes those codes
    answerable to the line guard anyway.

    == 2026-11-16 . THIS RETURNED None FOR EVERY FOOTBALL/BASKETBALL SPREAD
    It knew ' ml', 'over'/'under', and '-1.5'/'+1.5'/'rl' -- so
    "Golden State Warriors ATS (Jerry 55/100)" matched NOTHING and the
    audit recorded bet_market=None. That was the second of two chances to
    notice a spread had no number, and it is why a bare POTD label reached
    the record and graded VOID (2026-11-08 'Boston Celtics ATS').
    """
    t = str(label or '')
    for rx, code in _MARKET_WORDS:
        if rx.search(t):
            return code
    if _BARE_SIGNED_RE.search(t):
        return 'spread'
    return None


#: `market` values that are a SURFACE or a BET STRUCTURE rather than a
#: market. For these the real market lives in audit.bet_market.
#:
#: potd/dotd are SINGLE-GAME picks, so they can be a spread or a total and
#: must answer to the line guard. parlay/chalk_parlay/fight genuinely have
#: no single line -- a parlay's numbers live on its legs and a fight has
#: none -- so they stay exempt. Measured over 12,000 receipts since
#: 2026-10-01: chalk_parlay 30, potd 14, dotd 12, fight 5, parlay 1.
_SURFACE_MARKETS = {'potd', 'dotd'}
_NO_LINE_STRUCTURES = {'parlay', 'chalk_parlay', 'fight', 'prop', 'unknown'}


def _effective_market(row: dict) -> str:
    """The market this row should be GUARDED as.

    == 2026-11-16 . A SURFACE NAME IN THE MARKET COLUMN DISARMED THE GUARD
    `unactionable` and `recover_line` both key on `market`, and
    potd_rows/dawg_rows write `market='potd'`/`'dotd'` -- a SURFACE name in
    a market column. So the POTD, the single most prominent pick in the
    app, was structurally exempt from the line guard, from recover_line and
    from the spread price band. The guard was never wrong; it was never
    ASKED. [[feedback_a_display_label_became_an_identity_key]]
    [[feedback_guard_belongs_at_the_chokepoint]]

    `market` is deliberately NOT rewritten -- the stored codes stay
    countable -- so this resolves the market for guard purposes only, from
    audit.bet_market and then from the label itself.
    """
    m = str(row.get('market') or '').strip().lower()
    if m not in _SURFACE_MARKETS:
        return m
    aud = row.get('audit')
    bm = (aud or {}).get('bet_market') if isinstance(aud, dict) else None
    return str(bm or _bet_market(row.get('pick_label')) or m).lower()


def potd_rows(records: list, game_date: str | None = None) -> list[dict]:
    """Pick of the Day, from daily_best_bet_history rows.

    Shape (verified, 132 rows back to 2026-04-10): bet_date, sport, game,
    lean, sweat_score, odds_american, result, narrative. One POTD per
    day, so bet_date alone is a sufficient source_id.
    """
    out = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        gd = str(rec.get('bet_date') or game_date or '')[:10]
        sport = str(rec.get('sport') or 'MLB').upper()
        lean = rec.get('lean')
        if not gd or not lean:
            continue
        out.append({
            'sport': sport,
            'surface': 'potd',
            'game_date': gd,
            'source_id': _sid('potd', gd),
            'source_table': 'daily_best_bet_history',
            'matchup': rec.get('game'),
            'market': 'potd',
            'pick_label': lean,
            'pick_odds': rec.get('odds_american'),
            'conviction': rec.get('sweat_score'),
            'result': norm_result(rec.get('result')),
            'audit': {
                'bet_market': _bet_market(lean),
                'narrative': (rec.get('narrative') or '')[:600],
                'captured_by': 'jerry_anchor_potd',
            },
        })
    return out


def _num_or_none(v):
    """float(v) or None — so a sign flip never raises on a NULL or a string."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _side_from_matchup(team, matchup) -> str | None:
    """HOME / AWAY for a team pick, from an "Away @ Home" matchup string.

    Needed because a price is stored per SIDE, not per team name: without
    this, every Dawg receipt is unpriceable. 130 of 130 Dawg receipts carry
    no price today for exactly this reason — `daily_dawg` has no price column
    of its own, and the fallback to line_history needs a side.

    Returns None rather than guessing when the team is not clearly one of the
    two: a wrong side yields the OPPOSING price, which looks valid and
    silently inverts the ROI of every row built on it.
    """
    t = str(team or '').strip().lower()
    m = str(matchup or '')
    if not t or '@' not in m:
        return None
    away, _, home = m.partition('@')
    away, home = away.strip().lower(), home.strip().lower()
    in_home = t in home or home in t
    in_away = t in away or away in t
    if in_home and not in_away:
        return 'HOME'
    if in_away and not in_home:
        return 'AWAY'
    return None


def dawg_rows(records: list, game_date: str | None = None) -> list[dict]:
    """Dawg of the Day, from daily_dawg rows.

    Shape (verified, 123 rows back to 2026-04-22): game_date, team,
    matchup, game_id, tier, conviction, close_spread, spread_delta,
    result. One per day.

    2026-10-28: THE PRICE EXISTS NOW. The multi-sport Dawg rebuild
    shipped today added price_american / price_implied / priced_at /
    market / pick_side to `daily_dawg`, so the sentence below stopped
    being true the same day it was still being relied on. Verified on
    tonight's two rows: MLB +135, NHL +117, both receipts pick_odds NULL.
    `pick_odds` now comes straight off the source row, and the source's
    own `pick_side` wins over the derived one when it is present --
    the generator knows which side it picked; this builder is guessing
    from a matchup string.

    `market` stays 'dotd'. The source row says 'ml', but the receipt is
    what reached the user and its market sets the denominator every
    published Dawg record is computed against (the R3 decision, 10-28).
    The source's market is recorded in `audit` instead, so a later ROI
    pass can use the right breakeven without the record moving.

    2026-10-06: this builder set no `pick_odds` and no `pick_side`, and
    `daily_dawg` had no price column at all — so all 130 Dawg receipts are
    unpriced. The price is recoverable from line_history (which holds spread
    prices for every sport), but only given a side, so `pick_side` is now
    derived from the matchup. `pick_line` already carries close_spread, which
    is the other half line_history needs to price the right line.
    """
    out = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        gd = str(rec.get('game_date') or game_date or '')[:10]
        team = rec.get('team')
        if not gd or not team:
            continue
        _side = (str(rec.get('pick_side')).strip().upper()
                 if rec.get('pick_side')
                 else _side_from_matchup(team, rec.get('matchup')))
        _cs = _num_or_none(rec.get('close_spread'))
        out.append({
            'pick_side': _side,
            # The line FROM THE PICKED SIDE, not the home handicap.
            # Verified 2026-10-06 on 4 of 4 games with spread quotes:
            # close_spread equals the HOME line every time, while
            # line_history stores each side's own line (home +1.5 / away
            # -1.5 are the two halves of one market). The Dawg is usually
            # the AWAY dog, so storing close_spread unflipped pointed at the
            # FAVOURITE's run line — a real price for the opposite bet.
            # 2026-10-20 · GUARD THE NEGATION. This was
            #     -_num_or_none(rec.get('close_spread')) if _side == 'AWAY'
            # and `-None` is a TypeError, so ONE row with a NULL close_spread
            # (2026-07-09 Cubs @ Orioles, the only one of 132) raised inside
            # this list comprehension and took the ENTIRE dawg backfill down
            # on every run since the flip was added on 10-06:
            #     x dawg failed: bad operand type for unary -: 'NoneType'
            # Nothing was lost only because MLB's last dawg was 10-13 and the
            # upsert ignores duplicates; the next dawg pick in any sport would
            # have written no receipt at all. A row with no line now yields a
            # null pick_line, which is honest, instead of killing 131 others.
            'pick_line': (-_cs if (_side == 'AWAY' and _cs is not None) else _cs),
            'sport': str(rec.get('sport') or 'MLB').upper(),
            'surface': 'dawg',
            'game_date': gd,
            'source_id': _sid('dotd', rec.get('game_id') or gd),
            'source_table': 'daily_dawg',
            'game_id': rec.get('game_id'),
            'matchup': rec.get('matchup'),
            'market': 'dotd',
            'pick_label': team,
            'pick_odds': _num_or_none(rec.get('price_american')),
            'tier': rec.get('tier'),
            'conviction': rec.get('conviction'),
            'result': norm_result(rec.get('result')),
            'audit': {
                'spread_delta': rec.get('spread_delta'),
                'captured_by': 'generate_dawg_of_day',
                'price_basis': ('publish' if rec.get('price_american')
                                is not None else None),
                'price_source': ('daily_dawg.price_american'
                                 if rec.get('price_american') is not None
                                 else None),
                'source_market': rec.get('market'),
            },
        })
    return out


# Sweat Card `type` is a DISPLAY label, not a market code. Verified over
# 141 cached days / 854 top_8 items: Over/Under, ML, DotD, POTD and a
# long tail of prop_* families.
_SWEAT_MARKET = {
    'ml': 'ml',
    'rl': 'rl',
    'total': 'total',
    'over/under': 'total',
    'potd': 'potd',
    'dotd': 'dotd',
}


def _sweat_market(raw: str) -> str:
    t = str(raw or '').strip().lower()
    if t.startswith('prop_') or t == 'prop':
        return 'prop'
    return _SWEAT_MARKET.get(t, t or 'unknown')


def prop_jerry_rows(reads: list, sport: str) -> list[dict]:
    """Receipt rows for Prop Jerry picks, for capture AT PUBLISH TIME.

    == 2026-10-29 · PROP JERRY HAD NO LIVE CAPTURE AT ALL ==
    Measured on 10-29 over receipts for games from 10-22:

        surface        live  reconstructed  live%
        sweat_card       28         3       90.3%
        sharp_card       57         3       95.0%
        prop_jerry        0       925        0.0%

    Zero. Every prop receipt in the table exists only because
    backfill_public_receipts rebuilt it afterwards, and that path stamps
    capture_mode='reconstructed' by design. capture() is called from the
    sweat card, sharp card, dawg, POTD and fade publishers — props were
    simply never wired in, and nothing noticed because the backfill kept
    the table looking full.

    It is the reason the prop record cannot be published. Same day, on the
    curated prop surfaces where both modes exist:

        curated props  reconstructed  59.40%  ROI +5.06%
        curated props  live           56.00%  ROI -3.65%

    — the edge is in the reconstructed half only. With prop_jerry at 0%
    live there is no honest prop number to quote at all, for any sport.

    SHAPE IS COPIED FROM backfill_prop_jerry DELIBERATELY: same pick_label,
    same FADE flip, same `audit.side_convention='backed'` stamp. A live row
    and a backfilled row for the same read must be indistinguishable, or
    the two paths grade differently (the 10-04 two-conventions mess is
    exactly that failure, and it left 123 fade receipts permanently
    unsettleable).

    PASS reads are NOT captured. A receipt is the record of a play we
    published; a PASS is the record of one we declined, and writing it as a
    pick is how a denominator gets inflated with plays nobody made.
    """
    out: list[dict] = []
    for row in (reads or []):
        if not isinstance(row, dict):
            continue
        verdict = str(row.get('call_verdict') or '').upper() or None
        if verdict not in ('BACK', 'FADE'):
            continue                      # PASS / unset is not a published pick
        direction = str(row.get('direction') or '').lower()
        # A FADE backs the OPPOSITE of the prop's own side.
        backed = direction
        if verdict == 'FADE' and direction in ('over', 'under'):
            backed = 'under' if direction == 'over' else 'over'
        try:
            odds_int = (int(row['book_odds'])
                        if row.get('book_odds') is not None else None)
        except (TypeError, ValueError):
            odds_int = None
        line = row.get('prop_line')
        out.append({
            'sport': row.get('sport') or sport,
            'game_id': row.get('game_id'),
            'surface': 'prop_jerry',
            'market': 'prop',
            'game_date': row.get('game_date'),
            'player_name': row.get('player_name'),
            'prop_type': row.get('prop_type'),
            'pick_side': backed.upper() if backed in ('over', 'under') else None,
            'pick_line': line,
            'pick_odds': odds_int,
            'matchup': None,
            'pick_label': (
                f"{row.get('player_name', '')} "
                f"{(backed or direction).upper()} {line} {row.get('prop_type', '')}"
                f" @ {odds_int if odds_int else '?'}"
            ),
            'tier': None,
            'conviction': row.get('conviction'),
            'source_table': 'prop_jerry_reads',
            'source_id': str(row.get('id')),
            'audit': {'call_verdict': verdict, 'side_convention': 'backed'},
        })
    return out


def _card_anchor(it: dict, market: str, label: Any) -> str:
    """The identity of a card pick, independent of which writer composed it.

    == 2026-10-31 - ONE BET, TWO IDENTITIES, BOTH COUNTED ==

    `source_key` is composed by whichever engine supplied the pick, and the
    fade's shape is three-part: 'fade:401909281:rl'. The old code stripped
    only the FIRST segment, leaving '401909281:rl', while the same pick
    reaching the card without a source_key anchored on the bare game_id,
    '401909281'. One bet, two source_ids -- and the partial unique index on
    (sport, surface, game_date, source_id) cannot see they are one bet, so
    both stay live and both count.

    That is what made 2026-10-30 read 7-0 in the receipt ledger while the
    card blob held 5 picks and said 5-0: Orlando Magic +5.5 and Golden State
    +3.5 were each counted twice, both WINs. The blob was right; the ledger
    that feeds surface_records was not.

    For every market but prop the GAME is the identity, so anchor on game_id
    and let source_key fill in only when there is no game_id -- POTD/DotD
    anchor on the date. Props keep source_key, which carries
    'player|prop_type|line'; a game_id would collapse every prop in a game
    into one row (the defect fixed on 10-04).
    """
    if market != 'prop':
        gid = it.get('game_id')
        if gid not in (None, '', 'None'):
            return str(gid)
    # ══ 2026-11-18 · A PROP MUST NEVER ANCHOR ON A BARE game_id ═════════
    # The chain below used to read `source_key or game_id or id`, so any
    # prop dict without a source_key fell through to game_id -- producing
    # exactly the collision the docstring above warns about. It was live:
    # fetch_nba_prop_floor emitted no source_key and no `id` (its props-row
    # id sat under the private `_prop_id`), so 11-16 wrote
    # source_id='prop:a613d212...' and 'prop:39dbdb7f...' -- the GAME's id.
    # Two props in one game would have collapsed into one receipt and one
    # of the two bets would have vanished from the ledger.
    #
    # The producer is fixed, but this is the chokepoint every prop writer
    # passes through, and a guard on one of eight sources is not a guard
    # ([[feedback_guard_belongs_at_the_chokepoint]]). Order now prefers the
    # props-row id over the game, and a bare game_id is refused outright in
    # favour of a composite that still distinguishes two props in one game.
    _pid = it.get('id') or it.get('_prop_id')
    anchor = it.get('source_key') or _pid
    if not anchor:
        _gid = it.get('game_id')
        _lbl = it.get('label') or label or ''
        if _gid not in (None, '', 'None'):
            # Keep the game for scope, but NEVER alone: the label carries
            # player + stat + side + line, which is what makes two props in
            # the same game two receipts instead of one.
            anchor = f'{_gid}|{_lbl}|{it.get("line")}'
            # ASCII only. This runs inside a receipt writer, and a cp1252
            # stdout raises UnicodeEncodeError on a decorative glyph -- a
            # log line must never be able to kill a publish.
            print('  !! prop with no source_key or prop id - anchoring on '
                  f'game+label ({anchor[:70]}). Give the producer a '
                  'source_key.')
        else:
            anchor = None
    if isinstance(anchor, str) and ':' in anchor:
        anchor = anchor.split(':', 1)[1]
        if market and anchor.endswith(':' + market):
            anchor = anchor[:-(len(market) + 1)]
    if not anchor:
        anchor = f'{it.get("game") or ""}|{label}'
    return str(anchor)


def sweat_card_rows(card: dict, game_date: str) -> list[dict]:
    """Shape a published Sweat Card into receipt rows.

    Takes the whole card, not one list, because the picks users see live
    in TWO sections and only counting one silently halves the record —
    the same mistake publish_lock made until 2026-09-19, when football
    picks turned out to have never been locked at all.

      top_8          dashboard top-8. Carries NO `sport` field; every
                     source_table observed across 141 days is MLB
                     (mlb_pipeline_props, mlb_game_results, daily_dawg,
                     daily_best_bet_history), and football lives in its
                     own section, so MLB is the correct default rather
                     than a guess.
      football_picks NCAAF/NFL, carries an explicit `sport` + `game_id`.
    """
    if not isinstance(card, dict):
        return []
    # ══ 2026-11-06 · RECEIPTS NOW COME FROM THE LIST THE APP RENDERS ══
    # The app renders `unified_top_picks`. Receipts were cut from top_8 +
    # football_picks, which are a DIFFERENT SET, so what we showed and what
    # we graded diverged in both directions:
    #
    #     date     card items (unified)   sweat_card receipts
    #     10-30            5                     14
    #     10-31            6                     12
    #     11-05            4                      1
    #     11-06            4                      3
    #
    # On 11-05 top_8 was [] while unified held 4 picks, so NOTHING on the
    # card was captured and `East Texas A&M ML +136` sat Pending through a
    # WIN (settled by hand; see the 11-06 daily log). On 11-06 the one
    # uncaptured item is the floor fill, `Missouri ML`, which is exactly the
    # class that has gone 7 slots / 0 graded since the fill shipped --
    # displayed to users and absent from every record.
    #
    # Andy's call, 11-06: keep the fills and GRADE them, rather than hiding
    # them or dropping them. So the receipt source becomes the rendered
    # list. unified comes LAST so that where a pick sits in both lists the
    # top_8 row still establishes the identity, preserving the
    # first-write-wins ordering _live_keys depends on.
    #
    # Double-counting is handled twice over: _card_anchor already makes
    # every writer agree on one source_id per bet (the 10-31 fix), and the
    # in-batch `_seen` set below stops one payload carrying the same
    # (sport, market, anchor) twice -- a plain INSERT raises 23505 on a
    # duplicate and the batch is all-or-nothing.
    out = []
    _seen = set()
    sections = (('top_8', 'MLB'), ('football_picks', None),
                ('unified_top_picks', None))
    for section, default_sport in sections:
        for it in (card.get(section) or []):
            if not isinstance(it, dict):
                continue
            raw_type = it.get('type')
            market = _sweat_market(raw_type)
            sp = str(it.get('sport') or default_sport or '').upper()
            label = it.get('label') or it.get('pick')
            if not sp or not label:
                continue
            # source_key is the anchor the card itself uses: a game_id for
            # ML/total, a prop id (sometimes "table:id") for props, and the
            # DATE for POTD/DotD — which is why market has to be part of
            # the key, or the day's POTD and DotD collapse into one row.
            anchor = _card_anchor(it, market, label)
            _k = (sp, market, str(anchor))
            if _k in _seen:
                continue
            _seen.add(_k)
            # ══ 2026-10-04 · A PROP RECEIPT MUST CARRY ITS OWN IDENTITY ══
            # These rows were written with player_name, prop_type, pick_line
            # and pick_side ALL NULL, with everything packed into source_id as
            # 'prop:Shohei Ohtani|hits_under|1.5'. grade_public_receipts
            # matches on the structured columns, so every sweat_card prop was
            # invisible to it — on 10-03 that stranded Ohtani and Kim, both of
            # which had WON, while the prop_jerry receipts for the same players
            # graded normally because that writer populates the columns.
            # Same table, same day, different writer.
            #
            # The card already knows all of it: source_key is
            # "{player}|{prop_type}|{prop_line}". Parse it rather than ask the
            # grader to.
            _player = _ptype = _pside = None
            _pline = it.get('line')
            if market == 'prop':
                _sk = str(it.get('source_key') or '')
                _parts = _sk.split('|')
                if len(_parts) == 3:
                    _player, _ptype, _raw_line = (p.strip() for p in _parts)
                    try:
                        _pline = float(_raw_line)
                    except (TypeError, ValueError):
                        pass
                    # prop_type encodes the side: hits_under / ks_over.
                    _tail = _ptype.rsplit('_', 1)[-1].lower()
                    if _tail in ('over', 'under'):
                        _pside = _tail.upper()
            out.append({
                'sport': sp,
                'surface': 'sweat_card',
                'game_date': game_date,
                'source_id': _sid(market, anchor),
                'player_name': _player,
                'prop_type': _ptype,
                'pick_side': _side_for_receipt(it, market, _pside),
                'source_table': it.get('source_table') or 'jerry_cache.sweat_card',
                'game_id': it.get('game_id'),
                'matchup': it.get('game') or it.get('matchup'),
                'market': market,
                'pick_label': label,
                'pick_line': _pline,
                'pick_odds': it.get('odds'),
                'tier': (it.get('tier') or None),
                'conviction': it.get('conviction'),
                # top_8 carries its own graded result (826 of 854 items
                # across 141 days: 479 W / 331 L / 16 push). Dropping it
                # would leave the deepest surface we have showing zero
                # graded picks while the grade sat right there in the
                # payload. football_picks carries none — those grade via
                # the resolver like any other football pick.
                'result': norm_result(it.get('result')),
                'audit': {
                    'section': section,
                    'card_type': raw_type,
                    'rank': it.get('rank'),
                    'tier_source': it.get('tier_source'),
                    'side': it.get('side'),
                    'captured_by': 'generate_sweat_card',
                },
            })
    return out
