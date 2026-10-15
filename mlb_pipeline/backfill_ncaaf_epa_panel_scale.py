#!/usr/bin/env python3
"""Recompute epa_pred_spread at the fitted panelist scale.

== 2026-10-14 - WHY ==
ANDY: "i need models and overall engine updated before picks lock tomorrow for
ncaaf and nfl i want the new changes to be in affect."

epa_pred_spread was computed with K_PTS_EPA = 5.5 and came out at sd 1.30
against a market sd of 12.9 -- ratio 0.10. At that scale it is pinned near the
HFA, so `epa - market` takes its sign from the LINE and not from the game: it
fades whichever side is favoured, whatever the matchup says. panelist_registry
lists epa as NCAAF's one genuinely independent voice (50.7% side agreement with
projected), and it was unusable as one.

ncaaf_game_context.py now uses K_PTS_EPA_PANEL = 38.0, fitted on 332 graded
2026 games:

    net_epa sd                  0.2258
    actual_margin ~ net_epa     slope +38.92   corr +0.407
    market_line   ~ net_epa     slope -36.31   corr -0.511

agreeing with the 2026-09-26 variance-matching estimate of 37.8 that was
correctly refused at the time because it rested on n=10.

The context builder only recomputes rows when it runs, and NCAAF's next
scheduled run is Fri 2pm ET -- after which the Friday-night slate locks. This
backfills the column now so the fitted scale is in place for the games that are
about to lock, instead of arriving with them.

TOUCHES ONE COLUMN. epa_pred_spread only. It does NOT touch projected_spread:
the EPA fallback branch NULLs that by design (2026-10-10, "every single EPA
game is off the market by more than 20 points... a NULL is strictly safer than
a wrong number"), so the old constant never reached a published margin and the
new one does not either.

USAGE
    python backfill_ncaaf_epa_panel_scale.py --dry-run
    python backfill_ncaaf_epa_panel_scale.py --apply
    python backfill_ncaaf_epa_panel_scale.py --apply --all   # history too
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys

import requests

import ncaaf_game_context as N

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

SB = N.SB
H_READ = {'apikey': N.SB_KEY, 'Authorization': f'Bearer {N.SB_KEY}'} \
    if hasattr(N, 'SB_KEY') else None


def _hdrs():
    global H_READ
    if H_READ:
        return H_READ
    for attr in ('H_READ', 'SB_READ', 'HEADERS'):
        h = getattr(N, attr, None)
        if isinstance(h, dict) and 'apikey' in h:
            H_READ = h
            return h
    raise RuntimeError('cannot locate Supabase read headers on '
                       'ncaaf_game_context')


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    ap.add_argument('--all', action='store_true',
                    help='also recompute graded history (default: '
                         'game_date >= today only)')
    a = ap.parse_args()

    H = _hdrs()
    HW = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}
    params = {'select': 'game_id,game_date,home_team,away_team,neutral_site,'
                        'home_off_epa_pp,home_def_epa_pp,away_off_epa_pp,'
                        'away_def_epa_pp,epa_pred_spread,close_spread',
              'limit': '1000'}
    if not a.all:
        params['game_date'] = 'gte.2026-10-14'
    rows, off = [], 0
    while True:
        p = dict(params, offset=str(off))
        r = requests.get(f'{SB}/rest/v1/ncaaf_game_context', headers=H,
                         params=p, timeout=120)
        if r.status_code not in (200, 206):
            print(f'read failed {r.status_code}: {r.text[:200]}')
            return 1
        b = r.json()
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    print(f'{len(rows)} NCAAF rows in scope '
          f'({"all history" if a.all else "game_date >= 2026-10-14"})')
    print(f'K_PTS_EPA_PANEL = {N.K_PTS_EPA_PANEL}  '
          f'HOME_FIELD_PTS = {N.HOME_FIELD_PTS}')

    updates, old_vals, new_vals, mkts = [], [], [], []
    for x in rows:
        ho, hd = _f(x.get('home_off_epa_pp')), _f(x.get('home_def_epa_pp'))
        ao, ad = _f(x.get('away_off_epa_pp')), _f(x.get('away_def_epa_pp'))
        if None in (ho, ao):
            continue
        h_net = ho - (hd or 0)
        a_net = ao - (ad or 0)
        hfa = 0 if x.get('neutral_site') else N.HOME_FIELD_PTS
        new = round((h_net - a_net) * N.K_PTS_EPA_PANEL + hfa, 2)
        old = _f(x.get('epa_pred_spread'))
        if old is not None:
            old_vals.append(old)
        new_vals.append(new)
        cs = _f(x.get('close_spread'))
        if cs is not None:
            mkts.append(-cs)       # NCAAF close_spread is the HOME handicap
        if old is None or abs(old - new) > 0.005:
            updates.append({'game_id': x['game_id'], 'epa_pred_spread': new})

    def sd(v):
        return statistics.pstdev(v) if len(v) > 2 else float('nan')

    print(f'\nscale check:')
    print(f'  OLD epa_pred_spread  n={len(old_vals):<4} sd {sd(old_vals):7.3f}')
    print(f'  NEW epa_pred_spread  n={len(new_vals):<4} sd {sd(new_vals):7.3f}')
    print(f'  market (-close_spread) n={len(mkts):<4} sd {sd(mkts):7.3f}')
    if mkts and new_vals:
        print(f'  RATIO  old {sd(old_vals)/sd(mkts):.2f}  ->  '
              f'new {sd(new_vals)/sd(mkts):.2f}')
    print(f'\n{len(updates)} rows need updating')
    if a.dry_run:
        for u in updates[:5]:
            print(f"  would set {u['game_id']} -> {u['epa_pred_spread']:+.2f}")
        print('DRY RUN — nothing written')
        return 0

    ok = fail = 0
    for u in updates:
        gid = u['game_id']
        w = requests.patch(f'{SB}/rest/v1/ncaaf_game_context', headers=HW,
                           params={'game_id': f'eq.{gid}'},
                           data=json.dumps(
                               {'epa_pred_spread': u['epa_pred_spread']}),
                           timeout=60)
        if w.status_code in (200, 204):
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f'  PATCH {w.status_code}: {w.text[:200]}')
    print(f'patched {ok}, failed {fail}')

    # A 204 is not proof. Read it back and re-measure the ratio.
    chk, off = [], 0
    while True:
        p = dict(params, offset=str(off))
        r = requests.get(f'{SB}/rest/v1/ncaaf_game_context', headers=H,
                         params=p, timeout=120)
        b = r.json() if r.status_code in (200, 206) else []
        chk += b
        if len(b) < 1000:
            break
        off += 1000
    v = [_f(x.get('epa_pred_spread')) for x in chk]
    v = [y for y in v if y is not None]
    m = [-_f(x.get('close_spread')) for x in chk
         if _f(x.get('close_spread')) is not None]
    print(f'READ BACK: epa_pred_spread n={len(v)} sd {sd(v):.3f}  '
          f'market sd {sd(m):.3f}  ratio {sd(v)/sd(m):.2f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
