"""Lower the stored NCAAB moneyline badges to COVERAGE.

== 2026-11-02 ==============================================================
ncaab_game_context.compute_primary_play minted a play-tier moneyline on most
of the board. These rows were invisible until today because the same file
filed every game under the JOB RUN DATE; correcting that put the real slate
on the board and the badges with it:

    primary_play type=ml on 2026-11-02:  69 rows
       tier STRONG 62,  LIGHT_LEAN 7
       61 carry a price: range -100000 .. -105, MEDIAN -10000
       50 of 61 worse than -300;  55 of 61 worse than -200
       ten at -100000 on 28.5-to-40.5-point favourites
       e.g. "Illinois ML lean", STRONG, spread -40.5

MEASURED, and it is not a juice problem. 4,981 NCAAB favourite-ML plays
with a close price and a final score, ROI at each row's own price:

    price band          n     win%   breakeven   edge pp     ROI
    -100000..-1000    930   95.70%     95.78%     -0.08    -0.10%
      -1000..-500     573   86.04%     87.13%     -1.10    -1.22%
       -500..-300     769   77.89%     79.00%     -1.11    -1.41%
       -300..-250     390   70.26%     73.40%     -3.14    -4.29%
       -250..-200     501   69.26%     69.23%     +0.03    +0.01%
       -200..-150     853   60.38%     63.26%     -2.88    -4.44%
       -150..-100     959   53.49%     56.37%     -2.88    -5.08%

Not one band clears breakeven, and the heavy end holds split-half by date
(-1.20% n=1136 early, -0.45% n=1136 late). And the engine's own selection
cannot be measured at all: ncaab_game_results carries projected_spread on
0 of 11,529 rows.

The writer is capped (NCAAB_ML_TIER_CAP). This lowers the rows already
stored so the badge does not keep claiming STRONG until the next ctx run.

WHAT IT DOES NOT DO:
  * does not change `type`, `side`, `label`, `sub` or `signal_floor` -- the
    pick stays visible with its side and number so a shadow record
    accumulates, exactly as NCAAB totals are handled
  * does not touch a row whose primary_play is not type 'ml'
  * does not touch LIGHT_LEAN: the capped writer cannot produce an ml at
    that tier, so those 7 are from an older run and will be regenerated
  * NOTHING had published -- public_receipts for NCAAB on 2026-11-02 holds
    0 rows on sweat_card, sharp_card, potd, dawg and game_read -- so this
    is not a change to any published play

It PATCHes ctx directly and so bypasses pick_lock.preserve_published. That
is deliberate and narrow: the lock exists to stop a later run rewriting a
PUBLISHED pick, and here nothing is published, no side moves, and the only
change is a tier going DOWN (feedback_tier_demotion_needs_stake_boundary --
a demotion with no published stake behind it cannot cost anything).

  python _cap_ncaab_ml_tiers_1102.py
  python _cap_ncaab_ml_tiers_1102.py --apply
"""
from __future__ import annotations

import argparse
import json
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
from ncaab_game_context import NCAAB_ML_TIER_CAP               # noqa: E402

