#!/usr/bin/env python3
"""repair_ncaab_fixture_integrity.py — two fixture defects, one repair pass.

Both were found on 2026-11-14 chasing why 13 NCAAB games from 11-01..11-12
were still unscored after a full `resolve_ncaab_results --days-back 14`.

══ 1. POSTPONED GAMES STORED AS 0-0 FINALS ══════════════════════════════
ESPN returns, verbatim, for three games on 2026-11-14:

    state=post   name=STATUS_POSTPONED   completed=False   scores=['0','0']

`resolve_ncaab_results` tested `state == 'post'` and so read a postponed
game as a FINAL. compute_outcome_patch does not sanity-check a score, so it
then derived real-looking outcomes from an impossible one:

    Air Force     @ Pacific    0-0  spread_result=away_covered  total=under
    UNC Greensboro@ Cornell    0-0  spread_result=away_covered  total=under
    Louisiana     @ UNC Wilm.  0-0

Any pick on one of those games grades against a result that never happened.
The writers are fixed (resolve_ncaab_results, ncaab_backfill_results,
ncaaf_results_espn, ufc_grader all now require `type.completed`); this
clears what they already wrote. No other sport has a single 0-0 row —
checked across NCAAF/NFL/NBA/NHL/MLB, 2,568 rows.

══ 2. ONE NEUTRAL-SITE GAME, TWO ROWS, LINE AND SCORE SPLIT ═════════════
At a neutral site there is no home team, so ESPN and the odds feed pick
opposite ones. `game_id` embeds the orientation
(`ncaab_<date>_<away>_<home>`), so neither id matched and the game went
down the INSERT path. Season to date: **14 fixtures stored twice, 28 rows
for 14 games**, and 13 of them split like this:

    ours (odds feed)    close_spread 7.5    home_score NULL
    ESPN's (inserted)   close_spread NULL   74-72

The line is on a row that never gets a score; the score is on a row with no
line. Neither grades. That is why Kentucky/Kansas, UCLA/Arizona and
Villanova/Notre Dame have no result, and why four chalk_parlay snapshots
could never settle. All 13 carry `is_neutral_site = True` in
ncaab_game_context — 13 of 13.

THE MERGE DIRECTION IS NOT ARBITRARY. The score moves ONTO the lined row,
because the line is the half that cannot be re-fetched: ESPN will hand back
a final any time, and a closing spread is gone once the book takes it down.

SCORES ARE REASSIGNED BY TEAM NAME, NEVER BY POSITION. Copying
home_score onto home_score would hand the home side the away team's points
and grade the opposite side of what we published
([[feedback_grade_the_side_we_published]],
[[feedback_magnitude_cannot_express_a_side]]). The name test must hold in
both directions or the pair is skipped.

Then `spread_result` / `total_result` are recomputed from the LINED row's
own close_spread/close_total under the stored orientation — the same
convention compute_outcome_patch uses (negative close_spread = home
favoured).

The duplicate is deleted only with --delete-dupes, and only when it carries
no line of its own; a duplicate holding a line nothing else has is data,
not noise, and is reported for a human instead.

USAGE
    python repair_ncaab_fixture_integrity.py                  # dry run, both
    python repair_ncaab_fixture_integrity.py --apply
    python repair_ncaab_fixture_integrity.py --apply --delete-dupes
    python repair_ncaab_fixture_integrity.py --only zero      # or: dupes

EXIT  0 nothing left to repair   1 repairs pending (dry run)   2 a write failed
"""
from __future__ import annotations

import argparse
import datetime as dt
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
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

TABLE = 'ncaab_game_results'
SELECT = ('game_id,game_date,home_team,away_team,home_score,away_score,'
          'close_spread,close_total,spread_result,total_result,home_win,'
          'total_points')


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_rows(since: str) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H_READ, timeout=60,
                         params={'select': SELECT, 'game_date': f'gte.{since}',
                                 'limit': 1000, 'offset': off})
        if r.status_code != 200:
            raise RuntimeError(f'read failed {r.status_code}: {r.text[:200]}')
        body = r.json() or []
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _patch(row_key, payload: dict) -> bool:
    """PATCH by id and CONFIRM BY READBACK.

    A PostgREST patch can return 200 with zero rows when the filter matches
    nothing, and a column the table refuses silently drops out of the
    payload ([[feedback_204_is_not_a_write]],
    [[feedback_receipt_audit_column_is_frozen_too]]). So every field written
    here is read back and compared.
    """
    r = requests.patch(f'{SB}/rest/v1/{TABLE}', headers=H_WRITE, timeout=60,
                       params={'game_id': f'eq.{row_key}'}, json=payload)
    if r.status_code not in (200, 204):
        print(f'      ✗ PATCH {r.status_code}: {r.text[:160]}')
        return False
    body = r.json() if r.text else []
    if not body:
        print(f'      ✗ PATCH matched 0 rows for game_id={row_key}')
        return False
    got = body[0]
    bad = [k for k, v in payload.items()
           if str(got.get(k)) != str(v)]
    if bad:
        print(f'      ✗ readback disagrees on {bad}')
        return False
    return True


