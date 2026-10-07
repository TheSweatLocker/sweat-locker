#!/usr/bin/env python3
"""The Fade — track what fading the public ATS side actually does. All sports.

WHY (2026-10-07)
----------------
Andy: "I want to get this going for all sports, that's the point, we have the
money flow information. It doesn't even have to be proven, we can just label
what it is — The Fade — and what it does. We are just tracking the record
honestly."

That is the right frame and it changes what this is. This is NOT a claim of
edge. It is a graded record of a rule someone else's money defines, published
whatever it says. Same discipline as publishing our own losses, except the
picks are not ours, so there is no incentive to flatter them.

THE RULE, stated plainly so a user can check it:
  * ATS markets only (spread / run line). NOT moneyline — measured, the public
    ML side WINS: NCAAF 84.6-92.6% and MLB 58.4-61.8% at >=70% of tickets,
    because the public backs heavy favourites and favourites win games. That
    is the public being right at a bad price, which a hit-rate fade cannot
    see. Fading it would be a disaster.
  * Take the side holding >= THRESHOLD of TICKETS (not handle).
  * Bet the other side. Score at -110. Pushes are 0 units, not losses.
  * Latest pre-game capture per (game, side) — the archive scrapes a game
    repeatedly and counting every capture would weight busy games.

THE NFL GAME_ID TRAP — this is why NFL looked like missing data.
public_splits_archive keys NFL by a 32-char hash while nfl_game_results uses
season_week_away_home:

    splits            fc362aff0d889ec52d358307a70c32ed
    nfl_game_results  2024_18_SEA_LA
    overlap           ZERO

19,512 NFL split rows existed and joined to nothing. I reported "we have no
NFL split data" rather than asking why the join was empty. nfl_game_context
carries BOTH the hash and the teams/date, so it bridges — all 77 NFL split
game_ids resolve through it. Every sport here goes through the same
bridge-then-fallback path so this cannot silently drop a sport again, and the
join loss is REPORTED at every hop.

    python compute_fade_records.py                 # report only
    python compute_fade_records.py --apply         # write surface_records
    python compute_fade_records.py --threshold 60
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

JUICE = -110
ATS_MARKETS = ('rl', 'spread', 'ats')

# sport -> (results table, context table used to bridge a hash id)
SPORTS = {
    'MLB':   ('mlb_game_results',   'mlb_game_context'),
    'NFL':   ('nfl_game_results',   'nfl_game_context'),
    'NCAAF': ('ncaaf_game_results', 'ncaaf_game_context'),
    'NHL':   ('nhl_game_results',   'nhl_game_context'),
    'NBA':   ('nba_game_results',   'nba_game_context'),
}
SOURCES = [('oc', 'oc_bets_pct'), ('ftp', 'ftp_bets_pct'),
           ('cz', 'cz_bets_pct'), ('fr', 'fr_bettors_pct')]


def page(table, params):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q,
                         timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code}: {r.text[:160]}')
            return out
        chunk = r.json()
        if not isinstance(chunk, list):
            print(f'  ! {table} returned a non-list payload')
            return out
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def load_sport(sport):
    """(by_gameid, by_teams, bridge) for one sport. Each maps to an ATS result.

    by_gameid   result table's own id -> (spread_result, game_date)
    by_teams    (date, home, away)    -> (spread_result, game_date)
    bridge      context hash id       -> (date, home, away)
    """
    res_tbl, ctx_tbl = SPORTS[sport]
    by_gid, by_teams = {}, {}
    for g in page(res_tbl, {'select': 'game_id,game_date,home_team,away_team,'
                                      'spread_result'}):
        sr = str(g.get('spread_result') or '')
        if sr not in ('home_covered', 'away_covered', 'push'):
            continue
        d = str(g.get('game_date'))[:10]
        rec = (sr, d)
        if g.get('game_id'):
            by_gid[str(g['game_id'])] = rec
        by_teams[(d, str(g.get('home_team')), str(g.get('away_team')))] = rec
    bridge = {}
    for z in page(ctx_tbl, {'select': 'game_id,game_date,home_team,away_team'}):
        if z.get('game_id'):
            bridge[str(z['game_id'])] = (str(z.get('game_date'))[:10],
                                         str(z.get('home_team')),
                                         str(z.get('away_team')))
    return by_gid, by_teams, bridge


def resolve(gid, by_gid, by_teams, bridge):
    """Direct id first, then the context bridge. Returns (result, date, how)."""
    hit = by_gid.get(gid)
    if hit:
        return hit[0], hit[1], 'direct'
    key = bridge.get(gid)
    if key:
        hit = by_teams.get(key)
        if hit:
            return hit[0], hit[1], 'bridged'
        return None, None, 'bridged-but-ungraded'
    return None, None, 'no-context-row'


def unit(fade_won):
    if fade_won is None:
        return 0.0
    return (100.0 / abs(JUICE)) if fade_won else -1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--threshold', type=float, default=65.0,
                    help='minimum %% of TICKETS on the public side')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    thr = args.threshold

    print(f'=== The Fade · ATS only · public >= {thr:.0f}% of tickets · '
          f'{"APPLY" if args.apply else "REPORT"} ===\n')

    splits = [r for r in page('public_splits_archive',
                              {'select': 'game_id,sport,market,pick_side,'
                                         'captured_at,cz_bets_pct,'
                                         'fr_bettors_pct,ftp_bets_pct,'
                                         'oc_bets_pct'})
              if str(r.get('market')) in ATS_MARKETS]
    print(f'ATS split rows: {len(splits)}')
    by_sport = collections.defaultdict(list)
    for r in splits:
        by_sport[str(r.get('sport'))].append(r)
    print(f'  by sport: {dict((k, len(v)) for k, v in sorted(by_sport.items()))}\n')

    rows_out = []
    for sport in sorted(by_sport):
        if sport not in SPORTS:
            print(f'{sport}: no results table registered — skipped\n')
            continue
        by_gid, by_teams, bridge = load_sport(sport)

        # latest capture per (game, side)
        latest = {}
        for r in by_sport[sport]:
            k = (str(r.get('game_id')), str(r.get('pick_side')))
            cur = latest.get(k)
            if cur is None or str(r.get('captured_at')) > str(cur.get('captured_at')):
                latest[k] = r

        how = collections.Counter()
        graded = []
        for (gid, side), r in latest.items():
            sr, gdate, mode = resolve(gid, by_gid, by_teams, bridge)
            how[mode] += 1
            if sr is None:
                continue
            pcts = []
            for name, col in SOURCES:
                try:
                    pcts.append((name, float(r.get(col))))
                except (TypeError, ValueError):
                    continue
            if not pcts:
                continue
            if sr == 'push':
                fade = None
            else:
                public_won = ((sr == 'home_covered') if side == 'HOME'
                              else (sr == 'away_covered'))
                fade = not public_won
            mean = sum(v for _n, v in pcts) / len(pcts)
            graded.append({'date': gdate, 'mean': mean, 'fade': fade,
                           'per_src': pcts})

        qual = [g for g in graded if g['mean'] >= thr]
        w = sum(1 for g in qual if g['fade'] is True)
        l = sum(1 for g in qual if g['fade'] is False)
        p = sum(1 for g in qual if g['fade'] is None)
        n = w + l
        u = sum(unit(g['fade']) for g in qual)
        print(f'{sport}')
        print(f'  latest-capture game/side rows : {len(latest)}')
        print(f'  id resolution                 : {dict(how)}')
        print(f'  graded                        : {len(graded)}')
        print(f'  qualifying (>= {thr:.0f}% tickets)   : {len(qual)}')
        if n:
            print(f'  THE FADE  {w}-{l}' + (f'-{p}' if p else '')
                  + f'   {w/n*100:.1f}%   {u:+.2f}u   '
                    f'ROI {u/len(qual)*100:+.1f}%   n={n}')
            dates = sorted(g['date'] for g in qual if g['date'])
            rows_out.append({
                'sport': sport,
                'surface': 'fade_public_ats',
                'window_key': f'tickets_{int(thr)}pct',
                'wins': w, 'losses': l, 'pushes': p,
                'picks_count': len(qual),
                'hit_rate': round(w / n * 100, 2),
                'units_net': round(u, 2),
                'roi_pct': round(u / len(qual) * 100, 2),
                'epoch_start': dates[0] if dates else None,
                'last_pick_date': dates[-1] if dates else None,
                'last_computed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            })
        else:
            print(f'  THE FADE  no graded qualifying games')
        print()

    print(f'{"="*70}')
    print('Breakeven at -110 is 52.38%. Pushes count 0 units, not losses.')
    print('This is a TRACKED RECORD of a public-money rule, not a claim of')
    print('edge. Small n is labelled, never hidden.')
    print(f'{"="*70}\n')

    if not args.apply:
        print(f'{len(rows_out)} surface_records row(s) would be written. '
              f'Re-run with --apply.')
        return 0
    if not rows_out:
        print('nothing to write')
        return 0
    r = requests.post(f'{SB}/rest/v1/surface_records'
                      f'?on_conflict=sport,surface,window_key',
                      headers=H_W, timeout=60, data=json.dumps(rows_out))
    if r.status_code in (200, 201, 204):
        print(f'wrote {len(rows_out)} surface_records row(s)')
        return 0
    print(f'write failed {r.status_code}: {r.text[:300]}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
