"""One place that answers "what price was this pick available at?"

WHY THIS EXISTS (2026-10-06)
----------------------------
2,523 of 17,932 published receipts (14.1%) carry no price, concentrated in the
surfaces users actually read:

    dawg          130/130   100.0%
    daily_degen   156/156   100.0%
    sweat_card    827/1030   80.3%
    game_read    1014/1340   75.7%
    potd          100/146    68.5%
    sharp_card     31/602      5.1%
    prop_jerry    265/14295    1.9%

The obvious reading is "we don't capture prices". That is wrong, and the wrong
diagnosis would have led to a migration adding price columns. We capture
**629,388 line_history rows, 100% of them priced**, across every sport and
every market, per book, every ~15 minutes:

    MLB    ml 16,474   spread 15,586   total 16,444
    NFL    ml 44,706   spread 49,644   total 49,728
    NCAAF  ml 95,458   spread 102,572  total 102,858
    NHL    ml 27,804   spread 23,768   total 27,654
    NBA    ml 15,946   spread 20,372   total 20,374

The price was never missing. Nothing ever joined it to the pick.

WHY EVERY WRITER GOT IT WRONG
Each surface reached for a different field and got None when it guessed wrong,
silently:

    sharp_card_rows   it.get('odds')              -> works, 5% miss
    sweat_card_rows   it.get('odds')              -> card items carry no 'odds'
    potd_rows         rec.get('odds_american')    -> column exists, never written
    dawg_rows         (no pick_odds field at all) -> daily_dawg has NO price column

And the context tables disagree on the column name for the same quantity, so
even a writer that tried the context would guess wrong:

    MLB    home_ml_close / away_ml_close      90.5%
    NFL    close_home_ml / close_away_ml      99.7%   (home_ml_close is EMPTY)
    NCAAF  close_home_ml / close_away_ml      89.1%
    NHL    BOTH variants exist, 41.4% / 38.6%
    NBA    home_ml_close / away_ml_close      22.2%

I made exactly that mistake while diagnosing this: read NFL ml from
`home_ml_odds` (NULL), concluded NFL had no prices at all. It has 99.7%.

Worse, spread and total prices have NO column in any context table except NHL,
which is why a totals pick cannot be priced even when an ml pick can — and why
"Under 6.0" shipped on tonight's Sweat Card with no price.

So: one resolver, over line_history, which is the only source that has all
markets for all sports. Everything that publishes a pick calls it.

FAILS CLOSED
Never returns an invented number. When it cannot find the price it returns
price=None plus a `reason`, and the caller publishes without a price exactly as
it does today — no worse than the status quo, and the reason is recorded so the
gap is measurable instead of invisible.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_SB = os.environ.get('SUPABASE_URL')
_KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        or os.environ.get('SUPABASE_KEY'))
_H = {'apikey': _KEY, 'Authorization': f'Bearer {_KEY}'}

# Hard Rock is the contractual primary book, so it is the price we quote when
# it has one. The rest are fallbacks in rough order of how widely a user could
# actually get that number. A median across books would read better on paper
# and be unbettable in practice.
BOOK_PREFERENCE = (
    'hardrockbet', 'draftkings', 'fanduel', 'betmgm', 'espnbet',
    'caesars', 'williamhill_us', 'betrivers', 'fanatics', 'bovada',
)

# our pick vocabulary -> line_history.market
_MARKET = {
    'ml': 'ml', 'moneyline': 'ml', 'h2h': 'ml',
    'rl': 'spread', 'spread': 'spread', 'spreads': 'spread',
    'runline': 'spread', 'puckline': 'spread', 'dotd': 'spread',
    'total': 'total', 'totals': 'total', 'over/under': 'total',
}
# our side vocabulary -> line_history.side
_SIDE = {
    'home': 'home', 'away': 'away',
    'over': 'over', 'under': 'under',
}


def norm_market(raw) -> str | None:
    return _MARKET.get(str(raw or '').strip().lower())


def norm_side(raw) -> str | None:
    return _SIDE.get(str(raw or '').strip().lower())


def _rows(game_id: str, market: str, side: str) -> list | None:
    """Every priced capture for one (game, market, side). None on failure."""
    out, off = [], 0
    while True:
        try:
            r = requests.get(
                f'{_SB}/rest/v1/line_history', headers=_H, timeout=60,
                params={'select': 'line,price,book,captured_at',
                        'game_id': f'eq.{game_id}',
                        'market': f'eq.{market}',
                        'side': f'eq.{side}',
                        'price': 'not.is.null',
                        'order': 'captured_at.desc',
                        'limit': '1000', 'offset': str(off)})
        except requests.RequestException:
            return None
        if r.status_code not in (200, 206):
            return None
        chunk = r.json()
        if not isinstance(chunk, list):
            return None
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000
        if off >= 6000:          # one game/market/side cannot need more
            return out


def _pick_book(cands: list) -> dict:
    """Preferred book first; otherwise whatever we have, newest."""
    for b in BOOK_PREFERENCE:
        for c in cands:
            if str(c.get('book') or '').lower() == b:
                return c
    return cands[0]


def resolve(game_id, market, side, line=None, as_of=None,
            tolerance: float = 0.0) -> dict:
    """The price this pick was available at.

    game_id   must be the SAME id line_history uses — verified to join
              directly for MLB/NFL/NCAAF/NHL/NBA context rows.
    line      required for spread/total to avoid pricing a different line.
              `tolerance` allows a half-point of drift; 0.0 means exact.
    as_of     ISO timestamp. Only captures at or before it are considered, so
              a pick is never priced at a number that appeared after it was
              made. Omit to take the latest capture.

    Returns {'price', 'book', 'line', 'captured_at', 'reason'}; price is None
    whenever it could not be established and `reason` says why.
    """
    mk, sd = norm_market(market), norm_side(side)
    miss = {'price': None, 'book': None, 'line': None, 'captured_at': None}
    if not game_id:
        return {**miss, 'reason': 'no game_id'}
    if not mk:
        return {**miss, 'reason': f'market {market!r} not priceable here'}
    if not sd:
        return {**miss, 'reason': f'side {side!r} not recognised'}

    rows = _rows(str(game_id), mk, sd)
    if rows is None:
        return {**miss, 'reason': 'line_history lookup failed'}
    if not rows:
        return {**miss, 'reason': 'no priced capture for this game/market/side'}

    if as_of:
        at = str(as_of)
        eligible = [r for r in rows if str(r.get('captured_at') or '') <= at]
        if not eligible:
            # Every capture is newer than the pick. Using one would price the
            # pick at a number that did not exist yet, so refuse.
            return {**miss, 'reason': f'no capture at or before {at}'}
        rows = eligible

    if mk in ('spread', 'total'):
        if line is None:
            return {**miss, 'reason': f'{mk} needs a line to price'}
        try:
            want = float(line)
        except (TypeError, ValueError):
            return {**miss, 'reason': f'line {line!r} not numeric'}
        exact = [r for r in rows
                 if r.get('line') is not None
                 and abs(float(r['line']) - want) <= tolerance]
        if not exact:
            offered = sorted({float(r['line']) for r in rows
                              if r.get('line') is not None})
            return {**miss,
                    'reason': f'line {want} not offered '
                              f'(books had {offered[:6]})'}
        rows = exact

    newest = max(str(r.get('captured_at') or '') for r in rows)
    best = _pick_book([r for r in rows
                       if str(r.get('captured_at') or '') == newest])
    return {'price': best.get('price'), 'book': best.get('book'),
            'line': best.get('line'), 'captured_at': best.get('captured_at'),
            'reason': None}


def closing(game_id, market, side, line=None, commence_time=None,
            tolerance: float = 0.0) -> dict:
    """The LAST price before the game started — the true closing number.

    This is what B32 has been missing. `close_price_american` is empty on all
    1,641 jerry_reads and close_over_odds on 71 of 36,141 graded props, so CLV
    has never been computable. line_history captures every ~15 minutes and
    keeps `captured_at`, so for any game it already holds the close; it just
    has to be asked for.

    Without `commence_time` this degrades to the newest capture, which is the
    close only once the game has started.
    """
    return resolve(game_id, market, side, line=line, as_of=commence_time,
                   tolerance=tolerance)


def best_available(game_id, market, side, line=None, as_of=None,
                   tolerance: float = 0.0) -> dict:
    """The best price ANY tracked book showed at that moment.

    R2 measured 380 of 408 reads (93%) recording a worse price than one we had
    already observed, median give-up 1.11pp of implied probability — against a
    PRIME shortfall of only 2.1pp. Quoting this next to the taken price is how
    that stops being invisible.
    """
    mk, sd = norm_market(market), norm_side(side)
    miss = {'price': None, 'book': None, 'line': None, 'captured_at': None}
    if not mk or not sd or not game_id:
        return {**miss, 'reason': 'not priceable'}
    rows = _rows(str(game_id), mk, sd)
    if not rows:
        return {**miss, 'reason': 'no priced capture'}
    if as_of:
        rows = [r for r in rows if str(r.get('captured_at') or '') <= str(as_of)]
        if not rows:
            return {**miss, 'reason': 'no capture at or before as_of'}
    if mk in ('spread', 'total'):
        if line is None:
            return {**miss, 'reason': f'{mk} needs a line'}
        try:
            want = float(line)
        except (TypeError, ValueError):
            return {**miss, 'reason': 'line not numeric'}
        rows = [r for r in rows if r.get('line') is not None
                and abs(float(r['line']) - want) <= tolerance]
        if not rows:
            return {**miss, 'reason': f'line {want} not offered'}
    # Higher american odds always pays more on a winning bet, for favourites
    # and dogs alike (-105 beats -120; +140 beats +130), so max() is the best
    # price with no sign special-casing.
    best = max(rows, key=lambda r: float(r['price']))
    return {'price': best.get('price'), 'book': best.get('book'),
            'line': best.get('line'), 'captured_at': best.get('captured_at'),
            'reason': None}
