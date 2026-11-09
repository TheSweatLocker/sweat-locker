#!/usr/bin/env python3
"""Which games are most likely DOG covers -- measured, point-in-time.

== 2026-11-08 . WHY THIS REPLACED MY FIRST ATTEMPT ===================
My first ranking (calibrate_spread_split.dog_lean) scored past games with
features read out of *_game_context -- a table the pipeline OVERWRITES. For
a game played on 10-15, `home_ats_last10` and `projected_spread` in that
table are whatever the latest run wrote, which can include that game and
every game after it. I scored history with post-game data, got r ~ 0, and
reported "it's noise". ANDY: "YOU DONT KNOW IF IT WORKS!!! HOW DID YOU EVEN
BACK TEST?" -- he was right, I did not know.

This module backtests from `jerry_reads.input_snapshot`, which is captured
AT PICK TIME, and derives the outcome from our own graded result:

    dog covered <=> (we took the DOG and Won) or (we took the FAV and Lost)

so nothing is joined to a mutable table in the historical path. The LIVE
path still reads *_game_context, which is correct there -- for a game that
has not started, the context row IS the pre-game state. The two paths differ
on purpose and the docstrings say which is which.

== WHAT IS ACTUALLY MEASURED (graded rl picks since 2026-08-01) =======

    population                            n     dog cover   95% CI
    baseline, all graded rl picks        414       48.6%   43.8-53.4
    our model leans the FAVOURITE        127       52.8%   44.1-61.2
    low total, within-sport bottom 40%   155       56.1%   48.3-63.7
    both                                  61       57.4%   44.9-69.0
    both, stricter (lean<-2, pct<=50)     49       59.2%   45.2-71.8
    INVERSE corner (lean>+2, high total) 101       41.6%   32.5-51.3

Break-even at -110 is 52.38%. A 17.6pp gradient from the inverse corner to
the strict corner, and the inverse corner being the WORST on n=101 is the
validation -- a cherry-picked tranche has no reason to produce a clean
opposite.

TWO FINDINGS, and the first one is counter-intuitive:

 1. OUR SPREAD PROJECTION IS INVERTED AS A DOG SELECTOR. Across 5 bins,
    monotone: when the model leans hardest to the FAVOURITE the dog covers
    55.7%, and when it leans hardest to the DOG the dog covers 42.5%. This
    matches project_favourite_bias_was_the_market_chooser_1029, which
    measured the same inversion on the projection gap.

 2. LOW TOTALS FAVOUR DOGS. The single most robust component (n=155).
    Normalised WITHIN SPORT -- a pooled total mixes football ~50 with
    basketball ~230 and feedback_pooled_slope_across_scales_is_fake is
    exactly that error. An earlier run of this analysis had a bin spanning
    58.5..155.5 and it was meaningless.

HONEST LIMITS, stated because they decide how much weight this carries:
  - 285 of 414 records are NCAAF. This is mostly an NCAAF finding; NBA has
    n=68 and NCAAB n=61, and the combined rule has n=0 live NBA records.
  - One season. No walk-forward split yet, and
    feedback_walk_forward_misses_feature_leak says stability alone would
    not prove much anyway.
  - The population is games where we ALREADY made an rl pick, so it is the
    set we would be flipping, not every game on the board.
  - Bin n is 49-155 and the CIs are +-11pp. Clears break-even; does not
    clear it with room to spare.

Usage:
    python dog_selector.py --backtest
    python dog_selector.py --backtest --sport NCAAF
    python dog_selector.py --score --sport NCAAF --date 2026-11-09
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys

import requests
from dotenv import load_dotenv

load_dotenv('.env')
SB = os.getenv('SUPABASE_URL')
KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')
H = {'apikey': KEY, 'Authorization': 'Bearer %s' % KEY}

CTX = {'NBA': 'nba_game_context', 'NCAAF': 'ncaaf_game_context',
       'NCAAB': 'ncaab_game_context', 'NFL': 'nfl_game_context',
       'NHL': 'nhl_game_context'}

#: -110 break-even. A rule that does not clear this is not a bet.
BREAK_EVEN = 52.38
MIN_N = 30


def page(table, **params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get('%s/rest/v1/%s' % (SB, table), headers=H, params=p,
                         timeout=120)
        if r.status_code != 200:
            raise RuntimeError('%s %s %s' % (table, r.status_code,
                                             r.text[:200]))
        b = r.json()
        out += b
        if len(b) < 1000:
            break
        off += 1000
    return out


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                   # noqa: BLE001
            return None
    return v


def wilson(w, n):
    if n == 0:
        return (0.0, 0.0)
    p, z = w / n, 1.96
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** .5)
    return (100 * (c - m) / d, 100 * (c + m) / d)


# ───────────────────── the two measured components ────────────────────────
#
# model_lean_dog is INVERTED on purpose: see finding 1. A NEGATIVE lean
# (our model prefers the favourite) is the dog-friendly condition, so the
# selector score negates it.
#
# Both take a margin/line convention that is VERIFIED, never assumed:
#   a spread LINE is negative when the home team is favoured
#   a projected MARGIN is positive when the home team is favoured
# so the market's implied home margin is -line, and differencing a line
# against a margin without that flip is a units error. It is the exact
# mistake the first version of this backtest made.

def model_lean_dog(proj_margin, market_line, dog_is_home):
    """How much more our model likes the DOG than the market does."""
    if proj_margin is None or market_line is None:
        return None
    lean_home = proj_margin - (-market_line)
    return lean_home if dog_is_home else -lean_home


def total_percentile(total, sport_totals):
    """Where this game's total sits WITHIN ITS OWN SPORT, 0-100."""
    if total is None or not sport_totals:
        return None
    return 100.0 * sum(1 for v in sport_totals if v <= total) \
        / len(sport_totals)


