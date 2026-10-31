"""Make the home-screen card recap agree with the receipts.

== 2026-10-31 . A 5-0 NIGHT RENDERED AS 2-0 ==============================
ANDY: "Noticed sweat card recap only ahs 3 plays and not graded on home
page."

Both halves of that are real, and they are the same cause. For 2026-10-30:

    public_receipts (surface=sweat_card)   5 WIN + 1 VOID
    jerry_cache.sweat_card_2026-10-30
        unified_top_picks                  3 items   <- the 3 he can see
        top_8                              2 items
        top_8_summary   {"wins":2,"losses":0,"pushes":0,"pending":0,"resolved":2}

RecapStrip.tsx builds BOTH the yesterday figure and the CARD L30D rollup by
summing `top_8_summary` across the last 30 `sweat_card_%` rows, so the home
screen reports a 5-0 card as 2-0 and the 30-day number carries the same
shortfall on every day this has happened.

WHY THE PICKS ARE MISSING. The row's `fetched_at` is 22:20:34, the exact
second Minnesota -7.5 was published -- a recompose replaced Orlando Magic
+5.5 and Golden State Warriors +3.5 in the same slots. The blob keeps only
the FINAL composition; the receipts keep every pick that was ever live.
Andy's 10-30 ruling settles which is right: "unvoid so they grade", because
"the card really did show five picks to whoever was looking across the
evening". A record that drops the plays it later replaced is not a record.

refresh_card_grades.py cannot fix this. It re-resolves PENDING grades on the
picks already in the blob, and all three of these already read 'Win'. The
defect is the composition, not the grading.

WHAT THIS DOES. For each card date in the window, adds back every sweat_card
receipt that is missing from the blob, using the RECEIPT's own line, odds,
tier and result -- the frozen row is authoritative and is not re-derived
(feedback_grade_the_leg_from_the_row_it_came_from). Then recomputes
`top_8_summary` from the union.

WHAT IT DOES NOT DO:
  * VOIDED receipts stay out. Houston Rockets -6.5 was withdrawn for failing
    coherence -- our own projection had Houston by 5.5 while we laid -6.5 --
    which is a property of the pick, not bookkeeping.
  * it never changes a result that is already decided, and never invents one.
    A receipt with a null result is added as Pending so refresh_card_grades
    can settle it later.
  * it does not remove anything. A pick in the blob with no receipt is left
    alone and reported, because the missing receipt is its own bug.

USAGE
  python reconcile_card_recap_to_receipts.py --date 2026-10-30
  python reconcile_card_recap_to_receipts.py --date 2026-10-30 --apply
  python reconcile_card_recap_to_receipts.py --days 30 --apply
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

VOIDS = ('VOID', 'VOIDED')
WON = ('WIN', 'W', 'WON')
LOST = ('LOSS', 'L', 'LOST')
PUSHED = ('PUSH', 'TIE')
#: the two lists RecapStrip and the home card read
BLOBS = ('top_8', 'unified_top_picks')
from card_summaries import PAIRS as _PAIRS


def _et_today() -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=4)).date().isoformat()


def norm(lbl) -> str:
    """Compare a receipt label to a blob pick on the text alone, lowercased
    and whitespace-collapsed. No number stripping: the NUMBER is part of the
    identity here -- 'Warriors +1.5' and 'Warriors +3.5' were two different
    published quotes on 10-30 and must not collapse into one."""
    return ' '.join(str(lbl or '').split()).lower()


def result_word(res) -> str:
    r = str(res or '').upper()
    if r in WON:
        return 'Win'
    if r in LOST:
        return 'Loss'
    if r in PUSHED:
        return 'Push'
    return 'Pending'


def anchor_fields(x) -> dict:
    """Carry the receipt's IDENTITY onto the item, not just its text.

    == 2026-10-31 . THIS SCRIPT MINTED 3 DUPLICATE RECEIPTS ==============
    The first version of this function did not exist: an added item carried
    only pick/line/odds/tier/result. public_receipt.sweat_card_rows anchors
    a card item with

        anchor = source_key or game_id or id      (':' prefix stripped)
        if not anchor: anchor = f'{game}|{label}'
        source_id = f'{market}:{anchor}'

    and the duplicate guard is a PARTIAL UNIQUE INDEX on
    (sport, surface, game_date, source_id). With no game_id my items
    anchored on the fallback, so a pick already in the table as

        3981656  rl:401909279               capture_mode=live

    came back from backfill_public_receipts a second time as

        3982789  rl:|Detroit Pistons -8.5   capture_mode=reconstructed

    Different key, same bet. The index could not see the twin, so the insert
    was legal. Three 10-30 NBA WINs duplicated that way, and because both
    copies read WIN, the card record and the receipts AGREED on a number
    that was 3 wins too high -- a blob-vs-receipts check cannot catch it
    (feedback_dedupe_key_shape_must_match).

    So the dedupe key has to match in SHAPE, which means copying the anchor
    the card itself uses. For a prop that anchor is the structured identity
    "{player}|{prop_type}|{line}", which is also what lets
    grade_public_receipts find the row at all (the 2026-10-04 lesson quoted
    inside sweat_card_rows).
    """
    out = {}
    if x.get('game_id'):
        out['game_id'] = x['game_id']
    if x.get('matchup'):
        out['game'] = x['matchup']
    if x.get('conviction') is not None:
        out['conviction'] = x['conviction']
    # native items key off `label`; sweat_card_rows reads label-then-pick
    if x.get('pick_label'):
        out['label'] = x['pick_label']
    if str(x.get('market') or '').lower() in ('prop', 'props') \
            and x.get('player_name') and x.get('prop_type'):
        out['source_key'] = '%s|%s|%s' % (x['player_name'], x['prop_type'],
                                          x.get('pick_line'))
    return out


def summarise(items) -> dict:
    w = l = p = pend = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        r = str(it.get('result') or '').upper()
        if r in WON:
            w += 1
        elif r in LOST:
            l += 1
        elif r in PUSHED:
            p += 1
        else:
            pend += 1
    return {'wins': w, 'losses': l, 'pushes': p, 'pending': pend,
            'resolved': w + l + p}


def run_date(date: str, apply: bool) -> int:
    key = f'sweat_card_{date}'
    cr = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H_R, timeout=60,
                      params={'select': 'cache_key,data',
                              'cache_key': f'eq.{key}'})
    if cr.status_code != 200:
        print(f'  ! {key} HTTP {cr.status_code} {cr.text[:120]} '
              f'-- CANNOT VERIFY')
        return 1
    rows = cr.json()
    if not rows:
        print(f'  {date}: no {key} row')
        return 0
    blob = rows[0].get('data')
    if isinstance(blob, str):
        blob = json.loads(blob)
    if not isinstance(blob, dict):
        print(f'  {date}: {key} data is not an object')
        return 1

    rr = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R, timeout=60,
                      params={'select': 'id,sport,market,tier,pick_label,'
                                        'pick_line,pick_odds,result,'
                                        'published_at,game_id,matchup,'
                                        'conviction,player_name,prop_type',
                              'game_date': f'eq.{date}',
                              'surface': 'eq.sweat_card',
                              'order': 'published_at.asc'})
    if rr.status_code != 200:
        print(f'  ! receipts HTTP {rr.status_code} {rr.text[:120]} '
              f'-- CANNOT VERIFY')
        return 1
    receipts = [x for x in rr.json()
                if str(x.get('result') or '').upper() not in VOIDS]
    voided = len(rr.json()) - len(receipts)

    changed = False
    for name in BLOBS:
        items = blob.get(name)
        if not isinstance(items, list):
            continue
        have = {norm(it.get('pick') or it.get('label'))
                for it in items if isinstance(it, dict)}
        added = []
        for x in receipts:
            if norm(x.get('pick_label')) in have:
                continue
            item = {
                'sport': x.get('sport'),
                'type': ('POTD' if str(x.get('market') or '').lower() == 'potd'
                         else str(x.get('market') or '').lower()),
                'pick': x.get('pick_label'),
                'line': x.get('pick_line'),
                'odds': x.get('pick_odds'),
                'tier': x.get('tier'),
                'result': result_word(x.get('result')),
                '_from_receipt': x.get('id'),
                '_added_by': 'reconcile_card_recap_to_receipts',
            }
            item.update(anchor_fields(x))
            added.append(item)
        orphans = [norm(it.get('pick') or it.get('label'))
                   for it in items if isinstance(it, dict)
                   and norm(it.get('pick') or it.get('label'))
                   not in {norm(x.get('pick_label')) for x in receipts}]
        # == ALSO MIRROR THE GRADE OF PICKS ALREADY IN THE BLOB ========
        # Adding the missing picks is only half of it. A pick that IS in the
        # blob can still carry a stale result, and refresh_card_grades
        # reported "written" on Texas -6.5 while the blob kept reading
        # 'Pending' -- it trusted the status code. The receipts are the
        # authority on a grade, so where the blob disagrees with its own
        # receipt the receipt wins. Only fills or corrects against a
        # receipt that HAS a decided result; a null receipt result never
        # overwrites a decided blob result.
        by_label = {norm(x.get('pick_label')): x for x in receipts}
        regraded = []
        for it in items:
            if not isinstance(it, dict):
                continue
            rx = by_label.get(norm(it.get('pick') or it.get('label')))
            if not rx or not rx.get('result'):
                continue
            want = result_word(rx.get('result'))
            if str(it.get('result') or '') == want:
                continue
            regraded.append((it.get('pick') or it.get('label'),
                             it.get('result'), want))
            it['result'] = want
            it['_grade_from_receipt'] = rx.get('id')
        if regraded:
            changed = True
            for lbl, was, now in regraded:
                print(f'      ~ {lbl!r} result {was!r} -> {now!r} '
                      f'(from its receipt)')
        if added:
            blob[name] = items + added
            changed = True
        print(f'  {date} {name}: {len(items)} -> {len(items) + len(added)} '
              f'({len(added)} added from receipts)')
        for a in added:
            print(f'      + {a["pick"]!r} line={a["line"]} odds={a["odds"]} '
                  f'{a["result"]} (receipt {a["_from_receipt"]})')
        if orphans:
            print(f'      ! {len(orphans)} blob pick(s) with NO receipt, '
                  f'left alone: {orphans}')

    # ══ 2026-10-31 · BOTH SUMMARIES, OR THE SCREEN CONTRADICTS ITSELF ══
    # This used to recompute top_8_summary only. The blob carries TWO
    # lists and TWO summaries, and the app reads both: RecapStrip takes
    # the yesterday headline from top_8_summary, index.tsx:13904 takes the
    # CARD RESULT line under the visible list from unified_summary. Adding
    # picks to both lists and re-summarising one left 10-30 showing a 5-0
    # headline above a five-pick list whose own record read 3-0.
    from card_summaries import recompute as _recompute
    _sum_changes = _recompute(blob)
    if _sum_changes:
        changed = True
    for _k, _old, _new in _sum_changes:
        print(f'  {date} {_k}: {json.dumps(_old)} -> {json.dumps(_new)}')
    if not _sum_changes:
        print(f'  {date} summaries already match their lists')
    print(f'  {date} ({voided} voided receipt(s) excluded)')
    new_sum = blob.get('top_8_summary')

    if not changed:
        print(f'  {date}: already consistent, nothing to write')
        return 0
    if not apply:
        return 0
    blob['_recap_reconciled_at'] = dt.datetime.now(
        dt.timezone.utc).isoformat()
    pr = requests.patch(f'{SB}/rest/v1/jerry_cache', headers=H_W, timeout=60,
                        params={'cache_key': f'eq.{key}'},
                        json={'data': blob})
    back = pr.json() if pr.status_code in (200, 201) else []
    ok = False
    if back:
        b = back[0].get('data')
        if isinstance(b, str):
            b = json.loads(b)
        # verify BOTH summaries landed, not just the headline one
        ok = all((b or {}).get(_k) == blob.get(_k)
                 for _lk, _k in _PAIRS)
    if not ok:                       # readback, never the status code
        g = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H_R, timeout=60,
                         params={'cache_key': f'eq.{key}', 'select': 'data'})
        gj = g.json() if g.status_code == 200 else []
        if gj:
            b = gj[0].get('data')
            if isinstance(b, str):
                b = json.loads(b)
            # verify BOTH summaries landed, not just the headline one
        ok = all((b or {}).get(_k) == blob.get(_k)
                 for _lk, _k in _PAIRS)
    print(f'  {date} HTTP {pr.status_code} '
          f'{"CONFIRMED" if ok else "NOT CONFIRMED -- nothing changed"}')
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    anchor = dt.date.fromisoformat(a.date or _et_today())
    print(f'=== reconcile card recap to receipts '
          f'[{"APPLY" if a.apply else "DRY RUN"}] ===')
    bad = 0
    for i in range(a.days):
        bad += run_date((anchor - dt.timedelta(days=i)).isoformat(), a.apply)
    if not a.apply:
        print('\nRe-run with --apply to write.')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
