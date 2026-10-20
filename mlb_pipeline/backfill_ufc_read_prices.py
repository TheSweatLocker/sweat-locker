"""Stamp the price onto historic UFC jerry_reads so the record can become ROI.

== 2026-10-19 - WHY ==
All 103 UFC rows in jerry_reads carried price_american NULL, every event from
08-01 through 10-24. The read book sits at 13-6 (68.4%) and that number could
not be turned into profit or loss.

That matters more in UFC than anywhere else in this app: it is a MONEYLINE
market, so breakeven moves with the price. For scale, the model's own PRIME
tier hits 63.2% at an average decimal of 1.43 - 69.7% implied - which is a
-13.5% ROI. A hit rate without a price is not a record.

ufc_picks already held the odds the whole time (odds_{a,b}_median,
odds_{a,b}_best, odds_book_count, odds_pulled_at). They were simply never
carried across. generate_ufc_fight_synthesis.py now attaches them at write
time; this backfills what already exists.

THE SIDE MATTERS. A read's call_side is the side we BACK, and on a FADE
verdict that is the OPPOSITE of ufc_picks.recommended_side. Pricing off
recommended_side would value every fade at the favourite's number and flatter
the whole book. This keys off call_side.

JOINED BY FIGHTER NAME, NOT fight_order. The two feeds do not number a card
the same way - 16 of 95 reads disagree, which is what booked a win as a loss
until f33b9ca5. The read's own input_snapshot carries both fighter names, so
that is the join, and a row that cannot resolve to exactly one fight is SKIPPED
rather than priced off a guess.

NOTHING HERE TOUCHES result. Only price_american / price_implied / price_best /
price_books_n / priced_at, and only where they are currently NULL.

USAGE
    python backfill_ufc_read_prices.py --dry-run
    python backfill_ufc_read_prices.py
"""
from __future__ import annotations
import argparse
import os
import sys
import json
import unicodedata
from datetime import datetime, timezone

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
for _l in (open(_ENV, encoding='utf-8') if os.path.exists(_ENV) else []):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _page(t, p):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, timeout=180,
                         params=dict(p, limit='1000', offset=str(off)))
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{t} -> {r.status_code}: {(r.text or "")[:200]}')
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _norm(name) -> str:
    n = unicodedata.normalize('NFKD', str(name or ''))
    n = ''.join(c for c in n if not unicodedata.combining(c))
    return ''.join(c for c in n.lower() if c.isalnum())


def _same(a, b) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _dec_to_american(dec):
    try:
        d = float(dec)
    except (TypeError, ValueError):
        return None
    if not (d > 1.0) or d != d:
        return None
    return int(round((d - 1.0) * 100)) if d >= 2.0 else int(round(-100.0 / (d - 1.0)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    reads = _page('jerry_reads', {
        'select': 'id,game_id,game_date,call_market,call_side,result,'
                  'price_american,input_snapshot',
        'sport': 'eq.UFC', 'order': 'game_date.asc'})
    picks = _page('ufc_picks', {
        'select': 'event_date,fighter_a,fighter_b,odds_a_median,odds_b_median,'
                  'odds_a_best,odds_b_best,odds_book_count,odds_pulled_at'})
    by_date = {}
    for p in picks:
        by_date.setdefault(str(p.get('event_date'))[:10], []).append(p)
    print(f'UFC reads {len(reads)} · ufc_picks rows {len(picks)}')

    todo, skipped = [], {}
    for r in reads:
        if str(r.get('call_market') or '').lower() != 'fight':
            skipped['pass/other market'] = skipped.get('pass/other market', 0) + 1
            continue
        side = str(r.get('call_side') or '').upper()
        if side not in ('A', 'B'):
            skipped['no call_side'] = skipped.get('no call_side', 0) + 1
            continue
        if r.get('price_american') is not None:
            skipped['already priced'] = skipped.get('already priced', 0) + 1
            continue
        snap = r.get('input_snapshot') or {}
        if isinstance(snap, str):
            try:
                snap = json.loads(snap)
            except (ValueError, TypeError):
                snap = {}
        fa, fb = snap.get('fighter_a'), snap.get('fighter_b')
        if not (fa and fb):
            skipped['no snapshot names'] = skipped.get('no snapshot names', 0) + 1
            continue
        cands = by_date.get(str(r.get('game_date'))[:10], [])
        hits = [p for p in cands
                if (_same(fa, p.get('fighter_a')) and _same(fb, p.get('fighter_b')))
                or (_same(fa, p.get('fighter_b')) and _same(fb, p.get('fighter_a')))]
        if len(hits) != 1:
            skipped[f'name match returned {len(hits)}'] = \
                skipped.get(f'name match returned {len(hits)}', 0) + 1
            continue
        pk = hits[0]
        # call_side A/B indexes the READ's snapshot ordering, so resolve it to
        # a NAME first and then read that fighter's price out of ufc_picks -
        # the two tables can order the pair differently.
        backed = fa if side == 'A' else fb
        if _same(backed, pk.get('fighter_a')):
            med, best = pk.get('odds_a_median'), pk.get('odds_a_best')
        elif _same(backed, pk.get('fighter_b')):
            med, best = pk.get('odds_b_median'), pk.get('odds_b_best')
        else:
            skipped['backed fighter not in pick row'] = \
                skipped.get('backed fighter not in pick row', 0) + 1
            continue
        am = _dec_to_american(med)
        if am is None:
            skipped['no median odds'] = skipped.get('no median odds', 0) + 1
            continue
        patch = {'price_american': am,
                 'price_implied': round(1.0 / float(med), 4),
                 'priced_at': pk.get('odds_pulled_at')
                 or datetime.now(timezone.utc).isoformat()}
        if pk.get('odds_book_count') is not None:
            patch['price_books_n'] = pk['odds_book_count']
        ab = _dec_to_american(best)
        if ab is not None:
            patch['price_best'] = ab
        todo.append((r, patch, backed, med))

    print(f'\nto price: {len(todo)}')
    for k, v in sorted(skipped.items(), key=lambda kv: -kv[1]):
        print(f'  skipped · {k}: {v}')
    print()
    for r, patch, backed, med in todo:
        print('  %s %-22s back %-22s dec %-5s -> %+d  (%s)'
              % (str(r['game_date'])[:10], str(r['game_id'])[-18:], str(backed)[:22],
                 med, patch['price_american'], r.get('result') or 'ungraded'))
    if a.dry_run:
        print('\n(dry run - nothing written)')
        return 0

    wrote = 0
    for r, patch, _b, _m in todo:
        pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{r["id"]}',
                            headers=H_W, json=patch, timeout=30)
        if pr.status_code in (200, 204):
            wrote += 1
        else:
            print(f'  x {r["id"]}: {pr.status_code} {(pr.text or "")[:160]}')
    # A 2xx is not proof of a write.
    back = _page('jerry_reads', {'select': 'id,price_american', 'sport': 'eq.UFC',
                                 'price_american': 'not.is.null'})
    print(f'\npatched {wrote} · UFC reads now carrying a price: {len(back)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
