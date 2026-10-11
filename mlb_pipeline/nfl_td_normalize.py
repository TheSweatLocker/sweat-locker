"""B76b — de-vig a ONE-WAY anytime-TD market by normalising to the
expected number of distinct touchdown scorers.

THE PROBLEM. The book quotes anytime TD one-way: 26 outcomes for PHI @ JAX,
every one name='Yes', no 'No' counterpart anywhere (probed live, see
B76a / a3aa5088). A two-way de-vig needs both sides, so every row landed
with `devig='none'` and a NULL edge — the raw implied probability carries
the full overround and overstates every player.

THE FIX, and why it is principled rather than a fudge. The players in one
game are not independent two-way markets; they are a SET. The book's
overround is spread across that whole set, so the set is where it can be
removed. Sum every player's vigged probability on a team and it will
exceed the number of scorers that team is actually expected to produce.
Scale the set so the sum matches, and the scale factor IS the overround.

WHAT WE NORMALISE TO, measured not assumed. 716 team-games across 2025-26
with both a final score and player stats, counting DISTINCT players with
a rushing or receiving TD:

    team points    mean distinct scorers    n
      0-6                  0.00            35
      7-13                 0.92            99
     14-20                 1.56           164
     21-27                 2.19           200
     28-34                 2.82           136
     35+                   3.46            82

    E[scorers] = -0.0177 + 0.08917 * team_points     corr +0.792
    overall mean 2.03 per team-game

Implied team points come from the market itself — close_total and
close_spread — so the target is the book's own view of the game, not ours.
That matters: we are removing the vig, not injecting an opinion. Any
opinion belongs downstream in the model, against this fair baseline.

NOTE ON SCOPE. This returns P(at least one TD) per player, de-vigged. It
is NOT a first-TD probability — that needs drive order and possession
share, and honestly needs play-by-play we do not have (B76c/B76d).

READ-ONLY CLI. Shows what the normalisation does to today's board beside
the raw vigged numbers, so the correction is inspectable before anything
consumes it.

    python nfl_td_normalize.py --date 2026-10-11
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

import market_line as ML

#: E[distinct TD scorers] = _SCORERS_A + _SCORERS_B * team_points.
#: Fitted on 716 team-games, 2025-26, corr +0.792. See module docstring.
_SCORERS_A, _SCORERS_B = -0.0177, 0.08917

#: Sanity rails. A team-game has produced at most ~5 distinct scorers in
#: this sample; a target outside this range means the implied points are
#: wrong (a missing total, a sign error) and normalising to it would
#: silently rescale the whole slate.
_MIN_TARGET, _MAX_TARGET = 0.40, 5.00


def expected_scorers(team_points: float | None) -> float | None:
    if team_points is None:
        return None
    v = _SCORERS_A + _SCORERS_B * float(team_points)
    if not (_MIN_TARGET <= v <= _MAX_TARGET):
        return None
    return v


def implied_team_points(close_total, close_spread):
    """(home_pts, away_pts) from the market.

    NFL close_spread is POSITIVE when HOME is favoured — verified
    empirically on 65 graded 2026 games, where `home_margin -
    close_spread > 0` picked the home cover 30/30. Getting this backwards
    would hand the favourite's points to the underdog and rescale both
    teams the wrong way, which is the Red River inversion in a new costume.
    """
    try:
        t = float(close_total)
        s = float(close_spread)
    except (TypeError, ValueError):
        return None, None
    return (t + s) / 2.0, (t - s) / 2.0


def imp(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def normalise_market(outcomes: list, home_points, away_points) -> dict | None:
    """De-vig a whole anytime-TD market. {player_name: p_fair} or None.

    ══ IT MUST SEE THE WHOLE MARKET ══
    First cut of this normalised PER TEAM over the rows we had STORED, and
    on today's board it refused nearly every game with a scale factor near
    2.1. The cause was our own price band: nfl_generate_props admits only
    -250..+400, so 267 of 341 outcomes were dropped and the surviving 2-6
    players per team summed to far less than the ~2.8 scorers that team
    was expected to produce. Normalising a filtered subset to a whole
    team's target inflates every survivor.

    So the de-vig runs on the BOOK'S FULL OUTCOME LIST, before any band
    filter — the overround lives on the complete set and can only be taken
    off there. Filtering for publication happens afterwards, on numbers
    that are already fair.

    Game-level rather than per-team on purpose: outcomes carry a player
    name and a price but no team, and resolving 26 names to teams just to
    split a sum would add a roster lookup (and a cross-team failure mode)
    for no accuracy gain. The target is simply both teams' expectation.
    """
    hs, as_ = expected_scorers(home_points), expected_scorers(away_points)
    if hs is None or as_ is None:
        return None
    target = hs + as_
    raw = {}
    for o in outcomes or []:
        if str(o.get('name') or '').upper() not in ('YES', 'OVER'):
            continue
        nm = o.get('description')
        v = imp(o.get('price'))
        if nm and v is not None:
            raw[nm] = v
    s = sum(raw.values())
    if s <= 0 or len(raw) < 6:
        # Under ~6 listed players this is not the full market — almost
        # certainly an already-filtered list — and scaling it would
        # inflate whatever survived.
        return None
    k = target / s
    # k > 1 means the book's prices sum to LESS than the expected number
    # of scorers, i.e. negative overround, which does not happen. k very
    # small means the target is wrong (bad total or spread sign).
    if not (0.30 <= k <= 1.00):
        return None
    return {n: min(max(v * k, 0.001), 0.99) for n, v in raw.items()}


def normalise_team(players: list, team_points: float | None) -> dict | None:
    """Per-team variant, kept only for the read-only inspection CLI below.

    NOT used by the generator — see normalise_market for why a filtered
    per-team subset cannot be normalised honestly.
    """
    target = expected_scorers(team_points)
    if target is None:
        return None
    raw = {}
    for p in players:
        v = imp(p.get('odds'))
        if v is not None:
            raw[p['name']] = v
    s = sum(raw.values())
    if s <= 0 or not raw:
        return None
    k = target / s
    if not (0.25 <= k <= 1.05):
        return None
    return {n: min(max(v * k, 0.001), 0.99) for n, v in raw.items()}


def page(table, params, cap=20000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=120)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='2026-10-11')
    a = ap.parse_args()
    print(f'=== nfl_td_normalize · {a.date} · read-only ===')
    print(f'    E[scorers] = {_SCORERS_A:+.4f} {_SCORERS_B:+.5f} * pts '
          f'(716 team-games, corr +0.792)\n')

    ctx = {str(g['game_id']): g for g in page('nfl_game_context', {
        'select': 'game_id,home_team,away_team,close_total,close_spread',
        'game_date': f'eq.{a.date}'})}
    props = [p for p in page('nfl_pipeline_props', {
        'select': 'player_name,player_team,game_id,prop_type,'
                  'book_over_odds,result',
        'game_date': f'eq.{a.date}'})
        if str(p.get('prop_type')) == 'anytime_td_over']
    print(f'  {len(props)} anytime-TD rows across {len(ctx)} games')

    bygame = defaultdict(lambda: defaultdict(list))
    for p in props:
        if p.get('book_over_odds') is None:
            continue
        bygame[str(p.get('game_id'))][str(p.get('player_team'))].append(
            {'name': p['player_name'], 'odds': p['book_over_odds'],
             'result': p.get('result')})

    tot_raw = tot_fair = 0.0
    graded = []
    for gid, teams in sorted(bygame.items()):
        g = ctx.get(gid)
        if not g:
            continue
        hp, apts = implied_team_points(g.get('close_total'),
                                       g.get('close_spread'))
        print(f"\n  {g['away_team']} @ {g['home_team']}   total "
              f"{g.get('close_total')}  spread {g.get('close_spread')}  "
              f"-> implied {g['home_team']} {hp:.1f} / {g['away_team']} "
              f"{apts:.1f}" if hp is not None else
              f"\n  {g['away_team']} @ {g['home_team']}  (no total/spread)")
        for team, players in sorted(teams.items()):
            pts = hp if team == g.get('home_team') else apts
            fair = normalise_team(players, pts)
            rawsum = sum(x for x in (imp(p['odds']) for p in players)
                         if x is not None)
            if fair is None:
                print(f'    {team:<4} {len(players):>2} players · raw sum '
                      f'{rawsum:.2f} · NOT NORMALISED (target or factor '
                      f'out of range)')
                continue
            fsum = sum(fair.values())
            tot_raw += rawsum
            tot_fair += fsum
            print(f'    {team:<4} {len(players):>2} players · raw sum '
                  f'{rawsum:.2f} -> fair {fsum:.2f}  '
                  f'(vig removed {(1 - fsum / rawsum) * 100:.0f}%)')
            for p in sorted(players, key=lambda z: -(imp(z['odds']) or 0))[:3]:
                r = imp(p['odds'])
                print(f"        {p['name']:<22} {float(p['odds']):+5.0f}  "
                      f"raw {r * 100:5.1f}%  fair {fair[p['name']] * 100:5.1f}%")
            for p in players:
                if str(p.get('result') or '').upper() in ('WIN', 'LOSS'):
                    graded.append((1 if str(p['result']).upper() == 'WIN'
                                   else 0, imp(p['odds']), fair[p['name']]))

    if tot_raw:
        print(f'\n  slate: raw {tot_raw:.1f} -> fair {tot_fair:.1f}  '
              f'(mean overround {(1 - tot_fair / tot_raw) * 100:.1f}%)')
    if len(graded) >= 30:
        n = len(graded)
        hit = statistics.fmean(g[0] for g in graded) * 100
        rawm = statistics.fmean(g[1] for g in graded) * 100
        fairm = statistics.fmean(g[2] for g in graded) * 100
        print(f'\n  CALIBRATION on {n} settled rows:')
        print(f'    actual hit        {hit:5.1f}%')
        print(f'    raw vigged mean   {rawm:5.1f}%   gap {hit - rawm:+5.1f}')
        print(f'    de-vigged mean    {fairm:5.1f}%   gap {hit - fairm:+5.1f}')
        print('    -> the de-vigged mean should sit CLOSER to the actual')
        print('       hit rate. If it does, the normalisation is doing its')
        print('       job; the remaining gap is our edge, or our error.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
