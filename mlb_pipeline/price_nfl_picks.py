"""Attach a real price to every published NFL pick so ROI is computable.

2026-09-23. Andy: "Why no price we should price and pick for each pick to
track roi." He is right, and the reason is worse than a missing feed: the
price was already in the database the whole time.

  line_history carries 4,584 NFL rows for this week alone — 11 books,
  all 16 matchups, ml/spread/total, each with a side, a line and a price.

Three breaks stopped it reaching a pick:

  1. nfl_game_picks.odds_american is populated for MONEYLINES ONLY
     (10 of 29 this week). Spread and total picks get None, because the
     writer only ever looked up an ML price.
  2. line_history keys game_id as '20260924_ATL_GB'. nfl_game_context
     uses an md5 hash. Nothing joined them, so even the ML lookup was
     going somewhere else.
  3. jerry_reads — the surface the app actually reads — has no price
     column at all.

This module fixes 1 and 2. It rebuilds line_history's key from
(game_date, away_team, home_team), which is exactly how that key is
constructed, then prices each pick against the books quoting OUR line.

Matching the line is the part that must not be fudged. A pick of
PIT +3.5 is not priced by a book offering +3.0 — that is a different bet.
Rows at a different line are reported as such rather than substituted,
because a wrong price produces a confident wrong ROI, which is worse
than a blank.

Read-only. Prints; writes nothing.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from statistics import median

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
for _l in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Codes line_history uses in its key vs what nfl_game_context stores.
# Both are already NFL abbreviations, so this only covers relocations and
# the handful of feeds that disagree.
_ALIAS = {'LAR': 'LA', 'STL': 'LA', 'SD': 'LAC', 'OAK': 'LV',
          'WSH': 'WAS', 'JAC': 'JAX', 'ARZ': 'ARI', 'BLT': 'BAL',
          'CLV': 'CLE', 'HST': 'HOU'}


def _code(c: str) -> str:
    c = (c or '').upper().strip()
    return _ALIAS.get(c, c)


def page(table: str, **params) -> list[dict]:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params={'limit': 1000, 'offset': off, **params})
        b = r.json()
        if not isinstance(b, list):
            print(f'  !! {table}: {str(b)[:250]}')
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def american_to_decimal(p: int | float) -> float:
    p = float(p)
    return 1 + (p / 100 if p > 0 else 100 / abs(p))


def devig_fair(a: float, b: float) -> tuple[float, float]:
    """Two-way no-vig probabilities from a pair of American prices."""
    ia, ib = 1 / american_to_decimal(a), 1 / american_to_decimal(b)
    s = ia + ib
    return ia / s, ib / s


def lh_key(game_date: str, away: str, home: str) -> str:
    return f"{str(game_date)[:10].replace('-', '')}_{_code(away)}_{_code(home)}"


def quotes_for_window(lo: str, hi: str) -> dict:
    """Latest quote per (key, market, side, line), newest capture wins."""
    rows = page('line_history', sport='eq.NFL',
                select='game_id,market,book,side,line,price,captured_at',
                commence_time=f'gte.{lo}')
    latest: dict = {}
    for x in rows:
        if x.get('price') is None:
            continue
        k = (x['game_id'], str(x.get('market')), str(x.get('side')),
             x.get('line'), x.get('book'))
        prev = latest.get(k)
        if prev is None or str(x.get('captured_at')) > str(prev.get('captured_at')):
            latest[k] = x
    grouped = defaultdict(list)
    for (gid, market, side, line, _book), x in latest.items():
        grouped[(gid, market, side, line)].append(x)
    return grouped


def price_pick(grouped: dict, key: str, market: str, side: str,
               line) -> dict:
    """Consensus and best price across books quoting this exact line."""
    m = {'rl': 'spread', 'spread': 'spread', 'ml': 'ml',
         'total': 'total'}.get(market, market)
    if m == 'total':
        want_side = 'over' if str(side).upper() in ('OVER', 'O') else 'under'
    else:
        want_side = 'home' if str(side).upper() == 'HOME' else 'away'

    cands = []
    for (gid, mk, sd, ln), rows in grouped.items():
        if gid != key or mk != m or sd != want_side:
            continue
        if m == 'ml':
            cands += rows
        elif line is not None and ln is not None and abs(float(ln) - float(line)) < 1e-6:
            cands += rows
    if not cands:
        # say what IS quoted, so a mismatch is visible rather than silent
        other = sorted({ln for (gid, mk, sd, ln) in grouped
                        if gid == key and mk == m and sd == want_side
                        and ln is not None})
        return {'price': None, 'n_books': 0, 'other_lines': other}
    prices = [float(c['price']) for c in cands]
    best = max(prices, key=american_to_decimal)
    # Consensus must be taken in probability space, never on the American
    # numbers themselves. They are discontinuous across zero: -110 and
    # +100 are adjacent prices but forty units apart, so a plain median
    # of a mixed-sign book list returns nonsense. CAR@CLE produced a
    # "consensus" of -1 (a 1% implied probability on a pick'em spread)
    # before this was fixed.
    med_dec = median(american_to_decimal(p) for p in prices)
    cons = (round((med_dec - 1) * 100) if med_dec >= 2
            else round(-100 / (med_dec - 1)))
    return {
        'price': int(cons),
        'implied': 1 / med_dec,
        'best': int(best),
        'best_book': next(c['book'] for c in cands if float(c['price']) == best),
        'n_books': len({c['book'] for c in cands}),
    }


def main():
    lo = sys.argv[1] if len(sys.argv) > 1 else '2026-09-24'
    hi = sys.argv[2] if len(sys.argv) > 2 else '2026-09-30'
    ctx = {c['game_id']: c for c in page('nfl_game_context', select='*',
                                        game_date=f'gte.{lo}')
           if c['game_date'] <= hi}
    reads = [r for r in page('jerry_reads', sport='eq.NFL', select='*',
                             game_date=f'gte.{lo}') if r['game_date'] <= hi]
    grouped = quotes_for_window(lo, hi)
    print(f'line_history quote groups in window: {len(grouped)}\n')

    # 'conv' is deliberately NOT called edge. Conviction is an internal
    # 0-100 ranking, not a calibrated win probability, so subtracting the
    # book's implied percentage from it does not produce an edge — it
    # produces a number that looks like one. Until conviction is
    # calibrated against outcomes, both are shown side by side and
    # nothing is subtracted.
    hdr = (f"{'game':12s} {'pick':13s} {'line':6s} {'cons':>6s} {'best':>6s} "
           f"{'book':12s} {'bk':>3s} {'implied':>8s} {'conv':>5s}")
    print(hdr); print('-' * len(hdr))
    seen, priced, unpriced = set(), 0, []
    for r in sorted(reads, key=lambda x: (x['game_date'], x['game_id'])):
        c = ctx.get(r['game_id'])
        if not c or r['game_id'] in seen:
            continue
        seen.add(r['game_id'])
        key = lh_key(c['game_date'], c.get('away_team'), c.get('home_team'))
        market = str(r.get('call_market') or '').lower()
        side = str(r.get('call_side') or '')
        line = r.get('call_line')
        if market == 'total':
            side = 'OVER' if 'over' in str(r.get('call_text', '')).lower() else 'UNDER'
        q = price_pick(grouped, key, market, side, line)
        g = f"{c.get('away_team')}@{c.get('home_team')}"
        if q['price'] is None:
            unpriced.append((g, r.get('call_text'), market, line, q.get('other_lines')))
            print(f"{g:12s} {str(r.get('call_text'))[:12]:13s} {str(line):6s} "
                  f"{'—':>6s} {'—':>6s} {'':12s} {0:>3d} {'':>6s} {'':>6s}   "
                  f"NO BOOK AT OUR LINE (quoted: {q.get('other_lines')})")
            continue
        priced += 1
        conv = r.get('conviction')
        implied = q['implied'] * 100
        print(f"{g:12s} {str(r.get('call_text'))[:12]:13s} {str(line):6s} "
              f"{q['price']:>+6d} {q['best']:>+6d} {q['best_book'][:11]:12s} "
              f"{q['n_books']:>3d} {implied:>7.1f}% {str(conv):>5s}")

    print(f'\npriced {priced}/{len(seen)} published picks from data we already hold')
    if unpriced:
        print(f'{len(unpriced)} could not be priced at our exact line:')
        for g, call, m, line, other in unpriced:
            print(f'   {g:12s} {call!r} wants {line}, books quote {other}')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
