#!/usr/bin/env python3
"""Does a team-stat EDGE predict covering the spread? Point-in-time only.

WHY THIS REPLACES stat_vs_cover.py
----------------------------------
Andy: "I want the ability to use data analytics, statistics and probabilities
to study correlation of team stats, their differences and the final outcome,
I shouldn't have to pull teeth every waking hour to do this."

stat_vs_cover.py cannot answer that, and says so in its own docstring: it reads
team_computed_stats, a CURRENT-SEASON SNAPSHOT with one row per (team, stat)
and no history. A team's penalty margin in that table already includes the
games being scored, so every number it produces is "teams that ended the season
good at X also covered more often" — an association, never a bet. That is the
same leak that made SP+ backtests worthless on 09-26 and showed SOR at 79.3%
ATS before a point-in-time refit collapsed it to ~50%.

This reads PER-GAME history instead (ncaaf_team_game_stats, 3 seasons, 64 stat
columns) and for every game computes each team's average using ONLY that
team's EARLIER games in the same season. Nothing from the game being scored,
nothing from the future. So a number here is what we could actually have bet.

THE SHAPE OF THE QUESTION
Andy's: "if a team is a 3.5 point dog but more disciplined penalty wise does it
affect the outcome". That is three things at once, so the output crosses them:

  * which stat, and which direction counts as "better"
  * how big the edge in that stat is
  * whether the better team is the DOG or the FAVOURITE, inside a spread band

Reported with n on every line and a 52.38% breakeven marked, because a 54% hit
rate on n=20 is noise and we have shipped on exactly that mistake before.

WHAT WOULD MAKE A RESULT REAL
A cell here beating breakeven on n>=100 across MULTIPLE seasons is a
hypothesis worth a walk-forward test, not a finding. One season, or n<100, is
a coincidence until it repeats. The engine prints per-season splits for the
top results precisely so a single-season fluke cannot be quoted as an edge.

    python stat_pit_vs_cover.py                          # NCAAF, penalties
    python stat_pit_vs_cover.py --stat off_ppa --dir high
    python stat_pit_vs_cover.py --scan                   # rank every stat
    python stat_pit_vs_cover.py --stat penalties_yards --bands 0-3,3-7,7-14
"""
from __future__ import annotations
import argparse
import collections
import os
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

BREAKEVEN = 110 / 210          # 52.38% at -110
MIN_QUOTE_N = 30               # feedback_sample_size_with_pct


def page(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        chunk = None
        for attempt in range(4):
            try:
                r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=q,
                                 timeout=120)
            except requests.RequestException as e:
                if attempt == 3:
                    raise SystemExit(f'{table} offset={off}: {e}')
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code not in (200, 206):
                raise SystemExit(f'{table} {r.status_code}: {r.text[:300]}')
            chunk = r.json()
            if not isinstance(chunk, list):
                raise SystemExit(f'{table}: {chunk}')
            break
        out += chunk
        if len(chunk) < 1000:
            return out
        off += 1000


# Direction that counts as "better" for a team. Only stats whose polarity is
# unambiguous are listed; --dir overrides, and --scan refuses the rest rather
# than guessing, because a flipped polarity turns a losing finding into a
# winning one and reads perfectly plausible either way.
BETTER_LOW = {
    'penalties_yards', 'turnovers', 'fumbles_lost', 'passes_intercepted',
    'def_ppa', 'def_total_ppa', 'def_success_rate', 'def_explosiveness',
    'def_line_yards', 'def_open_field_yards', 'def_second_level_yards',
    'def_std_downs_ppa', 'def_pass_downs_ppa', 'def_power_success',
    'opp_points',
}
BETTER_HIGH = {
    'off_ppa', 'off_total_ppa', 'off_success_rate', 'off_explosiveness',
    'off_line_yards', 'off_open_field_yards', 'off_second_level_yards',
    'off_std_downs_ppa', 'off_pass_downs_ppa', 'off_power_success',
    'total_yards', 'net_passing_yards', 'rushing_yards', 'first_downs',
    'yards_per_pass', 'yards_per_rush', 'sacks', 'tackles_for_loss',
    'def_stuff_rate', 'third_down_conv', 'fourth_down_conv', 'points',
    'possession_seconds',
}


def roi(w: int, l: int) -> float:
    return ((w * (100 / 110) - l) / (w + l)) if (w + l) else 0.0


def load(sport: str):
    """Per-game team stats + game results, both full history."""
    stats = page('ncaaf_team_game_stats',
                 {'select': '*', 'order': 'game_date.asc'})
    games = page('ncaaf_game_results',
                 {'select': 'game_id,game_date,season,week,home_team,away_team,'
                            'home_score,away_score,close_spread,spread_result',
                  'close_spread': 'not.is.null',
                  'spread_result': 'not.is.null',
                  'order': 'game_date.asc'})
    return stats, games


