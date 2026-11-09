"""Re-run the archived "no edge" verdicts under the three-state standard.

Andy 2026-11-09, on being told 50 of 55 "noise" claims carry no sample
size: *"start there but also we need to rerun if you are bringing it up."*
Fair. A verdict I now know was stated beyond its evidence does not get to
stand just because it is already written down.

WHAT IS RE-RUN
--------------
The projection-gap verdicts, which are the ones that actually killed
ideas. From docs/daily/2026-11-04.md ("What actually backtests"):

    NBA gap: no edge.    Model-favoured ATS side 48.11% n=106
    NCAAF gap:           model likes HOME by >=3 -> 64.86% n=111
                         model likes AWAY by >=3 -> 46.50% n=157
    NCAAF totals gap:    anti-predictive, dropped
    NCAAB sides:         not a scale bug, just no edge

and from memory, the 10-29 finding that the projection gap is INVERTED
(>=3pts 51.2% vs <0pts 78.9%).

WHAT CHANGES vs THE ORIGINAL RUN
--------------------------------
1. Every rate carries n and a Wilson 95% CI. If the CI contains the bar,
   the answer is UNDETERMINED -- not "no edge".
2. Every rate is quoted against a DIRECTION-ONLY CONTROL (blanket home
   ATS in the same sport), because a model that only ever likes the home
   side in a league where home covers 54% has to beat 54%.
3. The projected_spread ORIENTATION is measured per sport, not assumed.
   retier_from_sport_bars._PROJ_HOME_POS only ever proved NCAAF (home
   positive) and NBA (home negative) -- opposite signs in two sports, so
   assuming either one elsewhere is a coin flip. A sport whose
   orientation cannot be established is reported as such and no verdict
   is issued for it.
4. The ctx <-> results join overlap is printed. Zero overlap means the
   verdict is UNAVAILABLE, which is a wiring defect to fix, not evidence
   of no edge (feedback_zero_rows_is_a_wiring_error).

NOT a leak concern: *_game_context.projected_spread does NOT mutate.
Measured 2026-11-09 against the pick-time jerry_reads.input_snapshot on
1,226 past games -- 98.9% byte-identical, 0 sign flips. My claim that it
was overwritten was wrong, and the real bug in the earlier dog backtest
was a units error (a spread LINE differenced against a projected MARGIN).

USAGE
    python recheck_verdicts.py
    python recheck_verdicts.py --sport NCAAF
"""
import argparse
import os
import statistics
import sys

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
SB = os.getenv('SUPABASE_URL')
KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')
HDR = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

MIN_N = 30
BE = 52.38
Z = 1.96

