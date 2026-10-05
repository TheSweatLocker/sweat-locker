#!/usr/bin/env python3
"""Fill the blank price and matchup columns on public_receipts.

WHY (2026-10-04)
----------------
Andy: "What do you mean null on every receipt?" — fair, it was not every
receipt. Measured across 11,797 receipts since 09-01, 88.7% carry a price.
The hole is specific:

    prop_jerry     9710/9731   99.8%
    sharp_card      561/592    94.8%
    ledger          179/179   100.0%
    potd             19/32     59.4%
    game_read         0/895     0.0%   <-
    sweat_card        0/319     0.0%   <-
    daily_degen       0/28      0.0%   <-
    dawg              0/21      0.0%   <-

So the Sweat Card — the home screen — stores no prices at all, and neither
do the game reads. A win-loss record without prices cannot say whether the
money went up: 5-3 on -250 favourites loses, 5-3 on +120 dogs wins well.

PRICE BASIS IS ALWAYS RECORDED
------------------------------
`jerry_reads.priced_at` runs from 50h BEFORE the read was generated to 264h
AFTER it, which looks alarming — a price stamped days after kickoff would
not be a price anyone could bet. It was worth checking rather than assuming:
line_history, the only source feeding these prices, holds 342,822 quotes
since 09-20 and **ZERO** captured after commence_time. The collector stops
at kickoff, so no live in-game line can reach this column. The 95 late-
stamped rows all came from one price_picks backfill on 10-03, carry 4-19
books of consensus, and run median -116 with nothing outside -400..+400.

So `priced_at` does not separate real prices from fake ones. It separates
WHEN WE WROTE THE PRICE DOWN, and that distinction is worth keeping:

  publish — stamped at or around publish time. What we could have taken.
            jerry_reads.price_american, prop book_over/under_odds.
  close   — the latest pre-commence consensus, written later by a backfill,
            or mlb_game_results.home_ml_close. A real pre-game number, but
            NOT the price we published at.

Both are honest; only one is ours. Every row this touches gets
audit.price_basis and audit.price_source so no consumer can confuse them,
and a price with no timestamp at all is refused as unknown provenance.
Nothing is guessed: a receipt whose price cannot be established stays NULL
and is reported in the not-recoverable breakdown.

NEVER OVERWRITES. Only fills columns that are currently NULL, and verifies
each write by reading the value back (a 204 is not a write).

    python backfill_receipt_prices.py --since 2026-09-01
    python backfill_receipt_prices.py --since 2026-09-01 --apply
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, os, sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}

# A price stamped later than this past midnight on the game's date cannot be
# a pre-game price. 31h covers a late West-coast start plus the ET offset.
PREGAME_GRACE_H = 31

PROP_TABLES = {'MLB': 'mlb_pipeline_props', 'NFL': 'nfl_pipeline_props',
               'NHL': 'nhl_pipeline_props', 'NBA': 'nba_pipeline_props'}


def page(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        p = dict(params)
        p.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=120, params=p)
        if r.status_code != 200:
            print(f'  ⚠ {table} fetch {r.status_code}: {r.text[:140]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def _iso(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None


def _basis(priced_at, game_date):
    """Which price basis a jerry_reads price qualifies as, or None to refuse.

    Both bases below are PRE-GAME prices. Verified 2026-10-04: line_history —
    the only source feeding jerry_reads prices — holds 342,822 quotes since
    09-20 and ZERO of them were captured after commence_time. The collector
    stops at kickoff, so no live in-game line can reach this column.

    What `priced_at` actually distinguishes is when WE recorded the price:

      publish — stamped at or around publish time; what we could have taken.
      close   — stamped later by a price_picks backfill, which takes the
                latest pre-commence quote per book. That is the closing
                consensus: a real number, pre-game, but not the price we
                published at.

    A price with no timestamp at all is refused — unknown provenance.
    """
    pa = _iso(priced_at)
    gd = _iso(str(game_date) + 'T00:00:00+00:00')
    if pa is None or gd is None:
        return None
    return 'publish' if pa <= gd + dt.timedelta(hours=PREGAME_GRACE_H) else 'close'


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # An American price is never between -100 and +100 exclusive, and 0 is the
    # shape an empty numeric column takes. Refuse rather than record nonsense.
    if -100 < f < 100:
        return None
    return int(round(f))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-09-01')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    print(f'=== backfill_receipt_prices · since {args.since} · '
          f'{"APPLY" if args.apply else "DRY"} ===\n')

    recs = page('public_receipts', {'select': '*',
                                    'game_date': f'gte.{args.since}'})
    print(f'receipts: {len(recs)}')

    # ---- sources -----------------------------------------------------
    jr_by_id, jr_by_game = {}, collections.defaultdict(list)
    for j in page('jerry_reads', {'select': 'id,sport,game_date,game_id,'
                                            'call_market,price_american,'
                                            'priced_at,input_snapshot->>matchup',
                                  'game_date': f'gte.{args.since}'}):
        jr_by_id[str(j['id'])] = j
        if j.get('game_id'):
            jr_by_game[(str(j['game_id']), str(j.get('call_market') or ''))].append(j)
    print(f'jerry_reads: {len(jr_by_id)}')

    props = {}
    for sport, tbl in PROP_TABLES.items():
        for p in page(tbl, {'select': 'player_name,prop_type,prop_line,'
                                      'book_over_odds,book_under_odds,game_date',
                            'game_date': f'gte.{args.since}'}):
            key = (str(p.get('player_name') or '').strip().lower(),
                   str(p.get('prop_type') or '').strip().lower(),
                   str(p.get('prop_line')))
            props.setdefault(key, p)
    print(f'prop rows indexed: {len(props)}')

    mlb_res = {}
    for g in page('mlb_game_results', {'select': 'game_id,home_team,away_team,'
                                                 'home_ml_close,away_ml_close',
                                       'game_date': f'gte.{args.since}'}):
        mlb_res[str(g['game_id'])] = g
    print(f'mlb results indexed: {len(mlb_res)}')

    potd = {}
    for b in page('daily_best_bet_history', {'select': 'bet_date,odds_american',
                                             'bet_date': f'gte.{args.since}'}):
        if b.get('odds_american') is not None:
            potd[str(b['bet_date'])] = b['odds_american']
    print(f'potd prices indexed: {len(potd)}\n')

    # ---- resolve -----------------------------------------------------
    price_fixes, matchup_fixes = [], []
    why = collections.Counter()

    def _jr_price(j, rec):
        """(price, basis, reason) — basis is 'publish' or 'close'."""
        p = _num(j.get('price_american'))
        if p is None:
            return None, None, 'source row has no price'
        b = _basis(j.get('priced_at'), rec['game_date'])
        if b is None:
            return None, None, 'price carries no timestamp — provenance unknown'
        return p, b, None

    for rec in recs:
        surface = str(rec.get('surface') or '')
        market = str(rec.get('market') or '')
        sid = str(rec.get('source_id') or '')
        stbl = str(rec.get('source_table') or '')

        # --- matchup, for the surfaces that left it blank -------------
        if not str(rec.get('matchup') or '').strip() and stbl == 'jerry_reads':
            j = jr_by_id.get(sid)
            m = (j or {}).get('matchup')
            if m and '@' in str(m):
                matchup_fixes.append((rec, str(m)))

        # --- price ----------------------------------------------------
        if rec.get('pick_odds') is not None:
            why['already priced'] += 1
            continue

        got = basis = src = None
        reason = 'no price source for this surface/market'

        if stbl == 'jerry_reads':
            j = jr_by_id.get(sid)
            if not j:
                reason = 'source row missing'
            else:
                got, basis, reason = _jr_price(j, rec)
                src = 'jerry_reads.price_american'

        elif stbl == 'jerry_cache.sweat_card' and ':' in sid:
            # "rl:<jerry game_id>" — same read, so the same publish price
            mk, _, gid = sid.partition(':')
            cands = jr_by_game.get((gid, mk)) or []
            if not cands:
                reason = 'no jerry read for that game+market'
            else:
                got, basis, reason = _jr_price(cands[0], rec)
                src = 'jerry_reads.price_american'

        elif stbl in PROP_TABLES.values() and sid.startswith('prop:'):
            body = sid[len('prop:'):]
            parts = body.split('|')
            if len(parts) != 3:
                reason = 'unparseable prop source_id'
            else:
                nm, ty, ln = parts
                p = props.get((nm.strip().lower(), ty.strip().lower(), ln))
                if not p:
                    reason = 'prop row not found at that line'
                elif ty.endswith('_over'):
                    got = _num(p.get('book_over_odds'))
                    basis, src = 'publish', f'{stbl}.book_over_odds'
                    reason = 'prop row has no over price' if got is None else None
                elif ty.endswith('_under'):
                    got = _num(p.get('book_under_odds'))
                    basis, src = 'publish', f'{stbl}.book_under_odds'
                    reason = 'prop row has no under price' if got is None else None
                else:
                    reason = 'prop_type carries no direction'

        elif stbl == 'mlb_game_results' and market == 'ml' and ':' in sid:
            # Closing moneyline — a real number, but NOT our published price.
            gid = sid.split(':', 1)[1]
            g = mlb_res.get(gid)
            lbl = str(rec.get('pick_label') or '').lower()
            if not g:
                reason = 'results row missing'
            else:
                side = None
                if str(g.get('home_team') or '').lower()[:12] in lbl:
                    side = 'home'
                elif str(g.get('away_team') or '').lower()[:12] in lbl:
                    side = 'away'
                if side is None:
                    reason = 'cannot tell which team the label names'
                else:
                    got = _num(g.get(f'{side}_ml_close'))
                    basis, src = 'close', f'mlb_game_results.{side}_ml_close'
                    reason = 'results row has no closing ML' if got is None else None

        elif stbl == 'daily_best_bet_history' and sid.startswith('potd:'):
            d = sid[len('potd:'):]
            got = _num(potd.get(d))
            basis, src = 'publish', 'daily_best_bet_history.odds_american'
            reason = 'POTD row never recorded a price' if got is None else None

        if got is None:
            why[f'{surface}/{market or "-"}: {reason}'] += 1
            continue
        price_fixes.append((rec, got, basis, src))

    # ---- report ------------------------------------------------------
    print(f'=== PRICES recoverable: {len(price_fixes)} ===')
    by = collections.defaultdict(lambda: collections.Counter())
    for rec, got, basis, _s in price_fixes:
        by[rec['surface']][basis] += 1
    for s in sorted(by):
        print(f'   {s:<14} {dict(by[s])}')
    print(f'\n=== MATCHUP recoverable: {len(matchup_fixes)} ===')

    print('\n=== NOT recoverable (stays NULL, by reason) ===')
    for k, n in why.most_common():
        if k == 'already priced':
            continue
        print(f'   {n:5d}  {k}')
    print(f'\n   (already priced: {why["already priced"]})')

    if not args.apply:
        print('\n--- sample price fills ---')
        for rec, got, basis, src in price_fixes[:10]:
            print(f'   {rec["game_date"]} {rec["sport"]:<5} {rec["surface"]:<11} '
                  f'{rec["market"]:<6} {str(rec["pick_label"])[:30]:<30} '
                  f'{got:+5d}  [{basis}] {src}')
        print(f'\nre-run with --apply')
        return 0

    ok = bad = 0
    for rec, got, basis, src in price_fixes:
        aud = rec.get('audit')
        if isinstance(aud, str):
            try:
                aud = json.loads(aud)
            except Exception:
                aud = None
        aud = dict(aud) if isinstance(aud, dict) else {}
        aud.update({'price_basis': basis, 'price_source': src,
                    'price_backfilled_at': dt.datetime.now(dt.timezone.utc).isoformat()})
        r = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W, timeout=60,
                           params={'id': f'eq.{rec["id"]}',
                                   'pick_odds': 'is.null'},
                           data=json.dumps({'pick_odds': got, 'audit': aud}))
        body = r.json() if r.content else []
        if r.status_code not in (200, 204) or not body:
            print(f'   x id={rec["id"]} {r.status_code} {r.text[:100]}')
            bad += 1
            continue
        if _num(body[0].get('pick_odds')) != got:
            print(f'   x REFUSED id={rec["id"]} came back '
                  f'{body[0].get("pick_odds")!r}, wanted {got}')
            bad += 1
            continue
        ok += 1
    print(f'\nprices written {ok}/{len(price_fixes)} (verified by read-back), {bad} failed')

    mok = mbad = 0
    for rec, m in matchup_fixes:
        r = requests.patch(f'{SB}/rest/v1/public_receipts', headers=H_W, timeout=60,
                           params={'id': f'eq.{rec["id"]}', 'matchup': 'is.null'},
                           data=json.dumps({'matchup': m}))
        body = r.json() if r.content else []
        if r.status_code in (200, 204) and body and body[0].get('matchup') == m:
            mok += 1
        else:
            mbad += 1
    print(f'matchups written {mok}/{len(matchup_fixes)}, {mbad} failed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
