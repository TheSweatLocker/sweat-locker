"""Settle prop receipts from the box score, so they never depend on a
mutable table again.

THE PROBLEM (measured 2026-09-25)
public_receipts is the immutable published-pick ledger, but prop receipts
inherit their result from prop_jerry_reads via source_id. Those source rows
are DELETED by cleanup_stale_coverage_props.py and dedupe_stale_prop_lines.py,
and mlb_pipeline_props is pruned the same way. On 2026-09-23 there were 229
hits_under receipts across 216 players and only 44 hits_under props left in the
source table.

So an immutable ledger inherits from a mutable, pruned one. When the source is
cleaned up the receipt is stranded at result=NULL forever — 618 of them across
09-22..09-24, of which 451 have no source row anywhere. Matching on a natural
key instead of source_id was tried first and recovered only 17%, because the
rows are gone, not renumbered.

THE FIX (Andy: "Option A")
A receipt already carries everything needed to settle itself: player_name,
prop_type, pick_line, pick_side, game_date. This grades from the MLB box score
and never consults prop_jerry_reads or mlb_pipeline_props at all. Once a
receipt is published it can always be settled, whatever gets pruned later.

Reuses grade_prop_jerry_reads' MLB API layer by import rather than copying it —
this repo has been bitten repeatedly by a second copy of a parser drifting from
the first.

THE FADE TRAP — found by --validate before anything was written.
A receipt's pick_side is NOT always the side we backed. On a FADE read we bet
the OPPOSITE side, but the receipt still records the prop's side: all 72 graded
FADE prop receipts since 09-15 display the prop side while their stored result
is for the faded side. So "Jose Quintana UNDER 10.5" carries the W/L of the
OVER. (That mislabelling is a separate receipts-integrity bug in the writer —
users are shown the wrong side — and is not fixed here.)

A settler therefore cannot assume pick_side is the backed side. But the source
read may be deleted, so the verdict is not always knowable. The guard:

  * Families that have NEVER been faded settle directly — pick_side is
    unambiguous. Measured over prop_jerry_reads history rather than hardcoded,
    because the fadeable set changes: hits_under has 0 FADE in 1,229 reads,
    while ha_over is 22.6% FADE.
  * Families that CAN be faded require the source read to confirm the verdict.
    If that row is gone, the receipt is skipped as fade_ambiguous rather than
    settled on a coin flip.

That keeps the 439 orphaned hits_under receipts settleable while refusing to
guess on the ~2% of families where a wrong guess would invert a published
result.

VALIDATE BEFORE TRUSTING
--validate re-settles receipts that ALREADY have a result and reports whether
this agrees with them. Run it first. A settler that disagrees with our own
published record is worse than no settler, and this is the only way to know
before writing anything.

    python settle_prop_receipts.py --validate --days 14
    python settle_prop_receipts.py --days 14              # dry run
    python settle_prop_receipts.py --days 14 --commit
"""
import argparse
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _line in open(os.path.join(_HERE, '.env'), encoding='utf-8'):
    if '=' in _line and not _line.startswith('#'):
        _k, _v = _line.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# The box-score layer, imported rather than duplicated.
from grade_prop_jerry_reads import (       # noqa: E402
    _MLB_STAT_MAP, _lookup_pid, _fetch_stat_for_date,
)

_BATTER_PROPS = {'hits_over', 'hits_under'}


def fadeable_families(sport: str, since: str) -> set:
    """Prop families that have EVER carried a FADE verdict.

    Derived from history rather than hardcoded — the fadeable set moves as
    discipline rules change, and a stale constant here would silently start
    mis-settling a family the day it becomes fadeable.
    """
    rows = page('prop_jerry_reads', {
        'select': 'prop_type,call_verdict', 'sport': f'eq.{sport}',
        'game_date': f'gte.{since}', 'call_verdict': 'eq.FADE'})
    return {str(x.get('prop_type') or '').lower() for x in rows}


def source_verdicts(recs: list) -> dict:
    """source_id -> (call_verdict, direction) for rows that still exist."""
    # Numeric ids only. Sharp-card receipts carry a composite string source_id
    # ("prop:Arizona Diamondbacks @ Colorado Rockies|Merrill Kelly Over 15.5
    # outs_over|15.5"), and feeding that to a bigint `id=in.()` filter 400s the
    # whole batch — taking down grading for every receipt in it, not just that
    # row.
    ids = sorted({str(r['source_id']) for r in recs
                  if r.get('source_id') and str(r['source_id']).isdigit()})
    out = {}
    for i in range(0, len(ids), 80):
        batch = ids[i:i + 80]
        for x in page('prop_jerry_reads',
                      {'select': 'id,call_verdict,direction',
                       'id': f'in.({",".join(batch)})'}):
            out[str(x['id'])] = ((x.get('call_verdict') or '').upper(),
                                 (x.get('direction') or '').lower())
    return out


