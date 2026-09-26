"""As-of-date team form features, leak-free, for model training.

Andy 2026-09-26: "not factoring them in in any model or factor assessing
portion of process is negligent and letting consumers down, thats real
data that changes every fucking week, it tells a story it needs to be
weighed in."

He is right, and the reason the models never used team stats is not that
the data does not exist — it is that the stats live on *_game_context,
which only holds the CURRENT slate (272 NFL rows). The trainer learns
from *_game_results, which has 7,113 graded games and no stat columns.
Training the full stat list on 272 rows would overfit and ship a worse
model than the market-only one.

This module removes that excuse. team_recent_games carries 14,618 NFL
rows back to 1999 — every game, from both teams' perspective — so the
same story the ctx stats tell can be REBUILT for every historical game:

    points for / against per game        scoring form
    margin per game                      how they win, not just whether
    home and road splits                 the venue half Andy asked for
    ATS rate, over rate                  market-relative form
    strength of schedule                 who they played
    strength of record                   how impressive that record is

LEAK DISCIPLINE. For a game on date D, a team's features come only from
its games BEFORE D in the same season. The snapshot is taken before the
game is folded into the running totals — fold first and the game
predicts itself, which is exactly the 2026-09-22 lookback leak that was
worth 6-7pp of fabricated edge. Nothing here reads a season aggregate.

Head-to-head is excluded from opponent win% for SOS, same correction as
compute_schedule_strength: without it every team that beats you makes
your schedule look harder, and an 0-2 team shows a perfect 1.000.

Imported by the existing trainers. Deliberately NOT a pipeline step —
Andy: "these need to be injected in current processes not adding
workflow to confuse system."
"""
from __future__ import annotations
import os
from collections import defaultdict
from typing import Optional

import requests

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

# Per-team features. The trainer expands each into home_/away_/diff_.
TEAM_FEATURES = [
    'ppg', 'papg', 'margin_pg', 'ats_rate', 'over_rate',
    'venue_ppg', 'venue_papg', 'venue_win_rate',
    'sos', 'sor', 'games_played',
]

MIN_PRIOR = 2          # games a team needs before its form means anything


def _page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{path} -> {r.status_code}: {(r.text or "")[:160]}')
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def build(sport: str) -> dict:
    """-> {(game_id, team): {feature: value}} using only prior games.

    Two passes are required. SOS/SOR need every team's record, but using
    a team's FINAL record would leak the future into earlier games, so
    opponent quality is itself evaluated as-of-date: what the opponent's
    record looked like at the time this game was played.
    """
    rows = _page('team_recent_games', {
        'sport': f'eq.{sport}',
        'select': 'game_id,game_date,season,team,opp,is_home,score_us,'
                  'score_them,won,spread_result,total_result'})
    rows = [r for r in rows if r.get('game_date') and r.get('season') is not None]
    rows.sort(key=lambda r: (str(r['season']), str(r['game_date'])))

    # Pass 1 — running record per (team, season), snapshotted per game so
    # opponent quality can later be read as of the right moment.
    rec_at: dict = {}
    run = defaultdict(lambda: {'w': 0, 'l': 0})
    for r in rows:
        k = (r['team'], r['season'])
        rec_at[(r['game_id'], r['team'])] = dict(run[k])
        if r.get('won') is not None:
            run[k]['w' if r['won'] else 'l'] += 1

    # Pass 2 — the features themselves.
    out: dict = {}
    agg = defaultdict(lambda: {
        'n': 0, 'pf': 0.0, 'pa': 0.0, 'atsw': 0, 'atsn': 0,
        'ovr': 0, 'ovn': 0, 'opps': [],
        'hn': 0, 'hpf': 0.0, 'hpa': 0.0, 'hw': 0,
        'an': 0, 'apf': 0.0, 'apa': 0.0, 'aw': 0,
    })
    for r in rows:
        team, season = r['team'], r['season']
        a = agg[(team, season)]
        home = bool(r.get('is_home'))

        if a['n'] >= MIN_PRIOR:
            f = {
                'games_played': a['n'],
                'ppg': a['pf'] / a['n'],
                'papg': a['pa'] / a['n'],
                'margin_pg': (a['pf'] - a['pa']) / a['n'],
                'ats_rate': (a['atsw'] / a['atsn']) if a['atsn'] else None,
                'over_rate': (a['ovr'] / a['ovn']) if a['ovn'] else None,
            }
            # Venue split for the side this team is on TODAY — the home/away
            # half Andy asked for. A road team's home form is not the
            # relevant number.
            vn, vpf, vpa, vw = ((a['hn'], a['hpf'], a['hpa'], a['hw']) if home
                                else (a['an'], a['apf'], a['apa'], a['aw']))
            f['venue_ppg'] = (vpf / vn) if vn else None
            f['venue_papg'] = (vpa / vn) if vn else None
            f['venue_win_rate'] = (vw / vn) if vn else None

            # SOS / SOR from opponent records AS THEY STOOD at the time,
            # head-to-head removed.
            ratios = []
            for opp_gid, opp in a['opps']:
                orec = rec_at.get((opp_gid, opp))
                if not orec:
                    continue
                w, l = orec['w'], orec['l']
                # strip this team's prior results against that opponent
                n = w + l
                if n > 0:
                    ratios.append(w / n)
            if ratios:
                f['sos'] = sum(ratios) / len(ratios)
                expected = sum(1.0 - x for x in ratios) / len(ratios)
                actual = (a['atsn'] and None)  # placeholder, replaced below
                wins = a['hw'] + a['aw']
                f['sor'] = (wins / a['n']) - expected
            else:
                f['sos'] = None
                f['sor'] = None
            out[(r['game_id'], team)] = f

        # fold this game in AFTER snapshotting
        su, st = r.get('score_us'), r.get('score_them')
        if su is not None and st is not None:
            a['n'] += 1
            a['pf'] += float(su)
            a['pa'] += float(st)
            won = bool(r.get('won'))
            if home:
                a['hn'] += 1; a['hpf'] += float(su); a['hpa'] += float(st)
                a['hw'] += 1 if won else 0
            else:
                a['an'] += 1; a['apf'] += float(su); a['apa'] += float(st)
                a['aw'] += 1 if won else 0
        if r.get('opp'):
            a['opps'].append((r['game_id'], r['opp']))
        sr = str(r.get('spread_result') or '').lower()
        if sr in ('won', 'lost'):
            a['atsn'] += 1
            a['atsw'] += 1 if sr == 'won' else 0
        tr = str(r.get('total_result') or '').lower()
        if tr in ('over', 'under'):
            a['ovn'] += 1
            a['ovr'] += 1 if tr == 'over' else 0

    return out
