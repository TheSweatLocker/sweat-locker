"""Set a tunable NCAAB weight baseline in signal_registry.

== WHY ====================================================================
Andy, 2026-11-03: "Nah we need to have a baseline for ncaab models and
overall engine (decision makers), lets set something and we will evaluate
and tune and weight as we go. I need a product."

That overrides my recommendation to wait for a split-half, and it is the
right call for shipping -- but a baseline still has to be anchored to
something, or the weekly refit has nothing to move away from.

Current state: all 65 NCAAB rows in signal_registry are
`recommended_weight=0.0, sample_n=0, tier='UNVALIDATED'`, so NCAAB
contributes nothing even once the ensemble is wired.

== HOW THE NUMBERS ARE CHOSEN ============================================
From backtest_ncaab_signals.py over 221 real graded games (pre-game
snapshots, real closing prices). The only honest figure for a signal that
names ONE side on every firing is SELECTION value -- does its subset beat
that side's rate across the whole sample? -- because the direction-only
control is identical to the signal by construction.

    weight = selection_pp / 100, capped at 0.153

0.153 is not arbitrary: it is the ceiling measured for NFL priors
(project_nfl_stale_line_and_inverted_weights_1030), and every graded signal
in this repo sits in 0.016-0.153. Using the same scale means NCAAB cannot
out-vote a measured signal from another sport.

    measured positive, n>=30   weight = sel/100  tier PROVISIONAL
    measured negative, n>=30   weight = 0.000    tier ANTI_VALIDATED
    fires on everything, 0pp   weight = 0.000    tier ANTI_VALIDATED
    cannot fire at all         weight = 0.000    tier REJECTED
    unmeasured (n<30 or 0)     weight = 0.020    tier UNVALIDATED

**PROVISIONAL, not VALIDATED.** One week of opening-week buy games, UNDER
running 57.04%, no split-half possible at n=41-73. The tier says so.

**The prior is 0.020, below the smallest measured positive (0.031).** An
unmeasured signal must never outweigh a measured one -- the 0.300 default
other sports carry is 2x the best tested sibling in families scoring
0.00-0.15 (feedback_unmeasured_signal_must_not_outweigh_measured), and
copying it here would have let 40 never-fired signals outvote the four that
actually measured positive.

== ZERO WEIGHT IS NOT "DISABLED" =========================================
A 0.000 signal still FIRES and is still recorded in attribution, so the
weekly refit keeps measuring it and can promote it later. That is the point
of shipping a baseline: it accrues.

Readback-verified per row.

    python set_ncaab_signal_baseline.py            # dry run
    python set_ncaab_signal_baseline.py --apply
"""
import collections
import json
import os
import sys
from datetime import datetime, timezone
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

#: the measured ceiling for a prior anywhere in this repo
WEIGHT_CAP = 0.153
#: strictly below the smallest measured positive weight (0.031)
PRIOR = 0.020
MIN_N = 30