SPORTS = {
    'NBA':   ('nba_game_context',   'nba_game_results',   '2026-10-01'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results', '2026-08-20'),
    'NCAAB': ('ncaab_game_context', 'ncaab_game_results', '2026-10-25'),
    'NHL':   ('nhl_game_context',   'nhl_game_results',   '2026-10-01'),
    'NFL':   ('nfl_game_context',   'nfl_game_results',   '2026-09-01'),
}


def page(table, **params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=HDR, params=p,
                         timeout=180)
        if r.status_code != 200:
            print(f'   !! {table} {r.status_code} {r.text[:160]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def wilson(w, n):
    if not n:
        return 0.0, 100.0
    p = w / n
    d = 1 + Z * Z / n
    c = p + Z * Z / (2 * n)
    m = Z * ((p * (1 - p) / n + Z * Z / (4 * n * n)) ** .5)
    return 100 * (c - m) / d, 100 * (c + m) / d


def corr(xs, ys):
    if len(xs) < 20 or len(set(xs)) < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs)
    vy = sum((b - my) ** 2 for b in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** .5 * vy ** .5)


def state(w, n, bar):
    """Three states. A CI that contains the bar is UNDETERMINED."""
    if n < MIN_N:
        return 'UNDETERMINED', f'n={n} < {MIN_N}'
    lo, hi = wilson(w, n)
    if lo > bar:
        return 'EDGE', f'CI low {lo:.1f} > {bar:.1f}'
    if hi < bar:
        return 'ANTI-EDGE', f'CI high {hi:.1f} < {bar:.1f}'
    return 'UNDETERMINED', f'CI {lo:.1f}-{hi:.1f} contains {bar:.1f}'


def line(label, w, n, bar):
    if not n:
        print(f'   {label:<42} n=0  -- no rows')
        return
    lo, hi = wilson(w, n)
    v, why = state(w, n, bar)
    print(f'   {label:<42} {100.0 * w / n:5.1f}%  n={n:<4} '
          f'CI {lo:5.1f}-{hi:5.1f}  bar {bar:5.1f}  {v:<13} ({why})')


def run_sport(sport, args):
    ctx_t, res_t, since = SPORTS[sport]
    print('=' * 100)
    print(f'{sport}')
    print('=' * 100)
    ctx = page(ctx_t, select='game_id,game_date,projected_spread,close_spread',
               game_date=f'gte.{since}')
    res = page(res_t, select='game_id,home_score,away_score')
    rmap = {}
    for r in res:
        hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
        if hs is None or as_ is None:
            continue
        rmap[str(r.get('game_id'))] = hs - as_

    joined = []
    for c in ctx:
        ps, cs = f(c.get('projected_spread')), f(c.get('close_spread'))
        gid = str(c.get('game_id'))
        if ps is None or cs is None or cs == 0 or gid not in rmap:
            continue
        joined.append({'proj': ps, 'cs': cs, 'margin': rmap[gid]})
    print(f'   ctx rows {len(ctx)}   results {len(rmap)}   '
          f'JOINED {len(joined)}')
    if not joined:
        print('   >> VERDICT UNAVAILABLE: ctx and results share no game_id. '
              'That is a wiring defect to fix, NOT evidence of no edge.')
        print()
        return

    # ---- orientation, MEASURED. Is projected_spread a home MARGIN? ----
    c_margin = corr([j['proj'] for j in joined], [j['margin'] for j in joined])
    c_line = corr([j['proj'] for j in joined], [j['cs'] for j in joined])
    print(f'   corr(projected_spread, actual home margin) = '
          f'{"n/a" if c_margin is None else f"{c_margin:+.3f}"}')
    print(f'   corr(projected_spread, close_spread)       = '
          f'{"n/a" if c_line is None else f"{c_line:+.3f}"}')
    if c_margin is None or abs(c_margin) < 0.15:
        print('   >> ORIENTATION NOT ESTABLISHED (|corr| < 0.15 vs the actual '
              'margin). No verdict issued for this sport.')
        print()
        return
    home_pos = c_margin > 0
    print(f'   >> projected_spread is a home {"MARGIN (positive = home "
          "favoured)" if home_pos else "LINE (negative = home favoured)"}')

    # normalise everything to "model's projected home margin"
    for j in joined:
        j['pm'] = j['proj'] if home_pos else -j['proj']
        # close_spread convention: home line, negative = home favoured
        j['home_cov'] = (j['margin'] + j['cs']) > 0
        j['push'] = abs(j['margin'] + j['cs']) < 1e-9
        # market's implied home margin = -close_spread
        j['gap'] = j['pm'] - (-j['cs'])

    live = [j for j in joined if not j['push']]
    print(f'   non-push n={len(live)}')

    # ---- IS THE MODEL EVEN ABLE TO DISAGREE WITH THE LINE? -------------
    # A fixed handicap (MLB run line, NHL puck line) is +/-1.5 always. If
    # the model's projected margin is smaller than the handicap, the sign
    # of (pm + cs) is set by cs -- the LINE -- and "the model-favoured
    # side" silently becomes "always take the dog". The first run of this
    # file printed NHL as a 62.9% EDGE on exactly that artefact.
    lines_seen = {j['cs'] for j in live}
    med_pm = statistics.median([abs(j['pm']) for j in live]) if live else 0.0
    med_cs = statistics.median([abs(j['cs']) for j in live]) if live else 0.0
    dog_side_picks = sum(1 for j in live
                         if (j['gap'] > 0) == (j['cs'] > 0))
    dog_frac = 100.0 * dog_side_picks / len(live) if live else 0.0
    fixed = len(lines_seen) <= 3
    print(f'   distinct close_spread values: {len(lines_seen)}'
          f'{"  << FIXED HANDICAP" if fixed else ""}   '
          f'median |proj margin| {med_pm:.2f} vs median |line| {med_cs:.2f}')
    print(f'   the model names the DOG side on {dog_frac:.1f}% of games')
    if dog_frac > 95 or (med_cs > 0 and med_pm < 0.5 * med_cs):
        print('   >> THE MODEL IS INERT HERE: it cannot outvote the line, so '
              'any rate below is the LINE\'s, not the model\'s.')

    # ---- controls. Two of them, because one is not enough -------------
    hw = sum(1 for j in live if j['home_cov'])
    hr = hw / len(live) if live else 0.5
    line('CONTROL 1: blanket HOME', hw, len(live), BE)
    aw = len(live) - hw
    line('CONTROL 2: blanket AWAY', aw, len(live), BE)
    # the structural control: always take whichever side the LINE makes the
    # dog. This is what a model with no opinion collapses to.
    dw = sum(1 for j in live
             if (j['home_cov'] if j['cs'] > 0 else not j['home_cov']))
    line('CONTROL 3: ALWAYS the line\'s DOG', dw, len(live), BE)
    # mix-matched: same home/away mix as the model, zero selection skill
    nh = sum(1 for j in live if j['gap'] > 0)
    na = len(live) - nh
    mix = (nh * hr + na * (1 - hr)) / len(live) if live else 0.5
    print(f'   CONTROL 4: same home/away MIX as the model, zero selection '
          f'skill -> {100.0 * mix:.1f}%')
    ctl = max(100.0 * mix, 100.0 * dw / len(live) if live else 0.0)
    bar = max(BE, ctl)
    print(f'   bar for any model claim = max(break-even {BE:.2f}, '
          f'best control {ctl:.1f}) = {bar:.1f}')
    if fixed:
        print('   !! a FIXED HANDICAP is not priced at -110. Its dog side '
              'runs about -190/-210, i.e. break-even 66-68%, so 52.38 is '
              'the WRONG bar and a "winning" rate here can still lose '
              'money (feedback_flat_110_inflates_every_ml_figure).')
    print()

    # ---- the verdict that was archived: model-favoured ATS side ----
    w = sum(1 for j in live if (j['gap'] > 0) == j['home_cov'])
    line('model-favoured ATS side (any gap)', w, len(live), bar)
    # the number that actually matters: what does the MODEL add over the
    # pure line rule it collapses to?
    adds = (100.0 * w / len(live)) - (100.0 * dw / len(live)) if live else 0.0
    print(f'   >> the MODEL adds {adds:+.1f}pp over "always the line\'s dog". '
          f'That is the model\'s contribution; the rest is the line.')

    for lo_g, hi_g, lbl in [(3, None, 'gap >= 3 pts'),
                            (6, None, 'gap >= 6 pts'),
                            (0, 3, 'gap 0-3 pts'),
                            (None, 0, 'gap < 0 (model FADES its own side)')]:
        sel = []
        for j in live:
            g = abs(j['gap'])
            signed = j['gap']
            if lo_g is not None and hi_g is None and g < lo_g:
                continue
            if lo_g is not None and hi_g is not None and not (lo_g <= g < hi_g):
                continue
            if lo_g is None and hi_g == 0 and signed >= 0:
                continue
            sel.append(j)
        w2 = sum(1 for j in sel if (j['gap'] > 0) == j['home_cov'])
        line(lbl, w2, len(sel), bar)

    # split by which side the model likes -- the 11-04 asymmetry claim
    print()
    for want_home, lbl in [(True, 'model likes HOME by >=3'),
                           (False, 'model likes AWAY by >=3')]:
        sel = [j for j in live
               if abs(j['gap']) >= 3 and (j['gap'] > 0) == want_home]
        w2 = sum(1 for j in sel if (j['gap'] > 0) == j['home_cov'])
        line(lbl, w2, len(sel), bar)
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport')
    args = ap.parse_args()
    print('Re-running the archived "no edge" verdicts with n, a Wilson 95% CI,')
    print('a direction-only control, and a measured orientation per sport.')
    print('A CI that contains the bar means UNDETERMINED, never "no edge".')
    print()
    todo = [args.sport.upper()] if args.sport else list(SPORTS)
    for sp in todo:
        if sp not in SPORTS:
            print(f'unknown sport {sp}')
            continue
        run_sport(sp, args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
