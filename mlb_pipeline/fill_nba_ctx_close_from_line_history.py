#!/usr/bin/env python3
"""Fill nba_game_context close lines from line_history, around the id split.

== WHY (2026-10-28) =======================================================
R10 asked why some NBA games carry no ctx close line. Measured answer:

    nba_game_context rows up to today            135
    missing close_spread                          65
      of those, dated before the 10-21 opener     60   <- PRESEASON
      regular season                               5

So 60 of 65 are preseason and are not a defect at all; books barely line
those games and nothing downstream should be reading them (see
project_situational_records_include_preseason_playoffs_1022 for what
counting preseason has already cost us). The real gap is 5 games, and
4 of the 5 are Clippers games:

    2026-10-21  Sacramento Kings @ LA Clippers        401909842
    2026-10-23  LA Clippers @ Los Angeles Lakers      401909851
    2026-10-25  LA Clippers @ Oklahoma City Thunder   401909865
    2026-10-28  Los Angeles Lakers @ LA Clippers      401909892
    2026-10-23  Chicago Bulls @ Milwaukee Bucks       401909848  (not Clippers)

That is the same root cause as this morning's phantom-row work: the Odds
API says "Los Angeles Clippers" and everything else says "LA Clippers", so
`_canonical_id` found no schedule match and odds_pull_core MINTED an id.
line_history is keyed on whatever id the puller used, so every quote for
those games is filed under e.g.
`nba_20261028_Los_Angeles_Lakers_Los_Angeles_Clippers` and joins to
nothing. That is R12.

THE ALIAS FIX IS ALREADY LIVE AND VERIFIED. Commit 5717af94 added
name_aliases for the Clippers, and the switchover is visible in the data
for tonight's game:

    minted id   first 2026-10-27T23:26:19   last 2026-10-28T13:26:21
    ESPN id     first 2026-10-28T16:26:22   last 2026-10-28T21:26:23

Two consecutive polls, writes moved. So this script is not a workaround for
a live bug; it recovers the rows stranded before the fix landed.

IT DOES NOT REPOINT line_history. Rewriting the game_id on historical
quotes would edit the audit trail of what we saw and when, in order to fix
a derived table. Instead it joins on `matchup` + the capture batch, which
are stored on every quote and are id-independent, and writes only the
derived close_* columns on nba_game_context.

CONSENSUS DISCIPLINE. A median across books that disagree can invent a line
no book ever posted - a median of an even +/-1.5 split is 0.0, and a fake
line makes a fake price (feedback_consensus_line_must_be_posted). So the
line is the MODAL posted line among the latest quotes, and the price is the
median only among the books posting THAT line.

SIGN. nba close_spread is HOME-perspective and negative when home is
favoured (measured corr(close_spread, home margin) = -0.517 on n=2,449).

    python fill_nba_ctx_close_from_line_history.py
    python fill_nba_ctx_close_from_line_history.py --apply
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request


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
#: NBA's first regular-season date. Earlier ctx rows are preseason and are
#: deliberately left alone rather than filled.
SEASON_OPEN = '2026-10-21'
#: ET calendar day. A UTC "today" is tomorrow in ET for 4-5 hours a night
#: (feedback_window_from_runner_clock_is_a_day_ahead).
TODAY = (dt.datetime.now(dt.timezone.utc)
         - dt.timedelta(hours=4)).date().isoformat()
#: line_history.matchup is the RAW Odds API string, so it is un-aliased even
#: on rows written after the alias fix - the alias is applied when deriving
#: the game_id, not when storing the matchup. ctx stores the canonical name.
#: So the lookup has to try both spellings or it finds nothing, which is
#: exactly how the first run of this script reported 4 of 5 games as having
#: "no line_history under ANY id" while 400 quotes sat there.
MATCHUP_ALIASES = {
    'LA Clippers': ['Los Angeles Clippers'],
}


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
        return e.read().decode('utf-8', 'replace')[:200], e.code


def paged(table, select, order, **filt):
    out, off = [], 0
    while True:
        p = {'select': select, 'order': order, 'limit': '1000',
             'offset': str(off)}
        p.update(filt)
        rows, st = req('GET', table, p)
        if not isinstance(rows, list):
            sys.exit('read of %s failed (%s): %s'
                     % (table, st, str(rows)[:160]))
        out += rows
        if len(rows) < 1000:
            return out
        off += 1000


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _consensus(quotes, side):
    """(line, price, n_books, n_at_line) from the latest quotes for one side.

    Modal line first, then the median price among only the books posting it.
    """
    qs = [q for q in quotes if str(q.get('side') or '').lower() == side]
    if not qs:
        return None, None, 0, 0
    lines = [_f(q.get('line')) for q in qs if _f(q.get('line')) is not None]
    line = None
    if lines:
        line = collections.Counter(lines).most_common(1)[0][0]
        at = [q for q in qs if _f(q.get('line')) == line]
    else:
        at = qs                      # ml has no line
    prices = [_f(q.get('price')) for q in at if _f(q.get('price')) is not None]
    if not prices:
        return line, None, len(qs), len(at)
    return line, int(round(statistics.median(prices))), len(qs), len(at)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=' * 94)
    print('FILL nba_game_context CLOSE LINES FROM line_history  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 94)

    ctx = paged('nba_game_context',
                'game_id,game_date,home_team,away_team,close_spread,'
                'close_total,home_ml_close,away_ml_close,'
                'close_spread_home_price,close_spread_away_price,'
                'close_total_over_price,close_total_under_price',
                'game_date.asc')
    pre = [c for c in ctx if c.get('close_spread') is None
           and str(c['game_date'])[:10] < SEASON_OPEN]
    # A game that has not been played yet has no CLOSING line, by definition.
    # ctx carries the forward schedule, so the first version of this counted
    # 24 "targets" when only 5 were real and the rest simply had not closed.
    future = [c for c in ctx if c.get('close_spread') is None
              and str(c['game_date'])[:10] > TODAY]
    targets = [c for c in ctx if c.get('close_spread') is None
               and SEASON_OPEN <= str(c['game_date'])[:10] <= TODAY]
    print('  ctx rows: %d   missing close_spread: %d'
          % (len(ctx), len(pre) + len(targets) + len(future)))
    print('  of those, PRESEASON (before %s): %d - left alone by design'
          % (SEASON_OPEN, len(pre)))
    print('  of those, NOT YET PLAYED (after %s): %d - nothing has closed'
          % (TODAY, len(future)))
    print('  regular-season played targets: %d' % len(targets))
    if not targets:
        return 0

    # Queried PER MATCHUP, not as one scan. An unfiltered NBA read of
    # line_history is cancelled server-side (57014: statement timeout) - the
    # table holds hundreds of thousands of quotes - and a timeout that
    # returns partial rows would silently price a game off a handful of
    # books. Five small filtered reads instead of one that cannot finish.
    by_match = collections.defaultdict(list)
    _sel = ('game_id,matchup,commence_time,market,side,line,price,book,'
            'captured_at')
    for c in targets:
        aw, hm = c.get('away_team'), c.get('home_team')
        key = ('%s @ %s' % (aw, hm)).strip().lower()
        cands = []
        for _aw in [aw] + MATCHUP_ALIASES.get(str(aw), []):
            for _hm in [hm] + MATCHUP_ALIASES.get(str(hm), []):
                cands.append('%s @ %s' % (_aw, _hm))
        for cand in cands:
            rows, st = req('GET', 'line_history',
                           {'select': _sel, 'sport': 'eq.NBA',
                            'matchup': 'eq.%s' % cand,
                            'order': 'captured_at.desc', 'limit': '400'})
            if not isinstance(rows, list):
                print('  !! line_history read failed for %r (%s)' % (cand, st))
                continue
            if rows:
                by_match[key] += rows
                if cand.strip().lower() != key:
                    print('     (matched on the alias spelling %r)' % cand)
    print('  matchups queried: %d   quotes found: %d'
          % (len(targets), sum(len(v) for v in by_match.values())))

    plans = []
    for c in targets:
        key = ('%s @ %s' % (c.get('away_team'),
                            c.get('home_team'))).strip().lower()
        qs = by_match.get(key) or []
        print('')
        print('  %s  %s @ %s   id=%s'
              % (str(c['game_date'])[:10], c.get('away_team'),
                 c.get('home_team'), c.get('game_id')))
        if not qs:
            print('     no line_history under ANY id for that matchup - skipped')
            continue
        ids = sorted({str(q['game_id']) for q in qs})
        print('     %d quotes across %d id(s): %s'
              % (len(qs), len(ids), [i[:46] for i in ids]))
        # the last capture BATCH, not one row: one poll writes many books
        batch_t = max(str(q['captured_at']) for q in qs)
        batch = [q for q in qs if str(q['captured_at']) == batch_t]
        if len(batch) < 3:
            day = batch_t[:10]
            batch = [q for q in qs if str(q['captured_at'])[:10] == day] or qs
        fill = {}
        sp = [q for q in batch if str(q.get('market')) == 'spread']
        hl, hp, nb, nat = _consensus(sp, 'home')
        _al, awp, _nb2, _n2 = _consensus(sp, 'away')
        if hl is not None:
            # home-perspective, negative when home is favoured
            fill['close_spread'] = hl if hl < 0 else -hl
            if hp is not None:
                fill['close_spread_home_price'] = hp
            if awp is not None:
                fill['close_spread_away_price'] = awp
            print('     spread: %d book(s), %d at the modal line %s  '
                  '-> close_spread %s (home px %s / away px %s)'
                  % (nb, nat, hl, fill['close_spread'], hp, awp))
        to = [q for q in batch if str(q.get('market')) == 'total']
        tl, op, nb, nat = _consensus(to, 'over')
        _tl2, up, _n3, _n4 = _consensus(to, 'under')
        if tl is not None:
            fill['close_total'] = tl
            if op is not None:
                fill['close_total_over_price'] = op
            if up is not None:
                fill['close_total_under_price'] = up
            print('     total : %d book(s), %d at the modal line %s  '
                  '-> close_total %s (over %s / under %s)'
                  % (nb, nat, tl, tl, op, up))
        ml = [q for q in batch if str(q.get('market')) == 'ml']
        _x, hml, nb, _n = _consensus(ml, 'home')
        _y, aml, _n5, _n6 = _consensus(ml, 'away')
        if hml is not None:
            fill['home_ml_close'] = hml
        if aml is not None:
            fill['away_ml_close'] = aml
        if hml is not None or aml is not None:
            print('     ml    : %d book(s) -> home %s / away %s'
                  % (nb, hml, aml))
        # never overwrite something already stored
        fill = {k: v for k, v in fill.items() if c.get(k) is None}
        if not fill:
            print('     nothing to fill')
            continue
        print('     WILL WRITE (from the %s batch): %s'
              % (batch_t[:19], sorted(fill)))
        plans.append((str(c['game_id']), fill))

    print('')
    print('  rows to patch: %d' % len(plans))
    if not a.apply:
        print('  DRY RUN - nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    for gid, fill in plans:
        _x, st = req('PATCH', 'nba_game_context',
                     {'game_id': 'eq.%s' % gid}, fill,
                     {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
            print('  !! patch failed for %s (%s): %s'
                  % (gid, st, str(_x)[:140]))
    print('  patched %d, failed %d' % (ok, fail))

    # A 204 is not a write - read the values back.
    back = paged('nba_game_context',
                 'game_id,game_date,close_spread,close_total',
                 'game_date.asc', game_date='gte.%s' % SEASON_OPEN)
    still = [b for b in back if b.get('close_spread') is None]
    print('  regular-season ctx rows still missing close_spread: %d of %d'
          % (len(still), len(back)))
    for b in still:
        print('     %s %s' % (str(b['game_date'])[:10], b['game_id']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
