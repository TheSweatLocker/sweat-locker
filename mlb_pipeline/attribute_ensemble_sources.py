#!/usr/bin/env python3
"""Attribute the signals that ACTUALLY made each pick, for every sport.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
Andy's standard (feedback_engine_selection_standard): every signal feeding a
pick must be recorded and assessed week to week, so we can see what drives
picks and what drives FAVOURABLE OUTCOMES, and reweight per sport.

`signal_attribution` could not answer that for any sport, because it was not
recording the decision. It tracked a hand-coded list of side diagnostics:

    sport   signals ATTRIBUTED                  signals that MADE the pick
    MLB      2  LR_SHADOW, LR_TOTAL_SHADOW        -
    NCAAF   10  EPA_GAP, ANCHOR, COHORT_*         91
    NFL      7  + GOAT, OFF_RATING_GAP            72
    NHL      0                                    31
    NBA      0                                     9

The real drivers -- `power_diff_edge`, `nfl_confluence_home`,
`ncaaf_ol_weight_adv_home`, `nhl_home_pp_vs_weak_pk`,
`nba_elo_home_ml_strong`, `external:covers` -- were never attributed
anywhere, and NHL/NBA/NCAAB had no rows at all.

`ensemble_scorer.py` already records exactly what is needed, per pick, in
`primary_play._ensemble_sources`:

    {"signal_key": "...", "class": "...", "side": "HOME_RL",
     "weight": 0.92, "contribution": 0.46, "n": 200, "prose": "..."}

So this reads that and writes it through. One writer, every sport, no
per-sport hand-coding -- which is why it fixes NHL/NBA/NCAAB and upgrades
NFL/NCAAF/MLB in the same pass.

DESIGN NOTES THAT MATTER
  * `signal_value` holds the CONTRIBUTION (the weighted amount the signal
    added to the chosen side), not the raw weight. The weight is what the
    registry recommends; the contribution is what it actually did here.
  * `kind` is derived by comparing the signal's side to the PICK's side on
    the same axis. HOME/AWAY and OVER/UNDER are different axes: a HOME
    signal on a total pick is `neutral`, not `warn`. Scoring those as
    disagreements would invent conflict.
  * sides are normalised off the market suffix (`HOME_RL` -> `HOME`) because
    the schema specifies HOME/AWAY/OVER/UNDER, and the market is already
    carried in `pick_market`.
  * a signal can appear more than once per game across markets. The unique
    key is (sport, game_id, signal_key), so rows are deduped to the single
    largest |contribution| rather than letting row order decide -- the same
    collision that `_load_registry` had to fix at market_scope.
  * `result` / `actual_*` are left NULL. The existing grader fills those;
    writing a result here would be guessing at a game outcome.

    python attribute_ensemble_sources.py
    python attribute_ensemble_sources.py --apply
    python attribute_ensemble_sources.py --sport NBA --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CTX = {
    'NFL': 'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NHL': 'nhl_game_context',
    'NBA': 'nba_game_context',
    'MLB': 'mlb_game_context',
    'NCAAB': 'ncaab_game_context',
}
NO_LEAGUE_WEEK = {'MLB', 'NHL', 'NBA', 'NCAAB'}
SIDE_AXIS = {'HOME': 'side', 'AWAY': 'side',
             'OVER': 'total', 'UNDER': 'total'}


def _load_env(path):
    out = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_HERE = os.path.dirname(os.path.abspath(__file__))
_E = _load_env(os.path.join(_HERE, '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = (_E.get('SUPABASE_SERVICE_ROLE_KEY') or _E.get('SUPABASE_KEY')
       or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
       'Content-Type': 'application/json'}


def _req(method, path, params=None, body=None, extra=None):
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
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return (json.loads(raw) if raw.strip() else None), resp.status
    except urllib.error.HTTPError as e:
        print('    HTTP %s on %s :: %s'
              % (e.code, path, e.read().decode('utf-8', 'replace')[:260]))
        return None, e.code


def fetch_all(table, select='*', **filt):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off)}
        p.update(filt)
        rows, st = _req('GET', table, p)
        if not isinstance(rows, list):
            print('    read failed on %s (status %s)' % (table, st))
            break
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def as_dict(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, dict) else None


def num(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def norm_side(raw):
    """'HOME_RL' -> 'HOME'. Returns None when unrecognised."""
    s = str(raw or '').upper().strip()
    for base in ('HOME', 'AWAY', 'OVER', 'UNDER'):
        if s == base or s.startswith(base + '_'):
            return base
    return None


def resolve_week(ctx, game_date, sport):
    if sport not in NO_LEAGUE_WEEK:
        for key in ('season_week', 'week'):
            try:
                iv = int(ctx.get(key))
            except (TypeError, ValueError):
                continue
            if iv > 0:
                return iv
    try:
        d = game_date
        if isinstance(d, str):
            d = dt.date.fromisoformat(str(d)[:10])
        return int(d.isocalendar()[1])
    except (TypeError, ValueError, AttributeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--sport')
    a = ap.parse_args()

    print('=' * 92)
    print('ATTRIBUTE _ensemble_sources -> signal_attribution  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 92)

    existing = set()
    for r in fetch_all('signal_attribution', select='sport,game_id,signal_key'):
        existing.add((str(r.get('sport')), str(r.get('game_id')),
                      str(r.get('signal_key'))))
    print('  attribution rows already present: %d' % len(existing))

    sports = [a.sport.upper()] if a.sport else list(CTX)
    grand = []
    for sp in sports:
        tbl = CTX.get(sp)
        if not tbl:
            print('  %-6s no context table registered — skipped' % sp)
            continue
        rows = fetch_all(tbl)
        if not rows:
            print('  %-6s %-22s no rows / unreadable' % (sp, tbl))
            continue
        # best row per (game_id, signal_key) by |contribution|
        best = {}
        picks = 0
        no_src = 0
        for c in rows:
            pp = as_dict(c.get('primary_play'))
            if not pp:
                continue
            es = pp.get('_ensemble_sources')
            if not isinstance(es, list) or not es:
                no_src += 1
                continue
            picks += 1
            gid = str(c.get('game_id') or '')
            gd = str(c.get('game_date') or '')[:10]
            if not gid or not gd:
                continue
            pick_side = norm_side(pp.get('side'))
            pick_market = str(pp.get('type') or '').lower() or None
            pick_tier = str(pp.get('tier') or '').upper() or None
            close = num(c.get('close_total') if pick_market == 'total'
                        else c.get('close_spread'))
            wk = resolve_week(c, gd, sp)
            season = None
            try:
                season = int(c.get('season'))
            except (TypeError, ValueError):
                season = None
            for e in es:
                if not isinstance(e, dict):
                    continue
                sk = str(e.get('signal_key') or '').strip()
                if not sk:
                    continue
                contrib = num(e.get('contribution'))
                sside = norm_side(e.get('side'))
                # kind: only compare sides on the SAME axis
                if sside is None or pick_side is None:
                    kind = 'neutral'
                elif SIDE_AXIS.get(sside) != SIDE_AXIS.get(pick_side):
                    kind = 'neutral'
                elif sside == pick_side:
                    kind = 'ok'
                else:
                    kind = 'warn'
                row = {
                    'sport': sp, 'game_id': gid, 'game_date': gd,
                    'season': season, 'season_week': wk,
                    'signal_key': sk,
                    'signal_value': contrib,
                    'signal_side': sside,
                    'kind': kind,
                    'pick_market': pick_market,
                    'pick_side': pick_side,
                    'pick_tier': pick_tier,
                    'close_line': close,
                }
                k = (sp, gid, sk)
                prev = best.get(k)
                if prev is None or abs(contrib or 0) > abs(prev['signal_value'] or 0):
                    best[k] = row
        fresh = [r for k, r in best.items() if k not in existing]
        dup = len(best) - len(fresh)
        kinds = collections.Counter(r['kind'] for r in fresh)
        print('  %-6s picks with sources %-5d  rows %-6d  new %-6d  '
              'already present %-5d  %s'
              % (sp, picks, len(best), len(fresh), dup, dict(kinds)))
        if no_src:
            print('         %d context row(s) carried primary_play but NO '
                  '_ensemble_sources' % no_src)
        grand += fresh

    print('')
    print('  TOTAL new attribution rows: %d' % len(grand))
    bysp = collections.Counter(r['sport'] for r in grand)
    print('  by sport: %s' % dict(bysp.most_common()))
    sigs = collections.Counter((r['sport'], r['signal_key']) for r in grand)
    print('  distinct (sport, signal): %d' % len(sigs))
    nullwk = sum(1 for r in grand if r['season_week'] is None)
    print('  rows with NO season_week: %d  (must be 0)' % nullwk)
    print('')
    for r in grand[:6]:
        print('     %-6s %s %-34s side=%-6s %-8s contrib=%s wk=%s'
              % (r['sport'], r['game_date'], r['signal_key'][:34],
                 r['signal_side'], r['kind'], r['signal_value'],
                 r['season_week']))

    if not grand:
        print('  nothing new to write.')
        return 0
    if not a.apply:
        print('')
        print('  DRY RUN — nothing written. Re-run with --apply.')
        return 0

    # full unique index on (sport, game_id, signal_key) -> on_conflict works
    ok = fail = 0
    CH = 400
    for i in range(0, len(grand), CH):
        chunk = grand[i:i + CH]
        _x, st = _req(
            'POST',
            'signal_attribution?on_conflict=sport,game_id,signal_key',
            None, chunk,
            {'Prefer': 'resolution=merge-duplicates,return=minimal'})
        if st in (200, 201, 204):
            ok += len(chunk)
        else:
            fail += len(chunk)
    print('')
    print('  wrote %d, failed %d' % (ok, fail))

    after = fetch_all('signal_attribution', select='sport,signal_key')
    cnt = collections.Counter(str(r.get('sport')) for r in after)
    dis = collections.defaultdict(set)
    for r in after:
        dis[str(r.get('sport'))].add(str(r.get('signal_key')))
    print('  attribution now, per sport (rows / distinct signals):')
    for sp in sorted(cnt):
        print('     %-7s %6d rows   %4d signals' % (sp, cnt[sp], len(dis[sp])))
    if ok and len(after) <= len(existing):
        print('  ABORT-LEVEL: writes reported success but the table did not'
              ' grow.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
