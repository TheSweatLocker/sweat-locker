"""Why the engine almost never picks a road team — NCAAF/NFL post-mortem.

ANDY 2026-10-10, on Iowa @ Washington: "i looked at the ioaw game played
aginst U of Wash and based on wta i saw i felt iowa spread was the move and it
was the move, how could we have not be Wash ML and engine would of went woth
iowa i want to walk that bakc and reverse engineer".

THE CASE, reconstructed from the frozen decision trail
Iowa was AP #20; Washington was UNRANKED. Iowa was better on every rating we
own: SP+ 14.8 (#19) vs 12.1 (#29), SOR 4.37 (#48) vs 2.88 (#64), points
allowed 12.6 (#9) vs 18.4 (#43), and a tougher schedule (SOS #66 vs #106).
Iowa was 3-2 ATS and 7-3 ATS on the road; Washington 1-4 and 4-6 at home.

The engine produced Washington on ALL THREE markets — ml STRONG/74, rl
LEAN/50, total UNDER/59 — so there was never an Iowa read to choose between.
And exactly three signals fired, none of them stat-based:

    external:dimers                w=0.79 hit=.804 n=179  contrib 0.39
    ncaaf_ret_prod_mismatch_home   w=0.72 hit=.718 n=71   contrib 0.36
    ncaaf_home_field_baseline      w=0.98 hit=.746 n=374  contrib 0.15

They sum to 0.90, the whole score. No SP+, SOR, EPA or points-allowed signal
fired — not overridden, SILENT, because sp_gap was only -2.7 and
projected_spread 0.26, both under the thresholds that trip a stat signal.

WHY IOWA'S CASE COULD NOT BE EXPRESSED — the structural finding
Of 110 NCAAF signals in the registry:
    AP rank          0 signals
    SOS / schedule   0 signals
    SOR              0 signals
Three of the four facts that most favoured Iowa have NO signal at all. The
fourth, its road ATS form, maps to away_ats_hot_on_road — which exists at hit
42.5% on n=106 and weight 0.0, zeroed out as non-predictive.

AND THE FIRING ASYMMETRY IS 11 TO 1. Across 409 games carrying a decision
trail, directional signal firings ran HOME 1,240 (91.4%) against AWAY 116
(8.6%). Part of that is by construction: several of the most-fired signals are
__fade variants that convert an away-team advantage into a home-side vote —
ncaaf_ol_weight_adv_away__fade (106 games), ncaaf_ground_leverage_away__fade
(77), away_ats_hot_on_road__fade (49). Weight totals tell the same story:
home-directed 19.37 across 38 signals, away/road-directed 9.40 across 29.

BUT HERE IS THE PART THAT INVERTS THE OBVIOUS CONCLUSION, and it is why this
script exists rather than a patch. The home lean is EARNED:

    NCAAF HOME picks   323 (71.8%)   ATS 128-104   55.2%
    NCAAF AWAY picks   127 (28.2%)   ATS  43-50    46.2%
    market baseline: home covers 50.48% on 6,329 games

Home picks beat the baseline by ~4.7pp; away picks fall ~3.3pp BELOW it. So
the engine is right to distrust its own road reads — and "make it pick away
more often" would make things worse, not better. Andy's Iowa read was correct,
and the policy that talked the engine out of it is aggregate-correct.

THE REAL DEFECT IS NARROWER AND MORE FIXABLE: the away path is starved of the
best quality inputs. When the engine does pick a road team it is doing so on
whatever weak signal happened to fire, never on AP rank, strength of schedule
or strength of record — because no such signal exists. 46.2% is what picking
away for weak reasons looks like. The test worth running is whether an
away-capable quality signal lifts that number, NOT whether to force more away
picks.

Note SOR is the one to be careful with: it measured NULL as a predictor on
11,856 games (project_money_flow_one_feed_artefact_1009), so adding it as a
signal would be adding a measured non-predictor. AP rank and SOS have never
been tested either way — those are the honest candidates.

WRITES NOTHING.

CLI
    python audit_home_away_signal_bias.py
    python audit_home_away_signal_bias.py --sport NFL
    python audit_home_away_signal_bias.py --game ncaaf_20261009_Iowa_Washington
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
CTX = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context'}
RES = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}
#: Facts a road team can be better at which the registry may not express.
QUALITY_PROBES = {
    'AP rank': ('ap_rank', 'ap_', 'poll'),
    'SOS / schedule': ('sos', 'schedule', 'strength_of_sched'),
    'SOR': ('sor',),
    'SP+': ('sp_plus', 'sp_overall'),
    'defense / pts allowed': ('def_', 'allow', 'points_allowed'),
    'ATS form': ('ats',),
}


def _page(t, p, cap=400000):
    out, off = [], 0
    while off < cap:
        q = dict(p); q['limit'] = '1000'; q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:140]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def one_game(sport, gid):
    rows = _page(CTX[sport], {'select': 'game_id,away_team,home_team,'
                                        'close_spread,sp_gap,projected_spread,'
                                        'home_ap_rank,away_ap_rank,'
                                        'primary_play',
                              'game_id': f'eq.{gid}'})
    if not rows:
        print(f'  no context row for {gid}')
        return
    x = rows[0]
    pp = x.get('primary_play') or {}
    print(f'\n=== {x["away_team"]} @ {x["home_team"]}  ({gid})')
    print(f'    close_spread={x.get("close_spread")}  sp_gap={x.get("sp_gap")}'
          f'  projected={x.get("projected_spread")}')
    print(f'    AP: home={x.get("home_ap_rank")} away={x.get("away_ap_rank")}')
    print(f'\n    ALL MARKET READS: '
          f'{json.dumps(pp.get("_ensemble_all_markets") or {})}')
    print(f'\n    SIGNALS THAT FIRED:')
    tot = 0.0
    for s in (pp.get('_ensemble_sources') or []):
        c = _f(s.get('contribution')) or 0.0
        tot += c
        print(f'      {str(s.get("signal_key"))[:34]:<36}'
              f'side={str(s.get("side")):<9} w={s.get("weight")} '
              f'hit={s.get("hit_rate")} n={s.get("n")} contrib={c}')
        print(f'           {str(s.get("prose"))[:88]}')
    print(f'      {"":<36}total contribution = {tot:.2f}  '
          f'(score={pp.get("score")})')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--game', default=None)
    a = ap.parse_args()
    sport = a.sport
    if a.game:
        one_game(sport, a.game)
        return 0

    # ── 1. WHICH QUALITY FACTS HAVE A SIGNAL AT ALL ──────────────────────
    reg = [x for x in _page('signal_registry',
                            {'select': 'sport,signal_name,market_scope,'
                                       'hit_rate,sample_n,'
                                       'recommended_weight'})
           if str(x.get('sport')) == sport]
    print(f'=== {sport} · {len(reg)} signals in the registry')
    print('\n  CAN THE ENGINE EXPRESS THESE FACTS?')
    for label, frags in QUALITY_PROBES.items():
        hits = [x for x in reg
                if any(f in str(x['signal_name']).lower() for f in frags)]
        live = [x for x in hits if (_f(x['recommended_weight']) or 0) > 0]
        mark = '  <-- NO SIGNAL EXISTS' if not hits else ''
        print(f'    {label:<24}{len(hits):>3} signals, {len(live):>3} live'
              f'{mark}')

    # ── 2. HOME/AWAY WEIGHT AND FIRING ASYMMETRY ─────────────────────────
    def bucket(n):
        n = n.lower()
        if 'home' in n:
            return 'HOME'
        if 'away' in n or 'road' in n:
            return 'AWAY'
        return 'neutral'
    print('\n  REGISTRY WEIGHT BY DIRECTION:')
    for b in ('HOME', 'AWAY', 'neutral'):
        s = [x for x in reg if bucket(str(x['signal_name'])) == b]
        ws = [_f(x['recommended_weight']) or 0 for x in s]
        live = sum(1 for w in ws if w > 0)
        print(f'    {b:<9}{len(s):>4} signals · {live:>3} live · '
              f'total weight {sum(ws):6.2f} · mean '
              f'{statistics.fmean(ws) if ws else 0:.3f}')

    ctx = _page(CTX[sport], {'select': 'game_id,away_team,home_team,'
                                       'primary_play'})
    firings = collections.Counter()
    most = collections.Counter()
    trails = 0
    for x in ctx:
        pp = x.get('primary_play')
        if not isinstance(pp, dict):
            continue
        srcs = pp.get('_ensemble_sources') or []
        if not srcs:
            continue
        trails += 1
        for s in srcs:
            sd = str(s.get('side') or '')
            firings['HOME' if 'HOME' in sd else
                    'AWAY' if 'AWAY' in sd else sd] += 1
            most[str(s.get('signal_key'))] += 1
    dirtot = firings['HOME'] + firings['AWAY']
    print(f'\n  SIGNAL FIRINGS across {trails} games with a decision trail:')
    if dirtot:
        print(f'    HOME {firings["HOME"]} ({100 * firings["HOME"] / dirtot:.1f}%)'
              f'   AWAY {firings["AWAY"]} '
              f'({100 * firings["AWAY"] / dirtot:.1f}%)')
    print('    most-fired:')
    for k, n in most.most_common(6):
        fade = '  <-- __fade turns an AWAY edge into a HOME vote' \
            if k.endswith('__fade') and 'away' in k.lower() else ''
        print(f'      {k[:44]:<46}{n:>5} ({100 * n / max(1, trails):4.1f}%)'
              f'{fade}')

    # ── 3. DOES THE LEAN PAY? ────────────────────────────────────────────
    res = {str(x['game_id']): x for x in
           _page(RES[sport], {'select': 'game_id,spread_result'})}
    picks = collections.Counter()
    perf = collections.defaultdict(lambda: [0, 0])
    for x in ctx:
        pp = x.get('primary_play')
        if not isinstance(pp, dict):
            continue
        side = str(pp.get('side') or '')
        if side not in ('HOME', 'AWAY'):
            continue
        picks[side] += 1
        g = res.get(str(x['game_id']))
        sr = str((g or {}).get('spread_result') or '').lower()
        if sr not in ('home_covered', 'away_covered'):
            continue
        won = (sr == 'home_covered') == (side == 'HOME')
        perf[side][0 if won else 1] += 1
    tot = picks['HOME'] + picks['AWAY']
    print(f'\n  PICK SIDE and ATS PERFORMANCE:')
    for side in ('HOME', 'AWAY'):
        w, l = perf[side]
        rate = f'{100 * w / (w + l):.1f}%' if w + l else 'n/a'
        print(f'    {side:<6}{picks[side]:>4} picks '
              f'({100 * picks[side] / max(1, tot):5.1f}%) · '
              f'ATS {w}-{l} ({rate})')
    print('\n' + '=' * 70)
    print('  READ THIS BEFORE "MAKE IT PICK AWAY MORE". The home lean is')
    print('  EARNED — home picks beat the 50.48% market baseline while away')
    print('  picks fall below it. Forcing more away picks would make things')
    print('  worse. The defect is that the away path has no quality input to')
    print('  reason with: AP rank and SOS have NO signal at all, so a road')
    print('  pick is made on whatever weak signal fired. Test whether an')
    print('  away-capable quality signal lifts that rate — do not force volume.')
    print('  And note SOR measured NULL as a predictor on 11,856 games, so it')
    print('  is NOT one of the honest candidates.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
