#!/usr/bin/env python3
"""Void receipts frozen on a call we did not publish, and write the right one.

== WHY (2026-10-28) ========================================================
179 of 1,982 game_read receipts (9.0%) grade the OPPOSITE side of the
jerry_reads row their own source_id names. e.g. 10-27: badge and read both
say `Florida Panthers PL`; the receipt says `Winnipeg Jets ML` and graded
LOSS. 148 of the 179 were published before the read's current generated_at
-- the receipt froze an earlier vintage, and pick_side / pick_line /
pick_label are immutable by trigger, so it can never catch up.

Migration 20261025a exists precisely to make this repairable: it replaced the
full unique constraint with a PARTIAL index (WHERE result IS NULL OR
lower(result) <> 'void'), so a voided row stops occupying its key and the
correct side can be inserted beside it. Nothing has used that until now.

== 2026-11-04 · B144: IT NOW COVERS A MOVED MARKET TOO =====================
The 10-28 version repaired a wrong SIDE and deliberately reported a moved
MARKET without touching it. Closing B144 means repairing both, because the
app renders jerry_reads -- the read IS what we published, so a receipt
naming a market the read does not name is wrong about what we published in
exactly the way a wrong side is. Re-measured on LIVE game_read receipts
against the jerry_reads row each one cites by source_id (4,455 of 4,457
citations resolve):

    receipt backs the OPPOSITE outcome       54   26W-28L
    receipt names a DIFFERENT MARKET        163   87W-56L  (143 graded)
    receipt agrees with its read          1,796

The old selector saw only 61 of those 163, because it required a side
mismatch before it would even look at the market -- so a market change that
kept the side token ('Portland Trail Blazers ML' against a read of
'Portland Trail Blazers -7.5') was invisible to it.

AND THE CAUSE IS NOT AN OVERRIDE. B144 was written up as "we publish
against our own read". It is not a second opinion; it is one mutable row
read at two different times. jerry_reads is UPDATED IN PLACE, the receipt
freezes whichever vintage it finds, and receipt.published_at is a copy of
the read's generated_at at write time -- so published_at != the read's
current generated_at is direct evidence the read moved afterwards:

    read moved after the receipt froze     market diverged   85.5%
                                           side diverged     66.7%
                                           agreed            41.4%

The 10-22 future-date guard in backfill_public_receipts closed most of it:
ZERO side divergences on games after 10-22. What it left open is the
same-day case its own comment predicted -- the reconstruction runs on game
day before that sport's read generator has produced the day's final read,
which is why the market class still reaches 2026-11-04 while the side class
stops on 10-27.

So the remedy is not to stop writing pre-game receipts; it is to CLOSE THE
LOOP by running this reconciliation on a schedule once the reads are final.
It was never scheduled -- it ran once by hand on 10-28 (41 replacements) and
158 live divergences accumulated behind it.

== THE GUARD THAT MAKES THIS HONEST ========================================
A repair like this can quietly rewrite history toward whatever we would say
now. So a receipt is only replaced when the read's `generated_at` precedes
the game's START time -- i.e. the side being written was on record before
anyone could know the outcome. Start times, measured present:

    NFL / NCAAF  kickoff_utc        100% populated
    NHL          commence_time      100%
    NBA          commence_time_utc
    MLB          none on ctx        -> 34 receipts cannot be judged, SKIPPED

Scope on the 179:
    read finalised BEFORE start   120   <- repairable
    read regenerated AFTER start   25   <- LEFT ALONE. The receipt is the
                                           honest record of what we published.
    MLB, no start time             34   <- LEFT ALONE, reported

== IT MAKES OUR RECORD WORSE, WHICH IS THE POINT =========================
Grading the 120 replacements against stored results:

    current receipts      52 W   50 L   14 VOID   4 pending     -0.37u
    replacements          45 W   48 L                            -3.98u
                                                          delta  -3.61u

A repair that IMPROVES the published record would be a red flag. The frozen
sides happened to be slightly luckier than the sides we actually published.

== 2026-11-04 · AND ON THE B144 PASS, ONE CLASS IMPROVES IT ===============
That red-flag rule was written for a side-only repair, and it is WRONG for
one of the three classes. Reported per class for exactly this reason -- the
pooled figure (-6.14u) averages them and hides it:

    MARKET only (61)   +5.07u -> -9.65u   delta -14.72u
    BOTH        (45)   -6.03u -> +2.55u   delta  +8.58u   <- improves

Checked rather than waved through, because "the repair makes us look
better" is the signature of rewriting history:

  MARKET only is 60 gradeable rows, 54 of them ml -> rl, EVERY one on the
  same side token, and 46 of 60 keep the same outcome. The 14 that change
  are the team winning without covering -- 'Dallas Stars ML WIN' becoming
  'Dallas Stars -1.5 LOSS'. So the receipts were crediting us with the
  EASIER version of our own bet: 43W-17L (71.7%) on labels we never
  published, against 29W-31L on the calls we did. That is the overstatement
  this item exists to remove.

  BOTH is 42 gradeable rows and 23 of them name the OPPOSITE TEAM. For an
  opposite-side pick the frozen result is the COMPLEMENT of the right one --
  17 of those 23 invert -- so if the wrong side lost, the right side won.
  The improvement is ARITHMETIC, not selection: a repair that did not move
  the record against the frozen side would be the real anomaly. The rule
  "the repair must look worse" only holds where the frozen row is a
  DIFFERENT bet; where it is the losing half of the same game, the opposite
  is forced.

The honest test is therefore not the sign of the delta. It is the pre-start
guard below: the call being written was on record before anyone could know
the outcome.

== WHAT IT WILL NOT DO ====================================================
  * it never voids a receipt whose replacement cannot be graded (27 of the
    120). Voiding without a replacement removes a real play from the
    denominator, which is the one thing worse than a wrong side.
  * it never touches a receipt whose read changed AFTER the game started.
  * it never touches a game that has not STARTED yet -- gen < start proves
    the call was on record pre-kickoff, not that the read has stopped
    moving, and a live game's read can be regenerated again before tip.
  * it never writes a 'pass' as a replacement. A read withdrawing its pick
    later does not unmake the pick we published.
  * it never writes a replacement the shared receipt guards refuse
    (recover_line / unactionable / receipt_invariants) -- and it runs those
    BEFORE the void, so a refusal leaves the original standing.
  * it never touches a non-game_read surface.
  * every new row carries an audit note naming the receipt it replaces, and
    capture_mode 'reconstructed' -- it is not claiming to be a live capture.

    python reconcile_stale_side_receipts.py
    python reconcile_stale_side_receipts.py --apply
    python reconcile_stale_side_receipts.py --sport NHL --apply
    python reconcile_stale_side_receipts.py --skip-markets --apply
"""
from __future__ import annotations
import argparse
import collections
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_outcome import CLOSE_HOME_FAV_SIGN           # noqa: E402

