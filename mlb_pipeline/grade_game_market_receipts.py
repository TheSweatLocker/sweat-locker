#!/usr/bin/env python3
"""grade_game_market_receipts.py - grade a receipt from the GAME's own score.

== WHY ==
2026-10-22. Andy's morning audit found `fade` receipt 'Under 229.5' on the
10-21 Dallas/Houston game sitting ungraded after the game finished 128-110.

Chased where fade receipts get graded and the answer is: nowhere, by design
that nobody finished.

  * generate_the_fade.py writes them with a comment saying "receipts, so the
    record grades itself the way every other surface does" - it captures
    market / pick_side / pick_line / pick_label and grades nothing.
  * sync_receipt_grades.py follows source_id into a source TABLE. The fade's
    source_table is 'line_snapshot' and its source_id is a composite string
    ('fade:NBA:401909840:total'), so there is no row to follow.
  * reconcile_receipt_results.py copies a grade between surfaces that
    published the SAME bet, and skips any group with `len(rows) < 2`. The
    Fade's whole point is publishing a side nobody else published, so it is
    always a group of one.
  * grade_orphaned_receipts.py resolves prop receipts from the graded prop
    row. A game total is not a prop.

7 of 10 fade receipts are graded only because another surface happened to
publish the same bet and reconcile copied it across. That is luck, not a
mechanism.

== WHAT THIS DOES ==
Grades a receipt whose market is a GAME market, straight from the final
score in that sport's results table. No source row, no sibling surface
needed - the score is the answer.

Covers `total` and `ml`. Both are unambiguous from two numbers:

    total   home + away vs the receipt's own pick_line, by pick_side
    ml      the winner, by pick_side

== WHY IT REFUSES SPREADS ==
A spread needs to know whether pick_line is stored signed FOR the picked
side or as the raw market number, and this project has the scar to prove
that matters: project_close_spread_sign_bug_914 found the convention
INVERTED between NFL and NCAAF, so one reading is right in one sport and
backwards in the other. Getting it wrong flips a win into a loss, which is
worse than leaving it ungraded (feedback_grade_the_side_we_published).

Spreads are REPORTED with their numbers so the convention can be settled
from data, and then this gains a branch. Of 10 fade receipts, 9 are totals.

Usage:
    python grade_game_market_receipts.py                  # dry run, 30d
    python grade_game_market_receipts.py --days 90
    python grade_game_market_receipts.py --surface fade
    python grade_game_market_receipts.py --apply
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:                                          # noqa: BLE001
        pass

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

RESULTS_TABLE = {
    'MLB': 'mlb_game_results',
    'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NHL': 'nhl_game_results',
    'NBA': 'nba_game_results',
    'NCAAB': 'ncaab_game_results',
}

#: Markets this grades.
GRADEABLE = ('total', 'ml', 'rl', 'spread', 'puckline', 'ats')

# ══ 2026-10-22 · THE SPREAD SIGN CONVENTION, MEASURED ══
# The first cut of this file refused spreads outright, because
# project_close_spread_sign_bug_914 found the convention INVERTED between NFL
# and NCAAF and a wrong reading flips a win into a loss. Refusing was the
# right default and the wrong resting place - it left the Lakers -5.5 Sharp
# Card receipt ungraded with no path to ever grade it.
#
# So it was settled from data instead of guessed. Took all 344 graded spread
# receipts that carry pick_side + pick_line + game_id, recomputed each one
# from the final score under BOTH readings, and compared to the stored grade:
#
#   sport    line signed FOR the side      line is the market number
#   NCAAF        97.2%  (239/246)                85.0%
#   NHL         100.0%  (5/5)                    80.0%
#   NBA         100.0%  (8/8)                   100.0%   <- all 8 are HOME
#                                                           picks, where the
#                                                           two readings
#                                                           COINCIDE, so NBA
#                                                           does not
#                                                           discriminate
#   MLB          66.7%  (14/21)                 57.1%
#
# `pick_line` is already signed from the picked side's point of view, and it
# is the better fit in every sport. NCAAF decides it: 97.2% against 85.0% on
# n=246.
#
# MLB IS EXCLUDED. 66.7% on n=21 is not a convention, it is noise with a
# lean, and its spread market is the run line, which has its own quirks
# (feedback_ml_vs_rl_conflation). MLB spread receipts stay ungraded here
# rather than being graded two times in three.
SPREAD_SPORTS = ('NCAAF', 'NHL', 'NBA', 'NFL', 'NCAAB')


def _et_today() -> dt.date:
    now_utc = dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo('America/New_York')).date()
    except Exception:                                          # noqa: BLE001
        return (now_utc - dt.timedelta(hours=5)).date()


def page(tbl, params):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H,
                         params={**params, 'limit': '1000',
                                 'offset': str(off)}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f'{tbl} HTTP {r.status_code}: {r.text[:200]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def grade_total(side: str, line, home, away):
    """WIN/LOSS/PUSH for an over/under, or None if it cannot be decided."""
    ln = _f(line)
    if ln is None:
        return None, 'no pick_line'
    tot = home + away
    if abs(tot - ln) < 1e-9:
        return 'PUSH', f'{tot} = {ln}'
    s = str(side or '').strip().lower()
    if s not in ('over', 'under'):
        return None, f'pick_side {side!r} is not over/under'
    hit = (tot > ln) if s == 'over' else (tot < ln)
    return ('WIN' if hit else 'LOSS'), f'{home}+{away}={tot} vs {s} {ln}'


def grade_spread(side: str, line, home, away):
    """WIN/LOSS/PUSH for a side with a line, or None if undecidable.

    `line` is read as ALREADY SIGNED from the picked side's point of view -
    measured at 97.2% on NCAAF's 246 graded receipts against 85.0% for the
    alternative reading. See SPREAD_SPORTS above.
    """
    ln = _f(line)
    if ln is None:
        return None, 'no pick_line'
    s = str(side or '').strip().upper()
    if s not in ('HOME', 'AWAY'):
        return None, f'pick_side {side!r} is not HOME/AWAY'
    margin = (home - away) if s == 'HOME' else (away - home)
    v = margin + ln
    if abs(v) < 1e-9:
        return 'PUSH', f'margin {margin:+g} with {ln:+g} lands on zero'
    return ('WIN' if v > 0 else 'LOSS'), (
        f'{home:g}-{away:g}, {s} margin {margin:+g} with {ln:+g} = {v:+g}')


def grade_ml(side: str, home, away):
    if home == away:
        return 'PUSH', f'{home}-{away} tie'
    s = str(side or '').strip().upper()
    if s not in ('HOME', 'AWAY'):
        return None, f'pick_side {side!r} is not HOME/AWAY'
    winner = 'HOME' if home > away else 'AWAY'
    return ('WIN' if s == winner else 'LOSS'), f'{home}-{away}, {winner} won'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--surface', action='append',
                    help='limit to these surfaces (default: all)')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    since = (_et_today() - dt.timedelta(days=a.days)).isoformat()
    today = _et_today().isoformat()
    params = {'select': 'id,sport,surface,game_id,game_date,market,pick_side,'
                        'pick_line,pick_label,result',
              'result': 'is.null',
              'and': f'(game_date.gte.{since},game_date.lt.{today})'}
    recs = page('public_receipts', params)
    if a.surface:
        want = {s.lower() for s in a.surface}
        recs = [r for r in recs if str(r.get('surface') or '').lower() in want]
    print(f'=== grade_game_market_receipts · {since}..{today} '
          f'{"APPLY" if a.apply else "[DRY]"} ===')
    print(f'  {len(recs)} ungraded receipt(s) on finished dates')
    if not recs:
        print('\nnothing ungraded in the window.')
        return 0

    by_market = Counter(str(r.get('market') or '-') for r in recs)
    print(f'  by market: {dict(by_market)}')

    # one results fetch per (sport, date)
    cache: dict[tuple, dict] = {}

    graded, skipped, spread_held, patched = [], [], [], 0
    for r in recs:
        mkt = str(r.get('market') or '').lower()
        sport = str(r.get('sport') or '').upper()
        gid = r.get('game_id')
        is_spread = mkt in ('rl', 'spread', 'puckline', 'ats')
        if is_spread and sport not in SPREAD_SPORTS:
            spread_held.append(r)
            continue
        if mkt not in GRADEABLE or not gid or sport not in RESULTS_TABLE:
            skipped.append((r, f'market={mkt!r} sport={sport!r} gid={gid!r}'))
            continue
        ck = (sport, str(r.get('game_date'))[:10])
        if ck not in cache:
            rows = page(RESULTS_TABLE[sport],
                        {'select': 'game_id,home_score,away_score',
                         'game_date': f'eq.{ck[1]}'})
            cache[ck] = {str(x.get('game_id')): x for x in rows}
        g = cache[ck].get(str(gid))
        if not g:
            skipped.append((r, 'no results row for that game_id'))
            continue
        hs, as_ = _f(g.get('home_score')), _f(g.get('away_score'))
        if hs is None or as_ is None:
            skipped.append((r, 'game not resolved yet'))
            continue
        if mkt == 'total':
            res, why = grade_total(r.get('pick_side'), r.get('pick_line'),
                                   hs, as_)
        elif is_spread:
            res, why = grade_spread(r.get('pick_side'), r.get('pick_line'),
                                    hs, as_)
        else:
            res, why = grade_ml(r.get('pick_side'), hs, as_)
        if res is None:
            skipped.append((r, why))
            continue
        graded.append((r, res, why))

    for r, res, why in graded:
        print(f'  {r["game_date"]} {str(r.get("sport")):6s} '
              f'{str(r.get("surface")):11s} {str(r.get("pick_label"))[:26]:26s} '
              f'NULL -> {res:5s} ({why})')
    if spread_held:
        print(f'\n  {len(spread_held)} spread receipt(s) HELD - their sport is '
              f'not in SPREAD_SPORTS, because the pick_line convention is '
              f'unconfirmed there. MLB measured 66.7% on n=21, which is noise '
              f'with a lean rather than a convention:')
        for r in spread_held[:8]:
            print(f'     {r["game_date"]} {str(r.get("sport")):6s} '
                  f'{str(r.get("pick_label"))[:30]:30s} '
                  f'side={r.get("pick_side")} line={r.get("pick_line")}')
    if skipped:
        print(f'\n  {len(skipped)} not gradeable here:')
        for r, why in skipped[:8]:
            print(f'     {r["game_date"]} {str(r.get("surface")):11s} '
                  f'{str(r.get("pick_label"))[:26]:26s} {why}')
        if len(skipped) > 8:
            print(f'     ... and {len(skipped) - 8} more')

    if a.apply:
        for r, res, _why in graded:
            pr = requests.patch(f'{SB}/rest/v1/public_receipts',
                                headers=H_W, params={'id': f'eq.{r["id"]}'},
                                json={'result': res}, timeout=30)
            if pr.status_code in (200, 204):
                patched += 1
            else:
                print(f'    ⚠ patch {r["id"]} {pr.status_code}: '
                      f'{pr.text[:160]}')
        print(f'\n  patched {patched}/{len(graded)}')
    elif graded:
        print(f'\n  would patch {len(graded)}. Re-run with --apply.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
