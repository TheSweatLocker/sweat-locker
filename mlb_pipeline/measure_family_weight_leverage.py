"""Can a FAMILY-level weight refit change what the engine picks? Measured: barely.

══ 2026-10-30 · WHY THE REFIT WAS NOT BUILT ══════════════════════════════
ANDY: "flip then refit ... the quicker we have that the quicker we can tune
across app and look at data and possibly strike gold."

Per-SIGNAL weight was already tried and is not the lever: a weighted vote's
winner is invariant to uniform scaling, so `scale` and `cap` were inert and
the fit lost out-of-sample (53.55% vs the live weights' 55.92%). The next
candidate was a weight per FAMILY -- money flow, model consensus, ATS
situational, team-stat mismatch, external handicappers -- on the theory that
the RELATIVE weight between two families does move the argmax when they
disagree.

That theory has a precondition nobody had measured: the families have to
disagree. This script measures it before any fitter gets written, because
fitting a knob that cannot move the output is how the last attempt was
wasted.

THE RESULT. On 1,076 (game, market) units with a recoverable outcome:

    all families AGREE on the side   931   86.5%
    families DISAGREE                145   13.5%

    disagreement rate by sport
      NFL     48/113   42.5%
      NCAAF   96/619   15.5%
      NHL      1/ 86    1.2%
      MLB      0/202    0.0%     <- never
      NBA      0/ 56    0.0%     <- never

A family weight can only change the pick in the 13.5%. On MLB and NBA it
cannot change a single one. So the refit is inert on the two sports with the
most data and the one real edge.

And in the units where it COULD act, there is nothing to tune:

    disagreement units only      majority-family side   blanket HOME
      NCAAF  n=96                       51.0%              52.1%
      NFL    n=48                       58.3%              37.5%

NCAAF's majority vote is a coin flip and no better than "always home". NFL's
looks informative, but n=48 puts the 95% CI near [44%, 72%] -- it does not
clear breakeven, and feedback_sample_size_with_pct gates at n>=30 for a
reason. Following the MINORITY family is worse still: 62/145 = 42.8%.

WHAT THIS MEANS -- THE BINDING CONSTRAINT IS NOT THE WEIGHTS
2,755 of 3,584 attributed signal sides are HOME. The library almost always
says one thing, so the vote has nothing to decide and no weighting can make
it decide differently. That is the same finding as
project_engine_selects_on_zero_measured_signals_1028 (the favourite lock is
firing skew, not a one-sided library) seen from the output end, and as
project_favourite_bias_was_the_market_chooser_1029.

The lever is EVIDENCE THAT CAN ARGUE THE OTHER SIDE, not a coefficient on
evidence that cannot. The measured candidates already exist in our own work
and they are all MARKET structure rather than team data:
  * fade heavy public money on SPREADS  +12.4% n=107, holds both halves
  * the short football spread flip       |line| <= 10.5, 62.0% n=121
  * wind >= 12 -> UNDER                  55.8%, 17 of 26 seasons
  * project_dog_selection_needs_market_data_1030: of every family tested,
    market structure is the only one that clears -- team data RANKS upsets
    but loses exactly where it disagrees with the price.

Until a market-structure family carries real weight, a family refit is a
coefficient on a unanimous vote.

RE-RUN THIS BEFORE BUILDING ANY WEIGHT FITTER. If the disagreement rate has
risen -- because a fade/market family was added and is firing -- the
precondition may now hold and the fitter becomes worth writing.

USAGE
  python measure_family_weight_leverage.py
  python measure_family_weight_leverage.py --min-n 30
"""
from __future__ import annotations

import argparse
import collections
import math
import os
import sys
from pathlib import Path

import requests

import signal_class_map as M

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

#: Two-sided markets only. A side with no stated opposite cannot have its
#: winner inferred when every signal was wrong.
OPPOSITE = {'HOME': 'AWAY', 'AWAY': 'HOME', 'OVER': 'UNDER', 'UNDER': 'OVER'}


def page(table: str, params: dict):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:150]} '
                  f'-- CANNOT VERIFY')
            return None
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def _ci95(w: int, n: int):
    """Wilson-free normal approx, good enough to show a CI does not clear."""
    if not n:
        return (0.0, 0.0)
    p = w / n
    h = 1.96 * math.sqrt(max(p * (1 - p), 1e-9) / n)
    return (100.0 * max(0.0, p - h), 100.0 * min(1.0, p + h))


def build_units():
    """(game_id, market, sport) -> [(family, side, was_right), ...]."""
    rows = page('signal_attribution',
                {'select': 'game_id,sport,game_date,pick_market,signal_key,'
                           'signal_side,signal_was_right',
                 'signal_was_right': 'not.is.null'})
    src = page('signal_sources', {'select': 'signal_key,class'})
    if rows is None or src is None:
        return None, 0
    reg = {s['signal_key']: s.get('class') for s in src}
    units = collections.defaultdict(list)
    for r in rows:
        if not r.get('signal_side'):
            continue
        base, _ = M.strip_fade(r.get('signal_key'))
        fam, _is_fade = M.resolve(r.get('signal_key'), r.get('sport'),
                                  reg.get(base))
        units[(r['game_id'], str(r.get('pick_market')),
               str(r.get('sport')).upper())].append(
            (fam, str(r['signal_side']).upper(), r['signal_was_right']))
    return units, len(rows)


