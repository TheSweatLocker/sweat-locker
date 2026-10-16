#!/usr/bin/env python3
"""Compute the TRUE parlay price for daily_degen, so its record means something.

== 2026-10-16 - WHY ==
daily_degen shows 25-119 (17.4%) in the receipts ledger and reads like the worst
surface we have. IT IS NOT MEASURABLE AS PUBLISHED:

  * all 161 receipts are PARLAYS (market=parlay, sport=MULTI)
  * pick_odds is populated on FIVE of 161
  * so the 17.4% is a hit rate with no price attached, and a hit rate without a
    price is not a record — the same mistake that made NHL props look like a
    41% disaster when the real answer was "efficiently priced"

A parlay's hit rate SHOULD be low. Four -110 legs pay about +1228, where
breakeven is 7.5%. At 17.4% that would be strongly profitable. So the surfaced
number may be hiding a winner, and nobody can tell which until the price is
computed.

THE LEGS ARE RECOVERABLE. daily_degen.legs (and the receipt's audit.legs)
carries every leg with its own American odds, so the parlay price is just the
product of the decimal odds.

TWO DEFECTS FOUND WHILE DOING IT, both reported:
  * leg_count says 3 on rows whose receipt label reads "daily_degen (4 legs)"
  * the five stored pick_odds do not reconcile with their own legs — 2026-10-16
    stores +254 while its legs (-120, +135, ...) multiply to far more

This computes the true price from the legs, reports the record BOTH ways, and
only writes with --apply.

USAGE
    python fix_degen_parlay_prices.py --dry-run
    python fix_degen_parlay_prices.py --apply
"""
from __future__ import annotations

import argparse
import json
import math
import sys

import requests

import market_line as ML

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}


def page(t, p):
    out, off = [], 0
    while True:
        q = dict(p, limit='1000', offset=str(off))
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
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


