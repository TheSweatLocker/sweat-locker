"""A pick in market M needs a posted number in market M, or it is not a bet.

══ 2026-11-05 · "MICHIGAN ST. ATS" ═════════════════════════════════════════
Extending audit_published_lines.py to totals showed that **26 of 78 published
picks (33%) carried no line at all**:

    NHL    6 of 10   'Anaheim Ducks PL'      close_puckline -1.5 WAS on the row
    NCAAB 20 of 60   'Michigan St. ATS'      nothing was on the row

Those are two different defects and only the first is a repair. The NHL picks
were composed before the odds pull landed and then frozen by the pick lock
while ctx filled in behind them — repair_spread_labels.py exists for exactly
that and now runs nightly.

The NCAAB ten are not repairable, and measuring them is what settles it. For
all ten games, across every capture on 2026-11-05:

    spread quotes 0    total quotes 0    moneyline quotes 0

The books never posted those games in any market, all day. Toledo @ Michigan
St., Gardner Webb @ Texas, Chicago St @ Incarnate Word — low-profile games the
Odds API does not carry. So the engine formed an ATS opinion on a game with no
spread and published it as the call, and a user opening that game read saw
"Michigan St. ATS" with no number to act on.

A pick with no number is not a bet:
  * a subscriber cannot place it;
  * the grader cannot settle it — receipt_invariants refuses a line-bearing
    market with no pick_line, so it is published AND ungradeable; and
  * the POTD price path falls back to -110, which on a dog puck line that
    closes near -195 is not a rounding error.

══ WHY PER MARKET, AND NOT "DOES THIS GAME HAVE ANY MARKET" ════════════════
The first version of this check asked whether the row had ANY posted market.
That is the wrong question. A game can be quoted for a total and not a spread
— common on NCAAB — and in that game a total pick is perfectly actionable
while an ATS pick is not. Gating on the game would refuse a good total; gating
on the market refuses only what cannot be bet.

══ FAILS OPEN ON AN UNKNOWN SPORT OR MARKET ════════════════════════════════
An unmapped sport or market returns publishable, deliberately. A gate must
never be the reason a live sport goes dark
([[feedback_weather_pullers_silent_noop]]), and this one is additive to
existing discipline rather than the only thing standing between a bad pick and
a user.
"""
from __future__ import annotations

#: market code -> the ctx columns that, if any is populated, mean the books
#: posted that market. NHL quotes its spread as `close_puckline`; several
#: tables carry both spellings, so both are listed and either will do.
_MARKET_COLS = {
    'spread': ('close_spread', 'close_puckline'),
    'rl': ('close_spread', 'close_puckline'),
    'ats': ('close_spread', 'close_puckline'),
    'puckline': ('close_puckline', 'close_spread'),
    'runline': ('close_spread',),
    'total': ('close_total',),
    'ou': ('close_total',),
    'over_under': ('close_total',),
    'ml': ('close_home_ml', 'close_away_ml', 'home_ml_close', 'away_ml_close',
           'home_ml_odds', 'away_ml_odds'),
    'moneyline': ('close_home_ml', 'close_away_ml', 'home_ml_close',
                  'away_ml_close', 'home_ml_odds', 'away_ml_odds'),
}

#: markets whose pick must also carry its own number on the pick object. A
#: moneyline has no line — only a price — so it is deliberately absent.
_NEEDS_LINE = {'spread', 'rl', 'ats', 'puckline', 'runline',
               'total', 'ou', 'over_under'}


#: a column the caller never SELECTED is not a column that is null. This
#: distinction is the whole safety property of this module -- see
#: market_is_posted.
_ABSENT = object()


def _probe(row: dict, cols):
    """(any_populated, any_present_in_row).

    `any_present_in_row` is False when NONE of the columns appear as keys at
    all, which means the caller did not ask for them -- a different fact from
    the books not posting the market.
    """
    row = row or {}
    seen = False
    for c in cols:
        v = row.get(c, _ABSENT)
        if v is _ABSENT:
            continue
        seen = True
        if v is not None:
            return True, True
    return False, seen


def market_is_posted(ctx_row: dict, market) -> tuple[bool, str]:
    """Did the books post `market` on this game? (ok, reason).

    Reads the context row's own close_* columns, which is where the odds pull
    writes what the books had. True for an unmapped market.

    ══ 2026-11-05 · AN ABSENT COLUMN IS NOT AN ABSENT MARKET ══════════
    The first version used `row.get(c) is not None`, which cannot tell a
    column that is NULL from one the caller never selected. Dry-running the
    gate inside ncaab_resolve_primary_play refused 19 of 43 games, including
    `Towson @ Utah` -- whose row in fact carries close_home_ml, close_away_ml,
    close_spread AND close_total, all populated. The resolver's own
    `_load_games` selects `close_spread,close_total` and no moneyline columns,
    so every ML pick arrived with the keys simply missing and the gate read
    that as "the books do not carry it".

    Three of those games were on tonight's Sweat Card, priced from
    line_history (`East Texas A&M ML +145`, `McNeese ML -140`,
    `Vermont ML -125`). Shipping that version would have darkened nineteen
    games on the strength of a `.get()` against a column nobody asked for
    ([[feedback_dict_get_on_a_guessed_column]],
    [[feedback_explicit_select_silent_blanks]]).

    So: refuse ONLY when the columns are present on the row and all null.
    When they are absent, say so and pass -- a gate must never be the reason
    a live sport goes dark, and "I was not told" is not evidence.
    """
    m = str(market or '').lower()
    cols = _MARKET_COLS.get(m)
    if cols is None:
        return True, 'market %r not mapped — not gated' % m
    populated, present = _probe(ctx_row, cols)
    if populated:
        return True, 'ok'
    if not present:
        return True, ('cannot tell: none of %s were selected on this row — '
                      'passing rather than guessing' % '/'.join(cols))
    return False, ('no posted %s on this game (%s present and all null) — '
                   'the books do not carry it' % (m, '/'.join(cols)))


def pick_is_actionable(pick: dict, ctx_row: dict) -> tuple[bool, str]:
    """Can a subscriber actually place this pick? (ok, reason).

    Two independent ways to fail, and the distinction is what tells a repair
    apart from a gate:

      * the MARKET is not posted  -> nothing to repair from, refuse the pick
      * the market IS posted but the PICK carries no number -> repairable
        from the row, and repair_spread_labels.py does exactly that

    The reason names which, so a caller can log the difference instead of
    treating a late odds pull like an unquoted game.
    """
    if not isinstance(pick, dict) or not pick:
        return True, 'no pick to check'
    m = str(pick.get('type') or pick.get('market') or '').lower()
    ok, why = market_is_posted(ctx_row, m)
    if not ok:
        return False, 'UNQUOTED: %s' % why
    if m in _NEEDS_LINE and pick.get('line') is None:
        return False, ('NO_LINE: %s is posted on this game but the pick '
                       'carries line=None — repairable from the row, see '
                       'repair_spread_labels.py' % m)
    return True, 'ok'


def screen(picks_with_ctx) -> tuple[list, list]:
    """(keep, refused) over an iterable of (pick, ctx_row, tag) triples.

    `refused` entries are (tag, reason) so the caller can print a named
    reason per drop rather than a count — a silent gate is how a surface
    goes empty without anyone knowing why.
    """
    keep, refused = [], []
    for pick, ctx, tag in picks_with_ctx or ():
        ok, why = pick_is_actionable(pick, ctx)
        if ok:
            keep.append((pick, ctx, tag))
        else:
            refused.append((tag, why))
    return keep, refused
