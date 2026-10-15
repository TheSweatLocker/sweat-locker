#!/usr/bin/env python3
"""Retire signals that are duplicates of another signal, or unit-mismatched.

== 2026-10-14 - WHY ==
ANDY: "we need to make duplicated independent now we need to fix those items."

A duplicate panelist is not harmless. It corrupts AGREEMENT: "Elo sees the
over" plus "MC sees the over" reads as two independent confirmations, and a
confluence count treats it as two. When the two numbers are the same number,
that is a manufactured second opinion, and it is user-facing.

Each signal below was measured, not assumed.

── 1 & 2. nhl_mc_over_edge / nhl_mc_under_edge · DUPLICATES ──────────────
NHL's three "panelists" (projected, goals, mc) all come out of ONE Elo
predictor at nhl_game_context.py:158-160. Measured on 151 rows carrying both:

    mean |projected_total - mc_expected_total| = 0.0602 goals   (max 0.0900)
    same side of close_total                    = 107/113 = 94.7%

Six ENABLED NHL signals read those two numbers:
    nhl_elo_over_edge, nhl_elo_under_edge                  <- projected_total
    nhl_projection_contradicts_total_over / _under         <- projected_total
    nhl_mc_over_edge, nhl_mc_under_edge                    <- mc_expected_total

The mc pair cannot say anything the elo pair has not already said: it is the
same number to within six hundredths of a goal. Retiring the mc pair removes
the false confluence and leaves the opinion itself intact, still expressed by
the elo pair.

NOT retired as "MC is useless" — retired as "this MC is the Elo projection
resimulated". The route back is to rebase NHL's MC on inputs Elo cannot see:
goalie GSAA, special teams, back-to-backs (voter_goalie_special.py, prediction
sd 0.249 against market sd 0.362, versus rolling form's 0.057). Once that MC is
independent and graded, these two can be re-enabled as genuine second opinions.

── 3. ncaaf_sp_plus_matchup_total · UNIT MISMATCH ────────────────────────
This is not a duplicate, it is a category error, and it fires on nearly every
game. `sp_plus_matchup_total` IS home_sp_plus + away_sp_plus:

    |(home_sp_plus + away_sp_plus) - sp_plus_matchup_total|
        n=383  mean 0.0012  max 0.0046

SP+ is a point-differential-per-game rating centred near zero, so their sum
has mean 2.21 and sd 20.0, range [-49.3, +56.2]. `close_total` has mean 52.77.
The condition is

    abs(sp_plus_matchup_total - close_total) >= 5

so it compares a rating sum (~2) to a points total (~53), a gap of about 50 on
every game. MEASURED: it fires on 377 of 382 rows = 98.7%. A signal that fires
on 98.7% of games carries no information and inflates whatever counts it.

The name is the trap: "matchup_total" reads like a projected total. It is not.

USAGE
    python fix_duplicate_and_broken_signals.py --dry-run
    python fix_duplicate_and_broken_signals.py --apply
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

TARGETS = [
    ('nhl_mc_over_edge', 'NHL',
     'disabled 2026-10-14: DUPLICATE of nhl_elo_over_edge. NHL projected, '
     'goals and mc all come from one Elo predictor '
     '(nhl_game_context.py:158-160); measured mean |projected_total - '
     'mc_expected_total| = 0.0602 goals (max 0.0900) and the same side of '
     'close_total on 107/113 = 94.7% of rows. Presenting both as separate '
     'signals manufactures a second opinion. Re-enable once NHL MC is rebased '
     'on goalie GSAA / special teams / back-to-backs (voter_goalie_special.py) '
     'and graded independent.'),
    ('nhl_mc_under_edge', 'NHL',
     'disabled 2026-10-14: DUPLICATE of nhl_elo_under_edge. Same measurement — '
     'mc_expected_total is projected_total to within 0.06 goals, same side '
     '94.7%. Re-enable once NHL MC reads inputs Elo cannot see.'),
    ('ncaaf_sp_plus_matchup_total', 'NCAAF',
     'disabled 2026-10-14: UNIT MISMATCH, fires on 98.7% of games (377/382). '
     'sp_plus_matchup_total IS home_sp_plus + away_sp_plus (measured mean '
     '|diff| 0.0012, max 0.0046) — a sum of point-differential ratings, mean '
     '2.21 sd 20.0, NOT a points total. The condition compares it to '
     'close_total (mean 52.77) with a >=5 threshold, so the ~50-point unit gap '
     'satisfies it on essentially every game. Needs a real SP+ projected '
     'total, not a rating sum, before it can mean anything.'),
    ('v3_total_nfl', 'NFL',
     'disabled 2026-10-14: DERIVED duplicate of projected_total, found by '
     'audit_duplicate_votes.py. v3_total is projected_total plus a '
     'rest/bye/short-week delta (nfl_v3_regression.py), measured r=+0.957, '
     'mean |diff| 0.449, same side of close_total 93.5% on n=108. '
     'nfl_projection_contradicts_total_over/_under already express that '
     'opinion, so this fired as a second confirmation of one number. Separately '
     'worth knowing: NFL totals are the worst market we have -- every panelist '
     'loses (projected/v3/model all 37.9%, -27.7% ROI, CI clearing BELOW '
     'breakeven) and the projection deviation is ANTI-informative '
     '(slope -0.908, CI[-1.553,-0.242]). This retires the duplicate; the '
     'market itself still needs a decision.'),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    for key, sport, reason in TARGETS:
        r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H,
                         params={'select': '*', 'signal_key': f'eq.{key}',
                                 'sport': f'eq.{sport}'}, timeout=120)
        rows = r.json() if r.status_code in (200, 206) else []
        if not rows:
            print(f'{key}: NOT FOUND (HTTP {r.status_code})')
            continue
        cur = rows[0]
        print(f'\n{sport} {key}  enabled={cur.get("enabled")}')
        if cur.get('enabled') is False:
            print('  already disabled — leaving it alone')
            continue
        if a.dry_run:
            print(f'  DRY RUN — would disable. reason: {reason[:110]}...')
            continue
        # Patch only columns proven present: naming a missing column makes
        # PostgREST drop the WHOLE patch (PGRST204).
        patch = {'enabled': False}
        if 'description' in cur:
            patch['description'] = (reason + ' | PRIOR: '
                                    + str(cur.get('description') or ''))[:1500]
        w = requests.patch(f'{SB}/rest/v1/signal_sources', headers=H_WRITE,
                           params={'signal_key': f'eq.{key}',
                                   'sport': f'eq.{sport}'},
                           data=json.dumps(patch), timeout=120)
        if w.status_code not in (200, 204):
            print(f'  WRITE FAILED {w.status_code}: {w.text[:220]}')
            continue
        print('  disabled')

    print('\nverification read-back:')
    for key, sport, _r in TARGETS:
        r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H,
                         params={'select': 'signal_key,sport,enabled',
                                 'signal_key': f'eq.{key}',
                                 'sport': f'eq.{sport}'}, timeout=120)
        for x in (r.json() if r.status_code in (200, 206) else []):
            print(f'  {x.get("sport"):<6} {x.get("signal_key"):<32} '
                  f'enabled={x.get("enabled")}')
    print('\nSTILL ENABLED and intended to be — the opinion itself survives:')
    for key, sport in (('nhl_elo_over_edge', 'NHL'),
                       ('nhl_elo_under_edge', 'NHL'),
                       ('ncaaf_projected_total', 'NCAAF')):
        r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H,
                         params={'select': 'signal_key,enabled',
                                 'signal_key': f'eq.{key}',
                                 'sport': f'eq.{sport}'}, timeout=120)
        for x in (r.json() if r.status_code in (200, 206) else []):
            print(f'  {sport:<6} {x.get("signal_key"):<32} '
                  f'enabled={x.get("enabled")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
