"""Learn, per sport and family, whether money flow should be FOLLOWED or FADED.

ANDY 2026-10-12, after the money-flow audit: build the per-sport direction
shadow.

THE FINDING THIS EXISTS TO ACT ON. Money-flow signals invert between sports.
Two independent families measured 2026-10-12 agree:

    1-source split flags      MLB -24.2% ROI n=189  vs  NCAAF +18.5% n=166
    heavy sharp divergence    MLB fade it +6.5%     vs  NCAAF follow it

So any single global treatment of money flow is guaranteed wrong in one of
the two sports, and the engine currently treats it uniformly everywhere.

WHAT THIS WRITES. models/money_flow_direction.json — one verdict per
(sport, family, source-count) cell:

    FOLLOW    the flag side is the better side; emit it unchanged
    FADE      the opposite side graded better; the shadow proposes a flip
    NEUTRAL   not enough evidence either way; never flip

TWO DIFFERENT BARS, DELIBERATELY. This is the subtle part and it is on
purpose:

  * the POINT ESTIMATE generates shadow candidates (fade ROI beats follow
    ROI by >= MIN_EDGE_PP on n >= MIN_N). A shadow that only fires on
    already-proven cells would never fire at all — nothing in the audit
    clears its bootstrap CI yet — and would therefore never accumulate the
    evidence needed to decide. Logging is cheap and changes nothing.
  * the CONFIDENCE INTERVAL governs PROMOTION to live, which is a human
    decision, not this file's. The rule is registered with a deliberately
    high min_sample and lift so nightly auto-promotion cannot flip it live
    on a point estimate.

Put plainly: a loose bar decides what to WATCH, a strict bar decides what to
DO. Conflating those is how a lucky cell ships.

HOW THE FADE IS GRADED. On the SAME games, buying the other side at ITS own
closing price — never the mirror bucket, which is a different set of games,
and never on hit rate, because a fade of a favourite-priced side can win 60%
and still lose money (MLB's public side: 59.2% at -158 against a 61.2%
breakeven).

WRITES: models/money_flow_direction.json only. Touches no pick, no record.

CLI
    python build_money_flow_direction.py
    python build_money_flow_direction.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

OUT = Path(__file__).parent / 'models' / 'money_flow_direction.json'

#: sport -> (results table, close_spread sign for home favourite, ml cols)
SPORTS = {
    'MLB': ('mlb_game_results', -1, ('home_ml_close', 'away_ml_close')),
    'NCAAF': ('ncaaf_game_results', -1, ('close_home_ml', 'close_away_ml')),
    'NFL': ('nfl_game_results', +1, ('close_home_ml', 'close_away_ml')),
    'NHL': ('nhl_game_results', -1, ('close_home_ml', 'close_away_ml')),
}

MIN_N = 40            # below this a cell is NEUTRAL regardless
MIN_EDGE_PP = 3.0     # fade ROI must beat follow ROI by this to be watched


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


def boot_ci(vals, iters=6000, seed=17):
    rng = random.Random(seed)
    n = len(vals)
    out = []
    for _ in range(iters):
        t = 0.0
        for _i in range(n):
            t += vals[rng.randrange(n)]
        out.append(t / n)
    out.sort()
    return out[int(iters * 0.025)] * 100, out[int(iters * 0.975)] * 100


def family_of(cls):
    return (cls.rsplit('_CONFIRMED', 1)[0].rsplit('_LEAN', 1)[0]
            .replace('_TRIPLE', ''))


def srcs_of(cls):
    if '_TRIPLE_CONFIRMED' in cls:
        return 'TRIPLE'
    if '_CONFIRMED' in cls:
        return 'CONFIRMED'
    return 'LEAN'


def collect(sport):
    """One record per gradeable flag: the follow bet and the fade bet."""
    res_t, sign, ml_cols = SPORTS[sport]
    flags = page('line_movement_flags', {
        'select': 'sport,market,side,classification,game_id',
        'sport': f'eq.{sport}'})
    res = page(res_t, {'select': 'game_id,game_date,home_score,away_score,'
                                 'close_spread,close_total,'
                                 + ','.join(ml_cols)})
    by_id = {str(r['game_id']): r for r in res if r.get('game_id')}

    out = []
    for x in flags:
        cls = str(x.get('classification') or '')
        if not cls or cls in ('PATTERN_ONLY', 'NEUTRAL', 'SOURCES_SPLIT'):
            continue
        r = by_id.get(str(x.get('game_id')))
        if not r:
            continue
        hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
        if hs is None or as_ is None:
            continue
        side = str(x.get('side') or '').upper()
        mkt = str(x.get('market') or '').lower()
        # The engine inverts these before emitting, so the "follow" side is
        # the inverted one — grading the raw side would score a bet we never
        # make.
        if cls.startswith('RLM') or cls.startswith('PUBLIC_MOVE'):
            side = {'HOME': 'AWAY', 'AWAY': 'HOME',
                    'OVER': 'UNDER', 'UNDER': 'OVER'}.get(side, side)
        margin = hs - as_
        if mkt in ('rl', 'spread'):
            cs = f(r.get('close_spread'))
            if cs is None or side not in ('HOME', 'AWAY'):
                continue
            cover = margin - (cs * sign)
            if abs(cover) < 1e-9:
                continue
            won = (cover > 0) if side == 'HOME' else (cover < 0)
            odds = opp = -110
        elif mkt == 'ml':
            if side not in ('HOME', 'AWAY'):
                continue
            won = (margin > 0) if side == 'HOME' else (margin < 0)
            odds = r.get(ml_cols[0] if side == 'HOME' else ml_cols[1])
            opp = r.get(ml_cols[1] if side == 'HOME' else ml_cols[0])
        elif mkt == 'total':
            ct = f(r.get('close_total'))
            if ct is None or side not in ('OVER', 'UNDER'):
                continue
            tot = hs + as_
            if abs(tot - ct) < 1e-9:
                continue
            won = (tot > ct) if side == 'OVER' else (tot < ct)
            odds = opp = -110
        else:
            continue
        pf_follow = profit(odds, won)
        pf_fade = profit(opp, not won)
        if pf_follow is None or pf_fade is None:
            continue
        out.append({'family': family_of(cls), 'srcs': srcs_of(cls),
                    'market': mkt, 'follow': pf_follow, 'fade': pf_fade,
                    'won': 1 if won else 0})
    return out


def verdict(rows):
    n = len(rows)
    fo = [r['follow'] for r in rows]
    fa = [r['fade'] for r in rows]
    roi_fo = statistics.fmean(fo) * 100
    roi_fa = statistics.fmean(fa) * 100
    d = {'n': n, 'roi_follow': round(roi_fo, 2), 'roi_fade': round(roi_fa, 2),
         'hit_follow': round(sum(r['won'] for r in rows) / n * 100, 1)}
    if n >= 30:
        lo, hi = boot_ci(fa)
        d['fade_ci'] = [round(lo, 2), round(hi, 2)]
        d['fade_ci_excludes_zero'] = not (lo <= 0 <= hi)
    else:
        d['fade_ci'] = None
        d['fade_ci_excludes_zero'] = False
    if n < MIN_N:
        d['direction'] = 'NEUTRAL'
        d['why'] = f'n={n} below MIN_N={MIN_N}'
    elif roi_fa - roi_fo >= MIN_EDGE_PP and roi_fa > 0:
        d['direction'] = 'FADE'
        d['why'] = (f'fade {roi_fa:+.1f}% beats follow {roi_fo:+.1f}% by '
                    f'{roi_fa - roi_fo:.1f}pp on n={n}')
    else:
        d['direction'] = 'FOLLOW'
        d['why'] = (f'follow {roi_fo:+.1f}% vs fade {roi_fa:+.1f}% on n={n}')
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', dest='dry')
    a = ap.parse_args()

    model = {'built_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
             'min_n': MIN_N, 'min_edge_pp': MIN_EDGE_PP,
             'note': ('Point estimate selects what to SHADOW; the bootstrap '
                      'CI governs promotion to live, which is a human '
                      'decision. FADE here means "worth logging a flip", '
                      'never "flip it live".'),
             'cells': {}}

    print('=== money-flow direction, per sport x family x source count ===')
    print('    fade graded on the SAME games at the other side\'s own price\n')
    for sport in sorted(SPORTS):
        rows = collect(sport)
        print(f'── {sport}: {len(rows)} gradeable flag-bets')
        if not rows:
            continue
        groups = defaultdict(list)
        for r in rows:
            groups[(r['family'], r['srcs'])].append(r)
            groups[(r['family'], 'ANY')].append(r)
            groups[('ANY', r['srcs'])].append(r)
        groups[('ANY', 'ANY')] = rows
        for (fam, sc), sub in sorted(groups.items()):
            v = verdict(sub)
            key = f'{sport}|{fam}|{sc}'
            model['cells'][key] = v
            mark = {'FADE': ' <== shadow a flip', 'FOLLOW': '',
                    'NEUTRAL': ''}[v['direction']]
            ci = ('' if not v['fade_ci'] else
                  f" fadeCI[{v['fade_ci'][0]:+.0f},{v['fade_ci'][1]:+.0f}]")
            print(f"    {fam:<13}{sc:<11}{v['direction']:<8} "
                  f"follow {v['roi_follow']:+6.1f}%  fade {v['roi_fade']:+6.1f}%"
                  f"  n={v['n']:<4}{ci}{mark}")
        print()

    fades = [k for k, v in model['cells'].items() if v['direction'] == 'FADE']
    strong = [k for k, v in model['cells'].items()
              if v['direction'] == 'FADE' and v['fade_ci_excludes_zero']]
    print('  ' + '=' * 68)
    print(f'  {len(model["cells"])} cells · {len(fades)} would shadow a flip '
          f'· {len(strong)} of those also clear their fade CI')
    print('  Only the CI-clearing ones are candidates for a human to promote.')
    if a.dry:
        print('\n  --dry-run: model NOT written')
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(model, indent=2), encoding='utf-8')
    print(f'\n  wrote {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
