"""Grade line_movement_flags on the side the ENGINE actually bets.

WHY THIS EXISTS. ensemble_scorer._handler_split turns every non-neutral
line_movement_flags row into an Opinion that pushes a pick toward a side.
On 2026-08-31 a 30-day MLB audit found the 2-source and 1-source
classifications losing badly, and a quarantine was added — but it compares

    signal_key == 'sharp_split_confirmed'

and the live football / NCAAB / NHL signals are keyed
`sharp_split_confirmed_nfl`, `_ncaaf`, `_ncaab`, `_nhl`. Nothing strips the
suffix, so for every sport except MLB:

  * the quarantine never applies — the classifications measured as losing
    still push picks;
  * and the tier match on the next line is skipped too, so
    `sharp_split_triple_confirmed_<sport>` emits on EVERY classification
    rather than only TRIPLE, while `sharp_split_confirmed_<sport>` emits on
    the same flags — the same flag counted twice.

Exposure measured 2026-10-12: NCAAF 354 non-neutral flags of which only 23
are TRIPLE, NHL 99, NFL 4.

Fixing the guard is a one-line change. Knowing WHICH WAY to fix it is not,
so this grades the flags per sport before anything is quarantined.

HOW IT GRADES. On the side the engine emits, not the raw flag side:
_flag_to_candidate inverts RLM_* and PUBLIC_MOVE_* (betting against the
public move), so this applies the same inversion. Grading the raw side
would score a side the engine never takes.

Spreads are graded against the number, moneylines on the winner with the
real closing price so ROI is money and not just hit rate, and totals
against the closing total.

WRITES NOTHING.

CLI
    python audit_line_flags.py
    python audit_line_flags.py --sport NCAAF
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import random

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

import market_line as ML

#: sport -> (results table, close_spread sign for a home favourite,
#:           (home ml col, away ml col))
SPORTS = {
    'MLB': ('mlb_game_results', -1, ('home_ml_close', 'away_ml_close')),
    'NCAAF': ('ncaaf_game_results', -1, ('close_home_ml', 'close_away_ml')),
    'NFL': ('nfl_game_results', +1, ('close_home_ml', 'close_away_ml')),
    'NHL': ('nhl_game_results', -1, ('close_home_ml', 'close_away_ml')),
}


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
    if o is None or o == 0:
        return None
    if won:
        return (o / 100.0) if o > 0 else (100.0 / -o)
    return -1.0


def boot_ci(vals, iters=8000, seed=11):
    """Percentile bootstrap CI of mean ROI. Mixed moneyline prices make the
    analytic SE meaningless, and the only question that matters is whether
    zero sits inside the interval."""
    rng = random.Random(seed)
    n = len(vals)
    means = []
    for _ in range(iters):
        s = 0.0
        for _i in range(n):
            s += vals[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    return means[int(iters * 0.025)] * 100, means[int(iters * 0.975)] * 100


def srcs(cls):
    """How many split sources backed the flag, per its classification."""
    if '_TRIPLE_CONFIRMED' in cls:
        return 'TRIPLE (3 src)'
    if '_CONFIRMED' in cls:
        return 'CONFIRMED (2 src)'
    return 'LEAN (1 src)'


def row_line(label, sub, pad=34):
    n = len(sub)
    if n < 15:
        print(f'    {label:<{pad}} n={n} too thin')
        return
    w = sum(r['won'] for r in sub)
    hit = w / n * 100
    se2 = (0.5 / n ** 0.5) * 200
    pf = [p for p in (profit(r.get('odds'), r['won']) for r in sub)
          if p is not None]
    roi = (statistics.fmean(pf) * 100) if pf else None
    roi_s = f'{roi:+6.1f}%' if roi is not None else '    n/a'
    # 52.38% is breakeven at -110; for ML the ROI column is the judge.
    flag = ''
    if roi is not None and n >= 30:
        if roi < -5:
            flag = '  <== LOSING MONEY'
        elif roi > 5:
            flag = '  <== profitable'
    ci = ''
    if pf and len(pf) >= 30:
        lo, hi = boot_ci(pf)
        ci = (f'  CI[{lo:+5.1f},{hi:+5.1f}]'
              + ('' if lo <= 0 <= hi else ' *'))
    print(f'    {label:<{pad}} {w}-{n - w}  {hit:5.1f}%  ROI {roi_s}  '
          f'n={n:<4} ±{se2:.0f}{ci}{flag}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    a = ap.parse_args()

    flags = page('line_movement_flags', {
        'select': 'sport,market,side,classification,game_id,money_pct,'
                  'bets_pct,handle_pct,bettors_pct,last_seen_at'})
    sports = [a.sport.upper()] if a.sport else sorted(SPORTS)

    print('=== line_movement_flags graded on the side the ENGINE bets ===')
    print('    RLM_* and PUBLIC_MOVE_* are inverted, exactly as')
    print('    _flag_to_candidate does, so this scores what is actually '
          'published.\n')

    for sport in sports:
        if sport not in SPORTS:
            continue
        res_t, sign, ml_cols = SPORTS[sport]
        res = page(res_t, {'select': 'game_id,game_date,home_team,away_team,'
                                     'home_score,away_score,close_spread,'
                                     'close_total,' + ','.join(ml_cols)})
        by_id = {str(r['game_id']): r for r in res if r.get('game_id')}
        fl = [x for x in flags if str(x.get('sport')).upper() == sport]

        graded = []
        unjoined = 0
        for x in fl:
            cls = str(x.get('classification') or '')
            if not cls or cls in ('PATTERN_ONLY', 'NEUTRAL', 'SOURCES_SPLIT'):
                continue
            r = by_id.get(str(x.get('game_id')))
            if not r:
                unjoined += 1
                continue
            hs, as_ = f(r.get('home_score')), f(r.get('away_score'))
            if hs is None or as_ is None:
                continue
            side = str(x.get('side') or '').upper()
            mkt = str(x.get('market') or '').lower()
            # Same inversion the scorer applies.
            invert = cls.startswith('RLM') or cls.startswith('PUBLIC_MOVE')
            if invert:
                side = {'HOME': 'AWAY', 'AWAY': 'HOME',
                        'OVER': 'UNDER', 'UNDER': 'OVER'}.get(side, side)
            margin = hs - as_
            odds = None
            if mkt in ('rl', 'spread'):
                cs = f(r.get('close_spread'))
                if cs is None or side not in ('HOME', 'AWAY'):
                    continue
                cover = margin - (cs * sign)
                if abs(cover) < 1e-9:
                    continue
                won = (cover > 0) if side == 'HOME' else (cover < 0)
                odds = -110 if sport in ('NFL', 'NCAAF') else None
            elif mkt == 'ml':
                if side not in ('HOME', 'AWAY'):
                    continue
                won = (margin > 0) if side == 'HOME' else (margin < 0)
                odds = r.get(ml_cols[0] if side == 'HOME' else ml_cols[1])
            elif mkt == 'total':
                ct = f(r.get('close_total'))
                if ct is None or side not in ('OVER', 'UNDER'):
                    continue
                tot = hs + as_
                if abs(tot - ct) < 1e-9:
                    continue
                won = (tot > ct) if side == 'OVER' else (tot < ct)
                odds = -110
            else:
                continue
            graded.append({'cls': cls, 'mkt': mkt, 'side': side,
                           'date': str(r.get('game_date'))[:10],
                           'won': 1 if won else 0, 'odds': odds,
                           'src': srcs(cls),
                           'fam': cls.rsplit('_CONFIRMED', 1)[0]
                                     .rsplit('_LEAN', 1)[0]
                                     .replace('_TRIPLE', '')})

        print(f'── {sport}: {len(fl)} flags · {len(graded)} graded · '
              f'{unjoined} could not join a result')
        if not graded:
            print()
            continue

        print('  BY SOURCE COUNT (this is what the quarantine keys on):')
        for s in ('TRIPLE (3 src)', 'CONFIRMED (2 src)', 'LEAN (1 src)'):
            row_line(s, [g for g in graded if g['src'] == s])

        print('  BY FAMILY:')
        for fam in sorted({g['fam'] for g in graded}):
            row_line(fam, [g for g in graded if g['fam'] == fam])

        print('  BY MARKET:')
        for mkt in sorted({g['mkt'] for g in graded}):
            row_line(mkt, [g for g in graded if g['mkt'] == mkt])

        # ══ TEMPORAL SPLIT ══
        # The 2026-08-31 MLB quarantine was fit to a 30-day window and the
        # full sample now disagrees with it. So every source-count row gets
        # cut in half by date: a real effect shows in both halves, a
        # window-fit one lives in one.
        dated = [g for g in graded if g.get('date')]
        if len(dated) >= 60:
            ds = sorted(g['date'] for g in dated)
            mid = ds[len(ds) // 2]
            print(f'  TEMPORAL SPLIT at {mid} (does it hold in both '
                  f'halves?):')
            for s_ in ('CONFIRMED (2 src)', 'LEAN (1 src)'):
                for half, lab in ((True, 'early'), (False, 'late')):
                    sub = [g for g in dated if g['src'] == s_
                           and ((g['date'] < mid) == half)]
                    row_line(f'{s_} · {lab}', sub)
        print()

    print('  ' + '=' * 68)
    print('  The MLB quarantine dropped CONFIRMED (2 src) and LEAN (1 src)')
    print('  to zero confidence on 2026-08-31. Every other sport still')
    print('  emits them because the guard compares a bare signal_key. The')
    print('  rows above say whether that is costing money in each sport —')
    print('  and a sport with too few graded flags gets no verdict, not a')
    print('  guess.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