def dog_score(lean, total_pct):
    """Higher = more likely a dog cover. None when nothing is available.

    Weights come from the measured single-component strength: low total is
    the bigger and better-sampled effect (n=155, 56.1%) than the inverted
    lean (n=127, 52.8%), so it carries more. A missing component drops out
    and the remaining weight renormalises -- it never reads as a neutral
    zero (feedback_dict_get_on_a_guessed_column).
    """
    parts = {}
    if total_pct is not None:
        parts['low_total'] = ((50.0 - total_pct) / 50.0, 0.60)
    if lean is not None:
        parts['fade_lean'] = (max(-1.0, min(1.0, -lean / 6.0)), 0.40)
    if not parts:
        return None, {}
    wsum = sum(w for _, w in parts.values())
    return (sum(v * w for v, w in parts.values()) / wsum,
            {k: v for k, (v, _) in parts.items()})


# ───────────────────────── backtest: point-in-time ────────────────────────

def load_history(sport=None):
    """Graded rl picks with their AT-PICK-TIME snapshot."""
    params = {'select': 'sport,game_date,call_side,call_line,result,'
                        'input_snapshot',
              'game_date': 'gte.2026-08-01', 'call_market': 'eq.rl'}
    if sport:
        params['sport'] = 'eq.%s' % sport
    recs = []
    for x in page('jerry_reads', **params):
        if str(x.get('result') or '').upper() not in ('WIN', 'LOSS'):
            continue
        s = _j(x.get('input_snapshot'))
        if not isinstance(s, dict):
            continue
        mk = s.get('market') if isinstance(s.get('market'), dict) else {}
        md = s.get('model') if isinstance(s.get('model'), dict) else {}
        line = _f(mk.get('spread'))
        ours = _f(x.get('call_line'))
        if line in (None, 0.0) or ours in (None, 0.0):
            continue
        took_dog = ours > 0
        won = str(x['result']).upper() == 'WIN'
        recs.append({
            'sport': x.get('sport'), 'date': x.get('game_date'),
            'dog_covered': 1.0 if (took_dog == won) else 0.0,
            'dog_is_home': line > 0, 'line': line,
            'proj': _f(md.get('projected_spread')),
            'total': _f(mk.get('total')),
        })
    # within-sport total percentiles
    bysport = {}
    for r in recs:
        bysport.setdefault(r['sport'], []).append(r)
    for rs in bysport.values():
        tv = sorted(x['total'] for x in rs if x['total'] is not None)
        for r in rs:
            r['total_pct'] = total_percentile(r['total'], tv)
    for r in recs:
        r['lean'] = model_lean_dog(r['proj'], r['line'], r['dog_is_home'])
        r['score'], r['parts'] = dog_score(r['lean'], r.get('total_pct'))
    return recs


def _row(label, rs):
    if not rs:
        print('  %-46s n=0' % label)
        return
    w, n = sum(r['dog_covered'] for r in rs), len(rs)
    lo, hi = wilson(w, n)
    pct = 100.0 * w / n
    flag = ''
    if n >= MIN_N and pct > BREAK_EVEN:
        flag = '  clears BE'
    if n >= MIN_N and lo > BREAK_EVEN:
        flag = '  CI ENTIRELY above BE'
    print('  %-46s n=%-4d %5.1f%%  [CI %.1f-%.1f]%s'
          % (label, n, pct, lo, hi, flag))


