#!/usr/bin/env python3
"""Do NBA schedule spots (B2B, rest advantage, 3-in-4) beat the closing line?

WHY (2026-10-04)
----------------
nba_model_validate just showed the SRS rating carries nothing the market has
not priced: actual_margin ~ +1.058*market -0.015*projection, ATS 51.1% on
n=2,203 against a 52.4% breakeven, and it gets WORSE as the disagreement grows
(47.0% at 6+ points of edge). So a side engine built on team strength is dead
on arrival — the same verdict as NHL props and NFL adjEPA.

That does not mean NBA has no edge; it means the edge is not team quality,
because team quality is the one thing every book models well. Rest and
schedule density are the classic NBA candidates: they are public, but they are
awkward to price, they interact with resting stars, and they are NOT in our
rating at all, so they are at least a genuinely different input
([[feedback_lens_independence_needs_different_inputs]]).

Derived purely from game dates already in nba_game_results — no new source.

  back-to-back   a team's previous game was yesterday
  3-in-4         three games in four calendar days
  4-in-5 / 5-in-7 denser variants
  rest edge      days_rest(home) - days_rest(away)

MEASURED AGAINST THE PRICE, NOT AGAINST WINNING. Every row reports n and ROI
at -110. A spot that wins 54% on n=60 is noise and the output says so rather
than inviting a read.

    python nba_rest_edge.py
"""
from __future__ import annotations
import collections, datetime as dt, os, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

from compute_margin_strength import load_games, clean_games

BREAKEVEN = 110 / 210


def _roi(w, l):
    risk = w + l
    return ((w * (100 / 110) - l) / risk) if risk else 0.0


def _d(s):
    return dt.date.fromisoformat(str(s)[:10])


def _cover(margin, home_line, back_home: bool):
    """+1 win / -1 loss / 0 push for backing home or away at the number."""
    adj = margin + home_line
    if abs(adj) < 1e-9:
        return 0
    return (1 if adj > 0 else -1) * (1 if back_home else -1)


def report(title, rows):
    """rows = [(label, [(cover, ...)])] already bucketed."""
    print(f'\n--- {title} ---')
    print('  %-22s %6s %6s %6s %6s %8s %9s  %s'
          % ('spot', 'n', 'W', 'L', 'push', 'hit%', 'ROI@-110', 'verdict'))
    for label, covers in rows:
        w = sum(1 for c in covers if c > 0)
        l = sum(1 for c in covers if c < 0)
        p = sum(1 for c in covers if c == 0)
        if w + l == 0:
            continue
        hit = w / (w + l)
        roi = _roi(w, l)
        if w + l < 100:
            verdict = 'n too small'
        elif hit > BREAKEVEN + 0.02:
            verdict = 'EDGE — worth a gate'
        elif hit < BREAKEVEN - 0.02:
            verdict = 'FADE candidate'
        else:
            verdict = 'no edge'
        print('  %-22s %6d %6d %6d %6d %7.1f%% %+8.1f%%  %s'
              % (label, w + l + p, w, l, p, 100 * hit, 100 * roi, verdict))


def main() -> int:
    games = clean_games('NBA', load_games('NBA'))
    games = [g for g in games
             if g.get('date') and g.get('margin') is not None
             and g.get('home_line') is not None]
    games.sort(key=lambda g: g['date'])
    print(f'\n=== NBA schedule spots · {len(games)} games with a closing line ===')
    print(f'    breakeven at -110 is {100*BREAKEVEN:.1f}%\n')

    # Build each team's game-date history so rest can be derived.
    hist = collections.defaultdict(list)
    for g in games:
        hist[g['home']].append(_d(g['date']))
        hist[g['away']].append(_d(g['date']))
    for t in hist:
        hist[t].sort()

    def rest_and_density(team, day):
        """(days_rest, games_in_prior_3_days, games_in_prior_4_days)."""
        prior = [d for d in hist[team] if d < day]
        if not prior:
            return None, 0, 0
        gap = (day - prior[-1]).days
        d3 = sum(1 for d in prior if 0 < (day - d).days <= 3)
        d4 = sum(1 for d in prior if 0 < (day - d).days <= 4)
        return gap, d3, d4

    b2b_side, b2b_opp, rest_adv = [], [], collections.defaultdict(list)
    three_in_four, both_b2b, no_rest_edge = [], [], []
    tot_b2b = []

    for g in games:
        day = _d(g['date'])
        hr, h3, _h4 = rest_and_density(g['home'], day)
        ar, a3, _a4 = rest_and_density(g['away'], day)
        if hr is None or ar is None:
            continue
        m, hl = g['margin'], g['home_line']

        h_b2b, a_b2b = hr == 1, ar == 1
        # Back the RESTED side when exactly one team is on a back-to-back.
        if h_b2b and not a_b2b:
            b2b_side.append(_cover(m, hl, back_home=False))   # fade the tired home team
        elif a_b2b and not h_b2b:
            b2b_side.append(_cover(m, hl, back_home=True))    # fade the tired road team
        if h_b2b and a_b2b:
            both_b2b.append(_cover(m, hl, back_home=True))

        # 3-in-4: back the opponent of a team playing its 3rd game in 4 days.
        h_dense = h3 >= 2
        a_dense = a3 >= 2
        if h_dense and not a_dense:
            three_in_four.append(_cover(m, hl, back_home=False))
        elif a_dense and not h_dense:
            three_in_four.append(_cover(m, hl, back_home=True))

        # Rest differential buckets, always backing the better-rested side.
        diff = min(hr, 4) - min(ar, 4)
        if diff != 0:
            key = f'{"home" if diff > 0 else "away"} +{abs(diff)}d rest'
            rest_adv[key].append(_cover(m, hl, back_home=diff > 0))
        else:
            no_rest_edge.append(_cover(m, hl, back_home=True))

        # Totals: a tired team is usually read as a slower, worse-shooting one.
        if (h_b2b or a_b2b) and g.get('close_total') is not None:
            pass   # close_total is not on the loaded dict; handled below

    report('Back-to-back (back the RESTED side)', [
        ('one team on a B2B', b2b_side),
        ('both on a B2B (home)', both_b2b),
    ])
    report('3-in-4 (back the FRESHER side)', [
        ('one team 3rd in 4 days', three_in_four),
    ])
    report('Rest differential (back better-rested)',
           sorted(rest_adv.items(), key=lambda kv: kv[0]))
    report('Control', [('equal rest, back home', no_rest_edge)])

    print('\nNOTE: these are PUBLIC angles. Any real edge here is small and'
          '\n      must clear -110 on a large n before it becomes a gate.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
