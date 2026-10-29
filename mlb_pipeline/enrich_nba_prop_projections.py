"""Enrich today's NBA props with Sleeper projected values + edge vs book line (2026-08-17).

For each nba_pipeline_props row today:
  1. Look up player in nba_player_projections
  2. Map prop_type → projected field (pts_over → proj_pts, etc.)
  3. Compute proj_vs_line_edge (projected - book_line)
  4. Patch onto prop row so signals in signal_sources can read via ctx.

Runs AFTER nba_sleeper_projections_pull.py + nba_generate_props.py in
workflow, BEFORE prop_ensemble_scorer.py.

CLI:
  python enrich_nba_prop_projections.py                 # today
  python enrich_nba_prop_projections.py --date 2026-10-25
  python enrich_nba_prop_projections.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Prop type base → projections column
# ══ 2026-10-28 · THE BOARD AND THIS TABLE SPELL TWO FAMILIES DIFFERENTLY ══
# `base` below is prop_type.split('_')[0], and the board's own vocabulary is
# `steals_over` / `blocks_over`, not `stl` / `blk`. So every steals and
# blocks prop fell straight through `if not proj_col: continue` -- 510 of
# tonight's 1,945 props, 26.2% of the board, dropped on spelling alone:
#
#   steals_over 144 + steals_under 144 + blocks_over 111 + blocks_under 111
#
# The projections were there the whole time: proj_stl is populated on 566 of
# 1,976 source rows and proj_blk on 557, the same ~28.6% as pts/reb/ast.
# Both spellings are kept so neither vocabulary can break it again.
PROP_TO_PROJ = {
    'pts':      'proj_pts',
    'reb':      'proj_reb',
    'ast':      'proj_ast',
    'stl':      'proj_stl',
    'steals':   'proj_stl',      # the spelling the props board actually uses
    'blk':      'proj_blk',
    'blocks':   'proj_blk',      # ditto
    'threes':   'proj_threes',
    'turnovers': 'proj_turnovers',
    'pra':      'proj_pra',
    'pr':       'proj_pr',
    'pa':       'proj_pa',
    'ra':       'proj_ra',
}

# ══ 2026-10-28 · THE COMBO FAMILIES ARE A SUM WE ALREADY HOLD ══
# proj_pra is populated on 13 of 1,976 source rows (0.7%) while proj_pts,
# proj_reb and proj_ast each sit at 568 (28.7%), so `pra` mapped correctly
# and still produced nothing -- 264 props tonight.
#
# A combo projection is the SUM of its parts, exactly: E[X+Y+Z] =
# E[X]+E[Y]+E[Z] holds whatever the correlation between them, so the derived
# mean is not an approximation. (Its VARIANCE would need the covariance,
# which is why this derives the projection only and asserts nothing about a
# distribution.)
#
# Verified against real rows: Anthony Davis 22.0 + 10.6 + 3.1 = 35.7 against
# a 34.5 line; Alex Sarr 16.4 + 7.2 + 2.4 = 25.9 against 19.5.
#
# Derived values are stamped in `signals._projection_derived_from` so a
# derived number is never mistaken for one the source supplied.
COMBO_PARTS = {
    'proj_pra': ('proj_pts', 'proj_reb', 'proj_ast'),
    'proj_pr':  ('proj_pts', 'proj_reb'),
    'proj_pa':  ('proj_pts', 'proj_ast'),
    'proj_ra':  ('proj_reb', 'proj_ast'),
}


def _paged(path: str, params: str) -> list:
    """Read every row. PostgREST caps an unpaged request at 1000.

    ══ 2026-10-28 · BOTH READS HERE WERE CAPPED ══
    The projections load asked for `limit=1000` against a table holding
    1,976 rows, and the props load asked for no limit at all, which
    PostgREST silently caps at 1000 against tonight's 1,945 props. So the
    enricher saw roughly half of each side and reported "matched 401/1000"
    as though 1000 were the slate.

    Same family as the prop scorer that saw 22% of an NHL board on 10-27.
    A silent cap reads as thin data, never as a failure.
    """
    out, off = [], 0
    while True:
        rr = requests.get(f'{SB}/rest/v1/{path}?{params}'
                          f'&limit=1000&offset={off}&order=id.asc',
                          headers=H_READ, timeout=30)
        if rr.status_code not in (200, 206):
            print(f'  ! {path} {rr.status_code} {(rr.text or "")[:120]}')
            return out
        b = rr.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _projection_for(proj: dict, proj_col: str):
    """(value, derived_from) — the source value, else the sum of its parts."""
    v = proj.get(proj_col)
    if v is not None:
        return float(v), None
    parts = COMBO_PARTS.get(proj_col)
    if not parts:
        return None, None
    vals = [proj.get(f) for f in parts]
    if any(x is None for x in vals):
        return None, None
    return round(sum(float(x) for x in vals), 2), list(parts)


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _current_season() -> str:
    now = datetime.now()
    return str(now.year if now.month >= 8 else now.year - 1)


def run(game_date: str | None = None, season: str | None = None, dry_run: bool = False):
    gd = game_date or _et_today()
    season = season or _current_season()
    print(f'=== enrich_nba_prop_projections · {gd} · season {season} ===')

    # 1. Load projections into name-indexed lookup
    projs = _paged('nba_player_projections', f'season=eq.{season}&select=*')
    if not projs:
        print(f'  no projections for season {season} — run nba_sleeper_projections_pull.py first')
        return

    proj_by_name = {p['player_name'].lower(): p for p in projs}
    print(f'  {len(proj_by_name)} player projections loaded')

    # 2. Load today's props
    props = _paged('nba_pipeline_props',
                   f'game_date=eq.{gd}&select=id,player_name,prop_type,'
                   f'direction,prop_line,book_line,signals')
    if not props:
        print(f'  no props on {gd}')
        return
    print(f'  {len(props)} props on {gd}')

    now_iso = datetime.now(timezone.utc).isoformat()
    matched = 0; patched = 0; failed = []
    for prop in props:
        pname_lc = (prop.get('player_name') or '').lower()
        proj = proj_by_name.get(pname_lc)
        if not proj: continue
        matched += 1
        # Map prop_type base → projection field
        base = (prop.get('prop_type') or '').split('_')[0]  # 'pts_over' → 'pts'
        proj_col = PROP_TO_PROJ.get(base)
        if not proj_col: continue
        projected_value, _derived = _projection_for(proj, proj_col)
        if projected_value is None: continue

        book_line = prop.get('prop_line')
        try: book_line = float(book_line)
        except (TypeError, ValueError): continue

        edge = round(float(projected_value) - book_line, 2)

        # 2026-08-22 FIX (silent-bug audit finding #1): _ship_nba_nhl_props.py
        # reads `p.get('projection')` but this script was only writing
        # `projected_value`. Every NBA prop projection signal
        # (nba_prop_projection_supports/_opposes/_strong) returned None
        # → zero signal contribution. Now write BOTH keys so existing readers
        # keep working AND the ensemble sees the projection.
        patch = {
            'projection':            float(projected_value),
            'projected_value':       float(projected_value),  # legacy field kept for backward compat
            'proj_vs_line_edge':     edge,
            'projections_updated_at': now_iso,
        }
        if _derived:
            # say so on the row: a summed combo is not a sourced projection
            patch['signals'] = dict(prop.get('signals') or {},
                                    _projection_derived_from=_derived)
        if not dry_run:
            pr = requests.patch(f'{SB}/rest/v1/nba_pipeline_props?id=eq.{prop["id"]}',
                                headers=H_WRITE, json=patch, timeout=10)
            if pr.status_code in (200, 204):
                patched += 1
            else:
                # 2026-09-24: this branch did not exist. Every failure was
                # counted as "not patched" and reported as a bare number, so
                # the run ended with "patched 0" and no reason given.
                #
                # That hid a total outage: `projection` above is not a column
                # on nba_pipeline_props (it has `projected_value`), and
                # PostgREST rejects the whole PATCH on an unknown column, so
                # the 8/22 fix broke the half that used to work. Verified:
                # both keys -> 400 PGRST204, projected_value alone -> 204.
                # Migration 20260924b adds the column; until it is applied
                # this prints the reason instead of swallowing it.
                failed.append((prop['id'], pr.status_code, (pr.text or '')[:160]))

        # Log the big edges
        if abs(edge) >= 2.0:
            direction_emoji = '🎯' if (edge > 0 and prop['direction']=='over') or (edge < 0 and prop['direction']=='under') else '🚫'
            print(f'  {direction_emoji} {prop["player_name"][:22]:<22} {prop["prop_type"]:<10} '
                  f'line={book_line:<5} proj={projected_value}  edge={edge:+g}')

    print(f'\n  {"[DRY] " if dry_run else ""}matched {matched}/{len(props)} · patched {patched}')
    if failed:
        codes = {}
        for _id, code, body in failed:
            codes.setdefault(code, body)
        print(f'  ⚠ {len(failed)} PATCH failure(s) — nothing was written for these:')
        for code, body in codes.items():
            print(f'      HTTP {code}: {body}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date'); p.add_argument('--season')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, season=args.season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