def build_history(stats: list, stat: str):
    """(team, season) -> list of (game_date, value), ascending.

    Keyed by SEASON as well as team: carrying last year's form into week 1
    would be a different (and much weaker) hypothesis than current-season
    form, and silently mixing the two is how a result becomes unreproducible.
    """
    hist = collections.defaultdict(list)
    for s in stats:
        v = s.get(stat)
        if v is None or s.get('team') is None:
            continue
        d = str(s.get('game_date') or '')[:10]
        if not d:
            continue
        try:
            hist[(s['team'], str(s.get('season')))].append((d, float(v)))
        except (TypeError, ValueError):
            continue
    for k in hist:
        hist[k].sort()
    return hist


def pit_mean(hist, team, season, before_date, min_games):
    """Mean of `team`'s values STRICTLY BEFORE before_date. None if too few.

    The strict `<` is the whole point of this module. With `<=` the game being
    scored contributes its own result to the feature that predicts it, which
    is precisely the leak that made SOR look like a 79% ATS edge.
    """
    rows = hist.get((team, str(season)))
    if not rows:
        return None
    vals = [v for d, v in rows if d < before_date]
    if len(vals) < min_games:
        return None
    return sum(vals) / len(vals)


def parse_bands(spec: str):
    out = []
    for part in spec.split(','):
        lo, hi = part.split('-')
        out.append((float(lo), float(hi)))
    return out


def evaluate(games, hist, stat, better, min_games, bands):
    """One row per game where both teams have enough prior history."""
    rows = []
    for g in games:
        d = str(g.get('game_date') or '')[:10]
        sp, res = g.get('close_spread'), str(g.get('spread_result') or '')
        if not d or sp is None or res not in ('home_covered', 'away_covered',
                                              'push'):
            continue
        hm = pit_mean(hist, g['home_team'], g.get('season'), d, min_games)
        am = pit_mean(hist, g['away_team'], g.get('season'), d, min_games)
        if hm is None or am is None:
            continue
        # Positive edge = HOME is better at this stat.
        edge = (am - hm) if better == 'low' else (hm - am)
        if edge == 0:
            continue
        # close_spread is the HOME handicap in NCAAF (verified n=7,325), so a
        # negative value means home is favoured.
        try:
            spread = float(sp)
        except (TypeError, ValueError):
            continue
        better_side = 'home' if edge > 0 else 'away'
        # Is the better-at-this-stat team the dog or the favourite?
        if spread < 0:          # home favoured
            role = 'dog' if better_side == 'away' else 'fav'
        elif spread > 0:        # away favoured
            role = 'dog' if better_side == 'home' else 'fav'
        else:
            role = 'pk'
        if res == 'push':
            outcome = 'push'
        else:
            outcome = 'win' if res == f'{better_side}_covered' else 'loss'
        band = None
        for lo, hi in bands:
            if lo <= abs(spread) < hi:
                band = f'{lo:g}-{hi:g}'
                break
        rows.append({'season': str(g.get('season')), 'band': band, 'role': role,
                     'edge': abs(edge), 'outcome': outcome,
                     'spread': abs(spread)})
    return rows


def tally(rows):
    w = sum(1 for r in rows if r['outcome'] == 'win')
    l = sum(1 for r in rows if r['outcome'] == 'loss')
    p = sum(1 for r in rows if r['outcome'] == 'push')
    return w, l, p


