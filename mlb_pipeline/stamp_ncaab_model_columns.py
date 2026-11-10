#!/usr/bin/env python3
"""Persist NCAAB's own model projection onto ncaab_game_results.

== WHY (2026-11-10) =======================================================
`NCAAB_ML_TIER_CAP = "COVERAGE"` caps every NCAAB favourite-ML pick, and the
evidence for it is solid: 4,981 graded favourite-ML plays, not one price band
clears breakeven. But the note that sets the cap also names the one thing
that could ever lift it:

    "Raise it only when NCAAB ML has a graded record of OUR picks that
     clears breakeven -- which first requires persisting projected_spread
     into ncaab_game_results so the question can be asked at all."
    -- ncaab_game_context.py, above NCAAB_ML_TIER_CAP

Measured today, `ncaab_game_results` (11,949 rows):

    home_score        11,860   99.3%
    close_spread       5,314   44.5%
    home_ml_close      5,263   44.0%
    spread_result      5,240   43.9%
    projected_spread       0    0.0%   <-- and projected_total, adj_em_gap,
    home_adj_em            0    0.0%        home/away adj_oe/de, pace_avg

So the market and the result are both there and OUR NUMBER IS NOT, on every
row. The engine's NCAAB selection is unmeasurable by construction, which
means the cap can never lift on evidence -- it can only ever lift by fiat,
and an unmeasured selection must not outweigh a measured one
(feedback_unmeasured_signal_must_not_outweigh_measured).

== WHY NOTHING EVER DID THIS ==============================================
The two tables use DISJOINT game_id namespaces, which is why a plain join
writes nothing and reads as "no data":

    ncaab_game_context.game_id   a17b252a474151cdb7b2245ac963d946   (hex)
    ncaab_game_results.game_id   ncaab_20260115_Arizona_St._Arizona (slug)

Measured: of 320 ctx rows carrying projected_spread, **0** appear in
ncaab_game_results by game_id. Same gap that leaves the NCAAB line_history
join resolving 0 games.

The bridge is not invented here. ncaab_result_ids.py records that
`grade_public_receipts` already joins a receipt to its result on
`(game_date, away_team, home_team)`, so that is the established key for this
table. Measured on the 263 past ctx rows that carry a projection:
**259 match exactly (98%)**; the 4 that do not are skipped and named, never
guessed (feedback_prefix_strip_can_name_another_school -- a near-miss on a
school name is a different school, not a match).

== WHAT THIS DOES AND DOES NOT CLAIM ======================================
It makes the question ASKABLE. It does not lift the cap and it does not
change a single published pick.

Honest limits, stated rather than discovered later:
  * ncaab_game_context is a ROLLING WINDOW, not history -- 435 rows total,
    263 past ones with a projection, spanning 2026-11-01..11-09. There is no
    way to reconstruct the other ~11,600 games. 259 rows is enough to START
    a shadow record under the n>=30 gate; it is nowhere near enough to
    overturn a 4,981-row measured negative.
  * The backfill trusts that a past ctx row still holds its PRE-game
    projection. ncaab_game_context writes forward games, so a past row is
    only touched by a backfill, and the 11-09 audit found ctx rows do not
    mutate (98.9% of 1,226 byte-identical). That is evidence, not proof --
    a projection stamped from a ctx row that was refreshed post-game would
    be a leaked feature, and a leak's signature is suspicious STABILITY
    (feedback_walk_forward_misses_feature_leak). The value of this script is
    FORWARD: run nightly after result resolution and the question is
    answerable from clean data regardless.

CLI:
    python stamp_ncaab_model_columns.py                      # dry run, 10d
    python stamp_ncaab_model_columns.py --apply
    python stamp_ncaab_model_columns.py --days 30 --apply
    python stamp_ncaab_model_columns.py --date 2026-11-09 --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

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

#: The engine's own numbers. Every one exists on BOTH tables with the SAME
#: spelling -- checked against a live row of each on 2026-11-10, not
#: assumed, because this table also carries `close_home_ml` (0% filled)
#: beside `home_ml_close` (44%) and picking the wrong twin reads as NULL
#: forever (feedback_select_star_before_concluding_absent).
MODEL_COLS = (
    'projected_spread', 'projected_total', 'adj_em_gap',
    'home_adj_em', 'away_adj_em',
    'home_adj_oe', 'away_adj_oe',
    'home_adj_de', 'away_adj_de',
    'pace_avg',
)


def _et_today():
    return datetime.now(ZoneInfo('America/New_York')).date()


def _get(table: str, params: dict) -> list:
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ,
                     params=params, timeout=40)
    if r.status_code != 200:
        # A non-200 is NOT an empty table. Raising here is deliberate: an
        # emptiness that decides whether we write is the exact shape that
        # made resolve_ncaab_results insert-only for its whole life
        # (feedback_in_filter_url_truncates_silently).
        raise SystemExit(f'! {table} read failed {r.status_code}: {r.text[:300]}')
    return r.json() or []


def run(dates: list, apply: bool) -> int:
    print(f'=== stamp_ncaab_model_columns . {dates[0]}..{dates[-1]} '
          f'({len(dates)}d){"" if apply else " . DRY RUN"} ===')
    stamped = present = unmatched = ambiguous = no_proj = 0
    failures = []
    for d in dates:
        ctx = _get('ncaab_game_context', {
            'game_date': f'eq.{d}',
            'select': 'game_id,game_date,home_team,away_team,'
                      + ','.join(MODEL_COLS),
        })
        if not ctx:
            continue
        res = _get('ncaab_game_results', {
            'game_date': f'eq.{d}',
            'select': 'game_id,game_date,home_team,away_team,home_score,'
                      + ','.join(MODEL_COLS),
        })
        by_key: dict = {}
        dupes: set = set()
        for x in res:
            k = (x['game_date'], x['away_team'], x['home_team'])
            if k in by_key:
                dupes.add(k)
            by_key[k] = x
        for c in ctx:
            payload = {k: c.get(k) for k in MODEL_COLS if c.get(k) is not None}
            if 'projected_spread' not in payload:
                no_proj += 1
                continue
            k = (c['game_date'], c['away_team'], c['home_team'])
            if k in dupes:
                # Two results rows for one game is the R166 duplicate shape.
                # Stamping either would put our number on a row the grader
                # may not be the one reading.
                ambiguous += 1
                print(f'  ? AMBIGUOUS {d} {c["away_team"]} @ {c["home_team"]}: '
                      f'more than one results row - skipped')
                continue
            target = by_key.get(k)
            if target is None:
                unmatched += 1
                print(f'  - unmatched {d} {c["away_team"]} @ {c["home_team"]}')
                continue
            # Never overwrite. A value already there came from somewhere and
            # this script is not the authority on it.
            payload = {kk: vv for kk, vv in payload.items()
                       if target.get(kk) is None}
            if not payload:
                present += 1
                continue
            if not apply:
                print(f'  [DRY] {d} {c["away_team"][:18]:18s} @ '
                      f'{c["home_team"][:18]:18s} proj_spread='
                      f'{payload.get("projected_spread")} '
                      f'({len(payload)} cols) -> {target["game_id"]}')
                stamped += 1
                continue
            # PATCH by the results row's OWN game_id, never by game_date: a
            # date-keyed PATCH once stamped an MLB grade onto an NHL row
            # (feedback_grade_each_row_not_the_date).
            pr = requests.patch(
                f'{SB}/rest/v1/ncaab_game_results',
                headers=H_WRITE,
                params={'game_id': f'eq.{target["game_id"]}'},
                json=payload, timeout=40,
            )
            if pr.status_code not in (200, 204):
                failures.append(f'{target["game_id"]}: {pr.status_code} '
                                f'{pr.text[:160]}')
                continue
            # READ BACK EVERY FIELD WE SET. A PATCH that returns 200 can
            # still have dropped a column the table refuses, and verifying
            # only one field is how "106 of 106 confirmed" got printed over
            # a partial write (feedback_receipt_audit_column_is_frozen_too).
            back = pr.json() if pr.text.strip().startswith('[') else []
            if not back:
                back = _get('ncaab_game_results', {
                    'game_id': f'eq.{target["game_id"]}',
                    'select': ','.join(payload.keys())})
            if not back:
                failures.append(f'{target["game_id"]}: wrote 200 but read '
                                f'back ZERO rows')
                continue
            missed = []
            for kk, vv in payload.items():
                got = back[0].get(kk)
                if got is None or abs(float(got) - float(vv)) > 1e-6:
                    missed.append(kk)
            if missed:
                failures.append(f'{target["game_id"]}: columns did NOT '
                                f'persist: {missed}')
                continue
            stamped += 1
            print(f'  ok {d} {c["away_team"][:18]:18s} @ '
                  f'{c["home_team"][:18]:18s} {len(payload)} cols '
                  f'-> {target["game_id"]}')
    print()
    print(f'  stamped={stamped}  already-present={present}  '
          f'ctx-had-no-projection={no_proj}  unmatched={unmatched}  '
          f'ambiguous={ambiguous}')
    if failures:
        print(f'  !! {len(failures)} WRITE FAILURE(S):')
        for f in failures[:20]:
            print('     ', f)
        return 1
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=10,
                    help='how many days back from today ET (default 10)')
    ap.add_argument('--date', help='a single ET date instead of --days')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if a.date:
        dates = [a.date]
    else:
        t = _et_today()
        dates = [(t - timedelta(days=i)).isoformat()
                 for i in range(a.days, -1, -1)]
    raise SystemExit(run(dates, a.apply))
