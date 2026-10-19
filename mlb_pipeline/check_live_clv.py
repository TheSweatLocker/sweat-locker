#!/usr/bin/env python3
"""Is the market moving TOWARD or AGAINST today's published picks? Read-only.

== 2026-10-19 - WHY THIS IS THE ONE SIGNAL WORTH BUILDING ==
Four independent searches found nothing that predicts the spread residual:
151/151/80 context features (zero clear Bonferroni, nominal hits == chance),
15 line-movement features (none reach nominal p<.05), a multivariate ridge
(TEST R2 NEGATIVE in all three sports), and our own closing line value
(mean +0.04 pts, sign-test p=0.64).

One thing did survive every control. Grading the SAME picks at our number and
again at the close separates the mechanical half from the information half:

                    at our line      at the close
    CLV > 0         22-11  66.7%     20-13  60.6%
    CLV < 0         11-18  37.9%     13-16  44.8%
    gap             28.8 pts         15.8 pts

Half of the gap is just holding a better number. The other ~16 points is
INFORMATION: the market moving toward a pick says something true about that
pick. n=62 with overlapping CIs, so this is the strongest lead in the
codebase, not a proven edge.

WHAT THIS DOES. For every pick published for a date, it compares OUR number to
the LATEST captured market number and reports which way the market has moved.
Negative live-CLV means the market has moved against us and, on the evidence
above, those picks underperform.

WHAT THIS DELIBERATELY DOES NOT DO. It does not gate, demote or unpublish
anything. n=62 does not earn a live gate, and the documented pattern for a
suppression rule here is to SHADOW first and exit on evidence
(feedback_suppression_gate_needs_shadow). It prints, and with --shadow it
records, so that in a few weeks the rule can be judged on its own data -
line_history now covers ~100% of games going forward, so the sample grows by
roughly 124 football and 50 NHL games a week.

USAGE
    python check_live_clv.py                     # today
    python check_live_clv.py --date 2026-10-19
    python check_live_clv.py --shadow            # also write clv_shadow rows
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
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
HW = {**H, 'Content-Type': 'application/json',
      'Prefer': 'resolution=merge-duplicates,return=minimal'}
SPREAD_MARKETS = ('rl', 'spread', 'ats', 'puckline')
LINE_RX = re.compile(r'([+-]\d+(?:\.\d+)?)')


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def today_et() -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=4)).strftime('%Y-%m-%d')


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


def build_line_history_index(sport: str, game_date: str):
    """(away, home) -> line_history game_id, for the dates around game_date.

    ══ 2026-10-19 · THE IDS DO NOT MATCH, AGAIN ══
    An NFL receipt carries the context HASH (f122527f003276d3...) while
    line_history keys on YYYYMMDD_AWAY_HOME (20261018_DAL_GB). Joining them
    on game_id finds nothing, which is the same two-id-schemes problem that
    made nfl_game_results scores look stale. line_history does carry
    `matchup` ("Dallas Cowboys @ Green Bay Packers"), so teams plus a date
    window is the join that actually works - the same fallback
    resolve_nfl_results already uses.
    """
    idx = {}
    lo = (dt.date.fromisoformat(game_date) - dt.timedelta(days=1)).isoformat()
    hi = (dt.date.fromisoformat(game_date) + dt.timedelta(days=2)).isoformat()
    r = requests.get(f'{SB}/rest/v1/line_history', headers=H, timeout=180,
                     params={'select': 'game_id,matchup,commence_time',
                             'sport': f'eq.{sport}', 'market': 'eq.spread',
                             'side': 'eq.home', 'book': 'eq.draftkings',
                             # TWO filters on ONE column need a LIST, not a
                             # dotted key: 'commence_time.lt' is not PostgREST
                             # syntax, it is silently treated as an unknown
                             # param and the window never applies - which is
                             # why this index came back holding 0 games.
                             'commence_time': [f'gte.{lo}', f'lt.{hi}'],
                             'limit': '1000'})
    if r.status_code not in (200, 206):
        return idx
    for x in r.json():
        mu = str(x.get('matchup') or '')
        if ' @ ' not in mu:
            continue
        away, home = (q.strip().lower() for q in mu.split(' @ ', 1))
        idx[(away, home)] = str(x['game_id'])
    return idx


def resolve_line_history_id(x, idx):
    """Map a receipt to its line_history game_id via teams, when ids differ."""
    mu = str(x.get('matchup') or '')
    if ' @ ' in mu:
        away, home = (q.strip().lower() for q in mu.split(' @ ', 1))
        if (away, home) in idx:
            return idx[(away, home)]
        # receipts sometimes carry abbreviations; match on a prefix
        for (a, h), gid in idx.items():
            if (a.startswith(away[:3]) and h.startswith(home[:3])) or                (away[:3] and a[:3] == away[:3] and h[:3] == home[:3]):
                return gid
    return None


def latest_lines(sport: str, game_ids: set[str], market: str = 'spread'):
    """Latest pregame capture per game, in HOME-handicap terms.

    Queried per game rather than streamed: a handful of ids is a handful of
    indexed lookups, where a date-range scan of a 3.47M row table is not.
    """
    out = {}
    side = 'over' if market == 'total' else 'home'
    for gid in game_ids:
        r = requests.get(f'{SB}/rest/v1/line_history', headers=H, timeout=120,
                         params={'select': 'line,price,captured_at,'
                                           'commence_time,book',
                                 'sport': f'eq.{sport}',
                                 'market': f'eq.{market}',
                                 'side': f'eq.{side}',
                                 'game_id': f'eq.{gid}',
                                 'order': 'captured_at.desc',
                                 'limit': '20'})
        if r.status_code not in (200, 206):
            continue
        rows = [x for x in r.json() if f(x.get('line')) is not None]
        if not rows:
            continue
        now = dt.datetime.now(dt.timezone.utc)
        fresh = []
        for x in rows:
            try:
                ca = dt.datetime.fromisoformat(
                    str(x['captured_at']).replace('Z', '+00:00'))
                ct = dt.datetime.fromisoformat(
                    str(x['commence_time']).replace('Z', '+00:00'))
            except Exception:                                  # noqa: BLE001
                continue
            if ca < min(now, ct):          # pregame only, never post-kickoff
                fresh.append((ca, f(x['line']), x.get('book')))
        if not fresh:
            continue
        fresh.sort(reverse=True)
        out[gid] = {'line': fresh[0][1], 'at': fresh[0][0].isoformat()[:19],
                    'book': fresh[0][2], 'n': len(fresh)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--shadow', action='store_true')
    a = ap.parse_args()
    d = a.date or today_et()
    print(f'=== live CLV · {d} · read-only ===\n')

    rec = [x for x in page('public_receipts',
                           {'select': 'id,sport,surface,market,game_id,'
                                      'game_date,pick_side,pick_line,'
                                      'pick_label,matchup,tier,conviction,'
                                      'result',
                            'game_date': f'eq.{d}'})
           if str(x.get('market')) in SPREAD_MARKETS]
    print(f'spread-market picks published for {d}: {len(rec)}')
    if not rec:
        print('  nothing to check')
        return 0

    # map every receipt to the id line_history actually uses
    lh_id = {}
    for sp in {str(x.get('sport')) for x in rec}:
        idx = build_line_history_index(sp, d)
        for x in rec:
            if str(x.get('sport')) != sp:
                continue
            gid = str(x.get('game_id') or '')
            # direct hit first (NHL ids already match), then the team bridge
            lh_id[x['id']] = gid if gid in set(idx.values()) else (
                resolve_line_history_id(x, idx) or gid)
        print(f'  {sp}: line_history index holds {len(idx)} games')
    by_sport = defaultdict(set)
    for x in rec:
        if lh_id.get(x['id']):
            by_sport[str(x.get('sport'))].add(lh_id[x['id']])
    live = {}
    for sp, ids in by_sport.items():
        got = latest_lines(sp, ids)
        for gid, v in got.items():
            live[(sp, gid)] = v
        print(f'  {sp}: live line found for {len(got)}/{len(ids)} games')

    rows = []
    price_only = []
    for x in rec:
        ours = f(x.get('pick_line'))
        if ours is None:
            m = LINE_RX.search(str(x.get('pick_label') or ''))
            ours = float(m.group(1)) if m else None
        side = str(x.get('pick_side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            side = str(_side_for_receipt({'label': x.get('pick_label'),
                                          'game': x.get('matchup')},
                                         str(x.get('market')), None)
                       or '').upper()
        lv = live.get((str(x.get('sport')), lh_id.get(x['id'])))
        if ours is None and lv and str(x.get('market')) == 'puckline':
            # ══ A FIXED HANDICAP HAS NO LINE VALUE, ONLY PRICE VALUE ══
            # NHL puckline (and MLB runline) is always +/-1.5: the number does
            # not move, the PRICE does. "Colorado Avalanche RL" stores no line
            # because there is nothing to store. Measuring line-CLV on these
            # is a category error, so they are reported as price-only and
            # excluded from the points CLV rather than defaulted to +/-1.5,
            # which would invent a number nobody quoted.
            price_only.append(x)
            continue
        if ours is None or side not in ('HOME', 'AWAY') or not lv:
            continue
        cur_ours = lv['line'] if side == 'HOME' else -lv['line']
        rows.append({'x': x, 'ours': ours, 'cur': cur_ours,
                     'clv': ours - cur_ours, 'side': side, 'lv': lv})
    print(f'  live CLV computable on {len(rows)} of {len(rec)}\n')
    if not rows:
        print('  (no overlap between published picks and captured lines)')
        return 0

    rows.sort(key=lambda r: r['clv'])
    print(f'  {"sport":<6}{"surface":<12}{"pick":<30}{"ours":>7}{"now":>7}'
          f'{"CLV":>7}  market moved')
    for r in rows:
        x = r['x']
        arrow = ('AGAINST us' if r['clv'] < 0 else
                 ('toward us' if r['clv'] > 0 else 'not at all'))
        print(f"  {str(x.get('sport')):<6}{str(x.get('surface')):<12}"
              f"{str(x.get('pick_label'))[:28]:<30}{r['ours']:>+7.1f}"
              f"{r['cur']:>+7.1f}{r['clv']:>+7.2f}  {arrow}")
    neg = [r for r in rows if r['clv'] < 0]
    pos = [r for r in rows if r['clv'] > 0]
    print(f'\n  moved AGAINST us: {len(neg)}   toward us: {len(pos)}   '
          f'unchanged: {len(rows)-len(neg)-len(pos)}')
    print(f'  mean live CLV {sum(r["clv"] for r in rows)/len(rows):+.3f} pts')
    print('\n  REFERENCE, graded at the close on n=62: CLV>0 went 60.6%, '
          'CLV<0 went 44.8%.')
    print('  This is a SHADOW read. Nothing is gated on it at this sample.')

    if a.shadow:
        batch = [{'game_date': d, 'sport': str(r['x'].get('sport')),
                  'receipt_id': r['x']['id'],
                  'surface': str(r['x'].get('surface')),
                  'market': str(r['x'].get('market')),
                  'pick_label': str(r['x'].get('pick_label'))[:120],
                  'pick_side': r['side'], 'our_line': r['ours'],
                  'live_line': r['cur'], 'clv': r['clv'],
                  'live_captured_at': r['lv']['at'],
                  'checked_at': dt.datetime.now(dt.timezone.utc).isoformat()}
                 for r in rows]
        w = requests.post(f'{SB}/rest/v1/clv_shadow', headers=HW,
                          params={'on_conflict': 'receipt_id,checked_date'},
                          data=json.dumps(batch), timeout=120)
        if w.status_code in (200, 201, 204):
            print(f'\n  shadow: wrote {len(batch)} rows')
        else:
            print(f'\n  shadow write skipped: HTTP {w.status_code} '
                  f'{w.text[:160]}')
            print('  (apply supabase/migrations/20261019a_clv_shadow.sql)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
