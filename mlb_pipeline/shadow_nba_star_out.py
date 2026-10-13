"""Shadow the NBA star-out fade until it has earned the right to publish.

ANDY 2026-10-13, on the recommendation to shadow rather than ship: "go
ahead".

WHAT THIS SHADOWS. Three signals have been enabled since 2026-08-22 and
have never fired, because they read `ctx.home_star_out` / `ctx.away_star_out`
— columns that do not exist:

    nba_home_star_out_fade              side AWAY_ML
    nba_away_star_out_fade              side HOME_ML
    nba_prop_star_out_teammate_boost    prop scope

The data now exists as `*_injury_impact` (points-per-game lost, summed over
OUT players), written by enrich_nba_injuries_to_context.py. The three
conditions were rewired to `>= 15.0` ppg and set enabled=FALSE, so they
still contribute nothing to a published pick. This file logs what they
WOULD have done and grades it.

WHAT THE BAR IS APPLIED TO. The DIFFERENTIAL, imp - opp_imp, and only when
the depleted side actually has a starter out. A first draft used the
absolute figure and fired on both sides of POR @ GSW (31.7 and 19.4),
proposing to fade both teams in one game; it also fired on WAS at 16.2 with
starters_out == [], where the number was a sum of bench absences rather
than a star.

WHY 15 PPG IS A GUESS, AND WHY THAT MATTERS. "A star is out" is not the
same event as "a starter is out". 15 ppg is roughly a starting-calibre
scorer: on the 2026-10-13..21 slate it separates GSW 31.7, LAC 44.7 and
POR 19.4 from MIN 11.8 and IND 5.4, which reads about right. But there is
NO graded NBA history to calibrate it against — the season opens 10-20 —
so promoting on that reasoning alone would put an uncalibrated threshold
straight into live picks. The whole point of this file is that the
threshold gets tested instead of asserted.

It also records the margin at several thresholds, not just 15, so the bar
can be chosen from the accumulated record rather than re-guessed.

HOW IT GRADES. The fade side is graded on the MONEYLINE with the real
closing price, because that is the side_expr (AWAY_ML / HOME_ML) — a fade
of a depleted team at -250 winning 60% is a loser, and a hit rate alone
would call it an edge. Games without a stored closing moneyline are left
ungraded rather than assumed at -110.

Writes only to rule_shadow_log, via the standard RuleRunner, so
promote_rule.py and the /tasks surfaces see it like any other shadow.

CLI
    python shadow_nba_star_out.py              # log today's slate (dry)
    python shadow_nba_star_out.py --apply      # log for real
    python shadow_nba_star_out.py --grade --apply
"""
from __future__ import annotations

import argparse
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

RULE = 'NBA_STAR_OUT_FADE'
THRESHOLD = 15.0
#: Extra bars recorded alongside the live one so the threshold can be
#: chosen from evidence later instead of re-guessed.
PROBE_BARS = (10.0, 15.0, 20.0, 30.0)

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


def profit(odds, won):
    o = f(odds)
    if o is None or o == 0 or abs(o) > 2000:
        return None
    if won:
        return (o / 100.0) if o > 0 else (100.0 / -o)
    return -1.0


def _runner():
    try:
        from rule_registry import RuleRunner, register_rule
        register_rule(
            RULE, 'situational',
            description=('NBA star-out fade. Rewired from the nonexistent '
                         'ctx.*_star_out to ctx.*_injury_impact >= 15 ppg '
                         '(points-per-game lost to OUT players). The three '
                         'signal_sources rows stay enabled=false until this '
                         'shadow has graded evidence; 15 is a reasoned guess '
                         'with no NBA history behind it.'),
            baseline_hit_rate=52.38, promotion_lift_pp=4.0,
            min_sample=60, mode='shadow')
        return RuleRunner(RULE)
    except Exception as e:                                     # noqa: BLE001
        print(f'  ⚠ rule_registry unavailable ({type(e).__name__}: {e})')
        return None


def log_slate(apply_: bool, days: int):
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    until = (today + timedelta(days=days)).isoformat()
    rows = [g for g in page('nba_game_context', {'select': '*'})
            if str(today) <= str(g.get('game_date'))[:10] <= until]
    runner = _runner() if apply_ else None
    fired = 0
    print(f'=== shadow {RULE} · slate [{today} .. {until}] ===')
    print(f'  {len(rows)} context rows · threshold {THRESHOLD:.0f} ppg\n')
    for g in sorted(rows, key=lambda z: str(z.get('game_date'))):
        for side, opp_label in (('home', 'AWAY_ML'), ('away', 'HOME_ML')):
            other = 'away' if side == 'home' else 'home'
            imp = f(g.get(f'{side}_injury_impact'))
            opp_imp = f(g.get(f'{other}_injury_impact')) or 0.0
            if imp is None:
                continue
            outs = g.get(f'{side}_starters_out') or []
            # ══ TWO GATES THE FIRST DRAFT GOT WRONG ══
            # 1. DIFFERENTIAL, not absolute. On 2026-10-16 POR @ GSW the
            #    first version fired on BOTH sides (home 31.7, away 19.4)
            #    and proposed fading both teams in the same game. If both
            #    benches are thin the edge is the gap between them, so the
            #    bar applies to imp - opp_imp.
            # 2. A STARTER MUST ACTUALLY BE OUT. WAS showed impact 16.2
            #    with starters_out == [] — the figure was a sum of bench
            #    absences. That is not "a star is out", and firing a
            #    star-out fade on it would be the column lying by another
            #    route.
            if (imp - opp_imp) < THRESHOLD or not outs:
                continue
            fired += 1
            detail = (f"{g.get('game_date')} "
                      f"{str(g.get('away_team'))[:16]} @ "
                      f"{str(g.get('home_team'))[:16]} · {side} impact "
                      f"{imp:.1f} vs {opp_imp:.1f} (diff "
                      f"{imp - opp_imp:+.1f}) out={outs} -> fade "
                      f"to {opp_label}")
            if not apply_:
                print(f'  [DRY] {detail}')
                continue
            print(f'  [LOG] {detail}')
            if runner is not None:
                runner.fire(
                    sport='NBA',
                    game_date=str(g.get('game_date'))[:10],
                    game_id=str(g.get('game_id')),
                    target_table='nba_game_context',
                    target_id=f"{g.get('game_id')}|{side}",
                    proposed_action=f'FADE {opp_label}',
                    before_state={'side': None, 'reason': 'signal disabled'},
                    after_state={'side': opp_label},
                    context={'injury_impact': imp,
                             'opp_injury_impact': opp_imp,
                             'impact_diff': round(imp - opp_imp, 1),
                             'starters_out': outs,
                             'threshold': THRESHOLD,
                             'depleted_side': side})
    print(f'\n{"[DRY] would log" if not apply_ else "logged"} {fired} fires')
    return fired


