"""Four live signals compare a 0-100 column against a 0-1 threshold.

`player_season_hit_pct` is stored on a PERCENT scale — verified against both
populated tables: NFL min 0.0 max 100.0 (164 of 171 values above 1.0), MLB
min 0.0 max 100.0 (1,375 of 1,382 above 1.0).

Four enabled signal_sources rows test it as if it were a fraction:

    nfl_prop_season_hit_pct_high     >= 0.70
    ncaaf_prop_season_hit_pct_high   >= 0.70
    nba_prop_season_hit_pct_high     >= 0.65
    nhl_prop_season_hit_pct_high     >= 0.60

So a signal whose prose reads "season hit% >= 70 — line-hitting consistency"
fires on any player above 0.7 PERCENT, which is very nearly every row that
has the column populated. It is not selecting consistent players, it is
selecting rows with lookback coverage.

This is why it measured 36.9% over n=149 and was tiered ANTI_VALIDATED. That
number was real but it was never a measurement of hot players, so the tier is
attached to a condition nobody intended.

WHAT THIS CHANGES. After the fix the signal fires on a genuinely different
and much smaller population, so its existing calibration is void.
backfill_prop_signal_tiers must be re-run for the affected sports afterwards,
or the old ANTI_VALIDATED verdict keeps being applied to a condition that no
longer means the same thing. This script refuses to finish quietly about that.

MLB is untouched — it has no season_hit_pct signal. NBA, NHL and NCAAF prop
tables are empty or unused, so NFL is the only sport where this changes live
behaviour today.

Dry by default.
"""
import os
import sys
import argparse
import requests

sys.stdout.reconfigure(encoding='utf-8')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

# signal_key -> (old fraction threshold, correct percent threshold)
TARGETS = {
    'nfl_prop_season_hit_pct_high':   ('0.70', '70'),
    'ncaaf_prop_season_hit_pct_high': ('0.70', '70'),
    'nba_prop_season_hit_pct_high':   ('0.65', '65'),
    'nhl_prop_season_hit_pct_high':   ('0.60', '60'),
}


def verify_scale() -> bool:
    """Re-check the column really is 0-100 before rewriting any condition."""
    ok = True
    for table in ('nfl_pipeline_props', 'mlb_pipeline_props'):
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params={'select': 'player_season_hit_pct',
                                 'player_season_hit_pct': 'not.is.null',
                                 'order': 'player_season_hit_pct.desc',
                                 'limit': 1})
        rows = r.json() if r.status_code == 200 else []
        if not rows:
            print(f'  {table}: no populated values (cannot confirm scale)')
            continue
        mx = float(rows[0]['player_season_hit_pct'])
        print(f'  {table}: max populated value = {mx}')
        if mx <= 1.0:
            print(f'    !! looks like a 0-1 fraction — ABORT, do not rewrite')
            ok = False
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    print('confirming the column scale before touching any condition:')
    if not verify_scale():
        sys.exit(2)

    rows = requests.get(
        f'{SB}/rest/v1/signal_sources', headers=H, timeout=180,
        params={'select': 'id,signal_key,sport,condition_expr,enabled,'
                          'display_prose_template',
                'signal_key': 'in.(' + ','.join(TARGETS) + ')'}).json()
    print(f'\nmatched {len(rows)} signal_sources rows\n')

    planned = []
    for row in rows:
        key = row['signal_key']
        old_t, new_t = TARGETS[key]
        cond = row.get('condition_expr') or ''
        if old_t not in cond:
            print(f'  {key}: threshold {old_t} not found in condition — '
                  f'SKIPPED, inspect by hand')
            print(f'      {cond[:150]}')
            continue
        new_cond = cond.replace(f'>= {old_t}', f'>= {new_t}')
        if new_cond == cond:
            new_cond = cond.replace(old_t, new_t)
        if new_cond == cond:
            print(f'  {key}: replacement produced no change — SKIPPED')
            continue
        planned.append((row['id'], key, row.get('sport'), cond, new_cond))

    for _id, key, sport, old, new in planned:
        print(f'  {key}  ({sport})')
        print(f'      before: {old[:130]}')
        print(f'      after : {new[:130]}')

    if not args.write:
        print(f'\nDRY RUN — {len(planned)} condition(s) would be rewritten. '
              f'Re-run with --write.')
        return

    ok = 0
    for _id, key, sport, old, new in planned:
        r = requests.patch(f'{SB}/rest/v1/signal_sources', headers=H_W,
                           timeout=60, params={'id': f'eq.{_id}'},
                           json={'condition_expr': new})
        if r.status_code in (200, 204):
            ok += 1
        else:
            print(f'  !! {key} {r.status_code} {r.text[:140]}')
    print(f'\nrewrote {ok}/{len(planned)}')
    if ok:
        print('\nTHESE SIGNALS NOW FIRE ON A DIFFERENT POPULATION.')
        print('Their signal_registry calibration was fitted to the broken')
        print('condition and is now meaningless. Re-run:')
        print('    python backfill_prop_signal_tiers.py --sport NFL --days 30')
        print('and any other sport whose prop table has graded rows.')


if __name__ == '__main__':
    main()
