#!/usr/bin/env python3
"""Fill nhl_game_results.close_puckline from the twin column that has it.

WHY THIS EXISTS
---------------
2026-11-19. Andy asked me to verify the ATS and moneyline records on the
Canucks @ Kraken card. The moneyline records were exact. Both ATS records
were one game short, and so was every other team that played on 2026-11-07:

    nhl_game_results.close_puckline   NULL on all 12 games of that slate
    nhl_game_context.close_spread     -1.5 / +1.5 on all 12

The line was never missing. It was in the other table, under a different
name. With close_puckline NULL the grader could not produce a
spread_result, so all 12 games dropped out of team_situational_records --
24 teams, one game each, and the gap cut OPPOSITE WAYS: Vancouver read
10-12 when it is 10-13, Seattle 9-8 when it is 10-8. A short denominator
renders as a perfectly plausible record, which is why it survived 12 days.

nhl_game_context carries THREE names for this one line -- close_spread,
close_puckline and close_puckline_home -- and on 2026-11-07 the close_*
family went NULL while close_spread stayed populated. So this reads
whichever of them is present rather than trusting one name.

WHY NOT JUST GRADE IT HERE: because backfill_game_grades.py already owns
that rule, with sanity bands, for three sports. Two implementations of
"who covered" is how NHL and NCAAB came to disagree in the first place
(see that script's own header). This fills the LINE; run it, then
`backfill_game_grades.py --sport NHL` turns the line into the grade.

SAFETY
  * Writes only where close_puckline IS NULL. Never overwrites a stored
    line -- a line that moved is a different problem and not this one's.
  * Refuses any value that is not exactly +/-1.5. An NHL puck line is
    fixed at 1.5; anything else means the source column holds something
    else. close_puckline on this table historically held MONEYLINE PRICES
    (-290..+240) and grading off those fabricated 1,120 rows at a
    believable 52.5/47.5 home/away split. A wrong line is worse than a
    missing one because it looks like evidence.
  * Refuses a row whose teams disagree between the two tables, so a
    game_id collision cannot move a line onto the wrong game.
  * --dry-run prints every write and touches nothing.

CLI
  python backfill_nhl_puckline_from_ctx.py --dry-run
  python backfill_nhl_puckline_from_ctx.py
  python backfill_nhl_puckline_from_ctx.py --since 2026-11-01
  python backfill_nhl_puckline_from_ctx.py --self-test
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        _ln = _ln.strip()
        if _ln and not _ln.startswith('#') and '=' in _ln:
            _k, _v = _ln.split('=', 1)
            os.environ.setdefault(_k, _v.strip().strip('"'))

SB = os.environ.get('SUPABASE_URL')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
HDRS = {'apikey': KEY or '', 'Authorization': 'Bearer ' + (KEY or '')}
WHDRS = {**HDRS, 'Content-Type': 'application/json',
         'Prefer': 'return=representation'}

#: An NHL puck line is 1.5. Not "about 1.5" -- exactly.
PUCKLINE = 1.5
#: The context columns that may hold it, in the order to trust them. All
#: three are the same home-perspective handicap; which one is populated
#: depends on which writer last ran.
CTX_COLS = ('close_puckline', 'close_spread', 'close_puckline_home')


def _page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = '1000'
        p['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=HDRS, params=p,
                         timeout=180)
        if r.status_code not in (200, 206):
            raise SystemExit(f'! {table} {r.status_code} {r.text[:200]}')
        chunk = r.json()
        if not isinstance(chunk, list):
            raise SystemExit(f'! {table} returned {chunk}')
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


def pick_line(ctx_row):
    """The first plausible puck line among the context twins.

    Returns (value, column) or (None, reason). A value that is not exactly
    +/-1.5 is REFUSED rather than rounded: the same column on the results
    table used to hold moneyline prices, and a -290 silently coerced to a
    line is the failure this guard exists for.
    """
    seen = []
    for col in CTX_COLS:
        v = ctx_row.get(col)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            seen.append(f'{col}={v!r} not numeric')
            continue
        if abs(abs(f) - PUCKLINE) < 1e-9:
            return f, col
        seen.append(f'{col}={f} is not +/-1.5')
    return None, ('; '.join(seen) if seen
                  else 'no context column carries a line')


def main(dry, since, limit_date=None):
    if not SB or not KEY:
        print('backfill_nhl_puckline_from_ctx: no Supabase credentials')
        return 2
    res = _page('nhl_game_results',
                {'select': 'game_id,game_date,home_team,away_team,'
                           'home_score,away_score,close_puckline,'
                           'spread_result',
                 'game_date': f'gte.{since}'})
    need = [r for r in res if r.get('close_puckline') is None
            and r.get('home_score') is not None]
    print(f'== backfill_nhl_puckline_from_ctx · {len(res)} result rows since '
          f'{since} · {len(need)} scored with NO close_puckline ==')
    if not need:
        print('  nothing to fill.')
        return 0
    ctx = {str(c['game_id']): c for c in _page(
        'nhl_game_context',
        {'select': 'game_id,game_date,home_team,away_team,'
                   + ','.join(CTX_COLS),
         'game_date': f'gte.{since}'})}

    writes, refused = [], []
    for r in need:
        c = ctx.get(str(r['game_id']))
        if not c:
            refused.append((r['game_date'], r['game_id'],
                            'no nhl_game_context row'))
            continue
        # A game_id is the join key; if the teams disagree the id is not
        # describing the same game and the line must not be moved onto it.
        if (str(c.get('home_team')) != str(r.get('home_team'))
                or str(c.get('away_team')) != str(r.get('away_team'))):
            refused.append((r['game_date'], r['game_id'],
                            f"team mismatch: ctx {c.get('away_team')} @ "
                            f"{c.get('home_team')} vs results "
                            f"{r.get('away_team')} @ {r.get('home_team')}"))
            continue
        val, src = pick_line(c)
        if val is None:
            refused.append((r['game_date'], r['game_id'], src))
            continue
        writes.append((r, val, src))

    print(f'  to fill {len(writes)} · refused {len(refused)}')
    if writes:
        print(f"  source column mix: "
              f"{dict(collections.Counter(s for _, _, s in writes))}")
    for r, val, src in writes:
        print(f"    {str(r['game_date'])[:10]}  {r['away_team'][:20]:20s} @ "
              f"{r['home_team'][:20]:20s} {r['away_score']}-{r['home_score']}"
              f"   close_puckline <- {val:+.1f}  (from ctx.{src})")
    for d, gid, why in refused:
        print(f'    REFUSED {str(d)[:10]} {gid}: {why}')

    if dry:
        print('\n  --dry-run: nothing written. Next step after a real run:')
        print('    python backfill_game_grades.py --sport NHL')
        return 0

    ok = bad = 0
    for r, val, _src in writes:
        resp = requests.patch(
            f'{SB}/rest/v1/nhl_game_results',
            headers=WHDRS,
            params={'game_id': f"eq.{r['game_id']}",
                    # Belt and braces: only write where it is STILL null, so
                    # a concurrent writer cannot be clobbered. A PATCH that
                    # matches no row returns [] -- which is not a write, and
                    # is counted as such.
                    'close_puckline': 'is.null'},
            data=json.dumps({'close_puckline': val}), timeout=60)
        if resp.status_code in (200, 201, 204) and resp.json():
            ok += 1
        else:
            bad += 1
            print(f"    ! {r['game_id']} {resp.status_code} "
                  f"{resp.text[:120]}")
    print(f'\n  filled {ok} · failed/no-op {bad}')
    print('  NEXT: python backfill_game_grades.py --sport NHL')
    return 0 if bad == 0 else 1


def _self_test():
    cases = []
    # The real 2026-11-07 shape: close_puckline absent, close_spread holds it.
    cases.append(('reads close_spread when close_puckline is NULL',
                  pick_line({'close_puckline': None, 'close_spread': -1.5,
                             'close_puckline_home': None}) == (-1.5,
                                                               'close_spread')))
    cases.append(('prefers close_puckline when both are present',
                  pick_line({'close_puckline': 1.5, 'close_spread': -1.5})
                  == (1.5, 'close_puckline')))
    # The guard that matters: moneyline prices in the line column.
    v, why = pick_line({'close_puckline': -290, 'close_spread': None})
    cases.append(('REFUSES a moneyline price (-290) as a line', v is None))
    v, why = pick_line({'close_puckline': None, 'close_spread': 0.0})
    cases.append(('REFUSES 0.0, which is not a puck line', v is None))
    v, why = pick_line({'close_puckline': None, 'close_spread': 2.5})
    cases.append(('REFUSES 2.5', v is None))
    v, why = pick_line({})
    cases.append(('no column present -> None with a reason',
                  v is None and 'no context column' in why))
    cases.append(('+1.5 is accepted',
                  pick_line({'close_spread': 1.5})[0] == 1.5))
    # A refusal must carry WHY, or the next person re-derives it.
    _, why = pick_line({'close_spread': 7.0})
    cases.append(('a refusal names the offending value',
                  '7.0' in why and 'not +/-1.5' in why))
    fails = 0
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            fails += 1
    print(f'\n  {len(cases) - fails}/{len(cases)} self-test cases passed')
    return 1 if fails else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--since', default='2026-09-01')
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args()
    if a.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(main(a.dry_run, a.since))
