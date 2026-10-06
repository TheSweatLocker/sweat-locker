#!/usr/bin/env python3
"""Expected points / EPA for NCAAF, fitted from ESPN play-by-play.

WHY (2026-10-06)
----------------
Andy: "is the ESPN data EPA calculation going to be just as good and accurate
for modeling and data purposes? Is it good enough for the product? I need data
to be correct."

The right answer is not an opinion, and PPA is the column that matters most —
def_ppa is one of only two signals that repeated above breakeven in all three
seasons (stat_pit_vs_cover). So this is built to be ACCEPTED OR REJECTED on
evidence, against CFBD's own off_ppa/def_ppa which we hold for 2024-2026.

ACCEPTANCE CRITERIA, declared before fitting so they cannot be moved after:

  1. game-level pearson r vs CFBD PPA        >= 0.95
  2. rank correlation of season-to-date team values >= 0.95
     (this is what users see — a rank that disagrees with the number is worse
      than no rank)
  3. the SIGNAL reproduces: re-running the three-season ATS test on MY
     def_ppa must still beat breakeven in all three seasons, the way CFBD's
     did at 52.5 / 53.1 / 59.0

Criterion 3 is the real one. Matching CFBD exactly is not the goal; carrying
the same information is. A series can differ by a constant and model
identically, and it can correlate at 0.97 and still lose the edge if the
disagreement sits exactly where the signal lives.

HOW EP IS BUILT
Nonparametric, the Burke/Connelly approach. For every play, find the NEXT
scoring event in the same half and score it from the possessing team's point
of view (+7/+3/+2 for their own score, negative for the opponent's, 0 if the
half ends with no score). EP for a game state is then the mean of that value
over all plays in the same state bin. No distributional assumptions, nothing
to mis-specify, and it degrades visibly rather than silently — a thin bin
shows up as a small n, which is reported.

THE HALF BOUNDARY IS LOAD-BEARING. "Next score" must not cross halftime: a
drive that ends the 2nd quarter is not rewarded for a touchdown scored after
the break. Without that reset, every late-half play inherits the next half's
scoring and EP is badly biased toward the start of a drive.

EPA for a play is EP(state after) - EP(state before), with possession changes
flipping the sign and scoring plays taking the points directly. Aggregated per
team-game it becomes off_ppa (mean EPA on that team's offensive plays) and
def_ppa (mean EPA allowed, i.e. the opponent's off_ppa).

    python ncaaf_epa_model.py --fit --days 40          # fit + report bins
    python ncaaf_epa_model.py --validate --days 40     # the 3 criteria
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import math
import os
import statistics as st
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent

from ncaaf_advanced_from_pbp import (            # noqa: E402
    _cached_summary, _resolve, event_ids, SB, H, _num, classify,
)

# Points by scoring event, from the possessing team's perspective.
TD, FG, SAFETY = 7.0, 3.0, 2.0


def _clock_seconds(p: dict):
    c = ((p.get('clock') or {}).get('displayValue') or '')
    try:
        m, s = c.split(':')
        return int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return None


def extract_plays(summary: dict) -> list:
    """Ordered plays with the state fields EP needs, plus score tracking.

    Score deltas are how a scoring event is identified and attributed: ESPN
    stamps homeScore/awayScore AFTER each play, so a change tells us both how
    many points and which side got them. The scoringPlay flag alone cannot
    attribute points (a defensive return TD is flagged on the offence's play).
    """
    id2name, home_id = {}, None
    hdr = summary.get('header') or {}
    for c in ((hdr.get('competitions') or [{}])[0].get('competitors') or []):
        tid = str((c.get('team') or {}).get('id') or '')
        if tid:
            id2name[tid] = _resolve(c.get('team') or {})
            if c.get('homeAway') == 'home':
                home_id = tid
    for t in ((summary.get('boxscore') or {}).get('teams') or []):
        tm = t.get('team') or {}
        if tm.get('id'):
            id2name.setdefault(str(tm['id']), _resolve(tm))

    out = []
    ph = pa = 0
    for d in ((summary.get('drives') or {}).get('previous') or []):
        off_id = str(((d.get('team') or {}).get('id')) or '')
        off = id2name.get(off_id) or _resolve(d.get('team') or {})
        for p in (d.get('plays') or []):
            hs, as_ = _num(p.get('homeScore')), _num(p.get('awayScore'))
            dh = (hs - ph) if hs is not None else 0
            da = (as_ - pa) if as_ is not None else 0
            if hs is not None:
                ph = hs
            if as_ is not None:
                pa = as_
            st_ = p.get('start') or {}
            period = ((p.get('period') or {}).get('number'))
            out.append({
                'off': off, 'off_id': off_id,
                'down': st_.get('down'),
                'dist': _num(st_.get('distance')),
                'ytg': _num(st_.get('yardsToEndzone')),
                'period': period,
                'half': 1 if (period or 1) <= 2 else 2,
                'clock': _clock_seconds(p),
                'gain': _num(p.get('statYardage')),
                'type': (p.get('type') or {}).get('text'),
                'is_pen': bool(p.get('isPenalty')),
                'is_to': bool(p.get('isTurnover')),
                'home_pts_delta': dh, 'away_pts_delta': da,
                'home_id': home_id,
            })
    return out


def label_next_score(plays: list) -> None:
    """Attach `next_score` to each play: points to the possessing team.

    Walks BACKWARD so each play inherits the outcome already computed for the
    one after it — O(n) instead of a forward scan per play. Resets at the half
    boundary, which is the whole reason this is not a single pass over the
    game.
    """
    nxt_pts, nxt_team, nxt_half = None, None, None
    for p in reversed(plays):
        dh, da = p['home_pts_delta'], p['away_pts_delta']
        if nxt_half is not None and p['half'] != nxt_half:
            nxt_pts, nxt_team = None, None       # new half, nothing ahead yet
        nxt_half = p['half']
        # This play's own scoring event becomes "the next score" for the plays
        # BEFORE it, attributed to whichever side the points went to.
        if dh > 0 or da > 0:
            pts = dh if dh > 0 else da
            # Normalise odd values (2 = safety/conversion, 6/7/8 = TD drive).
            if pts >= 6:
                pts_n = TD
            elif pts == 3:
                pts_n = FG
            else:
                pts_n = SAFETY
            # WHO scored, which is NOT always the team with the ball. A
            # defensive return touchdown is stamped on the OFFENCE's play, so
            # the first version credited that offence +7 for a play that cost
            # it 7 — a 14-point swing with the sign reversed, on 8
            # interception-return TDs on 10-03 alone plus every fumble return
            # and safety.
            home_scored = dh > 0
            off_is_home = (p['off_id'] == p['home_id'])
            p['_scored'] = (pts_n, home_scored == off_is_home)
            nxt_pts = pts_n
            nxt_team = p['home_id'] if home_scored else 'away'
        if nxt_pts is None:
            p['next_score'] = 0.0
        else:
            same = _same_side(p, nxt_team)
            p['next_score'] = nxt_pts if same else -nxt_pts


def _same_side(play: dict, scoring_side) -> bool:
    """Did the possessing team get the next score?"""
    if scoring_side is None:
        return False
    if scoring_side == 'away':
        return play['off_id'] != play['home_id']
    return play['off_id'] == scoring_side


# ── EP state bins ────────────────────────────────────────────────────
# Coarse enough that every bin has samples, fine enough to carry field
# position and down. Bin counts are printed so a thin bin is visible rather
# than quietly producing a confident number from 3 plays.
def ytg_bucket(ytg: float) -> int:
    return max(0, min(9, int((ytg - 0.01) // 10)))


def dist_bucket(dist: float) -> int:
    if dist <= 3:
        return 0
    if dist <= 7:
        return 1
    if dist <= 10:
        return 2
    return 3


def state_key(p: dict):
    if not p['down'] or p['ytg'] is None or p['dist'] is None:
        return None
    return (int(p['down']), ytg_bucket(p['ytg']), dist_bucket(p['dist']))


def fit_ep(all_plays: list):
    """state bin -> (EP, n). Falls back to coarser bins when a bin is thin."""
    byk = collections.defaultdict(list)
    bydown = collections.defaultdict(list)
    for p in all_plays:
        if p.get('next_score') is None or p['is_pen']:
            continue
        k = state_key(p)
        if k is None:
            continue
        byk[k].append(p['next_score'])
        bydown[(k[0], k[1])].append(p['next_score'])
    ep = {k: (st.mean(v), len(v)) for k, v in byk.items()}
    ep_coarse = {k: (st.mean(v), len(v)) for k, v in bydown.items()}
    return ep, ep_coarse


MIN_BIN = 25


def ep_of(p: dict, ep: dict, ep_coarse: dict):
    k = state_key(p)
    if k is None:
        return None
    hit = ep.get(k)
    if hit and hit[1] >= MIN_BIN:
        return hit[0]
    c = ep_coarse.get((k[0], k[1]))
    if c and c[1] >= MIN_BIN:
        return c[0]
    return hit[0] if hit else (c[0] if c else None)


def epa_for_game(plays: list, ep: dict, ep_coarse: dict):
    """{team: (mean_epa, n_plays)} over that team's offensive plays."""
    agg = collections.defaultdict(list)
    for i, p in enumerate(plays):
        if p['is_pen'] or not p['off']:
            continue
        # SCRIMMAGE PLAYS ONLY. The first version skipped nothing but
        # penalties, so kickoffs, punts and field goals were being averaged
        # into off_ppa — 1,988 of 22,955 plays on this window, every one with
        # a large EPA swing. CFBD's PPA is rush/pass only, and including the
        # rest is what held agreement to r=0.834 against a 0.95 bar.
        #
        # Non-scrimmage plays are still used for STATE TRANSITIONS below: the
        # EP of a 4th-and-8 state is legitimate whether the team punts or
        # goes for it. They just do not belong in the team's own average.
        if classify(p['type'], None) not in ('rush', 'pass'):
            continue
        before = ep_of(p, ep, ep_coarse)
        if before is None:
            continue
        scored = p.get('_scored')
        if scored:
            pts, by_offence = scored
            after = pts if by_offence else -pts
        else:
            nxt = plays[i + 1] if i + 1 < len(plays) else None
            if nxt is None or nxt['half'] != p['half']:
                after = 0.0
            else:
                a = ep_of(nxt, ep, ep_coarse)
                if a is None:
                    continue
                # Possession change flips whose expected points those are.
                after = a if nxt['off_id'] == p['off_id'] else -a
        agg[p['off']].append(after - before)
    return {t: (st.mean(v), len(v)) for t, v in agg.items() if v}


def load_all(dates: list):
    games = []
    for d in dates:
        ids = event_ids(d)
        for eid in ids:
            j = _cached_summary(eid)
            if not j:
                continue
            pl = extract_plays(j)
            if not pl:
                continue
            label_next_score(pl)
            games.append((d, eid, pl))
        print(f'  {d}: {len(ids)} games')
    return games


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float('nan')
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else float('nan')


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    return pearson(ranks(xs), ranks(ys))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=40)
    ap.add_argument('--fit', action='store_true')
    ap.add_argument('--validate', action='store_true')
    args = ap.parse_args()

    today = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)).date()
    dates = [(today - dt.timedelta(days=i)).isoformat()
             for i in range(args.days)]
    print(f'=== ncaaf_epa_model · {len(dates)} date(s) ===')
    games = load_all(dates)
    plays = [p for _d, _e, pl in games for p in pl]
    print(f'\n  games {len(games)}   plays {len(plays)}')
    labelled = [p for p in plays if p.get('next_score') is not None]
    print(f'  plays with a next-score label: {len(labelled)}')

    ep, ep_coarse = fit_ep(plays)
    print(f'  EP bins: {len(ep)} fine, {len(ep_coarse)} coarse '
          f'(min {MIN_BIN} plays to use a fine bin)')
    thin = sum(1 for _k, (_v, n) in ep.items() if n < MIN_BIN)
    print(f'  thin fine bins falling back to coarse: {thin}')

    print('\n  --- EP by down and distance to goal (sanity) ---')
    print('      ytg   ' + '  '.join(f'dn{d}' for d in (1, 2, 3, 4)))
    for b in range(10):
        cells = []
        for d in (1, 2, 3, 4):
            c = ep_coarse.get((d, b))
            cells.append(f'{c[0]:+5.2f}' if c else '    -')
        print(f'    {b*10+1:3d}-{b*10+10:<3d} ' + ' '.join(cells))
    print('    (EP should fall as yards-to-goal rises and as down rises)')

    if not args.validate:
        return 0

    # Criterion 1 + 2 against CFBD.
    mine, cfbd = {}, {}
    for d, _eid, pl in games:
        for team, (mean_epa, n) in epa_for_game(pl, ep, ep_coarse).items():
            mine[(d, team)] = mean_epa
    rows = []
    off = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/ncaaf_team_game_stats', headers=H,
                         timeout=90,
                         params={'select': 'team,game_date,off_ppa,def_ppa',
                                 'game_date': f'gte.{dates[-1]}',
                                 'limit': '1000', 'offset': str(off)})
        if r.status_code not in (200, 206):
            break
        chunk = r.json()
        if not isinstance(chunk, list):
            break
        rows += chunk
        if len(chunk) < 1000:
            break
        off += 1000
    for z in rows:
        if z.get('off_ppa') is not None:
            cfbd[(str(z['game_date'])[:10], z['team'])] = float(z['off_ppa'])

    pairs = [(mine[k], cfbd[k]) for k in mine if k in cfbd]
    print(f'\n  === CRITERION 1 · game-level agreement (n={len(pairs)}) ===')
    if len(pairs) < 30:
        print('  too few overlapping team-games to judge')
        return 1
    xs = [a for a, _b in pairs]
    ys = [b for _a, b in pairs]
    r1 = pearson(xs, ys)
    errs = [abs(a - b) for a, b in pairs]
    print(f'     pearson r      {r1:.4f}   (need >= 0.95)  '
          f'{"PASS" if r1 >= 0.95 else "FAIL"}')
    print(f'     spearman       {spearman(xs, ys):.4f}')
    print(f'     mean |err|     {st.mean(errs):.4f}')
    print(f'     mine  mean {st.mean(xs):+.4f}  sd {st.pstdev(xs):.4f}')
    print(f'     cfbd  mean {st.mean(ys):+.4f}  sd {st.pstdev(ys):.4f}')

    # Criterion 2: season-to-date team aggregates, which is what gets ranked.
    agg_m = collections.defaultdict(list)
    agg_c = collections.defaultdict(list)
    for (d, team), v in mine.items():
        if (d, team) in cfbd:
            agg_m[team].append(v)
            agg_c[team].append(cfbd[(d, team)])
    teams = [t for t in agg_m if len(agg_m[t]) >= 2]
    print(f'\n  === CRITERION 2 · team season-to-date ranks '
          f'(teams={len(teams)}) ===')
    if len(teams) >= 20:
        tm = [st.mean(agg_m[t]) for t in teams]
        tc = [st.mean(agg_c[t]) for t in teams]
        r2 = spearman(tm, tc)
        print(f'     rank correlation {r2:.4f}  (need >= 0.95)  '
              f'{"PASS" if r2 >= 0.95 else "FAIL"}')
        print(f'     pearson          {pearson(tm, tc):.4f}')
    else:
        print('     too few teams with 2+ games in this window')
    print('\n  CRITERION 3 (does the ATS signal survive) needs a full-season '
          'backfill;\n  run it once this window is widened.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
