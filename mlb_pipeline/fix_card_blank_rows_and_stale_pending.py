"""Two things the home card is rendering wrong, both user-facing.

== 2026-10-31 . "ANYTHING THATS SURFACING INCORRECT INFO TO USERS" ========

(1) BLANK CARD ROWS. jerry_cache.sweat_card_2026-10-16 carries entries with
    `pick: null` in both top_8 and unified_top_picks, two of them reading
    result 'Pending'. A row with no pick is not a pick -- it renders as an
    empty line on the card and counts toward nothing. These are pruned.
    They are NOT from the reconciler: their `_from_receipt` is null, so they
    were in the blob before today.

(2) A 3-WEEK-OLD "PENDING" ON A SETTLED GAME. The card shows `Texas -6.5`
    from 2026-10-10 as Pending. The receipt (1511154, surface=sweat_card,
    capture_mode=live) has result null, and no grader can reach it:
    `ncaaf_game_results` row `ncaaf_20261010_Oklahoma_Texas` has
    home_score/away_score/spread_result all NULL.

    It is the documented duplicate. Oklahoma vs Texas is the Red River game
    at a NEUTRAL site, so home/away is ambiguous, and
    project_ncaaf_five_team_name_mismatch_1021 names OU@TEX specifically as
    the row pair with home/away inverted. The twin
    `ncaaf_20261010_Texas_Oklahoma` DOES carry the score:

        home=Oklahoma 27, away=Texas 24, neutral_site=true

    So Oklahoma won 27-24 and Texas lost outright by 3. `Texas -6.5` is a
    LOSS, and that conclusion needs no sign convention at all -- a 3-point
    defeat is nowhere near covering -6.5, so the orientation ambiguity that
    created the duplicate cannot change the answer. That is the only reason
    this is safe to settle by hand.

    `result` is one of the three MUTABLE receipt columns
    (result / actual_value / graded_at pass through
    freeze_receipt_identity), so this is a legal write, unlike the
    pick_line and pick_odds repairs that the trigger refused today (R96).

WHAT THIS DOES NOT DO. It does not write the missing scores into
`ncaaf_game_results`. Deriving a game result across an inverted duplicate
pair is exactly the join that produced the duplicate, and doing it under
time pressure to fix one card row is the wrong trade -- R-item instead. The
other 7 unscored NCAAF games since 09-01 are left alone for the same reason.

Confirms by READBACK. A refused PATCH returns 200 with zero rows changed
(feedback_204_is_not_a_write).

USAGE
  python fix_card_blank_rows_and_stale_pending.py
  python fix_card_blank_rows_and_stale_pending.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

BLOBS = ('top_8', 'unified_top_picks')
#: receipt id -> (grade, the derivation, written out so it is auditable)
SETTLE = {
    1511154: ('LOSS',
              'ncaaf_20261010_Texas_Oklahoma (the inverted twin of this '
              "receipt's game_id) reads home=Oklahoma 27, away=Texas 24, "
              'neutral_site=true. Texas lost outright by 3, so Texas -6.5 '
              'cannot have covered under either orientation.'),
}
LOOKBACK_DAYS = 40


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v if isinstance(v, (dict, list)) else None


def prune_blanks(apply: bool) -> int:
    today = dt.date.fromisoformat(
        (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4))
        .date().isoformat())
    touched = failed = 0
    print('=== (1) card rows with no pick ===')
    for i in range(LOOKBACK_DAYS):
        date = (today - dt.timedelta(days=i)).isoformat()
        key = f'sweat_card_{date}'
        r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H_R, timeout=60,
                         params={'select': 'data', 'cache_key': f'eq.{key}'})
        if r.status_code != 200 or not r.json():
            continue
        blob = jd(r.json()[0].get('data'))
        if not isinstance(blob, dict):
            continue
        removed = []
        for name in BLOBS:
            items = blob.get(name)
            if not isinstance(items, list):
                continue
            keep = []
            for it in items:
                label = (it.get('pick') or it.get('label')
                         if isinstance(it, dict) else None)
                if isinstance(it, dict) and not str(label or '').strip():
                    removed.append((name, it.get('type'), it.get('result')))
                    continue
                keep.append(it)
            if len(keep) != len(items):
                blob[name] = keep
        if not removed:
            continue
        print(f'  {date}: pruning {len(removed)} blank row(s)')
        for name, typ, res in removed:
            print(f'      - [{name}] type={typ!r} result={res!r} '
                  f'(no pick text)')
        if not apply:
            continue
        blob['_blank_rows_pruned_at'] = dt.datetime.now(
            dt.timezone.utc).isoformat()
        pr = requests.patch(f'{SB}/rest/v1/jerry_cache', headers=H_W,
                            timeout=60, params={'cache_key': f'eq.{key}'},
                            json={'data': blob})
        back = pr.json() if pr.status_code in (200, 201) else []
        ok = False
        if back:
            b = jd(back[0].get('data')) or {}
            ok = all(str((it.get('pick') or it.get('label') or '')).strip()
                     for nm in BLOBS for it in (b.get(nm) or [])
                     if isinstance(it, dict))
        print(f'      HTTP {pr.status_code} '
              f'{"CONFIRMED" if ok else "NOT CONFIRMED"}')
        touched += 1 if ok else 0
        failed += 0 if ok else 1
    if not touched and not failed:
        print('  none found')
    return failed


def settle_stale(apply: bool) -> int:
    print('\n=== (2) receipts a grader cannot reach, settled from the twin ===')
    failed = 0
    for rid, (grade, why) in SETTLE.items():
        g = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                         timeout=60,
                         params={'id': f'eq.{rid}',
                                 'select': 'id,game_date,pick_label,result'})
        rows = g.json() if g.status_code == 200 else []
        if not rows:
            print(f'  {rid}: NOT FOUND')
            failed += 1
            continue
        cur = rows[0]
        if cur.get('result'):
            print(f'  {rid} {cur["pick_label"]!r}: already '
                  f'{cur["result"]!r} -- left alone')
            continue
        print(f'  {rid} {cur["game_date"]} {cur["pick_label"]!r} '
              f'-> {grade}')
        print(f'      why: {why}')
        if not apply:
            continue
        pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                            timeout=60, params={'id': f'eq.{rid}'},
                            json={'result': grade,
                                  'graded_at': dt.datetime.now(
                                      dt.timezone.utc).isoformat()})
        back = pr.json() if pr.status_code in (200, 201) else []
        ok = bool(back) and str(back[0].get('result')) == grade
        if not ok:                       # readback, never the status code
            gg = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                              timeout=60, params={'id': f'eq.{rid}',
                                                  'select': 'result'})
            gj = gg.json() if gg.status_code == 200 else []
            ok = bool(gj) and str(gj[0].get('result')) == grade
        print(f'      HTTP {pr.status_code} '
              f'{"CONFIRMED" if ok else "NOT CONFIRMED"}')
        failed += 0 if ok else 1
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print(f'[{"APPLY" if a.apply else "DRY RUN"}]')
    bad = prune_blanks(a.apply) + settle_stale(a.apply)
    if not a.apply:
        print('\nRe-run with --apply to write.')
    print('\nAfter applying, re-run reconcile_card_recap_to_receipts to '
          'recompute\nthe summaries, then the home screen agrees with the '
          'receipts again.')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
