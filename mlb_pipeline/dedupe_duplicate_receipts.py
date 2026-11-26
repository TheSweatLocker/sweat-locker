#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VOID the extra copies where one wager was recorded in public_receipts twice.

== WHY THIS EXISTS (2026-11-25) ==========================================
ANDY: "fix and ddeup. fix root cause."

46 wagers are in the table twice -- 48 extra GRADED rows -- so every one
of those bets counts twice in its surface record. The root cause is fixed
in `public_receipt` (one `bet_identity`, one `screen_for_write`, shared by
both scheduled writers); this is the cleanup for the rows already written.

WHY VOID AND NOT DELETE. `public_receipts` is the frozen audit trail:
`pick_label`, `pick_line`, `market`, `pick_side`, `source_*`, `pick_odds`,
`tier`, `conviction` and `audit` are all refused by the freeze trigger of
20260918b, SILENTLY, with HTTP 200 and zero rows changed. `result` is
mutable, and `_classify` in compute_surface_records returns None for
anything that is not win/loss/push, so VOID takes a row out of every
record while leaving it visible. We really did write it twice; the record
should not count it twice, and the row should not disappear.

WHICH COPY SURVIVES, in order:
  1. one with a USABLE PRICE. An unpriced receipt cannot be graded for
     ROI at all, and the price is set-once so it cannot be repaired
     later. 2026-06-20 and 2026-07-31 are the cases: the earliest copy
     carries odds=None and the later one -136/-168.
  2. then one with a game_id, which can be joined to the results tables.
  3. then the LOWEST id -- first-publisher-wins, the same rule
     `frozen_quote` and the publish lock already use.

REFUSALS. A group whose copies disagree on the result is never touched:
that is a grading question, not a duplicate question, and picking a
winner would be inventing a result. `ledger` is excluded throughout --
a parlay's identity is its slip and two slips on one night legitimately
share a label.

Provenance goes in the REPO, not the row: `audit` is frozen too, so a
stamp written there is dropped while the PATCH reports success
([[feedback_receipt_audit_column_is_frozen_too]]).

EVERY WRITE IS READ BACK. A refused PATCH returns 200 with the field
unchanged, indistinguishable from success, and with
`Prefer: return=representation` the echoed body shows the REQUEST, not
the stored row ([[feedback_204_is_not_a_write]]).

    python dedupe_duplicate_receipts.py            # dry run, default
    python dedupe_duplicate_receipts.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# BEFORE importing public_receipt: it reads SUPABASE_URL/KEY at import
# time from the environment and does not load .env itself, so importing
# it first leaves SB=None and the first request fails on a None URL.
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from public_receipt import (                                   # noqa: E402
    bet_identity, bet_tuple_no_game, SB, KEY,
)

if not SB or not KEY:
    raise SystemExit('SUPABASE_URL / SUPABASE_KEY not set — refusing to run '
                     'blind against the receipt record')

H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = dict(H_READ, **{'Content-Type': 'application/json',
                          'Prefer': 'return=minimal'})
GRADED = ('win', 'loss', 'push')


def is_graded(r) -> bool:
    return str(r.get('result') or '').strip().lower() in GRADED


def read_all(since: str | None = None) -> list:
    """Every receipt, paged on the PRIMARY KEY.

    Never offset: offset paging on a non-unique order fabricates rows and
    blanks others ([[feedback_offset_paging_on_a_nonunique_key_fabricates]]).

    `since` bounds the READ, not just the report, so the nightly detector
    costs a handful of requests instead of a hundred. It must bound the
    read of the COMPARISON set too -- a window that hides one copy of a
    pair would report the other as unique and clean.
    """
    out, last = [], None
    while True:
        p = {'select': 'id,game_date,surface,sport,market,pick_label,'
                       'pick_line,pick_side,pick_odds,result,game_id,'
                       'source_id,matchup,published_at,graded_at',
             'order': 'id.asc', 'limit': '1000'}
        if since:
            p['game_date'] = f'gte.{since}'
        if last is not None:
            p['id'] = f'gt.{last}'
        r = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_READ,
                         params=p, timeout=120)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out += batch
        last = batch[-1]['id']
    return out


def priced(r) -> bool:
    try:
        return abs(float(r.get('pick_odds'))) >= 100
    except (TypeError, ValueError):
        return False


def keep_rank(r):
    """Sort key: the survivor sorts FIRST."""
    return (not priced(r), not bool(r.get('game_id')), r['id'])


def group_wagers(rows: list):
    """(groups, ambiguous) keyed by one wager, matching the live guard.

    Uses `public_receipt.bet_identity` so the cleanup and the guard cannot
    disagree about what one wager is. The matchup -> game_id map is built
    per (sport, surface, date) first: one wager can carry the id on one
    copy and only the name on the other, and keying each on what it
    happens to have is two key spaces, not one.
    """
    rows = [r for r in rows if str(r.get('surface')) != 'ledger']
    by_day = defaultdict(list)
    for r in rows:
        by_day[(str(r.get('sport')), str(r.get('surface')),
                str(r.get('game_date')))].append(r)
    groups, ambiguous = defaultdict(list), []
    for grp, members in by_day.items():
        gmap = {}
        for r in members:
            mu = ' '.join(str(r.get('matchup') or '').split()).lower()
            if mu and r.get('game_id'):
                gmap.setdefault(mu, str(r['game_id']))
        # games known for each game-less tuple, so a copy with neither a
        # game_id nor a matchup can still be placed -- but only when the
        # tuple has exactly ONE game. With two it is genuinely ambiguous
        # (2026-10-03 published two `Under 7.0` bets on different games)
        # and merging them would erase a real wager.
        games_of = defaultdict(set)
        for r in members:
            ident = bet_identity(r, gid_of_matchup=gmap)
            bt = bet_tuple_no_game(r)
            if ident and bt:
                games_of[bt].add(ident[-1])
        for r in members:
            ident = bet_identity(r, gid_of_matchup=gmap)
            if ident is None:
                bt = bet_tuple_no_game(r)
                known = games_of.get(bt) if bt else None
                if known and len(known) == 1:
                    ident = tuple(bt) + (next(iter(known)),)
                else:
                    if known and len(known) > 1:
                        ambiguous.append(r)
                    continue
            groups[ident].append(r)
    return groups, ambiguous


