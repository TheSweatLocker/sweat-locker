"""Replace NCAAB averaged close lines with numbers books actually posted.

== 2026-11-02 ==============================================================
Both NCAAB writers averaged across books:

    ncaab_game_context._avg_market_line   sum(vals) / len(vals)
    ncaab_odds_pull._consensus_spread     round(sum(vals) / len(vals), 2)

so the stored "market" columns hold numbers no book posted. Measured over
every context table, asking only "is this a multiple of 0.5":

    NFL     all four columns  ~316 rows   0 violations
    NBA     all four columns  ~114        0
    NHL     open_total         230        4
    NCAAF   close_total        718        9
    NCAAB   close_spread        75       33
    NCAAB   close_total         75       46   <-- 61%

The writers are fixed (posted_line.posted_consensus / posted_price). This
repairs the rows already stored, recomputing from the per-book quotes in
line_history -- the only place the individual book numbers survive.

HOW A ROW IS MATCHED. NCAAB ids cannot join: line_history mints
'ncaab_20261103_Milwaukee_USC', ctx stores an md5 hash. So the join is
(ET play date, canonical home, canonical away) through
ncaab_team_resolver, which covers 150 of 150 line_history names and 244 of
244 ctx names. 78 of 78 games matched when this was measured.

SIGN CONVENTION, VERIFIED NOT ASSUMED. ctx.close_spread is the HOME-side
line exactly as line_history posts it (negative = home favoured): checked
on all 78 comparable games, 77 agree and the 1 that does not is a board
genuinely split across zero, which posted_consensus now refuses rather than
resolving to a side. This is the check that caught me storing -abs(spread)
on NBA the same morning (feedback_magnitude_cannot_express_a_side).

REFUSALS, each counted and printed:
  * no quotes for the game                  -> skip
  * no consensus (board straddles zero)     -> skip, leave NULL
  * recomputed value is not a half-point    -> skip and SAY SO; that would
    mean the books really are on a quarter board and this script is not
    the place to decide that
  * spread and moneyline name different
    favourites                              -> skip the whole row

Every write is read back. Nothing is written without --apply.

  python _repair_ncaab_averaged_closes_1102.py
  python _repair_ncaab_averaged_closes_1102.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

for _l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}
H_W = dict(H, **{'Content-Type': 'application/json',
                 'Prefer': 'return=representation'})

sys.path.insert(0, str(Path(__file__).parent))
from ncaab_team_resolver import resolve, resolve_matchup     # noqa: E402
from posted_line import is_postable, posted_consensus, posted_price  # noqa: E402
from sweat_dates import et_date_of                            # noqa: E402

#: Only an unambiguous price gap counts as evidence of a favourite. At
#: -110/-110 "favourite" is not a real call, and the first cut of the NBA
#: version of this guard flagged two such rows as contradictions.
MIN_IMPLIED_GAP = 0.04


def implied(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    return (-o) / ((-o) + 100) if o < 0 else 100 / (o + 100)


def page(table, params):
    out, off = [], 0
    while off < 400000:
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=180,
                         params=dict(params, limit=1000, offset=off))
        if r.status_code != 200:
            print(f'  !! {table} HTTP {r.status_code} {r.text[:250]}')
            return out, False
        b = r.json()
        out += b
        if len(b) < 1000:
            return out, True
        off += 1000
    return out, False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--since', default='2026-11-01')
    a = ap.parse_args()
    print('=== repair NCAAB averaged closes - %s ===\n'
          % ('APPLY' if a.apply else 'DRY RUN'))

    lh, lfull = page('line_history',
                     {'select': 'matchup,commence_time,market,side,line,'
                                'price,captured_at,book',
                      'sport': 'eq.NCAAB',
                      'commence_time': f'gte.{a.since}T00:00:00Z'})
    if not lh:
        print('  !! 0 NCAAB quotes read - WIRING ERROR, not an empty board.')
        return 1
    if not lfull:
        print('  !! quote paging incomplete - refusing to act on a '
              'truncated read.')
        return 1
    idx = {}
    for q in lh:
        aw, hm = resolve_matchup(q.get('matchup'))
        if not aw or not hm:
            continue
        d = et_date_of(q.get('commence_time'))
        if d:
            idx.setdefault((d, hm, aw), []).append(q)
    print(f'line_history: {len(lh)} quotes (complete) -> {len(idx)} games')

    ctx, cfull = page('ncaab_game_context',
                      {'select': 'id,game_id,game_date,home_team,away_team,'
                                 'close_spread,close_total,home_ml_close,'
                                 'away_ml_close',
                       'game_date': f'gte.{a.since}'})
    if not ctx or not cfull:
        print('  !! ctx read failed or truncated. Stopping.')
        return 1
    print(f'ncaab_game_context: {len(ctx)} rows since {a.since} (complete)\n')

    why = Counter()
    plans = []
    for c in ctx:
        hm, aw = resolve(c['home_team']), resolve(c['away_team'])
        if not hm or not aw:
            why['team name does not resolve'] += 1
            continue
        qs = idx.get((str(c['game_date'])[:10], hm, aw))
        if not qs:
            why['no line_history quotes for that game'] += 1
            continue
        batch_t = max(str(q['captured_at']) for q in qs)
        batch = [q for q in qs if str(q['captured_at']) == batch_t]
        if len(batch) < 3:
            batch = [q for q in qs
                     if str(q['captured_at'])[:10] == batch_t[:10]] or qs

        sp = [q['line'] for q in batch
              if q.get('market') == 'spread' and str(q.get('side')) == 'home']
        tot = [q['line'] for q in batch
               if q.get('market') == 'total' and str(q.get('side')) == 'over']
        mlh = [q['price'] for q in batch
               if q.get('market') == 'ml' and str(q.get('side')) == 'home']
        mla = [q['price'] for q in batch
               if q.get('market') == 'ml' and str(q.get('side')) == 'away']

        fill, notes = {}, []
        for col, vals, fn in (('close_spread', sp, posted_consensus),
                              ('close_total', tot, posted_consensus),
                              ('home_ml_close', mlh, posted_price),
                              ('away_ml_close', mla, posted_price)):
            if not vals:
                continue
            new = fn(vals)
            if new is None:
                why[f'{col}: no consensus (board straddles zero)'] += 1
                continue
            if fn is posted_consensus and is_postable(new) is False:
                why[f'{col}: consensus itself is not a half-point'] += 1
                notes.append(f'{col} consensus {new} is not a half-point - '
                             f'left alone, the board may really be quarters')
                continue
            cur = c.get(col)
            if cur is not None and abs(float(cur) - float(new)) < 1e-9:
                continue
            fill[col] = new

        if not fill:
            why['already correct / nothing to change'] += 1
            continue

        # Coherence: the spread and the price must name the same favourite.
        _cs = fill.get('close_spread', c.get('close_spread'))
        _mh = fill.get('home_ml_close', c.get('home_ml_close'))
        _ma = fill.get('away_ml_close', c.get('away_ml_close'))
        ih, ia = implied(_mh), implied(_ma)
        if (_cs is not None and ih is not None and ia is not None
                and abs(ih - ia) >= MIN_IMPLIED_GAP
                and (float(_cs) < 0) != (ih > ia)):
            why['REFUSED: spread and moneyline name different favourites'] += 1
            print(f'  !! REFUSED {c["away_team"]} @ {c["home_team"]}: spread '
                  f'{_cs:+g} vs ml {_mh}/{_ma}')
            continue
        plans.append((c, fill, notes))

    print(f'rows to repair: {len(plans)}')
    print('reasons rows were skipped:')
    for k, v in why.most_common():
        print(f'   {v:4d}  {k}')
    print()
    for c, fill, notes in plans[:14]:
        cur = {k: c.get(k) for k in fill}
        print(f'  {c["game_date"]} {str(c["away_team"])[:22]:22s} @ '
              f'{str(c["home_team"])[:18]:18s}')
        for k in fill:
            print(f'       {k:14s} {cur[k]!r:>22s} -> {fill[k]!r}'
                  if isinstance(cur[k], str) else
                  f'       {k:14s} {str(cur[k]):>22s} -> {fill[k]}')
        for n in notes:
            print(f'       note: {n}')
    if len(plans) > 14:
        print(f'  ... and {len(plans)-14} more')

    if not a.apply:
        print('\n  DRY RUN - nothing written. Re-run with --apply.')
        return 0

    print('\n--- applying ---')
    ok = bad = 0
    for c, fill, _n in plans:
        resp = requests.patch(f'{SB}/rest/v1/ncaab_game_context', headers=H_W,
                              params={'id': f'eq.{c["id"]}'}, json=fill,
                              timeout=60)
        back = requests.get(f'{SB}/rest/v1/ncaab_game_context', headers=H,
                            params={'select': ','.join(fill),
                                    'id': f'eq.{c["id"]}'}, timeout=60)
        got = back.json()[0] if (back.status_code == 200 and back.json()) else {}
        good = all(got.get(k) is not None
                   and abs(float(got[k]) - float(v)) < 1e-9
                   for k, v in fill.items())
        ok += 1 if good else 0
        bad += 0 if good else 1
        if not good:
            print(f'   x id={c["id"]} HTTP {resp.status_code} wanted {fill} '
                  f'got {got}')
    print(f'\n  repaired {ok}, failed {bad}')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
