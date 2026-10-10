"""Systematic scan of stat gaps and situational records vs covering — NCAAF/NFL.

ANDY 2026-10-10: "There has to be more significant underlying data or patetrns
given a teams stats gap, situational recrods, etc. I think you arebebing lazy
and not scoping or reseraching this correctly."

He is right about the scoping. Every test I ran before this was UNIVARIATE —
one variable, bucketed, measured. ncaaf_game_context carries 207 columns: 57
stat fields, 48 situational/ATS fields, 17 roster fields. I had tested about
five of them and never looked at a combination.

WHAT MAKES THIS LEAK-FREE, established before any feature was touched
ncaaf_game_context is upserted, so a past game's stats COULD have been
refreshed with current values. Tested rather than assumed: week-0 games carry
team_trends_updated_at stamped on the game date and their values DIFFER from
the teams' current season-long figures (Eastern Michigan off_epa 0.199 then vs
0.048 now). Across the table, 388 rows are stamped on or before their game
date, 3 after, 103 unstamped. So the columns are frozen at pick time — and
this script uses ONLY rows whose stamp is on or before the game date, dropping
the 3 leaked and the 103 unknown rather than trusting them.

Usable leak-free sample: 268 NCAAF games with a graded cover result and frozen
EPA on both sides.

WHY A SPLIT AND A CORRECTION ARE NOT OPTIONAL HERE
268 games against ~130 candidate features is a ratio that manufactures
phantoms. Testing everything and reporting the best cell is how you get a
"finding" that evaporates. So:
  * FEATURES ARE GAPS, pre-specified as home-minus-away, which halves the
    space and encodes the only question that matters in a matchup.
  * TIME SPLIT. Features are ranked on the EARLIER games and then validated
    on the LATER ones. A pattern that does not survive the holdout is not
    reported as real, however good it looks in training.
  * MULTIPLICITY IS COUNTED. The script states how many features it scanned
    and what the best cell would look like under pure chance, so a 56% cell
    out of 60 tests is read correctly.
  * NO FITTING. Each feature is split at its median and the cover rate of the
    favoured side measured, so there is no coefficient to overfit.

This is a SEARCH, and a search's output is candidates, not conclusions. The
holdout column is the one to read.

WRITES NOTHING.

CLI
    python scan_football_gap_patterns.py --sport NCAAF
    python scan_football_gap_patterns.py --sport NCAAF --today-slate
"""
from __future__ import annotations

import argparse
import collections
import math
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
BREAKEVEN = 52.38
CTX = {'NCAAF': 'ncaaf_game_context', 'NFL': 'nfl_game_context'}
RES = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results'}
HOME_FAV_SIGN = {'NCAAF': -1.0, 'NFL': +1.0}


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


def build_gaps(sport):
    """Leak-free rows with pre-specified home-minus-away gap features."""
    cols = _page(CTX[sport], {'select': '*', 'limit': '1'})
    if not cols:
        return [], []
    allc = sorted(cols[0].keys())
    # Pair every home_X with its away_X twin. That pairing IS the
    # pre-specification — no cherry-picking which stats to try.
    pairs = []
    for c in allc:
        if c.startswith('home_'):
            twin = 'away_' + c[5:]
            if twin in allc:
                pairs.append((c[5:], c, twin))
    sel = ','.join(['game_id', 'game_date', 'week', 'season',
                    'team_trends_updated_at', 'close_spread', 'home_team',
                    'away_team']
                   + [c for _n, c, t in pairs for c in (c, t)])
    rows = _page(CTX[sport], {'select': sel})
    graded = {}
    for x in _page(RES[sport], {'select': 'game_id,spread_result'}):
        sr = str(x.get('spread_result') or '').lower()
        if sr in ('home_covered', 'away_covered'):
            graded[str(x['game_id'])] = 1 if sr == 'home_covered' else 0
    out, leaked, unstamped = [], 0, 0
    for x in rows:
        gid = str(x.get('game_id'))
        if gid not in graded:
            continue
        stamp = str(x.get('team_trends_updated_at') or '')[:10]
        gd = str(x.get('game_date') or '')[:10]
        if not stamp:
            unstamped += 1
            continue
        if stamp > gd:
            leaked += 1
            continue
        g = {'gid': gid, 'date': gd, 'week': x.get('week'),
             'season': str(x.get('season')), 'home_cover': graded[gid],
             'mkt_home': (_f(x.get('close_spread')) or 0)
             * HOME_FAV_SIGN[sport],
             'home': x.get('home_team'), 'away': x.get('away_team')}
        for name, hc, ac in pairs:
            hv, av = _f(x.get(hc)), _f(x.get(ac))
            g[f'gap__{name}'] = None if hv is None or av is None else hv - av
        out.append(g)
    print(f'    {len(rows)} ctx rows · {len(out)} leak-free graded · '
          f'dropped {leaked} post-game-stamped, {unstamped} unstamped')
    return out, [f'gap__{n}' for n, _h, _a in pairs]


