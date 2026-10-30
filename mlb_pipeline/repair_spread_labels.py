"""Give a frozen spread pick back the number it was published without.

══ 2026-10-30 · "WE HAVE MAGIC ON SWEAT CARD BUT RAPTORS RL IN PRIMARY PLAY" ══
Andy, looking at tonight's NBA board. Two defects in that one sentence; this
script fixes the second.

On 4 of 6 NBA spread picks tonight, `primary_play.line` is None while the
context row HAS a close_spread:

    away @ home                  close_spread   pp.line   pp.label
    Los Angeles Lakers @ GSW          3.5        None     Los Angeles Lakers ATS
    Chicago Bulls @ Boston          -14.5        None     Boston Celtics ATS
    Indiana Pacers @ Miami           -8.5        None     Miami Heat ATS
    Orlando Magic @ Toronto          -5.5        None     Toronto Raptors RL
    Detroit Pistons @ Brooklyn        7.5        -8.5     Detroit Pistons -8.5
    Houston Rockets @ Dallas          6.5        -6.5     Houston Rockets -6.5

The two with a number are the two whose line existed when the label was
built. The other four were composed BEFORE the odds pull landed, fell
through `_label_from_candidate`'s no-line branch, and have been frozen by
the pick lock ever since while ctx.close_spread filled in behind them. Two
different fallbacks are visible in that column, which is how you can tell:
"ATS" comes from `_market_label`, while "RL" is the older hardcoded string
that the 2026-10-22 NHL note already documented ("that is why an NHL card
reads 'Anaheim Ducks RL'").

WHY THIS IS A REPAIR AND NOT A REPICK
A pick with no line is not a bet. A user cannot act on "Toronto Raptors RL",
and the grader cannot settle it -- public_receipt.unactionable refuses a
line-bearing market with no pick_line for exactly that reason, so these
either never captured or captured ungradeable. Filling the number does NOT
change the side, the market or the tier, which are what the lock protects;
it completes a record that was published incomplete. Same reasoning as the
2026-10-30 metadata backfill on locked card picks.

The number comes from the SAME place the label builder would have taken it
(ctx.close_spread, per-sport sign convention), and the side is read off the
stored pick -- never re-derived, because re-deriving a side is how
feedback_grade_the_leg_from_the_row_it_came_from happened.

USAGE
  python repair_spread_labels.py --dry-run
  python repair_spread_labels.py --apply --days 2
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

#: sport -> (ctx table, spread column, HOME-perspective sign flip needed)
#:
#: NFL stores close_spread as a home MARGIN (positive = home favoured) and
#: every other sport stores a HANDICAP (negative home = home favoured), so
#: NFL alone needs negating to reach the handicap the label prints. This is
#: the same table _label_from_candidate uses and the same one proven at
#: 44/0 and 287/10 against results; a sport absent here is skipped rather
#: than guessed (feedback_edge_formula_sign_audit).
CFG = {
    'NBA':   ('nba_game_context', 'close_spread', False),
    'NCAAF': ('ncaaf_game_context', 'close_spread', False),
    'NFL':   ('nfl_game_context', 'close_spread', True),
    'NHL':   ('nhl_game_context', 'close_puckline', False),
}

#: Market code per sport, for a label that still cannot get a number.
MARKET_CODE = {'NBA': 'ATS', 'NCAAF': 'ATS', 'NFL': 'ATS', 'NHL': 'PL'}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H_READ, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:180]}')
            return out
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def run(date_from: str, days: int, apply: bool, sports: list):
    hi = (datetime.fromisoformat(date_from).date()
          + timedelta(days=max(1, days) - 1)).isoformat()
    print(f'=== repair_spread_labels {date_from}..{hi} '
          f'{"APPLY" if apply else "DRY RUN"} ===')
    tot_fix = tot_fail = tot_stuck = 0
    for sport in sports:
        tbl, spcol, flip = CFG[sport]
        rows = page(tbl, {'select': f'game_id,game_date,home_team,away_team,'
                                    f'{spcol},primary_play',
                          'game_date': [f'gte.{date_from}', f'lte.{hi}']})
        fixed = ok = stuck = failed = 0
        print(f'\n  {sport}  {len(rows)} games')
        for row in rows:
            pp = row.get('primary_play')
            if not isinstance(pp, dict) or not pp:
                continue
            if str(pp.get('type') or '').lower() not in ('rl', 'spread'):
                continue
            if pp.get('line') is not None:
                ok += 1
                continue
            sp = _f(row.get(spcol))
            side = str(pp.get('side') or '').upper()
            if sp is None or side not in ('HOME', 'AWAY'):
                stuck += 1
                print(f'    - still no number: {pp.get("label")!r} '
                      f'({spcol}={row.get(spcol)}, side={side or "?"})')
                continue
            home_line = (-sp if flip else sp)
            line = home_line if side == 'HOME' else -home_line
            team = row.get('home_team') if side == 'HOME' else row.get('away_team')
            new_label = f'{team} {line:+g}'
            new_pp = dict(pp)
            new_pp['line'] = line
            new_pp['label'] = new_label
            new_pp['_label_repair'] = {
                'from': pp.get('label'), 'to': new_label, 'line': line,
                'source': f'{tbl}.{spcol}={sp}',
                'at': datetime.now(timezone.utc).isoformat(),
                'note': 'line existed on the context row but the label was '
                        'built before it landed; side/market/tier unchanged',
            }
            print(f'    {str(pp.get("label"))[:26]:26s} -> {new_label:26s} '
                  f'({spcol}={sp:+g}, side={side})')
            if not apply:
                fixed += 1
                continue
            pr = requests.patch(f'{SB}/rest/v1/{tbl}', headers=H_WRITE,
                                timeout=60,
                                params={'game_id': f'eq.{row["game_id"]}',
                                        'game_date': f'eq.{row["game_date"]}'},
                                json={'primary_play': new_pp})
            good = False
            if pr.status_code in (200, 201):
                back = pr.json() or []
                good = bool(back) and (
                    (back[0].get('primary_play') or {}).get('line') == line)
            if good:
                fixed += 1
            else:
                failed += 1
                print(f'      ! NOT CONFIRMED (HTTP {pr.status_code}) — a lock '
                      f'trigger may be reverting it')
        print(f'    {sport}: {fixed} repaired, {ok} already had a number, '
              f'{stuck} still unresolvable, {failed} failed')
        tot_fix += fixed; tot_fail += failed; tot_stuck += stuck
    print(f'\n=== {"applied" if apply else "DRY RUN"}: {tot_fix} repaired, '
          f'{tot_stuck} unresolvable, {tot_fail} failed ===')
    if not apply:
        print('Re-run with --apply to write.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--sport', action='append', choices=sorted(CFG))
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    d = a.date or (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
    run(d, a.days, a.apply and not a.dry_run, a.sport or sorted(CFG))


if __name__ == '__main__':
    main()