#: measured on 221 graded games, 2026-11-03. (selection_pp, hit_pct, n, roi)
MEASURED = {
    'away_team_ats_cold_season':               (+13.04, 65.85, 41, +25.72),
    # == CORRECTED BEFORE SHIPPING: n=73 LOST TO n=576 ==================
    # These two measured +4.60pp and +4.39pp on my 221-game sample and I
    # was about to weight them. ensemble_scorer's TOP_PICK_BARRED comment
    # already carries a BETTER-POWERED measurement of the same thing:
    # NCAAB totals posted 155-165 take the UNDER at **45.49% on n=576,
    # CI[41.5,49.6]** -- an upper bound below breakeven -- and it warns that
    # "the model's output concentrates in the worst measured total band".
    #
    # Checked directly: 33 of each signal's firings land in 155-165, and on
    # that subset they read **69.70% n=33**, which the n=576 CI excludes.
    # One week of opening-week buy games with UNDER running 57.04% overall
    # is not evidence against 576 games. So both go to zero, NCAAB totals
    # stay BARRED, and if the season proves otherwise the weekly refit will
    # say so (feedback_unmeasured_signal_must_not_outweigh_measured, and
    # the reverse: a weaker measurement must not overturn a stronger one).
    'ncaab_projected_total':                   (+4.60, 61.64, 73, -1.0),
    'ncaab_projection_contradicts_total_over': (+4.39, 61.43, 70, -1.0),
    'home_team_ats_hot_season':                (+3.12, 55.93, 59, +6.78),
    'away_ats_cold_on_road':                   (+0.00, 52.82, 142, +0.83),
    'home_ats_cold_at_home':                   (+0.00, 47.18, 142, -9.92),
    'ncaab_home_efficiency_edge':              (+8.43, 92.59, 108, -6.89),
    'ncaab_projected_spread':                  (+6.91, 91.07, 112, -7.68),
    'ncaab_confluence_home':                   (+4.93, 89.09, 110, -8.47),
    'ncaab_home_court_strong':                 (+4.73, 88.89, 126, -8.28),
    'home_ml_hot_at_home':                     (+5.84, 90.00, 60, -0.70),
    'ncaab_away_spread_edge':                  (-0.44, 46.74, 92, -10.77),
    'home_team_over_trend_season':             (-3.97, 38.98, 59, -25.58),
    'away_team_under_trend_season':            (-5.53, 51.52, 33, -1.65),
}

#: conditions that raise NameError, so they can NEVER be true on any row
DEAD_EXPR = {'sharp_split_triple_confirmed_ncaab', 'sharp_split_confirmed_ncaab',
             'sharp_scenario_match_ncaab', 'external_handicapper_pick_ncaab',
             'oddscrowd_ml_fade_boost_ncaab', 'oddscrowd_spread_fade_boost_ncaab',
             'oddscrowd_total_fade_boost_ncaab'}
#: reference ctx columns that exist in NO table
DEAD_COLS = {'ncaab_panel_total', 'ncaab_efficiency_home_dominant',
             'ncaab_efficiency_away_dominant', 'ncaab_3pt_shooting_gap_home',
             'ncaab_home_court_advantage', 'ncaab_conference_dog_cover'}

#: the sample's direction-only base rates, for baseline_pct
CONTROL = {'ml': 84.16, 'rl': 52.82, 'total': 57.04, '*': 52.38}


def page(table: str, select: str, **extra) -> list:
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': 1000, 'offset': off}
        p.update(extra)
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=90,
                         params=p)
        r.raise_for_status()
        b = r.json() or []
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def decide(name: str, scope: str):
    """(weight, tier, note, sample_n, hit, baseline) for one signal."""
    if name in DEAD_EXPR:
        return (0.0, 'REJECTED',
                'condition raises NameError (bare _HANDLER_ placeholder) so it '
                'can never be true on any row', 0, None, None)
    if name in DEAD_COLS:
        return (0.0, 'REJECTED',
                'references a ctx column that exists in no table', 0, None,
                None)
    m = MEASURED.get(name)
    if not m:
        return (PRIOR, 'UNVALIDATED',
                f'no firing with n>={MIN_N} on the 221-game sample; prior '
                f'{PRIOR} is below the smallest measured positive weight',
                0, None, None)
    sel, hit, n, roi = m
    base = CONTROL.get(str(scope or '*').lower(), CONTROL['*'])
    if name in ('ncaab_projected_total',
                'ncaab_projection_contradicts_total_over'):
        return (0.0, 'ANTI_VALIDATED',
                f'my sample says {sel:+.2f}pp on n={n}, but 33 of its firings '
                f'sit in the 155-165 total band measured at 45.49% on n=576, '
                f'CI[41.5,49.6] -- the stronger measurement wins and NCAAB '
                f'totals stay barred', n, hit, base)
    if roi <= 0:
        return (0.0, 'ANTI_VALIDATED',
                f'measured NEGATIVE: hit {hit:.2f}% n={n}, ROI {roi:+.2f}% at '
                f'the real closing price', n, hit, base)
    if abs(sel) < 0.01:
        return (0.0, 'ANTI_VALIDATED',
                f'fires on every game with 0.00pp selection value — it is the '
                f'direction control, not a signal (n={n})', n, hit, base)
    w = round(min(sel / 100.0, WEIGHT_CAP), 3)
    return (w, 'PROVISIONAL',
            f'selection {sel:+.2f}pp over the {scope} control, hit {hit:.2f}% '
            f'n={n}, ROI {roi:+.2f}%. PROVISIONAL: one week of opening-week '
            f'buy games, no split-half', n, hit, base)


