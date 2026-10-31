"""The LIVE record of any surface, cut by sport, market and tier.

══ 2026-10-30 · THE RECORD I QUOTED WAS A RECONSTRUCTION ══════════════════
I told Andy game reads at 57.0% (n=314) were our best surface. That figure
was 99% capture_mode='reconstructed' -- rebuilt by backfill_public_receipts
after the fact, not recorded when the pick went out. This script exists so
that mistake is not repeatable: it reports LIVE rows by default and prints
the live/reconstructed split first, so a reconstruction can never be read as
a record.

Why the distinction is not pedantic. Where both modes exist on the same
surface the reconstructed half reads 59.40% / +5.06% and the live half
56.00% / -3.65%. Reconstructed rows FLATTER, so a negative live number is
the optimistic one.

THE LIVE RECORD OF THE WHOLE APP, all time, as of tonight -- 672 bets:

    sweat_card   221  127-94   57.5%  [50.9,64.0]  ROI  +4.59%
    fade          74   41-33   55.4%  [44.1,66.7]  ROI  +7.02%
    sharp_card   314 163-151   51.9%  [46.4,57.4]  ROI  -7.07%
    potd          17     8-9   47.1%               ROI  -7.86%
    prop_jerry     1   -- no live record at all (R83)
    game_read      5   -- no live record at all

THE STEAM ROOM, cut down -- why it is -7.07%:

    market=ml   MLB      54   68.5%  [56.1,80.9]   ROI +17.32%   <- the edge
    market=rl   STRONG   69   42.0%  [30.4,53.7]   ROI -18.38%   <- the leak
    market=rl   NFL      16   31.2%                ROI -40.72%
    market=rl   NHL      23   39.1%  [19.2,59.1]   ROI -20.29%
    market=rl   NCAAF    50   48.0%  [34.2,61.8]   ROI  -8.88%
    market=ml   NCAAF    30   46.7%  [28.8,64.5]   ROI -19.60%
    market=ml   NHL      12   50.0%               ROI -11.69%
    market=prop          91   52.7%  [42.5,63.0]   ROI  -9.44%
    market=total         29   48.3%  [30.1,66.5]   ROI  -7.84%

Two readings, both uncomfortable.

1. The ONLY positive bucket is MLB moneylines, and that matches the one edge
   we have independently established (project_mlb_ml_is_the_one_real_edge:
   live 63-29, 68.48%, +18.83%, n=92). MLB is out of season, so the Steam
   Room currently has NO positive bucket in its live record.

2. Confidence is an anti-signal on spreads. STRONG spreads lose 18.38% of
   stake across 69 live bets while LEAN spreads lose 4.60% across 14. More
   conviction, worse result -- the same shape generate_sharp_card's own flip
   comment records ("our conviction on this market is an anti-signal") and
   that project_nfl_curated_spread_is_the_leak measured at -45.9%.

WHAT NOT TO CONCLUDE. n=69 on rl STRONG has a 95% CI of [30.4, 53.7], whose
upper bound sits just above the 52.38% spread breakeven, so this is a
credible negative rather than a proven one. And ROI here is computed from
each receipt's OWN pick_odds, never a flat -110
(feedback_flat_110_inflates_every_ml_figure); where pick_odds is null it
falls back to -110 and the fallback count is printed.

USAGE
  python audit_live_surface_roi.py
  python audit_live_surface_roi.py --surface sharp_card
  python audit_live_surface_roi.py --surface sweat_card --include-reconstructed
"""
from __future__ import annotations

import argparse
import collections
import math
import os
import sys
from pathlib import Path

import requests

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

WON = ('WIN', 'W')
LOST = ('LOSS', 'L')
#: Spread/total breakeven at -110. ML breakeven rises with the price, so no
#: single number is quoted for moneylines.
BREAKEVEN = 52.38
#: Cells thinner than this are counted but never quoted as a rate.
MIN_N = 15


def page(table: str, params: dict):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = 1000
        p['offset'] = off
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  ! {table} HTTP {r.status_code} {r.text[:140]}')
            return None
        d = r.json()
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def ci95(w: int, n: int):
    if not n:
        return (0.0, 0.0)
    p = w / n
    h = 1.96 * math.sqrt(max(p * (1 - p), 1e-9) / n)
    return (100.0 * max(0.0, p - h), 100.0 * min(1.0, p + h))


def settle(rows, sel):
    """(wins, n, units, null_odds) over the rows matching sel."""
    w = n = null_odds = 0
    u = 0.0
    for x in rows:
        if not sel(x):
            continue
        res = str(x.get('result') or '').upper()
        if res not in WON + LOST:
            continue
        n += 1
        o = x.get('pick_odds')
        try:
            o = float(o)
        except (TypeError, ValueError):
            o = -110.0
            null_odds += 1
        if res in WON:
            w += 1
            u += (o / 100.0 if o > 0 else 100.0 / abs(o))
        else:
            u -= 1.0
    return w, n, u, null_odds


