#!/usr/bin/env python3
"""Snap a pick's line to the number the market actually quoted.

== 2026-10-19 - WHY ==
Jerry's reads publish spread numbers that were never available anywhere.
Verified against line_history, 1000 captures across 11 books over an 11-day
window per game:

    PHI -6.5   books quoted  -2.5 -2.0 -1.5  3.0  3.5   -> never
    KC  -2.5   books quoted  -9.0 -8.5 -8.0 -7.5 -7.0   -> never
    NO  +2.5   books quoted  -1.5 -1.0 -0.5  0.0  1.0   -> never
    BAL -6.5   books quoted  -4.0 -3.5 -3.0 -2.5 -2.0   -> never

Across both football sports, 30 of 62 checked game_read picks (48%) carried a
line no book ever posted, mean 1.1 points off and up to 5.0. The curated
surfaces are far cleaner - sweat_card 1 of 15, sharp_card 2 of 17 - so this is
specifically the per-game read, where call_line comes straight out of the LLM
parse and nothing ever compares it to the market.

WHY IT MATTERS MORE THAN IT LOOKS. A record graded at a number nobody could
bet is not a record. A pick published 5 points better than the market wins for
free and inflates the surface; one published 5 points worse loses for free.
It is upstream of every tier, gate and model.

== THE SOURCE OF THE MARKET LINE IS THE WHOLE BALLGAME ==
The first version of this module read close_spread off the CONTEXT row for
every sport. A dry run over 134 stored football reads showed it "correcting"
44% of them, including an NFL read by FIFTEEN points - because
nfl_game_context.close_spread is not the market line at all. Measured against
line_history on games captured within 2h of kickoff:

    NFL    results NEGATED  mean err 0.29  27/29 exact   <- authoritative
           context as-is    mean err 9.09   2/29
           context NEGATED  mean err 3.67   9/29         <- NOT usable
    NCAAF  results as-is    mean err 0.34  92/103 exact
           context as-is    mean err 0.43  84/103 exact  <- usable

CHI @ ATL carried ctx=-9.5 where results said -1.5 and the books said +1.0.
Reading the wrong column would have corrupted far more lines than the bug it
was written to fix. So the source is explicit per sport, anything unverified
is REFUSED rather than guessed, and a disagreement beyond MAX_SNAP is treated
as a data problem and left alone.

FAILS OPEN throughout. No context, no usable market line, an unknown sport, or
any exception returns the line untouched - a read that publishes beats a
crash, and audit_published_line_integrity.py catches whatever slips through.
"""
from __future__ import annotations

#: how far a published line may sit from the market before it is snapped.
#: Half a point is the smallest increment books actually move in.
SNAP_TOLERANCE = 0.5

#: A snap larger than this is not a pick problem, it is a DATA problem.
#: Refuse it loudly rather than overwrite a pick with a number we cannot
#: trust - see the 15-point near-miss in the module docstring.
MAX_SNAP = 7.0

