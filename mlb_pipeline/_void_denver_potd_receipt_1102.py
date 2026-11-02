"""Void the superseded Denver POTD receipt so the record matches the play.

== 2026-11-02 ==============================================================
Today's POTD was anchored at 08:08 ET as Denver Nuggets ML -175 (conviction
58, scaled rank 80.7). Re-running jerry_anchor_potd picked Houston Rockets
ML -195 (conviction 61, scaled 88, LR 0.6082), which outranks it on the
engine's own sort key and clears both the -200 juice cap and the LR >= 0.60
gate. Andy approved the swap.

The swap updated jerry_cache and daily_best_bet_history. It could not update
the receipt: public_receipts identity fields are frozen by
freeze_receipt_identity, so pick_label stays Denver forever. `result` IS
mutable, and 20261025a_receipt_void_allows_replacement.sql exists for
exactly this -- void the superseded row, let the replacement be captured,
keep both so the audit trail survives.

WHY THIS IS SAFE TO DO AND NOT A RECORD REWRITE:
  * the row is UNGRADED (result NULL, graded_at NULL)
  * neither game has started -- Houston tips 20:30 ET, Denver 22:00 ET
  * nothing is being erased: the Denver row stays, marked VOID, with the
    reason and the replacement named in audit

Deliberately NOT deleting it. The void IS the audit trail
(20261025a: "DELETEing the voided row would destroy the audit trail of the
void").

  python _void_denver_potd_receipt_1102.py
  python _void_denver_potd_receipt_1102.py --apply
"""
from __future__ import annotations

import argparse
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

DATE = '2026-11-02'
#: the label that must NO LONGER be the POTD, and the one that must be.
SUPERSEDED = 'Denver Nuggets ML'
REPLACEMENT = 'Houston Rockets ML'
REASON = ('Superseded 2026-11-02: the POTD was re-anchored to Houston '
          'Rockets ML -195 (conviction 61, scaled 88, LR 0.6082), which '
          'outranks this pick on the engine own sort key and clears the '
          '-200 juice cap and the LR 0.60 gate. This row was never graded '
          'and the game had not started. Replacement recorded under the '
          'same date and surface.')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=== void superseded POTD receipt - %s ===\n'
          % ('APPLY' if a.apply else 'DRY RUN'))

    # The record must already name the replacement, or this is premature.
    h = requests.get(f'{SB}/rest/v1/daily_best_bet_history', headers=H,
                     params={'select': 'lean,game,odds_american',
                             'bet_date': f'eq.{DATE}'}, timeout=60)
    rec = (h.json() or [{}])[0] if h.status_code == 200 else {}
    print(f"  record now says: {rec.get('lean')!r} @ {rec.get('odds_american')}")
    if REPLACEMENT not in str(rec.get('lean') or ''):
        print(f'  !! the record does not name {REPLACEMENT!r} yet - REFUSING. '
              f'Voiding first would leave the day with no POTD receipt at '
              f'all.')
        return 1

    r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                     params={'select': 'id,pick_label,pick_odds,result,'
                                       'graded_at,audit,matchup',
                             'game_date': f'eq.{DATE}',
                             'surface': 'eq.potd'}, timeout=60)
    if r.status_code != 200:
        print(f'  !! receipts read HTTP {r.status_code} {r.text[:200]}')
        return 1
    rows = r.json()
    print(f'  potd receipts on {DATE}: {len(rows)}')
    for x in rows:
        print(f"     id={x['id']} {x['pick_label']!r} @ {x['pick_odds']} "
              f"result={x['result']!r} graded_at={x['graded_at']!r}")

    todo = [x for x in rows
            if SUPERSEDED in str(x.get('pick_label') or '')
            and not x.get('result') and not x.get('graded_at')]
    skipped = [x for x in rows
               if SUPERSEDED in str(x.get('pick_label') or '')
               and (x.get('result') or x.get('graded_at'))]
    if skipped:
        print(f'\n  !! {len(skipped)} matching row(s) are already settled or '
              f'graded - LEFT ALONE. A graded row needs its own repair path '
              f'(feedback_grade_each_row_not_the_date).')
    print(f'\n  to void: {len(todo)}')
    if not todo:
        print('  nothing to do.')
        return 0
    if not a.apply:
        print('  DRY RUN - nothing written. Re-run with --apply.')
        return 0

    bad = 0
    for x in todo:
        body = {'result': 'VOID',
                'audit': dict(x.get('audit') or {},
                              **{'voided_at': DATE,
                                 'voided_by': '_void_denver_potd_receipt_1102.py',
                                 'voided_reason': REASON,
                                 'superseded_by': REPLACEMENT})}
        resp = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                              params={'id': f'eq.{x["id"]}'}, json=body,
                              timeout=60)
        back = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                            params={'select': 'result',
                                    'id': f'eq.{x["id"]}'}, timeout=60)
        got = (back.json()[0].get('result')
               if back.status_code == 200 and back.json() else None)
        ok = got == 'VOID'
        bad += 0 if ok else 1
        print(f'   {"OK" if ok else "x "} id={x["id"]} HTTP {resp.status_code} '
              f'readback={got!r}')
    if bad:
        return 1
    print('\n  now re-run: python jerry_anchor_potd.py --threshold 70 --force')
    print('  (the Houston receipt is captured on that pass, once the Denver')
    print('   row no longer holds the live slot)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