START = {'NFL': ('nfl_game_context', 'kickoff_utc'),
         'NCAAF': ('ncaaf_game_context', 'kickoff_utc'),
         'NBA': ('nba_game_context', 'commence_time_utc'),
         'NHL': ('nhl_game_context', 'commence_time'),
         # ══ 2026-11-04 · `game_time_et` IS NOT ET ══
         # The column name says ET; every one of the 210 populated rows is a
         # timestamptz carrying +00:00, e.g. game_date 2027-03-06 with
         # game_time_et 2027-03-07T01:30:00+00:00 — 8:30pm ET, stored as UTC.
         # Checked before using it, because the pre-start guard below is a
         # STRING comparison against jerry_reads.generated_at (UTC): a column
         # that really held local ET would make every late game look
         # pre-start by the offset and quietly authorise repairs that are not
         # pre-game at all (feedback_hardcoded_et_offset_breaks_in_est).
         'NCAAB': ('ncaab_game_context', 'game_time_et')}
RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'NBA': 'nba_game_results', 'NHL': 'nhl_game_results',
           'NCAAB': 'ncaab_game_results'}
SPREAD_COL = {'NHL': 'close_puckline'}


def _env(path):
    out = {}
    try:
        for line in open(path, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_E = _env(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = (_E.get('SUPABASE_SERVICE_ROLE_KEY') or _E.get('SUPABASE_KEY')
       or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
       'Content-Type': 'application/json'}


def req(method, path, params=None, body=None, extra=None):
    u = BASE + path
    if params:
        u += '?' + urllib.parse.urlencode(params, safe='.,()*:')
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(u, data=data, method=method)
    for k, v in HDR.items():
        r.add_header(k, v)
    for k, v in (extra or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return (json.loads(raw) if raw.strip() else None), resp.status
    except urllib.error.HTTPError as e:
        return e.read().decode('utf-8', 'replace')[:240], e.code


def paged(table, select='*', order='game_date.asc', **filt):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off),
             'order': order}
        p.update(filt)
        rows, st = req('GET', table, p)
        if not isinstance(rows, list):
            sys.exit('read of %s failed (status %s) -- refusing to write'
                     % (table, st))
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def _n(v):
    s = str(v or '').upper()
    if s in ('H', 'HOME'):
        return 'HOME'
    if s in ('A', 'AWAY'):
        return 'AWAY'
    return s if s in ('OVER', 'UNDER') else None


def _now_utc():
    """ISO-8601 UTC, same shape as the stored timestamps it is compared to."""
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec='seconds'))


