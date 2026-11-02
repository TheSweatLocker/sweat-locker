"""Bring NCAAB live for tonight, via season_start rather than via `state`.

== 2026-11-02 ==============================================================
ANDY chose "go live, but capped" after this came up in the surface audit:

    sport_registry.NCAAB   state 'preseason'
                           season_start 2026-11-03
                           state_message 'NCAAB season starts Nov 3 ...'

while 119 NCAAB games tip TONIGHT (2026-11-02 ET) and the engine had already
built reads and badges for every one of them.

WHY THIS CHANGES season_start AND NOT state. auto_flip_sport_state.py
recomputes `state` from the dates every day:

    today < season_start                  -> preseason
    season_start <= today <= season_end   -> in_season
    today > season_end                    -> off_season

So writing state='in_season' today would be reverted by the next cron,
because today is still before season_start. Setting the DATE makes the
existing mechanism produce the right answer and keep producing it -- the
same reasoning as feedback_a_migration_can_be_live_then_reverted: judge on
what the live artefact will say tomorrow, not on what it says the moment
you write it.

It also clears state_message, which reads "NCAAB season starts Nov 3" and is
false the moment the sport is live. auto_flip does that itself on the
preseason -> in_season transition, so this just lets it.

WHAT IS CAPPED, so "live" does not mean "publishing a lock price".
Run _cap_ncaab_ml_tiers_1102.py first (done: 64 badges STRONG -> COVERAGE):

  * moneylines  NCAAB_ML_TIER_CAP = COVERAGE. Measured on 4,981 NCAAB
                favourite-ML plays: not one price band clears breakeven,
                from -100000..-1000 at -0.10% down to -150..-100 at -5.08%.
  * totals      NCAAB_TOTAL_TIER_CAP = COVERAGE, already in place.
  * props       banned for NCAAB by house rule
                (feedback_college_sports_no_props).
  * the fade    cannot see NCAAB at all: line_snapshot holds 0 NCAAB rows,
                ever. No public-split source exists for the sport.

So NCAAB appears with real numbers and no play tiers, which is the whole
point of a COVERAGE cap -- a shadow record accumulates and the gate can open
later on evidence.

  python _ncaab_go_live_1102.py
  python _ncaab_go_live_1102.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
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

NEW_START = '2026-11-02'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=== NCAAB go live - %s ===\n'
          % ('APPLY' if a.apply else 'DRY RUN'))

    r = requests.get(f'{SB}/rest/v1/sport_registry', headers=H,
                     params={'select': '*', 'sport': 'eq.NCAAB'}, timeout=60)
    if r.status_code != 200 or not r.json():
        print(f'  !! registry read failed HTTP {r.status_code} '
              f'{r.text[:200]}')
        return 1
    row = r.json()[0]
    print(f"  current: state={row.get('state')!r} "
          f"season_start={row.get('season_start')!r} "
          f"season_end={row.get('season_end')!r}")
    print(f"           state_message={str(row.get('state_message'))[:70]!r}")

    # Evidence that there really are games tonight, before moving the date.
    gm = requests.get(f'{SB}/rest/v1/ncaab_game_context', headers=H,
                      params={'select': 'game_id', 'game_date': f'eq.{NEW_START}'},
                      timeout=60)
    n_games = len(gm.json()) if gm.status_code == 200 else -1
    print(f'\n  ncaab_game_context rows on {NEW_START}: {n_games}')
    if n_games <= 0:
        print('  !! no games on that date - REFUSING to move season_start. '
              'Going live on a day with no slate is worse than staying dark.')
        return 1

    # And that nothing NCAAB can publish a play tier.
    ctx = requests.get(f'{SB}/rest/v1/ncaab_game_context', headers=H,
                       params={'select': 'primary_play',
                               'game_date': f'gte.{NEW_START}'}, timeout=60)
    import json as _json
    bad = []
    for c in (ctx.json() if ctx.status_code == 200 else []):
        pp = c.get('primary_play')
        if isinstance(pp, str):
            try:
                pp = _json.loads(pp)
            except Exception:                                  # noqa: BLE001
                pp = {}
        if str((pp or {}).get('tier') or '').upper() in ('PRIME', 'STRONG'):
            bad.append(pp)
    print(f'  forward NCAAB rows still carrying a PRIME/STRONG badge: '
          f'{len(bad)}')
    if bad:
        print('  !! REFUSING: run _cap_ncaab_ml_tiers_1102.py --apply first. '
              'Going live with a play-tier badge on an unmeasured market is '
              'the thing the cap exists to stop.')
        return 1

    if str(row.get('season_start')) == NEW_START:
        print('\n  season_start already correct - nothing to do.')
        return 0
    print(f"\n  would set season_start {row.get('season_start')!r} -> "
          f"{NEW_START!r} and clear state_message")
    if not a.apply:
        print('\n  DRY RUN - nothing written. Re-run with --apply.')
        return 0

    resp = requests.patch(f'{SB}/rest/v1/sport_registry', headers=H_W,
                          params={'sport': 'eq.NCAAB'},
                          json={'season_start': NEW_START,
                                'state_message': None}, timeout=60)
    back = requests.get(f'{SB}/rest/v1/sport_registry', headers=H,
                        params={'select': 'sport,state,season_start,'
                                          'state_message',
                                'sport': 'eq.NCAAB'}, timeout=60)
    got = back.json()[0] if (back.status_code == 200 and back.json()) else {}
    ok = str(got.get('season_start')) == NEW_START
    print(f'  {"OK" if ok else "x "} HTTP {resp.status_code}  readback '
          f'season_start={got.get("season_start")!r} '
          f'state={got.get("state")!r} '
          f'state_message={got.get("state_message")!r}')
    if not ok:
        return 1
    print('\n  now run: python auto_flip_sport_state.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
