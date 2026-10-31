"""Undo the 3 duplicate receipts this reconciler minted, and stop the cause.

== 2026-10-31 . A BLOB-VS-RECEIPTS CHECK CANNOT CATCH THIS ===============
reconcile_card_recap_to_receipts added picks back onto the card carrying
only pick/line/odds/tier/result. public_receipt.sweat_card_rows anchors a
card item with

    anchor = source_key or game_id or id        (':' prefix stripped)
    if not anchor: anchor = f'{game}|{label}'
    source_id = f'{market}:{anchor}'

and the duplicate guard is a PARTIAL UNIQUE INDEX on
(sport, surface, game_date, source_id). With no game_id the added items
anchored on the fallback, so backfill_public_receipts inserted a SECOND
receipt for three picks that were already in the table:

    3981656  rl:401909279              live           <- keep
    3982789  rl:|Detroit Pistons -8.5  reconstructed  <- void

    3981856  rl:401909281              live           <- keep
    3982790  rl:|Orlando Magic +5.5    reconstructed  <- void

    3981857  rl:401909285              live           <- keep
    3982791  rl:|Golden State Warriors +3.5  reconstructed  <- void

All three are 10-30 NBA and all six copies read WIN, so the home-card
record and the receipts AGREED on a figure 3 wins too high. The delta-0
check I ran against the receipts an hour ago could not see it: both sides
of that comparison were counting the same duplicate
(feedback_dedupe_key_shape_must_match).

TWO THINGS, BOTH NEEDED:

  (1) VOID the reconstructed copy. `result` is mutable, so this is a legal
      write, and the play survives at its live receipt -- nothing leaves
      the record. Identity-gated: it voids only a row that is
      capture_mode='reconstructed' AND has a live twin with the SAME label
      and the SAME result, so a genuinely-unique reconstructed receipt is
      never touched.

  (2) BACKFILL game_id ONTO THE BLOB ITEMS. Without this the next backfill
      re-mints them: a partial unique index that excludes voided rows would
      let the same insert through again, so voiding alone would last one
      night. Each item added by the reconciler names its own receipt in
      `_from_receipt`, so the anchor is fetched from that row rather than
      re-derived (feedback_grade_the_leg_from_the_row_it_came_from).

Confirms every write by READBACK.

USAGE
  python repair_reconciled_card_item_identity.py
  python repair_reconciled_card_item_identity.py --apply
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
H_R = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
H_W = dict(H_R)
H_W.update({'Content-Type': 'application/json',
            'Prefer': 'return=representation'})

BLOBS = ('top_8', 'unified_top_picks')
STAMP = 'reconcile_card_recap_to_receipts'
VOIDS = ('VOID', 'VOIDED')


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                      # noqa: BLE001
            return None
    return v if isinstance(v, (dict, list)) else None


def norm(v):
    return ' '.join(str(v or '').split()).lower()


def get(path, **params):
    r = requests.get(SB + '/rest/v1/' + path, headers=H_R, timeout=90,
                     params=params)
    if r.status_code != 200:
        print('  ! %s HTTP %s %s' % (path, r.status_code, r.text[:140]))
        return None
    return r.json()


def anchor_fields(x) -> dict:
    out = {}
    if x.get('game_id'):
        out['game_id'] = x['game_id']
    if x.get('matchup'):
        out['game'] = x['matchup']
    if x.get('conviction') is not None:
        out['conviction'] = x['conviction']
    if x.get('pick_label'):
        out['label'] = x['pick_label']
    if str(x.get('market') or '').lower() in ('prop', 'props') \
            and x.get('player_name') and x.get('prop_type'):
        out['source_key'] = '%s|%s|%s' % (x['player_name'], x['prop_type'],
                                          x.get('pick_line'))
    return out


def void_duplicates(apply: bool) -> int:
    """Void every reconstructed receipt that duplicates a live one."""
    print('=== (1) reconstructed receipts that duplicate a live receipt ===')
    rows = get('public_receipts',
               select='id,game_date,sport,surface,market,pick_label,result,'
                      'capture_mode,source_id,game_id',
               capture_mode='eq.reconstructed',
               published_at='gte.2026-10-31T00:00:00+00:00', limit=500)
    if rows is None:
        print('  READ FAILED -- nothing voided')
        return 1
    print('  %d reconstructed receipt(s) created today' % len(rows))
    failed = done = 0
    for x in rows:
        if str(x.get('result') or '').upper() in VOIDS:
            continue
        twins = get('public_receipts',
                    select='id,pick_label,result,capture_mode',
                    game_date='eq.%s' % x['game_date'],
                    surface='eq.%s' % x['surface'], limit=400) or []
        live = [t for t in twins
                if t['id'] != x['id']
                and norm(t['pick_label']) == norm(x['pick_label'])
                and str(t.get('capture_mode')) == 'live'
                and str(t.get('result') or '').upper() not in VOIDS]
        if not live:
            print('  KEEP %s %s %r -- no live twin, it is the only receipt'
                  % (x['id'], x['game_date'], x['pick_label']))
            continue
        # identity gate: the grade must agree, or this is not a duplicate
        # but a genuine disagreement that a human has to settle
        same = [t for t in live
                if norm(t.get('result')) == norm(x.get('result'))]
        if not same:
            print('  FLAG %s %r result=%r but live twin(s) %s -- NOT voided, '
                  'a grade disagreement is not a duplicate'
                  % (x['id'], x['pick_label'], x.get('result'),
                     [(t['id'], t['result']) for t in live]))
            failed += 1
            continue
        print('\n  VOID %s %s %-30r result=%r src_id=%r'
              % (x['id'], x['game_date'], x['pick_label'], x.get('result'),
                 x.get('source_id')))
        print('       survives as live receipt %s' % same[0]['id'])
        if not apply:
            continue
        pr = requests.patch(SB + '/rest/v1/public_receipts', headers=H_W,
                            timeout=60, params={'id': 'eq.%s' % x['id']},
                            json={'result': 'VOID',
                                  'graded_at': dt.datetime.now(
                                      dt.timezone.utc).isoformat()})
        back = pr.json() if pr.status_code in (200, 201) else []
        ok = bool(back) and str(back[0].get('result')) == 'VOID'
        if not ok:
            gg = get('public_receipts', select='result',
                     id='eq.%s' % x['id']) or []
            ok = bool(gg) and str(gg[0].get('result')) == 'VOID'
        print('       HTTP %s %s' % (pr.status_code,
                                     'CONFIRMED' if ok else 'NOT CONFIRMED'))
        done += 1 if ok else 0
        failed += 0 if ok else 1
    print('  voided %d' % done)
    return failed


def repair_items(days: int, apply: bool) -> int:
    """Give every reconciler-added blob item the anchor its receipt has."""
    print('\n=== (2) blob items added without an anchor ===')
    today = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(hours=4)).date()
    failed = 0
    for i in range(days):
        date = (today - dt.timedelta(days=i)).isoformat()
        key = 'sweat_card_%s' % date
        rows = get('jerry_cache', select='data', cache_key='eq.' + key)
        if rows is None:
            failed += 1
            continue
        if not rows:
            continue
        blob = jd(rows[0].get('data'))
        if not isinstance(blob, dict):
            continue
        fixed = []
        for name in BLOBS:
            items = blob.get(name)
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                if it.get('_added_by') != STAMP or not it.get('_from_receipt'):
                    continue
                if it.get('game_id') or it.get('source_key'):
                    continue            # already anchored
                rc = get('public_receipts',
                         select='id,market,pick_label,pick_line,game_id,'
                                'matchup,conviction,player_name,prop_type',
                         id='eq.%s' % it['_from_receipt'])
                if not rc:
                    print('  %s: receipt %s not readable -- item left alone'
                          % (date, it['_from_receipt']))
                    continue
                af = anchor_fields(rc[0])
                if not af.get('game_id') and not af.get('source_key'):
                    print('  %s: receipt %s has no game_id and no prop '
                          'identity either -- CANNOT anchor %r'
                          % (date, it['_from_receipt'], it.get('pick')))
                    failed += 1
                    continue
                it.update(af)
                it['_anchored_by'] = 'repair_reconciled_card_item_identity'
                fixed.append((name, it.get('pick'), af.get('game_id')
                              or af.get('source_key')))
        if not fixed:
            continue
        print('  %s: anchoring %d item(s)' % (date, len(fixed)))
        for name, pick, anch in fixed:
            print('      [%s] %-32r -> %s' % (name, pick, anch))
        if not apply:
            continue
        pr = requests.patch(SB + '/rest/v1/jerry_cache', headers=H_W,
                            timeout=60, params={'cache_key': 'eq.' + key},
                            json={'data': blob})
        ok = False
        back = pr.json() if pr.status_code in (200, 201) else []
        if back:
            b = jd(back[0].get('data')) or {}
            ok = all(
                it.get('game_id') or it.get('source_key')
                for nm in BLOBS for it in (b.get(nm) or [])
                if isinstance(it, dict) and it.get('_added_by') == STAMP
                and it.get('_from_receipt'))
        print('      HTTP %s %s' % (pr.status_code,
                                    'CONFIRMED' if ok else 'NOT CONFIRMED'))
        failed += 0 if ok else 1
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=35)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('[%s]' % ('APPLY' if a.apply else 'DRY RUN'))
    bad = void_duplicates(a.apply) + repair_items(a.days, a.apply)
    if not a.apply:
        print('\nRe-run with --apply to write.')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