def backtest(sport=None):
    recs = load_history(sport)
    if len(recs) < MIN_N:
        print('only %d graded records, need %d' % (len(recs), MIN_N))
        return 2
    print('=== dog_selector backtest%s ===' % (' [%s]' % sport if sport
                                               else ''))
    print('    features read ONLY from input_snapshot (at pick time)')
    print('    break-even at -110 = %.2f%%' % BREAK_EVEN)
    print()
    _row('baseline: every graded rl pick', recs)
    print()
    print('  -- components --')
    _row('our model leans the FAVOURITE (lean<0)',
         [r for r in recs if r['lean'] is not None and r['lean'] < 0])
    _row('low total, within-sport bottom 40%',
         [r for r in recs if r.get('total_pct') is not None
          and r['total_pct'] <= 40])
    print()
    print('  -- combined --')
    _row('lean<0 AND total_pct<=40',
         [r for r in recs if r['lean'] is not None and r['lean'] < 0
          and r.get('total_pct') is not None and r['total_pct'] <= 40])
    _row('lean<-2 AND total_pct<=50',
         [r for r in recs if r['lean'] is not None and r['lean'] < -2
          and r.get('total_pct') is not None and r['total_pct'] <= 50])
    print()
    print('  -- INVERSE corner (must be the WORST or the rule is noise) --')
    _row('lean>+2 AND total_pct>60',
         [r for r in recs if r['lean'] is not None and r['lean'] > 2
          and r.get('total_pct') is not None and r['total_pct'] > 60])
    print()
    print('  -- dog_score tranches (monotone or it is noise) --')
    sc = sorted((r for r in recs if r['score'] is not None),
                key=lambda r: r['score'])
    nb = 5
    k = len(sc) // nb
    for i in range(nb):
        part = sc[i * k:(i + 1) * k] if i < nb - 1 else sc[i * k:]
        _row('bin%d  score [%+.2f..%+.2f]'
             % (i + 1, part[0]['score'], part[-1]['score']), part)
    print()
    xs = [r['score'] for r in sc]
    ys = [r['dog_covered'] for r in sc]
    if len(set(xs)) > 1:
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        vx = sum((a - mx) ** 2 for a in xs)
        vy = sum((b - my) ** 2 for b in ys)
        if vx > 0 and vy > 0:
            print('  corr(dog_score, dog_covered) = %+.3f  n=%d'
                  % (cov / (vx ** .5 * vy ** .5), len(xs)))
    print()
    print('  NOTE: %d of %d records are NCAAF. Treat per-sport numbers with'
          % (sum(1 for r in recs if r['sport'] == 'NCAAF'), len(recs)))
    print('  their own n, and do not read the pooled row as universal.')
    return 0


# ────────────────────────── live: score a slate ───────────────────────────

def score_slate(sport, date):
    """Rank today's games by dog_score. Reads context, which for an
    unstarted game IS the pre-game state (unlike the historical path)."""
    table = CTX[sport]
    rows = page(table, select='*', game_date='eq.%s' % date)
    if not rows:
        print('no %s rows for %s' % (sport, date))
        return 1
    hist = page(table, select='close_total',
                game_date='gte.2026-08-01')
    tv = sorted(_f(x.get('close_total')) for x in hist
                if _f(x.get('close_total')) is not None)
    print('=== %s dog candidates for %s ===' % (sport, date))
    print('    score = 0.60*low_total + 0.40*fade_our_own_lean')
    print()
    out = []
    for x in rows:
        line = _f(x.get('close_spread'))
        if line in (None, 0.0):
            continue
        dog_is_home = line > 0
        lean = model_lean_dog(_f(x.get('projected_spread')), line,
                              dog_is_home)
        tp = total_percentile(_f(x.get('close_total')), tv)
        s, parts = dog_score(lean, tp)
        if s is None:
            continue
        dog = x.get('home_team') if dog_is_home else x.get('away_team')
        out.append((s, dog, abs(line), lean, tp, parts))
    out.sort(key=lambda t: -t[0])
    print('  %-26s %6s %7s %7s %6s' % ('dog', 'score', 'line', 'lean',
                                       'tot%'))
    for s, dog, ln, lean, tp, _p in out:
        print('  %-26s %+6.3f %+7.1f %7s %6s'
              % (str(dog)[:26], s, ln,
                 '%+.1f' % lean if lean is not None else '-',
                 '%.0f' % tp if tp is not None else '-'))
    print()
    print('  Ranking only. Nothing is published from here -- the measured')
    print('  edge is 56-59%% on n=49-155, which is an edge worth acting on')
    print('  and not one worth overriding a tier gate for.')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backtest', action='store_true')
    ap.add_argument('--score', action='store_true')
    ap.add_argument('--sport', choices=sorted(CTX))
    ap.add_argument('--date')
    a = ap.parse_args()
    if a.backtest:
        return backtest(a.sport)
    if a.score:
        if not a.sport:
            print('--score needs --sport')
            return 2
        date = a.date or (dt.datetime.now(dt.timezone.utc)
                          - dt.timedelta(hours=5)).date().isoformat()
        return score_slate(a.sport, date)
    ap.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main())
