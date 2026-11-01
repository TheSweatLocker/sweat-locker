"""One bet, a potd receipt AND a market receipt. Void the sibling.

== 2026-11-01 ==
MIN +3.5 produced TWO live sweat_card receipts: market='potd' (odds -120,
no line) and market='rl' (line 3.0, odds -104). One wager, two rows, two
prices -- the same defect family as the 10-30 night that read 7-0 when the
card said 5-0.

public_receipt._bet_key cannot catch it. The potd row carries no game_id
(so _bet_key returns None and exempts it) and 'potd' != 'rl' anyway, so
both dedupe arms miss by construction.

generate_sweat_card now drops the football pick when the POTD already
holds that game, so the BLOB shows the bet once. Receipts are frozen, so
the row written before that fix is still live and still counted.

KEEP the potd row: it is the canonical POTD record and carries the price
Andy actually has (-120 at +3.5). Void the market sibling.

Scope is deliberately narrow -- same (sport, surface, game_date) and the
same pick_label, one potd row and one market row. Historically this is 3
of 108 live POTDs, so a blanket structural rule is NOT warranted without
Andy's call on how the record counts a POTD that is also a card pick.

  python _void_potd_market_sibling_1101.py            # dry
  python _void_potd_market_sibling_1101.py --apply
"""
from __future__ import annotations
import argparse, collections, os, sys
from pathlib import Path
import requests
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
for l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in l and not l.startswith('#'):
        k, v = l.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}
HW = dict(H, **{'Content-Type': 'application/json',
                'Prefer': 'return=representation'})
VOIDS = ('VOID', 'VOIDED', 'NO_ACTION')
D = '2026-11-01'

ap = argparse.ArgumentParser(); ap.add_argument('--apply', action='store_true')
A = ap.parse_args()

r = requests.get(SB + '/rest/v1/public_receipts', headers=H, timeout=120,
                 params={'select': 'id,sport,surface,game_date,market,'
                                   'pick_label,pick_line,pick_odds,result,'
                                   'source_id,audit',
                         'game_date': 'eq.' + D, 'limit': 500})
rows = [x for x in r.json()
        if str(x.get('result') or '').upper() not in VOIDS]
byk = collections.defaultdict(list)
for x in rows:
    byk[(x['sport'], x['surface'], x['game_date'],
         str(x.get('pick_label')))].append(x)

kill = []
for k, v in byk.items():
    potd = [y for y in v if str(y.get('market')) == 'potd']
    other = [y for y in v if str(y.get('market')) != 'potd']
    if len(potd) == 1 and len(other) == 1:
        kill.append((other[0], potd[0]))

print('=== %d live receipts on %s; one-bet-two-rows pairs: %d ==='
      % (len(rows), D, len(kill)))
for drop, keep in kill:
    print('  void id=%-9s %-5s %-5s %-12s line=%-6s odds=%-6s'
          % (drop['id'], drop['sport'], drop.get('market'),
             str(drop.get('pick_label'))[:12], drop.get('pick_line'),
             drop.get('pick_odds')))
    print('  keep id=%-9s %-5s %-5s %-12s line=%-6s odds=%-6s'
          % (keep['id'], keep['sport'], keep.get('market'),
             str(keep.get('pick_label'))[:12], keep.get('pick_line'),
             keep.get('pick_odds')))
if not kill:
    sys.exit(0)
if not A.apply:
    print('  dry run -- rerun with --apply'); sys.exit(0)

ok = 0
for drop, keep in kill:
    aud = drop.get('audit') if isinstance(drop.get('audit'), dict) else {}
    aud['void'] = {'at': D, 'by': '_void_potd_market_sibling_1101',
                   'reason': (f'one wager with two live rows: this '
                              f'{drop.get("market")} row and potd receipt '
                              f'{keep["id"]} are the same bet on the same '
                              f'game and date. Keeping the potd row, which '
                              f'carries the price actually available '
                              f'({keep.get("pick_odds")}). The card blob was '
                              f'fixed to emit the bet once; this row predates '
                              f'that and receipts are frozen.')}
    pr = requests.patch(SB + '/rest/v1/public_receipts', headers=HW,
                        timeout=60, params={'id': 'eq.%s' % drop['id']},
                        json={'result': 'VOID', 'audit': aud})
    b = requests.get(SB + '/rest/v1/public_receipts', headers=H, timeout=60,
                     params={'select': 'id,result', 'id': 'eq.%s' % drop['id']})
    got = (b.json() or [{}])[0].get('result') if b.status_code == 200 else None
    if pr.status_code in (200, 204) and str(got).upper() in VOIDS:
        ok += 1; print('  voided %s -> %s' % (drop['id'], got))
    else:
        print('  x void %s HTTP %s readback=%r'
              % (drop['id'], pr.status_code, got))
print('\n  voided %d/%d (verified by readback)' % (ok, len(kill)))
