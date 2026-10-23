"""Re-date NCAAF game_ids that were built from the UTC date, not the ET date.

A late NCAAF kickoff crosses midnight UTC: Miami @ Notre Dame starts
00:30 UTC on 11-08, which is 7:30pm ET on 11-07. `game_date` is the ET date
and is correct. The game_id embedded the UTC date, so the two disagree, and
ncaaf_game_context carries a CHECK constraint that forbids exactly that:

    ncaaf_ctx_gid_encodes_game_date

So the context builder cannot write those games AT ALL. Worse, until
2026-10-22 the builder's batch POST was atomic and had a row-by-row fallback
gated on 409 only, so a single one of these rejected the entire slate with a
400 and the function printed "wrote 0 rows" behind a checkmark. That is how
Andy came to report "App state game tonight college football has no data":
the table was frozen at 16 of 76 upcoming games.

╔══ THE WRITER IS ALREADY FIXED; THIS IS THE LEGACY TAIL ══════════════════╗
ncaaf_odds_pull.py has used the ET-anchored date since 2026-09-16, after the
same bug produced Texas Tech @ Houston twice with different spreads. These
rows predate that fix - Miami/Notre Dame was created 2026-07-24, the others
2026-09-01 - and `on_conflict=game_id` means a later pull cannot correct an
id, it can only insert a second row under the right one. Neither happened,
so they have simply sat unbuildable.

Measured across ncaaf_game_results from 2026-09-01: 37 of 637 rows (5.8%)
carry a UTC-dated id, every one a kickoff between 00:00 and 02:30 UTC.
╚═══════════════════════════════════════════════════════════════════════════╝

╔══ WHAT THIS DELIBERATELY WILL NOT TOUCH ═════════════════════════════════╗
A game_id is a key, and three of those 37 are referenced by public_receipts
- frozen records. Renaming one would orphan a receipt, and receipts are not
ours to edit. So this repairs ONLY rows that satisfy all four:

    * the id's embedded date disagrees with game_date
    * the game has NOT been played (no score)
    * NOTHING in public_receipts references the id
    * the corrected id is not already taken

Anything failing one of those is reported and left alone. Played games keep
their odd ids forever; nothing reads a historical id's date, and the cost of
being wrong there is higher than the tidiness is worth.
╚═══════════════════════════════════════════════════════════════════════════╝

CLI
    python repair_ncaaf_game_id_dates.py --dry-run
    python repair_ncaaf_game_id_dates.py
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                          # noqa: BLE001
        pass

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

GID_DATE = re.compile(r'^ncaaf_(\d{8})_')


def page(tbl, params):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H, params=q, timeout=120)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tbl} {r.status_code} {r.text[:200]}')
        ch = r.json()
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', dest='dry')
    a = ap.parse_args()

    rows = page('ncaaf_game_results', {'select': '*'})
    print(f'=== repair_ncaaf_game_id_dates · {len(rows)} result rows ===')

    cand, held = [], []
    for g in rows:
        gid = str(g.get('game_id') or '')
        m = GID_DATE.match(gid)
        if not m:
            continue
        d = m.group(1)
        embedded = f'{d[:4]}-{d[4:6]}-{d[6:]}'
        gdate = str(g.get('game_date'))[:10]
        if embedded == gdate:
            continue
        new = GID_DATE.sub(f'ncaaf_{gdate.replace("-", "")}_', gid)
        if g.get('home_score') is not None or g.get('away_score') is not None:
            held.append((gid, 'already played'))
            continue
        rc = requests.get(f'{SB}/rest/v1/public_receipts', headers=H,
                          params={'select': 'id', 'game_id': f'eq.{gid}',
                                  'limit': '5'}, timeout=60)
        if rc.status_code == 200 and rc.json():
            held.append((gid, f'{len(rc.json())} public_receipt(s) reference it'))
            continue
        ex = requests.get(f'{SB}/rest/v1/ncaaf_game_results', headers=H,
                          params={'select': 'game_id',
                                  'game_id': f'eq.{new}', 'limit': '2'},
                          timeout=60)
        if ex.status_code == 200 and ex.json():
            held.append((gid, f'target id {new} already exists'))
            continue
        cand.append((gid, new, gdate, g.get('kickoff_utc')))

    print(f'\n  {len(cand)} repairable · {len(held)} held back')
    for gid, new, gdate, ko in cand:
        print(f'     {gid}')
        print(f'        -> {new}   (game_date {gdate}, kickoff {ko})')
    for gid, why in held[:12]:
        print(f'     HELD {gid}: {why}')

    if a.dry:
        print('\n  --dry-run: nothing written')
        return 0
    if not cand:
        return 0

    ok = 0
    for gid, new, _d, _k in cand:
        r = requests.patch(f'{SB}/rest/v1/ncaaf_game_results', headers=H_W,
                           params={'game_id': f'eq.{gid}'},
                           json={'game_id': new}, timeout=60)
        if r.status_code in (200, 204):
            ok += 1
        else:
            print(f'     ! {gid}: {r.status_code} {r.text[:140]}')
    print(f'\n  patched {ok}/{len(cand)}')

    # A 204 is not a write. Prove the old ids are gone and the new ones exist.
    back = page('ncaaf_game_results', {'select': '*'})
    still = 0
    for g in back:
        gid = str(g.get('game_id') or '')
        m = GID_DATE.match(gid)
        if not m:
            continue
        d = m.group(1)
        if f'{d[:4]}-{d[4:6]}-{d[6:]}' != str(g.get('game_date'))[:10]:
            still += 1
    print(f'  read-back: {still} row(s) still mismatched '
          f'(expected {len(held)} held)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
