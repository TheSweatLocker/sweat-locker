"""Backfill nfl_pipeline_props.projection from the stored signals blob.

WHY THIS EXISTS
---------------
nfl_generate_props computes a projection for every prop (project(), the
0.60*L4 + 0.35*season + 0.05*baseline blend, or the fantasy-weighted
variant) and puts it in the row dict as `projected`. But the only write
path to the DB is _to_pipeline_props_shape, which never mapped it, and
the `nfl_props` table it used to also write is dead. So the number that
justifies every NFL prop pick was computed and then discarded.

Result: projection NULL on 1,794 of 1,796 rows, which silently disabled
three enabled signal_sources rows that gate on p.get('projection'):
  nfl_prop_projection_edge_supports (BACK 0.55)
  nfl_prop_projection_edge_opposes  (FADE 0.60)
  nfl_prop_projection_strong        (BACK 0.75)

The writer is fixed in nfl_generate_props.py. This repairs the history.

HOW THE VALUE IS RECOVERED
--------------------------
Nothing is re-scraped and nothing is invented. signals.edge_pct is the
*directional* edge the generator computed, as a percent:

    signals['edge_pct'] = round(directional_edge * 100, 1)
    directional_edge    = edge_pct_raw if OVER else -edge_pct_raw
    edge_pct_raw        = (proj - line) / line

so, inverting:

    proj = line + line * (edge_pct/100) * (+1 if OVER else -1)

That is an exact inversion of the generator's own arithmetic, losing only
the rounding of edge_pct to one decimal (±0.0005*line — a twentieth of a
yard on a 100-yard line).

CROSS-CHECK. signals also stores l4 / season_avg / league_baseline /
opp_pct, which are the literal inputs to project(). So the blend can be
recomputed from scratch and compared against the inverted value. The two
derivations are independent. They are expected to agree except where the
fantasy-projection lens fired (nfl_player_projections is not stored in
signals, so the recompute cannot see it) — those rows legitimately
diverge and are reported separately rather than quietly averaged.

A row is only written when the inversion succeeds. Rows missing edge_pct
or prop_line are skipped and counted, never guessed.

Dry by default. --write to apply.
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
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

PAGE = 500


def fetch_all(only_null: bool) -> list:
    """Paginate — PostgREST caps at 1000 and silently truncates."""
    out, offset = [], 0
    params = {
        'select': 'id,game_date,player_name,prop_type,prop_line,direction,'
                  'projection,signals',
        'order': 'id.asc',
    }
    if only_null:
        params['projection'] = 'is.null'
    while True:
        r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props', headers=H,
                         timeout=180,
                         params={**params, 'limit': PAGE, 'offset': offset})
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < PAGE:
            return out
        offset += PAGE


def blend(l4, season_avg, league_baseline, opp_pct):
    """Mirror of nfl_generate_props.project() WITHOUT the fantasy lens.

    Kept byte-for-byte in step with the generator's fallback branch so the
    cross-check actually checks something. If the generator's weights
    change, this must change with them.
    """
    if l4 is None and season_avg is None:
        return None
    if league_baseline is None:
        return None
    _l4 = l4 if l4 is not None else (season_avg or league_baseline)
    _season = season_avg if season_avg is not None else (l4 or league_baseline)
    base = 0.60 * _l4 + 0.35 * _season + 0.05 * league_baseline
    if opp_pct is not None:
        base *= 1.0 + (opp_pct - 0.5) * 0.15
    return round(base, 2)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--all', action='store_true',
                    help='revisit rows that already have a projection')
    args = ap.parse_args()

    rows = fetch_all(only_null=not args.all)
    print(f'rows to consider: {len(rows)}\n')

    patches, skipped = [], []
    agree = diverge = nocheck = 0
    for row in rows:
        sig = row.get('signals')
        if not isinstance(sig, dict):
            skipped.append((row['id'], 'no signals blob'))
            continue
        line = _f(row.get('prop_line'))
        edge_pct = _f(sig.get('edge_pct'))
        direction = (row.get('direction') or '').lower()
        if line is None or edge_pct is None or direction not in ('over', 'under'):
            skipped.append((row['id'], 'missing prop_line / edge_pct / direction'))
            continue

        sign = 1.0 if direction == 'over' else -1.0
        proj = round(line + line * (edge_pct / 100.0) * sign, 2)

        check = blend(_f(sig.get('l4')), _f(sig.get('season_avg')),
                      _f(sig.get('league_baseline')), _f(sig.get('opp_pct')))
        if check is None:
            nocheck += 1
        elif abs(check - proj) <= max(0.05, 0.02 * abs(proj)):
            agree += 1
        else:
            diverge += 1

        patches.append((row['id'], proj, check, row))

    print(f'reconstructed : {len(patches)}')
    print(f'skipped       : {len(skipped)}')
    print('\ncross-check against the recomputed blend:')
    print(f'  agree (within 2%)      : {agree}')
    print(f'  diverge                : {diverge}   '
          f'(expected where the fantasy lens fired)')
    print(f'  no inputs to check with: {nocheck}')

    print('\nsample:')
    print(f"  {'player':22s} {'prop':18s} {'dir':5s} {'line':>7s} "
          f"{'proj':>8s} {'blend':>8s}")
    for _id, proj, check, row in patches[:12]:
        print(f"  {(row.get('player_name') or '')[:21]:22s} "
              f"{(row.get('prop_type') or '')[:17]:18s} "
              f"{row.get('direction',''):5s} {row.get('prop_line'):>7} "
              f"{proj:>8.2f} {('—' if check is None else f'{check:.2f}'):>8s}")

    if skipped:
        print(f'\nskipped detail (first 10 of {len(skipped)}):')
        for _id, why in skipped[:10]:
            print(f'  id={_id}: {why}')

    if not args.write:
        print(f'\nDRY RUN — {len(patches)} row(s) would be patched. '
              f'Re-run with --write.')
        return

    ok = fail = 0
    for _id, proj, _check, _row in patches:
        r = requests.patch(f'{SB}/rest/v1/nfl_pipeline_props',
                           headers=H_W, timeout=60,
                           params={'id': f'eq.{_id}'},
                           json={'projection': proj})
        if r.status_code in (200, 204):
            ok += 1
        else:
            fail += 1
            if fail <= 5:
                print(f'  !! id={_id} {r.status_code} {r.text[:140]}')
        if ok and ok % 250 == 0:
            print(f'  ...{ok} patched')
    print(f'\npatched {ok}/{len(patches)}  failed {fail}')


if __name__ == '__main__':
    main()
