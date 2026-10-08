"""Re-apply the defensive gates to STORED primary_play rows.

══ 2026-10-02 · WHY A POST-PASS AND NOT JUST THE CALL SITE ══
Every sport's context builder already calls apply_all_defensive_gates, and
the gates are correct — verified directly on the stored NE @ BUF row:

    apply_heavy_ml_spread_reroute  ->  BUF ML -305  =>  BUF -7
    apply_all_defensive_gates      ->  BUF -7, tier COVERAGE

But the live row still read "BUF ML, LEAN, -305" after a full rebuild. The
gates run on the IN-FLIGHT row mid-build, and the market columns
(close_home_ml, close_spread) are not reliably populated at that point — so
a price-dependent gate silently finds nothing to act on and returns the pick
untouched. `_any()` in apply_unpriced_market_gate fails OPEN by design when
no price column EXISTS (correct for NCAAB, which has none), and an in-flight
row that has not been given its price columns yet looks exactly like that.

Measured after rebuilding all three sports with locks bypassed:
    NFL    40 of 69 ML picks still past -200  (incl. DET ML -225 PRIME)
    NCAAF  13 of 29 still past -200           (incl. Michigan ML -225 STRONG)
    NHL    28 of 48 picks unpriced but at a published tier

That is the lesson from this session restated: I validated the gate on the
population where prices exist (stored rows) rather than the population the
change actually runs against (in-flight rows).

This pass reads rows back AFTER the build, when every column is populated,
and applies the same gate chain.

NOT BLINDLY IDEMPOTENT, WHICH IS WHY IT ONLY DEMOTES. apply_all_defensive_gates
includes the LR override, and that can RAISE a tier — the first dry run here
surfaced LEAN -> STRONG promotions on NBA and MLB rows. A promotion arriving
through a plumbing job is a model change with no before/after behind it, so
only reroutes, unpriced caps and tier DEMOTIONS are written. Promotions are
counted and refused. With that guard the pass is safe to run every cycle.

NEVER TOUCHES A STARTED GAME. A pick on a game already under way is the
historical record of what was published; only future games are re-gated.

Dry run unless --apply.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from collections import Counter
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
load_dotenv('.env')
SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
RH = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
WH = {**RH, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

TABLES = {
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NHL':   'nhl_game_context',
    'NBA':   'nba_game_context',
    'MLB':   'mlb_game_context',
}
# Field on each table that carries kickoff/first-pitch, for the started check.
START_COLS = ('kickoff_utc', 'game_time_utc', 'start_time_utc', 'commence_time')

# Tier ordering, best first. Used to tell a demotion from a promotion so this
# pass can refuse the latter.
_RANK = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2, 'COVERAGE': 3, 'PASS': 4,
         'SKIP': 4, 'NONE': 5}


def paged(tbl: str, params: dict) -> list:
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{tbl}', headers=RH,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=120)
        if r.status_code not in (200, 206):
            print(f'  ! {tbl} {r.status_code} {(r.text or "")[:140]}')
            return out
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _started(row: dict, now: datetime) -> bool:
    for c in START_COLS:
        v = row.get(c)
        if not v:
            continue
        try:
            dt = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        except ValueError:
            continue
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt <= now
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', help='limit to one sport')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--limit', type=int, default=80,
                    help='how many changes to print (default 80)')
    args = ap.parse_args()

    from defensive_gates import apply_all_defensive_gates

    now = datetime.now(timezone.utc)
    today = str(now.date())
    sports = ([args.sport.upper()] if args.sport else list(TABLES))
    grand = Counter()

    for sport in sports:
        tbl = TABLES.get(sport)
        if not tbl:
            continue
        rows = paged(tbl, {'select': '*', 'game_date': f'gte.{today}'})
        if not rows:
            continue
        changed, skipped_started, nopick, same = [], 0, 0, 0
        refused_promo = 0
        sub_fixed = 0
        for ctx in rows:
            pp = ctx.get('primary_play')
            if not isinstance(pp, dict) or not pp.get('type'):
                nopick += 1
                continue
            if _started(ctx, now):
                skipped_started += 1
                continue
            try:
                new = apply_all_defensive_gates(pp, ctx, sport=sport)
                # 2026-10-03 · the edge check runs AFTER the gates in every
                # context builder (see model_edge.apply_to_pick — it exists
                # separately because the LR path covers only lr_v1, and
                # ensemble_v2 produced 263 of 319 picks). This pass has to
                # mirror that order or a stored row never receives the cap:
                # the 10-03 non-positive-edge change would have been inert on
                # all 16 affected live picks without it.
                try:
                    from model_edge import apply_to_pick as _edge_apply
                    new = _edge_apply(new, ctx) or new
                except ImportError:
                    pass
            except Exception as e:
                print(f'  ! gate raised on {ctx.get("game_id")}: '
                      f'{type(e).__name__}: {e}')
                continue
            if not isinstance(new, dict):
                continue
            before = (pp.get('type'), pp.get('side'), pp.get('label'),
                      pp.get('tier'))
            after = (new.get('type'), new.get('side'), new.get('label'),
                     new.get('tier'))
            # 2026-10-02 · A STALE SUBTITLE IS A USER-VISIBLE DEFECT, so it
            # has to count as a change. This tuple was (type, side, label,
            # tier) only, which meant the pass reported CHANGED 0 on rows
            # whose `sub` still named the old market: Auburn @ Tennessee read
            # "Tennessee ML: Model projects 8.72-point edge" under a pick
            # labelled Tennessee -6.5. Comparing the subtitle's leading label
            # against the pick's own label catches exactly that, without
            # treating ordinary prose differences as changes.
            def _stale_ml_sub(d):
                """True only for the ML->spread reroute artifact.

                NARROW ON PURPOSE. A first attempt rewrote any subtitle whose
                prefix differed from the label, which would have CORRUPTED two
                live rows: PHI ML and Missouri ML carry
                    "⚠ Downgraded to LEAN: MC sim at 42%"
                — an intentional, user-facing gate disclosure that merely
                happens to contain a colon. Rewriting it to "PHI ML: MC sim at
                42%" would delete the warning.

                So the test is the artifact's exact shape: the subtitle leads
                with "<team> ML" while the pick is no longer a moneyline, and
                the team still matches. Anything else is left alone.
                """
                s, lb = d.get('sub'), d.get('label')
                if not isinstance(s, str) or not lb or ':' not in s:
                    return False
                pre, lb = s.split(':', 1)[0].strip(), str(lb).strip()
                if not pre.endswith(' ML') or lb.endswith(' ML'):
                    return False
                return lb.startswith(pre[:-3].strip())

            # REPAIR, don't just detect. apply_heavy_ml_spread_reroute fixes
            # the subtitle when it reroutes, but a row rerouted on an earlier
            # run returns early from that gate (already 'rl'), so its stale
            # subtitle can only be repaired here.
            if _stale_ml_sub(new):
                _s = new['sub']
                new = dict(new)
                new['sub'] = f"{new['label']}:{_s.split(':', 1)[1]}"
                sub_fixed += 1

            if before == after and new.get('sub') == pp.get('sub'):
                same += 1
                continue
            # 2026-10-02 · DEMOTIONS AND REROUTES ONLY.
            # apply_all_defensive_gates includes the LR override, which can
            # RAISE a tier — so re-running the chain is not idempotent for
            # those paths. The first dry run surfaced LEAN -> STRONG
            # promotions on NBA and MLB rows. This pass exists to apply
            # defect fixes to stored rows, not to re-score the board, so a
            # promotion is refused: it would be a model change arriving
            # through a plumbing job, with no before/after behind it.
            if not (new.get('_heavy_ml_reroute') or new.get('_unpriced_market')):
                if _RANK.get(str(after[3]).upper(), 9) < \
                        _RANK.get(str(before[3]).upper(), 9):
                    refused_promo += 1
                    continue
            changed.append((ctx, pp, new, before, after))

        print(f'\n=== {sport} === {len(rows)} forward rows · no pick {nopick} '
              f'· started/skipped {skipped_started} · unchanged {same} '
              f'· refused promotions {refused_promo} · subtitle fixes {sub_fixed}'
              f' · CHANGED {len(changed)}')
        # 2026-10-07: was changed[:12]. This is the only review surface for a
        # pass that rewrites stored picks, and on the first NFL run it hid 41
        # of 53 changes behind "… and 41 more" — so nobody could see what they
        # were approving. --limit keeps output sane on a huge catch-up run.
        for ctx, pp, new, b, a in changed[:args.limit]:
            rr = ' [reroute]' if new.get('_heavy_ml_reroute') else ''
            up = ' [unpriced]' if new.get('_unpriced_market') else ''
            print(f"   {str(ctx.get('away_team'))[:13]:13s}@"
                  f"{str(ctx.get('home_team'))[:13]:13s} "
                  f"{b[0]}/{str(b[2])[:18]:18s}/{b[3]:8s} -> "
                  f"{a[0]}/{str(a[2])[:18]:18s}/{a[3]}{rr}{up}")
        if len(changed) > 12:
            print(f'   … and {len(changed) - 12} more')

        grand['changed'] += len(changed)
        grand['started'] += skipped_started

        if args.apply and changed:
            ok = blocked = 0
            for ctx, pp, new, b, a in changed:
                gid = str(ctx['game_id'])
                q = urllib.parse.quote(gid, safe='')
                # ══ TWO-STEP, AND VERIFIED BY READ-BACK ══
                # enforce_pick_lock() is a DB trigger: it refuses a
                # primary_play LABEL change on a stamped game and RETURNS 204
                # anyway. Clearing the stamp in the SAME statement is also
                # refused, because the trigger compares labels first and that
                # branch writes OLD.pick_locked_at straight back.
                #
                # So the stamp must be cleared in its own statement, then the
                # pick written. This is the escape hatch 20260926b documented
                # and 20260929d actually made work; the clear is logged to
                # pick_lock_drift, so "who unlocked this and when" stays a
                # query rather than a guess.
                #
                # Every write is then READ BACK. A 204 from PostgREST means
                # "statement ran", not "value changed" — trusting it is how an
                # earlier pass in this session reported patched=103 while the
                # database discarded all 103.
                requests.patch(f'{SB}/rest/v1/{tbl}?game_id=eq.{q}',
                               headers=WH, json={'pick_locked_at': None},
                               timeout=40)
                requests.patch(f'{SB}/rest/v1/{tbl}?game_id=eq.{q}',
                               headers=WH, json={'primary_play': new},
                               timeout=40)
                # Re-stamp: the corrected pick is now the published one and
                # deserves the same protection the old one had.
                requests.patch(
                    f'{SB}/rest/v1/{tbl}?game_id=eq.{q}', headers=WH,
                    json={'pick_locked_at':
                          datetime.now(timezone.utc).isoformat()}, timeout=40)
                rb = requests.get(f'{SB}/rest/v1/{tbl}', headers=RH, params={
                    'select': 'primary_play', 'game_id': f'eq.{gid}'},
                    timeout=40)
                got = (rb.json() or [{}])[0].get('primary_play') or {} \
                    if rb.status_code == 200 else {}
                if (got.get('type'), got.get('label')) == (a[0], a[2]):
                    ok += 1
                else:
                    blocked += 1
                    if blocked <= 3:
                        print(f'  ! {gid}: write did NOT stick — still '
                              f'{got.get("type")}/{got.get("label")}')
            print(f'   patched {ok}/{len(changed)}'
                  + (f'  ✖ BLOCKED {blocked}' if blocked else ''))
            grand['patched'] += ok
            grand['blocked'] += blocked

    print(f'\n{"=" * 56}')
    print(f'changed {grand["changed"]} · patched {grand["patched"]} · '
          f'BLOCKED {grand["blocked"]} · skipped started {grand["started"]}')
    if not args.apply:
        print('[dry] nothing written — pass --apply')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