def line(label, rows, width=34):
    w, l, p = tally(rows)
    n = w + l
    if n == 0:
        return f'  {label:<{width}} n=0'
    hit = w / n
    mark = ''
    if n >= MIN_QUOTE_N:
        mark = '  <<<' if hit > BREAKEVEN else ''
    else:
        mark = '   (n too small to quote)'
    return (f'  {label:<{width}} {w:4d}-{l:<4d}'
            + (f'-{p}' if p else '   ')
            + f'  {hit*100:5.1f}%  ROI {roi(w,l)*100:+6.1f}%  n={n}{mark}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=['NCAAF'],
                    help='NCAAF only — NFL has no per-game team stats table yet')
    ap.add_argument('--stat', default='penalties_yards')
    ap.add_argument('--dir', choices=['low', 'high'],
                    help='which direction is BETTER (inferred for known stats)')
    ap.add_argument('--min-games', type=int, default=3,
                    help='prior games required per team before a game counts')
    ap.add_argument('--bands', default='0-3,3-7,7-14,14-60')
    ap.add_argument('--scan', action='store_true',
                    help='rank every polarity-known stat instead of detailing one')
    args = ap.parse_args()

    bands = parse_bands(args.bands)
    print(f'=== stat_pit_vs_cover · {args.sport} · POINT-IN-TIME ===')
    stats, games = load(args.sport)
    print(f'  per-game stat rows: {len(stats)}   games with a close spread '
          f'and a graded result: {len(games)}')
    seasons = sorted({str(s.get('season')) for s in stats})
    print(f'  seasons in the per-game store: {", ".join(seasons)}')
    print(f'  features use ONLY each team\'s earlier games in the same season '
          f'(min {args.min_games})')
    print(f'  breakeven at -110 is {BREAKEVEN*100:.2f}%\n')

    if args.scan:
        known = sorted(BETTER_LOW | BETTER_HIGH)
        present = [c for c in known if any(s.get(c) is not None for s in stats)]
        print(f'  scanning {len(present)} stats with an unambiguous direction\n')
        out = []
        for c in present:
            better = 'low' if c in BETTER_LOW else 'high'
            rows = evaluate(games, build_history(stats, c), c,
                            better, args.min_games, bands)
            w, l, _ = tally(rows)
            if w + l >= MIN_QUOTE_N:
                out.append((roi(w, l), w / (w + l), w + l, c, better))
        out.sort(reverse=True)
        print(f'  {"stat":<26} {"better":<7} {"hit":>7} {"ROI":>9} {"n":>7}')
        for r, hit, n, c, better in out:
            flag = '  <<< beats breakeven' if hit > BREAKEVEN else ''
            print(f'  {c:<26} {better:<7} {hit*100:6.1f}% {r*100:+8.1f}% '
                  f'{n:7d}{flag}')
        print('\n  Backing the team with the better season-to-date figure, '
              'every game, no band filter.')
        print('  A line here is a HYPOTHESIS. Nothing is an edge until it '
              'repeats across seasons.')
        return 0

    stat = args.stat
    better = args.dir or ('low' if stat in BETTER_LOW else
                          'high' if stat in BETTER_HIGH else None)
    if better is None:
        print(f'  ⛔ polarity of "{stat}" is not known — pass --dir low|high.')
        print('     Refusing to guess: a flipped polarity turns a losing '
              'finding into a winning one and reads plausible either way.')
        return 2
    if not any(s.get(stat) is not None for s in stats):
        print(f'  ⛔ no non-null values for "{stat}" in the per-game store.')
        return 2

    hist = build_history(stats, stat)
    rows = evaluate(games, hist, stat, better, args.min_games, bands)
    print(f'  stat: {stat}   better = {better.upper()}er   '
          f'usable games: {len(rows)}')
    print(f'  (a game counts only if BOTH teams have >= {args.min_games} '
          f'earlier games this season)\n')

    print('  ── backing the team with the better figure, by spread band ──')
    for lo, hi in bands:
        b = f'{lo:g}-{hi:g}'
        print(line(f'spread {b}', [r for r in rows if r['band'] == b]))
    print(line('ALL games', rows))

    print('\n  ── Andy\'s question: is the better team a DOG or a FAVOURITE? ──')
    for role, label in (('dog', 'better team is the DOG'),
                        ('fav', 'better team is the FAVOURITE')):
        sub = [r for r in rows if r['role'] == role]
        print(line(label, sub))
        for lo, hi in bands:
            b = f'{lo:g}-{hi:g}'
            print(line(f'    spread {b}',
                       [r for r in sub if r['band'] == b], width=30))

    print('\n  ── does a BIGGER stat edge help? (quartiles of |edge|) ──')
    ev = sorted(r['edge'] for r in rows)
    if len(ev) >= 8:
        qs = [ev[int(len(ev) * f)] for f in (0.25, 0.5, 0.75)]
        buckets = [('Q1 smallest', lambda e: e < qs[0]),
                   ('Q2', lambda e: qs[0] <= e < qs[1]),
                   ('Q3', lambda e: qs[1] <= e < qs[2]),
                   ('Q4 largest', lambda e: e >= qs[2])]
        print(f'     quartile cuts at |edge| = '
              f'{qs[0]:.2f} / {qs[1]:.2f} / {qs[2]:.2f}')
        for lab, fn in buckets:
            print(line(lab, [r for r in rows if fn(r['edge'])]))

    print('\n  ── per season (a one-season result is a coincidence) ──')
    for s in sorted({r['season'] for r in rows}):
        print(line(f'season {s}', [r for r in rows if r['season'] == s]))

    print('\n  Point-in-time, so these are bettable-equivalent numbers, not '
          'associations.')
    print('  Still: nothing goes near the engine until it repeats across '
          'seasons at n>=100.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
