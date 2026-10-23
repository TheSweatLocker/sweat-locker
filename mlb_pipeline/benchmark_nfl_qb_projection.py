"""Is our QB projection better than the vendors we already ingest?

ANDY 2026-10-22: "double checking QB projections — do we take into account
sleeper data that i know we have and their projections as well as other
items or is it pretty basic?"

It does not use them, and it is fairly basic: a recency-weighted baseline
times opponent positional defence, the player's home/away split, the game
total and the game script. It ignores snap share, target share, air yards,
EPA, pressure rate, weather and teammate injuries, all of which are either
in nfl_player_stats already or a pull away.

Before adding any of that, the question worth answering is whether the thing
we have beats the two projections we already receive nightly for free
(espn_fantasy and sleeper, 15,356 rows). If it does not, the honest move is
to say so rather than to surface ours as proprietary insight.

╔══ WHAT THIS CAN AND CANNOT MEASURE HONESTLY ═════════════════════════════╗
LEAK-FREE: the baseline. before_week() restricts each player's history to
weeks strictly before the one being projected, so a week-4 baseline uses
weeks 1-3 and nothing else.

NOT LEAK-FREE: the opponent factor. nfl_positional_defense is a CURRENT
snapshot refreshed nightly with no history - there is no way to ask what a
defence looked like after week 3. Projecting week 4 against it uses a rating
that includes week 4 itself.

So this reports BOTH:
    baseline only   strictly as-of, directly comparable to the vendors
    full model      flattered by the defence leak, an UPPER BOUND

Quoting the full-model number alone would be the leaky-backtest mistake that
made 65-79% ATS look real (project_models_dont_beat_the_close_1005). The
baseline column is the one to believe.

The vendors are not leak-free either in the strictest sense - they are
whatever the feed held when pulled - but they are a genuine ex-ante
projection for that week, which is the comparison that matters.

CLI
    python benchmark_nfl_qb_projection.py
    python benchmark_nfl_qb_projection.py --weeks 4,5,6
"""
from __future__ import annotations