def jd(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                      # noqa: BLE001
            return None
    return v


def dec(american):
    """American -> decimal."""
    a = f(american)
    if a is None or a == 0:
        return None
    return 1 + (100 / -a) if a < 0 else 1 + (a / 100)


def to_american(d):
    """Decimal -> American."""
    if d is None or d <= 1:
        return None
    prof = d - 1
    return round(prof * 100) if prof >= 1 else round(-100 / prof)


def wilson(w, n):
    if n == 0:
        return None, None
    z, p = 1.96, w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def norm(r):
    r = str(r or '').strip().upper()
    return {'WIN': 'W', 'LOSS': 'L', 'PUSH': 'P'}.get(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    src = {str(x.get('game_date'))[:10]: x
           for x in page('daily_degen', {'select': '*'})}
    rec = [x for x in page('public_receipts', {'select': '*'})
           if str(x.get('surface')) == 'daily_degen']
    print(f'daily_degen source rows {len(src)}, receipts {len(rec)}')

    computed, mismatch, noleg = [], [], 0
    for x in rec:
        d = str(x.get('game_date'))[:10]
        legs = None
        s = src.get(d)
        if s:
            legs = jd(s.get('legs'))
        if not legs:
            au = jd(x.get('audit')) or {}
            legs = au.get('legs') if isinstance(au, dict) else None
        if not isinstance(legs, list) or not legs:
            noleg += 1
            continue
        ds = [dec(lg.get('odds')) for lg in legs if isinstance(lg, dict)]
        ds = [v for v in ds if v is not None]
        if len(ds) < 2:
            noleg += 1
            continue
        prod = 1.0
        for v in ds:
            prod *= v
        am = to_american(prod)
        stored = f(x.get('pick_odds'))
        computed.append({'id': x['id'], 'date': d, 'n_legs': len(ds),
                         'american': am, 'decimal': prod,
                         'stored': stored, 'result': norm(x.get('result')),
                         'label': str(x.get('pick_label') or '')})
        if stored is not None and am is not None and abs(stored - am) > 5:
            mismatch.append((d, stored, am, len(ds)))

    print(f'parlay price computable on {len(computed)}, '
          f'no usable legs on {noleg}')
    if mismatch:
        print(f'\nSTORED pick_odds DISAGREES with its own legs on '
              f'{len(mismatch)} row(s):')
        for d, st, am, nl in mismatch[:10]:
            print(f'  {d}  stored {st:+.0f}  computed {am:+.0f}  '
                  f'({nl} legs)')

    # leg_count vs label
    lbl_mismatch = 0
    for c in computed:
        m = None
        for tok in ('(2 legs)', '(3 legs)', '(4 legs)', '(5 legs)'):
            if tok in c['label']:
                m = int(tok[1])
        if m is not None and m != c['n_legs']:
            lbl_mismatch += 1
    if lbl_mismatch:
        print(f'\nLABEL leg count disagrees with the actual legs on '
              f'{lbl_mismatch} of {len(computed)} rows.')

    graded = [c for c in computed if c['result'] in ('W', 'L')]
    print(f'\n== THE RECORD, graded {len(graded)} ==')
    w = sum(1 for c in graded if c['result'] == 'W')
    l = len(graded) - w
    lo, hi = wilson(w, len(graded))
    print(f'  hit rate (what is being surfaced): {w}-{l}  '
          f'{100*w/max(len(graded),1):.1f}%  CI[{lo:.1f},{hi:.1f}]')
    u = 0.0
    for c in graded:
        u += (c['decimal'] - 1) if c['result'] == 'W' else -1.0
    print(f'  AT THE TRUE PARLAY PRICE: {u:+.2f} units, '
          f'ROI {100*u/max(len(graded),1):+.1f}%')
    if graded:
        avg = sum(c['decimal'] for c in graded) / len(graded)
        be = 100 / avg
        print(f'  avg parlay decimal {avg:.2f} '
              f'({to_american(avg):+.0f} American), breakeven {be:.1f}%')
        print(f'  -> {"PROFITABLE" if u > 0 else "LOSING"} once the price is '
              f'counted. The 17.4% headline on its own says nothing.')
    byleg = {}
    for c in graded:
        byleg.setdefault(c['n_legs'], [0, 0, 0.0])
        b = byleg[c['n_legs']]
        if c['result'] == 'W':
            b[0] += 1
            b[2] += c['decimal'] - 1
        else:
            b[1] += 1
            b[2] -= 1.0
    print(f'\n  {"legs":<6}{"W-L":>9}{"hit%":>8}{"units":>10}{"ROI":>8}')
    for n in sorted(byleg):
        ww, ll, uu = byleg[n]
        if ww + ll == 0:
            continue
        print(f'  {n:<6}{f"{ww}-{ll}":>9}{100*ww/(ww+ll):>7.1f}%{uu:>10.2f}'
              f'{100*uu/(ww+ll):>7.1f}%')

    if a.dry_run:
        print('\nDRY RUN — would write pick_odds on '
              f'{sum(1 for c in computed if c["stored"] is None or abs((c["stored"] or 0)-(c["american"] or 0))>5)} rows')
        return 0

    ok = fail = skip = 0
    for c in computed:
        if c['american'] is None:
            skip += 1
            continue
        if c['stored'] is not None and abs(c['stored'] - c['american']) <= 5:
            skip += 1
            continue
        w_ = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_WRITE,
                            params={'id': f'eq.{c["id"]}'},
                            data=json.dumps({'pick_odds': c['american']}),
                            timeout=60)
        if w_.status_code in (200, 204):
            body = w_.json()[0] if (w_.status_code == 200
                                    and w_.text.strip().startswith('[')) else {}
            if body and f(body.get('pick_odds')) != c['american']:
                fail += 1
                if fail <= 3:
                    print(f'  {c["date"]}: PATCH returned 200 but pick_odds '
                          f'unchanged ({body.get("pick_odds")}) — column is '
                          f'likely immutable, same as pick_label')
            else:
                ok += 1
        else:
            fail += 1
    print(f'\nwrote {ok}, failed {fail}, skipped {skip}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