# ─── defect 1: fabricated 0-0 finals ──────────────────────────────────────

def repair_zero_finals(rows: list, apply: bool) -> tuple[int, int]:
    """Clear score and derived outcome on any 0-0 'final'."""
    print('── 1. POSTPONED GAMES STORED AS 0-0 FINALS ' + '─' * 29)
    bad = [r for r in rows
           if r.get('home_score') == 0 and r.get('away_score') == 0]
    if not bad:
        print('   none')
        return 0, 0
    done = fail = 0
    for r in sorted(bad, key=lambda z: str(z['game_date'])):
        print(f"   {str(r['game_date'])[:10]} {r['away_team']} @ "
              f"{r['home_team']}  sp={r.get('spread_result')} "
              f"tot={r.get('total_result')}")
        payload = {'home_score': None, 'away_score': None,
                   'total_points': None, 'home_win': None,
                   'spread_result': None, 'total_result': None}
        if not apply:
            print('      [DRY] would clear score + derived outcomes')
            continue
        if _patch(r['game_id'], payload):
            print('      ✓ cleared')
            done += 1
        else:
            fail += 1
    return done, fail


# ─── defect 2: orientation-split fixtures ─────────────────────────────────

def find_split_pairs(rows: list) -> list:
    """(lined_row, scored_row) for every fixture stored in both orientations.

    Matched on the UNORDERED team pair within +-1 day, because the two
    feeds disagree about the date as well (ctx 11-02 vs results 11-03 on
    UCLA/Arizona).
    """
    idx: dict = {}
    for r in rows:
        key = frozenset((r.get('home_team'), r.get('away_team')))
        if len(key) != 2:
            continue
        idx.setdefault(key, []).append(r)
    pairs = []
    for key, group in idx.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.get('home_team') == b.get('home_team'):
                    continue  # same orientation — not this defect
                try:
                    da = dt.date.fromisoformat(str(a['game_date'])[:10])
                    db = dt.date.fromisoformat(str(b['game_date'])[:10])
                except Exception:
                    continue
                if abs((da - db).days) > 1:
                    continue
                a_scored = a.get('home_score') is not None
                b_scored = b.get('home_score') is not None
                if a_scored == b_scored:
                    # Both scored = a double-counted game; neither scored =
                    # nothing to move. Reported, not silently merged.
                    pairs.append((None, None, a, b))
                    continue
                scored, unscored = (a, b) if a_scored else (b, a)
                pairs.append((unscored, scored, a, b))
    return pairs