def void_row(rid, apply: bool) -> tuple[bool, str]:
    """PATCH result=VOID and READ IT BACK. (ok, detail)."""
    if not apply:
        return True, 'dry-run'
    try:
        resp = requests.patch(
            f'{SB}/rest/v1/public_receipts', headers=H_WRITE,
            params={'id': f'eq.{rid}'}, json={'result': 'VOID'}, timeout=30)
    except Exception as e:                                     # noqa: BLE001
        return False, f'{type(e).__name__}: {e}'
    if resp.status_code not in (200, 204):
        return False, f'HTTP {resp.status_code}: {resp.text[:140]}'
    # The status code cannot tell a refused write from a successful one.
    try:
        back = requests.get(f'{SB}/rest/v1/public_receipts', headers=H_READ,
                            params={'id': f'eq.{rid}', 'select': 'id,result'},
                            timeout=30).json()
    except Exception as e:                                     # noqa: BLE001
        return False, f'readback raised {type(e).__name__}: {e}'
    if not back:
        return False, 'readback returned no row'
    got = str(back[0].get('result') or '')
    if got.upper() != 'VOID':
        return False, f'readback says result={got!r} — the write was refused'
    return True, 'VOID confirmed'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='write; without it nothing is changed')
    ap.add_argument('--surface', help='limit to one surface')
    ap.add_argument('--since', help='bound BOTH the read and the report to '
                                    'game_date >= YYYY-MM-DD')
    ap.add_argument('--gate', action='store_true',
                    help='exit 1 when duplicates are found (detector mode)')
    args = ap.parse_args()

    rows = read_all(args.since)
    print(f'receipts read: {len(rows)}')
    groups, ambiguous = group_wagers(rows)

    plan, refused = [], []
    for ident, members in groups.items():
        g = [x for x in members if is_graded(x)]
        if len(g) < 2:
            continue
        if args.surface and ident[1] != args.surface:
            continue
        if args.since and ident[2] < args.since:
            continue
        if len({str(x.get('result')).strip().lower() for x in g}) > 1:
            refused.append((ident, g))
            continue
        g = sorted(g, key=keep_rank)
        plan.append((ident, g[0], g[1:]))

    print(f'\n=== {len(plan)} duplicate wager(s), '
          f'{sum(len(v) for _i, _k, v in plan)} extra graded row(s) to VOID ===')
    print('by surface:', Counter(i[1] for i, _k, v in plan
                                 for _ in range(len(v))).most_common())
    if refused:
        print(f'\n!! {len(refused)} group(s) REFUSED — copies disagree on the '
              f'result. That is a grading question, not a duplicate one:')
        for ident, g in refused:
            print(f'   {ident[2]} {ident[1]} {ident[4][:34]} '
                  f'{[(x["id"], x.get("result")) for x in g]}')
    if ambiguous:
        print(f'\n!! {len(ambiguous)} row(s) carry no game_id and no matchup '
              f'on a label spanning 2+ games — left alone, not guessed')

    print()
    ok = bad = 0
    done = []
    for ident, keep, drop in sorted(plan, key=lambda t: (t[0][2], t[0][1])):
        print(f'{ident[2]} {ident[1]:<11} {ident[0]:<6} '
              f'{str(keep.get("pick_label"))[:32]:<32} '
              f'KEEP id={keep["id"]} ({keep.get("result")}, '
              f'odds={keep.get("pick_odds")}, gid={bool(keep.get("game_id"))})')
        for d in drop:
            good, detail = void_row(d['id'], args.apply)
            print(f'      {"VOID" if good else "FAIL"} id={d["id"]} '
                  f'mkt={d.get("market")} src={str(d.get("source_id"))[:38]} '
                  f'-> {detail}')
            if good:
                ok += 1
            else:
                bad += 1
        done.append({'wager': list(ident), 'kept': keep['id'],
                     'voided': [d['id'] for d in drop],
                     'result': keep.get('result'),
                     'label': keep.get('pick_label')})

    print(f'\n{"APPLIED" if args.apply else "DRY RUN"}: '
          f'{ok} row(s) voided, {bad} failed, '
          f'{len(refused)} group(s) refused, {len(ambiguous)} ambiguous')
    if args.apply and done:
        # audit is frozen, so provenance lives in the repo
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d')
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '..', 'docs',
                            f'receipt_dedupe_{stamp}.json')
        with open(os.path.abspath(path), 'w', encoding='utf-8') as f:
            json.dump({'ran_at': datetime.now(timezone.utc).isoformat(),
                       'voided': ok, 'failed': bad,
                       'refused_disagree': [list(i) for i, _g in refused],
                       'groups': done}, f, indent=1)
        print(f'provenance written: {os.path.abspath(path)}')
    if args.gate and plan:
        print('*** DUPLICATE WAGERS FOUND. The screen in '
              'public_receipt.screen_for_write is meant to make this '
              'impossible, so a hit here means a NEW writer is bypassing it '
              '-- find the writer, do not just re-run with --apply. ***')
        return 1
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
