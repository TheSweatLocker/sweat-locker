"""Does a team-stat advantage predict ATS, beyond what the line already knows?

Andy 2026-09-25: "I want to involve the stats now, that should be a must,
while studying when a team outranks this by this much in these statistical
categories, x team wins x percent of time."

WHAT THIS ANSWERS
For each stat, split games by how big the two teams' gap was BEFORE kickoff,
and report how often the better team covered. The honest target is ATS, not
straight-up wins: a team with a large stat edge is already favoured and the
spread has already moved to price it. Straight-up win rate for the better
team will look impressive and mean nothing. Only beating the CLOSING SPREAD
is evidence the stat carries information the market missed, so SU is
reported alongside purely as the "yes, the favourite wins" sanity column.

WHY IT DOES NOT READ team_stats_rolling
That table is the obvious source and it is unusable here. Every row carries
the same refreshed_at and only season 2026 exists — it is one current
season-to-date snapshot, not a per-date history. Backtesting 2026 games
against it would let a week-3 game be predicted by stats that already
contain that game's result. That is precisely the lookback leak found on
2026-09-22 (l5_hit_count=5 "predicting" 308-4), and it is worth 6-7pp of
fake edge. So every figure here is rebuilt from team_recent_games using
STRICTLY PRIOR games — for a game on date D, a team's stats come only from
its games before D, in the same season.

WHAT IT CAN AND CANNOT MEASURE
Leak-free and deep (30,656 NCAAF / 14,618 NFL rows, back to 2020):
    points per game, points allowed, scoring margin, ATS rate, over rate
Not available at all, because we only ever stored current snapshots:
    EPA, success rate, explosiveness, SP+
The second list is the more interesting one and the reason to start
archiving team_stats_rolling now — a snapshot per week would make those
measurable a season from today. Nothing can recover their history.

VALIDATION reuses mine_systems so a "system" cannot be born with softer
gates here than it would face there: sample floor, edge over the real 54.2%
breakeven, survival on held-out seasons, and Bonferroni across every bucket
tested. Buckets are declared up front rather than chosen after seeing the
data, because picking the winning bucket afterwards is how noise gets
published.

    python study_stat_edge.py --sport NCAAF
    python study_stat_edge.py --sport NFL --holdout-from 2024
"""
from __future__ import annotations
import argparse
import os
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

# Reuse the validation contract rather than restating it. If the gates move
# in mine_systems they must move here too — that is the point of importing.
from mine_systems import BREAKEVEN, MIN_N, MIN_OOS_N, MIN_EDGE_PP, ALPHA, binom_p, page  # noqa: E402


# Gap buckets, declared BEFORE looking at any outcome. Units are the stat's
# own (points per game for scoring stats, rate points for ATS/over rates).
GAP_BUCKETS = [(3.0, 7.0), (7.0, 14.0), (14.0, 999.0)]

STATS = {
    'ppg':        lambda s: s['pf'] / s['n'],
    'papg':       lambda s: -s['pa'] / s['n'],       # negated: higher = better
    'margin':     lambda s: (s['pf'] - s['pa']) / s['n'],
    'ats_rate':   lambda s: 100.0 * s['atsw'] / s['atsn'] if s['atsn'] else None,
    'over_rate':  lambda s: 100.0 * s['ovr'] / s['ovn'] if s['ovn'] else None,
}
MIN_PRIOR_GAMES = 3   # a team needs this many prior games before it has a stat


def load(sport: str) -> list:
    return page('team_recent_games', {
        'sport': f'eq.{sport}',
        'select': 'game_id,game_date,season,team,opp,is_home,score_us,'
                  'score_them,won,spread_line,spread_result,total_result',
    })