def page(path: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{path}', headers=H, params=q, timeout=90)
        if r.status_code not in (200, 206):
            print(f'  ERROR {r.status_code}: {(r.text or "")[:200]}')
            sys.exit(2)
        b = r.json()
        if not isinstance(b, list):
            print(f'  ERROR: {str(b)[:200]}')
            sys.exit(2)
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def settle(rec: dict, fadeable: set, verdicts: dict) -> tuple:
    """Return (result, actual, reason). result is None when unsettleable.

    Settles the side we actually BACKED, which is pick_side except on a FADE,
    where the receipt recorded the prop's side and we bet the other one.
    """
    pt = (rec.get('prop_type') or '').lower()
    side = (rec.get('pick_side') or '').strip().lower()

    # Resolve which side we really backed before touching any box score.
    if pt in fadeable:
        v = verdicts.get(str(rec.get('source_id')))
        if v is None:
            # Source deleted and this family can be faded — the backed side is
            # genuinely unknowable. Refuse rather than settle a coin flip.
            return None, None, 'fade_ambiguous_source_gone'
        if v[0] == 'FADE':
            side = 'over' if side == 'under' else 'under'
    line = rec.get('pick_line')
    name = rec.get('player_name') or ''
    gd = str(rec.get('game_date') or '')[:10]
    if line is None:
        return None, None, 'no_line'
    try:
        line = float(line)
    except (TypeError, ValueError):
        return None, None, 'bad_line'
    if side not in ('over', 'under'):
        return None, None, f'side_unmapped:{side or "none"}'

    if pt in _BATTER_PROPS:
        stat, group, pitcher = 'hits', 'hitting', False
    else:
        stat = _MLB_STAT_MAP.get(pt)
        group, pitcher = 'pitching', True
        if not stat:
            return None, None, f'stat_unmapped:{pt}'

    pid = _lookup_pid(name, is_pitcher=pitcher)
    if not pid:
        return None, None, 'no_player_id'
    actual = _fetch_stat_for_date(pid, stat, gd, group=group)
    if actual is None:
        # Did not play / scratched / API had nothing for that date.
        return None, None, 'no_boxscore'

    if actual == line:
        return 'Push', actual, 'ok'
    hit = (actual > line) if side == 'over' else (actual < line)
    return ('Win' if hit else 'Loss'), actual, 'ok'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--validate', action='store_true',
                    help='re-settle ALREADY-GRADED receipts and report agreement')
    ap.add_argument('--limit', type=int, default=0, help='cap rows (0 = all)')
    ap.add_argument('--commit', action='store_true')
    args = ap.parse_args()

    hi = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    lo = hi - timedelta(days=args.days)
    # source_id MUST be selected — the FADE guard looks it up, and PostgREST
    # returns no error for a column simply left out of the select, so omitting
    # it made every receipt look like its source was deleted and skipped the
    # whole pitcher set as fade_ambiguous.
    q = {'select': 'id,sport,game_date,player_name,prop_type,pick_side,'
                   'pick_line,result,source_id',
         'surface': 'eq.prop_jerry', 'sport': f'eq.{args.sport}',
         'game_date': f'gte.{lo}', 'order': 'game_date.asc'}
    q['result'] = 'not.is.null' if args.validate else 'is.null'
    recs = [r for r in page('public_receipts', q)
            if str(r.get('game_date') or '')[:10] <= hi.isoformat()]
    if args.limit:
        recs = recs[:args.limit]

    mode = 'VALIDATE' if args.validate else ('APPLY' if args.commit else 'DRY')
    print(f'=== settle_prop_receipts · {args.sport} · {lo}..{hi} · {mode} ===')
    print(f'  {len(recs)} receipt(s)')
    if not recs:
        return

    fadeable = fadeable_families(args.sport, (hi - timedelta(days=120)).isoformat())
    verdicts = source_verdicts(recs)
    print(f'  fadeable families: {sorted(fadeable) or "none"}')
    print(f'  source reads still present: {len(verdicts)}/{len(recs)}')

    agree = Counter()
    reasons = Counter()
    patches = []
    t0 = time.time()
    for i, r in enumerate(recs, 1):
        res, actual, why = settle(r, fadeable, verdicts)
        reasons[why] += 1
        if args.validate:
            if res is None:
                continue
            stored = str(r.get('result') or '')
            # NO_ACTION/Void are bookkeeping outcomes this settler does not
            # produce; compare only where the stored value is a real outcome.
            if stored.lower() in ('win', 'loss', 'push'):
                agree['agree' if stored.lower() == res.lower() else 'DISAGREE'] += 1
                if stored.lower() != res.lower() and agree['DISAGREE'] <= 8:
                    print(f'    DISAGREE id={r["id"]} {r["player_name"]} '
                          f'{r["prop_type"]} {r["pick_side"]} {r["pick_line"]} '
                          f'-> stored={stored} settled={res} (actual={actual})')
            else:
                agree[f'stored_{stored[:12]}'] += 1
        elif res is not None:
            patches.append((r['id'], res))
        if i % 100 == 0:
            print(f'    …{i}/{len(recs)}  ({time.time()-t0:.0f}s)')

    print(f'\n  outcomes: {dict(reasons)}')
    if args.validate:
        a, d = agree.get('agree', 0), agree.get('DISAGREE', 0)
        n = a + d
        print(f'\n  AGREEMENT vs our published record: {a}/{n}'
              + (f'  ({a/n*100:.1f}%)' if n else ''))
        for k, v in agree.items():
            if k.startswith('stored_'):
                print(f'    {k}: {v}')
        if n and d == 0:
            print('\n  ✓ settler reproduces every graded receipt. Safe to apply.')
        elif d:
            print(f'\n  ⚠ {d} disagreement(s). DO NOT APPLY until explained — a '
                  f'settler that contradicts the published record is worse than '
                  f'none.')
        return

    print(f'  settleable: {len(patches)} of {len(recs)}')
    if not args.commit:
        print('\n  DRY RUN — add --commit to write.')
        return
    ok = 0
    for rid, res in patches:
        pr = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                            timeout=30, params={'id': f'eq.{rid}'},
                            json={'result': res})
        ok += pr.status_code in (200, 204)
    print(f'  patched {ok}/{len(patches)}')


if __name__ == '__main__':
    main()
