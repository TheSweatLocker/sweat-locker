#!/usr/bin/env python3
"""Is the slate that is about to LOCK actually ready? One report, per sport.

== 2026-10-14 - WHY THIS EXISTS ==
ANDY: "Fix all issues and make sure end to end everything working i need models
and overall engine updated before picks lock tomorrow for ncaaf and nfl ...
ensure all data is good to go and models are newly tuned."

A lock is the one moment where missing data becomes permanent: the pick freezes
with whatever was on the row. NFL locks Thu 8am ET (Jerry reads generate +
lock) and again Thu 2pm ET (TNF finalize); NCAAF locks Fri 2pm ET for Friday
night and Sat 8:15am for Game Day. check_pipeline_health.py is MLB-shaped and
does not answer "is THIS slate ready".

So this checks the games inside the lock window, and it checks the three things
that have actually gone wrong before:

 1. COVERAGE — is each column that a pick depends on populated on the slate?
    Coverage on "all rows" hides a hole in tonight's games
    (feedback_coverage_pct_hides_dead_code), so this reports the LOCK WINDOW.
 2. SCALE — is each model number on the market's scale? A model at ratio 0.10
    fades whatever is favoured and calls it an opinion. This is how NCAAF epa
    sat unusable for weeks and how NFL v4 emitted +1.5 on every game.
 3. STALENESS — when was the row last computed? Checked by RECENCY against the
    newest row in the table, not against a wall-clock date
    (feedback_verify_freshness_by_recency_not_date).

Exit code 1 if anything in the window is BAD, so it can gate a pipeline.

USAGE
    python prelock_readiness.py --sport NFL
    python prelock_readiness.py --sport NCAAF --through 2026-10-19
    python prelock_readiness.py            # both, default windows
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from collections import Counter

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H

#: Per sport: context table, the lock window in days, and what a pick needs.
SPEC = {
    'NFL': {
        'ctx': 'nfl_game_context',
        'through_days': 8,
        'required': ['close_spread', 'close_total', 'projected_spread',
                     'projected_total', 'primary_play'],
        'wanted': ['v3_spread', 'v4_spread', 'mc_probabilities',
                   'panel_pred_home_pts', 'signal_confluence_net',
                   'splits_summary', 'home_pass_epa_pg',
                   'home_def_pass_epa_allowed'],
        'models': [('projected_spread', 'close_spread', 'margin'),
                   ('v3_spread', 'close_spread', 'margin'),
                   ('v4_spread', 'close_spread', 'margin'),
                   ('projected_total', 'close_total', 'total')],
    },
    'NCAAF': {
        'ctx': 'ncaaf_game_context',
        'through_days': 6,
        'required': ['close_spread', 'close_total', 'projected_total',
                     'primary_play'],
        'wanted': ['projected_spread', 'sp_plus_pred_spread',
                   'epa_pred_spread', 'home_sp_plus', 'away_sp_plus',
                   'home_off_epa_pp', 'away_def_epa_pp', 'mc_probabilities',
                   'home_l4_total_avg', 'signal_confluence_net'],
        'models': [('projected_spread', 'close_spread', 'margin'),
                   ('epa_pred_spread', 'close_spread', 'margin'),
                   ('sp_plus_pred_spread', 'close_spread', 'margin'),
                   ('projected_total', 'close_total', 'total')],
    },
}
#: NFL close_spread is the AWAY line; everyone else's is the HOME handicap.
#: Either way the market's implied home margin is a sign flip away, and sd is
#: unaffected by the flip -- recorded so the next reader does not re-derive it.
BAD = []


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                         timeout=180)
        if r.status_code not in (200, 206):
            return out, r.status_code
        b = r.json()
        out += b
        if len(b) < 1000:
            return out, 200
        off += 1000


def filled(x, c):
    v = x.get(c)
    if v is None or v == '' or v == {} or v == []:
        return False
    if isinstance(v, str) and v.strip() in ('{}', 'null', ''):
        return False
    return True


def run(sport, today, through):
    sp = SPEC[sport]
    rows, sc = page(sp['ctx'], {'select': '*'})
    if sc not in (200, 206):
        print(f'{sport}: context read failed HTTP {sc}')
        BAD.append(f'{sport}: context unreadable')
        return
    win = [x for x in rows
           if today <= str(x.get('game_date'))[:10] <= through]
    print(f'\n{"="*84}\n{sport} LOCK WINDOW {today} .. {through}   '
          f'{len(win)} games   (table holds {len(rows)})')
    if not win:
        print('  no games in window')
        return
    print('  dates: ' + ', '.join(
        f'{d}×{n}' for d, n in sorted(
            Counter(str(x.get('game_date'))[:10] for x in win).items())))

    print(f'\n  REQUIRED — a pick depends on these')
    for c in sp['required']:
        n = sum(1 for x in win if filled(x, c))
        ok = n == len(win)
        tag = 'OK' if ok else 'INCOMPLETE'
        if not ok:
            BAD.append(f'{sport}: {c} on {n}/{len(win)}')
        print(f'    {c:<28}{n:>4}/{len(win):<4}  {tag}')

    print(f'  INPUTS — degrade the pick if absent')
    for c in sp['wanted']:
        n = sum(1 for x in win if filled(x, c))
        pct = 100 * n / len(win)
        tag = ('OK' if pct >= 90 else 'THIN' if pct >= 50 else 'MOSTLY ABSENT')
        print(f'    {c:<28}{n:>4}/{len(win):<4}  {tag}')

    print(f'  MODEL SCALE — ratio to the market on THIS slate')
    for col, mkt, _kind in sp['models']:
        v = [_f(x.get(col)) for x in win]
        v = [y for y in v if y is not None]
        m = [_f(x.get(mkt)) for x in win]
        m = [y for y in m if y is not None]
        if len(v) < 6 or len(m) < 6:
            print(f'    {col:<28}n={len(v):<4} too few to judge')
            continue
        sv, sm = statistics.pstdev(v), statistics.pstdev(m)
        ratio = sv / max(sm, 1e-9)
        tag = ('COMPRESSED' if ratio < 0.5
               else 'INFLATED' if ratio > 2.0 else 'on scale')
        if ratio < 0.5:
            BAD.append(f'{sport}: {col} ratio {ratio:.2f} COMPRESSED')
        print(f'    {col:<28}n={len(v):<4} sd {sv:7.3f}  mkt {sm:7.3f}  '
              f'ratio {ratio:.2f}  {tag}')

    # staleness by RECENCY, not wall clock
    stamps = [str(x.get('features_computed_at')) for x in rows
              if x.get('features_computed_at')]
    if stamps:
        newest = max(stamps)
        wstamps = [str(x.get('features_computed_at')) for x in win
                   if x.get('features_computed_at')]
        fresh = sum(1 for t in wstamps if t[:10] == newest[:10])
        print(f'  FRESHNESS   newest row in table: {newest[:19]}')
        print(f'              window rows stamped that same day: '
              f'{fresh}/{len(win)}')

    # the panel, on this slate
    shp, _ = page('engine_shadow_picks',
                  {'select': 'game_id,market,side,voters,engine_version',
                   'sport': f'eq.{sport}', 'pregame': 'is.true',
                   'engine_version': 'eq.panel_only_v1'})
    ids = {str(x.get('game_id')) for x in win}
    mine = [s for s in shp if str(s['game_id']) in ids]
    if mine:
        c = Counter(f"{s['market']}:{s['side']}" for s in mine)
        vz = Counter(s['voters'] for s in mine)
        print(f'  SHADOW PANEL on this slate: {len(mine)} rows')
        print('    ' + ', '.join(f'{k}={v}' for k, v in sorted(c.items())))
        print(f'    voters: ' + ', '.join(f'{k}→{v}'
                                          for k, v in sorted(vz.items())))
    else:
        print('  SHADOW PANEL on this slate: none (run '
              'shadow_panelist_engine.py --today)')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(SPEC))
    ap.add_argument('--today')
    ap.add_argument('--through')
    a = ap.parse_args()
    today = a.today or dt.date.today().isoformat()
    print(f'PRE-LOCK READINESS — a lock freezes whatever is on the row.')
    for sport in ([a.sport] if a.sport else list(SPEC)):
        through = a.through or (
            dt.date.fromisoformat(today)
            + dt.timedelta(days=SPEC[sport]['through_days'])).isoformat()
        run(sport, today, through)
    print(f'\n{"="*84}')
    if BAD:
        print(f'{len(BAD)} ISSUE(S) IN A LOCK WINDOW:')
        for b in BAD:
            print(f'  * {b}')
        return 1
    print('All lock windows ready.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
