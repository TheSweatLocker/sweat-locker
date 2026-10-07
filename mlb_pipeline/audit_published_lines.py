"""Does every published pick name a line the books actually had?

Detector only — this writes nothing. It answers the question that matters for
the "we show the receipts" claim: is the number on the card one a subscriber
could have taken?

For each pick it reports one of four verdicts, and the distinction between
the middle two is the whole point:

    ok              within a point of the books
    MOVED           differs, but the books DID trade there — our number is
                    the one that was live when we picked. Not a defect. This
                    is signal: BAL opened -6.5, traded -6.0 on 61 captures,
                    and is now +3.5. A 9.5-point swing with the favourite
                    flipping is the most interesting thing on that card and
                    we currently surface none of it.
    NEVER TRADED    differs AND the books never posted it. A defect. "NE -8.5"
                    on a game that ranged -4.0..-3.0 across 1,064 captures.
    no data         no quotes to check against, or no line on the pick

CLI:
    python audit_published_lines.py                      # all sports, 30d
    python audit_published_lines.py --sport NFL --days 60
    python audit_published_lines.py --live               # upcoming games only
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                    # noqa: BLE001
        pass

SB, H = ML.SB, ML.H

CONTEXT = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
           'MLB': 'mlb_game_context', 'NHL': 'nhl_game_context',
           'NBA': 'nba_game_context'}
#: nflverse stores POSITIVE = home favoured; the books and every other
#: context table store the home handicap. Negate NFL to compare.
FLIP = {'NFL': True}
SPREADY = {'rl', 'spread', 'ats', 'runline', 'puckline'}


def page(t, p, cap=200000):
    out, off = [], 0
    while off < cap:
        q = dict(p)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:160]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def audit(sport: str, since: str, live_only: bool):
    ctbl = CONTEXT.get(sport)
    if not ctbl:
        return None
    ctx = page(ctbl, {'select': 'game_id,game_date,home_team,away_team,'
                                'close_spread,primary_play',
                      'game_date': f'gte.{since}'})
    if live_only:
        today = date.today().isoformat()
        ctx = [z for z in ctx if str(z['game_date'])[:10] >= today]
    if not ctx:
        print(f'{sport}: no games since {since}')
        return None
    reads = {str(a['game_id']): a for a in
             page('jerry_reads', {'select': 'game_id,call_side,call_text,'
                                            'call_line,call_market,conviction',
                                  'sport': f'eq.{sport}',
                                  'game_date': f'gte.{since}'})}
    rows = ML.fetch_spreads(sport, since)
    by_mt = ML.index_by_matchup(rows)
    match = ML.build_matcher(by_mt, sport)
    print(f'=== {sport} ===')
    print(f'  {len(ctx)} games · {len(rows)} book quotes · '
          f'{len(by_mt)} matchups priced')

    verdicts = collections.Counter()
    defects, moved = [], []
    for z in ctx:
        gid = str(z['game_id'])
        pp = jl(z.get('primary_play'))
        a = reads.get(gid) or {}
        mt = match(z['away_team'], z['home_team'])

        # Every place a spread number reaches a user, checked separately:
        # the engine's own pick object and the published read. They disagree
        # with each other often enough that collapsing them hides defects.
        cands = []
        if str(pp.get('type') or '').lower() in SPREADY and pp.get('line') is not None:
            cands.append(('primary_play', pp.get('side'), pp.get('line'),
                          pp.get('label'), pp.get('tier')))
        if str(a.get('call_market') or '').lower() in SPREADY and a.get('call_line') is not None:
            cands.append(('jerry_read', a.get('call_side'), a.get('call_line'),
                          a.get('call_text'), a.get('conviction')))
        if not cands:
            continue

        for src, side, pub, label, tier in cands:
            mkt, nbooks = ML.line_for_side(by_mt, mt, side)
            v = ML.classify(pub, mkt)
            if v == 'unknown' or mkt is None:
                verdicts['no data'] += 1
                continue
            if v == 'ok':
                verdicts['ok'] += 1
                continue
            traded, nhit, lo, hi = ML.ever_traded(rows, mt, side, pub)
            rec = dict(date=str(z['game_date'])[:10], src=src,
                       game=f"{z['away_team']}@{z['home_team']}",
                       label=label, pub=pub, mkt=mkt, tier=tier,
                       nbooks=nbooks, nhit=nhit, lo=lo, hi=hi, kind=v)
            if traded:
                verdicts['MOVED'] += 1
                moved.append(rec)
            else:
                verdicts['NEVER TRADED'] += 1
                defects.append(rec)

    n = sum(verdicts.values())
    for k in ('ok', 'MOVED', 'NEVER TRADED', 'no data'):
        if verdicts[k]:
            print(f'    {k:<14}{verdicts[k]:>5}  {verdicts[k]/max(n,1)*100:>5.1f}%')

    if defects:
        print(f'  -- {len(defects)} DEFECTS (a number the books never posted) --')
        for r in sorted(defects, key=lambda r: r['date'], reverse=True)[:14]:
            print(f"    {r['date']}  {r['game']:<14}{r['src']:<13}"
                  f"\"{str(r['label'])[:22]:<22}\" published {r['pub']:+6.1f}  "
                  f"books {r['mkt']:+6.1f} (range {r['lo']:+.1f}..{r['hi']:+.1f}"
                  f", {r['nbooks']} books)  tier {r['tier']}")
    if moved:
        print(f'  -- {len(moved)} MOVED (real line movement, not a bug) --')
        for r in sorted(moved, key=lambda r: -abs(r['pub'] - r['mkt']))[:10]:
            print(f"    {r['date']}  {r['game']:<14}{r['src']:<13}"
                  f"\"{str(r['label'])[:22]:<22}\" we have {r['pub']:+6.1f}  "
                  f"now {r['mkt']:+6.1f}  moved {r['mkt']-r['pub']:+.1f}"
                  f"  ({r['nhit']} captures at our number)")
    print()
    return verdicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--live', action='store_true',
                    help='only games that have not started')
    args = ap.parse_args()
    since = (date.today() - timedelta(days=args.days)).isoformat()
    sports = [args.sport] if args.sport else ['NFL', 'NCAAF', 'MLB', 'NHL']
    grand = collections.Counter()
    for s in sports:
        v = audit(s, since, args.live)
        if v:
            grand.update(v)
    if len(sports) > 1:
        print('=== ALL SPORTS ===')
        n = sum(grand.values())
        for k in ('ok', 'MOVED', 'NEVER TRADED', 'no data'):
            if grand[k]:
                print(f'  {k:<14}{grand[k]:>5}  {grand[k]/max(n,1)*100:>5.1f}%')


if __name__ == '__main__':
    main()
