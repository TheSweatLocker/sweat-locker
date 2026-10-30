#!/usr/bin/env python3
"""Cap how much of a published slate may be the favourite.

== WHY (2026-10-29) ========================================================
Andy: "they cant be bias like this again this week, it just cant... the
engine needs to land somewhere based on minimal edge if that the only thing
it cant be biased."

Measured on the actionable board (LEAN/STRONG/PRIME) for 10-30 onward:

    NCAAF   21 of 25 chalk = 84%     and Saturday 10-31 alone: 18 of 19 = 95%
    NFL     16 of 24 chalk = 67%     (8 dogs - far healthier)

WHY THIS IS A CAP AND NOT A FIX
The engine does not have dog opinions to promote. Every enabled NCAAF
spread signal that points at dogs was measured this session and NOT ONE
clears breakeven: ncaaf_experience_edge_early_away 38.2%, away_covers_as_dog
43.8%, ncaaf_ol_weight_adv_away 44.2%, ncaaf_away_spread_edge 46.9% - all
below the 52.4% an ATS bet needs. The surviving signals are home-flavoured
(ncaaf_ol_weight_adv_home 72% fav, home_ats_hot_at_home 77%,
ncaaf_ground_leverage_home 75%) because the away variants are the ones that
lose.

So balance cannot be created by promotion without inventing an opinion. It
can only be created by publishing LESS. This gate NEVER flips a side - that
would be fabrication. It demotes the WEAKEST chalk picks to COVERAGE until
the slate is inside the cap, lowest conviction first.

Publishing 18 chalk picks asserts 18 independent edges. We have nothing like
18. Publishing five is not a worse product than publishing eighteen; it is a
more honest one.
"""
from __future__ import annotations
import argparse, json, os, sys, collections
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')
SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY'])
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json'}

ACTIONABLE = ('LEAN', 'STRONG', 'PRIME')
# NFL close_spread is a home MARGIN (nflverse); the others are betting lines.
CTX = {'NFL': ('nfl_game_context', True), 'NCAAF': ('ncaaf_game_context', False),
       'NBA': ('nba_game_context', False), 'NHL': ('nhl_game_context', False)}


