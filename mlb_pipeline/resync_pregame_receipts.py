#!/usr/bin/env python3
"""Keep an UNGRADED game_read receipt pointing at the pick we currently publish.

== 2026-10-29 · WHY THIS EXISTS ==
ANDY, after tonight's CAR @ GB pick was corrected from GB ML to CAR +3.5:
  "Primary play for GB game tonight still says GB ML but CAR +3.5 on pregame
   badges. We need to fix and make sure this is not an issue across other
   games and sports (all games)."

He was right, and it was not one game. Measured the same hour over every
ungraded game_read receipt for today forward:

    sport   receipts  agree  DISAGREE
    NFL           14      4        10
    NCAAF          3       3        0
    NBA            3       2        1
    NHL           11      10       1
    TOTAL         31      19       12   (39%)

e.g. receipt 'LV +1.5' / engine 'NYJ ML'; receipt 'JAX -4.5' / engine
'JAX -9.5'; receipt 'DET -4.5' / engine 'DET ML'.

THE MECHANISM. A receipt is frozen on purpose: pick_side, pick_line and
pick_label are immutable by trigger (20260918b), because the receipt is the
record of what we published. The engine then keeps re-deriving the pick as
lines and signals move, so the longer a game sits before kickoff the further
the record drifts from the board. That is also how
project_receipts_grade_the_opposite_side_1028 happened — 179 receipts
grading a side we had abandoned, 9% of game_read.

WHY NOT reconcile_stale_side_receipts. That tool repairs receipts for games
already PLAYED, and deliberately refuses the ones whose read changed after
kickoff. This is the pre-kickoff half of the same problem: nothing kept the
record current while the pick could still legitimately move.

WHAT IT DOES. VOID the stale receipt and capture a live one for the current
pick. VOID is the honest verb: we did publish that pick, and then withdrew
it. 20261025a's partial unique index is what lets the replacement reuse the
key — without it the insert fails 23505, which is exactly what blocked the
stale-side repair for three days.

THE GUARD THAT MAKES IT HONEST — and it is the whole safety story:
  * the game must be PROVABLY un-started, from the sport's own start column
        NFL / NCAAF  kickoff_utc
        NBA          commence_time_utc
        NHL          commence_time
    MLB has no start column (only rain_prob_at_kickoff), so MLB is REFUSED
    rather than guessed. Same decision reconcile_stale_side_receipts made
    for its 34 MLB rows.
  * the receipt must be UNGRADED. A graded receipt is history.
  * a receipt already VOID is left alone.
Anything this cannot prove is pre-kickoff is skipped and counted, never
defaulted. A receipt rewritten after kickoff would be a fabricated record,
which is worse than a stale one.

USAGE
    python resync_pregame_receipts.py                      # dry run, all sports
    python resync_pregame_receipts.py --sport NFL --apply
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json'}

#: sport -> (context table, the column that proves kickoff)
#  MLB is absent ON PURPOSE: it has no start-time column, so it cannot be
#  proven pre-kickoff and is refused rather than guessed.
SPORTS = {
    'NFL':   ('nfl_game_context',   'kickoff_utc'),
    'NCAAF': ('ncaaf_game_context', 'kickoff_utc'),
    'NBA':   ('nba_game_context',   'commence_time_utc'),
    'NHL':   ('nhl_game_context',   'commence_time'),
}
MARKET_OF_TYPE = {'ml': 'ml', 'rl': 'rl', 'spread': 'rl', 'pl': 'rl',
                  'total': 'total', 'over': 'total', 'under': 'total'}


def _page(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{table} -> {r.status_code}: {r.text[:200]}')
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _norm(s) -> str:
    import re
    return re.sub(r'[^a-z0-9+.\-]', '', str(s or '').lower())


def _pp(row: dict):
    pp = row.get('primary_play')
    if isinstance(pp, str):
        try:
            pp = json.loads(pp)
        except ValueError:
            return None
    return pp if isinstance(pp, dict) else None


def _started(raw, now) -> bool | None:
    """True/False, or None when it cannot be determined — never a guess."""
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t <= now


def run(sport: str, apply: bool) -> dict:
    tbl, startcol = SPORTS[sport]
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    rec = _page('public_receipts', {
        'select': 'id,game_id,game_date,market,pick_label,pick_side,pick_line,'
                  'pick_odds,tier,conviction,matchup,source_table,source_id,result',
        'sport': f'eq.{sport}', 'surface': 'eq.game_read',
        'game_date': f'gte.{today}', 'result': 'is.null'})
    ctx = _page(tbl, {'select': f'game_id,primary_play,{startcol}',
                      'game_date': f'gte.{today}'})
    cmap = {str(c['game_id']): c for c in ctx}

    out = {'checked': 0, 'agree': 0, 'stale': 0, 'fixed': 0,
           'skipped_started': 0, 'skipped_unknown': 0, 'failed': 0}
    acts = []
    for r in rec:
        c = cmap.get(str(r['game_id']))
        if not c:
            out['skipped_unknown'] += 1
            continue
        pp = _pp(c)
        if not pp or not pp.get('label'):
            out['skipped_unknown'] += 1
            continue
        out['checked'] += 1
        if _norm(pp['label']) == _norm(r['pick_label']):
            out['agree'] += 1
            continue
        started = _started(c.get(startcol), now)
        if started is None:
            out['skipped_unknown'] += 1
            print(f"  ? {sport} {r['game_date']} {str(r['pick_label'])[:20]:22} "
                  f"-> {str(pp['label'])[:20]:22} NO START TIME — skipped")
            continue
        if started:
            out['skipped_started'] += 1
            print(f"  · {sport} {r['game_date']} {str(r['pick_label'])[:20]:22} "
                  f"-> {str(pp['label'])[:20]:22} ALREADY STARTED — record stands")
            continue
        out['stale'] += 1
        acts.append((r, pp))
        print(f"  ! {sport} {r['game_date']} {str(r['pick_label'])[:20]:22} "
              f"-> {str(pp['label'])[:20]:22} pre-kickoff, replaceable")

    if not apply:
        return out

    from public_receipt import capture
    for r, pp in acts:
        v = requests.patch(f"{SB}/rest/v1/public_receipts?id=eq.{r['id']}",
                           headers=H_W, json={'result': 'VOID'}, timeout=60)
        if v.status_code not in (200, 204):
            print(f"  x void {r['id']} -> {v.status_code} {v.text[:120]}")
            out['failed'] += 1
            continue
        row = {
            'sport': sport, 'game_id': r['game_id'], 'game_date': r['game_date'],
            'surface': 'game_read',
            'market': MARKET_OF_TYPE.get(str(pp.get('type') or '').lower(),
                                         str(pp.get('type') or '').lower()),
            'matchup': r.get('matchup'),
            'pick_label': pp.get('label'), 'pick_side': pp.get('side'),
            'pick_line': pp.get('line'),
            # The price belongs to the OLD market; price_picks re-prices the
            # new row. Carrying it over is how an ML price ends up on a
            # spread (the 10-28 'SAS -13.5 at -650' defect).
            'pick_odds': None,
            'tier': pp.get('tier'), 'conviction': pp.get('conviction'),
            'source_table': r.get('source_table') or 'jerry_reads',
            'source_id': str(r.get('source_id')),
            'audit': {'replaces_receipt': r['id'],
                      'reason': 'pre-kickoff resync: engine pick moved',
                      'was': r.get('pick_label')},
        }
        res = capture([row], surface='game_read', quiet=True)
        if res.get('written'):
            out['fixed'] += 1
        else:
            out['failed'] += 1
            print(f"  x replacement NOT written for {r['id']}: {res}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(SPORTS))
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    sports = [a.sport] if a.sport else sorted(SPORTS)
    print(f"=== resync_pregame_receipts · {datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z"
          f" · {'APPLY' if a.apply else 'DRY'} ===")
    print('    MLB is not listed: no start-time column, so it cannot be '
          'proven pre-kickoff.\n')
    tot = {}
    for sp in sports:
        try:
            res = run(sp, a.apply)
        except RuntimeError as e:
            print(f'  ! {sp}: {e}')
            continue
        for k, v in res.items():
            tot[k] = tot.get(k, 0) + v
        print(f"  {sp}: checked={res['checked']} agree={res['agree']} "
              f"stale={res['stale']} fixed={res['fixed']} "
              f"skipped(started)={res['skipped_started']} "
              f"skipped(unknown)={res['skipped_unknown']} failed={res['failed']}\n")
    print(f'  TOTAL {tot}')
    if not a.apply and tot.get('stale'):
        print('\n  DRY RUN — re-run with --apply to void and replace these.')
    return 1 if tot.get('failed') else 0


if __name__ == '__main__':
    raise SystemExit(main())