def repair_split_pairs(rows: list, apply: bool,
                       delete_dupes: bool) -> tuple[int, int]:
    print('\n── 2. ONE FIXTURE, TWO ROWS, LINE AND SCORE SPLIT ' + '─' * 22)
    pairs = find_split_pairs(rows)
    if not pairs:
        print('   none')
        return 0, 0
    done = fail = 0
    for target, source, a, b in pairs:
        if target is None:
            print(f"   ⚠ {str(a['game_date'])[:10]} {a['away_team']} @ "
                  f"{a['home_team']}  both rows "
                  f"{'scored' if a.get('home_score') is not None else 'unscored'}"
                  f" — reported, not merged")
            continue
        print(f"   {str(target['game_date'])[:10]} "
              f"{target['away_team']} @ {target['home_team']}")
        print(f"      lined  id={target['game_id']} sp={target.get('close_spread')} "
              f"tot={target.get('close_total')} score=None")
        print(f"      scored id={source['game_id']} "
              f"{source['away_team']} @ {source['home_team']} "
              f"{source.get('away_score')}-{source.get('home_score')}")

        # BY TEAM NAME. Both directions must hold or we do not touch it.
        if not (source.get('home_team') == target.get('away_team')
                and source.get('away_team') == target.get('home_team')):
            print('      ✗ team names do not mirror exactly — SKIPPED')
            continue
        hs = source.get('away_score')   # source's AWAY is target's HOME
        as_ = source.get('home_score')
        if hs is None or as_ is None:
            print('      ✗ source score incomplete — SKIPPED')
            continue
        if hs == 0 and as_ == 0:
            print('      ✗ source is a 0-0 "final" — SKIPPED (defect 1)')
            continue

        payload = {'home_score': hs, 'away_score': as_,
                   'total_points': hs + as_, 'home_win': hs > as_}
        cs = _f(target.get('close_spread'))
        if cs is not None:
            margin = hs - as_
            payload['spread_result'] = ('home_covered' if margin > -cs
                                        else 'away_covered' if margin < -cs
                                        else 'push')
        ct = _f(target.get('close_total'))
        if ct is not None:
            tot = hs + as_
            payload['total_result'] = ('over' if tot > ct
                                       else 'under' if tot < ct else 'push')
        print(f"      -> home {hs} away {as_} "
              f"sp={payload.get('spread_result')} "
              f"tot={payload.get('total_result')}")
        # THE DUPLICATE MUST GO ONCE THE MERGE LANDS. Before the merge only
        # one row of the pair was scored, so the fixture counted once. After
        # it, both would be scored AND lined, and every record derived from
        # this table — pick_ncaab_sides, the signal backtests, the blind
        # controls — would count that game TWICE, once from each side. So a
        # merged duplicate is deleted even when it carries its own line.
        #
        # Which line survives is not arbitrary: the target's orientation is
        # the one ncaab_game_context uses and the one grade_public_receipts
        # joins a receipt on, so it is the line our published picks were
        # actually written against. The discarded one is printed in full so
        # the choice is on the record rather than silent — 2 of the 4 are
        # exact mirrors of the kept line (Nebraska -2.5 = Providence +2.5),
        # and 2 differ by a point from a different capture time.
        _dup_line = source.get('close_spread')
        if not apply:
            print('      [DRY] would patch the lined row')
            if delete_dupes:
                extra = (f' (DISCARDS its own close_spread={_dup_line})'
                         if _dup_line is not None else '')
                print(f"      [DRY] would delete duplicate "
                      f"id={source['game_id']}{extra}")
            continue
        if not _patch(target['game_id'], payload):
            fail += 1
            continue
        print('      ✓ merged onto the lined row')
        done += 1
        if delete_dupes:
            if _dup_line is not None:
                print(f'      · discarding duplicate close_spread={_dup_line} '
                      f"(keeping {target.get('close_spread')} — the "
                      f'orientation receipts join on)')
            r = requests.delete(f'{SB}/rest/v1/{TABLE}', headers=H_WRITE,
                                params={'game_id': f"eq.{source['game_id']}"},
                                timeout=60)
            if r.status_code in (200, 204):
                print(f"      ✓ deleted duplicate id={source['game_id']}")
            else:
                print(f'      ✗ delete {r.status_code}: {r.text[:120]}')
                fail += 1
    return done, fail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-10-01',
                    help='earliest game_date to inspect (default 2026-10-01)')
    ap.add_argument('--apply', action='store_true',
                    help='write (default is a dry run)')
    ap.add_argument('--delete-dupes', action='store_true',
                    help='also delete a merged duplicate that carries no line')
    ap.add_argument('--only', choices=('zero', 'dupes'),
                    help='run just one of the two repairs')
    args = ap.parse_args()

    print(f'=== NCAAB fixture integrity · since {args.since} · '
          f'{"APPLY" if args.apply else "DRY RUN"} ===')
    rows = fetch_rows(args.since)
    print(f'    {len(rows)} {TABLE} rows\n')

    pending = fails = 0
    if args.only != 'dupes':
        d, f = repair_zero_finals(rows, args.apply)
        fails += f
        pending += d if not args.apply else 0
    if args.only != 'zero':
        # Re-read when the first repair wrote, so a cleared 0-0 row is not
        # then treated as a merge source.
        if args.apply and args.only != 'dupes':
            rows = fetch_rows(args.since)
        d, f = repair_split_pairs(rows, args.apply, args.delete_dupes)
        fails += f
        pending += d if not args.apply else 0

    print()
    if fails:
        print(f'✗ {fails} write(s) failed')
        return 2
    if not args.apply:
        print('dry run — rerun with --apply to write')
        return 1
    print('✓ repairs applied')
    return 0


if __name__ == '__main__':
    sys.exit(main())