#: close_spread -> HOME HANDICAP multiplier, per sport AND per table, measured
#: against the books (see docstring). A sport absent from a map means that
#: table is not a usable market source for it.
_RESULTS_SIGN = {'NFL': -1.0, 'NCAAF': 1.0}
# ══ 2026-11-02 · NBA / NHL / NCAAB ADDED — THIS WAS WHY NBA SPREADS WERE
#                UNPUBLISHABLE ═════════════════════════════════════════════
# ANDY: "there are lot of live sports eveyr single day and plent ot choose
# from, eveything looks thin evey single day."
#
# A big part of "thin" was here. market_handicap() returns None for any sport
# missing from this map, so snap_line never reached its own
# "line absent; set to the market handicap" branch, and every NBA spread read
# was stored with call_line NULL. Measured on today's board:
#
#     15 NBA context rows: close_spread populated 15/15,
#                          close_spread_home_price populated 15/15
#      8 NBA rl reads:     7 of 8 carry call_line None AND price None
#
# The number was sitting on the very row the read was built from. And the
# cell it blocks is the ONE NBA cell with a positive measured record --
# graded NBA receipts, ROI at each row own price:
#
#     rl / HOME   n=36  63.9% vs BE 53.0%  ROI +19.95%  halves +16.4/+23.5
#     rl (all)    n=49  65.3% vs BE 52.7%  ROI +23.41%
#     ml (all)    n=50  62.0% vs BE 61.8%  ROI  -1.65%
#     ml / HOME   n=37  54.1% vs BE 58.8%  ROI  -9.36%
#     total       n=35  48.6% vs BE 52.4%  ROI  -6.97%
#
# So the market we could not publish is the only one measuring positive, and
# the market we DO publish (moneyline, including today POTD) is the negative
# one. n=36 over a 13-day-old season is thin and not established -- it is
# quoted here as the reason to make the market publishable, not as a claim
# that it is proven.
#
# THE SIGN IS MEASURED, NOT ASSUMED, for each of the three:
#   NBA   +1.0  close_spread IS the home-side line. Cross-checked against
#         the moneyline favourite on all 51 NBA rows carrying a spread since
#         10-28: 0 incoherent. Matches _HOME_HANDICAP_SIGN NBA 1.0 in
#         generate_the_fade.py:186.
#   NCAAB +1.0  agreed with the line_history home-side posted line on 77 of
#         78 comparable games; the single disagreement is a board split
#         across zero, which posted_consensus now refuses outright.
#   NHL   +1.0  filled from the line_history home-side line by
#         fill_ctx_close_from_line_history; 0 spread/moneyline incoherences
#         across the 37 rows holding a spread.
#
# NFL STAYS DELIBERATELY ABSENT. nfl_game_context.close_spread is a LIVE odds
# snapshot that freezes stale once a game leaves the feed -- 63 of 272
# disagree with results and line_history says books posted the RESULTS number
# 19:1 (project_nfl_stale_line_and_inverted_weights_1030). Adding it here
# would snap good NFL picks to a stale number.
_CONTEXT_SIGN = {'NCAAF': 1.0, 'NBA': 1.0, 'NHL': 1.0, 'NCAAB': 1.0}
_RESULTS_TABLE = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results'}

#: ctx columns holding the price for each side of the spread. Only sports
#: whose table actually has them; a missing column is left alone rather than
#: named in a select (feedback_explicit_select_silent_blanks).
#: A LINE inside a label, as a standalone token. The naked pattern
#: [+-]?\d+(\.\d+)? matches the digits in a TEAM NAME: "Philadelphia 76ers
#: ATS" came back as "Philadelphia -11.5ers ATS", and the 49ers would go the
#: same way. Requiring non-alphanumeric boundaries makes it a token rather
#: than a substring (feedback_parse_a_pick_by_shape_not_vocabulary).
_LINE_TOKEN = r'(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?(?![A-Za-z0-9])'

_SPREAD_PRICE_COLS = {
    'NBA': ('close_spread_home_price', 'close_spread_away_price'),
    'NCAAF': ('close_spread_home_price', 'close_spread_away_price'),
}


def market_spread_price(ctx: dict, sport: str, side: str):
    """The posted price for this side of the spread, or None.

    A spread read with no price is graded at a guess, and rule (1) of
    receipt_invariants warns on exactly that. The price lives on the same ctx
    row as the line, so it is filled from the same place and at the same
    time.
    """
    if not isinstance(ctx, dict):
        return None
    cols = _SPREAD_PRICE_COLS.get(str(sport).upper())
    if not cols:
        return None
    s = str(side or '').strip().upper()
    col = cols[0] if s == 'HOME' else cols[1] if s == 'AWAY' else None
    if not col:
        return None
    return _f(ctx.get(col))

_RESULTS_CACHE: dict = {}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _results_close_spread(ctx: dict, sport: str):
    """close_spread from the authoritative results table, via teams + date."""
    tbl = _RESULTS_TABLE.get(str(sport).upper())
    if not tbl or not isinstance(ctx, dict):
        return None
    away, home = ctx.get('away_team'), ctx.get('home_team')
    gd = str(ctx.get('game_date') or '')[:10]
    if not (away and home and gd):
        return None
    key = (tbl, str(away), str(home), gd)
    if key in _RESULTS_CACHE:
        return _RESULTS_CACHE[key]
    val = None
    try:
        import requests as _rq

        import market_line as _ML
        r = _rq.get(f'{_ML.SB}/rest/v1/{tbl}', headers=_ML.H, timeout=20,
                    params={'select': 'close_spread,game_date',
                            'away_team': f'eq.{away}',
                            'home_team': f'eq.{home}',
                            'game_date': f'eq.{gd}', 'limit': '1'})
        if r.status_code in (200, 206):
            rows = r.json()
            if rows:
                val = _f(rows[0].get('close_spread'))
    except Exception:                                          # noqa: BLE001
        val = None
    _RESULTS_CACHE[key] = val
    return val


