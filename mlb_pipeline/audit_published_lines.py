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
           'NBA': 'nba_game_context', 'NCAAB': 'ncaab_game_context'}
#: nflverse stores POSITIVE = home favoured; the books and every other
#: context table store the home handicap. Negate NFL to compare.
FLIP = {'NFL': True}
SPREADY = {'rl', 'spread', 'ats', 'runline', 'puckline'}
# ══ 2026-11-05 · TOTALS WERE NEVER CHECKED AGAINST THE MARKET ══════
# This gated on SPREADY alone, so a published OVER/UNDER was never
# compared to a posted number on ANY sport. That is most of the NHL and
# NCAAB boards, and it is the exact shape of what got through tonight:
# `Over 48.5` on JAX@BAL, rank 1 of the Sweat Card, off a close_total
# frozen at 48.5 while 11 of 11 books sat at 39.5. A spread-only audit
# could not have caught it.
#
# market_line already had index_totals(), and ever_traded() handles
# over/under correctly (it negates only for home/away), so this needed
# wiring, not new machinery.
TOTALISH = {'total', 'ou', 'over_under', 'totals'}


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


def _names_resolve(sport: str, away, home) -> bool:
    """True when BOTH team names map to a canonical name.

    Lets the caller separate "we cannot read the name" from "the books have
    no market". Only NCAAB has a resolver to ask; elsewhere the matcher works
    off the names directly, so a miss there is a market gap by definition.
    """
    if str(sport).upper() != 'NCAAB':
        return True
    try:
        import ncaab_team_resolver as R
        R.load_aliases()
        return bool(R.resolve(away)) and bool(R.resolve(home))
    except Exception:                                    # noqa: BLE001
        return True


