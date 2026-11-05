#!/usr/bin/env python3
"""backfill_ncaab_receipt_prices - give NCAAB receipts the number users need.

== B142: A PUBLISHED PICK WITH NO LINE AND NO PRICE (2026-11-04) ===========
Backlog T1, flagged as leading because it is wrong data in front of users.
Measured on public_receipts since 2026-10-28 (11,090 rows):

    market needs a line but pick_line NULL     60
    pick_odds NULL                            195
    both NULL                                  36
    forward/ungraded with a gap                51

NCAAB is 180 of the 195 no-odds rows (total 98, ml 82) and 25 of the 60
no-line rows. So this is not a general pricing failure, it is an NCAAB one,
and the reason is known: I reported on 2026-11-03 that "NCAAB spreads have no
price anywhere in the system" and that was WRONG. line_history holds live
two-sided NCAAB quotes from 5-11 books; the blocker was that its matchups
carry mascots ("Milwaukee Panthers @ Santa Clara Broncos") while receipts do
not ("Milwaukee @ Santa Clara"). ncaab_team_resolver bridges exactly that,
and joined 5,493 of 5,521 historical games - 99.5%.

== WHAT THIS WILL NOT DO =================================================
* It never prices a game from a quote captured AFTER tip. For a finished
  game the only honest number is the last one a bettor could actually have
  taken, so every candidate quote is filtered to captured_at <= the
  fixture's commence_time. A price lifted from mid-game would make the ROI
  on our own record a fiction.
* It requires >= 2 books. A lone book is not a consensus, and a median of
  one is just that book (feedback_consensus_line_must_be_posted).
* It only ever fills a NULL. public_receipts.pick_odds is SET-ONCE and
  pick_line is frozen harder than that, so an overwrite would silently
  no-op anyway - and a price we already published is the published price.
* It writes nothing before 2026-11-01. line_history's earliest NCAAB
  commence_time is 2026-11-01T14:30, so 10-30 and 10-31 have zero rows and
  cannot be priced from it. Those stay NULL rather than get a guess.

== AND IT READS BACK ======================================================
Both frozen columns refuse writes by returning 200/204 with zero rows
changed, which is indistinguishable from success
(feedback_receipt_audit_column_is_frozen_too, feedback_204_is_not_a_write).
So every PATCH asks for the row back and compares the stored value to what
was sent. The summary counts CONFIRMED writes, never attempted ones.

CLI:
  python backfill_ncaab_receipt_prices.py --dry-run
  python backfill_ncaab_receipt_prices.py --apply
  python backfill_ncaab_receipt_prices.py --apply --since 2026-11-03
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text().split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json',
       'Prefer': 'return=representation'}

# line_history's earliest NCAAB commence_time. Nothing before this can be
# priced from it, measured not assumed.
LH_FLOOR = '2026-11-01'
MIN_BOOKS = 2

# receipt market -> line_history market
_MKT = {'ml': 'ml', 'rl': 'spread', 'spread': 'spread', 'total': 'total'}


def _res(name):
    try:
        from ncaab_team_resolver import resolve
    except Exception:
        return None
    try:
        r = resolve(str(name or ''))
    except Exception:
        return None
    return str(r).strip().lower() if r else None


def _pair(matchup):
    """('away','home') resolved and lowercased, or None."""
    s = str(matchup or '')
    if '@' not in s:
        return None
    a, h = s.split('@', 1)
    ra, rh = _res(a), _res(h)
    return (ra, rh) if (ra and rh) else None


def _median(v):
    v = sorted(v)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _page(tbl, params, cap=40000):
    out, off = [], 0
    while True:
        p = dict(params)
        p['limit'] = '1000'
        p['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_R, params=p,
                         timeout=120)
        if r.status_code != 200:
            print(f'  read {tbl} {r.status_code}: {r.text[:160]}')
            break
        b = r.json()
        if not b:
            break
        out += b
        off += 1000
        if off > cap:
            break
    return out


def _side_for(rc):
    """line_history side token for this receipt, or None if undecidable."""
    mk = str(rc.get('market') or '').lower()
    if mk == 'total':
        lbl = str(rc.get('pick_label') or '').lower()
        if 'under' in lbl:
            return 'under'
        if 'over' in lbl:
            return 'over'
        return None
    sd = str(rc.get('pick_side') or '').upper()
    if sd.startswith('HOME'):
        return 'home'
    if sd.startswith('AWAY'):
        return 'away'
    return None


def build_quotes(since):
    """(pair, market, side) -> {'line','price','books','asof'} pre-tip only."""
    rows = _page('line_history', {
        'select': 'matchup,market,side,line,price,book,captured_at,'
                  'commence_time',
        'sport': 'eq.NCAAB',
        'commence_time': f'gte.{since}T00:00:00',
        'order': 'captured_at.desc',
    })
    print(f'  line_history NCAAB rows since {since}: {len(rows)}')
    # newest PRE-TIP quote per (fixture, market, side, book)
    best = {}
    dropped_post = 0
    for x in rows:
        ct, cap = x.get('commence_time'), x.get('captured_at')
        if not ct or not cap:
            continue
        if cap > ct:                    # quote captured after tip-off
            dropped_post += 1
            continue
        p = _pair(x.get('matchup'))
        if not p:
            continue
        k = (p, x.get('market'), x.get('side'), x.get('book'))
        if k not in best or cap > best[k]['captured_at']:
            best[k] = x
    print(f'  dropped {dropped_post} post-tip quote(s) — a number nobody '
          f'could have taken is not a price')
    agg = defaultdict(list)
    for (p, mk, sd, _bk), x in best.items():
        agg[(p, mk, sd)].append(x)
    out = {}
    for k, v in agg.items():
        if len(v) < MIN_BOOKS:
            continue
        lines = [float(x['line']) for x in v if x.get('line') is not None]
        prices = [int(x['price']) for x in v if x.get('price') is not None]
        out[k] = {
            'line': _median(lines) if lines else None,
            'price': int(_median(prices)) if prices else None,
            'books': len(v),
            'asof': max(x['captured_at'] for x in v),
        }
    print(f'  consensus quotes ({MIN_BOOKS}+ books): {len(out)}')
    return out


def run(since, apply_it, limit=None):
    quotes = build_quotes(since)
    rc = _page('public_receipts', {
        'select': 'id,sport,surface,market,game_date,matchup,pick_label,'
                  'pick_side,pick_line,pick_odds,result',
        'sport': 'eq.NCAAB',
        'game_date': f'gte.{since}',
    })
    print(f'  NCAAB receipts since {since}: {len(rc)}')
    plan = []
    miss = defaultdict(int)
    for x in rc:
        mk = str(x.get('market') or '').lower()
        lh_mk = _MKT.get(mk)
        if not lh_mk:
            miss['market_not_priceable'] += 1
            continue
        need_odds = x.get('pick_odds') is None
        need_line = (lh_mk in ('spread', 'total')
                     and x.get('pick_line') is None)
        if not (need_odds or need_line):
            continue
        p = _pair(x.get('matchup'))
        if not p:
            miss['matchup_unresolvable'] += 1
            continue
        sd = _side_for(x)
        if not sd:
            miss['side_undecidable'] += 1
            continue
        q = quotes.get((p, lh_mk, sd))
        if not q:
            miss['no_consensus_quote'] += 1
            continue
        body = {}
        if need_odds and q.get('price') is not None:
            body['pick_odds'] = q['price']
        if need_line and q.get('line') is not None:
            body['pick_line'] = q['line']
        if not body:
            miss['quote_lacked_the_field'] += 1
            continue
        plan.append((x, body, q))
    print(f'\n  priceable now: {len(plan)}')
    for k, v in sorted(miss.items()):
        print(f'  skipped · {k:26s} {v}')
    if limit:
        plan = plan[:limit]
    print()
    for x, body, q in plan[:25]:
        print('  id=%-8s %-6s %-34s %-18s -> %-34s (%d books, asof %s)'
              % (x['id'], x.get('market'), str(x.get('matchup'))[:34],
                 str(x.get('pick_label'))[:18], body, q['books'],
                 q['asof'][:16]))
    if len(plan) > 25:
        print(f'  ... and {len(plan) - 25} more')
    if not apply_it:
        print('\n  [DRY RUN] nothing written. re-run with --apply')
        return 0
    confirmed = refused = 0
    refused_fields = defaultdict(int)
    for x, body, _q in plan:
        r = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W,
                           params={'id': f'eq.{x["id"]}'}, json=body,
                           timeout=30)
        got = None
        if r.status_code in (200, 201) and isinstance(r.json(), list) and r.json():
            got = r.json()[0]
        if got is None:
            # 200/204 with zero rows IS the frozen-column refusal
            rb = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_R,
                              params={'id': f'eq.{x["id"]}',
                                      'select': 'pick_line,pick_odds'},
                              timeout=20)
            got = (rb.json() or [{}])[0] if rb.status_code == 200 else {}
        ok = True
        for f, want in body.items():
            have = got.get(f)
            if have is None or abs(float(have) - float(want)) > 1e-6:
                ok = False
                refused_fields[f] += 1
        if ok:
            confirmed += 1
        else:
            refused += 1
    print(f'\n  CONFIRMED on readback: {confirmed} of {len(plan)}')
    if refused:
        print(f'  REFUSED (column frozen / no-op): {refused} — '
              f'{dict(refused_fields)}')
        print('  A frozen column returns success with zero rows changed, so '
              'this count comes from reading each row back, not from the '
              'PATCH status.')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default=LH_FLOOR,
                    help=f'earliest game_date (default {LH_FLOOR}, the first '
                         f'date line_history can price)')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    a = ap.parse_args()
    since = max(a.since, LH_FLOOR)
    if since != a.since:
        print(f'  --since {a.since} is before line_history NCAAB coverage; '
              f'clamped to {since}')
    print(f'=== backfill_ncaab_receipt_prices · since {since} · '
          f'{"APPLY" if a.apply else "DRY"} ===')
    sys.exit(run(since, a.apply and not a.dry_run, a.limit))


if __name__ == '__main__':
    main()
