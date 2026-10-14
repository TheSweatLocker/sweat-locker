#!/usr/bin/env python3
"""Record on the four v4-reading signals what the v4 repair changed.

== 2026-10-14 - WHY ==
The 2026-10-13 annotation on nfl_home_spread_edge / nfl_away_spread_edge said:

  "reads v4_spread, which is a CONSTANT (~1.74, sd 0.276). With v4 fixed this
   condition reduces to a pure line-size test ... It is a line filter wearing a
   model name. Left enabled; retire or re-point at mc_expected_margin once
   NFL_CONSENSUS_MC_V3 has a record."

"v4 fixed" there meant v4 HELD FIXED, i.e. constant. v4 is now actually
repaired (nfl_v4_train.py, commit 80216e78): prediction sd 0.302 -> 3.845,
range [+0.78,+3.10] -> [-6.02,+14.25], correlation with the actual margin
+0.025 -> +0.342, and it goes negative on 76 of 269 games instead of 0 of 285.

So the annotation is now MISLEADING in both directions: these are no longer
line filters, and re-pointing them at mc_expected_margin is no longer the
obvious fix. Leaving a stale note on a live signal is how the next person
mis-reads it.

MEASURED IMPACT, 2024 holdout, ATS at -110, breakeven 52.38%
(backtest_v4_signal_impact.py). Sign verified: corr(line, actual margin)
+0.487 and always-HOME covers 51.2%, so the grading is fair -- a first pass
that negated spread_line produced 81-94% on BOTH sides at once, which is
impossible and was a sign error, not a finding.

                            OLD v4 (constant)     NEW v4 (team features)
  nfl_home_spread_edge      49.1%  -6.2% ROI      50.9%  -2.8% ROI
  nfl_away_spread_edge      45.0% -14.1% ROI      47.9%  -8.5% ROI
  consensus v4 vote HOME    49.6%  -5.3% ROI      51.7%  -1.2% ROI
  consensus v4 vote AWAY    46.6% -11.0% ROI      49.0%  -6.5% ROI

Every one improves. EVERY ONE STILL LOSES, and no CI clears breakeven, so the
honest status is "better, still not profitable" -- not a win to celebrate.

They are LEFT ENABLED, deliberately and for stated reasons rather than by
default:
  * the repair strictly improves all four, and the 10-13 decision was already
    to leave them enabled;
  * no CI clears, so "these lose" is not established at n=73-148 -- disabling
    four live signals on inconclusive evidence, with nobody around to course
    correct, is the bigger unilateral move;
  * the consensus pair is materially FAIRER now. v4 was always positive, so it
    cast a GUARANTEED HOME vote in "3+ of V3/V4/Panel/MC agree", inflating
    home consensus and suppressing away. That gate now counts a real opinion.

WHAT SHOULD HAPPEN NEXT, for whoever picks this up: grade these four forward on
live rows with the repaired v4 and retire any whose CI clears below breakeven.
The decision needs a forward record, not another backtest.

USAGE
    python annotate_v4_signals_post_fix.py --dry-run
    python annotate_v4_signals_post_fix.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

NOTE = {
    'nfl_home_spread_edge':
        '2026-10-14: v4 REPAIRED (sd 0.302->3.845, corr w/ actual margin '
        '+0.025->+0.342, now negative on 76/269). This is no longer a line '
        'filter. 2024 holdout ATS: 49.1%/-6.2% under old v4 -> 50.9%/-2.8% '
        'under new. Improved, still below the 52.38% breakeven, CI does not '
        'clear. Left enabled; grade FORWARD and retire if a CI clears below.',
    'nfl_away_spread_edge':
        '2026-10-14: v4 REPAIRED (sd 0.302->3.845, corr +0.025->+0.342). No '
        'longer a line filter. 2024 holdout ATS: 45.0%/-14.1% under old v4 -> '
        '47.9%/-8.5% under new. Improved, still losing, CI does not clear. '
        'Left enabled; grade FORWARD and retire if a CI clears below.',
    'nfl_spread_consensus_home_rl':
        '3+ of V3/V4/Panel/MC agree home covers close_spread. 2026-10-14: v4 '
        'REPAIRED. Until today v4_spread was ALWAYS POSITIVE, so v4 cast a '
        'guaranteed HOME vote in this count -- inflating home consensus and '
        'suppressing away (the 2/5-becomes-2/4 inflation described at '
        'nfl_game_context.py:1094-1112). The count now reflects a real '
        'opinion. v4 half alone on 2024: 49.6%/-5.3% -> 51.7%/-1.2%.',
    'nfl_spread_consensus_away_rl':
        '3+ of V3/V4/Panel/MC agree away covers close_spread. 2026-10-14: v4 '
        'REPAIRED. v4 was always positive and therefore never voted away, so '
        'this side was systematically suppressed. v4 half alone on 2024: '
        '46.6%/-11.0% -> 49.0%/-6.5%. Note V3 is projected_spread + a rest '
        'nudge (panelist_registry.py), so V3 and Panel are NOT independent '
        'votes -- this gate counts fewer real opinions than its name implies.',
}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    for key, note in NOTE.items():
        r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H,
                         params={'select': '*', 'signal_key': f'eq.{key}',
                                 'sport': 'eq.NFL'}, timeout=120)
        rows = r.json() if r.status_code in (200, 206) else []
        if not rows:
            print(f'{key}: NOT FOUND (HTTP {r.status_code})')
            continue
        cur = rows[0]
        print(f'\n{key}  enabled={cur.get("enabled")}')
        if a.dry_run:
            print(f'  DRY RUN — would set description to:\n    {note[:160]}...')
            continue
        # Patch ONLY `description`: naming a column that does not exist makes
        # PostgREST drop the entire patch (PGRST204).
        w = requests.patch(f'{SB}/rest/v1/signal_sources', headers=H_WRITE,
                           params={'signal_key': f'eq.{key}',
                                   'sport': 'eq.NFL'},
                           data=json.dumps({'description': note}), timeout=120)
        if w.status_code not in (200, 204):
            print(f'  WRITE FAILED {w.status_code}: {w.text[:220]}')
            continue
        print('  description updated')

    print('\nverification read-back (enabled must be unchanged = True):')
    for key in NOTE:
        r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H,
                         params={'select': 'signal_key,enabled,description',
                                 'signal_key': f'eq.{key}',
                                 'sport': 'eq.NFL'}, timeout=120)
        for x in (r.json() if r.status_code in (200, 206) else []):
            d = str(x.get('description') or '')
            print(f'  {x.get("signal_key"):<32} enabled={x.get("enabled")}  '
                  f'note starts: {d[:48]}...')
    return 0


if __name__ == '__main__':
    sys.exit(main())
