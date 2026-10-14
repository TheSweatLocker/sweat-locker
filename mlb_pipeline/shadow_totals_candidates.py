#!/usr/bin/env python3
"""Shadow the two totals rules that beat their direction control.

Andy 2026-10-13: "lets get all totals up to speed".

audit_totals_edge.py measured every feature in feature_history against the
TOTALS residual (actual_total - close_total) on leak-free rows, split by time.
Two rules came out of it with a real lift over the direction-only control, and
NEITHER clears a bootstrap CI. So they are shadowed, not shipped.

    NFL    away_pa_pg >= 27.4   -> take UNDER
           test 138/252 (54.76%) +4.49% ROI, CI[-6.8%, +15.8%]
           direction-only control (UNDER on all 2,221 test games): -2.18%
           feature lift +6.67pp

    NCAAF  away_ou_unders >= 6  -> take OVER
           test 196/369 (53.12%) +1.40% ROI, CI[-8.1%, +11.4%]
           direction-only control (OVER on all 1,625 test games): -7.99%
           feature lift +9.39pp
           its out-of-sample correlation (+0.0596) was STRONGER than its
           in-sample one (+0.0499), which is the opposite of the overfit shape

WHY THESE TWO AND NOTHING ELSE. The audit's other candidates failed the
control, which is the whole point of having one:
    NFL   home_avg_total  UNDER  lift +0.26pp  — selecting nothing
    NCAAF away_avg_total  UNDER  lift -2.06pp  — actively worse than the side
    NCAAF away_last5_total UNDER lift -4.79pp
A rule that cannot beat "bet this side on everything" has found no games.

BOTH ARE REGRESSION-TO-MEAN IN SHAPE, which is worth stating because it is a
falsifiable story rather than a curve fit: a defence that has been leaking
points (away_pa_pg high) and a team whose games keep going UNDER
(away_ou_unders high) are both cases where the market appears to extrapolate
recent scoring and over-correct. If that is real it should persist; if it was
window luck it will not.

CI STRADDLES ZERO ON BOTH, so the honest status is "worth measuring forward",
not "an edge". Promotion needs the shadow log to accumulate and
backtest_rules.py to grade it — min_sample 100 rather than the default 30,
because a totals rule at 53-55% needs more than 30 bets to separate from noise.

USAGE
    python shadow_totals_candidates.py --register     # once
    python shadow_totals_candidates.py --sport NFL
    python shadow_totals_candidates.py --sport NCAAF
"""
from __future__ import annotations

import argparse
import sys

import requests

from compute_margin_strength import H, SB
from rule_registry import RuleRunner, register_rule

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

# ══ READ THIS BEFORE TREATING ANY OF THESE AS A FINDING ══
# The audit was run across 6 sports, scoring ~65 features each and trying 5
# thresholds per survivor. That is on the order of 48 graded rules. At a 5%
# bar you would EXPECT roughly two or three to look good by chance, and the
# strongest lifts measured here (+3 to +10pp over the direction control) are
# exactly the size chance produces at that search width.
#
# NOT ONE of them cleared a bootstrap CI. Every single rule's interval
# straddles zero:
#   MLB   away_srs >= 0.281       -> UNDER  239/435 54.94%  CI[-3.7%, +12.5%]  lift +10.30pp
#   NCAAF away_ou_unders >= 6     -> OVER   196/369 53.12%  CI[-8.1%, +11.4%]  lift  +9.39pp
#   NFL   away_pa_pg >= 27.4      -> UNDER  138/252 54.76%  CI[-6.8%, +15.8%]  lift  +6.67pp
#   MLB   away_last5_margin >= 2.2 -> UNDER 115/219 52.51%  CI[-12.1%,+11.4%]  lift  +5.90pp
#   NBA   away_ats_l5_losses >= 2 -> UNDER  383/731 52.39%  CI[-7.0%, +6.8%]   lift  +4.47pp
#   NCAAB win_pct_diff >= 0.217   -> OVER   301/572 52.62%  CI[-7.5%, +8.8%]   lift  +3.12pp
#
# A pattern worth naming rather than six independent discoveries: FIVE OF SIX
# are AWAY-side features, and most point UNDER. A single away-side artefact
# would produce exactly that, so these may be one thing wearing six hats.
#
# Three are shadowed below — the strongest per sport family — purely because
# FORWARD data is the only thing that can separate them from the noise above.
# Nothing here is promotable on what has been measured.
#
#: (rule_name, feature, threshold, side, context table)
RULES = {
    'NFL': ('totals_away_pa_pg_under', 'away_pa_pg', 27.4, 'UNDER',
            'nfl_game_context'),
    'NCAAF': ('totals_away_ou_unders_over', 'away_ou_unders', 6.0, 'OVER',
              'ncaaf_game_context'),
    'MLB': ('totals_away_srs_under', 'away_srs', 0.281, 'UNDER',
            'mlb_game_context'),
}