def winner_of(v) -> str | None:
    """The side that actually won, read off the signals' own outcomes.

    A signal marked right means ITS side won, so one such row names the
    winner. Where every signal was wrong and they all took the same side,
    the winner is the opposite side. Mixed-wrong units with two sides and no
    winner are unrecoverable and skipped rather than guessed.

    Never derived from `result` -- that is the PICK's outcome, stamped
    identically on every signal row, and it punishes a signal for
    DISAGREEING (project_signal_hit_rate_was_our_pick_rate_1028).
    """
    right = [s for _, s, w in v if w is True]
    if right:
        return right[0]
    sides = {s for _, s, _ in v}
    if len(sides) == 1:
        return OPPOSITE.get(next(iter(sides)))
    return None


def run(min_n: int) -> int:
    units, n_rows = build_units()
    if units is None:
        return 2
    agree = disagree = recoverable = 0
    by_sport = collections.Counter()
    dis_by_sport = collections.Counter()
    side_counts = collections.Counter()
    minority_w = minority_n = 0
    # disagreement-only accuracy: majority family vs the blanket control
    duel = collections.defaultdict(lambda: [0, 0, 0])   # maj_w, n, home_w

    for (_gid, _mkt, sport), v in units.items():
        for _f, s, _w in v:
            side_counts[s] += 1
        win = winner_of(v)
        if win is None:
            continue
        recoverable += 1
        by_sport[sport] += 1
        fams = collections.defaultdict(set)
        for f, s, _w in v:
            fams[f].add(s)
        # a family that is internally split casts no vote
        votes = [next(iter(ss)) for ss in fams.values() if len(ss) == 1]
        if len({s for s in votes}) < 2:
            agree += 1
            continue
        disagree += 1
        dis_by_sport[sport] += 1
        cnt = collections.Counter(votes)
        maj = cnt.most_common(1)[0][0]
        mino = min(cnt, key=lambda s: cnt[s])
        minority_n += 1
        if mino == win:
            minority_w += 1
        d = duel[sport]
        d[1] += 1
        if maj == win:
            d[0] += 1
        if win == 'HOME':
            d[2] += 1

    print(f'=== family-weight leverage · {n_rows} attributed rows · '
          f'{len(units)} units ===')
    print(f'recoverable outcome on {recoverable} unit(s)\n')
    print(f'  all families AGREE on the side   {agree:5d}  '
          f'{100.0 * agree / recoverable:5.1f}%')
    print(f'  families DISAGREE                {disagree:5d}  '
          f'{100.0 * disagree / recoverable:5.1f}%'
          f'   <- the only units a weight can move\n')
    print('disagreement rate by sport:')
    for sport, n in by_sport.most_common():
        d = dis_by_sport[sport]
        flag = '   <- never' if d == 0 else ''
        print(f'   {sport:6s} {d:4d}/{n:4d} = {100.0 * d / n:5.1f}%{flag}')

    print(f'\nsignal sides across every row: '
          + ', '.join(f'{s}={n}' for s, n in side_counts.most_common()))
    one = side_counts.most_common(1)
    if one:
        s, n = one[0]
        print(f'  {n} of {sum(side_counts.values())} '
              f'({100.0 * n / sum(side_counts.values()):.1f}%) say {s} -- a '
              f'vote with nothing to decide cannot be tuned')

    if minority_n:
        lo, hi = _ci95(minority_w, minority_n)
        print(f'\nfollowing the MINORITY family: {minority_w}/{minority_n} = '
              f'{100.0 * minority_w / minority_n:.1f}%  CI[{lo:.1f},{hi:.1f}]')

    print(f'\ndisagreement units only (n >= {min_n} shown):')
    print(f"   {'sport':6s} {'n':>4s} {'majority family':>17s} "
          f"{'95% CI':>16s} {'blanket HOME':>14s}")
    verdict_ok = False
    for sport, (mw, mn, hw) in sorted(duel.items(), key=lambda x: -x[1][1]):
        if mn < min_n:
            print(f'   {sport:6s} {mn:4d}   n below the gate -- not quoted')
            continue
        lo, hi = _ci95(mw, mn)
        print(f'   {sport:6s} {mn:4d} {100.0 * mw / mn:16.1f}% '
              f'  [{lo:5.1f},{hi:5.1f}] {100.0 * hw / mn:13.1f}%')
        if lo > 52.38:
            verdict_ok = True

    print('\n--- verdict ---')
    if disagree == 0:
        print('NO leverage at all: families never disagree. A weight refit '
              'cannot change a single pick.')
    elif not verdict_ok:
        print(f'Families disagree on only {100.0 * disagree / recoverable:.1f}% '
              f'of units, and in those the majority vote does not clear '
              f'breakeven with its CI. A family-weight fitter would be a '
              f'coefficient on a near-unanimous vote.')
        print('The lever is evidence that can argue the OTHER side -- market '
              'structure -- not a weight on evidence that cannot. See this '
              "file's docstring for the measured candidates.")
    else:
        print('A family whose majority vote clears breakeven on its own CI '
              'now exists. The precondition holds; a fitter is worth '
              'writing. Walk it forward and keep the blanket control.')
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-n', type=int, default=30,
                    help='n below which a cell is not quoted (default 30)')
    a = ap.parse_args()
    raise SystemExit(run(a.min_n))


if __name__ == '__main__':
    main()