def eval_feature(rows, feat):
    """Median split; cover rate of the side the gap favours. No fitting."""
    vals = [(g[feat], g['home_cover']) for g in rows
            if g.get(feat) is not None]
    if len(vals) < 40:
        return None
    med = statistics.median(v for v, _ in vals)
    # Favoured side = sign of (gap - median), so a feature with an offset
    # baseline is not credited for the baseline itself.
    hits = [c if (v - med) > 0 else 1 - c for v, c in vals if v != med]
    if len(hits) < 40:
        return None
    hit = statistics.fmean(hits) * 100
    se = (0.5 / len(hits) ** 0.5) * 100
    return {'feat': feat, 'hit': hit, 'n': len(hits), 'se2': 2 * se,
            'edge': hit - BREAKEVEN, 'median': med}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NCAAF', choices=('NCAAF', 'NFL'))
    ap.add_argument('--top', type=int, default=8)
    a = ap.parse_args()
    sport = a.sport
    print(f'=== {sport} GAP PATTERN SCAN')
    rows, feats = build_gaps(sport)
    if len(rows) < 100:
        print('    too few leak-free rows to split and validate.')
        return 0
    rows.sort(key=lambda g: g['date'])
    cut = int(len(rows) * 0.6)
    train, test = rows[:cut], rows[cut:]
    print(f'    {len(feats)} paired gap features')
    print(f'    TRAIN {len(train)} games ({train[0]["date"]}..'
          f'{train[-1]["date"]})  ·  HOLDOUT {len(test)} games '
          f'({test[0]["date"]}..{test[-1]["date"]})')
    print('    Time-ordered split: rank on TRAIN, validate on HOLDOUT.')

    scored = [r for r in (eval_feature(train, f) for f in feats) if r]
    scored.sort(key=lambda r: -r['edge'])
    print(f'\n    {len(scored)} features had enough coverage to score.')
    exp_best = BREAKEVEN + 2 * (0.5 / max(1, len(train)) ** 0.5) * 100 * \
        math.sqrt(2 * math.log(max(2, len(scored))))
    print(f'    Under pure chance the BEST of {len(scored)} features would')
    print(f'    look like ~{exp_best:.1f}% on this sample. Treat anything')
    print(f'    below that as noise no matter where it ranks.')

    print(f'\n  TOP {a.top} ON TRAIN, THEN THE SAME FEATURE ON HOLDOUT:')
    print(f'      {"feature":<34}{"train":>16}{"holdout":>18}')
    survivors = []
    for r in scored[:a.top]:
        h = eval_feature(test, r['feat'])
        tr = f'{r["hit"]:.1f}% n={r["n"]}'
        ho = f'{h["hit"]:.1f}% n={h["n"]}' if h else 'n/a'
        flag = ''
        if h and h['edge'] > 0 and r['edge'] > 0:
            flag = '  <-- holds both'
            survivors.append((r, h))
        print(f'      {r["feat"].replace("gap__",""):<34}{tr:>16}{ho:>18}'
              f'{flag}')

    print(f'\n  BOTTOM {a.top} ON TRAIN (a consistent NEGATIVE is a FADE'
          f' candidate):')
    for r in scored[-a.top:]:
        h = eval_feature(test, r['feat'])
        tr = f'{r["hit"]:.1f}% n={r["n"]}'
        ho = f'{h["hit"]:.1f}% n={h["n"]}' if h else 'n/a'
        flag = '  <-- fade holds both' if h and h['edge'] < 0 \
            and r['edge'] < 0 else ''
        print(f'      {r["feat"].replace("gap__",""):<34}{tr:>16}{ho:>18}'
              f'{flag}')

    print('\n' + '=' * 74)
    print(f'  {len(survivors)} of the top {a.top} stayed positive on the')
    print('  holdout. That number is what matters — a train-only winner is')
    print('  a coincidence this search was guaranteed to produce.')
    if survivors:
        print('\n  CANDIDATES (positive in BOTH halves):')
        for r, h in survivors:
            print(f'    {r["feat"].replace("gap__",""):<34}'
                  f'train {r["hit"]:.1f}% (n={r["n"]}) · '
                  f'holdout {h["hit"]:.1f}% (n={h["n"]}) · '
                  f'2SE +/-{h["se2"]:.1f}pp')
        print('\n  None of these is publishable on this sample. They are')
        print('  pre-registration candidates: grade them FORWARD rather than')
        print('  re-deriving on a bigger pool and calling that confirmation.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
