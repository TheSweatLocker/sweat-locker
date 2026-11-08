"""Price every published pick, every sport, so ROI and CLV are computable.

2026-09-23. Andy: "this should be a thing for every sport every matchup."

A pick carrying a line and no price has no ROI. You can say whether it
won; you cannot say what it returned, and those stop being the same
question as soon as juice ranges from -102 to -125. On the day this was
written, v_unpriced_published_picks held 507 rows.

The prices were never missing. line_history carries them for every sport
we run — MLB 89,930 rows, NCAAF 10,746, NFL 5,798, NHL 3,942, NBA 2,430 —
each with market, side, line and price across a dozen books. What was
missing was a join and somewhere to put the result.

The join turns out to be nearly free. line_history keys game_id exactly
as the context table does for MLB (md5), NCAAF (ncaaf_YYYYMMDD_A_H), NBA
(ESPN event id) and NHL (NHL game id). Only NFL disagrees: line_history
uses 20261005_ATL_NO while nfl_game_context stores an md5, so that one
key is rebuilt from (game_date, away, home) via nfl_team_aliases.

Two rules that are not negotiable, both because a wrong price is worse
than a blank one:

  * a pick is priced ONLY by books quoting its exact line. PIT +3.5 is
    not priced off a book offering +3.0 — different bet, different
    probability. Unmatched lines are reported, never substituted.
  * consensus is computed in probability space. American odds are
    discontinuous across zero, so a plain median over a mixed-sign book
    list is meaningless; it once returned -1 for a pick'em spread.

Dry by default.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
for _l in (open(os.path.join(_HERE, '.env'), encoding='utf-8')
              if os.path.exists(os.path.join(_HERE, '.env')) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

CONTEXT = {
    'MLB': 'mlb_game_context',   'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context', 'NBA': 'nba_game_context',
    'NHL': 'nhl_game_context',   'NCAAB': 'ncaab_game_context',
}

# call_market vocabulary -> line_history market vocabulary. MLB's run
# line and NHL's puck line are both spreads to the books.
MARKET = {'ml': 'ml', 'rl': 'spread', 'spread': 'spread',
          'puckline': 'spread', 'total': 'total'}


def page(table: str, **params) -> list[dict]:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params={'limit': 1000, 'offset': off, **params})
        b = r.json()
        if not isinstance(b, list):
            print(f'  !! {table}: {str(b)[:220]}')
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def american_to_decimal(p) -> float:
    p = float(p)
    return 1 + (p / 100 if p > 0 else 100 / abs(p))


def decimal_to_american(d: float) -> int:
    return int(round((d - 1) * 100)) if d >= 2 else int(round(-100 / (d - 1)))


_NFL_ALIAS: dict | None = None


def nfl_key(game_date, away, home) -> str | None:
    """Rebuild line_history's NFL key: 20261005_ATL_NO.

    nfl_game_context stores codes already, but feeds disagree on a
    handful (LAR/LA, WSH/WAS, JAC/JAX), so canonicalise through
    nfl_team_aliases rather than trusting the string.
    """
    global _NFL_ALIAS
    if _NFL_ALIAS is None:
        _NFL_ALIAS = {}
        for a in page('nfl_team_aliases', select='canonical_name,full_name,alt_names,city'):
            canon = a['canonical_name']
            for n in filter(None, [canon, a.get('full_name'), a.get('city')]
                            + list(a.get('alt_names') or [])):
                _NFL_ALIAS[str(n).upper()] = canon
    a, h = _NFL_ALIAS.get(str(away).upper()), _NFL_ALIAS.get(str(home).upper())
    if not a or not h:
        return None
    return f"{str(game_date)[:10].replace('-', '')}_{a}_{h}"


def ncaab_key(game_date, away, home) -> str | None:
    """Rebuild line_history's NCAAB key: ncaab_20261106_Connecticut_Michigan.

    == 2026-11-06 · WHY NCAAB WAS NEVER PRICED, ON ANY DATE ================
    `jerry_reads.game_id` for NCAAB is the Odds-API 32-char hex
    (013d1268942b6898eb89ee0a7d5c8d49, 56 of 56 rows on 11-06), while
    `line_history.game_id` is this slug. Two namespaces that cannot join,
    and only NFL had a key translation — so price_one reported "no quotes
    for this game" for every NCAAB read and `price_american` was null on
    0 of 126 reads across 11-05, 11-06 and 11-07. An unpriced read cannot
    produce a priced receipt, cannot be graded for ROI or CLV, and cannot
    qualify for the Dawg, which selects on price.

    NO ALIAS LOOKUP, deliberately, unlike nfl_key. Measured over the live
    board: of 56 distinct NCAAB game_ids in line_history, **56** are
    reproduced exactly by (game_date, away_team, home_team) from
    ncaab_game_context with nothing but space -> underscore. The 34
    context rows that did NOT match have no line_history row under ANY
    name (probed Troy, Northern_Colorado, Cal_Baptist, Arkansas_St,
    Colorado_San_Diego, New_Mexico_St -> 0 rows each) — they are games
    with no posted lines, not name failures. So a mascot strip or alias
    pass would add exactly zero matches while risking naming a different
    school, which is the documented NCAAB resolver trap
    (feedback_prefix_strip_can_name_another_school). If a future miss IS
    a name problem, route it through ncaab_team_resolver rather than
    hand-rolling a strip here (feedback_a_blocklist_duplicating_a_lookup_table).

    The date needs no offset: all 56 matched on ncaab_game_context's own
    game_date, 0 at +/-1 day, so the ET/UTC split that caused R166 does
    not reach this key.
    """
    if not away or not home or not game_date:
        return None
    return ('ncaab_%s_%s_%s' % (str(game_date)[:10].replace('-', ''),
                                away, home)).replace(' ', '_')


#: Pages of 1,000 captures to walk per sport, newest first. An NCAAF Saturday
#: is ~44 games x 3 markets x 2 sides x ~10 books = 2,640 rows per capture
#: cycle, so 40 pages covers roughly 15 cycles — enough to see every game on
#: the board, and still only 40 cheap keyset requests instead of 700 deep
#: offsets.
QUOTE_SCAN_PAGES = 40


def quote_index(sport: str, lo: str) -> dict:
    """Latest quote per (game_id, market, side, line), newest wins.

    ══ 2026-10-23 · THIS HUNG, WHICH IS WHY PRICING WAS DEGRADING ══
    price_picks never completed. Run against a single sport it exits 124 on a
    115s timeout with zero output, which is why jerry_reads pricing fell from
    100% in early October to 55% on 10-23, 21% on the 10-24 slate and 0% on
    10-27/28/31, and why an unpriced pick cannot be graded for ROI or CLV.

    The cause was here. It paged ALL of line_history for the sport with
    commence_time >= lo, and lo defaults to 14 days back:

        NFL     702,512 rows
        NCAAF   the COUNT ITSELF times out (57014) — over 600k
        NBA     639,202 rows
        MLB      70,544 rows

    At 1,000 per request that is 700+ requests per sport with offsets reaching
    700,000, and a deep offset makes Postgres walk every skipped row. Across
    six sports it is thousands of requests and millions of row-walks — the
    exact shape that took the app down on 10-21. So this being unscheduled was
    protective, not an oversight: scheduling it as written would have been
    dangerous.

    What the function actually needs is the CURRENT board, not history. So it
    takes ONE page of the most recent captures, ordered captured_at DESC, with
    no offset walking at all. That also fixes a correctness bug: the old query
    had no `order`, so "newest wins" depended on whatever order PostgREST
    happened to return (feedback_unordered_data_zero_picks_a_vintage).

    `lo` is still honoured as a floor so a backfill cannot reach past its
    window, but it no longer drives the volume.
    """
    # Keyset pagination on captured_at, newest first. NOT offset: the
    # original walked offsets to 700,000 and hung. And NOT a single flat page
    # either — the first attempt at this capped one page at 8,000 rows, and
    # NCAAF captures 136,658 rows in 24h, so which games landed inside the cap
    # was luck. Two identical runs priced 26 and then 12 NCAAF reads, which is
    # how that showed up. A cursor keeps every page cheap at any depth while
    # still walking far enough to cover the whole board.
    rows, cursor, pages = [], None, 0
    while pages < QUOTE_SCAN_PAGES:
        params = {'sport': f'eq.{sport}',
                  'select': 'game_id,market,book,side,line,price,'
                            'captured_at,commence_time',
                  'commence_time': f'gte.{lo}',
                  'order': 'captured_at.desc',
                  'limit': 1000}
        if cursor:
            params['captured_at'] = f'lt.{cursor}'
        r = requests.get(f'{SB}/rest/v1/line_history', headers=H, timeout=120,
                         params=params)
        if r.status_code != 200:
            print(f'  !! line_history {sport}: HTTP {r.status_code} '
                  f'{r.text[:180]}')
            break
        b = r.json()
        if not isinstance(b, list):
            print(f'  !! line_history {sport}: {str(b)[:180]}')
            break
        if not b:
            break
        rows += b
        pages += 1
        nxt = str(b[-1].get('captured_at'))
        if nxt == cursor:           # all ties — cursor cannot advance
            break
        cursor = nxt
        if len(b) < 1000:
            break
    if pages >= QUOTE_SCAN_PAGES:
        print(f'  · {sport}: quote scan stopped at {QUOTE_SCAN_PAGES} pages '
              f'({len(rows)} rows) — newest captures are in hand, older were '
              f'not read')
    latest = {}
    for x in rows:
        if x.get('price') is None:
            continue
        k = (x['game_id'], x.get('market'), x.get('side'), x.get('line'), x.get('book'))
        if k not in latest or str(x['captured_at']) > str(latest[k]['captured_at']):
            latest[k] = x
    grouped = defaultdict(list)
    for (gid, mk, sd, ln, _b), x in latest.items():
        grouped[(gid, mk, sd, ln)].append(x)
    return grouped


def price_one(grouped: dict, key: str, market: str, side: str, line, text: str) -> dict:
    m = MARKET.get(str(market).lower())
    if not m or not key:
        return {'price': None, 'why': f'market {market!r} not priceable'}
    if m == 'total':
        want = 'over' if 'over' in str(text or side).lower() else 'under'
    else:
        want = 'home' if str(side).upper() == 'HOME' else 'away'

    cands = []
    for (gid, mk, sd, ln), rows in grouped.items():
        if gid != key or mk != m or sd != want:
            continue
        if m == 'ml':
            cands += rows
        elif line is not None and ln is not None and abs(float(ln) - float(line)) < 1e-6:
            cands += rows
    if not cands:
        other = sorted({ln for (gid, mk, sd, ln) in grouped
                        if gid == key and mk == m and sd == want and ln is not None})
        return {'price': None,
                'why': (f'no book at line {line}' if other else 'no quotes for this game'),
                'other_lines': other}
    prices = [float(c['price']) for c in cands]
    med = median(american_to_decimal(p) for p in prices)
    best = max(prices, key=american_to_decimal)
    return {
        'price': decimal_to_american(med),
        'implied': round(1 / med, 4),
        'best': int(best),
        'best_book': next(c['book'] for c in cands if float(c['price']) == best),
        'n_books': len({c['book'] for c in cands}),
    }


#: A spread, run line, puck line or total is a JUICE market - every book
#: prices it inside roughly +/-300. A moneyline is not. So a price past that
#: band on a side/total pick cannot have been computed against that market.
_JUICE_MARKETS = {'rl', 'spread', 'ats', 'puckline', 'total'}
_JUICE_LIMIT = 300.0


def price_impossible(market, price) -> bool:
    """True when the stored price cannot belong to the stored market.

    == 2026-11-06 . THE 10-27 INVALIDATION ONLY RAN IN ONE WRITER =========
    sync_jerry_reads_from_ctx.py nulls the price when IT changes a read's
    market, and that fixed the sports it touches -- measured today, MLB 0,
    NCAAF 0, NFL 0 impossible prices out of 403 side/total reads.

    But a pick's market also moves OUTSIDE that writer. On 11-06 the new
    NCAAB ml -> COVERAGE ceiling pushed picks off the moneyline onto the
    spread, and NCAAB's sync skips every read whose long_read is real prose
    (all of them), so nothing invalidated the price:

        15 of 42 NCAAB side reads (35.7%) carried a moneyline price
        Kansas -28.5 at -20000 · Iowa St. -30.5 at -18182
        Purdue -19.5 at -4500, while line_history holds spread/home/-19.5
        at -110 from betmgm, fanduel, espnbet and draftkings

    Telling a subscriber a 28-point spread is available at -20000 is worse
    than showing nothing, and every ROI and breakeven on those rows is
    wrong. So the test moves to the CHOKEPOINT every writer passes
    (feedback_guard_belongs_at_the_chokepoint): price_picks decides what a
    price is, so price_picks decides when one is impossible.
    """
    if str(market or '').lower() not in _JUICE_MARKETS:
        return False
    try:
        v = float(price)
    except (TypeError, ValueError):
        return False
    return v < -_JUICE_LIMIT or v > _JUICE_LIMIT


#: A side market has a side, so it has a moneyline to be confused with.
#: A total does not, which is why it is absent here.
_SIDE_MARKETS = {'rl', 'spread', 'ats', 'pl', 'puckline'}

#: Per-side moneyline column, in preference order. The context tables do
#: not agree on the spelling - see the _ML_KEYS note in model_edge.py and
#: the 2026-11-07 fill scan, which measured NFL on close_home_ml 163/164
#: while NBA carries only home_ml_close.
_CTX_ML_KEYS = {
    'HOME': ('close_home_ml', 'home_ml_close', 'home_ml_odds', 'home_ml'),
    'AWAY': ('close_away_ml', 'away_ml_close', 'away_ml_odds', 'away_ml'),
}

#: Below this the test is not safe. A spread genuinely lives near -110, so
#: a -110 side price sitting on a -110 moneyline is a coincidence, and one
#: such row exists (NCAAF 10-14 Sam Houston +1.5). No book prices a side
#: identically to a moneyline of -150 or worse by chance.
_ML_IDENTITY_MIN_JUICE = 150.0


def _ctx_ml(ctx_row, side):
    """That side's moneyline off a context row, whatever it is called."""
    if not isinstance(ctx_row, dict):
        return None
    for k in _CTX_ML_KEYS.get(str(side or '').upper(), ()):
        v = ctx_row.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def price_is_the_moneyline(market, price, side, ctx_row):
    """Reason string when a SIDE price is actually that side's MONEYLINE.

    == 2026-11-07 . THE +/-300 BAND IS A MAGNITUDE TEST, NOT A MARKET ONE ==
    price_impossible() catches a side price outside +/-300, which found the
    NCAAB -20000 spreads. It cannot catch a moneyline that happens to sit
    INSIDE the band, and that is the common case in hockey. Measured over
    316 favourite side reads since 09-01, 12 carry a price byte-identical
    to that side's own moneyline:

        NHL 10-03 Minnesota Wild -1.5        -205 == ml -205   11 books
        NHL 10-11 New York Rangers -1.5      -275 == ml -275   11 books
        NHL 10-15 Tampa Bay Lightning -1.5   -275 == ml -275   11 books
        NHL 10-15 Florida Panthers -1.5      -295 == ml -295   11 books
        NHL 10-20 Carolina Hurricanes -1.5   -300 == ml -300   11 books
        NBA 10-29 New Orleans Pelicans -5.5  -238 == ml -238   11 books
        ... 11 NHL on/before 10-22, plus the NBA row on 10-29

    A -1.5 lay is strictly harder than winning the game, so its price must
    be LONGER than the moneyline - every one of these is ~20pp of implied
    probability wrong, and it reached public_receipts on both the game_read
    and the Sharp. The 10-22 NHL line-column fix stopped most of it; the
    NBA row is 10-29, so this is neither closed history nor one sport.

    WHY ONLY EXACT EQUALITY. The structural rule I measured first - a
    favourite's side price must be LONGER than its own moneyline - flags 21
    rows, and I verified it has false positives: NHL 10-31 Detroit Red
    Wings +1.5 at -274 against a -110 moneyline is a legitimate price for
    TAKING 1.5 goals, and call_line cannot be used to tell laying from
    taking because its sign is unreliable (NCAAF 11-07 'Boise State -17.5'
    is stored as call_line +17.5). Byte equality at >= 150 juice has no
    false positives in the measured set, so that is what ships. The wider
    rule is written up rather than enforced.
    """
    if str(market or '').lower() not in _SIDE_MARKETS:
        return None
    m = _ctx_ml(ctx_row, side)
    if m is None or m >= 0 or abs(m) < _ML_IDENTITY_MIN_JUICE:
        return None
    try:
        v = float(price)
    except (TypeError, ValueError):
        return None
    if abs(v - m) >= 0.5:
        return None
    return (f'{v:+g} is exactly the {str(side).lower()} MONEYLINE '
            f'({m:+g}) - a side bet cannot be priced at the moneyline')


