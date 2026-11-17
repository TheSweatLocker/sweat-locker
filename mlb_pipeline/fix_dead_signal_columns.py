#!/usr/bin/env python3
"""Repair or retire every enabled signal that reads a non-existent column.

== WHY (2026-11-16) =======================================================
ANDY: "for naythign that we just dfound it needs to be fixed so it stops
fucking ahppening, same shit conotinue to ahpen day after day this is
unsat".

`audit_data_feeds.py --section signals` now FAILS on this condition (it
used to print it and return 0, and was scheduled in no workflow at all).
Eight columns, ten signal-reads, two sports. A signal whose condition opens
`ctx.X is not None` on an absent column is permanently false: the lens is
dark and the registry is claiming something the engine does not have.

Every change below was MEASURED against live context rows with the SAME
evaluator the scorer uses, because a condition that raises is swallowed and
reads exactly like an honestly-false one
([[feedback_swallowed_nameerror_is_a_false_condition]]). The prop signal
was checked through prop_ensemble_scorer._safe_eval, not the game
evaluator -- props get `p` in globals and the game path does not, so
testing a prop expression with the wrong evaluator reports 0 fires and
would have been my own wiring error, not a finding
([[feedback_zero_rows_is_a_wiring_error]]).

WIRED -- the data exists under another name, verified by firing count:

  nfl_prop_dome_pass_boost      ctx.dome -> ctx.roof in ('dome','closed')
      `dome` does not exist; `roof` does, at 132/316, values dome/outdoor.
      The other three NFL dome signals were migrated to ctx.roof and this
      one was left behind. MEASURED with a pass_yds prop: 0/300 -> 47/300.

  ncaab_conference_dog_cover    ctx.conference_game -> conference_home ==
                                conference_away
      `conference_game` does not exist; both conference columns do, at
      610/726. MEASURED 0/726 firing TODAY, which is correct and not a
      failure: only 1.1% of November games are same-conference. It becomes
      live in conference season, which is the point.

  ncaab_home_court_advantage    ctx.home_win_pct_at_home -> derived from
                                home_ml_l10_at_home
      No season home win pct exists, but the last-10 home moneyline record
      does, at 683/726. Derived with an n>=5 floor so a 1-0 home record
      cannot read as 100% ([[feedback_sample_size_with_pct]]).
      MEASURED 6/726.

RETIRED -- enabled is a lie when the signal cannot work, and the honest
move is to stop claiming the lens rather than point it somewhere plausible:

  ncaab_3pt_shooting_gap_home   NO 3-POINT DATA EXISTS, anywhere.
      ncaab_game_context has no 3pt column and ncaab_team_stats has none
      either. Worth noting as a real GAP rather than a bug: 3-point
      shooting is one of the few genuinely INDEPENDENT NCAAB lenses, and
      six of nine current lenses already collapse onto one team-efficiency
      reading. Adding the data is an improvement with a known payoff.

  ncaab_panel_total             the input exists and carries a CONSTANT
                                OFFSET, so the signal is a bias not an edge
      panel_prediction.panel_projected_total is populated (544/726), so
      this one COULD have been wired. Measured against the close on n=437:
          mean +5.47   median +6.09   stdev 5.49
          83.3% of games project ABOVE the close
          fires OVER on 63.6% of the board, UNDER on 5.0%
      At its own +/-4 threshold that is a standing OVER vote on two thirds
      of NCAAB, which is exactly
      [[feedback_a_constant_offset_reads_like_an_edge]]. De-biasing the
      panel projection, or comparing it against its own median rather than
      the close, is real work with a real payoff -- it is not a column
      rename, so it is not done here.

  ncaab_efficiency_home_dominant / _away_dominant
                                the twin is populated and wiring it would
                                DOUBLE-COUNT a lens we already have
      *_efficiency_overall does not exist; *_adj_em does, at 677/649 of
      726. But adj_em already drives OFF_RATING_GAP at VALIDATED weight
      1.0, and the 11-16 lens-independence measurement put adj_em and
      defense in agreement 94.8% of the time with six of nine NCAAB lenses
      resolving to one team-efficiency reading. Two more rows reading
      adj_em add correlated weight, not information
      ([[feedback_lens_independence_needs_different_inputs]]).

Every write is confirmed by READBACK, never by the status code
([[feedback_204_is_not_a_write]]), and the reason is appended to the row's
`description` so a later reader does not have to find this file.

    python fix_dead_signal_columns.py            # dry
    python fix_dead_signal_columns.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv('.env')
load_dotenv('../.env')

SB = os.environ.get('SUPABASE_URL', '').rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

_PT = "(ctx.panel_prediction or {}).get('panel_projected_total')"

#: signal_key -> {field: new_value, ...} plus a 'why' recorded on the row.
REWIRE = {
    'nfl_prop_dome_pass_boost': {
        'condition_expr': (
            "ctx.roof is not None and str(ctx.roof).lower() in "
            "('dome','closed') and p.get('prop_type') is not None and "
            "any(w in str(p['prop_type']).lower() for w in ('pass_yds',"
            "'passing_yds','rec_yds','receiving_yds','rec ','anytime_td'))"),
        'why': ('2026-11-16: read ctx.dome, which does not exist on '
                'nfl_game_context; repointed to ctx.roof (132/316 filled, '
                'values dome/outdoor) to match the three sibling dome '
                'signals. Measured 0/300 -> 47/300 firing.'),
    },
    'ncaab_conference_dog_cover': {
        'condition_expr': (
            "ctx.conference_home is not None and ctx.conference_away is "
            "not None and str(ctx.conference_home).strip() == "
            "str(ctx.conference_away).strip() and ctx.close_spread is not "
            "None and float(ctx.close_spread) < 0 and "
            "float(ctx.close_spread) >= -7"),
        'why': ('2026-11-16: read ctx.conference_game, which does not '
                'exist; derived from conference_home == conference_away '
                '(both 610/726 filled). Fires 0/726 in November because '
                'only 1.1% of games are same-conference yet - correct, '
                'not dark.'),
    },
    'ncaab_home_court_advantage': {
        'condition_expr': (
            "ctx.close_spread is not None and float(ctx.close_spread) > 0 "
            "and float(ctx.close_spread) <= 5 and ctx.home_ml_l10_at_home "
            "is not None and ctx.home_ml_l10_at_home_losses is not None "
            "and (float(ctx.home_ml_l10_at_home) + "
            "float(ctx.home_ml_l10_at_home_losses)) >= 5 and "
            "float(ctx.home_ml_l10_at_home) / "
            "(float(ctx.home_ml_l10_at_home) + "
            "float(ctx.home_ml_l10_at_home_losses)) >= 0.75"),
        'why': ('2026-11-16: read ctx.home_win_pct_at_home, which does '
                'not exist; derived from the last-10 home moneyline '
                'record (683/726 filled) with an n>=5 floor so a 1-0 '
                'home record cannot read as 100%. Measured 6/726.'),
    },
}

RETIRE = {
    'ncaab_3pt_shooting_gap_home':
        ('2026-11-16: DISABLED - reads home_3pt_pct and away_3pt_def and '
         'NO 3-point data exists anywhere in the schema '
         '(ncaab_game_context has none, ncaab_team_stats has none). '
         'Enabled was a claim to a lens the engine does not have. '
         '3-point shooting is one of the few genuinely independent NCAAB '
         'lenses, so sourcing this data is a real improvement, not a '
         'rename.'),
    'ncaab_panel_total':
        ('2026-11-16: DISABLED - panel_prediction.panel_projected_total '
         'IS populated (544/726) but runs mean +5.47 / median +6.09 '
         'above the close on n=437, with 83.3% of games projected above '
         'it. At the signal\'s own +/-4 threshold that fires OVER on '
         '63.6% of the board and UNDER on 5.0% - a constant offset, not '
         'an edge. Needs the panel projection de-biased, or compared '
         'against its own median, before it can carry weight.'),
    'ncaab_efficiency_home_dominant':
        ('2026-11-16: DISABLED - reads home/away_efficiency_overall, '
         'which do not exist. The twin *_adj_em IS populated, but adj_em '
         'already drives OFF_RATING_GAP at VALIDATED weight 1.0 and the '
         '11-16 independence measurement found six of nine NCAAB lenses '
         'collapsing onto one team-efficiency reading (adj_em and '
         'defense agree 94.8%). Wiring this adds correlated weight, not '
         'information.'),
    'ncaab_efficiency_away_dominant':
        ('2026-11-16: DISABLED - see ncaab_efficiency_home_dominant. '
         'Same absent column, same populated twin, same double-count.'),
}


def _readback(sid, field):
    g = requests.get(f'{SB}/rest/v1/signal_sources', headers=H_R, timeout=30,
                     params={'id': f'eq.{sid}', 'select': field})
    j = g.json() if g.status_code == 200 else []
    return j[0].get(field) if j else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not (SB and KEY):
        print('SUPABASE env missing')
        return 2

    keys = sorted(set(REWIRE) | set(RETIRE))
    r = requests.get(f'{SB}/rest/v1/signal_sources', headers=H_R, timeout=40,
                     params={'select': 'id,signal_key,sport,enabled,'
                                       'condition_expr,description',
                             'signal_key': f'in.({",".join(keys)})'})
    if r.status_code != 200:
        print(f'read failed {r.status_code}: {r.text[:200]}')
        return 2
    rows = {x['signal_key']: x for x in r.json()}
    missing = [k for k in keys if k not in rows]
    if missing:
        print(f'  ! not in signal_sources, skipping: {missing}')

    print(f'=== dead-column signal repair · '
          f'{"APPLY" if a.apply else "DRY"} ===')
    ok = bad = 0

    for key, spec in REWIRE.items():
        row = rows.get(key)
        if not row:
            continue
        print(f'\n  REWIRE {key} (id={row["id"]}, {row["sport"]})')
        print(f'    was: {str(row["condition_expr"])[:110]}')
        print(f'    now: {spec["condition_expr"][:110]}')
        if not a.apply:
            continue
        desc = str(row.get('description') or '')
        body = {'condition_expr': spec['condition_expr'],
                'enabled': True,
                'description': (desc + ('\n' if desc else '')
                                + spec['why'])[:4000]}
        requests.patch(f'{SB}/rest/v1/signal_sources', headers=H_W,
                       timeout=30, params={'id': f'eq.{row["id"]}'},
                       json=body)
        got = _readback(row['id'], 'condition_expr')
        good = (got == spec['condition_expr'])
        print(f'    {"CONFIRMED" if good else "NOT CONFIRMED - unchanged"}')
        ok += 1 if good else 0
        bad += 0 if good else 1

    for key, why in RETIRE.items():
        row = rows.get(key)
        if not row:
            continue
        print(f'\n  RETIRE {key} (id={row["id"]}, {row["sport"]}, '
              f'enabled={row["enabled"]})')
        print(f'    {why[:150]}')
        if not a.apply:
            continue
        desc = str(row.get('description') or '')
        requests.patch(f'{SB}/rest/v1/signal_sources', headers=H_W,
                       timeout=30, params={'id': f'eq.{row["id"]}'},
                       json={'enabled': False,
                             'description': (desc + ('\n' if desc else '')
                                             + why)[:4000]})
        got = _readback(row['id'], 'enabled')
        good = (got is False)
        print(f'    {"CONFIRMED disabled" if good else "NOT CONFIRMED"}')
        ok += 1 if good else 0
        bad += 0 if good else 1

    print(f'\n  confirmed={ok} failed={bad}')
    if not a.apply:
        print('  DRY RUN - re-run with --apply to write.')
        return 0
    print('  Re-run `python audit_data_feeds.py --section signals` to '
          'confirm the gate is green.')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
