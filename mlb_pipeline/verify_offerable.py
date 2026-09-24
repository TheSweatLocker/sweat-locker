"""Can a subscriber actually place every pick we published?

2026-09-23. Found while pricing the slate: seven NCAAF picks name a line
no book offers, and one of them is on the wrong side of the game.

    Oregon @ USC   we published "USC -2.5" STRONG, conviction 83
                   every book has USC at +3.0

USC is a three-point underdog. We told a subscriber to lay two and a half
points on a dog. That is not a stale number, it is the opposite team
relative to the market, and no amount of model confidence makes it
placeable.

The check that matters is NOT "did the line move" and NOT "did the sign
flip" — both of those happen legitimately. PHI @ CHI opened at home -1.5
and closed at home +4.5, a six-point move, and CHI +4.5 is quoted at ten
books. That pick is fine.

The check that matters is whether any book is quoting the exact thing we
named. If none is, the pick cannot be placed at the price we implied, and
the failure is ours.

Three verdicts:

  OK          a book offers our side at our line
  DRIFTED     our side is offered, at a different line only
  WRONG_SIDE  the market has our team on the other side of pick'em

WRONG_SIDE is the one that must block a publish. DRIFTED should refresh
the line. Read-only; reports.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
for _l in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

CONTEXT = {'MLB': 'mlb_game_context', 'NFL': 'nfl_game_context',
           'NCAAF': 'ncaaf_game_context', 'NBA': 'nba_game_context',
           'NHL': 'nhl_game_context'}
MARKET = {'ml': 'ml', 'rl': 'spread', 'spread': 'spread',
          'puckline': 'spread', 'total': 'total'}


def page(t, **p):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, timeout=180,
                         params={'limit': 1000, 'offset': off, **p})
        b = r.json()
        if not isinstance(b, list):
            print(f'  !! {t}: {str(b)[:220]}')
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


_ALIAS: dict | None = None


def nfl_key(game_date, away, home):
    global _ALIAS
    if _ALIAS is None:
        _ALIAS = {}
        for a in page('nfl_team_aliases', select='canonical_name,full_name,city,alt_names'):
            for n in filter(None, [a['canonical_name'], a.get('full_name'),
                                   a.get('city')] + list(a.get('alt_names') or [])):
                _ALIAS[str(n).upper()] = a['canonical_name']
    a, h = _ALIAS.get(str(away).upper()), _ALIAS.get(str(home).upper())
    return f"{str(game_date)[:10].replace('-', '')}_{a}_{h}" if a and h else None


def verdict(offered: list, ours) -> str:
    """Classify our line against what the market is actually quoting."""
    offered = [float(x) for x in offered if x is not None]
    if not offered:
        return 'NO_QUOTES'
    if ours is None:
        return 'OK'
    ours = float(ours)
    if any(abs(x - ours) < 1e-6 for x in offered):
        return 'OK'
    # Every quote on the other side of pick'em from us means the market
    # has our team as the opposite of what we published. A spread of -2.5
    # against a market of +3.0 is not a 5.5-point drift, it is the wrong
    # team getting the points.
    if all((x > 0) != (ours > 0) for x in offered) and ours != 0:
        return 'WRONG_SIDE'
    return 'DRIFTED'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default=None)
    ap.add_argument('--sport', default=None)
    args = ap.parse_args()
    if not args.since:
        from datetime import datetime, timezone
        args.since = str(datetime.now(timezone.utc).date())

    sports = [args.sport.upper()] if args.sport else list(CONTEXT)
    totals = defaultdict(int)
    alarms = []
    for sport in sports:
        reads = page('jerry_reads', sport=f'eq.{sport}', select='*',
                     game_date=f'gte.{args.since}')
        if not reads:
            continue
        ctx = {c['game_id']: c for c in page(
            CONTEXT[sport], select='game_id,game_date,away_team,home_team,close_spread,close_total',
            game_date=f'gte.{args.since}')}
        lh = page('line_history', sport=f'eq.{sport}',
                  select='game_id,market,side,line',
                  commence_time=f'gte.{args.since}')
        quotes = defaultdict(set)
        for x in lh:
            quotes[(x['game_id'], x.get('market'), x.get('side'))].add(x.get('line'))

        for r in reads:
            mkt = MARKET.get(str(r.get('call_market') or '').lower())
            if not mkt or not r.get('call_text'):
                continue
            c = ctx.get(r['game_id'])
            key = r['game_id']
            if sport == 'NFL' and c:
                key = nfl_key(c['game_date'], c.get('away_team'), c.get('home_team')) or key
            if mkt == 'total':
                side = 'over' if 'over' in str(r['call_text']).lower() else 'under'
            else:
                side = 'home' if str(r.get('call_side')).upper() == 'HOME' else 'away'
            offered = sorted(x for x in quotes.get((key, mkt, side), set()) if x is not None)
            v = 'OK' if mkt == 'ml' and quotes.get((key, mkt, side)) else \
                verdict(offered, r.get('call_line'))
            totals[f'{sport}:{v}'] += 1
            totals[v] += 1
            if v in ('WRONG_SIDE', 'DRIFTED'):
                alarms.append((sport, c, r, offered, v))

    order = ['WRONG_SIDE', 'DRIFTED', 'NO_QUOTES', 'OK']
    print('verdicts: ' + '  '.join(f'{k}={totals.get(k, 0)}' for k in order))
    print()
    for v in ('WRONG_SIDE', 'DRIFTED'):
        rows = [a for a in alarms if a[4] == v]
        if not rows:
            continue
        print(f'--- {v} ({len(rows)})')
        for sport, c, r, offered, _ in rows:
            g = f"{(c or {}).get('away_team')} @ {(c or {}).get('home_team')}"
            print(f"  {sport:6s} {r['game_date']}  {g[:30]:32s} "
                  f"published {str(r.get('call_text'))[:16]:18s} conv={str(r.get('conviction')):>3s}"
                  f"  market offers {offered}")
        print()
    if totals.get('WRONG_SIDE'):
        print(f"{totals['WRONG_SIDE']} pick(s) are on the opposite side of the market. "
              f"These must not publish.")
        sys.exit(2)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