DEMOTE_FROM = ('PRIME', 'STRONG')
WHY = ('NCAAB favourite ML is negative or flat in every price band, '
       'n=4,981 graded plays priced at their own close; and our own '
       'selection is unmeasurable (projected_spread 0 of 11,529 '
       'ncaab_game_results rows). Capped 2026-11-02.')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--date', default=None,
                    help='one ET date; default = every forward-dated row')
    a = ap.parse_args()
    print('=== cap NCAAB ml badges to %s - %s ===\n'
          % (NCAAB_ML_TIER_CAP, 'APPLY' if a.apply else 'DRY RUN'))

    params = {'select': 'id,game_id,game_date,home_team,away_team,'
                        'primary_play,home_ml_close,away_ml_close,'
                        'close_spread'}
    if a.date:
        params['game_date'] = f'eq.{a.date}'
    r = requests.get(f'{SB}/rest/v1/ncaab_game_context', headers=H,
                     params=dict(params, limit=1000), timeout=120)
    if r.status_code != 200:
        print(f'  !! HTTP {r.status_code} {r.text[:250]}')
        return 1
    rows = r.json()
    if not rows:
        print('  !! 0 rows read - WIRING ERROR, not an empty slate.')
        return 1
    print(f'ncaab_game_context rows read: {len(rows)}')

    seen, todo = Counter(), []
    for c in rows:
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except Exception:                                  # noqa: BLE001
                pp = {}
        pp = pp or {}
        if str(pp.get('type') or '').lower() != 'ml':
            continue
        tier = str(pp.get('tier') or '').upper()
        seen[tier] += 1
        if tier not in DEMOTE_FROM:
            continue
        side = str(pp.get('side') or '').upper()
        if side not in ('HOME', 'AWAY'):
            # These blobs have NO `side`: the 2026-10-30 fix added it to
            # ncaab_game_context.compute_primary_play and not to
            # ncaab_resolve_primary_play, which overwrites it. Recover it by
            # matching the label against THIS ROW'S OWN two team names --
            # label is f'{fav_team} ML...' where fav_team is literally one of
            # them, so this is an exact comparison against known values and
            # not vocabulary parsing. Ambiguous or no match -> leave it off
            # rather than guess a side
            # (feedback_parse_a_pick_by_shape_not_vocabulary).
            lbl = str(pp.get('label') or '')
            hits = [s for s, t in (('HOME', c.get('home_team')),
                                   ('AWAY', c.get('away_team')))
                    if t and lbl.startswith(str(t))]
            side = hits[0] if len(hits) == 1 else ''
        price = (c.get('home_ml_close') if side == 'HOME'
                 else c.get('away_ml_close') if side == 'AWAY' else None)
        new_pp = dict(pp)
        new_pp['tier'] = NCAAB_ML_TIER_CAP
        if side and not str(pp.get('side') or ''):
            new_pp['side'] = side
            new_pp['_side_recovered'] = (
                'derived 2026-11-02 by exact match of label against this '
                'row home_team/away_team; the resolver did not store it')
        new_pp['_ml_cap'] = {'from': tier, 'to': NCAAB_ML_TIER_CAP,
                             'why': WHY}
        todo.append((c, new_pp, price))

    print(f'ml badges by tier: {dict(seen)}')
    print(f'to demote: {len(todo)}\n')
    if todo:
        prices = [float(p) for _c, _p, p in todo if p is not None]
        if prices:
            prices.sort()
            print(f'  prices on the demoted rows: {prices[0]:+.0f} .. '
                  f'{prices[-1]:+.0f}   median '
                  f'{prices[len(prices)//2]:+.0f}')
            print(f'  worse than -300: '
                  f'{sum(1 for p in prices if p < -300)} of {len(prices)}')
    for c, _np, price in todo[:10]:
        pp = c.get('primary_play') or {}
        if isinstance(pp, str):
            pp = json.loads(pp)
        print(f'  {str(c["away_team"])[:20]:20s} @ '
              f'{str(c["home_team"])[:18]:18s} '
              f'{str(pp.get("label"))[:26]:26s} {pp.get("tier"):7s} -> '
              f'{NCAAB_ML_TIER_CAP}  price='
              f'{price if price is not None else "none":>8}  '
              f'spread={c.get("close_spread")}')
    if len(todo) > 10:
        print(f'  ... and {len(todo)-10} more')

    if not a.apply:
        print('\n  DRY RUN - nothing written. Re-run with --apply.')
        return 0

    print('\n--- applying ---')
    ok = bad = 0
    for c, new_pp, _p in todo:
        resp = requests.patch(f'{SB}/rest/v1/ncaab_game_context', headers=H_W,
                              params={'id': f'eq.{c["id"]}'},
                              json={'primary_play': new_pp}, timeout=60)
        back = requests.get(f'{SB}/rest/v1/ncaab_game_context', headers=H,
                            params={'select': 'primary_play',
                                    'id': f'eq.{c["id"]}'}, timeout=60)
        got = (back.json()[0].get('primary_play') or {}) if (
            back.status_code == 200 and back.json()) else {}
        if isinstance(got, str):
            try:
                got = json.loads(got)
            except Exception:                                  # noqa: BLE001
                got = {}
        good = (str(got.get('tier') or '').upper()
                == NCAAB_ML_TIER_CAP.upper()
                and str(got.get('side') or '') == str(new_pp.get('side') or '')
                and str(got.get('label') or '') == str(new_pp.get('label') or ''))
        ok += 1 if good else 0
        bad += 0 if good else 1
        if not good:
            print(f'   x id={c["id"]} HTTP {resp.status_code} '
                  f'readback tier={got.get("tier")!r} side={got.get("side")!r}')
    print(f'\n  demoted {ok}, failed {bad}')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