def grade(apply_: bool):
    rows = page('rule_shadow_log', {
        'select': 'id,game_id,game_date,proposed_action,after_state,context,'
                  'actual_outcome',
        'rule_name': f'eq.{RULE}'})
    pend = [r for r in rows if not r.get('actual_outcome')]
    print(f'=== grade {RULE} ===')
    print(f'  {len(rows)} logged · {len(pend)} ungraded')
    if not pend:
        print('  nothing to grade yet — the shadow needs played games.')
        return 0
    res = {str(x['game_id']): x for x in page('nba_game_results', {
        'select': 'game_id,home_score,away_score,close_home_ml,close_away_ml'})
        if x.get('game_id')}
    tally = defaultdict(list)
    bars = defaultdict(list)
    graded = 0
    for r in pend:
        g = res.get(str(r.get('game_id')))
        if not g:
            continue
        hs, as_ = f(g.get('home_score')), f(g.get('away_score'))
        if hs is None or as_ is None:
            continue
        side = str((r.get('after_state') or {}).get('side') or '')
        if side == 'AWAY_ML':
            won, odds = as_ > hs, g.get('close_away_ml')
        elif side == 'HOME_ML':
            won, odds = hs > as_, g.get('close_home_ml')
        else:
            continue
        u = profit(odds, won)
        if u is None:
            # No stored closing price: record the result but never invent
            # -110 on a moneyline — that is how a fade of a heavy favourite
            # gets scored as profitable.
            outcome = 'Win' if won else 'Loss'
            if apply_:
                requests.patch(
                    f'{ML.SB}/rest/v1/rule_shadow_log?id=eq.{r["id"]}',
                    headers=H_WRITE,
                    json={'actual_outcome': outcome, 'would_have_hit': won,
                          'outcome_backfilled_at':
                              datetime.now(timezone.utc).isoformat()},
                    timeout=20)
            graded += 1
            tally['no_price'].append(1 if won else 0)
            continue
        tally['priced'].append(u)
        ctxb = r.get('context') or {}
        imp = (f(ctxb.get('impact_diff'))
               if ctxb.get('impact_diff') is not None
               else f(ctxb.get('injury_impact'))) or 0.0
        for b in PROBE_BARS:
            if imp >= b:
                bars[b].append(u)
        outcome = 'Win' if won else 'Loss'
        graded += 1
        if not apply_:
            print(f'  [DRY] {r.get("game_date")} {side} impact {imp:.1f} '
                  f'-> {outcome} {u:+.2f}u')
            continue
        requests.patch(f'{ML.SB}/rest/v1/rule_shadow_log?id=eq.{r["id"]}',
                       headers=H_WRITE,
                       json={'actual_outcome': outcome, 'would_have_hit': won,
                             'outcome_backfilled_at':
                                 datetime.now(timezone.utc).isoformat()},
                       timeout=20)
    print(f'\n  graded {graded}')
    pr = tally.get('priced') or []
    if pr:
        print(f'  PRICED fades: n={len(pr)} ROI '
              f'{statistics.fmean(pr) * 100:+.1f}%')
    if tally.get('no_price'):
        print(f'  unpriced (hit rate only): n={len(tally["no_price"])} '
              f'{statistics.fmean(tally["no_price"]) * 100:.1f}%')
    if bars:
        print('\n  ROI by threshold — pick the bar from THIS, not from 15:')
        for b in PROBE_BARS:
            v = bars.get(b) or []
            if len(v) < 10:
                print(f'    >= {b:>4.0f} ppg   n={len(v)} too thin')
                continue
            print(f'    >= {b:>4.0f} ppg   n={len(v):<4} ROI '
                  f'{statistics.fmean(v) * 100:+6.1f}%')
    print('\n  Promotion is a human decision: flip the three signal_sources '
          'rows\n  to enabled=true only once a bar clears on this record.')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--grade', action='store_true')
    ap.add_argument('--days', type=int, default=3)
    a = ap.parse_args()
    if a.grade:
        return grade(a.apply)
    log_slate(a.apply, a.days)
    return 0


if __name__ == '__main__':
    sys.exit(main())