#: THE SIDE PRICE WAS ALWAYS ON THE ROW, IN ITS OWN COLUMN (2026-11-07).
#:
#: Two context tables carry a dedicated price for the side market, and
#: price_picks never read either of them:
#:
#:     NHL  close_puckline_home_price / _away_price   239 of 290 since 10-01
#:     NBA  close_spread_home_price   / _away_price   136 of 213 since 10-01
#:
#: NCAAB, NFL, NCAAF and MLB have no such column and are priced from
#: line_history, which works for them. NHL is the opposite case: it has
#: ZERO line_history rows under its own game_id namespace (checked on all
#: 11 affected games), so price_one could never price an NHL side bet -
#: that is the 61 unmatched NHL reads in this step's own output.
#:
#: This is what made the moneyline-on-a-side-bet rows possible. On
#: 10-15 Tampa Bay Lightning -1.5 the row held close_puckline_home_price
#: -108 while the read published -275, the HOME MONEYLINE. The Sharp was
#: reading this column and the read was not, which is why one play went out
#: at two prices; on 10-11 Washington Capitals -1.5 the Sharp's +120 and
#: this column's +120 agree exactly.
#:
#: Correcting all 12 measured rows from these columns moves them 17.6 to
#: 23.6pp of implied probability, e.g. Dallas Stars -1.5 from -200 to +132.
_CTX_SIDE_PRICE = {
    'NHL': {'HOME': 'close_puckline_home_price',
            'AWAY': 'close_puckline_away_price',
            'home_line': 'close_puckline_home'},
    'NBA': {'HOME': 'close_spread_home_price',
            'AWAY': 'close_spread_away_price',
            'home_line': 'close_spread'},
}


