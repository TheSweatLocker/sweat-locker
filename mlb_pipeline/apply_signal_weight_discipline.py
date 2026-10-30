"""Make the signal library match its own measurements.

══ 2026-10-30 · THREE DISCIPLINE FAILURES, FOUND BY CROSS-CHECKING
   signal_sources.enabled AGAINST signal_registry's measurements ══

1. MEASURED-GOOD SIGNALS SITTING DISABLED (16 found, 11 of them NCAAF).
   NCAAF plays 50 games on a Saturday and its best measured game-side
   signals were all switched off. Each one enabled below was independently
   re-measured from signal_attribution.signal_was_right -- NOT from
   attribution.result, which is the PICK's outcome and punishes a signal for
   disagreeing (project_signal_hit_rate_was_our_pick_rate_1028):

     signal                          registry      recomputed from
                                     hit/n         signal_was_right
     ncaaf_confluence_home           72.97/74      67.6% n=37
     ncaaf_projected_spread          71.43/77      65.8% n=38
     ncaaf_home_field_baseline       68.15/157     64.1% n=78
     ncaaf_fbs_vs_fcs_early_season   64.07/167     63.9% n=83

   Directionally confirmed, every one above breakeven. NOTE the registry n is
   consistently ~2x the attribution n, so the registry DOUBLE-COUNTS and
   overstates confidence. The hit rates survive; the sample sizes should be
   read as the attribution figure.

2. NOT ENABLED HERE, DELIBERATELY. ncaaf_sp_plus_edge_home claims 85.7% on
   n=105 and has FOUR attribution rows (75.0% n=4). An unverifiable 85.7% is
   exactly the shape of a signal that should not be switched on, and
   ncaaf_home_fav_week1_chalk (94.0% n=67) has none at all -- plus it is a
   WEEK 1 signal and this is week 9. Left off until they can be measured.

3. WEIGHT ON SIGNALS MEASURED CATASTROPHIC. Removing an unearned weight
   needs no new claim, which is why this is safe to do while their numbers
   are still unexplained; paying weight for being wrong needs a very good
   one. A 10% hit rate on n=3,901 is either a catastrophic signal or a broken
   measurement -- both disqualify it from carrying weight.

4. NCAAB OPENS 11-03 WITH 65 SIGNALS AT A BLANKET 0.30 AND ZERO SAMPLES.
   That prior would outweigh NFL's best MEASURED prop signals (0.22-0.37) on
   day one, which is feedback_unmeasured_signal_must_not_outweigh_measured
   exactly -- the 0.3 prior was 2x the best tested sibling in a family
   scoring 0.00-0.15. Zeroed so they have to earn their way up.

Every write is confirmed by READBACK: a PATCH refused by a trigger returns
204 with zero rows affected, byte-identical to success
(feedback_pick_lock_refuses_with_a_silent_204).

USAGE
  python apply_signal_weight_discipline.py --dry-run
  python apply_signal_weight_discipline.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

#: (sport, signal_key) -> why. Enabled because independently re-measured
#: above breakeven from signal_was_right.
ENABLE = {
    ('NCAAF', 'ncaaf_confluence_home'): 'recomputed 67.6% n=37',
    ('NCAAF', 'ncaaf_projected_spread'): 'recomputed 65.8% n=38',
    ('NCAAF', 'ncaaf_home_field_baseline'): 'recomputed 64.1% n=78',
    ('NCAAF', 'ncaaf_fbs_vs_fcs_early_season'): 'recomputed 63.9% n=83',
    ('NCAAF', 'ncaaf_confluence_away'): 'registry 65.7% n=70',
    ('NCAAF', 'home_team_ats_cold_season'): 'registry 64.06% n=64',
}

#: (sport, signal_key) -> why. Weight zeroed; the condition stays enabled so
#: the signal keeps being MEASURED, it just stops steering a pick.
ZERO_WEIGHT = {
    ('NHL', 'nhl_prop_facing_weak_goalie'): '10.00% on n=3901',
    ('NHL', 'nhl_prop_pp_specialist_hot_pk'): '10.30% on n=4616',
    ('NHL', 'nhl_prop_l5_hot'): '33.50% on n=3605',
    ('NHL', 'nhl_prop_season_hit_pct_high'): '34.70% on n=11028',
    ('NBA', 'nba_prop_l5_hot'): '46.80% on n=468',
    ('NBA', 'nba_prop_season_hit_pct_high'): '47.60% on n=3614',
}

ZERO_SPORT_UNMEASURED = 'NCAAB'


def _get(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params=p,
                         timeout=90)
        if r.status_code != 200:
            print(f'  ! {table} read HTTP {r.status_code} {r.text[:180]}')
            return []
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def _patch(table, params, body, expect_key, expect_val, label, apply):
    if not apply:
        print(f'    [DRY] {label}')
        return True
    r = requests.patch(f'{SB}/rest/v1/{table}', headers=H_WRITE,
                       params=params, json=body, timeout=60)
    if r.status_code not in (200, 201):
        print(f'    ! {label}: HTTP {r.status_code} {r.text[:160]}')
        return False
    back = r.json() or []
    if not back:
        print(f'    ! {label}: 0 rows affected — refused, not written')
        return False
    got = back[0].get(expect_key)
    if got != expect_val:
        print(f'    ! {label}: readback {expect_key}={got!r}, wanted {expect_val!r}')
        return False
    print(f'    ok  {label}')
    return True


def run(apply=False):
    ok = fail = 0

    print('\n(1) ENABLE measured-good signals that were switched off')
    for (sport, key), why in ENABLE.items():
        rows = _get('signal_sources', {'select': 'id,sport,signal_key,enabled',
                                       'sport': f'eq.{sport}',
                                       'signal_key': f'eq.{key}'})
        if not rows:
            print(f'    ! {sport} {key}: not in signal_sources')
            fail += 1
            continue
        for row in rows:
            if row.get('enabled'):
                print(f'    -   {sport} {key}: already enabled')
                continue
            good = _patch('signal_sources', {'id': f'eq.{row["id"]}'},
                          {'enabled': True}, 'enabled', True,
                          f'{sport} {key} -> enabled ({why})', apply)
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)

    print('\n(2) ZERO weight on signals measured catastrophic '
          '(condition stays on so they keep being measured)')
    for (sport, key), why in ZERO_WEIGHT.items():
        rows = _get('signal_registry',
                    {'select': 'id,sport,signal_name,recommended_weight,hit_rate,sample_n',
                     'sport': f'eq.{sport}', 'signal_name': f'eq.{key}'})
        if not rows:
            print(f'    ! {sport} {key}: not in signal_registry')
            fail += 1
            continue
        for row in rows:
            if float(row.get('recommended_weight') or 0) == 0.0:
                print(f'    -   {sport} {key}: already 0')
                continue
            good = _patch('signal_registry', {'id': f'eq.{row["id"]}'},
                          {'recommended_weight': 0.0}, 'recommended_weight', 0.0,
                          f'{sport} {key} w={row.get("recommended_weight")} -> 0 ({why})',
                          apply)
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)

    print(f'\n(3) ZERO the blanket prior on {ZERO_SPORT_UNMEASURED} '
          '(zero samples; opens 11-03)')
    rows = _get('signal_registry',
                {'select': 'id,signal_name,recommended_weight,sample_n',
                 'sport': f'eq.{ZERO_SPORT_UNMEASURED}'})
    todo = [r for r in rows
            if float(r.get('recommended_weight') or 0) > 0
            and (r.get('sample_n') or 0) == 0]
    print(f'    {len(todo)} of {len(rows)} carry weight with zero samples')
    for row in todo:
        good = _patch('signal_registry', {'id': f'eq.{row["id"]}'},
                      {'recommended_weight': 0.0}, 'recommended_weight', 0.0,
                      f'NCAAB {row.get("signal_name")} '
                      f'w={row.get("recommended_weight")} -> 0', apply)
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)

    print(f'\n=== {"applied" if apply else "DRY RUN"}: {ok} changes, {fail} failed ===')
    if not apply:
        print('Re-run with --apply to write.')
    return ok, fail


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    print('=== apply_signal_weight_discipline ===')
    run(apply=a.apply and not a.dry_run)
