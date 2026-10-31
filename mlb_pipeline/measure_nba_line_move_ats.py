"""Does the side an NBA line MOVED TOWARD cover? Measured, not assumed.

== 2026-10-31 . THE SPLITS FEED IS DARK, SO MEASURE WHAT IS NOT ===========
Andy wants NBA plays tonight on the same read that went 5-0. That read is
the public fade, and it needs money/ticket share, which OddsCrowd has not
published for tonight's games. Rather than deflect or substitute an
unmeasured proxy, this backtests a rule built only from columns that ARE
populated: `open_spread` and `close_spread` on nba_game_context, graded
against `spread_result` on nba_game_results.

THE TWO COMPETING STORIES, and why this has to be measured rather than
reasoned about:
  * line moves toward a side  => public money arrived there => FADE it
  * line moves toward a side  => sharp money arrived there => FOLLOW it
Both are plausible and they are opposite bets. The data decides.

HONESTY REQUIREMENTS BUILT IN:
  * a DIRECTION-ONLY CONTROL. "Take the home team" and "take the favourite"
    are reported on the same rows, because a rule that cannot beat a
    one-word rule is not a rule
    (feedback_signal_needs_a_direction_control, and the engine already
    loses to "take away" on NFL spreads by 46.99pp).
  * SPLIT-HALF by date. A rule that flips sign between halves is noise, and
    this codebase has shipped two such findings before catching them.
  * every % carries its n, and nothing under n=30 is quoted as a rate
    (feedback_sample_size_with_pct).
  * breakeven is 52.38% because these are spreads at about -110. ML
    breakeven would be 56-58% and is not used here.

SIGN CONVENTION, stated because it is the whole risk. NBA close_spread is
HOME-perspective with NEGATIVE meaning the home team is favoured
(nba_odds_pull's own docstring, and verified on this slate: Minnesota @ San
Antonio reads -12.5 with San Antonio at -591). So:
    close_spread - open_spread < 0  ->  the line moved TOWARD the HOME side
    close_spread - open_spread > 0  ->  the line moved TOWARD the AWAY side
`spread_result` is 'home_covered' / 'away_covered' / 'push'.

USAGE
  python measure_nba_line_move_ats.py
  python measure_nba_line_move_ats.py --sport NHL
"""
from __future__ import annotations

import argparse
import collections
import math
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
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
BREAKEVEN = 52.38
MIN_N = 30

TABLES = {
    'NBA': ('nba_game_context', 'nba_game_results'),
    'NHL': ('nhl_game_context', 'nhl_game_results'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results'),
}


def page(table, params):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:160]}')
            return None
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ci95(w, n):
    if not n:
        return (0.0, 0.0)
    p = w / n
    h = 1.96 * math.sqrt(max(p * (1 - p), 1e-9) / n)
    return (100.0 * max(0.0, p - h), 100.0 * min(1.0, p + h))


def report(label, rows, pick_fn):
    """rows: list of (date, winner, meta). pick_fn -> 'HOME'/'AWAY'/None."""
    w = n = 0
    for _, winner, meta in rows:
        side = pick_fn(meta)
        if side is None or winner == 'push':
            continue
        n += 1
        if (side == 'HOME') == (winner == 'home_covered'):
            w += 1
    if n < MIN_N:
        print(f'    {label:44} n={n:<5} -- under n={MIN_N}, not quoted')
        return
    lo, hi = ci95(w, n)
    verdict = ''
    if hi < BREAKEVEN:
        verdict = 'NEGATIVE, CI clears below breakeven'
    elif lo > BREAKEVEN:
        verdict = 'POSITIVE, CI clears above breakeven'
    print(f'    {label:44} {w}-{n - w}  {100.0 * w / n:5.1f}%  '
          f'CI[{lo:4.1f},{hi:4.1f}]  n={n:<5} {verdict}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NBA')
    ap.add_argument('--min-move', type=float, default=1.0)
    a = ap.parse_args()
    ctx_t, res_t = TABLES[a.sport.upper()]

    ctx = page(ctx_t, {'select': 'game_id,game_date,open_spread,close_spread'})
    res = page(res_t, {'select': 'game_id,spread_result,close_spread'})
    if ctx is None or res is None:
        print('CANNOT VERIFY -- treating as a failure')
        return 2
    rmap = {str(x['game_id']): x for x in res}

    rows = []
    for x in ctx:
        g = str(x.get('game_id'))
        r = rmap.get(g)
        if not r:
            continue
        winner = str(r.get('spread_result') or '').lower()
        if winner not in ('home_covered', 'away_covered', 'push'):
            continue
        o, c = f(x.get('open_spread')), f(x.get('close_spread'))
        if o is None or c is None:
            continue
        cs = f(r.get('close_spread'))
        rows.append((str(x.get('game_date')), winner,
                     {'move': round(c - o, 2), 'close': c, 'res_close': cs}))
    print(f'=== {a.sport}: {len(rows)} games with open, close and a graded '
          f'spread result ===')
    if not rows:
        print('  nothing to measure')
        return 1
    moved = [r for r in rows if abs(r[2]['move']) >= a.min_move]
    print(f'  of those, {len(moved)} moved at least {a.min_move:g} point(s)')
    dist = collections.Counter(
        ('toward HOME' if r[2]['move'] < 0 else
         'toward AWAY' if r[2]['move'] > 0 else 'flat') for r in rows)
    print(f'  movement direction: {dict(dist)}')
    print(f'  breakeven {BREAKEVEN}% (spreads at ~-110); every cut shows its n')

    print('\n  -- DIRECTION-ONLY CONTROLS, on the same rows --')
    report('always HOME', rows, lambda m: 'HOME')
    report('always the FAVOURITE (close < 0 = home fav)', rows,
           lambda m: 'HOME' if (m['close'] or 0) < 0 else 'AWAY')

    print(f'\n  -- THE RULE, both directions, |move| >= {a.min_move:g} --')
    report('FOLLOW the side the line moved toward', moved,
           lambda m: 'HOME' if m['move'] < 0 else 'AWAY')
    report('FADE the side the line moved toward', moved,
           lambda m: 'AWAY' if m['move'] < 0 else 'HOME')

    print('\n  -- dose response on |move| --')
    for lo_, hi_ in ((1.0, 1.5), (1.5, 2.5), (2.5, 4.0), (4.0, 99.0)):
        band = [r for r in rows if lo_ <= abs(r[2]['move']) < hi_]
        report(f'FADE, |move| in [{lo_:g},{hi_:g})', band,
               lambda m: 'AWAY' if m['move'] < 0 else 'HOME')

    print('\n  -- SPLIT HALF by date (a rule that flips sign is noise) --')
    dates = sorted({r[0] for r in moved})
    if len(dates) >= 4:
        mid = dates[len(dates) // 2]
        early = [r for r in moved if r[0] < mid]
        late = [r for r in moved if r[0] >= mid]
        print(f'    split at {mid}: {len(early)} early / {len(late)} late')
        report('FADE  EARLY half', early,
               lambda m: 'AWAY' if m['move'] < 0 else 'HOME')
        report('FADE  LATE half', late,
               lambda m: 'AWAY' if m['move'] < 0 else 'HOME')
    else:
        print('    too few distinct dates to split')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
