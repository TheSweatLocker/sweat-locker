#!/usr/bin/env python3
"""GRADE NBA PROPS — the missing link that made NBA unmeasurable.

══ WHY (2026-10-26) ════════════════════════════════════════════════════════
Andy: "Why is NBA unmeasurable also?"

Because nothing writes `result`. Both inputs exist:

    nba_game_results        108 completed games in 2026-10
    nba_player_game_logs    969 player-games on 2026-10-20..25
    nba_pipeline_props   11,178 rows, result NULL on ALL of them

I had previously reported "nba_game_results has no scores on any date".
That was WRONG — 996 of 1,000 sampled rows carry a home_score back to
2024-10-28. The blocker is the grader B155 left open, not the data.

**8,760 of 11,178 prop rows (78%) already match a player-game log**, so this
takes NBA from zero graded props to ~8.7k in one pass.

══ THE JOIN, AND WHY IT IS BY NAME ═════════════════════════════════════════
`nba_pipeline_props.player_id` is **NULL on every row** — checked, not
assumed — so the clean id join is unavailable and the key is
(player_name, game_date). That is the join shape that has burned this repo
before (the STL feed mismatch, the NFL "J.Williams" collision where one
name was three player_ids), so:

  * a name matching MORE THAN ONE log row for the same date is left
    UNGRADED and counted, never resolved by picking one
  * a prop whose date has NO logs at all is SKIPPED, not Voided — "not
    ingested yet" and "player did not play" are different facts and
    conflating them would book DNPs as losses
  * a prop whose date HAS logs but whose player is absent is a genuine DNP
    and graded Void

Populating `player_id` on the props writer is the real fix; this grader
reports the ambiguity count so that stays visible rather than silently
absorbed.

══ CONVENTIONS CHECKED, NOT ASSUMED ════════════════════════════════════════
`result` is **Title case** — the other three sports use 'Win' / 'Loss' /
'Void' / 'Push' (feedback_result_column_is_title_case). Writing 'WIN' here
would make every downstream `== 'Win'` comparison silently miss.

Writes batch by (result, final_value) using an encoded `id=in.(...)` filter,
so ~8.7k rows go out in a few hundred requests rather than 8.7k (B154 —
214k requests/day is what took the app down for paying users).

    python grade_nba_props.py                 # dry run
    python grade_nba_props.py --apply
    python grade_nba_props.py --date 2026-10-25 --apply
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

# prop_type base -> how to compute the actual value from a log row
STAT = {
    'pts': lambda g: g.get('points'),
    'points': lambda g: g.get('points'),
    'reb': lambda g: g.get('rebounds'),
    'rebounds': lambda g: g.get('rebounds'),
    'ast': lambda g: g.get('assists'),
    'assists': lambda g: g.get('assists'),
    'steals': lambda g: g.get('steals'),
    'stl': lambda g: g.get('steals'),
    'blocks': lambda g: g.get('blocks'),
    'blk': lambda g: g.get('blocks'),
    'turnovers': lambda g: g.get('turnovers'),
    # threes: fg3m is the populated column; threes_made is often NULL
    'threes': lambda g: (g.get('fg3m') if g.get('fg3m') is not None
                         else g.get('threes_made')),
    'pra': lambda g: _sum3(g.get('points'), g.get('rebounds'),
                           g.get('assists')),
    'pr': lambda g: _sum3(g.get('points'), g.get('rebounds'), 0),
    'pa': lambda g: _sum3(g.get('points'), 0, g.get('assists')),
    'ra': lambda g: _sum3(0, g.get('rebounds'), g.get('assists')),
}
CHUNK = 60


def _sum3(a, b, c):
    vals = [a, b, c]
    if any(v is None for v in vals):
        return None
    try:
        return sum(float(v) for v in vals)
    except (TypeError, ValueError):
        return None


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
        print('    HTTP %s on %s %s :: %s'
              % (e.code, method, path,
                 e.read().decode('utf-8', 'replace')[:400]))
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


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='grade one game_date only')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('=' * 76)
    print('GRADE NBA PROPS  (%s)%s'
          % ('APPLY' if a.apply else 'DRY RUN',
             ('  date=%s' % a.date) if a.date else ''))
    print('=' * 76)

    pfilt = {'game_date': 'eq.%s' % a.date} if a.date else {}
    props = [p for p in fetch_all('nba_pipeline_props', **pfilt)
             if not str(p.get('result') or '').strip()]
    print('  ungraded props: %d' % len(props))
    if not props:
        return 0

    logs = fetch_all('nba_player_game_logs')
    print('  player-game logs: %d' % len(logs))

    # (name, date) -> list of log rows; a date with ANY log is "ingested"
    by_key = collections.defaultdict(list)
    dates_with_logs = set()
    for g in logs:
        d = str(g.get('game_date'))[:10]
        nm = str(g.get('player_name') or '').strip()
        if not d or not nm:
            continue
        by_key[(nm, d)].append(g)
        dates_with_logs.add(d)

    plans = []
    n_skip_date = n_dnp = n_amb = n_nostat = n_noline = 0
    for p in props:
        d = str(p.get('game_date'))[:10]
        nm = str(p.get('player_name') or '').strip()
        base = str(p.get('prop_type') or '').replace('_over', '') \
                                           .replace('_under', '')
        direction = str(p.get('direction') or '').lower()
        line = f(p.get('prop_line'))
        if line is None or direction not in ('over', 'under'):
            n_noline += 1
            continue
        if d not in dates_with_logs:
            # not ingested yet -- NOT a DNP. Leave it alone.
            n_skip_date += 1
            continue
        rows = by_key.get((nm, d), [])
        if len(rows) > 1:
            n_amb += 1
            continue
        if not rows:
            plans.append((p.get('id'), 'Void', None))
            n_dnp += 1
            continue
        fn = STAT.get(base)
        if fn is None:
            n_nostat += 1
            continue
        val = f(fn(rows[0]))
        if val is None:
            n_nostat += 1
            continue
        if abs(val - line) < 1e-9:
            res = 'Push'
        elif direction == 'over':
            res = 'Win' if val > line else 'Loss'
        else:
            res = 'Win' if val < line else 'Loss'
        plans.append((p.get('id'), res, val))

    print('')
    print('  gradeable now              %d' % len(plans))
    print('    of which DNP -> Void     %d' % n_dnp)
    print('  skipped, date not ingested %d  <-- NOT voided, deliberately'
          % n_skip_date)
    print('  skipped, ambiguous name    %d  <-- player_id is NULL on props'
          % n_amb)
    print('  skipped, no stat mapping   %d' % n_nostat)
    print('  skipped, no line/direction %d' % n_noline)
    graded = [x for x in plans if x[1] != 'Void']
    if graded:
        c = collections.Counter(x[1] for x in graded)
        w, l = c.get('Win', 0), c.get('Loss', 0)
        print('')
        print('  WOULD GRADE: %s' % dict(c))
        if w + l:
            print('  implied raw hit rate %.1f%% on n=%d  (a hit rate is not'
                  ' an edge until it is priced)' % (100.0 * w / (w + l),
                                                    w + l))
    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    # batch by identical (result, final_value) so one PATCH covers many ids
    groups = collections.defaultdict(list)
    for pid, res, val in plans:
        if pid is None:
            continue
        groups[(res, val)].append(pid)
    ok = fail = reqs = 0
    for (res, val), ids in groups.items():
        body = {'result': res, 'resolved_at': None}
        body.pop('resolved_at')
        if val is not None:
            body['final_value'] = val
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            flt = 'in.(%s)' % ','.join(str(x) for x in chunk)
            _r, st = _req('PATCH', 'nba_pipeline_props', {'id': flt}, body,
                          {'Prefer': 'return=minimal'})
            reqs += 1
            if st in (200, 204):
                ok += len(chunk)
            else:
                fail += len(chunk)
    print('')
    print('  wrote %d, failed %d, in %d requests' % (ok, fail, reqs))

    # assert the OUTPUT moved -- a green step may write nothing
    if ok:
        chk = fetch_all('nba_pipeline_props', select='id,result,final_value',
                        **(pfilt or {}))
        still = sum(1 for x in chk if not str(x.get('result') or '').strip())
        print('  ungraded remaining in that scope: %d' % still)
        if still >= len(props):
            print('  ABORT-LEVEL: writes reported success but nothing moved.')
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
