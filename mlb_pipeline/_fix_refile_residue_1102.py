"""Clean up after the NCAAB re-file: a wrong audit stamp, and 6 duplicate reads.

== 2026-11-02 ==============================================================
_repair_ncaab_game_date_1102.py re-filed NCAAB rows onto their real play
date. Two things did not go as written, and both are mine to clean up.

(1) public_receipts.game_date IS IMMUTABLE BY DESIGN, and I should have
    checked before trying. 20260918a_public_receipts.sql:

        -- Immutability trigger: identity fields FROZEN after insert.
        -- Only result / actual_value / graded_at may change.
        NEW.game_date := OLD.game_date;

    with UNIQUE (sport, surface, game_date, source_id) -- the date is part
    of the row's identity. The PATCH returned HTTP 200 and changed nothing,
    which is the exact failure mode in feedback_fix_the_cache_not_the_frozen_receipt
    ("a receipt PATCH returns 200 with 0 rows").

    But `audit` is NOT frozen, so the stamp went in while the date did not:
    44 published receipts now claim `refiled_at: 2026-11-02` and a
    `refiled_from` for a move that never happened. A receipt asserting a
    change it did not take is worse than no stamp at all -- it is the
    same class of defect as feedback_comment_asserting_a_measurement, on a
    row a subscriber can read. This replaces it with what is actually true.

    The 44 receipts KEEP their published date. That is correct: a receipt
    records what we published and when, and the upstream cause is fixed, so
    no new receipt can be filed under a run date. void+replace
    (20261025a_receipt_void_allows_replacement.sql) is the sanctioned path
    if those rows ever need re-issuing, and that is a decision about a
    published record, not a clerical repair.

(2) jerry_reads has UNIQUE (sport, game_id, game_date). Six PATCHes failed
    because the date bug had produced TWO reads for the same game under two
    different game_dates, so merging them onto the real date collides. This
    reports them; it deletes nothing without --apply-dupes, and it keeps the
    NEWEST read by generated_at because that is the one composed from the
    most recent context.

  python _fix_refile_residue_1102.py
  python _fix_refile_residue_1102.py --apply
  python _fix_refile_residue_1102.py --apply --apply-dupes
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

for _l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}
H_W = dict(H, **{'Content-Type': 'application/json',
                 'Prefer': 'return=representation'})

sys.path.insert(0, str(Path(__file__).parent))
from sweat_dates import et_date_of                             # noqa: E402

TRUTH = ('game_date on this receipt is the job run date, not the play date '
         '(ncaab_game_context.py used today_et() before 2026-11-02). It is '
         'NOT corrected here: public_receipts.game_date is frozen by '
         'freeze_receipt_identity and is part of UNIQUE (sport, surface, '
         'game_date, source_id). The upstream cause is fixed, so no new '
         'receipt can be filed this way. Noted 2026-11-02.')


def page(table, params):
    out, off = [], 0
    while off < 100000:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params=dict(params, limit=1000, offset=off))
        if r.status_code != 200:
            print(f'  !! {table} HTTP {r.status_code} {r.text[:250]}')
            return out, False
        b = r.json()
        out += b
        if len(b) < 1000:
            return out, True
        off += 1000
    return out, False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--apply-dupes', action='store_true',
                    help='also delete the stale duplicate read per game')
    a = ap.parse_args()
    print('=== re-file residue - %s ===\n'
          % ('APPLY' if a.apply else 'DRY RUN'))

    ctx, full = page('ncaab_game_context',
                     {'select': 'game_id,game_date,game_time_et,home_team,'
                                'away_team'})
    if not ctx or not full:
        print('  !! ctx read failed or truncated - WIRING ERROR. Stopping.')
        return 1
    TRUE = {r['game_id']: et_date_of(r.get('game_time_et')) for r in ctx
            if et_date_of(r.get('game_time_et'))}
    NAME = {r['game_id']: f"{r['away_team']} @ {r['home_team']}" for r in ctx}

    # ── (1) the audit stamp I should not have written ──────────────────────
    pr, prfull = page('public_receipts',
                      {'select': 'id,game_id,game_date,surface,market,result,'
                                 'audit', 'sport': 'eq.NCAAB'})
    if not prfull:
        print('  !! receipts read truncated - stopping.')
        return 1
    bogus = [r for r in pr if (r.get('audit') or {}).get('refiled_at')]
    still = [r for r in bogus
             if r['game_id'] in TRUE and r['game_date'] != TRUE[r['game_id']]]
    print(f'[1/2] receipts carrying my refiled_* stamp: {len(bogus)}')
    print(f'      of those, game_date genuinely did NOT move: {len(still)}')
    print(f'      (a stamp on a move that did not happen -> replace with the '
          f'truth)')
    moved = len(bogus) - len(still)
    if moved:
        print(f'      !! {moved} DID move - leaving their stamp, it is honest')

    # ── (2) duplicate reads per game ──────────────────────────────────────
    jr, jrfull = page('jerry_reads',
                      {'select': 'id,game_id,game_date,result,call_market,'
                                 'call_side,conviction,generated_at',
                       'sport': 'eq.NCAAB'})
    if not jrfull:
        print('  !! reads read truncated - stopping.')
        return 1
    by_game = defaultdict(list)
    for r in jr:
        by_game[r['game_id']].append(r)
    dupes = {g: rs for g, rs in by_game.items() if len(rs) > 1}
    print(f'\n[2/2] NCAAB games with MORE THAN ONE jerry_reads row: '
          f'{len(dupes)} (of {len(by_game)} games)')
    kill = []
    for g, rs in sorted(dupes.items()):
        rs = sorted(rs, key=lambda x: str(x.get('generated_at') or ''),
                    reverse=True)
        keep, drop = rs[0], rs[1:]
        settled = [d for d in drop
                   if str(d.get('result') or '').upper()
                   in ('WIN', 'LOSS', 'PUSH', 'VOID')]
        print(f'   {NAME.get(g, g)[:46]:46s} tip {TRUE.get(g)}')
        print(f'      KEEP  id={keep["id"]} {keep["game_date"]} '
              f'{keep.get("call_market")}/{keep.get("call_side")} '
              f'conv={keep.get("conviction")} gen={str(keep.get("generated_at"))[:19]} '
              f'result={keep.get("result")}')
        for d in drop:
            print(f'      STALE id={d["id"]} {d["game_date"]} '
                  f'{d.get("call_market")}/{d.get("call_side")} '
                  f'conv={d.get("conviction")} gen={str(d.get("generated_at"))[:19]} '
                  f'result={d.get("result")}')
        if settled:
            print(f'      -> SKIPPED: a duplicate carries a settled result '
                  f'({[d["result"] for d in settled]}). A settled row needs '
                  f'its own repair path, not a delete.')
            continue
        kill += drop
    print(f'\n   stale reads deletable: {len(kill)}')

    if not a.apply:
        print('\n  DRY RUN - nothing written.')
        return 0

    print('\n--- applying (1) honest stamps ---')
    ok = bad = 0
    for r in still:
        aud = dict(r.get('audit') or {})
        aud.pop('refiled_at', None)
        aud.pop('refiled_from', None)
        aud.pop('refiled_reason', None)
        aud['game_date_is_run_date'] = TRUE.get(r['game_id'])
        aud['game_date_note'] = TRUTH
        resp = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                              params={'id': f'eq.{r["id"]}'},
                              json={'audit': aud}, timeout=60)
        back = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                            params={'select': 'audit', 'id': f'eq.{r["id"]}'},
                            timeout=60)
        got = (back.json()[0].get('audit') or {}) if (
            back.status_code == 200 and back.json()) else {}
        good = 'refiled_at' not in got and got.get('game_date_note')
        ok += 1 if good else 0
        bad += 0 if good else 1
        if not good:
            print(f'   x receipt id={r["id"]} HTTP {resp.status_code} '
                  f'readback keys={sorted(got)[:6]}')
    print(f'   stamps corrected {ok}, failed {bad}')

    if not a.apply_dupes:
        print('\n  duplicate reads NOT touched (pass --apply-dupes). '
              'Deleting a read is destructive and it is not blocking '
              'anything today.')
        return 0 if bad == 0 else 1

    print('\n--- applying (2) stale duplicate reads ---')
    dok = dbad = 0
    for d in kill:
        resp = requests.delete(f'{SB}/rest/v1/jerry_reads', headers=H_W,
                               params={'id': f'eq.{d["id"]}'}, timeout=60)
        back = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H,
                            params={'select': 'id', 'id': f'eq.{d["id"]}'},
                            timeout=60)
        gone = back.status_code == 200 and not back.json()
        dok += 1 if gone else 0
        dbad += 0 if gone else 1
        if not gone:
            print(f'   x read id={d["id"]} HTTP {resp.status_code} still present')
    print(f'   stale reads deleted {dok}, failed {dbad}')
    return 0 if (bad == 0 and dbad == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