def _mkt(v):
    """One market name per bet, so 'pl'/'puckline'/'spread' stop reading as
    three different markets than 'rl'. Returns '' when there is nothing to
    compare -- an absent market is not a disagreement."""
    m = str(v or '').strip().lower()
    return {'pl': 'rl', 'puckline': 'rl', 'spread': 'rl', 'runline': 'rl',
            'ou': 'total', 'totals': 'total'}.get(m, m)


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def grade(sport, side, market, res, read_line=None):
    """The replacement's result, or None when it cannot be settled."""
    if not res:
        return None
    hs, as_ = _f(res.get('home_score')), _f(res.get('away_score'))
    if hs is None or as_ is None:
        return None
    m = str(market or '').lower()
    if m in ('pl', 'puckline', 'spread'):
        m = 'rl'
    if side in ('HOME', 'AWAY') and m == 'ml':
        if hs == as_:
            return None
        return 'WIN' if ((hs > as_) == (side == 'HOME')) else 'LOSS'
    if side in ('HOME', 'AWAY') and m == 'rl':
        ln = _f(read_line)
        if ln is None:
            col = SPREAD_COL.get(sport, 'close_spread')
            raw = _f(res.get(col))
            sgn = CLOSE_HOME_FAV_SIGN.get(sport)
            if raw is None or sgn is None:
                return None
            h = raw * sgn * -1.0
        else:
            # call_line is the PICKED side's own handicap
            h = ln if side == 'HOME' else -ln
        adj = (hs - as_) + h
        if adj == 0:
            return 'PUSH'
        return 'WIN' if ((adj > 0) == (side == 'HOME')) else 'LOSS'
    if side in ('OVER', 'UNDER') and m == 'total':
        t = _f(res.get('close_total'))
        if t is None:
            return None
        p = hs + as_
        if p == t:
            return 'PUSH'
        return 'WIN' if ((p > t) == (side == 'OVER')) else 'LOSS'
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--sport', choices=sorted(START))
    ap.add_argument('--skip-markets', action='store_true',
                    dest='skip_markets',
                    help='repair only opposite-side receipts, leaving a '
                         'moved MARKET alone (the pre-B144 behaviour)')
    a = ap.parse_args()

    print('=' * 100)
    print('RECONCILE STALE-SIDE game_read RECEIPTS  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 100)

    reads = {str(x['id']): x for x in paged(
        'jerry_reads',
        'id,sport,game_id,game_date,call_market,call_side,call_line,'
        'call_text,conviction,generated_at,price_american')}
    rec = paged('public_receipts', '*', surface='eq.game_read')
    print('  game_read receipts %d   jerry_reads %d' % (len(rec), len(reads)))

    starts, results = {}, {}
    for sp, (tbl, col) in START.items():
        for x in paged(tbl, 'game_id,%s' % col, **{'game_date': 'gte.2026-08-01'}):
            if x.get(col):
                starts[(sp, str(x['game_id']))] = str(x[col])
        scol = SPREAD_COL.get(sp, 'close_spread')
        for x in paged(RESULTS[sp],
                       'game_id,home_score,away_score,close_total,%s' % scol):
            results[(sp, str(x['game_id']))] = x
    print('  start times %d   result rows %d' % (len(starts), len(results)))

    # ══ 2026-11-04 · THE TOOL RE-SELECTED ITS OWN COMPLETED WORK ══
    # A repair is void-then-insert, so a repaired key holds a VOID row AND a
    # LIVE replacement. Nothing checked for the replacement, so every row
    # fixed on 10-28 was selected again. Measured before this guard:
    #
    #     receipts selected on a side mismatch        195
    #       of which already VOID                      68
    #       of those 68, a LIVE sibling already exists  66
    #
    # Two consequences, and the second is the bad one. `--apply` would have
    # attempted 46 inserts that the partial unique index refuses with 23505
    # (noise). And the EFFECT ON THE PUBLISHED RECORD block would have
    # claimed a -2.52u delta for plays that are ALREADY in the record
    # through their live siblings -- a frozen VOID row contributes 0.00u, so
    # re-counting its replacement reads as brand new damage. A repair tool
    # that cannot tell "not yet done" from "done" cannot be put on a
    # schedule, and scheduling is the actual fix for B144
    # (feedback_204_is_not_a_write: confirm by readback, and here the
    # readback is "does the corrected row already exist").
    live_key = set()
    for r in rec:
        if str(r.get('result') or '').upper() != 'VOID':
            live_key.add((str(r.get('sport')), str(r.get('game_date')),
                          str(r.get('source_id'))))

    repair, skip = [], collections.Counter()
    for r in rec:
        rd = reads.get(str(r.get('source_id')))
        if not rd:
            continue
        sp = str(r.get('sport'))
        if a.sport and sp != a.sport:
            continue
        rs, cs = _n(r.get('pick_side')), _n(rd.get('call_side'))
        side_moved = bool(rs and cs and rs != cs)
        rm, cm = _mkt(r.get('market')), _mkt(rd.get('call_market'))
        market_moved = bool(rm and cm and rm != cm)
        if not (side_moved or market_moved):
            continue
        # ══ 2026-11-04 · B144: A MARKET CHANGE IS THE SAME DEFECT ══
        # The 10-28 version required rs != cs and then THREW AWAY anything
        # whose market had also moved, reporting it as "the known B144
        # population". Two things were wrong with leaving it there.
        #
        # First, requiring a side mismatch to even reach the market test
        # means a market change that KEEPS the side token was never seen at
        # all -- read 'Portland Trail Blazers -7.5' (rl/AWAY) against
        # receipt 'Portland Trail Blazers ML' (ml/AWAY). Measured on live
        # game_read receipts: 61 visible to the old selector, 163 actually
        # present.
        #
        # Second, it is not a smaller or a larger claim, it is the same one.
        # The app renders jerry_reads; the read IS what we published. A
        # receipt naming a market the read does not name is wrong about
        # what we published, exactly as a wrong side is. Graded live rows:
        #
        #     opposite outcome (wrong team, or over vs under)   54  26W-28L
        #     different market, side not opposed               143  87W-56L
        #
        # Kept as two labelled classes with their own record-impact lines,
        # because replacing an ML receipt with a total IS a bigger edit to
        # read in a diff -- and --skip-markets restores the old behaviour.
        # THREE classes, not two. The first cut of this labelled any
        # side_moved row 'side' even when the market had moved as well --
        # which is precisely the conflation the 10-28 note objected to, and
        # it made 'LSU ML' vs a read of 'Under 51.5' print as a side flip.
        # Caught by funnelling the classes separately: the tool claimed 46
        # side repairs while a same-market-opposite-side count found 0 left.
        #   side   same market, the other team (or over vs under)
        #   market the market moved, the side token still agrees
        #   both   the market moved AND the side token differs. Note the
        #          tokens are not always comparable here: HOME vs UNDER is
        #          two different bets, not a contradiction, so 'both' is
        #          "two things moved", not "doubly wrong".
        kind = ('side' if side_moved and not market_moved
                else 'both' if side_moved else 'market')
        if kind in ('market', 'both') and a.skip_markets:
            skip['market moved - skipped by --skip-markets'] += 1
            continue
        if (sp, str(r.get('game_date')), str(r.get('source_id'))) in live_key                 and str(r.get('result') or '').upper() == 'VOID':
            skip['already repaired - a live replacement exists on this key'] += 1
            continue
        if sp not in START:
            skip['%s: no start-time column on ctx' % sp] += 1
            continue
        st = starts.get((sp, str(r.get('game_id'))))
        gen = str(rd.get('generated_at') or '')
        if not st:
            skip['%s: game has no start time' % sp] += 1
            continue
        if not gen:
            skip['read has no generated_at'] += 1
            continue
        if gen >= st:
            skip['read changed AFTER the game started - receipt stands'] += 1
            continue
        # ══ 2026-11-04 · gen < start IS NOT "the read is FINAL" ══
        # The pre-start test proves the call we are about to write was on
        # record before kickoff. It does NOT prove the read has stopped
        # moving: for a game that has not started yet, both the receipt and
        # the read are still live and the read can be regenerated again
        # before tip. Found on the NCAAB dry run -- 'California ML' against
        # a read of 'California -16.5' for a game on 2026-11-04, today.
        # Repairing that freezes a SECOND receipt from a vintage that is
        # still provisional, which is the very defect B144 is about. Defer:
        # tomorrow's run sees the same row with the game in the past.
        if st > _now_utc():
            skip['game has not started - read can still move, deferred'] += 1
            continue
        # ══ A 'pass' IS NOT A REPLACEMENT PICK ══
        # Four NCAAB rows wanted to replace a published 'Iowa St. ML lean'
        # with market='pass'. backfill_public_receipts already refuses to
        # create a receipt from a pass read ("a read with no call_market is a
        # read, not a pick"), and receipt_invariants refuses a no-play on a
        # tier gate, so writing one here would walk straight past both. They
        # reached this point only because they are still PENDING, which is
        # the branch that lets an ungradeable replacement through.
        #
        # Left alone rather than voided: the receipt is the record of a pick
        # we did publish, and the read withdrawing it later does not unmake
        # that. Voiding would delete a real play from the denominator on the
        # strength of a later change of mind.
        if _mkt(rd.get('call_market')) in ('', 'pass', 'none'):
            skip['the read now PASSES - no replacement pick to write'] += 1
            continue
        res = results.get((sp, str(r.get('game_id'))))
        new_grade = grade(sp, cs, rd.get('call_market'), res, rd.get('call_line'))
        pending = str(r.get('result') or '') == '' and res is None
        if new_grade is None and not pending:
            skip['replacement cannot be graded - NOT voided'] += 1
            continue
        repair.append((r, rd, new_grade, pending, kind))

    print('')
    print('  REPAIRABLE: %d' % len(repair))
    for k, v in skip.most_common():
        print('     skipped %-52s %d' % (k, v))

    bykind = collections.Counter(k for _r, _d, _g, _p, k in repair)
    print('     of which  SIDE only %d   MARKET only %d   BOTH %d'
          % (bykind.get('side', 0), bykind.get('market', 0),
             bykind.get('both', 0)))
    bysport = collections.Counter(str(r.get('sport'))
                                  for r, _d, _g, _p, _k in repair)
    print('     by sport: %s' % dict(bysport.most_common()))
    print('')
    print('  %-11s %-6s %-7s %-24s %-6s %-24s %-6s %-7s'
          % ('date', 'sport', 'moved', 'receipt (frozen)', 'res',
             'replacement (the read)', 'side', 'grade'))
    print('  ' + '-' * 100)
    for r, rd, g, pend, kind in sorted(
            repair, key=lambda z: (z[4], str(z[0]['game_date'])))[:40]:
        print('  %-11s %-6s %-7s %-24s %-6s %-24s %-6s %-7s'
              % (str(r['game_date'])[:11], r['sport'], kind,
                 ('%s [%s]' % (str(r.get('pick_label'))[:17],
                               r.get('market')))[:24],
                 str(r.get('result') or '-')[:6],
                 ('%s [%s]' % (str(rd.get('call_text'))[:17],
                               rd.get('call_market')))[:24],
                 _n(rd.get('call_side')),
                 g or ('pending' if pend else '?')))
    if len(repair) > 40:
        print('  ... and %d more' % (len(repair) - 40))

    # ══ ALWAYS SHOW WHAT THIS DOES TO THE PUBLISHED RECORD ══
    # A repair that IMPROVES our record is a red flag -- it would mean we are
    # rewriting history toward whatever looks best. Printed every run, apply
    # or not, so it can never be skipped.
    def _u(odds, g):
        try:
            o = float(odds)
        except (TypeError, ValueError):
            o = -110.0
        if g == 'WIN':
            return (o / 100.0) if o > 0 else (100.0 / abs(o))
        return -1.0 if g == 'LOSS' else 0.0
    # Reported per CLASS as well as pooled: the two populations have
    # different sizes and a pooled delta can hide one of them improving the
    # record while the other worsens it (feedback_tier_mix_reverses_the_sign).
    def _impact(rows):
        cu = nu = 0.0
        cc, nc = collections.Counter(), collections.Counter()
        for r, rd, g, _p, _k in rows:
            og = str(r.get('result') or '').upper() or 'PENDING'
            cc[og] += 1
            if og in ('WIN', 'LOSS', 'PUSH'):
                cu += _u(r.get('pick_odds'), og)
            nc[g or 'PENDING'] += 1
            if g in ('WIN', 'LOSS', 'PUSH'):
                nu += _u(rd.get('price_american'), g)
        return cu, nu, cc, nc
    for _k in ('side', 'market', 'both'):
        _rows = [t for t in repair if t[4] == _k]
        if not _rows:
            continue
        _cu, _nu, _cc, _nc = _impact(_rows)
        print('')
        print('  EFFECT ON THE PUBLISHED RECORD - %s moved (%d)'
              % (_k.upper(), len(_rows)))
        print('     frozen receipts   %s' % dict(_cc.most_common()))
        print('     replacements      %s' % dict(_nc.most_common()))
        print('     units  %+.2fu -> %+.2fu   delta %+.2fu%s'
              % (_cu, _nu, _nu - _cu,
                 '   <<< this class IMPROVES the record - check it'
                 if _nu - _cu > 0.5 else ''))
    cu, nu, cc, nc = _impact(repair)
    print('')
    print('  EFFECT ON THE PUBLISHED RECORD - POOLED')
    print('     frozen receipts   %s' % dict(cc.most_common()))
    print('     replacements      %s' % dict(nc.most_common()))
    print('     units  %+.2fu -> %+.2fu   delta %+.2fu%s'
          % (cu, nu, nu - cu,
             '   <<< this repair IMPROVES the record - check it'
             if nu - cu > 0.5 else ''))

    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    # Imported here, not at module scope: a dry run must stay read-only even
    # if these modules are unavailable, and the message if they are must name
    # the write path rather than fail the whole audit.
    try:
        from public_receipt import recover_line as _recover_line
        from public_receipt import unactionable as _unactionable
        from receipt_invariants import check_row as _check_row
    except Exception as _e:                                    # noqa: BLE001
        print('  REFUSING TO WRITE: the shared receipt guards are '
              'unavailable (%s). A replacement written unscreened is frozen '
              'on insert and cannot be corrected.' % _e)
        return 1
    voided = wrote = failed = refused_guard = 0
    for r, rd, g, pend, kind in repair:
        rid = r.get('id')
        row = {
            'sport': r.get('sport'),
            'surface': 'game_read',
            'game_date': r.get('game_date'),
            'game_id': r.get('game_id'),
            'source_id': r.get('source_id'),
            'source_table': r.get('source_table') or 'jerry_reads',
            'matchup': r.get('matchup'),
            'market': str(rd.get('call_market') or '').lower() or None,
            'pick_label': rd.get('call_text'),
            'pick_side': _n(rd.get('call_side')),
            'pick_line': rd.get('call_line'),
            'pick_odds': rd.get('price_american'),
            'conviction': rd.get('conviction'),
            'tier': r.get('tier'),
            'result': g,
            'capture_mode': 'reconstructed',
            'audit': {
                'reason': ('replaces receipt %s, which froze %s %s while the '
                           'read it cites says %s %s. The read was finalised '
                           'before the game started, so this call was on '
                           'record pre-game.'
                           % (rid, r.get('market'), r.get('pick_side'),
                              rd.get('call_market'), _n(rd.get('call_side')))),
                'moved': kind,
                'replaced_receipt_id': rid,
                'replaced_market': r.get('market'),
                'replaced_side': r.get('pick_side'),
                'read_generated_at': rd.get('generated_at'),
                'captured_by': 'reconcile_stale_side_receipts',
            },
        }
        # ══ 2026-11-04 · THE THIRD WRITER THAT BYPASSED THE GUARDS ══
        # public_receipt.capture() screens through receipt_invariants and
        # recovers/refuses a lineless pick; backfill_public_receipts got the
        # same three guards tonight (B142). This file POSTs raw, and it is a
        # writer of record -- so the same defect class was reachable through
        # it. Measured on the 111 rows this run would insert:
        #
        #     replacement has NO price             18
        #     line-bearing market with NO line      3
        #
        # Those 3 are B142 exactly: 'Over' with pick_line NULL is a receipt a
        # user cannot act on and a grader cannot settle, and pick_line is
        # FROZEN on insert, so it could never be repaired afterwards. The 18
        # priceless ones matter because compute_surface_records defaults a
        # missing price to 0.909 units -- so an unpriced replacement books a
        # fictional -110 return (feedback_flat_110_inflates_every_ml_figure).
        #
        # recover_line first, because the number is often in the label
        # already; then refuse what survives; then the surface invariants.
        # A refusal here means the ORIGINAL receipt is left standing rather
        # than voided, which is the correct failure direction: a wrong
        # receipt in the denominator beats no receipt at all.
        _recover_line(row)
        if _unactionable(row):
            refused_guard += 1
            print('  guard: NOT replacing receipt %s — the read it cites is '
                  '%r on market %r with no line, which is ungradeable and '
                  'pick_line is frozen on insert'
                  % (rid, str(row.get('pick_label'))[:34], row.get('market')))
            continue
        # ══ 2026-11-05 · THIS LINE CRASHED THE STEP ON EVERY RUN ══
        # It was `_ref[0][1]`, written on the assumption that screen()
        # hands back (row, why) pairs. It does not: its last line is
        # `return keep, [r for r, _w in refused]`, so element 0 is the
        # ROW DICT and [1] is a key lookup -> KeyError: 1. The step has
        # failed rc=1 on every MLB Grade Overnight run since it was
        # scheduled on 2026-11-04, which means the B144 repair has never
        # actually executed once.
        #
        # screen() discards the reason before returning, so the reason
        # cannot be recovered from its result at all. check_row is the
        # function that carries it -> (severity, reason) -- so ask that
        # directly rather than reaching into a shape that does not exist.
        _sev, _why = _check_row(row, 'game_read')
        if _sev == 'REFUSE':
            refused_guard += 1
            print('  guard: NOT replacing receipt %s — %s'
                  % (rid, _why or 'refused by receipt_invariants'))
            continue
        # ══ ORDER IS THE WHOLE POINT: GUARD, THEN VOID, THEN INSERT ══
        # The first cut of this voided the original and only then screened
        # the replacement, so a refused replacement left the play VOID with
        # nothing beside it -- removing a real pre-game call from the
        # denominator, which this file's own docstring forbids ("it never
        # voids a receipt whose replacement cannot be graded"). The void is
        # the irreversible half, so it goes last of the two
        # (feedback_gate_the_stored_row_not_the_inflight_row).
        if str(r.get('result') or '').upper() != 'VOID':
            _x, st = req('PATCH', 'public_receipts', {'id': 'eq.%s' % rid},
                         {'result': 'VOID'}, {'Prefer': 'return=minimal'})
            if st not in (200, 204):
                failed += 1
                print('  !! void failed for receipt %s (%s)' % (rid, st))
                continue
            _rb, _st2 = req('GET', 'public_receipts',
                            {'id': 'eq.%s' % rid, 'select': 'id,result'})
            _got = (_rb or [{}])[0].get('result') if isinstance(_rb, list)                 else None
            if str(_got).upper() != 'VOID':
                failed += 1
                print('  !! void NOT confirmed on readback for receipt %s '
                      '(reads back %r) - no replacement written'
                      % (rid, _got))
                continue
            voided += 1
        body, st = req('POST', 'public_receipts', None, [row],
                       {'Prefer': 'return=minimal'})
        if st in (200, 201, 204):
            wrote += 1
        else:
            failed += 1
            print('  !! insert failed for read %s (%s): %s'
                  % (r.get('source_id'), st, str(body)[:140]))

    print('')
    print('  voided %d, replacements written %d, failed %d, '
          'refused by the shared guards %d'
          % (voided, wrote, failed, refused_guard))

    # ══ CONFIRM BY READBACK, PER CLASS ══
    # The void is a PATCH and the replacement is a POST; either can return a
    # success status without the row being what we asked for, so the only
    # honest close-out is to re-read the surface and re-run the same
    # comparison (feedback_204_is_not_a_write). Counted per class, because a
    # pooled "still stale" figure would let the market half stay broken
    # while the side half cleared.
    after = paged('public_receipts', '*', surface='eq.game_read')
    left_side = left_mkt = 0
    for r in after:
        if str(r.get('result') or '').upper() == 'VOID':
            continue
        rd = reads.get(str(r.get('source_id')))
        if not rd:
            continue
        rs, cs = _n(r.get('pick_side')), _n(rd.get('call_side'))
        rm, cm = _mkt(r.get('market')), _mkt(rd.get('call_market'))
        if rs and cs and rs != cs:
            left_side += 1
        elif rm and cm and rm != cm:
            left_mkt += 1
    print('  LIVE receipts still disagreeing with their read:'
          ' %d on SIDE, %d on MARKET' % (left_side, left_mkt))
    print('     (a residue is expected and correct: a read regenerated AFTER'
          ' kickoff is never repaired, and MLB has no start-time column to'
          ' judge one by)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
