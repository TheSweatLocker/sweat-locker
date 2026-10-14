#!/usr/bin/env python3
"""Shadow suppressing NFL `total` as a primary play.

══ 2026-10-14 · THE CASE, MEASURED ══
The NFL engine emits a TOTAL as the headline pick on **56 of 316**
`nfl_game_context` rows. The projection behind it is not a model of the total:

  * `projected_total` mean **45.00**, sd **2.31**, against a market sd of
    **5.85**. 65% of games land within ±1 of `BASE_TOTAL = 44.5` and 177 of 316
    round to exactly 44, because `compute_projections` blends 70% matchup with
    30% anchored to BASE_TOTAL.
  * correlation with the closing total is **+0.134**.
  * so the O/U lean is a MECHANICAL FUNCTION OF THE LINE'S LEVEL: on games with
    a market total ≥ 47 it leans UNDER **91 of 100**; on games ≤ 41 it leans
    OVER **96%**. That is the line reflected back, not a read.
  * 27 NFL total-scoped registry signals, **zero** with any measured hit rate.

══ AND THE INPUTS CANNOT FIX IT ══
Before proposing suppression I tested whether NFL's own stored team stats could
build a better total — off_rating, def_ppg, pass/rush matchup pairs, EPA, all
populated 193/193. On 79 games with a final and a closing total:

    in-sample        market+stats beat market-alone by +0.479 MAE (R² .263→.330)
    leave-one-out    −0.154 MAE  (WORSE)
    temporal split   −1.949 MAE  (WORSE)

The in-sample gain was entirely overfitting — 79 rows across 6 parameters. So
de-anchoring would not produce a better total either; there is no out-of-sample
total signal in what we own. This is a different diagnosis from NHL, where the
unused inputs were worth ~zero even IN-sample (see the 10-14 log).

That combination — a lean provably determined by the line, and no recoverable
signal in the inputs — is why the proposal is to stop emitting the pick rather
than to rebuild the model.

══ WHY A SHADOW AND NOT A GATE ══
feedback_suppression_gate_needs_shadow. A suppression removes picks from a
paying user's screen, and the graded NFL total record is tiny (19 receipts: 11
VOID, 5 WIN, 3 LOSS; 15 jerry_reads: 1 Win, 3 Loss, 9 ungraded). 8 graded
receipts cannot justify a permanent gate in either direction. This logs what
WOULD be suppressed and what the engine's next-best market was, so the decision
is made on evidence later.

Promotion needs the shadow log plus `backtest_rules.py` — and the bar is not
"the suppressed picks lost". It is "the alternative the engine would have
emitted did BETTER", which is the only comparison that justifies the swap.

USAGE
    python shadow_nfl_total_suppress.py --register     # once
    python shadow_nfl_total_suppress.py
"""
from __future__ import annotations

import argparse
import collections
import datetime
import sys

import requests

import market_line as ML
from rule_registry import RuleRunner, register_rule

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
RULE = 'nfl_total_suppress'
#: BASE_TOTAL from nfl_game_context. A projection inside this band of it is
#: indistinguishable from the anchor rather than a matchup read.
BASE_TOTAL = 44.5
ANCHOR_BAND = 2.0


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code} {r.text[:160]}')
            return out
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


def do_register() -> None:
    ok = register_rule(
        rule_name=RULE, rule_class='suppression', sport='NFL',
        description=('Suppress NFL total as a primary play: projected_total is '
                     'anchored to BASE_TOTAL 44.5 (sd 2.31 vs market 5.85, '
                     'corr +0.134) so its O/U lean is a function of the line '
                     'level (91% UNDER above 47, 96% OVER below 41), and team '
                     'stats add nothing out-of-sample (LOO -0.154 MAE).'),
        baseline_hit_rate=52.38, promotion_lift_pp=2.0, min_sample=40,
        mode='shadow')
    print(f'  register {RULE}: {"ok" if ok else "FAILED"}')


def run() -> int:
    runner = RuleRunner(RULE)
    print(f'=== {RULE} · mode={runner.mode}')
    if runner.mode == 'off':
        print('  mode is off — nothing logged')
        return 0

    today = datetime.date.today().isoformat()
    ctx = page('nfl_game_context', {'select': '*'})
    fired = 0
    tally = collections.Counter()
    anchored = collections.Counter()
    for c in ctx:
        pp = c.get('primary_play') or {}
        mkt = str(pp.get('type') or pp.get('market') or '')
        if mkt != 'total':
            tally[mkt or 'none'] += 1
            continue
        tally['total'] += 1
        ct = _f(c.get('close_total'))
        pt = _f(c.get('projected_total'))
        # Record WHY it qualifies, so the log can be read without re-deriving
        near_anchor = pt is not None and abs(pt - BASE_TOTAL) <= ANCHOR_BAND
        anchored['near_anchor' if near_anchor else 'off_anchor'] += 1
        alt = (pp.get('_ensemble_all_markets') or {})
        alt_markets = {k: v.get('label') for k, v in alt.items()
                       if isinstance(v, dict) and k != 'total' and v.get('label')}
        if str(c.get('game_date'))[:10] < today:
            continue            # forward-looking log only
        runner.fire(
            sport='NFL', target_table='nfl_game_context',
            target_id=str(c.get('game_id')),
            game_date=str(c.get('game_date'))[:10],
            game_id=str(c.get('game_id')),
            proposed_action='SUPPRESS_TOTAL_PRIMARY',
            before_state={'tier': pp.get('tier'), 'label': pp.get('label'),
                          'conviction': pp.get('conviction'),
                          'projected_total': pt, 'close_total': ct},
            after_state={'alternatives': alt_markets or None},
            context={'matchup': f"{c.get('away_team')} @ {c.get('home_team')}",
                     'near_anchor': near_anchor,
                     'anchor_delta': (None if pt is None else round(pt - BASE_TOTAL, 2)),
                     'lean': (None if (pt is None or ct is None)
                              else ('OVER' if pt > ct else 'UNDER')),
                     'why': 'projected_total is anchored; lean is line-determined'})
        fired += 1
        print(f"  FIRE {c.get('game_date')} {c.get('away_team')} @ "
              f"{c.get('home_team')}  {pp.get('tier')} {pp.get('label')!r}  "
              f"proj={pt} close={ct}  alt={alt_markets or '—'}")

    print(f'\n  primary_play market mix across {len(ctx)} rows: {dict(tally)}')
    print(f'  of the total picks: {dict(anchored)} '
          f'(near_anchor = |proj − {BASE_TOTAL}| <= {ANCHOR_BAND})')
    print(f'  shadow rows logged (upcoming only): {fired}')
    if tally['total'] and not fired:
        print('  note: all total primaries are in the past — nothing forward to '
              'log yet, which is expected mid-week.')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--register', action='store_true')
    a = ap.parse_args()
    if a.register:
        do_register()
        return 0
    return run()


if __name__ == '__main__':
    sys.exit(main())