def audit(sport: str, since: str, live_only: bool, on: str = None):
    ctbl = CONTEXT.get(sport)
    if not ctbl:
        return None
    ctx = page(ctbl, {'select': 'game_id,game_date,home_team,away_team,'
                                'close_spread,primary_play',
                      'game_date': f'gte.{since}'})
    # ══ 2026-11-05 · --live ON NFL MEANS THE REST OF THE SEASON ═══════
    # `--live` keeps every game that has not started, which for NFL is 151
    # rows running to January. Books have not posted most of them, so they
    # land in `no data` -- and the report then reads `no data 100.0%`, which
    # looks like a broken detector rather than "these games have no market
    # yet". That is how the NCAAB gap stayed invisible: the honest no-data of
    # far-future games was indistinguishable from a real inability to check.
    #
    # `--on DATE` answers the question that actually gets asked: are the
    # numbers on TONIGHT's surfaces the numbers the books have right now.
    if on:
        ctx = [z for z in ctx if str(z['game_date'])[:10] == on]
    elif live_only:
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
    # When one date is named, the quote window is that date -- not `since`.
    # A 30-day commence_time filter on line_history 500s on deep pages for
    # NFL and NCAAF (observed: PARTIAL after 13,000 and 9,000 rows), and a
    # partial quote set makes a median across "the books that fit".
    _until = None
    if on:
        _until = (date.fromisoformat(on) + timedelta(days=2)).isoformat()
    rows = ML.fetch_spreads(sport, on or since, _until)
    by_mt = ML.index_by_matchup(rows)
    trows = ML.fetch_market(sport, 'total', on or since, _until)
    by_tot = ML.index_totals(trows)
    # Build the matcher from whichever index has more matchups: a game can be
    # quoted for a total and not a spread (common on NCAAB), and keying off
    # spreads alone would make those games unmatchable and report them as
    # unverifiable when the total IS checkable.
    match = ML.build_matcher(by_mt if len(by_mt) >= len(by_tot) else by_tot,
                             sport)
    print(f'=== {sport} ===')
    print(f'  {len(ctx)} games · {len(rows)} book quotes · '
          f'{len(by_mt)} matchups priced'
          + (f' · on {on}' if on else ''))
    # ══ "UNMATCHED" IS TWO DIFFERENT THINGS AND ONLY ONE IS OUR BUG ════
    # A game with no priced matchup is either (a) a name our resolver cannot
    # map, which is a defect here, or (b) a game the books never posted, which
    # is a gap at the source and nothing to fix. Reporting them together is
    # how 11 NCAAB games read as a resolver failure tonight when the resolver
    # had in fact mapped all 22 team names correctly and the Odds API simply
    # does not carry Gardner Webb @ Texas.
    _unres, _nomkt = [], []
    for z in ctx:
        if match(z['away_team'], z['home_team']) is not None:
            continue
        (_nomkt if _names_resolve(sport, z['away_team'], z['home_team'])
         else _unres).append(f"{z['away_team']}@{z['home_team']}")
    if _unres:
        print(f'  ✗ {len(_unres)} game(s) whose TEAM NAMES did not resolve — '
              f'this is a defect in the resolver, not a market gap:')
        for g in _unres[:10]:
            print(f'      {g}')
    if _nomkt:
        print(f'  · {len(_nomkt)} game(s) resolved fine but the books posted '
              f'no spread — nothing to check against (e.g. {_nomkt[0]})')

    verdicts = collections.Counter()
    defects, moved, nolines = [], [], []
    for z in ctx:
        gid = str(z['game_id'])
        pp = jl(z.get('primary_play'))
        a = reads.get(gid) or {}
        mt = match(z['away_team'], z['home_team'])

        # Every place a spread number reaches a user, checked separately:
        # the engine's own pick object and the published read. They disagree
        # with each other often enough that collapsing them hides defects.
        cands = []
        _ppt = str(pp.get('type') or '').lower()
        _rdm = str(a.get('call_market') or '').lower()
        if _ppt in SPREADY and pp.get('line') is not None:
            cands.append(('primary_play', 'spread', pp.get('side'),
                          pp.get('line'), pp.get('label'), pp.get('tier')))
        elif _ppt in TOTALISH and pp.get('line') is not None:
            cands.append(('primary_play', 'total', pp.get('side'),
                          pp.get('line'), pp.get('label'), pp.get('tier')))
        if _rdm in SPREADY and a.get('call_line') is not None:
            cands.append(('jerry_read', 'spread', a.get('call_side'),
                          a.get('call_line'), a.get('call_text'),
                          a.get('conviction')))
        elif _rdm in TOTALISH and a.get('call_line') is not None:
            cands.append(('jerry_read', 'total', a.get('call_side'),
                          a.get('call_line'), a.get('call_text'),
                          a.get('conviction')))
        # A pick whose market IS checkable but whose line is NULL is its own
        # finding, not a silent skip. 'New York Rangers PL' with call_line
        # null can be neither verified nor graded, and folding it into
        # 'no data' beside a game the books never posted hides it (R156).
        for _s2, _m2, _ln2, _lb2 in (
                ('primary_play', _ppt, pp.get('line'), pp.get('label')),
                ('jerry_read', _rdm, a.get('call_line'), a.get('call_text'))):
            if _m2 in (SPREADY | TOTALISH) and _ln2 is None:
                verdicts['NO LINE ON PICK'] += 1
                nolines.append(dict(date=str(z['game_date'])[:10], src=_s2,
                                    game='%s@%s' % (z['away_team'],
                                                    z['home_team']),
                                    label=_lb2, market=_m2))
        if not cands:
            continue

        for src, kind, side, pub, label, tier in cands:
            if kind == 'total':
                mkt, nbooks = by_tot.get(mt, (None, 0))
                _sl = str(side or '').lower()
                side = ('over' if 'over' in _sl
                        else 'under' if 'under' in _sl else side)
            else:
                mkt, nbooks = ML.line_for_side(by_mt, mt, side)
            v = ML.classify(pub, mkt)
            if v == 'unknown' or mkt is None:
                verdicts['no data'] += 1
                continue
            if v == 'ok':
                verdicts['ok'] += 1
                continue
            traded, nhit, lo, hi = ML.ever_traded(
                trows if kind == 'total' else rows, mt, side, pub)
            rec = dict(date=str(z['game_date'])[:10], src='%s/%s' % (src, kind),
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
    for k in ('ok', 'MOVED', 'NEVER TRADED', 'NO LINE ON PICK', 'no data'):
        if verdicts[k]:
            print(f'    {k:<17}{verdicts[k]:>5}  '
                  f'{verdicts[k]/max(n,1)*100:>5.1f}%')
    if nolines:
        print(f'  -- {len(nolines)} PICK(S) WITH NO LINE '
              f'(ungradeable AND unverifiable) --')
        for r in nolines[:12]:
            print(f"    {r['date']}  {r['game'][:30]:<30}"
                  f"{r['src']:<13}{r['market']:<8}{str(r['label'])[:30]}")

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
    ap.add_argument('--on', default=None,
                    help='one play date (YYYY-MM-DD); also narrows the quote '
                         'window to it, which is what makes NFL/NCAAF finish')
    args = ap.parse_args()
    since = (date.today() - timedelta(days=args.days)).isoformat()
    # 2026-11-05: NBA and NCAAB were missing, so the two sports carrying
    # most of the slate were never checked. NCAAB is the whole reason -- it
    # joins through ncaab_team_resolver now (see market_line.build_matcher).
    sports = ([args.sport] if args.sport
              else ['NFL', 'NCAAF', 'MLB', 'NHL', 'NBA', 'NCAAB'])
    grand = collections.Counter()
    for s in sports:
        v = audit(s, since, args.live, on=args.on)
        if v:
            grand.update(v)
    if len(sports) > 1:
        print('=== ALL SPORTS ===')
        n = sum(grand.values())
        for k in ('ok', 'MOVED', 'NEVER TRADED', 'NO LINE ON PICK',
                  'no data'):
            if grand[k]:
                print(f'  {k:<14}{grand[k]:>5}  {grand[k]/max(n,1)*100:>5.1f}%')


if __name__ == '__main__':
    main()
