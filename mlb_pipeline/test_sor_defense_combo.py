"""Do teams with BOTH a better SOR and a better defence cover more?

Andy 2026-10-09: "SO teams with stringer sor than their oppponetn and less
point allowed by defense (combo), do those teamss who have both of those
better or maybe a metric on yad alloswed if defense dvoa better than opponent,
do they ahve abetter dchance of covering spread?"

HOW THIS AVOIDS THE LEAK THAT RUINED THE FIRST ATTEMPT
team_computed_stats is upserted in place, so today's SOR already contains last
week's results — regressing it on last week's covers reads the outcome
(project_rolling_stats_leak_trap_929). team_stats_rolling_history, however,
carries dated snapshots. So for every game this takes the most recent snapshot
STRICTLY BEFORE kickoff and uses only that. Nothing in the predictor can know
the result.

THE METRICS, and why these ones
  sor                 strength of record — how impressive your results are
                      given your schedule
  def_epa_per_play    expected points added allowed per play. This is the
                      DVOA-family idea Andy asked for: it is efficiency, not
                      volume, so it does not punish a defence for being on the
                      field a lot. LOWER is better.
  points_allowed_pg   the plain-English version. LOWER is better.
  yds_allowed_pg      yards allowed, which Andy also asked about. Kept because
                      he asked, flagged because yards are the weakest of the
                      three — a defence can allow yards and not points.

WHAT IS REPORTED
Home-cover rate split four ways: neither edge, SOR edge only, defence edge
only, BOTH. Plus a 2-standard-error band on every cell, because the whole
point of asking "do they cover more" is whether the gap survives noise — and
a 10pp gap on 20 games does not.

WRITES NOTHING.

CLI
    python test_sor_defense_combo.py
    python test_sor_defense_combo.py --sport NCAAF --def-metric points_allowed_pg
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
RESULTS = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}
#: every one of these is "lower is better"
DEF_METRICS = ('def_epa_per_play', 'points_allowed_pg', 'yds_allowed_pg')


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:120]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cell(label, ys):
    n = len(ys)
    if not n:
        print(f'    {label:<30}      n=0')
        return
    p = statistics.fmean(ys) * 100
    se = (0.5 / n ** 0.5) * 100
    print(f'    {label:<30}{p:5.1f}%  n={n:<4} +/-{2 * se:4.1f}pp (2SE)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF')
    ap.add_argument('--def-metric', default='def_epa_per_play',
                    choices=DEF_METRICS)
    ap.add_argument('--sor-key', default='sor',
                    choices=('sor', 'sor_margin'), dest='sor_key',
                    # argparse runs help through %-formatting, so a literal
                    # percent must be doubled or add_argument raises
                    # "badly formed help string".
                    help="'sor' is win%%-based (8 snapshot dates); "
                         "'sor_margin' is the better opponent-adjusted "
                         "margin version but only has 4 dates so far")
    a = ap.parse_args()
    sport = a.sport.upper()
    dm = a.def_metric
    sor_key = a.sor_key

    hist = _page('team_stats_rolling_history',
                 {'select': 'team,stat_key,raw_value,snapshot_date',
                  'sport': f'eq.{sport}',
                  'stat_key': f'in.(sor,sor_margin,{dm})'})
    # 2026-10-09 · PER-METRIC LOOKUP. The first version demanded SOR and the
    # defence metric in the SAME snapshot, which discarded most games because
    # the two are snapshotted on different days (NCAAF sor: 8 dates from
    # 09-29; def_epa_per_play: 7 dates from 09-26, barely overlapping). Taking
    # the latest snapshot before kickoff for EACH metric independently keeps
    # the no-leak property — every value still predates the game — without
    # throwing away games for a bookkeeping coincidence.
    byk = collections.defaultdict(lambda: collections.defaultdict(dict))
    for x in hist:
        v = _f(x.get('raw_value'))
        if v is not None:
            byk[str(x['stat_key'])][str(x['snapshot_date'])[:10]][
                str(x['team'])] = v
    kdates = {k: sorted(d) for k, d in byk.items()}

    def latest_before(key, team, gd):
        for d in reversed(kdates.get(key, [])):
            if d < gd:
                v = byk[key][d].get(team)
                if v is not None:
                    return v
        return None

    dates = sorted({d for k in byk for d in kdates[k]})
    print(f'=== {sport} · defence metric: {dm} (lower is better)')
    print(f'    {len(hist)} history rows · {len(dates)} snapshot dates '
          f'{dates[0]}..{dates[-1]}')

    res = [x for x in _page(RESULTS[sport],
                            {'select': 'game_date,home_team,away_team,'
                                       'spread_result'})
           if str(x.get('spread_result') or '').lower()
           in ('home_covered', 'away_covered')]

    rows = []
    for g in res:
        gd = str(g['game_date'])[:10]
        h, aw = str(g['home_team']), str(g['away_team'])
        hs = latest_before(sor_key, h, gd)
        as_ = latest_before(sor_key, aw, gd)
        hd = latest_before(dm, h, gd)
        ad = latest_before(dm, aw, gd)
        if None in (hs, as_, hd, ad):
            continue
        rows.append({
            'y': 1 if str(g['spread_result']).lower() == 'home_covered' else 0,
            'sor_edge': hs > as_,
            'def_edge': hd < ad,                   # lower allowed = better
            'date': gd,
        })
    print(f'    {len(res)} games with a cover result · {len(rows)} have a '
          f'PRE-GAME snapshot with both teams and both metrics')
    if len(rows) < 40:
        print('    too few to report honestly. Not drawing a conclusion.')
        return 0

    base = statistics.fmean([r['y'] for r in rows]) * 100
    print(f'\n  HOME-COVER RATE  (base on this set {base:.1f}%)')
    cell('neither edge',
         [r['y'] for r in rows if not r['sor_edge'] and not r['def_edge']])
    cell('SOR edge only',
         [r['y'] for r in rows if r['sor_edge'] and not r['def_edge']])
    cell('defence edge only',
         [r['y'] for r in rows if not r['sor_edge'] and r['def_edge']])
    cell('BOTH edges  <-- the question',
         [r['y'] for r in rows if r['sor_edge'] and r['def_edge']])

    print('\n  AND THE MIRROR — the away side of the same games:')
    cell('away has BOTH edges',
         [1 - r['y'] for r in rows
          if not r['sor_edge'] and not r['def_edge']])

    both = [r['y'] for r in rows if r['sor_edge'] and r['def_edge']]
    neither = [r['y'] for r in rows
               if not r['sor_edge'] and not r['def_edge']]
    if both and neither:
        gap = (statistics.fmean(both) - statistics.fmean(neither)) * 100
        se = ((0.25 / len(both)) + (0.25 / len(neither))) ** 0.5 * 100
        print(f'\n  BOTH vs NEITHER: {gap:+.1f}pp · 2SE = {2 * se:.1f}pp · '
              f'{"BEYOND noise" if abs(gap) > 2 * se else "WITHIN noise"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
