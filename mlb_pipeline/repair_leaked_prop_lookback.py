#!/usr/bin/env python3
"""REPAIR THE 2026-09-22 L5 LEAK IN ALREADY-STORED PROP ROWS.

══ WHY (2026-10-26) ════════════════════════════════════════════════════════
project_prop_l5_leak_922 fixed the LEAK. It did not fix the DATA.

`fetch_mlb_player_recent` used to pull a player's whole season gameLog with
no upper date bound, so once the predicted game was played it became the
newest entry and sat inside its own L5/L10 window. The 9/22 fix added a
`before_date` bound, which stops NEW contamination. Every row written before
that still carries the poisoned values, and nothing ever rewrote them.

Measured 2026-10-26: **12,316 of 12,934** graded, priced, enriched MLB prop
rows have their own game -- or a LATER game -- inside the stored lookback.

    deGrom, game_date 2026-08-31, UNDER 17.5 outs
      _stat_last10[0] = {"date": "2026-08-31", "value": 21.0}   <- the game
      _stat_avg_l5    = 14.2 = mean(21, 11, 18, 6, 15)

That is not a cosmetic problem. Those columns feed the LR model that sets
prop tier, so tier was partly set by the outcome it was predicting -- which
is why MLB tier PRIME reads +23.69% ROI before the fix and -3.51% after.

══ WHY THIS NEEDS NO API CALLS ═════════════════════════════════════════════
`signals._stat_last10` stores the per-game rows WITH DATES. So the bounded
window is reconstructable from data already on the row: keep only entries
with `date < game_date` and recompute. Same arithmetic the fixed pipeline
runs, on the same inputs it would have had.

══ WHAT IT WILL *NOT* DO, AND WHY ══════════════════════════════════════════
Two honesty guards, in the spirit of the one already at
backfill_prop_lookback.py:479 ("a hit rate over a capped 10-game window is
not a season rate"):

  * `_stat_avg_season` / `player_season_hit_pct` are NOT repaired. MLB
    computed them from a 30-value array; only 10 entries were ever stored.
    They cannot be rebuilt, so they are NULLED rather than left holding a
    leaked number under an honest label.

  * L10 is only rewritten when 10 bounded entries SURVIVE. If dropping the
    contaminated entries leaves 8, an "L10" over 8 games is a last-8 wearing
    an L10 label. Same for L5 at 5. Short windows are nulled, not guessed.

    In practice this nulls L10 on EVERY repaired row, because the store is
    capped at 10 entries and dropping even one always leaves 9. That is a
    real cost, so the surviving window is not thrown away -- it is written
    under an honest label instead:

        _stat_avg_recent  mean over ALL surviving bounded entries
        _stat_recent_n    how many games that is

    A consumer that wants a short-window average has one, correctly
    labelled with its own n. Nothing pretends to be a 10-game number.

Nulling is the conservative direction: a null reads as "we do not know",
a leaked value reads as skill we never had.

══ REVERSIBILITY ═══════════════════════════════════════════════════════════
Every original value is preserved under `signals._leak_repair` before it is
overwritten, with `_leak_repaired_at`. Nothing is destroyed, so the write
can be walked back from the row itself. `--revert` does exactly that.

Contaminated entries are also stripped from `_stat_last10` itself, because
a stored window containing a future date is wrong on its face -- but the
originals live in `_leak_repair.prev_last10`.

USAGE
    python repair_leaked_prop_lookback.py                 # dry run, default
    python repair_leaked_prop_lookback.py --apply
    python repair_leaked_prop_lookback.py --revert --apply
    python repair_leaked_prop_lookback.py --sport NFL     # same class, NFL

Default is a dry run. It prints what WOULD change, how far each value
moves, and how many rows fall into each guard -- because a repair that
silently nulls thousands of columns is its own kind of outage.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from backfill_prop_lookback import compute_lookback
except ImportError:                                     # pragma: no cover
    compute_lookback = None

CHUNK = 50

TABLES = {
    'MLB': 'mlb_pipeline_props',
    'NFL': 'nfl_pipeline_props',
    'NHL': 'nhl_pipeline_props',
    'NBA': 'nba_pipeline_props',
}


def _load_env(path):
    out = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_HERE = os.path.dirname(os.path.abspath(__file__))
_E = _load_env(os.path.join(_HERE, '.env'))
URL = _E.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
KEY = _E.get('SUPABASE_KEY') or os.environ.get('SUPABASE_KEY')
if not URL or not KEY:
    sys.exit('missing SUPABASE_URL / SUPABASE_KEY')
BASE = URL.rstrip('/') + '/rest/v1/'
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
       'Content-Type': 'application/json'}


def _req(method, path, params=None, body=None, extra=None):
    u = BASE + path
    if params:
        u += '?' + urllib.parse.urlencode(params)
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
        # A 400 names a COLUMN, not just a table -- print the body.
        print('    HTTP %s on %s %s :: %s'
              % (e.code, method, path,
                 e.read().decode('utf-8', 'replace')[:400]))
        return None, e.code


def fetch_all(table):
    out, off = [], 0
    while True:
        rows, st = _req('GET', table,
                        {'select': '*', 'limit': '1000', 'offset': str(off)})
        if not isinstance(rows, list):
            print('    read failed at offset %d (status %s)' % (off, st))
            break
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def f(v):
    try:
        x = float(v)
        return x if x == x and abs(x) != float('inf') else None
    except (TypeError, ValueError):
        return None


def sig_of(r):
    s = r.get('signals')
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except ValueError:
            s = {}
    return dict(s) if isinstance(s, dict) else {}


def _avg(vals):
    return round(sum(vals) / len(vals), 2) if vals else None


def plan_row(r):
    """Return (patch, note) or (None, reason) for one row.

    patch is the full new signals blob plus any top-level column changes.
    """
    s = sig_of(r)
    l10 = s.get('_stat_last10')
    gd = str(r.get('game_date') or '')
    if not isinstance(l10, list) or not l10 or not gd:
        return None, 'no stored lookback'
    if s.get('_leak_repair'):
        return None, 'already repaired'

    kept, dropped = [], []
    for e in l10:
        if not isinstance(e, dict):
            continue
        d = str(e.get('date') or '')
        if not d:
            # NFL stores {value, opp, week, season} with no date; a window
            # we cannot date is a window we cannot certify. Leave it alone
            # rather than guess which entry is the game.
            return None, 'entries carry no date'
        (dropped if d >= gd else kept).append(e)

    if not dropped:
        return None, 'already clean'

    vals = [f(e.get('value')) for e in kept]
    vals = [v for v in vals if v is not None]
    line = f(s.get('_line'))
    if line is None:
        line = f(r.get('prop_line'))
    direction = str(s.get('_direction')
                    or r.get('direction') or '').lower()

    new = dict(s)
    new['_leak_repair'] = {
        'prev_avg_l5': s.get('_stat_avg_l5'),
        'prev_avg_l10': s.get('_stat_avg_l10'),
        'prev_avg_season': s.get('_stat_avg_season'),
        'prev_l5_hit': r.get('player_l5_hit_count'),
        'prev_l10_hit': r.get('player_l10_hit_count'),
        'prev_season_pct': r.get('player_season_hit_pct'),
        'prev_last10': l10,
        'dropped_dates': [str(e.get('date')) for e in dropped],
        'clean_games': len(vals),
        'repaired_at': '2026-10-26',
        'reason': 'project_prop_l5_leak_922 -- unbounded gameLog window',
    }
    new['_stat_last10'] = kept
    new['_stat_avg_l5'] = _avg(vals[:5]) if len(vals) >= 5 else None
    new['_stat_avg_l10'] = _avg(vals[:10]) if len(vals) >= 10 else None
    # The store is capped at 10, so dropping any entry leaves <10 and the
    # L10 label can never be earned again. Keep the information under a
    # name that states its own window instead of discarding it.
    new['_stat_avg_recent'] = _avg(vals)
    new['_stat_recent_n'] = len(vals)
    # cannot be rebuilt from a 10-entry store -- null, never leave leaked
    new['_stat_avg_season'] = None

    cols = {'signals': new}
    if compute_lookback is not None and line is not None and direction:
        lb = compute_lookback(vals, line, direction)
        cols['player_l5_hit_count'] = lb['l5'] if len(vals) >= 5 else None
        cols['player_l10_hit_count'] = lb['l10'] if len(vals) >= 10 else None
    else:
        cols['player_l5_hit_count'] = None
        cols['player_l10_hit_count'] = None
    cols['player_season_hit_pct'] = None

    return cols, 'repaired (%d dropped, %d kept)' % (len(dropped), len(kept))


def revert_row(r):
    s = sig_of(r)
    lr = s.get('_leak_repair')
    if not isinstance(lr, dict):
        return None, 'nothing to revert'
    new = dict(s)
    new['_stat_last10'] = lr.get('prev_last10')
    new['_stat_avg_l5'] = lr.get('prev_avg_l5')
    new['_stat_avg_l10'] = lr.get('prev_avg_l10')
    new['_stat_avg_season'] = lr.get('prev_avg_season')
    new.pop('_leak_repair', None)
    new.pop('_stat_avg_recent', None)
    new.pop('_stat_recent_n', None)
    return ({'signals': new,
             'player_l5_hit_count': lr.get('prev_l5_hit'),
             'player_l10_hit_count': lr.get('prev_l10_hit'),
             'player_season_hit_pct': lr.get('prev_season_pct')},
            'reverted')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB', choices=sorted(TABLES))
    ap.add_argument('--apply', action='store_true',
                    help='actually write; default is a dry run')
    ap.add_argument('--revert', action='store_true',
                    help='restore the values saved under _leak_repair')
    a = ap.parse_args()

    table = TABLES[a.sport]
    print('=' * 74)
    print('%s  %s  (%s)' % ('REVERT' if a.revert else 'REPAIR', table,
                            'APPLY' if a.apply else 'DRY RUN'))
    print('=' * 74)
    if compute_lookback is None and not a.revert:
        print('  NOTE: compute_lookback not importable -- hit counts will be')
        print('        NULLED rather than recomputed.')

    rows = fetch_all(table)
    print('  read %d rows' % len(rows))

    planner = revert_row if a.revert else plan_row
    patches, notes = [], collections.Counter()
    moves, nulled_l5, nulled_l10 = [], 0, 0
    for r in rows:
        cols, note = planner(r)
        notes[note.split(' (')[0]] += 1
        if cols is None:
            continue
        rid = r.get('id')
        if rid is None:
            notes['no id -- cannot patch'] += 1
            continue
        if not a.revert:
            old = f(sig_of(r).get('_stat_avg_l5'))
            newv = f(cols['signals'].get('_stat_avg_l5'))
            if cols['signals'].get('_stat_avg_l5') is None:
                nulled_l5 += 1
            elif old is not None and newv is not None:
                moves.append(abs(newv - old) / old if old else 0)
            if cols['signals'].get('_stat_avg_l10') is None:
                nulled_l10 += 1
        patches.append((rid, cols))

    print('')
    print('  outcome per row:')
    for k, v in notes.most_common():
        print('     %-34s %d' % (k, v))
    print('')
    print('  rows to write: %d' % len(patches))
    if not a.revert and patches:
        print('     L5  rewritten with a value : %d' % len(moves))
        print('     L5  nulled (<5 clean games): %d' % nulled_l5)
        print('     L10 nulled (<10 clean)     : %d' % nulled_l10)
        if moves:
            moves.sort()
            print('     median L5 change: %.1f%%   p90: %.1f%%   max: %.1f%%'
                  % (100 * moves[len(moves) // 2],
                     100 * moves[int(len(moves) * 0.9)], 100 * moves[-1]))

    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    # One PATCH per row would be 13,290 requests. B154 exists because the
    # first signal that we were issuing 214k requests/day was the app
    # going down for paying users, so this goes out as batched upserts
    # instead: ~270 requests at CHUNK=50.
    #
    # merge-duplicates on the primary key updates only the columns named
    # in the payload. Every object here carries the SAME key set, which is
    # what feedback_postgrest_batch_normalize_keys requires -- a batch with
    # ragged keys gets its keys unioned and writes nulls over columns the
    # short objects never mentioned. Asserted, not assumed.
    keyset = {frozenset(c) for _rid, c in patches}
    if len(keyset) != 1:
        print('  ABORT: payload objects do not share one key set (%d shapes).'
              % len(keyset))
        print('         A ragged batch upsert would null columns it never '
              'names.')
        return 1

    ok = fail = 0
    for i in range(0, len(patches), CHUNK):
        batch = patches[i:i + CHUNK]
        body = []
        for rid, cols in batch:
            o = dict(cols)
            o['id'] = rid
            body.append(o)
        _r, st = _req('POST', table, {}, body,
                      {'Prefer': 'resolution=merge-duplicates,'
                                 'return=minimal'})
        if st in (200, 201, 204):
            ok += len(batch)
        else:
            fail += len(batch)
            if fail <= CHUNK * 2:
                print('     FAILED batch at %d (status %s)' % (i, st))
        if (i // CHUNK) % 20 == 0:
            print('     ... %d/%d' % (min(i + CHUNK, len(patches)),
                                      len(patches)))
    print('')
    print('  wrote %d, failed %d  (%d requests)'
          % (ok, fail, (len(patches) + CHUNK - 1) // CHUNK))

    # Assert the OUTPUT moved. A green step may write nothing
    # (feedback_green_step_that_writes_nothing) -- so re-read one repaired
    # row and confirm the marker is actually on it.
    if ok and patches:
        rid = patches[0][0]
        back, _st = _req('GET', table, {'select': 'signals',
                                        'id': 'eq.%s' % rid})
        got = (sig_of(back[0]) if isinstance(back, list) and back else {})
        if a.revert:
            good = '_leak_repair' not in got
        else:
            good = isinstance(got.get('_leak_repair'), dict)
        print('  verify id=%s : marker %s' % (rid, 'PRESENT' if
                                              isinstance(got.get(
                                                  '_leak_repair'), dict)
                                              else 'absent'))
        if not good:
            print('  ABORT-LEVEL: the write reported success but the row did'
                  ' not change.')
            return 1
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