def _ctx_side_columns(sport: str) -> list[str]:
    cfg = _CTX_SIDE_PRICE.get(str(sport or '').upper())
    return [cfg['HOME'], cfg['AWAY'], cfg['home_line']] if cfg else []


def _ctx_side_quote(sport, market, side, call_line, ctx_row) -> dict:
    """Price a side bet from the context row's own side-price column.

    The stored LINE must match the line we are pricing. A price belongs to
    a specific (market, side, line) - that is the whole reason this step
    re-prices a moved pick at all - so a -1.5 price must not be attached to
    a -2.5 call. Both tables store the line home-perspective, so the away
    line is its negation.
    """
    cfg = _CTX_SIDE_PRICE.get(str(sport or '').upper())
    if not cfg or str(market or '').lower() not in _SIDE_MARKETS:
        return {'price': None, 'why': 'no ctx side-price column for this sport'}
    if not isinstance(ctx_row, dict):
        return {'price': None, 'why': 'no ctx row'}
    sd = str(side or '').upper()
    if sd not in ('HOME', 'AWAY'):
        return {'price': None, 'why': 'ctx side price needs a HOME/AWAY side'}
    try:
        home_line = float(ctx_row.get(cfg['home_line']))
    except (TypeError, ValueError):
        return {'price': None, 'why': 'ctx holds no line for this game'}
    want = home_line if sd == 'HOME' else -home_line
    if call_line is not None:
        try:
            if abs(float(call_line) - want) > 0.01:
                return {'price': None,
                        'why': f'ctx line {want:+g} is not the called '
                               f'{float(call_line):+g}'}
        except (TypeError, ValueError):
            return {'price': None, 'why': 'call_line is not a number'}
    v = ctx_row.get(cfg[sd])
    if v is None:
        return {'price': None, 'why': f'{cfg[sd]} is null on this game'}
    try:
        px = int(float(v))
    except (TypeError, ValueError):
        return {'price': None, 'why': f'{cfg[sd]} is not a number'}
    if not px:
        return {'price': None, 'why': f'{cfg[sd]} is zero'}
    # Same key contract as price_one(), because the patch assembly reads
    # q['n_books'] / q['implied'] straight out of it. One source, so
    # n_books is 1 and it IS the best book - and price_best_book names the
    # column so a later audit can tell a ctx-priced row from a book-priced
    # one without a separate stamp.
    return {'price': px, 'implied': round(1 / american_to_decimal(px), 4),
            'best': px, 'best_book': f'ctx:{cfg[sd]}', 'n_books': 1,
            'why': f'ctx {cfg[sd]}'}


