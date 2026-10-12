"""Grade the MONEY_FLOW_DIRECTION_BY_SPORT shadow rows.

WHY THIS EXISTS RATHER THAN backtest_rules.py. The generic grader
(`backtest_rules.would_have_hit`) is built around verdict semantics —
BACK / FADE / PASS on a row whose own `result` column is read back. These
rows are a different shape: a SIDE proposal on a game
(`HOME_RL -> AWAY_RL`, or `EMIT_FADED UNDER`), graded from the game's score
against the closing number. Handed to the generic grader every row returns
None, because `before_state['verdict']` does not exist and
`before_v == after_v` short-circuits. A rule that logs forever and never
grades is the failure already on record for pick_lock_drift, so this rule
carries its own grader.

WHAT IT WRITES. Only onto `rule_shadow_log` rows for this rule:
    actual_outcome        'Win' / 'Loss' / 'Push' for the PROPOSED side
    would_have_hit        True when the proposal beat what we actually did
    outcome_backfilled_at timestamp

SEMANTICS, STATED BECAUSE THEY ARE EASY TO GET BACKWARDS:
  * proposed_action 'X->Y' (a FLIP): the proposal is side Y, and it helped
    iff Y won and X lost. Both sides come from one result row, so they
    cannot disagree about what happened.
  * proposed_action 'EMIT_FADED Y': we emitted nothing. The proposal is a
    bet where there was none, so it helped iff Y won. There is no "what we
    did instead" to compare against — declining to bet is 0 units, and the
    comparison is against that.
  * A push grades as Push and sets would_have_hit NULL — never guessed.

WRITES NOTHING unless --apply.

CLI
    python grade_money_flow_shadow.py
    python grade_money_flow_shadow.py --apply
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

RULE = 'MONEY_FLOW_DIRECTION_BY_SPORT'

SPORTS = {
    'MLB': ('mlb_game_results', -1, ('home_ml_close', 'away_ml_close')),
    'NCAAF': ('ncaaf_game_results', -1, ('close_home_ml', 'close_away_ml')),
    'NFL': ('nfl_game_results', +1, ('close_home_ml', 'close_away_ml')),
    'NHL': ('nhl_game_results', -1, ('close_home_ml', 'close_away_ml')),
}

H_WRITE = {**ML.H, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}


def page(table, params, cap=200000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=180)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                                  # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def profit(odds, won):
    o = f(odds)
    if o is None or o == 0 or abs(o) > 2000:
        return None
    if won:
        return (o / 100.0) if o > 0 else (100.0 / -o)
    return -1.0


def grade_side(side_label, res, sport):
    """(won, odds) for a candidate label against one result row.

    Returns (None, None) when the market cannot be graded, and
    ('PUSH', odds) on an exact push — never a guess.
    """
    _res_t, sign, ml_cols = SPORTS[sport]
    hs, as_ = f(res.get('home_score')), f(res.get('away_score'))
    if hs is None or as_ is None:
        return None, None
    margin = hs - as_
    lab = str(side_label or '').upper()
    if lab.endswith('_ML'):
        home = lab.startswith('HOME')
        if abs(margin) < 1e-9:
            return 'PUSH', None
        won = (margin > 0) if home else (margin < 0)
        return won, res.get(ml_cols[0] if home else ml_cols[1])
    if lab.endswith('_RL'):
        cs = f(res.get('close_spread'))
        if cs is None:
            return None, None
        home = lab.startswith('HOME')
        cover = margin - (cs * sign)
        if abs(cover) < 1e-9:
            return 'PUSH', -110
        return ((cover > 0) if home else (cover < 0)), -110
    if lab in ('OVER', 'UNDER'):
        ct = f(res.get('close_total'))
        if ct is None:
            return None, None
        tot = hs + as_
        if abs(tot - ct) < 1e-9:
            return 'PUSH', -110
        return ((tot > ct) if lab == 'OVER' else (tot < ct)), -110
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    rows = page('rule_shadow_log', {
        'select': 'id,sport,game_id,game_date,proposed_action,before_state,'
                  'after_state,actual_outcome,rule_mode,context',
        'rule_name': f'eq.{RULE}'})
    print(f'=== grade {RULE} ===')
    print(f'  {len(rows)} shadow rows logged')
    pend = [r for r in rows if not r.get('actual_outcome')]
    print(f'  {len(pend)} ungraded\n')
    if not pend:
        print('  nothing to grade — the shadow has not fired yet.')
        print('  It fires from ensemble_scorer._handler_split when a sport\'s')
        print('  graded history says the flag side is the wrong side, so it')
        print('  needs a pick run on a slate carrying money-flow flags.')
        return 0

    # Results per sport, fetched once.
    cache = {}
    for sport in {str(r.get('sport')).upper() for r in pend}:
        if sport not in SPORTS:
            continue
        res_t, _s, ml_cols = SPORTS[sport]
        res = page(res_t, {'select': 'game_id,home_score,away_score,'
                                     'close_spread,close_total,'
                                     + ','.join(ml_cols)})
        cache[sport] = {str(x['game_id']): x for x in res if x.get('game_id')}

    tally = defaultdict(lambda: {'helped': 0, 'hurt': 0, 'u_prop': 0.0,
                                 'u_actual': 0.0})
    graded = 0
    for r in pend:
        sport = str(r.get('sport')).upper()
        res = (cache.get(sport) or {}).get(str(r.get('game_id')))
        if not res:
            continue
        act = str(r.get('proposed_action') or '')
        prop_side = (r.get('after_state') or {}).get('side')
        cur_side = (r.get('before_state') or {}).get('side')
        if not prop_side:
            continue
        pw, po = grade_side(prop_side, res, sport)
        if pw is None:
            continue
        if pw == 'PUSH':
            outcome, hit = 'Push', None
            up = 0.0
        else:
            outcome = 'Win' if pw else 'Loss'
            up = profit(po, pw)
            if up is None:
                up = 0.0
            if cur_side:
                cw, co = grade_side(cur_side, res, sport)
                if cw in (None, 'PUSH'):
                    hit = None
                else:
                    hit = bool(pw) and not bool(cw)
                    ua = profit(co, cw)
                    tally[sport]['u_actual'] += (ua if ua is not None else 0.0)
            else:
                # EMIT_FADED: nothing was emitted, so the comparison is
                # against declining to bet, which is exactly 0 units.
                hit = bool(pw)
        tally[sport]['u_prop'] += up
        if hit is True:
            tally[sport]['helped'] += 1
        elif hit is False:
            tally[sport]['hurt'] += 1
        graded += 1
        if not a.apply:
            print(f'  [DRY] {sport} {r.get("game_date")} {act:<28} '
                  f'-> {outcome:<5} would_have_hit={hit}')
            continue
        pr = requests.patch(
            f'{ML.SB}/rest/v1/rule_shadow_log?id=eq.{r["id"]}',
            headers=H_WRITE,
            json={'actual_outcome': outcome, 'would_have_hit': hit,
                  'outcome_backfilled_at':
                      datetime.now(timezone.utc).isoformat()}, timeout=20)
        if pr.status_code not in (200, 201, 204):
            print(f'  PATCH {pr.status_code} on id={r["id"]}: {pr.text[:120]}')

    print(f'\n  graded {graded}')
    print('\n  SCOREBOARD — would the per-sport direction have helped?')
    for sport, t in sorted(tally.items()):
        n = t['helped'] + t['hurt']
        print(f'    {sport:<6} n={n:<4} helped {t["helped"]} · hurt '
              f'{t["hurt"]} · proposed {t["u_prop"]:+.2f}u vs actual '
              f'{t["u_actual"]:+.2f}u')
    print('\n  Promotion is a HUMAN decision. No cell cleared its fade')
    print('  confidence interval when the model was built, so a positive')
    print('  scoreboard here is the start of a case, not a trigger.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