def line(label: str, w: int, n: int, u: float, note: str = '') -> str:
    lo, hi = ci95(w, n)
    return (f'  {label:26s} {n:5d} {100.0 * w / n:6.1f}% '
            f'[{lo:5.1f},{hi:5.1f}] {100.0 * u / n:7.2f}%  {note}')


def run(surface: str | None, include_recon: bool) -> int:
    params = {'select': 'surface,sport,market,tier,result,pick_odds,'
                        'capture_mode,game_date',
              'result': 'not.is.null'}
    if surface:
        params['surface'] = f'eq.{surface}'
    allrows = page('public_receipts', params)
    if allrows is None:
        print('CANNOT VERIFY -- treating as a failure')
        return 2

    modes = collections.Counter(str(x.get('capture_mode')) for x in allrows)
    print('=== capture_mode split (a reconstruction is NOT a record) ===')
    for m, c in modes.most_common():
        print(f'   {m:16s} {c:6d}')
    rows = allrows if include_recon else [
        x for x in allrows if str(x.get('capture_mode')) == 'live']
    if include_recon:
        print('\n!! including reconstructed rows -- these FLATTER; where both '
              'modes exist, reconstructed reads +5.06% against live -3.65%')
    if not rows:
        print('\nno live rows for this surface -- nothing can be claimed about '
              'it (R83). A missing live record is a capture bug, not a result.')
        return 1

    print(f'\n=== {"live" if not include_recon else "all"} graded bets: '
          f'{len(rows)} ===')
    print(f'  {"cut":26s} {"n":>5s} {"hit%":>7s} {"95% CI":>15s} {"ROI%":>8s}')

    if not surface:
        for s in sorted({str(x.get('surface')) for x in rows}):
            w, n, u, _ = settle(rows, lambda x, s=s: str(x.get('surface')) == s)
            if n < MIN_N:
                print(f'  {"surface=" + s:26s} {n:5d}   n below {MIN_N} '
                      f'-- not quoted')
                continue
            print(line('surface=' + s, w, n, u))
        return 0

    out = []
    for dim, get in (('sport', lambda x: str(x.get('sport')).upper()),
                     ('market', lambda x: str(x.get('market')).lower()),
                     ('tier', lambda x: str(x.get('tier')).upper())):
        for v in sorted({get(x) for x in rows}):
            w, n, u, nullo = settle(rows, lambda x, g=get, v=v: g(x) == v)
            if n < MIN_N:
                continue
            note = f'({nullo} null odds -> -110)' if nullo else ''
            out.append((100.0 * u / n, f'{dim}={v}', w, n, u, note))
    for _, lab, w, n, u, note in sorted(out):
        print(line(lab, w, n, u, note))

    # the cross-cut is where a diffuse loss turns out to be one bucket
    print(f'\n=== market x tier and market x sport (n >= {MIN_N}) ===')
    cross = []
    for mk in sorted({str(x.get('market')).lower() for x in rows}):
        for dim, get in (('tier', lambda x: str(x.get('tier')).upper()),
                         ('sport', lambda x: str(x.get('sport')).upper())):
            for v in sorted({get(x) for x in rows}):
                w, n, u, _ = settle(
                    rows, lambda x, mk=mk, g=get, v=v:
                    str(x.get('market')).lower() == mk and g(x) == v)
                if n < MIN_N:
                    continue
                cross.append((100.0 * u / n, f'{mk} {dim}={v}', w, n, u))
    for _, lab, w, n, u in sorted(cross):
        lo, hi = ci95(w, n)
        verdict = ''
        # A moneyline is NEVER judged against 52.38. ML breakeven rises with
        # the price (books average -120..-203, so 56-58%), and labelling an
        # ML cut "clears breakeven" off the spread number is exactly
        # feedback_flat_110_inflates_every_ml_figure. MLB ml reads
        # CI[56.1,80.9]: its lower bound sits AT the bottom of the ML
        # breakeven band, so it is marginal, not cleared. ML is judged on
        # ROI alone.
        if lab.startswith('ml '):
            verdict = ('ROI-judged; ML breakeven is 56-58%, '
                       'not 52.38') if u > 0 else 'ROI negative'
        elif hi < BREAKEVEN:
            verdict = 'NEGATIVE, CI clears below breakeven'
        elif lo > BREAKEVEN:
            verdict = 'POSITIVE, CI clears above breakeven'
        print(line(lab, w, n, u, verdict))
    print('\nML breakeven rises with the price (56-58%), so a moneyline cut '
          'is judged on ROI, not against 52.38%.')
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--surface', default=None,
                    help='one surface to cut down; omit for the overview')
    ap.add_argument('--include-reconstructed', action='store_true')
    a = ap.parse_args()
    raise SystemExit(run(a.surface, a.include_reconstructed))


if __name__ == '__main__':
    main()
