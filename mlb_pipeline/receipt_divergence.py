#!/usr/bin/env python3
"""Detect and repair a game_read receipt that no longer states its read's bet.

== WHY THIS EXISTS (2026-11-17) ===========================================
`public_receipts.pick_side / pick_line / pick_label` are FROZEN by the
trigger in 20260918b: it restores OLD on every update, so a PATCH returns
200 and changes nothing. That freeze is correct -- a receipt you can
rewrite is not a receipt.

But `backfill_public_receipts.backfill_jerry_reads` upserts on
(sport, surface, game_date, source_id), and `source_id` is the READ's id,
which does NOT change when the read's call changes. So when a read is
regenerated with a different call, the upsert tries to UPDATE a frozen
row, is silently refused, and the receipt keeps the pick as it stood on
the first pass.

The 10-22 fix ("game day or earlier only") killed 121 of the 156 then
known, leaving same-day regeneration as the exposure. Measured 2026-11-17
over game_read receipts since 11-01:

    non-VOID game_read receipts                873
    diverge from their read on side OR line    162
    diverge on SIDE                             95   (all NCAAB,
                                                      all reconstructed)
      and the two DISAGREE on the outcome       48   (27 receipt WIN vs
                                                      read Loss, 21 the
                                                      other way)

The shape is always the same: the receipt holds a TOTAL and the read now
holds a side -- `rec=UNDER 150.5` against `read=HOME None` -- because the
read's whole market was regenerated and the receipt froze the first one.

A LIVE receipt captured at publish time is the truth of what we showed,
and must stand even when the model later changes its mind
([[feedback_grade_the_side_we_published]]). A RECONSTRUCTED receipt was
never shown to anyone: it was rebuilt later FROM the read, so fidelity to
that read is its only claim to exist. When it diverges it is not a
receipt of anything.

THE REMEDY IS THE ONE ANDY ALREADY CHOSE. On 2026-10-25, on five NFL
game_read receipts frozen on a side we did not publish: "Void and publish
correct". Migration 20261025a exists for it -- the old unique constraint
became a partial index, so a voided row no longer occupies its slot.

    python receipt_divergence.py                 # dry run, with the delta
    python receipt_divergence.py --apply
    python receipt_divergence.py --since 2026-11-01 --sport NCAAB
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import requests

_envf = Path(__file__).with_name('.env')
if _envf.exists():
    for _l in _envf.read_text().split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY') or os.environ.get(
    'SUPABASE_SERVICE_ROLE_KEY')
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

#: Markets that mean the same bet under different spellings.
_MKT = {'rl': 'spread', 'run_line': 'spread', 'runline': 'spread',
        'pl': 'spread', 'puckline': 'spread', 'puck_line': 'spread',
        'ats': 'spread', 'moneyline': 'ml', 'money_line': 'ml',
        'ou': 'total', 'o/u': 'total'}

#: Columns the replacement must NOT carry over from the voided row.
_SKIP = {'id', 'result', 'graded_at', 'actual_value'}


def norm_mkt(m) -> str:
    m = str(m or '').strip().lower()
    return _MKT.get(m, m)


def norm_side(s) -> str:
    return str(s or '').strip().upper()


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def identity(market, side, line) -> tuple:
    ln = _f(line)
    return (norm_mkt(market), norm_side(side),
            None if ln is None else round(ln, 2))


def diverged(receipt: dict, read: dict) -> bool:
    """True when the receipt states a different bet than the read it cites.

    Conservative on purpose: a receipt whose side is unknown, or a read
    with no call yet, is NOT a divergence -- it is missing information,
    and voiding on missing information destroys a good record
    ([[feedback_any_null_finding_must_be_investigated]]).
    """
    r_id = identity(receipt.get('market'), receipt.get('pick_side'),
                    receipt.get('pick_line'))
    k_id = identity(read.get('call_market'), read.get('call_side'),
                    read.get('call_line'))
    if not r_id[1] or not k_id[1]:
        return False
    if not k_id[0]:
        return False
    return r_id != k_id


def paged(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_R, timeout=60,
                         params={**params, 'offset': str(off),
                                 'limit': '1000'})
        if r.status_code != 200:
            sys.exit(f'read of {table} failed {r.status_code}: {r.text[:200]}')
        j = r.json() or []
        out += j
        if len(j) < 1000:
            return out
        off += 1000


def void_receipt(rid) -> bool:
    """Void one receipt, CONFIRMED BY READBACK.

    A PATCH against this table returns 200 with nothing changed when the
    trigger refuses it, so the status code proves nothing
    ([[feedback_204_is_not_a_write]]).
    """
    requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W, timeout=30,
                   params={'id': f'eq.{rid}'},
                   json={'result': 'VOID',
                         'graded_at': dt.datetime.now(
                             dt.timezone.utc).isoformat()})
    g = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R, timeout=30,
                     params={'id': f'eq.{rid}', 'select': 'result'})
    j = g.json() if g.status_code == 200 else []
    return bool(j and str(j[0].get('result')).upper() == 'VOID')


def _american(v):
    f = _f(v)
    if f is None:
        return None
    return int(round(f))


def _norm_result(v):
    s = str(v or '').strip().lower()
    return {'w': 'WIN', 'win': 'WIN', 'l': 'LOSS', 'loss': 'LOSS',
            'p': 'PUSH', 'push': 'PUSH', 'v': 'VOID',
            'void': 'VOID'}.get(s, s.upper() or None)


def replacement_from_read(old: dict, read: dict) -> dict:
    """Compose the receipt the read says it should be.

    Every column except the identity and the grade is carried from the
    voided row verbatim, so a replacement cannot quietly change anything
    the divergence was not about.
    """
    body = {k: v for k, v in old.items() if k not in _SKIP}
    body['market'] = read.get('call_market')
    body['pick_side'] = norm_side(read.get('call_side')) or None
    body['pick_line'] = _f(read.get('call_line'))
    body['pick_label'] = read.get('call_text')
    body['pick_odds'] = _american(read.get('price_american'))
    body['conviction'] = read.get('conviction')
    body['result'] = _norm_result(read.get('result'))
    body['graded_at'] = read.get('resolved_at')
    body['capture_mode'] = 'reconstructed'
    return body


def find(since: str, sport: str | None = None,
         ungraded_only: bool = False) -> tuple:
    """Return (live_receipts, diverged_pairs, unresolved_count).

    `ungraded_only` is the automatic-repair case. An UNGRADED diverged
    receipt has no record consequence yet, so voiding and republishing it
    is pure correctness and stops the wrong row ever grading. A GRADED one
    is a published number, and changing it is a decision, not a cleanup --
    that is why this function can look at either set and only the caller
    decides which.
    """
    p = {'select': '*', 'surface': 'eq.game_read',
         'game_date': f'gte.{since}', 'order': 'id'}
    if sport:
        p['sport'] = f'eq.{sport}'
    if ungraded_only:
        p['result'] = 'is.null'
    rec = [x for x in paged('public_receipts', p)
           if str(x.get('result') or '').upper() != 'VOID']
    ids = sorted({str(x['source_id']) for x in rec if x.get('source_id')})
    reads: dict = {}
    for i in range(0, len(ids), 150):
        q = {'select': 'id,sport,call_market,call_side,call_line,call_text,'
                       'conviction,result,price_american,resolved_at',
             'id': 'in.(%s)' % ','.join(ids[i:i + 150])}
        for y in paged('jerry_reads', q):
            reads[str(y['id'])] = y
    hits, unresolved = [], 0
    for x in rec:
        y = reads.get(str(x.get('source_id')))
        if not y:
            unresolved += 1
            continue
        if diverged(x, y):
            hits.append((x, y))
    return rec, hits, unresolved


def repair(hits: list) -> tuple:
    """Void each diverged receipt and republish it from its read.

    Returns (voided, replaced, failed). A void that is not confirmed by
    readback leaves the row live and skips its replacement, because the
    alternative -- posting the replacement anyway -- would put BOTH sides
    of the same bet in the record.
    """
    voided = replaced = failed = 0
    for x, y in hits:
        if not void_receipt(x['id']):
            print(f"  id={x['id']} VOID NOT CONFIRMED - left live, "
                  f"no replacement attempted")
            failed += 1
            continue
        voided += 1
        body = replacement_from_read(x, y)
        r = requests.post(f'{SB}/rest/v1/public_receipts', headers=H_W,
                          timeout=30, json=[body])
        if r.status_code in (200, 201) and r.json():
            replaced += 1
        else:
            print(f"  id={x['id']} voided but REPLACEMENT FAILED "
                  f"{r.status_code}: {r.text[:160]}")
            print('     this play is now OUT of the record until it is '
                  'republished - fix and re-run')
            failed += 1
    return voided, replaced, failed


def repair_ungraded(sport: str, since: str = '2026-11-01') -> int:
    """Automatic pre-pass for backfill_public_receipts.

    Only UNGRADED rows, so no published number changes. Best-effort and
    loud: it prints what it did and never raises, because a repair hiccup
    must not stop the backfill it runs in front of.
    """
    try:
        _rec, hits, _u = find(since, sport, ungraded_only=True)
    except SystemExit as e:                                  # paged() exits
        print(f'  divergence pre-pass skipped (read failed): {e}')
        return 0
    if not hits:
        return 0
    print(f'  divergence pre-pass: {len(hits)} UNGRADED receipt(s) no '
          f'longer state their read\'s bet - voiding and republishing')
    for x, y in hits[:10]:
        print(f"    id={x['id']} {(x.get('matchup') or '')[:28]:<28} "
              f"{norm_mkt(x.get('market'))} {norm_side(x.get('pick_side'))} "
              f"{x.get('pick_line')} -> {norm_mkt(y.get('call_market'))} "
              f"{norm_side(y.get('call_side'))} {y.get('call_line')}")
    v, rp, f_ = repair(hits)
    print(f'  divergence pre-pass: voided {v}  replaced {rp}  failed {f_}')
    return rp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-11-01')
    ap.add_argument('--sport')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--ungraded-only', action='store_true',
                    help='only rows with no result yet - changes no '
                         'published number')
    a = ap.parse_args()
    if not (SB and KEY):
        print('SUPABASE env missing')
        return 2

    rec, hits, unresolved = find(a.since, a.sport, a.ungraded_only)
    print(f'=== diverged game_read receipts . since {a.since} '
          f'{"- " + a.sport if a.sport else ""} ===')
    print(f'  live (non-VOID) game_read receipts: {len(rec)}')
    if unresolved:
        print(f'  !! {unresolved} receipt(s) cite a read id that does not '
              f'resolve - NOT touched, investigate separately')
    print(f'  diverged: {len(hits)}')
    if not hits:
        print('  nothing to repair.')
        return 0

    # The record delta, stated BEFORE anything is written, because voiding
    # a graded receipt changes a published number.
    def tally(pick):
        c = {'WIN': 0, 'LOSS': 0, 'PUSH': 0, 'OTHER': 0}
        for t in hits:
            r = str(pick(t) or '').upper()
            c[r if r in c else 'OTHER'] += 1
        return c
    before = tally(lambda t: t[0].get('result'))
    after = tally(lambda t: _norm_result(t[1].get('result')))
    print(f'\n  record on these rows now   : '
          f"{before['WIN']}W-{before['LOSS']}L-{before['PUSH']}P "
          f"(+{before['OTHER']} other)")
    print(f'  record the reads call for  : '
          f"{after['WIN']}W-{after['LOSS']}L-{after['PUSH']}P "
          f"(+{after['OTHER']} other)")
    print(f"  net: {after['WIN'] - before['WIN']:+d}W "
          f"{after['LOSS'] - before['LOSS']:+d}L\n")

    for x, y in hits[:40]:
        print(f"  id={x['id']:<9} {x['sport']:<5} {x['game_date']} "
              f"{(x.get('matchup') or '')[:30]:<30} "
              f"receipt {norm_mkt(x.get('market')):<6} "
              f"{norm_side(x.get('pick_side')):<5} {str(x.get('pick_line')):<7}"
              f"({x.get('result')})  ->  read "
              f"{norm_mkt(y.get('call_market')):<6} "
              f"{norm_side(y.get('call_side')):<5} {str(y.get('call_line')):<7}"
              f"({y.get('result')})")
    if len(hits) > 40:
        print(f'  ... and {len(hits) - 40} more')

    if not a.apply:
        print('\n  DRY RUN - nothing written. Re-run with --apply.')
        print('  --apply VOIDS each row above and republishes it from its '
              'read. Both numbers above are published records, so this is '
              'a decision, not a cleanup.')
        return 1

    voided, replaced, failed = repair(hits)
    print(f'\n  voided {voided}   replaced {replaced}   failed {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
