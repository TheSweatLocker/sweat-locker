"""Shadow a CORRECTED NFL model consensus, and grade it against the live one.

ANDY 2026-10-13, on the priority list: "start at top and work down" — item 1
was "rewire nfl_model_consensus_* from (v4, panel) to (mc, v3)".

WHAT IS WRONG WITH THE LIVE PAIR. Three separate defects, measured:

 1. IT USES THE TWO WORST VOTERS. Of the four spread voters available,
    v4_spread has sd 0.276 across 9 distinct values and a slope of +0.005
    regressed on the line — it is a CONSTANT (~1.74), not a model. panel is
    real but compressed to +0.434 of market scale. Those two are also the
    most correlated pair in deviation terms (+0.925), while mc_expected_margin
    (sd 7.583, on-line slope +0.792, the only voter at market scale) and
    v3_spread (sd 4.042) are the most independent — and both are ignored.

 2. THE SIGN CONVENTION IS INTERNALLY INCONSISTENT. The home condition tests
    `v4_spread < 0`, i.e. it treats negative as home-favoured, while
    nfl_spread_consensus_*_rl tests `v4_spread > close_spread + 0.5`, i.e.
    positive-is-home-margin. v4 is never negative, so the home signal fires
    0 of 59 — the NFL consensus can only ever pick AWAY.

 3. NEITHER CONDITION COMPARES TO THE LINE. `(panel_away - panel_home) > 0`
    means panel thinks the away team wins OUTRIGHT, not that it beats the
    number. On a game where the away side is favoured by 10 and panel
    predicts away by 3, the live signal fires AWAY_RL — a losing ATS bet
    from a correct model opinion. A spread signal has to be measured against
    the spread.

THE CORRECTED FORM, which this shadows:

    HOME_RL  when mc > close + T  AND  v3 > close + T
    AWAY_RL  when mc < close - T  AND  v3 < close - T

with T = CONSENSUS_T points. Both voters must beat the LINE, in the same
direction, by the same margin — which is what "consensus on a spread" means.
Sign convention follows nfl_spread_consensus_*_rl, the one that is already
consistent: NFL close_spread is POSITIVE when the home team is favoured, so
it is directly comparable to a predicted home margin.

WHAT IS LOGGED. Every row where the corrected form fires, together with what
the LIVE pair did on the same game, so the two are graded head to head rather
than the new one being graded in isolation. Grading is ATS at -110, which is
the market these signals emit (HOME_RL / AWAY_RL).

Changes no published pick: the live signals are untouched and this writes only
to rule_shadow_log.

CLI
    python shadow_nfl_consensus.py
    python shadow_nfl_consensus.py --apply
    python shadow_nfl_consensus.py --grade --apply
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

RULE = 'NFL_CONSENSUS_MC_V3'
CONSENSUS_T = 1.0
H_WRITE = {**ML.H, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}


def page(table, params, cap=100000):
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
            time.sleep(3)
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


def mc_margin(g):
    m = g.get('mc_probabilities')
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except Exception:                                      # noqa: BLE001
            return None
    return f(m.get('mc_expected_margin')) if isinstance(m, dict) else None


def sides_for(g):
    """-> (corrected_side, live_side) each 'HOME_RL' | 'AWAY_RL' | None."""
    cs = f(g.get('close_spread'))
    mc = mc_margin(g)
    v3 = f(g.get('v3_spread'))
    v4 = f(g.get('v4_spread'))
    ph, pa = f(g.get('panel_pred_home_pts')), f(g.get('panel_pred_away_pts'))

    new = None
    if cs is not None and mc is not None and v3 is not None:
        if mc > cs + CONSENSUS_T and v3 > cs + CONSENSUS_T:
            new = 'HOME_RL'
        elif mc < cs - CONSENSUS_T and v3 < cs - CONSENSUS_T:
            new = 'AWAY_RL'

    live = None
    if v4 is not None and ph is not None and pa is not None:
        if v4 < 0 and (pa - ph) < 0:
            live = 'HOME_RL'
        elif v4 > 0 and (pa - ph) > 0:
            live = 'AWAY_RL'
    return new, live


def _runner():
    try:
        from rule_registry import RuleRunner, register_rule
        register_rule(
            RULE, 'model',
            description=('Corrected NFL model consensus: mc_expected_margin '
                         'AND v3_spread must BOTH beat the closing line by '
                         f'{CONSENSUS_T} in the same direction. Replaces the '
                         'live pair, which uses a constant (v4_spread) plus a '
                         'compressed model (panel), tests an inconsistent '
                         'sign, and never compares to the line at all.'),
            baseline_hit_rate=52.38, promotion_lift_pp=3.0,
            min_sample=120, mode='shadow')
        return RuleRunner(RULE)
    except Exception as e:                                     # noqa: BLE001
        print(f'  ⚠ rule_registry unavailable ({type(e).__name__}: {e})')
        return None


def log_slate(apply_: bool, days: int):
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    until = (today + timedelta(days=days)).isoformat()
    rows = [g for g in page('nfl_game_context', {'select': '*'})
            if str(today) <= str(g.get('game_date'))[:10] <= until]
    runner = _runner() if apply_ else None
    fired = agree = disagree = only_new = only_live = 0
    print(f'=== shadow {RULE} · [{today} .. {until}] ===')
    print(f'  {len(rows)} context rows · T={CONSENSUS_T}\n')
    for g in sorted(rows, key=lambda z: str(z.get('game_date'))):
        new, live = sides_for(g)
        if new is None and live is None:
            continue
        if new and live:
            agree += (new == live)
            disagree += (new != live)
        elif new:
            only_new += 1
        else:
            only_live += 1
        if new is None:
            continue
        fired += 1
        cs = f(g.get('close_spread'))
        detail = (f"{g.get('game_date')} "
                  f"{str(g.get('away_team'))[:4]:<5} @ "
                  f"{str(g.get('home_team'))[:4]:<5} close={cs} "
                  f"mc={mc_margin(g)} v3={f(g.get('v3_spread'))} "
                  f"-> {new}   (live pair: {live or 'no fire'})")
        if not apply_:
            print(f'  [DRY] {detail}')
            continue
        print(f'  [LOG] {detail}')
        if runner is not None:
            runner.fire(
                sport='NFL', game_date=str(g.get('game_date'))[:10],
                game_id=str(g.get('game_id')),
                target_table='nfl_game_context',
                target_id=str(g.get('game_id')),
                proposed_action=f'CONSENSUS {new}',
                before_state={'side': live},
                after_state={'side': new},
                context={'close_spread': cs, 'mc': mc_margin(g),
                         'v3': f(g.get('v3_spread')),
                         'v4': f(g.get('v4_spread')),
                         'threshold': CONSENSUS_T,
                         'live_side': live})
    print(f'\n  corrected fired {fired} · both fired and AGREED {agree} · '
          f'both fired and DISAGREED {disagree}')
    print(f'  only corrected fired {only_new} · only live fired {only_live}')
    return fired


def grade(apply_: bool):
    rows = page('rule_shadow_log', {
        'select': 'id,game_id,game_date,after_state,before_state,context,'
                  'actual_outcome', 'rule_name': f'eq.{RULE}'})
    pend = [r for r in rows if not r.get('actual_outcome')]
    print(f'=== grade {RULE} ===')
    print(f'  {len(rows)} logged · {len(pend)} ungraded')
    if not pend:
        print('  nothing to grade yet.')
        return 0
    res = {}
    for x in page('nfl_game_results', {
            'select': 'game_id,game_date,home_team,away_team,home_score,'
                      'away_score,close_spread'}):
        if x.get('game_id'):
            res[str(x['game_id'])] = x
    def unit(won):
        return (100.0 / 110.0) if won else -1.0
    tally = defaultdict(list)
    graded = 0
    for r in pend:
        g = res.get(str(r.get('game_id')))
        if not g:
            continue
        hs, as_ = f(g.get('home_score')), f(g.get('away_score'))
        cs = f(g.get('close_spread'))
        if hs is None or as_ is None or cs is None:
            continue
        cover = (hs - as_) - cs          # >0 means home beat the number
        if abs(cover) < 1e-9:
            continue                     # push
        new = str((r.get('after_state') or {}).get('side') or '')
        live = (r.get('before_state') or {}).get('side')
        new_won = (cover > 0) if new == 'HOME_RL' else (cover < 0)
        tally['corrected'].append(unit(new_won))
        if live:
            live_won = (cover > 0) if live == 'HOME_RL' else (cover < 0)
            tally['live'].append(unit(live_won))
            tally['head_to_head'].append(1 if (new_won and not live_won)
                                         else (-1 if (live_won and not new_won)
                                               else 0))
        graded += 1
        if not apply_:
            print(f'  [DRY] {r.get("game_date")} {new} '
                  f'{"WIN" if new_won else "LOSS"}'
                  + (f'  live {live} '
                     f'{"WIN" if live_won else "LOSS"}' if live else ''))
            continue
        requests.patch(f'{ML.SB}/rest/v1/rule_shadow_log?id=eq.{r["id"]}',
                       headers=H_WRITE,
                       json={'actual_outcome': 'Win' if new_won else 'Loss',
                             'would_have_hit': new_won,
                             'outcome_backfilled_at':
                                 datetime.now(timezone.utc).isoformat()},
                       timeout=20)
    print(f'\n  graded {graded}')
    for k in ('corrected', 'live'):
        v = tally.get(k) or []
        if len(v) >= 10:
            w = sum(1 for x in v if x > 0)
            print(f'  {k:<10} {w}-{len(v) - w}  ROI '
                  f'{statistics.fmean(v) * 100:+.1f}%  n={len(v)}')
        elif v:
            print(f'  {k:<10} n={len(v)} too thin')
    h = tally.get('head_to_head') or []
    if h:
        print(f'  head to head on the SAME games: corrected better '
              f'{sum(1 for x in h if x > 0)} · live better '
              f'{sum(1 for x in h if x < 0)} · same {sum(1 for x in h if x == 0)}')
    print('\n  Promotion is manual: rewire the two signal_sources rows only')
    print('  once this record justifies it.')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--grade', action='store_true')
    ap.add_argument('--days', type=int, default=8)
    a = ap.parse_args()
    if a.grade:
        return grade(a.apply)
    log_slate(a.apply, a.days)
    return 0


if __name__ == '__main__':
    sys.exit(main())
