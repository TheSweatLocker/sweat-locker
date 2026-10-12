"""Do our published picks beat the closing line?

ANDY 2026-10-12: "dont lean on the reciepts ... showing shitty reciprts
doesn t mkae the app better just because were honest ... there need to be
some performance".

He is right, and this is the measurement that has never been run. Hit rate
is a LAGGING, high-variance read — 60 picks tells you almost nothing. CLV
is the leading one: if we consistently get a worse number than the market
closes at, we are behind the market and no amount of calibration fixes it.
If we consistently beat it, there is real edge the W-L record has not
caught up to yet. It is the only forward-looking evidence available at our
sample sizes.

HOW IT IS COMPUTED
  * Our number: public_receipts.pick_line (the side's own number, positive
    = getting points) and pick_odds.
  * The close: the LAST line_history capture strictly before
    commence_time, taken as the MEDIAN across books so one stale or
    outlier book cannot swing it.
  * Spread / total: CLV in POINTS of line, signed so positive always means
    we got the better number — a spread side wants a higher number, an
    OVER wants a lower total, an UNDER a higher one.
  * Moneyline: CLV in implied-probability POINTS, positive meaning we took
    a better price than the close.

WHY THE JOIN IS PARTIAL AND WHY THAT IS STATED, NOT HIDDEN. Only 22% of
receipt game_ids appear in line_history, because line_history carries a
mix of id schemes and the poller only covers what the odds feed returned.
A CLV number computed on the covered subset is still informative, but it
is a subset and the coverage is printed with every run so it cannot be
read as the whole book.

WRITES NOTHING.

CLI
    python audit_clv.py --since 2026-09-15
    python audit_clv.py --sport NCAAF
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

import market_line as ML

MKT = {'rl': 'spread', 'spread': 'spread', 'ml': 'ml', 'total': 'total'}


def page(table, params, cap=80000):
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
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def imp(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-09-15')
    ap.add_argument('--sport', default=None)
    a = ap.parse_args()

    print(f'=== CLV · do our picks beat the close? · since {a.since} ===\n')
    rc = [x for x in page('public_receipts', {
        'select': 'sport,market,game_id,pick_side,pick_line,pick_odds,'
                  'result,surface,game_date',
        'game_date': f'gte.{a.since}'})
        if str(x.get('market')) in MKT and x.get('game_id')]
    if a.sport:
        rc = [x for x in rc if str(x.get('sport')).upper() == a.sport.upper()]

    lh = page('line_history', {
        'select': 'game_id,market,side,line,price,captured_at,commence_time',
        'captured_at': f'gte.{a.since}'})
    # (game_id, market, side) -> rows, newest last
    by_key = defaultdict(list)
    for r in lh:
        by_key[(str(r.get('game_id')), str(r.get('market')),
                str(r.get('side')).lower())].append(r)

    def closing(gid, market, side):
        """Median line/price across books at the last pre-game capture."""
        rows = by_key.get((gid, market, side)) or []
        pre = [r for r in rows
               if r.get('commence_time') and r.get('captured_at')
               and str(r['captured_at']) < str(r['commence_time'])]
        if not pre:
            return None, None
        last = max(str(r['captured_at']) for r in pre)
        # Everything within the final 90 minutes of captures counts as the
        # close, so a single book's late tick is not the whole signal.
        tail = [r for r in pre if str(r['captured_at'])[:16] >= last[:16]]
        lines = [float(r['line']) for r in tail if r.get('line') is not None]
        prices = [float(r['price']) for r in tail if r.get('price') is not None]
        return (statistics.median(lines) if lines else None,
                statistics.median(prices) if prices else None)

    buckets = defaultdict(list)
    matched = 0
    for x in rc:
        gid = str(x['game_id'])
        mkt = MKT[str(x['market'])]
        side = str(x.get('pick_side') or '').lower()
        if side not in ('home', 'away', 'over', 'under'):
            continue
        cl, cp = closing(gid, mkt, side)
        if cl is None and cp is None:
            continue
        matched += 1
        sport = str(x.get('sport'))
        if mkt == 'ml':
            ours, close = imp(x.get('pick_odds')), imp(cp)
            if ours is None or close is None:
                continue
            # Lower implied probability = better price for the same side.
            buckets[(sport, 'ml')].append((close - ours) * 100)
        else:
            try:
                ours = float(x['pick_line'])
            except (TypeError, ValueError, KeyError):
                continue
            if cl is None:
                continue
            # Positive = we got the better number.
            # spread: a side always wants a HIGHER number.
            # total:  OVER wants LOWER, UNDER wants HIGHER.
            if mkt == 'total' and side == 'over':
                buckets[(sport, mkt)].append(cl - ours)
            else:
                buckets[(sport, mkt)].append(ours - cl)

    print(f'  {len(rc)} receipts on graded markets · {matched} matched to a '
          f'closing line ({matched / max(len(rc), 1) * 100:.0f}% coverage)')
    print('  Coverage is partial because line_history carries a mix of id '
          'schemes.\n')
    if not buckets:
        print('  nothing matched — cannot judge CLV')
        return 0

    print(f"  {'sport / market':<22}{'n':>6}{'mean CLV':>11}{'median':>9}"
          f"{'beat close':>12}")
    print('  ' + '-' * 62)
    tot = []
    for (sport, mkt), vals in sorted(buckets.items()):
        if len(vals) < 10:
            continue
        unit = 'pp' if mkt == 'ml' else 'pts'
        beat = sum(1 for v in vals if v > 0) / len(vals) * 100
        print(f'  {sport + " " + mkt:<22}{len(vals):>6}'
              f'{statistics.fmean(vals):>+9.2f}{unit:>2}'
              f'{statistics.median(vals):>+9.2f}{beat:>11.0f}%')
        tot += vals
    if tot:
        beat = sum(1 for v in tot if v > 0) / len(tot) * 100
        print('  ' + '-' * 62)
        print(f'  {"ALL (mixed units)":<22}{len(tot):>6}'
              f'{statistics.fmean(tot):>+9.2f}  {statistics.median(tot):>+9.2f}'
              f'{beat:>11.0f}%')
    print('\n  Read "beat close" as the share of picks that got a better')
    print('  number than the market settled on. Above ~50% is the only')
    print('  forward evidence of edge available at these sample sizes;')
    print('  consistently below it means we are trading against the market')
    print('  and losing the trade before the game starts.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
