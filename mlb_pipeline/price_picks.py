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
for _l in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
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


def quote_index(sport: str, lo: str) -> dict:
    """Latest quote per (game_id, market, side, line), newest wins."""
    rows = page('line_history', sport=f'eq.{sport}',
                select='game_id,market,book,side,line,price,captured_at',
                commence_time=f'gte.{lo}')
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
        ctx = {c['game_id']: c for c in page(CONTEXT[sport], select='game_id,game_date,away_team,home_team',
                                             game_date=f'gte.{lo}')}
        grouped = quote_index(sport, lo)
        ok = fail = skip = 0
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
            if r.get('price_american') is not None and _has_started(r, ctx):
                skip += 1
                continue
            if not r.get('call_text') or not r.get('call_market'):
                skip += 1
                continue
            c = ctx.get(r['game_id'])
            key = r['game_id']
            if sport == 'NFL' and c:
                key = nfl_key(c['game_date'], c.get('away_team'), c.get('home_team')) or key
            q = price_one(grouped, key, r.get('call_market'), r.get('call_side'),
                          r.get('call_line'), r.get('call_text'))
            if q.get('price') is None:
                fail += 1
                reasons[q.get('why', '?')] += 1
                continue
            ok += 1
            patches.append((r['id'], {
                'price_american': q['price'], 'price_best': q['best'],
                'price_best_book': q['best_book'], 'price_books_n': q['n_books'],
                'price_implied': q['implied'],
                'priced_at': datetime.now(timezone.utc).isoformat(),
            }))
        print(f'{sport:6s} reads={len(reads):4d}  priceable={ok:4d}  '
              f'unmatched={fail:4d}  already/skip={skip:4d}')
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
