"""One bet, one result — across every surface that published it.

WHY THIS EXISTS (2026-10-08)
----------------------------
Andy: "things are not being enginnerried corretcly and not afterthought is
given... no problem solving is actulaly happening jys band aid after band
aid".

He is right, and this is the thing underneath every symptom we chased today.
Tyler Mahle threw 19 outs against a 13.5 line. One bet. Four receipts:

    sharp_card   LOSS     'Tyler Mahle Over 13.5 OUTS'
    potd         WIN      'Tyler Mahle Over 13.5 Outs (Jerry 85/100)'
    sweat_card   None     'Tyler Mahle Over 13.5 Outs (Jerry 85/100)'
    prop_jerry   Win      'Tyler Mahle OVER 13.5 outs_over @ -115'

Three different answers to the same question. Every surface grades itself,
each by a different route, and nothing has ever asserted they agree. So:
  * the POTD recap showed Loss while Receipts showed Win
  * the Sharp record counted a win as a loss
  * patching one surface (which is what I did yesterday, and again this
    morning) fixes one number and leaves the others lying

The existing consistency watchdog could not catch it. It compares
jerry_cache against daily_best_bet_history — but grade_potd WRITES one and
COPIES it to the other, so they cannot disagree about a wrong grade. It was
checking a copy against its own source.

WHAT THIS DOES
Groups every public_receipts row by the BET, not by the surface, and
requires one result. The grouping key is deliberately identity-based rather
than label-based, because the four rows above prove labels are not stable
across surfaces ('13.5 OUTS' vs '13.5 Outs (Jerry 85/100)' vs
'13.5 outs_over @ -115').

AUTHORITY ORDER, and it is not a vote:
  1. a GRADED row in <sport>_pipeline_props — the only place an actual
     measured stat lives (final_value 19 vs line 13.5)
  2. failing that, unanimous agreement among the surfaces
Anything else is reported and left alone. We never resolve a disagreement by
majority, because three surfaces copying one bad grade is not evidence.

`public_receipts.result` is mutable by design — the freeze trigger protects
identity and pick fields, not the outcome — so correcting it is permitted.
Identity, line and odds are never touched.

CLI
    python reconcile_receipt_results.py --days 14
    python reconcile_receipt_results.py --days 14 --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

PROPS_TABLE = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props'}
_NORM = {'win': 'WIN', 'w': 'WIN', 'loss': 'LOSS', 'l': 'LOSS',
         'push': 'PUSH', 'p': 'PUSH', 'void': 'VOID', 'no action': 'VOID'}

#: "Tyler Mahle Over 13.5 Outs (Jerry 85/100)" / "... Over 13.5 OUTS"
#: / "... OVER 13.5 outs_over @ -115"  -> (player, dir, line, stat)
_LBL = re.compile(
    r'^(?P<player>.+?)\s+(?P<dir>Over|Under)\s+(?P<line>\d+(?:\.\d+)?)\s+'
    r'(?P<stat>[A-Za-z_ ]+)', re.I)


def _norm_result(v):
    s = str(v or '').strip().lower()
    return _NORM.get(s, s.upper() or None)


def _key(rec):
    """Identity of the BET, stable across how each surface spells it."""
    m = _LBL.match(str(rec.get('pick_label') or '').strip())
    if not m:
        return None
    stat = m.group('stat').strip().lower()
    stat = re.sub(r'_(over|under)$', '', stat)        # outs_over -> outs
    stat = stat.split(' @ ')[0].strip().replace(' ', '_')
    return (str(rec.get('game_date'))[:10],
            str(rec.get('sport') or 'MLB').upper(),
            m.group('player').strip().lower(),
            m.group('dir').lower(),
            float(m.group('line')),
            stat)


def _page(t, p, cap=60000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:130]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


#: Per-run cache of prop rows by (table, game_date). truth_from_props is
#: called once per distinct BET, and each call used to page the whole props
#: table for that date — fine for one day's reconcile, ruinous for a
#: many-date backfill (grade_orphaned_receipts walks 180 days). Caching here
#: rather than in the caller keeps ONE definition of the matching rules.
_PROPS_CACHE: dict = {}


def truth_from_props(key, rows=None):
    """The measured answer, or None. Never guesses.

    `rows` lets a caller supply prefetched prop rows for this date; otherwise
    they are fetched once per (table, date) and cached for the run.
    """
    date_s, sport, player, direction, line, stat = key
    tbl = PROPS_TABLE.get(sport)
    if not tbl:
        return None, None
    if rows is None:
        ck = (tbl, date_s)
        if ck not in _PROPS_CACHE:
            _PROPS_CACHE[ck] = _page(
                tbl, {'select': 'player_name,prop_type,prop_line,direction,'
                                'result,final_value',
                      'game_date': f'eq.{date_s}'})
        rows = _PROPS_CACHE[ck]
    for r in rows:
        if str(r.get('player_name') or '').strip().lower() != player:
            continue
        if str(r.get('direction') or '').lower() != direction:
            continue
        try:
            if abs(float(r.get('prop_line')) - line) > 1e-6:
                continue
        except (TypeError, ValueError):
            continue
        pt = re.sub(r'_(over|under)$', '', str(r.get('prop_type') or '').lower())
        if pt != stat:
            continue
        res = _norm_result(r.get('result'))
        if res in ('WIN', 'LOSS', 'PUSH'):
            return res, f'{tbl} final_value={r.get("final_value")} vs {line}'
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    since = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()

    recs = _page('public_receipts',
                 {'select': 'id,game_date,sport,surface,pick_label,result',
                  'game_date': f'gte.{since}'})
    groups = collections.defaultdict(list)
    for r in recs:
        k = _key(r)
        if k:
            groups[k].append(r)

    print(f'=== reconcile_receipt_results · {len(recs)} receipts since {since} '
          f'· {len(groups)} distinct prop bets · '
          f'{"APPLY" if args.apply else "DRY"} ===')

    conflicts = fixed = unresolved = 0
    for k, rows in sorted(groups.items()):
        seen = {_norm_result(r.get('result')) for r in rows}
        graded = {s for s in seen if s in ('WIN', 'LOSS', 'PUSH')}
        if len(rows) < 2 or len(graded | ({None} if None in seen else set())) < 2:
            continue
        if len(graded) <= 1 and None not in seen:
            continue
        conflicts += 1
        date_s, sport, player, direction, line, stat = k
        print(f'\n  {date_s} {sport} {player} {direction} {line} {stat}')
        for r in rows:
            print(f"      {r['surface']:<12}{str(_norm_result(r.get('result'))):<6}"
                  f"  {str(r.get('pick_label'))[:52]}")
        truth, why = truth_from_props(k)
        if not truth:
            unresolved += 1
            print('      -> NO GRADED PROP ROW — left alone, not guessed')
            continue
        print(f'      -> TRUTH {truth}  ({why})')
        for r in rows:
            if _norm_result(r.get('result')) == truth:
                continue
            if not args.apply:
                continue
            pr = requests.patch(f'{SB}/rest/v1/public_receipts?id=eq.{r["id"]}',
                                headers=H_W,
                                json={'result': truth,
                                      'graded_at': dt.datetime.now(
                                          dt.timezone.utc).isoformat()},
                                timeout=60)
            if pr.status_code not in (200, 204):
                print(f'         ! {r["surface"]} patch {pr.status_code}')
        fixed += 1

    print(f'\n  conflicting bets: {conflicts} · '
          f'{"corrected" if args.apply else "correctable"} {fixed} · '
          f'unresolved (no graded prop row) {unresolved}')
    return 1 if conflicts else 0


if __name__ == '__main__':
    sys.exit(main())
