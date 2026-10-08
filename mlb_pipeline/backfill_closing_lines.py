"""Reconstruct the closing line for games that never got one, from the books.

WHY (2026-10-07, BACKLOG B59)
-----------------------------
`freeze_closing_lines.py` is the right mechanism — T-5min MLB, T-15min
NFL/NCAAF, idempotent, stamps `close_locked_at` — and it IS on a 10-minute
cron. But it is stamped on only 16 of 272 NFL rows (5.9%), 57 of 478 NCAAF
(11.9%) and 0 of 81 MLB, and its own dry run reports
`NFL: 92 unlocked game(s) older than 12h`. It refuses to guess a close after
the fact, which is correct behaviour for it.

We do not have to guess. `line_history` stores every book's quote with a
capture time, so the LAST quote captured before kickoff IS the close. This
reconstructs it, by median across books, and stamps `close_locked_at` so the
number is marked verified and nothing overwrites it again.

MEASURED BEFORE WRITING ANYTHING — the blast radius is small:

    sport   games that move   grades that flip
    NFL           7                 0
    NCAAF        60                 1

and 49 of the 60 NCAAF moves are under a point. The single NCAAF flip is a
MONEYLINE pick, which does not grade off the spread at all.

A FIRST ATTEMPT AT THAT MEASUREMENT SAID 19 FLIPS, with "corrections" like
North Carolina -36.5 -> +21.0 and a max change of 57.5 points. Every one was
`market_line.build_matcher` accepting a match when only ONE side resolved,
picking another school with a similar name on FCS-vs-FBS blowouts. The
fallback is gone and this script relies on the strict both-halves match; a
game that cannot be matched is reported as unmatched, never guessed.

WHAT IT WILL NOT DO
  * never touches a game with `close_locked_at` already set — that is a
    verified close and this must not second-guess it
  * never writes a number fewer than MIN_BOOKS books quoted before kickoff
  * never writes when the matchup cannot be resolved on BOTH team names
  * requires `--apply`; dry by default, because this writes to settled games

CLI
    python backfill_closing_lines.py --sport NFL            # dry
    python backfill_closing_lines.py --sport NFL --apply
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys
from datetime import date, timedelta
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

#: Minimum books that must have quoted before kickoff. Same floor as
#: refresh_market_lines: one book posting an alternate line is how the
#: original bad-number class got in.
MIN_BOOKS = 3

SPORTS = {
    'NFL':   dict(tbl='nfl_game_context',   spread='close_spread',
                  flip=True,  total='close_total'),
    'NCAAF': dict(tbl='ncaaf_game_context', spread='close_spread',
                  flip=False, total='close_total'),
    'MLB':   dict(tbl='mlb_game_context',   spread='close_spread',
                  flip=False, total='close_total'),
    'NHL':   dict(tbl='nhl_game_context',   spread='close_puckline',
                  flip=False, total='close_total'),
}


def page(t, p, cap=200000):
    out, off = [], 0
    while off < cap:
        q = dict(p)
        q['limit'] = '1000'
        q['offset'] = str(off)
        r = requests.get(f'{SB}/rest/v1/{t}', headers=H, params=q, timeout=180)
        if r.status_code not in (200, 206):
            print(f'  ! {t} {r.status_code} {r.text[:150]}')
            return out
        ch = r.json()
        if not isinstance(ch, list):
            return out
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000
    return out


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run(sport: str, since: str, apply: bool):
    cfg = SPORTS[sport]
    tbl, scol = cfg['tbl'], cfg['spread']
    has_lock = sport != 'NHL'          # NHL has no close_locked_at column

    cols = ['game_id', 'game_date', 'home_team', 'away_team', scol,
            cfg['total']]
    if has_lock:
        cols.append('close_locked_at')
    ctx = page(tbl, {'select': ','.join(cols), 'game_date': f'gte.{since}'})
    if not ctx:
        print(f'{sport}: no games since {since}')
        return collections.Counter()

    rows = ML.fetch_market(sport, 'spread', since)
    tot_rows = ML.fetch_market(sport, 'total', since)
    by_all = ML.index_by_matchup(rows)
    match = ML.build_matcher(by_all, sport)

    # kickoff per matchup, straight from line_history's own commence_time —
    # the same source as the quotes, so there is no second clock to disagree.
    kickoff = {}
    for r in rows:
        mt = str(r.get('matchup'))
        ct = str(r.get('commence_time') or '')
        if ct and (mt not in kickoff or ct < kickoff[mt]):
            kickoff[mt] = ct

    per_mt = collections.defaultdict(list)
    for r in rows:
        per_mt[str(r.get('matchup'))].append(r)
    per_mt_tot = collections.defaultdict(list)
    for r in tot_rows:
        per_mt_tot[str(r.get('matchup'))].append(r)

    print(f'=== {sport} ===')
    print(f'  {len(ctx)} games · {len(rows)} spread quotes · '
          f'{len(by_all)} matchups priced')

    stats = collections.Counter()
    diffs, writes = [], []
    today = date.today().isoformat()
    for z in ctx:
        if has_lock and z.get('close_locked_at'):
            stats['already locked'] += 1
            continue
        if str(z['game_date'])[:10] >= today:
            stats['not played yet — freezer will get it'] += 1
            continue
        mt = match(z['away_team'], z['home_team'])
        if not mt:
            stats['unmatched (not guessed)'] += 1
            continue
        ko = kickoff.get(mt)
        if not ko:
            stats['no kickoff time'] += 1
            continue
        pre = ML.index_by_matchup(per_mt[mt], as_of=ko)
        new_hl, nb = ML.home_line(pre, mt)
        if new_hl is None or nb < MIN_BOOKS:
            stats[f'under {MIN_BOOKS} books before kickoff'] += 1
            continue

        want = round(-new_hl if cfg['flip'] else new_hl, 2)
        cur = f(z.get(scol))
        patch = {}
        if cur is None or abs(cur - want) >= 0.25:
            patch[scol] = want
        pt = ML.index_totals(per_mt_tot.get(mt, []), as_of=ko).get(mt)
        if pt and pt[1] >= MIN_BOOKS:
            curt = f(z.get(cfg['total']))
            if curt is None or abs(curt - pt[0]) >= 0.25:
                patch[cfg['total']] = pt[0]
        if has_lock:
            patch['close_locked_at'] = ko       # the close is AS OF kickoff

        if cur is not None:
            d = abs(cur - want)
            diffs.append(d)
            stats['same number, stamping only' if d < 0.25
                  else f'moves {"<1" if d < 1 else "1-3" if d <= 3 else "3+"} pts'] += 1
        else:
            stats['was NULL — filling'] += 1
        writes.append((z, patch, cur, want, nb))

    for k, v in stats.most_common():
        print(f'    {k:<38}{v:>5}')
    if diffs:
        print(f'    median |move| {statistics.median(diffs):.2f}  '
              f'max {max(diffs):.1f}')

    big = [(z, p, c, w, n) for z, p, c, w, n in writes
           if c is not None and abs(c - w) > 3.0]
    if big:
        print(f'  -- {len(big)} moving more than 3 points, listed in full --')
        for z, p, c, w, n in big:
            print(f"    {str(z['game_date'])[:10]}  "
                  f"{z['away_team'][:18]:<18}@ {z['home_team'][:18]:<18}"
                  f"{scol} {c:+6.1f} -> {w:+6.1f}  ({n} books)")

    if not apply:
        print(f'  [dry] {len(writes)} rows would be written — pass --apply')
        print()
        return stats

    ok = 0
    for z, patch, _c, _w, _n in writes:
        r = requests.patch(f'{SB}/rest/v1/{tbl}?game_id=eq.{z["game_id"]}',
                           headers=H_WRITE, json=patch, timeout=90)
        if r.status_code in (200, 204):
            ok += 1
        else:
            print(f'    ! {z["game_id"]} {r.status_code} {r.text[:120]}')
    print(f'  WROTE {ok}/{len(writes)}')
    print()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default=None)
    ap.add_argument('--since', default='2026-08-01')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    sports = [args.sport.upper()] if args.sport else list(SPORTS)
    for s in sports:
        if s not in SPORTS:
            print(f'unknown sport {s}')
            continue
        run(s, args.since, args.apply)


if __name__ == '__main__':
    main()
