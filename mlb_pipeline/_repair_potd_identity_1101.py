"""Repair the 2026-11-01 POTD record row, and void the duplicate receipt.

== 2026-11-02 - WHAT IS WRONG ==============================================
Andy hand-set the 11-01 POTD: NFL, MIN +3.5 at -120, written 11:10:31Z. By
17:23Z the record row in daily_best_bet_history had been rewritten:

    lean           'MIN +3.5'                        <- preserved, correct
    narrative      the full Minnesota write-up       <- preserved, correct
    result         'Loss'                            <- CORRECT (DET 19-15)
    sweat_score    53                                <- CORRECT, see below
    sport          'NBA'                             <- WRONG
    game           'Boston Celtics @ Orlando Magic'  <- WRONG
    odds_american  -110                              <- WRONG

jerry_cache.best_bet_2026-11-01 stayed entirely correct (sport NFL, game
MIN @ DET, leanDisplay 'MIN +3.5', context.odds -120), which is why the app
rendered the right play all day. The damage is confined to the RECORD.

A second POTD receipt was then reconstructed from the corrupted row:

    id 4023352  NFL  'MIN +3.5'  -120  MIN @ DET                      11:35Z
    id 4067290  NBA  'MIN +3.5'  -110  Boston Celtics @ Orlando Magic 17:23Z

Both live, both ungraded, both source_id = 'potd:2026-11-01'. So the day has
two headline bets and the NBA one will be graded against a game that was
never picked.

== WHAT IS *NOT* WRONG, AND WHY THAT MATTERS ===============================
`result = 'Loss'` is RIGHT. nfl_game_results: DET 19, MIN 15 - a 4-point
home win, so MIN +3.5 loses. This repair therefore does NOT touch the grade;
it is an identity repair, not a regrade. Had the grade also been wrong it
would need its own path (feedback_grade_each_row_not_the_date).

`sweat_score = 53` is also RIGHT, which was not obvious. 53 looks like it
arrived with the NBA overwrite, but nfl_game_context for MIN @ DET carries
sweat_score 53 while nba_game_context for Boston @ Orlando carries 90. So 53
describes the NFL game. Checked rather than assumed, and left alone.

== HOW ====================================================================
PATCH BY id, never by bet_date. A PATCH keyed on the date is what stamped an
MLB Dawg's loss onto an NHL row on 10-28 and reported Seattle +117 as a loss
when it had WON 5-4 (feedback_grade_each_row_not_the_date).

Every field is read back after the write and compared to the intended value,
because a 2xx on this table has meant "changed nothing" before - the pick
lock returns 204 on a refusal, identical to success
(feedback_pick_lock_refuses_with_a_silent_204, feedback_204_is_not_a_write).

  python _repair_potd_identity_1101.py            # dry run, prints the diff
  python _repair_potd_identity_1101.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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

#: the record row, BY ID
HIST_ID = 1046
#: what the published play actually was, from jerry_cache + nfl_game_results
WANT = {'sport': 'NFL', 'game': 'MIN @ DET', 'odds_american': -120}
#: the receipt reconstructed from the corrupted row
BAD_RECEIPT_ID = 4067290
GOOD_RECEIPT_ID = 4023352


def get(table, **p):
    r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=60)
    if r.status_code != 200:
        print(f'  ! {table} HTTP {r.status_code} {r.text[:200]}')
        return []
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    mode = 'APPLYING' if a.apply else 'DRY RUN'
    print(f'=== repair 11-01 POTD identity · {mode} ===\n')

    # ── 0. re-verify the premise against live data, every run ──────────
    cache = get('jerry_cache', select='data',
                cache_key='eq.best_bet_2026-11-01')
    if not cache:
        print('  ✖ no jerry_cache row for best_bet_2026-11-01 — REFUSING')
        return 1
    d = cache[0].get('data') or {}
    g = d.get('game') if isinstance(d.get('game'), dict) else {}
    ctx = d.get('context') if isinstance(d.get('context'), dict) else {}
    live = {'sport': d.get('sport'),
            'game': g.get('matchup') or d.get('matchup'),
            'odds_american': ctx.get('odds')}
    print('  jerry_cache (the published surface, authoritative):')
    for k in WANT:
        print(f'      {k:<14} {live[k]!r}')
    if any(live[k] != WANT[k] for k in WANT):
        print(f'  ✖ jerry_cache no longer matches what this script was '
              f'written to restore ({WANT}) — REFUSING rather than writing '
              f'a stale constant.')
        return 1
    print('  ✓ matches the values hardcoded here\n')

    rows = get('daily_best_bet_history', select='*', id=f'eq.{HIST_ID}')
    if not rows:
        print(f'  ✖ no history row id={HIST_ID} — REFUSING')
        return 1
    row = rows[0]
    if str(row.get('bet_date')) != '2026-11-01':
        print(f'  ✖ id={HIST_ID} is bet_date {row.get("bet_date")}, not '
              f'2026-11-01 — REFUSING')
        return 1

    patch = {k: v for k, v in WANT.items() if row.get(k) != v}
    print(f'  history row id={HIST_ID} (bet_date {row.get("bet_date")}):')
    for k in ('sport', 'game', 'odds_american', 'sweat_score', 'lean',
              'result'):
        mark = '  ->  %r' % WANT[k] if k in patch else '   (unchanged)'
        print(f'      {k:<14} {str(row.get(k))[:46]!r}{mark}')
    print()
    if not patch:
        print('  nothing to change on the history row')

    # ── 2. the duplicate receipt ───────────────────────────────────────
    recs = get('public_receipts', select='*', surface='eq.potd',
               game_date='eq.2026-11-01', order='published_at.asc')
    print(f'  POTD receipts on 2026-11-01: {len(recs)}')
    for r in recs:
        tag = ''
        if r['id'] == BAD_RECEIPT_ID:
            tag = '   <- VOID (reconstructed from the corrupted row)'
        elif r['id'] == GOOD_RECEIPT_ID:
            tag = '   <- keep'
        print(f'      id={r["id"]}  {r["sport"]:<5} {str(r["pick_label"])[:14]:<15}'
              f'{r["pick_odds"]!s:<6} {str(r["matchup"])[:34]:<35}'
              f'result={r["result"]!s:<6}{tag}')
    bad = [r for r in recs if r['id'] == BAD_RECEIPT_ID]
    if not bad:
        print(f'  (receipt {BAD_RECEIPT_ID} not present — nothing to void)')
    elif bad[0].get('result') not in (None, '', 'None'):
        print(f'  ! receipt {BAD_RECEIPT_ID} is already graded '
              f'{bad[0]["result"]!r} — a graded row needs its own repair '
              f'path, NOT a void. REFUSING to touch it.')
        bad = []
    print()

    if not a.apply:
        print('  DRY RUN — nothing written. Re-run with --apply.')
        return 0

    # ── 3. write, BY ID ────────────────────────────────────────────────
    if patch:
        r = requests.patch(f'{SB}/rest/v1/daily_best_bet_history',
                           headers=H_W, params={'id': f'eq.{HIST_ID}'},
                           json=patch, timeout=60)
        print(f'  history PATCH -> HTTP {r.status_code}, '
              f'{len(r.json()) if r.text.strip().startswith("[") else "?"} row(s)')
    if bad:
        r = requests.patch(
            f'{SB}/rest/v1/public_receipts', headers=H_W,
            params={'id': f'eq.{BAD_RECEIPT_ID}'},
            json={'result': 'VOID',
                  'audit': dict(bad[0].get('audit') or {}, **{
                      'voided_at': '2026-11-02',
                      'voided_by': '_repair_potd_identity_1101.py',
                      'voided_reason': (
                          'reconstructed from daily_best_bet_history after '
                          'that row had been overwritten with an NBA game. '
                          'The published POTD was NFL MIN +3.5 at -120, '
                          'receipt 4023352. This row duplicated the same '
                          'source_id with the wrong sport, game and price.')})},
            timeout=60)
        print(f'  receipt VOID  -> HTTP {r.status_code}')

    # ── 4. READ BACK. A 2xx is not a write. ────────────────────────────
    print('\n  readback:')
    back = get('daily_best_bet_history', select='*', id=f'eq.{HIST_ID}')
    ok = True
    if not back:
        print('    ✖ history row vanished'); return 1
    b = back[0]
    for k, v in WANT.items():
        got = b.get(k)
        same = (float(got) == float(v)) if k == 'odds_american' and got is not None \
            else (got == v)
        print(f'    {"✓" if same else "✖"} {k:<14} {got!r}'
              f'{"" if same else f"  (wanted {v!r})"}')
        ok = ok and same
    for k in ('lean', 'result', 'sweat_score'):
        print(f'    · {k:<14} {str(b.get(k))[:46]!r}  (left alone)')
    if bad:
        rb = get('public_receipts', select='id,result,sport',
                 id=f'eq.{BAD_RECEIPT_ID}')
        got = rb[0].get('result') if rb else None
        print(f'    {"✓" if got == "VOID" else "✖"} receipt '
              f'{BAD_RECEIPT_ID} result={got!r}')
        ok = ok and got == 'VOID'
    live_now = get('public_receipts', select='id,sport,pick_odds,result',
                   surface='eq.potd', game_date='eq.2026-11-01')
    kept = [r for r in live_now if r.get('result') != 'VOID']
    print(f'    {"✓" if len(kept) == 1 else "✖"} live POTD receipts for '
          f'11-01: {len(kept)} '
          f'{[(r["id"], r["sport"], r["pick_odds"]) for r in kept]}')
    ok = ok and len(kept) == 1
    print(f'\n  {"✓ REPAIRED and verified" if ok else "✖ READBACK FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