def build_asof(rows: list) -> dict:
    """(game_id, team) -> stat dict computed from that team's PRIOR games only."""
    by_team = defaultdict(list)
    for r in rows:
        if r.get('season') is None or not r.get('game_date'):
            continue
        by_team[(r['team'], r['season'])].append(r)
    out = {}
    for key, games in by_team.items():
        games.sort(key=lambda x: str(x['game_date']))
        run = {'n': 0, 'pf': 0.0, 'pa': 0.0, 'atsw': 0, 'atsn': 0, 'ovr': 0, 'ovn': 0}
        for g in games:
            # Snapshot BEFORE folding this game in — that ordering is the
            # whole guarantee. Fold first and the game predicts itself.
            if run['n'] >= MIN_PRIOR_GAMES:
                snap = {}
                for name, fn in STATS.items():
                    try:
                        snap[name] = fn(run)
                    except Exception:
                        snap[name] = None
                out[(g['game_id'], g['team'])] = snap
            su, st = g.get('score_us'), g.get('score_them')
            if su is not None and st is not None:
                run['n'] += 1
                run['pf'] += float(su)
                run['pa'] += float(st)
            sr = (g.get('spread_result') or '').lower()
            if sr in ('won', 'lost'):
                run['atsn'] += 1
                run['atsw'] += 1 if sr == 'won' else 0
            tr = (g.get('total_result') or '').lower()
            if tr in ('over', 'under'):
                run['ovn'] += 1
                run['ovr'] += 1 if tr == 'over' else 0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF')
    ap.add_argument('--holdout-from', type=int, default=2025,
                    help='seasons >= this are held out of the in-sample fit')
    args = ap.parse_args()

    rows = load(args.sport)
    print(f'=== stat-edge study · {args.sport} ===')
    print(f'  team_recent_games rows: {len(rows)}')
    asof = build_asof(rows)
    print(f'  leak-free as-of-date snapshots: {len(asof)}')

    # Pair the two team-perspectives of each game.
    by_game = defaultdict(list)
    for r in rows:
        by_game[r['game_id']].append(r)

    # buckets[(stat, lo, hi)] -> [ats_wins, ats_n, su_wins, su_n, oos_w, oos_n]
    buckets = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    paired = 0
    for gid, sides in by_game.items():
        if len(sides) != 2:
            continue
        a, b = sides
        sa, sb = asof.get((gid, a['team'])), asof.get((gid, b['team']))
        if not sa or not sb:
            continue
        paired += 1
        season = a.get('season') or 0
        for stat in STATS:
            va, vb = sa.get(stat), sb.get(stat)
            if va is None or vb is None:
                continue
            gap = va - vb
            better, other = (a, b) if gap > 0 else (b, a)
            agap = abs(gap)
            sr = (better.get('spread_result') or '').lower()
            won = better.get('won')
            for lo, hi in GAP_BUCKETS:
                if not (lo <= agap < hi):
                    continue
                k = (stat, lo, hi)
                if sr in ('won', 'lost'):
                    buckets[k][1] += 1
                    buckets[k][0] += 1 if sr == 'won' else 0
                    if season >= args.holdout_from:
                        buckets[k][5] += 1
                        buckets[k][4] += 1 if sr == 'won' else 0
                if won is not None:
                    buckets[k][3] += 1
                    buckets[k][2] += 1 if won else 0

    print(f'  games with both sides snapshotted: {paired}')
    tested = len(buckets)
    alpha_adj = ALPHA / max(1, tested)
    print(f'  buckets tested: {tested} · Bonferroni alpha: {alpha_adj:.5f}')
    print(f'  breakeven {BREAKEVEN}% · need >= {BREAKEVEN + MIN_EDGE_PP}% in-sample, '
          f'n >= {MIN_N}, holdout n >= {MIN_OOS_N}\n')

    print(f'{"STAT":<11}{"GAP":>12}{"ATS":>13}{"ATS%":>8}{"SU%":>7}'
          f'{"HOLDOUT":>12}{"p":>9}  VERDICT')
    survivors = []
    for (stat, lo, hi), (w, n, sw, sn, ow, on) in sorted(buckets.items()):
        if n == 0:
            continue
        pct = 100.0 * w / n
        supct = 100.0 * sw / sn if sn else 0.0
        oopct = 100.0 * ow / on if on else 0.0
        p = binom_p(w, n, BREAKEVEN / 100.0)
        fails = []
        if n < MIN_N:                      fails.append('n')
        if pct < BREAKEVEN + MIN_EDGE_PP:  fails.append('edge')
        if on < MIN_OOS_N:                 fails.append('oos-n')
        elif oopct < BREAKEVEN:            fails.append('oos')
        if p >= alpha_adj:                 fails.append('sig')
        verdict = 'SURVIVES' if not fails else 'fail:' + ','.join(fails)
        if not fails:
            survivors.append((stat, lo, hi, pct, n))
        hi_s = '+' if hi > 900 else f'-{hi:g}'
        print(f'{stat:<11}{f"{lo:g}{hi_s}":>12}{f"{w}-{n-w}":>13}{pct:>7.1f}%'
              f'{supct:>6.1f}%{f"{oopct:.1f}% n={on}":>12}{p:>9.4f}  {verdict}')

    print()
    if survivors:
        print(f'  {len(survivors)} bucket(s) cleared every gate:')
        for s, lo, hi, pct, n in survivors:
            print(f'    {s} gap {lo:g}-{hi:g}: {pct:.1f}% on n={n}')
        print('  Shadow before publishing — see feedback_suppression_gate_needs_shadow.')
    else:
        print('  0 buckets cleared every gate. The stat gaps are already priced '
              'into the spread,\n  which is the expected result and the reason '
              'to report it rather than keep hunting.')


if __name__ == '__main__':
    main()