import argparse
import collections
import os
import statistics
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_player_projection import (          # noqa: E402
    page, _f, baseline, before_week, opp_factor, home_factor, env_factor,
    trail_factor, STAT_COL,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                          # noqa: BLE001
        pass

SEASON = 2026
STAT = 'proj_pass_yds'
ACTUAL_COL = 'passing_yards'


def mae(pairs):
    return statistics.fmean(abs(p - a) for p, a in pairs) if pairs else None


def bias(pairs):
    return statistics.fmean(p - a for p, a in pairs) if pairs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--weeks', default='4,5,6')
    a = ap.parse_args()
    weeks = [int(w) for w in a.weeks.split(',') if w.strip()]

    stats = page('nfl_player_stats',
                 {'select': '*', 'season': f'eq.{SEASON}', 'position': 'eq.QB'})
    defrows = [r for r in page('nfl_positional_defense', {'select': '*'})
               if int(r.get('season') or 0) == SEASON]
    ctx = page('nfl_game_context', {'select': '*', 'season': f'eq.{SEASON}'})
    vend = [r for r in page('nfl_player_projections',
                            {'select': '*', 'season': f'eq.{SEASON}',
                             'position': 'eq.QB'})]

    # schedule: (week, team) -> (is_home, opp, total, spread_for_team)
    sched = {}
    tot_all = []
    for g in ctx:
        try:
            wk = int(g.get('week'))
        except (TypeError, ValueError):
            continue
        h, aw = str(g.get('home_team') or ''), str(g.get('away_team') or '')
        t, sp = _f(g.get('close_total')), _f(g.get('close_spread'))
        if t:
            tot_all.append(t)
        if h:
            sched[(wk, h)] = (True, aw, t, sp)
        if aw:
            sched[(wk, aw)] = (False, h, t,
                               (-sp if sp is not None else None))
    league_total = statistics.fmean(tot_all) if tot_all else None

    by_player = collections.defaultdict(list)
    for r in stats:
        nm = r.get('player_display_name') or r.get('player_name')
        try:
            r['_week'] = int(r.get('week'))
        except (TypeError, ValueError):
            continue
        if nm:
            by_player[nm].append(r)
    for nm in by_player:
        by_player[nm].sort(key=lambda x: -x['_week'])

    vend_idx = collections.defaultdict(dict)
    for r in vend:
        try:
            wk = int(r.get('week'))
        except (TypeError, ValueError):
            continue
        v = _f(r.get('proj_pass_yds'))
        if v is not None:
            vend_idx[(wk, r.get('player_name'))][r.get('source')] = v

    rows_base, rows_full = [], []
    rows_vendor = collections.defaultdict(list)
    rows_vendor_avg = []
    n_games = 0

    for wk in weeks:
        for nm, games in by_player.items():
            actual = next((_f(g.get(ACTUAL_COL)) for g in games
                           if g.get('_week') == wk), None)
            if actual is None:
                continue
            team = next((str(g.get('team')) for g in games
                         if g.get('_week') == wk), None)
            s = sched.get((wk, team))
            if not s:
                continue
            is_home, opp, close_total, sp_for = s
            hist = before_week(games, wk)
            base, _note, ok = baseline(hist, STAT_COL[STAT])
            if not ok:
                continue
            n_games += 1
            rows_base.append((base, actual))

            val = base
            for fac, _n, applied in (
                    opp_factor(defrows, opp, STAT),
                    home_factor(hist, STAT_COL[STAT], is_home),
                    env_factor(close_total, league_total, STAT),
                    trail_factor(sp_for, STAT)):
                if applied and fac is not None:
                    val *= fac
            rows_full.append((val, actual))

            vs = vend_idx.get((wk, nm), {})
            for src, v in vs.items():
                rows_vendor[src].append((v, actual))
            if vs:
                rows_vendor_avg.append((statistics.fmean(vs.values()), actual))

    print(f'=== QB passing yards · weeks {weeks} · season {SEASON} ===')
    print(f'    {n_games} player-games with a usable prior baseline\n')
    print(f'{"MODEL":<34}{"n":>5}{"MAE":>9}{"bias":>9}')
    print('-' * 57)

    def line(label, pairs, note=''):
        if not pairs:
            print(f'{label:<34}{0:>5}{"—":>9}{"—":>9}  {note}')
            return
        print(f'{label:<34}{len(pairs):>5}{mae(pairs):>9.1f}'
              f'{bias(pairs):>+9.1f}  {note}')

    line('OURS · baseline only', rows_base, 'leak-free, as-of')
    line('OURS · full model', rows_full, 'OPTIMISTIC — defence leak')
    for src in sorted(rows_vendor):
        line(f'vendor · {src}', rows_vendor[src])
    line('vendor · mean of both', rows_vendor_avg)

    # ── PAIRED on identical (week, player) keys ──────────────────────
    # An MAE table where each model covers a different set of games can be
    # won by coverage rather than by accuracy. This restricts to the games
    # EVERY model projected, so the comparison cannot be explained by n.
    print('paired on the games every model covered:')
    keyed_ours, keyed_vend = {}, collections.defaultdict(dict)
    for wk in weeks:
        for nm, games in by_player.items():
            actual = next((_f(g.get(ACTUAL_COL)) for g in games
                           if g.get('_week') == wk), None)
            if actual is None:
                continue
            hist = before_week(games, wk)
            base, _n2, ok = baseline(hist, STAT_COL[STAT])
            if not ok:
                continue
            keyed_ours[(wk, nm)] = (base, actual)
            for src, v in vend_idx.get((wk, nm), {}).items():
                keyed_vend[src][(wk, nm)] = (v, actual)
    srcs = sorted(keyed_vend)
    common = set(keyed_ours)
    for src in srcs:
        common &= set(keyed_vend[src])
    if not common:
        print('   no games covered by every model')
        return 0
    print(f'   n = {len(common)} player-games')
    print('')
    print(f'   {"MODEL":<30}{"MAE":>9}{"bias":>9}')
    print('   ' + '-' * 48)
    ours = [keyed_ours[k] for k in common]
    print(f'   {"OURS · baseline only":<30}{mae(ours):>9.1f}{bias(ours):>+9.1f}')
    for src in srcs:
        v = [keyed_vend[src][k] for k in common]
        print(f'   {"vendor · " + src:<30}{mae(v):>9.1f}{bias(v):>+9.1f}')
    both = [(statistics.fmean([keyed_vend[s][k][0] for s in srcs]),
             keyed_ours[k][1]) for k in common]
    print(f'   {"vendor · mean of both":<30}{mae(both):>9.1f}{bias(both):>+9.1f}')
    blend = [(0.5 * keyed_ours[k][0]
              + 0.5 * statistics.fmean([keyed_vend[s][k][0] for s in srcs]),
              keyed_ours[k][1]) for k in common]
    print(f'   {"50/50 ours + vendors":<30}{mae(blend):>9.1f}{bias(blend):>+9.1f}')
    print()
    # ── IS THE GAP REAL, OR IS IT 82 GAMES OF NOISE? ────────────────
    # feedback_sample_size_with_pct: every number gets its n, and a
    # difference gets a band. These are PAIRED errors on the same games, so
    # the per-game difference is the right unit - it cancels the game-to-game
    # variance that dominates both models.
    import math
    for src in srcs:
        diffs = [abs(keyed_ours[k][0] - keyed_ours[k][1])
                 - abs(keyed_vend[src][k][0] - keyed_vend[src][k][1])
                 for k in common]
        m = statistics.fmean(diffs)
        sd = statistics.pstdev(diffs) if len(diffs) > 1 else 0.0
        se = sd / math.sqrt(len(diffs)) if diffs else 0.0
        lo, hi = m - 1.96 * se, m + 1.96 * se
        verdict = ('WE ARE WORSE' if lo > 0 else
                   'WE ARE BETTER' if hi < 0 else
                   'INDISTINGUISHABLE at this n')
        print(f'   ours - {src}: {m:+.1f} yd MAE  95% CI [{lo:+.1f}, {hi:+.1f}]'
              f'  -> {verdict}')
    print()
    best = min([('ours', mae(ours)), ('vendors', mae(both)),
                ('blend', mae(blend))], key=lambda x: x[1])
    print(f'   lowest MAE: {best[0]} ({best[1]:.1f} yards)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
