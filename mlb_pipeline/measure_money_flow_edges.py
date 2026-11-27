# -*- coding: utf-8 -*-
"""MONEY FLOW on NCAAB sides: the strongest thing in the 2026-11-27 study.

== WHY (2026-11-27) ==================================================
ANDY: "Stop worrying about beating the close, and worry about overall
results based on engine driven signals (money flow, siutaionals records,
matchup gaps or ineeficeincies)".

Of the three families he named, this is the one that measured positive.
`ncaab_game_context.splits_summary` holds, per market (ml / rl / total)
and per side, `bets_pct_avg`, `money_pct_avg`, `handle_pct_avg`,
`sources_agree` and `strength_pts_avg`, plus `triple_confirmed` and
`dissent_flags`. The sharp read is DIVERGENCE: money heavier than tickets
on a side means the handle disagrees with the ticket count.

FIRST RESULT, n=561 graded sides, 2026-11-01 -> 11-26:

    divergence >= 10pp   n=300   169-131   56.3%   +22.64u   ROI +7.55%
    back the public (bets% >= 80)  n= 85    38-47   44.7%   ROI -14.65%
    triple_confirmed=True          n=144    80-64   55.6%   ROI  +6.06%
    controls: always HOME -6.76%, always FAVOURITE -10.16%

The complement is the reason to take it seriously rather than the headline
number: backing the public on the SAME games goes 44.7%, which is below
the documented FADE line, and `triple_confirmed` separates +6.06% from
-3.84%. That is a structure, not one lucky threshold.

**AND IT IS SIDES ONLY.** The same divergence rule on TOTALS is negative
at every threshold (>= 15pp goes 44.7%, ROI -14.74%). That inverts against
KenPom, which measured positive on totals and dead on sides -- see
measure_kenpom_vs_close.py. Independent support for the sides > totals
discipline already in CLAUDE.md.

== WHAT THIS IS NOT ==================================================
+1.37 SE at n=300. It does NOT clear the n>=30-with-significance bar and
nothing here should carry weight or reach a card on this sample. It is one
month. The reason it is only one month is a DATA GAP, not a limit of the
method: `splits_summary` is a single aggregated snapshot per game with one
`captured_at`, so intra-day movement is already lost, and no NCAAB splits
history table exists at all. Re-run this as the season accumulates.

Join note: context and results use DIFFERENT KEY SPACES (md5 hash vs
composed slug), so a `game_id` join returns 0 rows in silence. Joins here
are on resolved names with a +/-1 day tolerance, because the two tables
disagree by a day on late tips.

    python measure_money_flow_edges.py
    python measure_money_flow_edges.py --refresh
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import re
import sys
import unicodedata

import requests
from dotenv import load_dotenv

from ncaab_hist_cache import load_results

load_dotenv()

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                              # noqa: BLE001
    pass

ATS_WIN = 100.0 / 110.0          # NCAAB stores no spread/total juice
ATS_BE = 110.0 / 210.0


def rep(label, picks, be=ATS_BE, floor=30):
    w = sum(1 for o in picks if o == 'W')
    l = sum(1 for o in picks if o == 'L')
    n = w + l
    if not n:
        print(f'  {label:<40} --')
        return
    u = w * ATS_WIN - l
    hit = w / n
    z = (hit - be) / math.sqrt(be * (1 - be) / n)
    note = '' if n >= floor else f'  (n<{floor}, no verdict)'
    print(f'  {label:<40} n={n:>4}  {w:>3}-{l:<3} {100 * hit:>5.1f}%  '
          f'{u:+7.2f}u  ROI {100 * u / n:+6.2f}%  {z:+5.2f} SE{note}')


def norm(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode()
    s = s.lower().replace('.', '').replace(chr(39), '').replace('-', ' ')
    s = re.sub(r'\bst\b', 'state', s)
    return re.sub(r'\s+', ' ', s).strip()


def div(blk, key):
    """(money_pct - bets_pct, bets_pct) for one side, or (None, None)."""
    d = blk.get(key) if isinstance(blk, dict) else None
    if not isinstance(d, dict):
        return None, None
    b, m = d.get('bets_pct_avg'), d.get('money_pct_avg')
    if b is None or m is None:
        return None, None
    return float(m) - float(b), float(b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args()

    u = os.environ['SUPABASE_URL']
    k = os.environ['SUPABASE_KEY']
    h = {'apikey': k, 'Authorization': f'Bearer {k}'}
    r = requests.get(f'{u}/rest/v1/ncaab_game_context', headers=h,
                     params={'select': 'game_date,home_team,away_team,'
                                       'splits_summary,close_spread',
                             'splits_summary': 'not.is.null',
                             'order': 'game_date.asc', 'limit': '5000'},
                     timeout=60)
    r.raise_for_status()
    ctx = r.json()
    res = load_results(refresh=a.refresh, quiet=True)

    byday = {}
    for x in res:
        if x['home_score'] is None:
            continue
        byday.setdefault(x['game_date'], []).append(x)

    def find(d, home, away):
        hn, an = norm(home), norm(away)
        base = dt.date.fromisoformat(str(d)[:10])
        for off in (0, 1, -1):
            day = (base + dt.timedelta(days=off)).isoformat()
            for x in byday.get(day, []):
                if {norm(x['home_team']), norm(x['away_team'])} == {hn, an}:
                    return x
        return None

    pairs = [(x, g) for x in ctx
             for g in [find(x['game_date'], x['home_team'], x['away_team'])]
             if g]
    if not ctx:
        print('no context rows carry splits_summary')
        return 0
    print(f'=== NCAAB MONEY FLOW: {len(pairs)} of {len(ctx)} joined '
          f'({ctx[0]["game_date"]} -> {ctx[-1]["game_date"]}) ===')
    if len(pairs) < 0.8 * len(ctx):
        print('  !! join rate below 80% -- check the name resolver before '
              'reading anything below as an absence')

    # ------------------------------------------------------------ sides
    ats = []
    for x, g in pairs:
        if g['spread_result'] not in ('home_covered', 'away_covered'):
            continue
        hd, hb = div((x['splits_summary'] or {}).get('rl'), 'HOME')
        ad, ab = div((x['splits_summary'] or {}).get('rl'), 'AWAY')
        if hd is None or ad is None:
            continue
        ats.append((g, hd, hb, ad, ab))
    print(f'\n-- SIDES (rl splits): {len(ats)} graded games --')
    cov = ['W' if g['spread_result'] == 'home_covered' else 'L'
           for g, *_ in ats]
    rep('control: always HOME', cov)
    rep('control: always AWAY', ['L' if c == 'W' else 'W' for c in cov])
    rep('control: always FAVOURITE',
        ['W' if ((g['spread_result'] == 'home_covered')
                 == (float(g['close_spread']) < 0)) else 'L'
         for g, *_ in ats if g['close_spread'] is not None])

    def back(sel):
        pk = []
        for g, hd, hb, ad, ab in ats:
            s = sel(hd, hb, ad, ab)
            if s is None:
                continue
            won = (g['spread_result'] == 'home_covered') if s == 'H' \
                else (g['spread_result'] == 'away_covered')
            pk.append('W' if won else 'L')
        return pk

    print('\n  BACK the side where MONEY outweighs TICKETS:')
    for th in (3, 5, 8, 10, 15, 20):
        rep(f'divergence >= {th}pp',
            back(lambda hd, hb, ad, ab, t=th:
                 'H' if hd >= t and hd > ad
                 else ('A' if ad >= t and ad > hd else None)))
    print('\n  FADE the public (few tickets on that side):')
    for th in (35, 30, 25, 20):
        rep(f'bets% <= {th}',
            back(lambda hd, hb, ad, ab, t=th:
                 'H' if hb <= t else ('A' if ab <= t else None)))
    print('\n  BACK the public -- the SAME games from the other side, which')
    print('  is what makes the divergence read a structure and not a fluke:')
    for th in (65, 70, 75, 80):
        rep(f'bets% >= {th}',
            back(lambda hd, hb, ad, ab, t=th:
                 'H' if hb >= t else ('A' if ab >= t else None)))

    print('\n  the payload flag:')
    for want in (True, False):
        pk = []
        for x, g in pairs:
            if g['spread_result'] not in ('home_covered', 'away_covered'):
                continue
            ss = x['splits_summary'] or {}
            if bool(ss.get('triple_confirmed')) != want:
                continue
            hd, _ = div(ss.get('rl'), 'HOME')
            ad, _ = div(ss.get('rl'), 'AWAY')
            if hd is None or ad is None or hd == ad:
                continue
            s = 'H' if hd > ad else 'A'
            won = (g['spread_result'] == 'home_covered') if s == 'H' \
                else (g['spread_result'] == 'away_covered')
            pk.append('W' if won else 'L')
        rep(f'triple_confirmed={want}, back divergence', pk)

    # ----------------------------------------------------------- totals
    tot = []
    for x, g in pairs:
        if g['total_result'] not in ('over', 'under'):
            continue
        od, ob = div((x['splits_summary'] or {}).get('total'), 'OVER')
        ud, ub = div((x['splits_summary'] or {}).get('total'), 'UNDER')
        if od is None or ud is None:
            continue
        tot.append((g, od, ob, ud, ub))
    print(f'\n-- TOTALS (total splits): {len(tot)} graded games --')
    print('   the same rule that works on sides FAILS here. Reported in')
    print('   full rather than dropped, because dropping the losing half')
    print('   of a rule is how a sides-only edge becomes a universal one.')
    rep('control: always OVER',
        ['W' if g['total_result'] == 'over' else 'L' for g, *_ in tot])
    rep('control: always UNDER',
        ['W' if g['total_result'] == 'under' else 'L' for g, *_ in tot])

    def backt(sel):
        pk = []
        for g, od, ob, ud, ub in tot:
            s = sel(od, ob, ud, ub)
            if s is None:
                continue
            won = (g['total_result'] == 'over') if s == 'O' \
                else (g['total_result'] == 'under')
            pk.append('W' if won else 'L')
        return pk

    for th in (3, 5, 8, 10, 15):
        rep(f'divergence >= {th}pp',
            backt(lambda od, ob, ud, ub, t=th:
                  'O' if od >= t and od > ud
                  else ('U' if ud >= t and ud > od else None)))
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