_QUOTED_CACHE: dict = {}


def quoted_lines(ctx: dict, sport: str, side: str):
    """Every handicap the books ever posted for this side, or None.

    ══ 2026-10-19 · SNAP ONLY WHAT WAS NEVER AVAILABLE ══
    Comparing a pick to a single market number is wrong, because the number
    moves. PHI's home line ran from -2.5 to +3.5 across an 11-day window, so
    a read published at PHI -2.0 took a REAL price and only looks wrong
    against the close. Snapping it to the closing number would overwrite a
    legitimate early pick - the opposite of the bug this module exists for.

    The honest test is the one the audit uses: was our number EVER posted, at
    any book, at any time before kickoff? If yes, leave it alone. If no, it
    was never bettable and the nearest number that WAS is the correct
    replacement. That is strictly minimal - a fabricated number becomes a
    real one, and nothing else is touched.
    """
    sp = str(sport).upper()
    gid = str((ctx or {}).get('game_id') or '')
    if not gid:
        return None
    key = (sp, gid, str(side or '').upper())
    if key in _QUOTED_CACHE:
        return _QUOTED_CACHE[key]
    out = None
    try:
        import requests as _rq

        import market_line as _ML
        r = _rq.get(f'{_ML.SB}/rest/v1/line_history', headers=_ML.H, timeout=25,
                    params={'select': 'line,side,captured_at,commence_time',
                            'sport': f'eq.{sp}', 'market': 'eq.spread',
                            'game_id': f'eq.{gid}', 'limit': '1000'})
        if r.status_code in (200, 206):
            want = 'home' if str(side or '').upper() == 'HOME' else 'away'
            vals = set()
            for x in r.json():
                if str(x.get('side')) != want:
                    continue
                if str(x.get('captured_at')) >= str(
                        x.get('commence_time') or 'z'):
                    continue          # pregame captures only
                v = _f(x.get('line'))
                if v is not None:
                    vals.add(v)
            out = vals or None
    except Exception:                                          # noqa: BLE001
        out = None
    _QUOTED_CACHE[key] = out
    return out


def market_handicap(ctx: dict, sport: str, side: str,
                    allow_lookup: bool = True):
    """The market's handicap FOR THE GIVEN SIDE, or None when unknowable.

    Prefers the authoritative results table; falls back to the context column
    ONLY for a sport where that column has been measured accurate. Returns
    None rather than guessing: a refused snap leaves today's behaviour, a
    wrong snap destroys a pick.
    """
    if not isinstance(ctx, dict):
        return None
    sp = str(sport).upper()
    home_handicap = None
    if allow_lookup and sp in _RESULTS_SIGN:
        cs = _results_close_spread(ctx, sp)
        if cs is not None:
            home_handicap = _RESULTS_SIGN[sp] * cs
    if home_handicap is None and sp in _CONTEXT_SIGN:
        cs2 = _f(ctx.get('close_spread'))
        if cs2 is not None:
            home_handicap = _CONTEXT_SIGN[sp] * cs2
    if home_handicap is None:
        return None
    s = str(side or '').strip().upper()
    if s == 'HOME':
        return home_handicap
    if s == 'AWAY':
        return -home_handicap
    return None