def page(table: str, params: dict) -> list[dict]:
    out, off = [], 0
    while True:
        p = dict(params, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {table} {r.status_code} {r.text[:160]}')
            return out
        b = r.json()
        if not isinstance(b, list):
            return out
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def do_register() -> None:
    for sport, (name, feat, th, side, _tbl) in RULES.items():
        ok = register_rule(
            rule_name=name, rule_class='totals', sport=sport,
            description=(f'{sport}: take {side} when {feat} >= {th}. '
                         f'Measured lift over the direction-only control, '
                         f'CI straddles zero — shadow only.'),
            baseline_hit_rate=52.38, promotion_lift_pp=2.0, min_sample=100,
            mode='shadow')
        print(f'  register {name} ({sport}): {"ok" if ok else "FAILED"}')


def run(sport: str) -> int:
    name, feat, th, side, ctx_tbl = RULES[sport]
    runner = RuleRunner(name)
    print(f'=== {name} · mode={runner.mode} · {sport}')
    if runner.mode == 'off':
        print('  mode is off — nothing logged')
        return 0

    # Upcoming games only: a shadow row is a FORWARD prediction. Logging past
    # games would re-log what the audit already measured and pollute the
    # forward sample with in-sample rows.
    import datetime as _dt
    today = _dt.date.today().isoformat()
    ctx = [c for c in page(ctx_tbl, {'select': '*'})
           if str(c.get('game_date'))[:10] >= today]
    fh = {str(r['game_id']): r for r in
          page('feature_history', {'select': '*', 'sport': f'eq.{sport}'})}
    # feature_history is keyed on the RESULTS id space, which for NFL/NCAAF is
    # not the context id space (nfl_game_id_bridge). Join on (date, teams).
    by_key = {}
    for r in fh.values():
        by_key[(str(r['game_date'])[:10], str(r['home_team']),
                str(r['away_team']))] = r

    fired = checked = nofeat = 0
    for c in ctx:
        key = (str(c.get('game_date'))[:10], str(c.get('home_team')),
               str(c.get('away_team')))
        row = fh.get(str(c.get('game_id'))) or by_key.get(key)
        ct = c.get('close_total')
        if row is None or ct is None:
            nofeat += 1
            continue
        v = (row.get('features') or {}).get(feat)
        if v is None:
            nofeat += 1
            continue
        checked += 1
        if float(v) < th:
            continue
        runner.fire(
            sport=sport, target_table=ctx_tbl,
            target_id=str(c.get('game_id')), game_date=str(c.get('game_date'))[:10],
            game_id=str(c.get('game_id')),
            proposed_action=f'TOTAL_{side}',
            before_state={'close_total': float(ct)},
            after_state={'pick': side, 'line': float(ct)},
            context={'feature': feat, 'value': float(v), 'threshold': th,
                     'matchup': f"{c.get('away_team')} @ {c.get('home_team')}",
                     'audit': 'audit_totals_edge.py 2026-10-13'})
        fired += 1
        print(f"  FIRE {c.get('game_date')} {c.get('away_team')} @ "
              f"{c.get('home_team')}  {feat}={float(v):.2f} >= {th}  "
              f"-> {side} {float(ct)}")
    print(f'  upcoming ctx rows={len(ctx)}  with a feature row={checked}  '
          f'unjoinable/no feature={nofeat}  fired={fired}')
    if checked == 0 and ctx:
        print('  ⚠ zero games could be joined to feature_history — rebuild it '
              '(build_feature_history.py --apply) before trusting a 0-fire run')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(RULES))
    ap.add_argument('--register', action='store_true')
    a = ap.parse_args()
    if a.register:
        do_register()
        return 0
    if not a.sport:
        ap.error('--sport or --register required')
    return run(a.sport)


if __name__ == '__main__':
    sys.exit(main())
