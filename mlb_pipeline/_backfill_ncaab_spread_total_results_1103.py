"""Fill spread_result / total_result on NCAAB games that already have both inputs.

== WHY ====================================================================
Andy wants NCAAB on real measured stats. The two markets worth looking at
are spreads and totals -- favourite ML is already measured dead (negative in
every price band, n=4,981; and tonight every NCAAB ML signal with n>=30 came
out between -0.70% and -8.47% ROI at the real closing price).

Neither can be measured, because the OUTCOME column is empty:

    this season: 210 rows, 167 scored
      scored AND close_spread AND close_total     73
      of those, spread_result / total_result NULL 72

All 72 are computable from columns already in the row, and all 72 share
`result_logged_at = 2026-11-03T18:17:06` -- one scoring pass that ran before
the closing lines arrived.

TWO SEPARATE BUGS PRODUCED THAT, and this script is only the cleanup:

 1. `resolve_ncaab_results.compute_outcome_patch` returned None the moment
    the score matched, so a game scored BEFORE its line landed never got its
    derived outcomes. Fixed in that file (unit-tested, 7 cases).

 2. That resolver cannot currently reach these rows anyway: run over the
    last 5 days it reports **"existing rows matched: 0/187"** and would
    INSERT 187 "unlined" duplicates. Its computed
    `ncaab_YYYYMMDD_AWAY_HOME` id is built from ESPN-resolved names while
    the lined rows were created from the odds feed's names, so the ids do
    not meet. NOT fixed here -- it needs the (date, resolved pair) bridge the
    rest of the NCAAB code now uses, and it is a bigger change than a
    backfill. Flagged.

== WHAT THIS WRITES ======================================================
`spread_result` and `total_result` only, and only where the row already has
a final score and the corresponding closing line, and the outcome column is
NULL. It re-derives nothing else and overwrites nothing.

The convention is taken from the resolver itself, not re-invented:

    NCAAB close_spread negative = HOME favoured
    margin = home_score - away_score
    home_covered  if margin > -close_spread
    away_covered  if margin < -close_spread
    push          if equal

Sanity-checked against the 1 row that already has spread_result before
writing anything: if our formula disagrees with the stored value on that
row, the script refuses outright rather than backfill 72 rows off a
misremembered sign convention (feedback_edge_formula_sign_audit).

    python _backfill_ncaab_spread_total_results_1103.py
    python _backfill_ncaab_spread_total_results_1103.py --apply
"""
import collections
import json
import os
import sys
from pathlib import Path

import requests

for _ln in (Path(__file__).parent / '.env').read_text(
        encoding='utf-8', errors='replace').split('\n'):
    if '=' in _ln and not _ln.lstrip().startswith('#'):
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

APPLY = '--apply' in sys.argv


def page(select: str) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/ncaab_game_results', headers=H,
                         timeout=120,
                         params={'select': select, 'limit': 1000,
                                 'offset': off})
        r.raise_for_status()
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def spread_outcome(hs, as_, cs):
    margin = hs - as_
    if margin > -cs:
        return 'home_covered'
    if margin < -cs:
        return 'away_covered'
    return 'push'


def total_outcome(hs, as_, ct):
    tot = hs + as_
    if tot > ct:
        return 'over'
    if tot < ct:
        return 'under'
    return 'push'


def main() -> int:
    rows = page('game_id,game_date,home_team,away_team,home_score,away_score,'
                'close_spread,close_total,spread_result,total_result')
    scored = [r for r in rows
              if r.get('home_score') is not None
              and r.get('away_score') is not None]
    print(f'ncaab_game_results: {len(rows)} rows, {len(scored)} scored')

    # ── sign-convention control: agree with every row that ALREADY has an
    #    outcome, or refuse. 5,028 rows carry one historically.
    checked = agree = 0
    bad = []
    for r in scored:
        cs = _f(r.get('close_spread'))
        if cs is None or not r.get('spread_result'):
            continue
        checked += 1
        want = spread_outcome(r['home_score'], r['away_score'], cs)
        if want == str(r['spread_result']).lower():
            agree += 1
        elif len(bad) < 5:
            bad.append((r['game_id'], r['home_score'], r['away_score'], cs,
                        r['spread_result'], want))
    print(f'  sign-convention control: formula agrees with {agree}/{checked} '
          f'rows that already carry spread_result')
    for b in bad:
        print(f'     DISAGREE {b[0]}: {b[2]}-{b[1]} cs={b[3]} stored={b[4]} '
              f'computed={b[5]}')
    if checked == 0:
        print('  !! nothing to check the convention against — refusing')
        return 1
    if agree / checked < 0.99:
        print(f'  !! only {100.0*agree/checked:.1f}% agreement — the sign '
              f'convention is not what this script assumes. REFUSING.')
        return 1

    plan = []
    for r in scored:
        patch = {}
        cs = _f(r.get('close_spread'))
        if cs is not None and not r.get('spread_result'):
            patch['spread_result'] = spread_outcome(r['home_score'],
                                                    r['away_score'], cs)
        ct = _f(r.get('close_total'))
        if ct is not None and not r.get('total_result'):
            patch['total_result'] = total_outcome(r['home_score'],
                                                  r['away_score'], ct)
        if patch:
            plan.append((r, patch))
    print(f'\n  rows to fill: {len(plan)}')
    bydate = collections.Counter(str(r['game_date']) for r, _p in plan)
    print(f'  by date: {dict(sorted(bydate.items())[-8:])}')
    sc = collections.Counter(p.get('spread_result') for _r, p in plan
                             if 'spread_result' in p)
    tc = collections.Counter(p.get('total_result') for _r, p in plan
                             if 'total_result' in p)
    print(f'  spread outcomes: {dict(sc)}')
    print(f'  total outcomes : {dict(tc)}')
    print()
    for r, p in plan[:8]:
        m = r['home_score'] - r['away_score']
        print(f"   {r['game_date']} {r['away_team'][:18]:18} @ "
              f"{r['home_team'][:18]:18} margin {m:+4} "
              f"cs={r.get('close_spread')} ct={r.get('close_total')} -> {p}")
    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return 0

    ok = fail = 0
    print()
    for r, p in plan:
        pr = requests.patch(f'{SB}/rest/v1/ncaab_game_results', headers=H_W,
                            timeout=30,
                            params={'game_id': f"eq.{r['game_id']}"},
                            data=json.dumps(p))
        if pr.status_code not in (200, 204):
            print(f"  !! {r['game_id']} {pr.status_code}: {pr.text[:120]}")
            fail += 1
            continue
        rb = requests.get(f'{SB}/rest/v1/ncaab_game_results', headers=H,
                          timeout=30,
                          params={'select': 'spread_result,total_result',
                                  'game_id': f"eq.{r['game_id']}"})
        got = (rb.json() or [{}])[0]
        if all(str(got.get(k)) == str(v) for k, v in p.items()):
            ok += 1
        else:
            print(f"  !! {r['game_id']} NOT CONFIRMED: wanted {p}, got {got}")
            fail += 1
    print(f'\n  filled and confirmed: {ok}   failed: {fail}')
    after = page('home_score,close_spread,spread_result,close_total,total_result')
    left = sum(1 for r in after
               if r.get('home_score') is not None
               and ((_f(r.get('close_spread')) is not None
                     and not r.get('spread_result'))
                    or (_f(r.get('close_total')) is not None
                        and not r.get('total_result'))))
    print(f'  gradeable-but-unfilled remaining: {left}')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
