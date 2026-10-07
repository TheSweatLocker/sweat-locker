"""Make `close_spread` actually mean the close. Fixes stale lines at the source.

WHY (2026-10-07)
----------------
Andy: "is the current pick BAL -6 if so we need to change? I need the line
movement to assessed and not stale lines post as picks thats what i need just
fix the systems."

It was BAL -6. The books had BAL +3.5 — a 9.5-point swing with the favourite
flipping, almost certainly injury news. The pick named a number nobody could
take, and nothing on the card said the line had moved at all.

ROOT CAUSE: `<sport>_game_context.close_spread` is in practice the OPENING
line, never refreshed. Identical to `open_spread` on 263 of 314 NFL games
(83.8%), 69.1% NCAAF, 92.5% MLB. On next week's 14 NFL games it matched
`open_spread` 14/14 and the actual books 1/14.

Every published number derives faithfully from that column — primary_play.line,
the label, jerry_reads.call_line, the frozen receipt. So the pick builder was
never buggy; its input was the wrong column. And it was never a timing problem:
line_history had all 14 of those games priced before the reads ran.

WHY FIX IT HERE RATHER THAN IN EACH CONSUMER
`close_spread` is what every consumer already expects to be the current
market, and `recompute_<sport>_primary_play._normalize_label` ALREADY
re-derives a pick's label and line from it for any game that has not started.
So correcting this one column makes the picks self-heal and makes movement
computable as `close_spread - open_spread`, with no change to the pick logic
and no second opinion about what the line is.

SAFETY — what this will not do:
  * never touches a game that has already started (a line keeps moving after
    kickoff and after settlement; re-deriving a played game's number would
    restate the price we claim to have taken)
  * never touches primary_play, jerry_reads, or any receipt — it writes market
    columns only, and lets the existing normalizer do the pick
  * never writes a line the books never posted: a value is only accepted when
    at least MIN_BOOKS books quote it
  * fails closed per game — a game with no usable quotes is left exactly as it
    was rather than being nulled

CLI:
    python refresh_market_lines.py --dry-run           # all sports, report
    python refresh_market_lines.py --sport NFL
    python refresh_market_lines.py --days-ahead 14
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import market_line as ML

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                      # noqa: BLE001
        pass

SB, H = ML.SB, ML.H
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'return=representation'}

#: At least this many books must quote a number before it replaces what is
#: stored. One book posting an alternate line is exactly how the original
#: defect got in (game_context.py takes the FIRST bookmaker with a spread).
MIN_BOOKS = 3

#: A move this big is not ordinary shopping — it is news. Reported loudly and
#: counted, because surfacing it is the point of the exercise.
BIG_MOVE_PTS = 3.0

SPORTS = {
    # table, spread column, does the table store nflverse sign?, ml columns
    'NFL':   dict(tbl='nfl_game_context',   spread='close_spread',
                  flip=True,  home_ml='close_home_ml', away_ml='close_away_ml',
                  total='close_total'),
    'NCAAF': dict(tbl='ncaaf_game_context', spread='close_spread',
                  flip=False, home_ml='close_home_ml', away_ml='close_away_ml',
                  total='close_total'),
    'MLB':   dict(tbl='mlb_game_context',   spread='close_spread',
                  flip=False, home_ml='home_ml_close', away_ml='away_ml_close',
                  total='close_total'),
    'NHL':   dict(tbl='nhl_game_context',   spread='close_puckline',
                  flip=False, home_ml='close_home_ml', away_ml='close_away_ml',
                  total='close_total'),
}


def page(t, p, cap=200000):
    out, off = [], 0
    while off < cap:
        q = dict(p)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:160]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run(sport: str, days_ahead: int, dry: bool):
    cfg = SPORTS[sport]
    tbl, scol = cfg['tbl'], cfg['spread']
    today = date.today().isoformat()
    until = (date.today() + timedelta(days=days_ahead)).isoformat()

    cols = ['game_id', 'game_date', 'home_team', 'away_team', scol,
            'open_spread', cfg['total'], 'open_total',
            cfg['home_ml'], cfg['away_ml']]
    ctx = page(tbl, {'select': ','.join(cols),
                     'and': f'(game_date.gte.{today},game_date.lte.{until})'})
    if not ctx:
        print(f'{sport}: no unstarted games in {today}..{until}')
        return 0, 0

    sp_rows = ML.fetch_market(sport, 'spread', today)
    to_rows = ML.fetch_market(sport, 'total', today)
    ml_rows = ML.fetch_market(sport, 'ml', today)
    by_sp = ML.index_by_matchup(sp_rows)
    by_to = ML.index_totals(to_rows)
    by_ml = ML.index_prices(ml_rows)
    match = ML.build_matcher(by_sp, sport)

    print(f'=== {sport} ===')
    print(f'  {len(ctx)} unstarted games · {len(by_sp)} matchups priced '
          f'({len(sp_rows)} spread quotes)')

    changed = skipped = big = 0
    for z in sorted(ctx, key=lambda x: (str(x['game_date']), x['away_team'])):
        gid = str(z['game_id'])
        mt = match(z['away_team'], z['home_team'])
        if not mt:
            skipped += 1
            continue
        hl, nb = ML.home_line(by_sp, mt)
        if hl is None or nb < MIN_BOOKS:
            skipped += 1
            continue

        # The ONE conversion in this file. NFL's tables use the nflverse
        # convention (positive = home favoured); line_history and every other
        # context table carry the home handicap as the books publish it
        # (negative = home laying). Getting this backwards would invert every
        # pick in the sport, so it is a named flag rather than inferred.
        want_spread = round(-hl if cfg['flip'] else hl, 2)
        cur = _f(z.get(scol))
        patch = {}
        if cur is None or abs(cur - want_spread) >= 0.25:
            patch[scol] = want_spread

        tot = by_to.get(mt)
        if tot and tot[1] >= MIN_BOOKS:
            curt = _f(z.get(cfg['total']))
            if curt is None or abs(curt - tot[0]) >= 0.25:
                patch[cfg['total']] = tot[0]

        mls = by_ml.get(mt) or {}
        for key, side in ((cfg['home_ml'], 'home'), (cfg['away_ml'], 'away')):
            got = mls.get(side)
            if got and got[1] >= MIN_BOOKS:
                if _f(z.get(key)) != got[0]:
                    patch[key] = got[0]
        if not patch:
            continue

        op = _f(z.get('open_spread'))
        move = (want_spread - op) if op is not None else None
        tag = ''
        if move is not None and abs(move) >= BIG_MOVE_PTS:
            big += 1
            tag = f'   << {abs(move):.1f}-PT MOVE since open'
        g = f"{z['away_team']}@{z['home_team']}"
        shown = (f"{scol} {('%+.1f' % cur) if cur is not None else '-'}"
                 f" -> {want_spread:+.1f}" if scol in patch
                 else f'{scol} unchanged')
        print(f"  {str(z['game_date'])[:10]}  {g:<30}{shown:<34}"
              f"({nb} books){tag}")

        if dry:
            changed += 1
            continue
        r = requests.patch(f'{SB}/rest/v1/{tbl}?game_id=eq.{gid}',
                           headers=H_WRITE, json=patch, timeout=90)
        if r.status_code in (200, 204) and (r.status_code == 204 or r.json()):
            changed += 1
        else:
            print(f'    ! patch failed {r.status_code} {r.text[:140]}')
    print(f'  {"would update" if dry else "updated"} {changed} · '
          f'skipped {skipped} (unmatched or under {MIN_BOOKS} books) · '
          f'{big} games moved {BIG_MOVE_PTS}+ pts since open')
    print()
    return changed, big


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--days-ahead', type=int, default=10)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    sports = [args.sport] if args.sport else list(SPORTS)
    tc = tb = 0
    for s in sports:
        if s not in SPORTS:
            print(f'unknown sport {s}')
            continue
        c, b = run(s, args.days_ahead, args.dry_run)
        tc += c
        tb += b
    print(f'TOTAL {"would update" if args.dry_run else "updated"} {tc} games · '
          f'{tb} with a {BIG_MOVE_PTS}+ point move since open')


if __name__ == '__main__':
    main()