def _probe_ml_columns(table: str) -> list[str]:
    """Which moneyline columns this table actually has.

    Probed one at a time on purpose. A single select naming an absent
    column 400s the WHOLE fetch and the caller reads zero rows, which is
    the failure this repo has now shipped twice
    (feedback_fallback_select_must_degrade_per_column).
    """
    keep = []
    _extra = [c for cfg in _CTX_SIDE_PRICE.values()
              for c in (cfg['HOME'], cfg['AWAY'], cfg['home_line'])]
    for cands in list(_CTX_ML_KEYS.values()) + [tuple(_extra)]:
        for c in cands:
            if c in keep:
                continue
            try:
                r = requests.get(f'{SB}/rest/v1/{table}',
                                 headers=H, timeout=15,
                                 params={'select': c, 'limit': '1'})
            except Exception:
                continue
            if r.status_code in (200, 206):
                keep.append(c)
    return keep


def _has_started(read: dict, ctx: dict) -> bool:
    """True when this game's scheduled start is already behind us.

    A started game's price is a receipt — it records what we published and
    must not be overwritten by a later market. An unstarted game's price is
    a live quote and has to be refreshed. Falls back to 'not started' when
    the kickoff is unknown, because re-pricing a forward game is harmless
    while freezing a wrong price is not.
    """
    c = ctx.get(read.get('game_id')) or {}
    for key in ('kickoff_utc', 'commence_time', 'game_time_utc'):
        raw = c.get(key)
        if raw:
            try:
                ts = str(raw).replace('Z', '+00:00')
                return datetime.fromisoformat(ts) <= datetime.now(timezone.utc)
            except (TypeError, ValueError):
                pass
    gd = str(c.get('game_date') or read.get('game_date') or '')[:10]
    if gd:
        # No clock available — only treat it as started once the date has
        # fully passed, so a same-day game is still re-priced.
        return gd < str(datetime.now(timezone.utc).date())
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default=None, help='game_date lower bound (default 14d back)')
    ap.add_argument('--sport', default=None, help='limit to one sport')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    lo = args.since or (datetime.now(timezone.utc).date().replace(
        day=1) if False else None)
    if not lo:
        from datetime import timedelta
        lo = str(datetime.now(timezone.utc).date() - timedelta(days=14))

    sports = [args.sport.upper()] if args.sport else list(CONTEXT)
    grand = defaultdict(int)
    for sport in sports:
        reads = page('jerry_reads', sport=f'eq.{sport}', select='*',
                     game_date=f'gte.{lo}')
        if not reads:
            continue
        # The moneyline columns are appended to the SAME narrow select
        # rather than switching to '*'. A '*' here would also hand
        # _has_started the kickoff columns it currently never receives,
        # which would start freezing prices this run re-prices today -
        # arguably a fix, but an unmeasured one, and not this change.
        _mlcols = _probe_ml_columns(CONTEXT[sport])
        _sel = ','.join(['game_id', 'game_date', 'away_team', 'home_team']
                        + _mlcols)
        ctx = {c['game_id']: c for c in page(CONTEXT[sport], select=_sel,
                                             game_date=f'gte.{lo}')}
        grouped = quote_index(sport, lo)
        ok = fail = skip = refused_ml = 0
        reasons = defaultdict(int)
        patches = []
        for r in reads:
            # 2026-09-24. This used to skip any row that already carried a
            # price, which quietly guaranteed a stale one. A price belongs
            # to a specific (market, side, line); when the pick moves, the
            # old price is not merely old, it is wrong.
            #
            # It happened the same day this was written. The juice-reroute
            # cover check flipped KC @ MIA and SEA @ WAS from a spread back
            # to a moneyline, and the reads kept the spread's price:
            #
            #     KC ML  showed -110   actual moneyline -650
            #     DET ML showed -115   actual moneyline -298
            #
            # Telling a subscriber they can have -650 at -110 is worse than
            # showing nothing. Forward games are re-priced every run — the
            # market moves, so the number has to. Only a game that has
            # already started keeps its price, because that one is a
            # historical record of what we published.
            # An IMPOSSIBLE price is never frozen. _has_started protects a
            # started game's price because that is a record of what we
            # published - but only when it could have been that market's
            # price in the first place. See price_impossible().
            _impossible = price_impossible(r.get('call_market'),
                                           r.get('price_american'))
            if _impossible:
                print(f'    [IMPOSSIBLE PRICE] {r.get("call_text")!r} '
                      f'({r.get("call_market")}) holds {r.get("price_american")} '
                      f'- not a {r.get("call_market")} price; clearing')
            # A moneyline INSIDE the +/-300 band is invisible to the test
            # above. See price_is_the_moneyline().
            if not _impossible:
                _wrongmkt = price_is_the_moneyline(
                    r.get('call_market'), r.get('price_american'),
                    r.get('call_side'), ctx.get(r.get('game_id')))
                if _wrongmkt:
                    print(f'    [MONEYLINE ON A SIDE BET] '
                          f'{r.get("call_text")!r} ({r.get("call_market")}): '
                          f'{_wrongmkt}; clearing')
                    reasons['moneyline_on_a_side_bet'] += 1
                    _impossible = True
            # ══ 2026-11-07 · A STORED JUICED ML MUST GO EVEN WITH NO QUOTE
            # The first version of this gate sat further down, after
            # price_one(). That missed `Wake Forest ML` at -50000 on today's
            # NCAAF board: Merrimack is FCS and the feed has no quotes for
            # the game ("no quotes for this game" x7), so price_one returns
            # None and the loop `continue`s BEFORE any gate -- leaving the
            # -50000 that was written on 11-04 standing untouched. The thing
            # we must act on is the price already ON the read, which is known
            # right here, next to the _impossible check and for the same
            # reason.
            _stored = r.get('price_american')
            if str(r.get('call_market') or '').lower() == 'ml' and _stored is not None:
                try:
                    _sv = float(_stored)
                except (TypeError, ValueError):
                    _sv = None
                # ONLY a game that has not started. A published read on a
                # game that already played is a receipt of what we told
                # subscribers, and demoting it rewrites history -- the dry
                # run of the first version of this block offered to pass on
                # `Michigan ML -800` and `UNLV ML -2000` from games that had
                # already been played and graded, because the _has_started
                # test sits BELOW this point in the loop
                # (feedback_grade_the_side_we_published).
                if (_sv is not None and _sv < -_JUICE_LIMIT
                        and not _has_started(r, ctx)):
                    print(f'    [ML JUICE] {r.get("call_text")!r} holds '
                          f'{_sv:g}, past -{_JUICE_LIMIT:g} - demoting the '
                          f'read to a pass')
                    patches.append((r['id'], {
                        'call_market': 'pass', 'call_side': None,
                        'call_line': None,
                        'call_text': 'No play - moneyline past our juice ceiling',
                        'price_american': None, 'price_implied': None,
                        'price_best': None, 'price_best_book': None,
                        'price_books_n': None, 'priced_at': None,
                    }))
                    refused_ml += 1
                    continue
            if (r.get('price_american') is not None and _has_started(r, ctx)
                    and not _impossible):
                skip += 1
                continue
            if not r.get('call_text') or not r.get('call_market'):
                skip += 1
                continue
            c = ctx.get(r['game_id'])
            key = r['game_id']
            if sport == 'NFL' and c:
                key = nfl_key(c['game_date'], c.get('away_team'), c.get('home_team')) or key
            elif sport == 'NCAAB' and c:
                key = ncaab_key(c['game_date'], c.get('away_team'),
                                c.get('home_team')) or key
            q = price_one(grouped, key, r.get('call_market'), r.get('call_side'),
                          r.get('call_line'), r.get('call_text'))
            if q.get('price') is None:
                # The row's own side-price column, for the two sports that
                # have one. See _ctx_side_quote(). This is the source that
                # was missing when a side bet ended up wearing the
                # moneyline.
                _cq = _ctx_side_quote(sport, r.get('call_market'),
                                      r.get('call_side'), r.get('call_line'), c)
                if _cq.get('price') is not None:
                    reasons['priced from ctx side column'] += 1
                    q = _cq
                else:
                    fail += 1
                    reasons[q.get('why', '?')] += 1
                    continue
            # ══ 2026-11-07 · THE PRICER IS THE FIRST THING THAT KNOWS ══
            # The ML juice gate added to sync_jerry_reads_from_ctx the same
            # day reads the CLOSING ML off the ctx row, and took NCAAF reads
            # advertising an ML worse than -300 from 14 of 51 to 1. The two
            # survivors on that board -- `Wake Forest ML` at -50000 and
            # `Edmonton Oilers ML` at -322 -- both sit on ctx rows whose
            # close_home_ml and close_away_ml are BOTH NULL. The resolver
            # chose the moneyline with no price in front of it, so no
            # ctx-side gate could ever have refused them; the price came from
            # here, out of the live book (fanatics, -50000).
            #
            # So this is the chokepoint for the market-priced half of the
            # same rule (feedback_guard_belongs_at_the_chokepoint). Refusing
            # to PRICE it is not enough on its own -- that leaves the read
            # still saying `Wake Forest ML` with no number, which is worse --
            # so the read is demoted to a pass, exactly as the ctx-side gate
            # does.
            #
            # Reversible by design: ctx.primary_play still says `ml`, so if
            # the price moves back inside the ceiling the next sync run
            # restores the call and this stops refusing it.
            _mkt = str(r.get('call_market') or '').lower()
            if _mkt == 'ml' and q['price'] is not None and float(q['price']) < -_JUICE_LIMIT:
                print(f'    [ML JUICE] {r.get("call_text")!r} prices at '
                      f'{q["price"]} , past -{_JUICE_LIMIT:g} - demoting the '
                      f'read to a pass instead of advertising it')
                patches.append((r['id'], {
                    'call_market': 'pass', 'call_side': None, 'call_line': None,
                    'call_text': 'No play - moneyline past our juice ceiling',
                    'price_american': None, 'price_implied': None,
                    'price_best': None, 'price_best_book': None,
                    'price_books_n': None, 'priced_at': None,
                }))
                refused_ml += 1
                continue
            ok += 1
            patches.append((r['id'], {
                'price_american': q['price'], 'price_best': q['best'],
                'price_best_book': q['best_book'], 'price_books_n': q['n_books'],
                'price_implied': q['implied'],
                'priced_at': datetime.now(timezone.utc).isoformat(),
            }))
        print(f'{sport:6s} reads={len(reads):4d}  priceable={ok:4d}  '
              f'unmatched={fail:4d}  already/skip={skip:4d}  '
              f'ml-juice-refused={refused_ml:3d}')
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:4]:
            print(f'          {n:4d}  {why}')
        grand['ok'] += ok; grand['fail'] += fail; grand['skip'] += skip

        if args.write and patches:
            done = 0
            for rid, body in patches:
                resp = requests.patch(f'{SB}/rest/v1/jerry_reads', headers=H_W,
                                      timeout=60, params={'id': f'eq.{rid}'}, json=body)
                if resp.status_code in (200, 204):
                    done += 1
                else:
                    print(f'          !! id={rid} HTTP {resp.status_code} {resp.text[:140]}')
                    break
            print(f'          wrote {done}/{len(patches)}')

    print(f"\ntotal priceable {grand['ok']}  unmatched {grand['fail']}  skipped {grand['skip']}")
    if not args.write:
        print('DRY RUN — re-run with --write to store.')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
