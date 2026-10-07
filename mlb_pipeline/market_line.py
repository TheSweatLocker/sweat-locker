"""The true market line for a game and side, from the books, at a point in time.

WHY THIS EXISTS (2026-10-07)
----------------------------
Every published spread number is derived from `<sport>_game_context.close_spread`
and then frozen into the pick label and the receipt. That column is wrong on
**15 of 270** NFL games (5.6%) — not drift, wrong:

    game            context   the books   nfl_game_results
    ARI@SF  09-27     +17.5        +7.5        +7.5
    IND@WAS 10-04     -16.5        -4.5        -4.5
    LAC@SEA 10-04     +10.5        +7.0        +7.0
    DEN@SF  10-04      -1.5        +3.0        +3.0
    MIN@TB  09-27      +1.5        -1.5        -1.5   <- sign

It shipped "NE -8.5" on a game that never traded outside -4.0..-3.0 across
1,064 captures, because the normalizer faithfully derived -8.5 from a
close_spread of +8.5 that the market never had.

`line_history` does not have this problem. It stores EVERY book's quote with
a price and a capture time — 192k NFL rows, 419k NCAAF — so it can answer
both "what is the line now" and "what was the line when we picked", which
`close_spread` cannot do at all.

SO: resolve the line from the books, by MEDIAN across them. Median not mean,
and median not first-book: the MLB path in game_context.py takes
`if mkt["key"] == "spreads" and not spread_line` — the first bookmaker in the
API response — so one stale or alternate quote becomes the number of record.
odds_pull_core._consensus already got this right; this is the same idea made
available to anything that needs to CHECK a line rather than write one.

SIGN CONVENTION, stated once because getting it wrong inverts everything.
Everything this module returns is the **home team's own handicap in
sportsbook terms**: negative means the home team is laying points.

    home_line = -3.5   ->  home is a 3.5-point favourite
    home_line = +3.5   ->  home is a 3.5-point underdog

That is `line_history.line` for `side='home'` as the books publish it, and it
is the OPPOSITE of the nflverse convention used by `nfl_game_context` and
`nfl_game_results` (where positive = home favoured). Callers reading those
columns must negate. NCAAF/MLB/NHL/NBA context tables already store the home
handicap, so they match this module directly.

NOTHING HERE WRITES. It reads line_history and answers questions.
"""
from __future__ import annotations

import os
import statistics
from pathlib import Path
from typing import Optional

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL', '')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY', ''))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: How far a published number may sit from the books before it is a defect
#: rather than ordinary shopping. Half-point splits between books are normal;
#: a full point is not, on a market we claim to have taken.
TOLERANCE_PTS = 1.0

#: Team-nickname -> the abbreviation the NFL context tables use. line_history
#: keys on a `matchup` string of full names ("Baltimore Ravens @ Atlanta
#: Falcons") and the context tables key on abbreviations, with NO shared id
#: between them, so a join needs this. NCAAF/MLB use near-full names in both
#: places and match on a prefix instead.
NFL_NICK = {
    'cardinals': 'ARI', 'falcons': 'ATL', 'ravens': 'BAL', 'bills': 'BUF',
    'panthers': 'CAR', 'bears': 'CHI', 'bengals': 'CIN', 'browns': 'CLE',
    'cowboys': 'DAL', 'broncos': 'DEN', 'lions': 'DET', 'packers': 'GB',
    'texans': 'HOU', 'colts': 'IND', 'jaguars': 'JAX', 'chiefs': 'KC',
    'raiders': 'LV', 'chargers': 'LAC', 'rams': 'LA', 'dolphins': 'MIA',
    'vikings': 'MIN', 'patriots': 'NE', 'saints': 'NO', 'giants': 'NYG',
    'jets': 'NYJ', 'eagles': 'PHI', 'steelers': 'PIT', '49ers': 'SF',
    'seahawks': 'SEA', 'buccaneers': 'TB', 'titans': 'TEN',
    'commanders': 'WAS',
}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _page(params, cap=120000):
    out, off = [], 0
    while off < cap:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/line_history', headers=H, params=q,
                         timeout=180)
        if r.status_code not in (200, 206):
            return out
        chunk = r.json()
        if not isinstance(chunk, list):
            return out
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000
    return out


def fetch_spreads(sport: str, since: str, until: Optional[str] = None):
    """Raw spread quotes for a window, as a list of dicts.

    One call, then index it — a per-game query would be hundreds of round
    trips for a slate.
    """
    # `book` is REQUIRED here, not optional detail: index_by_matchup takes the
    # latest quote PER BOOK before taking a median, so without it every row
    # collapses into one pseudo-book and the "median across books" silently
    # becomes "whatever was captured last".
    p = {'select': 'matchup,side,book,line,price,captured_at,commence_time',
         'sport': f'eq.{sport}', 'market': 'eq.spread',
         'commence_time': f'gte.{since}'}
    rows = _page(p)
    if until:
        rows = [r for r in rows if str(r.get('commence_time') or '') < until]
    return rows