def snap_line(line, ctx: dict, sport: str, side: str, market: str,
              tolerance: float = SNAP_TOLERANCE, allow_lookup: bool = True):
    """Return (snapped_line, note). note is None when nothing changed.

    Only spread markets are touched: a moneyline has no line and a total is a
    different number entirely. A note beginning 'REFUSED' means the caller
    must NOT overwrite the line.
    """
    if str(market or '').lower() not in ('rl', 'spread', 'ats'):
        return line, None
    ours = _f(line)
    # PREFERRED PATH: the full set of numbers the books ever posted. Only a
    # number that was NEVER posted gets replaced, and it is replaced by the
    # nearest one that WAS - so a legitimately early pick survives.
    if ours is not None and allow_lookup:
        q = quoted_lines(ctx, sport, side)
        if q:
            if any(abs(v - ours) <= 0.01 for v in q):
                return ours, None                 # it was really available
            near = min(q, key=lambda v: abs(v - ours))
            if abs(near - ours) > MAX_SNAP:
                return ours, (f'REFUSED to snap: published {ours:+.1f}, '
                              f'nearest ever posted {near:+.1f}, '
                              f'{abs(near - ours):.1f} pts apart - beyond '
                              f'MAX_SNAP {MAX_SNAP}; pick left untouched')
            return near, (f'published {ours:+.1f}, which NO book ever '
                          f'posted; nearest quoted was {near:+.1f} '
                          f'({ours - near:+.1f} off)')
    mk = market_handicap(ctx, sport, side, allow_lookup=allow_lookup)
    if mk is None:
        return line, None
    if ours is None:
        # no line emitted at all: the market's number is strictly better than
        # publishing nothing, and it is a number a user could actually take
        return mk, (f'line absent; set to the market handicap {mk:+.1f}')
    if abs(ours - mk) <= tolerance:
        return ours, None
    if abs(ours - mk) > MAX_SNAP:
        return ours, (f'REFUSED to snap: published {ours:+.1f} vs market '
                      f'{mk:+.1f} is {abs(ours - mk):.1f} pts apart, beyond '
                      f'MAX_SNAP {MAX_SNAP} - investigate the line source; '
                      f'pick left untouched')
    return mk, (f'published {ours:+.1f} but the market was {mk:+.1f} '
                f'({ours - mk:+.1f} off); snapped to the market')


def apply_to_parsed(parsed: dict, ctx: dict, sport: str,
                    verbose: bool = True) -> dict:
    """Snap parsed['call_line'] in place against the market. Fails open."""
    try:
        if not isinstance(parsed, dict):
            return parsed
        mkt = str(parsed.get('call_market') or '')
        if mkt.lower() not in ('rl', 'spread', 'ats'):
            return parsed
        # Fill the price from the same ctx row when the read has none. A
        # spread pick with no price is graded against a guess
        # (feedback_flat_110_inflates_every_ml_figure), and it is the other
        # half of why NBA spread reads could not publish.
        if parsed.get('price_american') is None:
            _px = market_spread_price(ctx, sport, parsed.get('call_side'))
            if _px is not None:
                parsed['price_american'] = int(round(_px))
                parsed['_price_filled_from'] = 'ctx close_spread side price'
                if verbose:
                    print(f'  [line] price absent; set to the posted '
                          f'{int(round(_px)):+d} from context')
        new, note = snap_line(parsed.get('call_line'), ctx, sport,
                              parsed.get('call_side'), mkt)
        if note:
            if str(note).startswith('REFUSED'):
                parsed['_line_warning'] = note
                if verbose:
                    print(f'  [line] {note}')
                return parsed
            parsed['call_line'] = new
            parsed['_line_snapped'] = note
            # rebuild call_text so the label a user reads carries the real
            # number: a snapped line with a stale label is worse than either
            ct = parsed.get('call_text')
            if ct:
                import re
                # == 2026-11-02 - A LABEL WITH NO NUMBER IN IT ========
                # re.sub can only rewrite a number that is ALREADY in
                # the text. NBA spread reads are labelled "Dallas
                # Mavericks ATS" -- no digits at all -- so filling the
                # line left the label unchanged and a user read a pick
                # with no number on it while the receipt carried -10.5.
                #
                # The test is "does the text contain a digit", NOT
                # "did the substitution change anything". My first cut
                # compared before against after, which also fires when
                # the number was already CORRECT, and produced
                # "New York Knicks -14.5 -14.5".
                _txt = str(ct)
                # the test is for a LINE TOKEN, not for any digit:
                # "Philadelphia 76ers ATS" contains digits but no
                # line, and testing r'\d' sent it down the
                # substitution branch, which correctly refuses to
                # touch "76" -- so the label came back with no
                # number on it at all.
                if re.search(_LINE_TOKEN, _txt):
                    parsed['call_text'] = re.sub(
                        _LINE_TOKEN, f'{new:+.1f}', _txt,
                        count=1)
                else:
                    # drop a trailing market word so the label reads as
                    # a bet: 'Dallas Mavericks ATS' -> '... -10.5'
                    _base = re.sub(r'\s*\b(ATS|PL|SPREAD|RL)\b\s*$',
                                   '', _txt, flags=re.I).strip()
                    parsed['call_text'] = f'{_base} {new:+.1f}'.strip()
            if verbose:
                print(f'  [line] {note}')
        return parsed
    except Exception as e:                                     # noqa: BLE001
        if verbose:
            print(f'  [line] snap SKIPPED (fail-open): '
                  f'{type(e).__name__}: {e}')
        return parsed
