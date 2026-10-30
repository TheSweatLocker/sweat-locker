#!/usr/bin/env python3
"""Write the dog floor's own reason as the card subtitle and Jerry read.

== WHY (2026-10-29) ========================================================
apply_dog_floor replaces primary_play, and two pieces of copy are then left
describing the market it replaced:

  * primary_play['sub'], the card subtitle, which on a replaced moneyline
    pick reads "LAC +6.5 - LA - 70% vs 73% implied - -3.0pp edge" -- our own
    copy quoting a negative moneyline edge under a spread play we publish as
    a LEAN;
  * jerry_reads.short_read / long_read, whose prose argues the team we no
    longer take. jerry_pick_scrub catches that and falls back to the same
    stale `sub`, so the scrub alone does not fix it.

apply_dog_floor already stores the real reason in _dog_floor.reason. This
turns it into one user-facing sentence, so the card, the read and the pick
all say the same thing.

Run it immediately after apply_dog_floor, and before jerry_pick_scrub so the
scrub sees copy that already agrees with the pick.

The conviction clause is claimed ONLY where the ensemble actually had a
spread opinion on that side. Fade-only promotions (MIA +7, USC +6.5,
Maryland +2.5 on the first pass) carry the conviction of the market the
engine originally published, so asserting "our spread model lands here at
conviction 55" on those would be a misstatement.
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')
SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY'])
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json'}

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context'}


def copy_for(label, df, conv):
    """One user-facing sentence. No internal tier words, no stale ML edge."""
    src = str(df.get('source') or '')
    fav = df.get('fav_bets_pct')
    had_model = any('ensemble rl wants this dog' in str(x)
                    for x in (df.get('reason') or []))
    bits = []
    if src.startswith('fade') and fav is not None:
        try:
            bits.append(f'{float(fav):.0f}% of tickets sit on the favourite, '
                        f'so we are taking the points')
        except (TypeError, ValueError):
            pass
    if had_model and conv:
        bits.append(f'our spread model lands on this side at conviction {int(conv)}')
    elif had_model:
        bits.append('our spread model lands on this side')
    if not bits:
        bits.append('we are taking the points here')
    out = '; '.join(bits)
    return f'{label} — {out[0].upper()}{out[1:]}.'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CTX), help='default: both')
    ap.add_argument('--from-date', default=None,
                    help='earliest game_date (default today ET)')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    start = a.from_date or (dt.datetime.now(dt.timezone.utc)
                            - dt.timedelta(hours=4)).date().isoformat()
    sports = [a.sport] if a.sport else sorted(CTX)
    done = failed = 0
    for sp in sports:
        tbl = CTX[sp]
        rows = requests.get(
            f'{SB}/rest/v1/{tbl}?select=game_id,game_date,home_team,away_team,'
            f'primary_play&game_date=gte.{start}&limit=1000',
            headers=H_READ, timeout=60).json()
        if not isinstance(rows, list):
            print(f'  ! {sp} read failed: {str(rows)[:160]}')
            return 2
        for r in rows:
            pp = r.get('primary_play') or {}
            df = pp.get('_dog_floor')
            if not isinstance(df, dict):
                continue
            label = str(pp.get('label') or '').strip()
            body = copy_for(label, df, pp.get('conviction'))
            print(f'{sp} {r["away_team"]}@{r["home_team"]}')
            print(f'    {body}')
            if not a.apply:
                continue

            # --- the Jerry read -------------------------------------------
            rd = requests.get(f'{SB}/rest/v1/jerry_reads', headers=H_READ, params={
                'select': 'id', 'sport': f'eq.{sp}',
                'game_id': f'eq.{r["game_id"]}'}, timeout=20).json()
            if isinstance(rd, list) and rd:
                requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{rd[0]["id"]}',
                               headers=H_WRITE,
                               json={'short_read': body, 'long_read': body},
                               timeout=20)

            # --- the card subtitle, confirmed by READBACK -----------------
            # `sub` is not a field enforce_pick_lock compares, but a refused
            # PATCH returns 204 either way, so never trust the status code.
            npp = dict(pp)
            npp['sub'] = body
            requests.patch(f'{SB}/rest/v1/{tbl}?game_id=eq.{r["game_id"]}',
                           headers=H_WRITE, json={'primary_play': npp}, timeout=20)
            chk = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ, params={
                'select': 'primary_play',
                'game_id': f'eq.{r["game_id"]}'}, timeout=20).json()
            ok = (isinstance(chk, list) and chk
                  and (chk[0].get('primary_play') or {}).get('sub') == body)
            if ok:
                done += 1
            else:
                failed += 1
                print('       ! subtitle NOT written (readback disagrees)')
    print(f'\n  {done} updated' + (f' · {failed} FAILED' if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