def index_by_matchup(rows, as_of: Optional[str] = None):
    """{matchup: {'home': (line, n_books), 'away': (...)}} by MEDIAN.

    `as_of` (ISO timestamp) restricts to quotes captured at or before that
    moment, which is how you ask "what was the line WHEN WE PICKED" — the
    question `close_spread` can never answer because it holds one value that
    keeps being overwritten.

    Taking the median of the LATEST capture per book, not of every row: a
    book quoted 200 times would otherwise outvote one quoted twice.
    """
    per_book = {}
    for r in rows:
        ca = str(r.get('captured_at') or '')
        if as_of and ca > as_of:
            continue
        ln = _f(r.get('line'))
        if ln is None:
            continue
        k = (str(r.get('matchup')), str(r.get('side')), str(r.get('book', '')))
        if k not in per_book or ca > per_book[k][0]:
            per_book[k] = (ca, ln, _f(r.get('price')))
    agg = {}
    for (mt, side, _bk), (_ca, ln, _pr) in per_book.items():
        agg.setdefault(mt, {}).setdefault(side, []).append(ln)
    out = {}
    for mt, sides in agg.items():
        out[mt] = {s: (round(statistics.median(v), 2), len(v))
                   for s, v in sides.items() if v}
    return out


def build_matcher(by_matchup, sport: str):
    """A function (away, home) -> matchup key, or None.

    NFL joins through the nickname table; the others match on one side's name
    being a prefix of the matchup half. Returns None rather than guessing when
    it cannot resolve uniquely — a wrong match here would compare a pick
    against a DIFFERENT GAME's line, which is how I briefly "found" a defect
    in the FIU pick that did not exist (an `ilike '*Panthers*'` query returned
    Pittsburgh's rows alongside Florida International's).
    """
    halves = {}
    for mt in by_matchup:
        if ' @ ' not in mt:
            continue
        a, h = mt.split(' @ ', 1)
        if sport == 'NFL':
            ka = NFL_NICK.get(a.split()[-1].lower())
            kh = NFL_NICK.get(h.split()[-1].lower())
            if ka and kh:
                halves[mt] = (ka, kh)
        else:
            halves[mt] = (a.lower().replace('.', '').strip(),
                          h.lower().replace('.', '').strip())

    def match(away, home):
        if sport == 'NFL':
            want = (str(away).upper(), str(home).upper())
            hits = [mt for mt, k in halves.items() if k == want]
            return hits[0] if len(hits) == 1 else None
        na = str(away or '').lower().replace('.', '').strip()
        nh = str(home or '').lower().replace('.', '').strip()
        hits = [mt for mt, (a, h) in halves.items()
                if na and nh and a.startswith(na) and h.startswith(nh)]
        if len(hits) == 1:
            return hits[0]
        hits = [mt for mt, (a, h) in halves.items()
                if (na and a.startswith(na)) or (nh and h.startswith(nh))]
        return hits[0] if len(hits) == 1 else None

    return match


def home_line(by_matchup, matchup):
    """(line, n_books) for the home side, or (None, 0)."""
    if not matchup:
        return None, 0
    blk = by_matchup.get(matchup) or {}
    if 'home' in blk:
        return blk['home']
    if 'away' in blk:                 # derive it rather than give up
        ln, n = blk['away']
        return (-ln if ln is not None else None), n
    return None, 0


def line_for_side(by_matchup, matchup, side: str):
    """The handicap the named side is getting, from the books."""
    hl, n = home_line(by_matchup, matchup)
    if hl is None:
        return None, 0
    s = str(side or '').upper()
    if s == 'HOME':
        return hl, n
    if s == 'AWAY':
        return -hl, n
    return None, 0


def classify(published, market, tolerance: float = TOLERANCE_PTS):
    """Why a published number differs from the market's: the whole point.

    Returns one of:
      'ok'          within tolerance
      'stale'       off by more than tolerance, same direction
      'sign'        we call it a favourite and the books call it a dog, or
                    the reverse — the error that flips a graded result
      'unknown'     not enough information to say

    A 'stale' or 'sign' verdict is NOT automatically a bug. A line that moved
    is the most interesting thing on a card: BAL opened -6.5, traded -6.0 on
    61 captures, and is now +3.5 — a 9.5-point swing with the favourite
    flipping. Our published "BAL -6" was a real, takeable number when the pick
    was made. Deciding between "moved" and "never existed" needs the price
    HISTORY, not the current line, which is what `ever_traded` is for.
    """
    p, m = _f(published), _f(market)
    if p is None or m is None:
        return 'unknown'
    if abs(p) >= 1.0 and abs(m) >= 1.0 and (p > 0) != (m > 0):
        return 'sign'
    return 'ok' if abs(p - m) <= tolerance else 'stale'


def ever_traded(rows, matchup, side: str, value, tol: float = 0.25):
    """Did the books ever actually post this number for this side?

    The one question that separates a stale-but-real line from a fabricated
    one, and the test that proved "NE -8.5" was a defect (1,064 captures,
    range -4.0..-3.0) while "BAL -6" was legitimate (61 captures at exactly
    -6.0). Returns (traded, n_captures, lo, hi).
    """
    v = _f(value)
    if v is None:
        return False, 0, None, None
    want = str(side or '').lower()
    vals = []
    for r in rows:
        if str(r.get('matchup')) != matchup:
            continue
        ln = _f(r.get('line'))
        if ln is None:
            continue
        s = str(r.get('side') or '').lower()
        if s == want:
            vals.append(ln)
        elif s in ('home', 'away'):
            vals.append(-ln)          # quote the other side from ours
    if not vals:
        return False, 0, None, None
    hits = [x for x in vals if abs(x - v) < tol]
    return bool(hits), len(hits), min(vals), max(vals)
