#!/usr/bin/env python3
"""Void receipts frozen on a side we did not publish, and write the right one.

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

== WHAT IT WILL NOT DO ====================================================
  * it never voids a receipt whose replacement cannot be graded (27 of the
    120). Voiding without a replacement removes a real play from the
    denominator, which is the one thing worse than a wrong side.
  * it never touches a receipt whose read changed AFTER the game started.
  * it never touches a non-game_read surface.
  * every new row carries an audit note naming the receipt it replaces, and
    capture_mode 'reconstructed' -- it is not claiming to be a live capture.

    python reconcile_stale_side_receipts.py
    python reconcile_stale_side_receipts.py --apply
    python reconcile_stale_side_receipts.py --sport NHL --apply
"""
from __future__ import annotations
import argparse
import collections
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
         'NHL': ('nhl_game_context', 'commence_time')}
RESULTS = {'NFL': 'nfl_game_results', 'NCAAF': 'ncaaf_game_results',
           'NBA': 'nba_game_results', 'NHL': 'nhl_game_results'}
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

    repair, skip = [], collections.Counter()
    market_changed = []
    for r in rec:
        rd = reads.get(str(r.get('source_id')))
        if not rd:
            continue
        rs, cs = _n(r.get('pick_side')), _n(rd.get('call_side'))
        if not (rs and cs and rs != cs):
            continue
        sp = str(r.get('sport'))
        if a.sport and sp != a.sport:
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
        # ══ 2026-10-28 · A MARKET CHANGE IS NOT A WRONG SIDE ══
        # The first dry run flagged rows like
        #     receipt 'LSU ML' (HOME)   vs read 'Under 51.5' (UNDER)
        #     receipt 'Over 57.5'(OVER) vs read 'Lou Monroe +11.5' (HOME)
        # as stale sides, because HOME != UNDER as strings. Those are not the
        # same bet on the wrong side -- the read moved to a DIFFERENT MARKET.
        # That is the known B144 population and a materially different defect:
        # replacing an ML receipt with a total is a far bigger claim than
        # correcting a side, and the two must not be repaired in one pass.
        #
        # So the clean case is: SAME market, opposite side. Market changes are
        # counted and reported, never written here.
        _rm = str(r.get('market') or '').lower()
        _cm = str(rd.get('call_market') or '').lower()
        for _al in (('pl', 'rl'), ('puckline', 'rl'), ('spread', 'rl'),
                    ('ou', 'total')):
            _rm = _al[1] if _rm == _al[0] else _rm
            _cm = _al[1] if _cm == _al[0] else _cm
        if _rm and _cm and _rm != _cm:
            skip['MARKET changed, not just the side (B144) - reported only'] += 1
            market_changed.append((r, rd))
            continue
        res = results.get((sp, str(r.get('game_id'))))
        new_grade = grade(sp, cs, rd.get('call_market'), res, rd.get('call_line'))
        pending = str(r.get('result') or '') == '' and res is None
        if new_grade is None and not pending:
            skip['replacement cannot be graded - NOT voided'] += 1
            continue
        repair.append((r, rd, new_grade, pending))

    print('')
    print('  REPAIRABLE: %d' % len(repair))
    for k, v in skip.most_common():
        print('     skipped %-52s %d' % (k, v))

    if market_changed:
        print('')
        print('  MARKET-CHANGED, reported not repaired: %d' % len(market_changed))
        print('  %-11s %-6s %-26s %-8s %-26s %s'
              % ('date', 'sport', 'receipt', 'market', 'read now says', 'market'))
        for r, rd in sorted(market_changed,
                            key=lambda z: str(z[0]['game_date']))[:12]:
            print('  %-11s %-6s %-26s %-8s %-26s %s'
                  % (str(r['game_date'])[:11], r['sport'],
                     str(r.get('pick_label'))[:26], r.get('market'),
                     str(rd.get('call_text'))[:26], rd.get('call_market')))
        if len(market_changed) > 12:
            print('  ... and %d more' % (len(market_changed) - 12))

    bysport = collections.Counter(str(r.get('sport')) for r, _d, _g, _p in repair)
    print('     by sport: %s' % dict(bysport.most_common()))
    print('')
    print('  %-11s %-6s %-26s %-6s %-26s %-6s %-7s'
          % ('date', 'sport', 'receipt (frozen)', 'res', 'replacement (the read)',
             'side', 'grade'))
    print('  ' + '-' * 96)
    for r, rd, g, pend in sorted(repair, key=lambda z: str(z[0]['game_date']))[:40]:
        print('  %-11s %-6s %-26s %-6s %-26s %-6s %-7s'
              % (str(r['game_date'])[:11], r['sport'],
                 str(r.get('pick_label'))[:26], str(r.get('result') or '-')[:6],
                 str(rd.get('call_text'))[:26], _n(rd.get('call_side')),
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
    cu = nu = 0.0
    cc, nc = collections.Counter(), collections.Counter()
    for r, rd, g, _p in repair:
        og = str(r.get('result') or '').upper() or 'PENDING'
        cc[og] += 1
        if og in ('WIN', 'LOSS', 'PUSH'):
            cu += _u(r.get('pick_odds'), og)
        nc[g or 'PENDING'] += 1
        if g in ('WIN', 'LOSS', 'PUSH'):
            nu += _u(rd.get('price_american'), g)
    print('')
    print('  EFFECT ON THE PUBLISHED RECORD')
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

    voided = wrote = failed = 0
    for r, rd, g, pend in repair:
        rid = r.get('id')
        if str(r.get('result') or '').upper() != 'VOID':
            _x, st = req('PATCH', 'public_receipts', {'id': 'eq.%s' % rid},
                         {'result': 'VOID'}, {'Prefer': 'return=minimal'})
            if st not in (200, 204):
                failed += 1
                print('  !! void failed for receipt %s (%s)' % (rid, st))
                continue
            voided += 1
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
                'reason': ('replaces receipt %s, which froze side %s while '
                           'the read it cites says %s. The read was finalised '
                           'before the game started, so this side was on '
                           'record pre-game.'
                           % (rid, r.get('pick_side'), _n(rd.get('call_side')))),
                'replaced_receipt_id': rid,
                'read_generated_at': rd.get('generated_at'),
                'captured_by': 'reconcile_stale_side_receipts',
            },
        }
        body, st = req('POST', 'public_receipts', None, [row],
                       {'Prefer': 'return=minimal'})
        if st in (200, 201, 204):
            wrote += 1
        else:
            failed += 1
            print('  !! insert failed for read %s (%s): %s'
                  % (r.get('source_id'), st, str(body)[:140]))

    print('')
    print('  voided %d, replacements written %d, failed %d'
          % (voided, wrote, failed))

    after = paged('public_receipts', '*', surface='eq.game_read')
    left = 0
    for r in after:
        if str(r.get('result') or '').upper() == 'VOID':
            continue
        rd = reads.get(str(r.get('source_id')))
        if not rd:
            continue
        rs, cs = _n(r.get('pick_side')), _n(rd.get('call_side'))
        if rs and cs and rs != cs:
            left += 1
    print('  LIVE receipts still on a stale side: %d (was 179 before any '
          'repair)' % left)
    return 0


if __name__ == '__main__':
    sys.exit(main())
