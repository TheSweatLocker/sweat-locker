"""Void receipts whose pick_line is a projection, not a posted line.

== 2026-11-02 ==============================================================
From the 11-01 audit: 57 live NCAAB game_read receipts, all priceless, and
sixteen of them carrying lines no book posts:

    Under 157.83333333333334
    Under 154.16666666666666
    Under 149.25

That is the engine's own projected_total written into the field that holds
the market's number, so the receipt records a bet that could not have been
placed. Same family as project_jerry_published_fake_lines_1019.

receipt_invariants.check_row now REFUSES these at the chokepoint every
writer passes through, so no new ones can be created. This clears the ones
already stored.

MEASURED across all 27,432 receipts since 08-01: exactly 17 rows (0.06%)
carry a pick_line that is not a multiple of 0.5 -- 16 NCAAB totals and 1 MLB
total. One of the 17 is GRADED, and that one is LEFT ALONE: a graded row is
part of the published record and needs its own repair path, not a quiet void
(feedback_grade_each_row_not_the_date).

  python _void_fabricated_line_receipts_1102.py
  python _void_fabricated_line_receipts_1102.py --apply
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

REASON = ('pick_line was the engine projected total, not a posted line — '
          'no book posts a total at that number, so this receipt recorded a '
          'bet that could not be placed. Voided 2026-11-02; '
          'receipt_invariants now refuses the shape at source.')


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def page(table, params):
    out, off = [], 0
    while off < 100000:
        r = requests.get(SB + '/rest/v1/' + table, headers=H, timeout=120,
                         params=dict(params, limit=1000, offset=off))
        if r.status_code != 200:
            print('  ! HTTP %s %s' % (r.status_code, r.text[:200]))
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=== void fabricated-line receipts · %s ===\n'
          % ('APPLY' if a.apply else 'DRY RUN'))

    rows = page('public_receipts',
                {'select': 'id,sport,surface,market,pick_label,pick_line,'
                           'pick_odds,result,game_date,audit',
                 'game_date': 'gte.2026-08-01'})
    bad = []
    for r in rows:
        if str(r.get('market') or '').lower() not in (
                'spread', 'rl', 'total', 'puckline', 'pl'):
            continue
        ln = fl(r.get('pick_line'))
        if ln is None or abs(ln * 2 - round(ln * 2)) <= 1e-6:
            continue
        bad.append(r)

    print('receipts scanned: %d' % len(rows))
    print('with a line that is not a half-point: %d\n' % len(bad))
    todo, graded = [], []
    for r in bad:
        res = str(r.get('result') or '').upper()
        (graded if res in ('WIN', 'LOSS', 'PUSH') else todo).append(r)
        mark = '  <- GRADED, left alone' if res in ('WIN', 'LOSS', 'PUSH') \
            else ''
        print('   id=%-9s %-6s %-10s %-34s line=%-20r result=%s%s'
              % (r['id'], r['sport'], r['market'],
                 str(r['pick_label'])[:34], r['pick_line'], res or None, mark))

    print('\n  to void: %d    left alone because graded: %d'
          % (len(todo), len(graded)))
    if not a.apply:
        print('  DRY RUN — nothing written. Re-run with --apply.')
        return 0

    ok = 0
    for r in todo:
        body = {'result': 'VOID',
                'audit': dict(r.get('audit') or {},
                              **{'voided_at': '2026-11-02',
                                 'voided_by': '_void_fabricated_line_receipts_1102.py',
                                 'voided_reason': REASON,
                                 'voided_line': r.get('pick_line')})}
        resp = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                              params={'id': f'eq.{r["id"]}'}, json=body,
                              timeout=60)
        back = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                            params={'select': 'result', 'id': f'eq.{r["id"]}'},
                            timeout=60)
        got = (back.json()[0].get('result')
               if back.status_code == 200 and back.json() else None)
        good = got == 'VOID'
        ok += 1 if good else 0
        print('   %s id=%s HTTP %s readback=%r'
              % ('✓' if good else '✖', r['id'], resp.status_code, got))
    print('\n  voided %d of %d' % (ok, len(todo)))
    return 0 if ok == len(todo) else 1


if __name__ == '__main__':
    sys.exit(main())
