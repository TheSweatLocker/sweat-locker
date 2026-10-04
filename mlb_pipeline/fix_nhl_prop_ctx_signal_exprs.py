#!/usr/bin/env python3
"""Repair the four ctx-dependent NHL prop signals (2026-10-03).

These four had never fired once on 5,889 graded props and sat at sample_n=0 in
signal_registry, which read as "tested and found wanting" rather than "never
evaluable". THREE independent bugs, each silent on its own:

1. `p.get('player_team')` — NHL props have no such column. It is
   `team_abbrev`. So the left side of the team comparison was always None.

2. `ctx.home_team` is 'Columbus Blue Jackets' while the prop carries 'CBJ'.
   Even with the right column the comparison could not match. `None == '...'`
   is merely False and `and` short-circuits, so nothing ever raised.

3. `nhl_prop_pp_specialist_hot_pk` tested `ctx.away_pk_pct <= 0.78`, but
   pk_pct is stored on a 0-100 scale — measured over 120 context rows:
   min 20.0, median 79.9, max 100.0. The threshold could never be met at any
   team's penalty kill. Same unit class as fix_season_hit_pct_signal_units.py.
   sv_pct genuinely IS 0-1 (min 0.682, median 0.899), so those thresholds
   stay.

The fix: conditions now read side-aware `own_*` / `opp_*` fields that
prop_ctx_resolve.derive_side_fields injects, so no expression has to re-derive
which side a player is on from mismatched team vocabularies.

Usage:  python fix_nhl_prop_ctx_signal_exprs.py [--apply]
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

_SHOOTER = ("p.get('prop_type') is not None and any(w in str(p['prop_type']).lower() "
            "for w in ('goal','pts','shots'))")

FIXES = {
    'nhl_prop_facing_elite_goalie': {
        'condition_expr': (
            "p.get('opp_goalie_sv_pct') is not None "
            "and float(p['opp_goalie_sv_pct']) >= 0.920 and " + _SHOOTER),
        'side_expr': "'BACK' if p['direction'] == 'under' else 'FADE'",
        'display_prose_template': 'shooter facing elite goalie (opp SV% >= .920)',
    },
    'nhl_prop_facing_weak_goalie': {
        'condition_expr': (
            "p.get('opp_goalie_sv_pct') is not None "
            "and float(p['opp_goalie_sv_pct']) <= 0.895 and " + _SHOOTER),
        'side_expr': "'BACK' if p['direction'] == 'over' else 'FADE'",
        'display_prose_template': 'shooter facing weak goalie (opp SV% <= .895)',
    },
    'nhl_prop_b2b_fade': {
        'condition_expr': (
            "p.get('own_back_to_back') is not None "
            "and str(p['own_back_to_back']).lower() == 'true'"),
        'side_expr': "'BACK' if p['direction'] == 'under' else 'FADE'",
        'display_prose_template': 'player on a back-to-back — fatigue',
    },
    # 0-100 scale, NOT 0-1. See bug 3 in the module docstring.
    'nhl_prop_pp_specialist_hot_pk': {
        'condition_expr': (
            "p.get('opp_pk_pct') is not None "
            "and float(p['opp_pk_pct']) <= 78.0 and "
            "p.get('prop_type') is not None and any(w in str(p['prop_type']).lower() "
            "for w in ('goal','pts','pp'))"),
        'side_expr': "'BACK' if p['direction'] == 'over' else 'FADE'",
        'display_prose_template': 'PP chance vs weak PK (opp PK <= 78%)',
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='write the repaired expressions (default: preview)')
    args = ap.parse_args()

    r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H, timeout=30,
                     params={'select': 'signal_key,sport,condition_expr,side_expr,'
                                       'display_prose_template,enabled',
                             'sport': 'eq.NHL',
                             'signal_key': 'in.(%s)' % ','.join(FIXES)})
    r.raise_for_status()
    rows = {x['signal_key']: x for x in r.json()}
    print('found %d of %d target signals in signal_sources\n' % (len(rows), len(FIXES)))

    missing = [k for k in FIXES if k not in rows]
    if missing:
        print('  ! not in signal_sources: %s' % missing)

    changed = 0
    for key, fix in FIXES.items():
        cur = rows.get(key)
        if not cur:
            continue
        diffs = {f: v for f, v in fix.items() if (cur.get(f) or '') != v}
        print('%s  enabled=%s' % (key, cur.get('enabled')))
        if not diffs:
            print('    already current\n')
            continue
        for f, v in diffs.items():
            print('    %s' % f)
            print('      OLD  %s' % (cur.get(f) or '')[:150])
            print('      NEW  %s' % v[:150])
        print()
        if args.apply:
            pr = requests.patch(f'{SB}/rest/v1/signal_sources', headers=H_W, timeout=30,
                                params={'sport': 'eq.NHL', 'signal_key': f'eq.{key}'},
                                data=json.dumps(fix))
            # A PATCH that matched nothing returns 200 with an empty body.
            body = pr.json() if pr.content else []
            if pr.status_code not in (200, 204) or not body:
                print('    x WRITE FAILED %s %s' % (pr.status_code, pr.text[:160]))
                return 1
            print('    written, row returned\n')
        changed += 1

    print('%s %d signal(s)' % ('updated' if args.apply else 'would update', changed))
    if not args.apply:
        print('(preview only — re-run with --apply)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