def main() -> int:
    reg = [r for r in page('signal_registry', '*')
           if str(r.get('sport')).upper() == 'NCAAB']
    srcs = {s['signal_key']: s for s in page('signal_sources', '*')
            if str(s.get('sport')).upper() == 'NCAAB'}
    print(f'signal_registry NCAAB rows: {len(reg)}   '
          f'signal_sources NCAAB: {len(srcs)}')

    plan = []
    for r in reg:
        name = str(r.get('signal_name'))
        scope = r.get('market_scope') or (srcs.get(name) or {}).get('market_scope')
        w, tier, note, n, hit, base = decide(name, scope)
        plan.append((r, name, scope, w, tier, note, n, hit, base))

    bytier = collections.Counter(p[4] for p in plan)
    print(f'\n  tier assignment: {dict(bytier)}')
    tot = sum(p[3] for p in plan)
    print(f'  total weight across {len(plan)} signals: {tot:.3f}  '
          f'(max single {max(p[3] for p in plan):.3f})')
    print()
    for r, name, scope, w, tier, note, n, hit, base in sorted(
            plan, key=lambda x: (-x[3], x[1])):
        if w > 0 or tier in ('ANTI_VALIDATED', 'REJECTED'):
            print(f'   {name[:40]:40} {str(scope or "-"):5} w={w:.3f} '
                  f'{tier:15} n={n}')
            print(f'       {note[:104]}')
    n_prior = sum(1 for p in plan if p[4] == 'UNVALIDATED')
    print(f'   ... plus {n_prior} unmeasured at the {PRIOR} prior')
    if not APPLY:
        print('\n(dry run -- pass --apply to write)')
        return 0

    stamp = datetime.now(timezone.utc).isoformat()
    ok = fail = 0
    print()
    for r, name, scope, w, tier, note, n, hit, base in plan:
        body = {
            'recommended_weight': w,
            'tier': tier,
            'notes': f'[baseline 2026-11-03] {note}',
            'last_computed_at': stamp,
        }
        if n:
            body['sample_n'] = n
        if hit is not None:
            body['hit_rate'] = round(hit / 100.0, 4)
        if base is not None:
            body['baseline_pct'] = round(base, 2)
            if hit is not None:
                body['edge_pp'] = round(hit - base, 2)
        pr = requests.patch(f'{SB}/rest/v1/signal_registry', headers=H_W,
                            timeout=30,
                            params={'id': f"eq.{r['id']}"},
                            data=json.dumps(body))
        if pr.status_code not in (200, 204):
            print(f'  !! {name} {pr.status_code}: {pr.text[:120]}')
            fail += 1
            continue
        rb = requests.get(f'{SB}/rest/v1/signal_registry', headers=H, timeout=30,
                          params={'select': 'recommended_weight,tier',
                                  'id': f"eq.{r['id']}"})
        got = (rb.json() or [{}])[0]
        if (got.get('recommended_weight') is None
                or abs(float(got['recommended_weight']) - w) > 1e-6
                or str(got.get('tier')) != tier):
            print(f'  !! {name} NOT CONFIRMED: {got}')
            fail += 1
        else:
            ok += 1
    print(f'  set and confirmed: {ok}   failed: {fail}')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
