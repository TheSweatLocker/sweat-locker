#!/usr/bin/env python3
"""Did the line we PUBLISHED ever exist at a book? Read-only.

== 2026-10-19 - THE FINDING THAT OUTRANKS EVERY MODEL RESULT ==
ANDY: "Week 6 and we still ahve weekly fucking plumbing probems and not
capturing data, this cant be happening."

He is right, and it is worse than a capture gap. Checked against line_history,
which holds 500 captures across 7 books for each of these games:

    CAR @ PHI   market PHI -1.5..-2.5   we published PHI -6.5   ~4.5 off
    LAC @ KC    market KC  -7.0..-7.5   we published KC  -2.5    5.0 off, OUR WAY
    NO  @ NYG   market NO  -1.5         we published NO  +2.5    4.0 off, OUR WAY

None of those three numbers was EVER available at any book at any time. Six of
twelve NFL spread picks on 2026-10-18 were 3+ points off the market, in both
directions.

WHY THIS INVALIDATES MORE THAN IT LOOKS LIKE:
  * A record graded at a number nobody could bet is not a record. Picks
    published 5 points BETTER than the market win more often, for free, and
    inflate the surface. Picks published worse lose for free.
  * It explains away the one "signal" the deep dive found. Closing line value
    sorted results hard (CLV>0 60.6% vs CLV<0 44.8% graded at the close), and
    I read that as the market carrying information about our picks. A large
    part of it is simply this: positive CLV mostly MEANS our published line
    was wrongly generous. The artifact and the signal are the same number.
    Retracted until it can be measured on picks whose line is verified.
  * It is upstream of every tier, gate and model. No amount of selection
    tuning survives grading against a fantasy number.

WHAT THIS DOES. For every published pick with a line, it asks line_history
whether that exact number was ever quoted for that game, at any book, at any
time before kickoff - and reports the distance to the nearest number that was.

USAGE
    python audit_published_line_integrity.py
    python audit_published_line_integrity.py --sport NFL --date 2026-10-18
    python audit_published_line_integrity.py --tolerance 1.0
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import sys
from collections import defaultdict

import requests

import market_line as ML
from public_receipt import _side_for_receipt

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
LINE_RX = re.compile(r'([+-]\d+(?:\.\d+)?)')
SPREAD_MARKETS = ('rl', 'spread', 'ats')


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def page(t, p):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, timeout=240,
                         params=dict(p, limit='1000', offset=str(off)))
        if r.status_code not in (200, 206):
            print(f'  !! {t} HTTP {r.status_code} {r.text[:150]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def market_quotes_by_gid(sport: str, lo: str, hi: str):
    """game_id -> every HOME-side line ever quoted pregame.

    ══ 2026-10-19 · CONSTRUCT THE ID, DO NOT FUZZY-MATCH THE NAMES ══
    The first version matched context teams against line_history.matchup by
    4-character prefix, and covered only 17 of 55 NFL picks: context stores
    abbreviations ("KC") while matchup stores full names ("Kansas City
    Chiefs"), so the prefix test fails on exactly the rows that matter. It
    then UNDER-REPORTED the defect, which is the worst direction for an
    integrity audit.

    The ids are constructible instead:
        NFL    20261018_CAR_PHI              = YYYYMMDD_AWAY_HOME, abbrevs
        NCAAF  ncaaf_20261010_UCF_Oklahoma State = ncaaf_YYYYMMDD_Away_Home
    and both context tables use the same team spellings the ids are built
    from. Keying on game_id removes the name problem completely.
    """
    quotes = defaultdict(set)
    last = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/line_history', headers=H, timeout=240,
                         params={'select': 'id,game_id,line,captured_at,'
                                           'commence_time',
                                 'sport': f'eq.{sport}',
                                 'market': 'eq.spread',
                                 'side': 'eq.home',
                                 'commence_time': [f'gte.{lo}', f'lt.{hi}'],
                                 'id': f'gt.{last}',
                                 'order': 'id.asc', 'limit': '1000'})
        if r.status_code not in (200, 206):
            # ══ 2026-10-19 · A FAILED QUERY IS NOT AN EMPTY TABLE ══
            # line_history throws 57014 (statement timeout) INTERMITTENTLY and
            # planner-dependently: measured the same window at limit 1000 ->
            # 200 in 0.4s, limit 500 -> 500 in 8.3s, limit 200 -> 200 in 0.4s.
            # The old `break` turned one transient failure into "NCAAF: 0
            # games with quoted lines", which reads as "we have no data" and
            # silently under-reported the defect being audited. Retry, shrink,
            # and if it still fails say so loudly instead of returning empty.
            ok = False
            for lim in ('500', '200', '100'):
                rr = requests.get(f'{SB}/rest/v1/line_history', headers=H,
                                  timeout=240,
                                  params={'select': 'id,game_id,line,'
                                                    'captured_at,commence_time',
                                          'sport': f'eq.{sport}',
                                          'market': 'eq.spread',
                                          'side': 'eq.home',
                                          'commence_time': [f'gte.{lo}',
                                                            f'lt.{hi}'],
                                          'id': f'gt.{last}',
                                          'order': 'id.asc', 'limit': lim})
                if rr.status_code in (200, 206):
                    r, ok = rr, True
                    break
            if not ok:
                print(f'  !! line_history unreadable after retries at id>'
                      f'{last}: {r.status_code} {r.text[:120]}')
                print('  !! REPORTING PARTIAL - this is a query failure, '
                      'not an absence of data')
                break
        b = r.json()
        if not b:
            break
        for x in b:
            ln = f(x.get('line'))
            if ln is None:
                continue
            if str(x.get('captured_at')) >= str(x.get('commence_time') or 'z'):
                continue
            quotes[str(x['game_id'])].add(ln)
        last = b[-1]['id']
        if len(b) < 1000:
            break
    return quotes


def candidate_gids(sport: str, game_date: str, away, home):
    """The line_history ids this game could carry, date +/- 1 day."""
    out = []
    try:
        d0 = dt.date.fromisoformat(str(game_date)[:10])
    except Exception:                                          # noqa: BLE001
        return out
    for delta in (0, -1, 1):
        d = (d0 + dt.timedelta(days=delta))
        if sport == 'NFL':
            out.append(f'{d.strftime("%Y%m%d")}_{away}_{home}')
        else:
            out.append(f'ncaaf_{d.strftime("%Y%m%d")}_{away}_{home}')
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--date', default=None)
    ap.add_argument('--tolerance', type=float, default=0.5)
    a = ap.parse_args()

    sports = [a.sport] if a.sport else ['NFL', 'NCAAF']
    ctx_tbl = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context'}
    print(f'=== published-line integrity · tolerance {a.tolerance} pts ===')

    grand = defaultdict(lambda: [0, 0, 0.0])
    for sport in sports:
        sel = {'select': 'id,sport,surface,market,game_id,game_date,pick_side,'
                         'pick_line,pick_label,matchup,tier,result',
               'sport': f'eq.{sport}'}
        if a.date:
            sel['game_date'] = f'eq.{a.date}'
        else:
            sel['game_date'] = 'gte.2026-10-05'      # line_history starts here
        rec = [x for x in page('public_receipts', sel)
               if str(x.get('market')) in SPREAD_MARKETS]
        if not rec:
            print(f'\n{sport}: no spread receipts in range')
            continue
        dates = sorted({str(x['game_date'])[:10] for x in rec})
        lo = (dt.date.fromisoformat(dates[0]) - dt.timedelta(days=1)).isoformat()
        hi = (dt.date.fromisoformat(dates[-1]) + dt.timedelta(days=2)).isoformat()
        quotes = market_quotes_by_gid(sport, lo, hi)
        ctx = {str(x['game_id']): x for x in page(ctx_tbl[sport],
                                                  {'select': 'game_id,'
                                                             'away_team,'
                                                             'home_team'})}
        print(f'\n{"="*72}\n{sport}: {len(rec)} spread picks, '
              f'{len(quotes)} games with quoted lines\n{"="*72}')
        rows = []
        for x in rec:
            ours = f(x.get('pick_line'))
            if ours is None:
                m = LINE_RX.search(str(x.get('pick_label') or ''))
                ours = float(m.group(1)) if m else None
            if ours is None:
                continue
            c = ctx.get(str(x.get('game_id')))
            mu = str(x.get('matchup') or '')
            if c:
                aw, hm = c.get('away_team'), c.get('home_team')
            elif ' @ ' in mu:
                aw, hm = (q.strip() for q in mu.split(' @ ', 1))
            else:
                continue
            k = None
            if str(x.get('game_id')) in quotes:
                k = str(x.get('game_id'))
            else:
                for cand in candidate_gids(sport, x.get('game_date'), aw, hm):
                    if cand in quotes:
                        k = cand
                        break
            if not k:
                continue
            side = str(x.get('pick_side') or '').upper()
            if side not in ('HOME', 'AWAY'):
                side = str(_side_for_receipt(
                    {'label': x.get('pick_label'),
                     'game': f'{aw} @ {hm}'}, 'rl', None) or '').upper()
            if side not in ('HOME', 'AWAY'):
                continue
            # every number ever quoted FOR THE SIDE WE TOOK
            quoted = (quotes[k] if side == 'HOME'
                      else {-v for v in quotes[k]})
            nearest = min(quoted, key=lambda q: abs(q - ours))
            rows.append({'x': x, 'ours': ours, 'side': side,
                         'nearest': nearest, 'off': ours - nearest,
                         'n_quotes': len(quoted),
                         'game': f'{aw} @ {hm}'})
        if not rows:
            print('  no pick could be matched to quoted lines')
            continue
        bad = [r for r in rows if abs(r['off']) > a.tolerance]
        bad.sort(key=lambda r: -abs(r['off']))
        print(f'  checked {len(rows)}   NEVER-QUOTED (off by > '
              f'{a.tolerance}): {len(bad)}  ({100*len(bad)/len(rows):.0f}%)')
        print(f'  mean |off| {sum(abs(r["off"]) for r in rows)/len(rows):.2f} pts'
              f'   mean signed off '
              f'{sum(r["off"] for r in rows)/len(rows):+.2f} pts'
              f'   (positive = our number was GENEROUS to us)')
        print(f'\n  {"date":<12}{"surface":<12}{"pick":<24}{"ours":>7}'
              f'{"nearest quoted":>16}{"off":>7}  result')
        for r in bad[:20]:
            x = r['x']
            print(f"  {str(x['game_date'])[:10]:<12}{str(x.get('surface')):<12}"
                  f"{str(x.get('pick_label'))[:22]:<24}{r['ours']:>+7.1f}"
                  f"{r['nearest']:>+16.1f}{r['off']:>+7.1f}  {x.get('result')}")
        for r in rows:
            k = str(r['x'].get('surface'))
            grand[k][0] += 1
            grand[k][1] += abs(r['off']) > a.tolerance
            grand[k][2] += r['off']
        # does a generous line inflate the record?
        gen = [r for r in rows if r['off'] > a.tolerance
               and str(r['x'].get('result')).upper() in ('WIN', 'W', 'LOSS', 'L')]
        harsh = [r for r in rows if r['off'] < -a.tolerance
                 and str(r['x'].get('result')).upper() in ('WIN', 'W', 'LOSS', 'L')]
        for nm, v in (('line GENEROUS to us', gen), ('line HARSH to us', harsh)):
            if len(v) < 8:
                print(f'  {nm:<24}n={len(v)} - too thin to quote')
                continue
            w = sum(1 for r in v
                    if str(r['x'].get('result')).upper() in ('WIN', 'W'))
            print(f'  {nm:<24}{w}-{len(v)-w}  {100*w/len(v):.1f}%  n={len(v)}')

    if grand:
        print(f'\n{"="*72}\nBY SURFACE (all sports in range)\n{"="*72}')
        print(f'  {"surface":<14}{"checked":>8}{"never quoted":>14}{"mean signed":>13}')
        for k, (n, b, s) in sorted(grand.items(), key=lambda kv: -kv[1][1]):
            print(f'  {k:<14}{n:>8}{b:>14}{s/max(n,1):>+13.2f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