def _pp(row):
    pp = row.get('primary_play') or {}
    if isinstance(pp, str):
        try: pp = json.loads(pp)
        except Exception: return None
    return pp if isinstance(pp, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(CTX))
    ap.add_argument('--date', help='single game_date; default = all from today on')
    ap.add_argument('--max-chalk-pct', type=float, default=65.0,
                    help='max %% of side picks that may be the favourite')
    ap.add_argument('--min-side-picks', type=int, default=5,
                    help='never cut below this many side picks on a date')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    tbl, margin = CTX[a.sport]
    params = {'select': 'game_id,game_date,home_team,away_team,close_spread,primary_play',
              'order': 'game_date.asc'}
    if a.date: params['game_date'] = f'eq.{a.date}'
    else:      params['game_date'] = 'gte.' + __import__('datetime').date.today().isoformat()
    rows = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ, params=params,
                        timeout=60).json()
    bydate = collections.defaultdict(list)
    for r in rows:
        pp = _pp(r)
        if not pp or str(pp.get('tier')) not in ACTIONABLE: continue
        side = str(pp.get('side') or '')
        sp = r.get('close_spread')
        if side not in ('HOME', 'AWAY') or sp is None: continue
        home_fav = (float(sp) > 0) if margin else (float(sp) < 0)
        # ══ 2026-10-29 · A PRICED EDGE IS NEVER THE WEAKEST THING ON THE
        #                SLATE ════════════════════════════════════════════
        # The cap cuts the weakest chalk, but "weakest" cannot mean a pick
        # whose own model edge beats the price. After the LR coin-flip
        # overrule restored Iowa ML (+15.0pp), Air Force ML (+16.6pp) and
        # KC ML (+9.9pp), a conviction-ordered cap would have cut exactly
        # those -- the only picks on the board with a measured reason to
        # exist -- to satisfy a ratio. Positive measured edge is therefore
        # EXEMPT, and the cap operates on the rest.
        try:
            _e = pp.get('_model_edge_pp')
            _e = float(_e) if _e is not None else None
        except (TypeError, ValueError):
            _e = None
        if _e is not None and _e > 0:
            continue
        bydate[r['game_date']].append({
            'row': r, 'pp': pp, 'side': side,
            'chalk': (side == 'HOME') == home_fav,
            'conv': float(pp.get('conviction') or 0)})
    if not bydate:
        print(f'=== chalk cap · {a.sport} · no actionable side picks found ===')
        return 0

    print(f'=== apply_chalk_cap · {a.sport} · max {a.max_chalk_pct:.0f}% chalk · '
          f'floor {a.min_side_picks} side picks{"" if a.apply else " [DRY RUN]"} ===')
    total_demoted = 0
    for d in sorted(bydate):
        picks = bydate[d]
        chalk = [p for p in picks if p['chalk']]
        dogs = [p for p in picks if not p['chalk']]
        n = len(picks)
        pct = 100.0 * len(chalk) / n
        if pct <= a.max_chalk_pct:
            print(f'  {d}: {len(chalk)}C/{len(dogs)}D = {pct:.0f}% chalk - inside the cap')
            continue
        # how many chalk may stay so that chalk/(chalk+dogs) <= cap
        f = a.max_chalk_pct / 100.0
        keep = len(dogs) * f / (1 - f) if f < 1 else len(chalk)
        keep = int(keep)
        # honour the floor: never drop the slate below min-side-picks
        while keep + len(dogs) < a.min_side_picks and keep < len(chalk):
            keep += 1
        # Deterministic tie-break: conviction, then game_id, so the same
        # slate always yields the same cut rather than list order deciding
        # which of three LEAN-64s survives.
        drop = sorted(chalk, key=lambda p: (p['conv'], str(p['row']['game_id'])))[
            :max(0, len(chalk) - keep)]
        newpct = 100.0 * (len(chalk) - len(drop)) / max(1, n - len(drop))
        if not drop:
            print(f'  {d}: {len(chalk)}C/{len(dogs)}D = {pct:.0f}% chalk - the floor of '
                  f'{a.min_side_picks} side picks binds, so the cap cannot act. A slate '
                  f'with {len(dogs)} dog(s) cannot be balanced by subtraction.')
            continue
        print(f'  {d}: {len(chalk)}C/{len(dogs)}D = {pct:.0f}% chalk '
              f'-> demote {len(drop)} weakest chalk -> {newpct:.0f}% '
              f'({len(chalk)-len(drop)}C/{len(dogs)}D)')
        for p in drop:
            r = p['row']
            print(f"      demote {str(r['away_team'])[:16]:16s}@{str(r['home_team'])[:16]:16s} "
                  f"{p['side']:5s} {p['pp'].get('tier'):7s} conv={p['conv']:.0f} -> COVERAGE")
            if not a.apply: continue
            pp = dict(p['pp'])
            pp['_chalk_cap'] = {'was': pp.get('tier'), 'reason':
                                f'slate was {pct:.0f}% chalk, cap {a.max_chalk_pct:.0f}%'}
            pp['tier'] = 'COVERAGE'
            resp = requests.patch(f'{SB}/rest/v1/{tbl}?game_id=eq.{r["game_id"]}',
                                  headers=H_WRITE, json={'primary_play': pp}, timeout=20)
            if resp.status_code in (200, 204): total_demoted += 1
            else: print(f'        ! patch failed {resp.status_code}: {resp.text[:140]}')
    if a.apply: print(f'  demoted {total_demoted} pick(s) to COVERAGE')
    else:       print('  DRY RUN - nothing written')
    return 0


if __name__ == '__main__':
    sys.exit(main())
