#!/usr/bin/env python3
"""NBA PROP LOOKBACK + THE MEASURED GATE — stop shipping one flat tier.

══ WHY (2026-10-26) ════════════════════════════════════════════════════════
`nba_generate_props.py:209` writes every row with `tier: 'COVERAGE'`,
`conviction: 0`, `signals: {}`. Nothing enriches them afterwards. That is
why all 8,760 graded NBA props carry one tier: there is no discriminator,
so nothing can be ranked and nothing can be gated.

Andy: "When will NBA and NHL props be better?" This is step one for NBA.

══ THE DISCRIMINATOR, MEASURED ON 8,638 GRADED PROPS ═══════════════════════
`prop_line / bounded_L5_average`, quintiles WITHIN direction (pooling
direction hides it), every window bounded to games STRICTLY BEFORE the
prop's game_date, bound asserted at 0 violations:

    OVER    Q1 61.0%  Q2 49.7%  Q3 44.0%  Q4 35.0%  Q5 24.0%   ratio asc
            ROI  -2.00%   -10.42%  -13.24%  -18.18%  -34.82%
    UNDER   Q1 44.2%  Q2 50.9%  Q3 55.8%  Q4 55.4%  Q5 57.6%
            ROI -12.39%    -5.66%   +0.46%   -0.08%   -0.89%

Monotone in both directions with a 37-point spread on overs. **The market
prices nearly all of it** -- the best cell is UNDER Q3 at +0.46% -- so this
is a RANKING and BANNING signal, not a licence to publish plays.

Thresholds are the measured quintile boundaries, fixed from the full
sample. They are NOT re-fitted per run: re-learning a threshold has cost
2-8 points every time it has been measured here (the fade 55.7%->47.7%, the
wind cutoff 55.8%->53.6%).

══ WHAT IT ASSIGNS, AND WHY NOTHING GETS PRIME ═════════════════════════════
    OVER,  ratio > 1.36   -> SKIP      measured -18.18% and -34.82%
    UNDER, ratio >= 1.07  -> LEAN      the only cells at or near breakeven
    everything else       -> COVERAGE  info only

No PRIME and no STRONG anywhere, because **no cell measured profitably**.
Assigning a strong tier off a projection before outcomes justify it is
exactly how NFL ended up defaulting every edge-detected prop to STRONG
(B135) and how NBA nearly did (B155).

Writes `_stat_avg_l5`, `_stat_recent_n` and `_line_vs_l5` into signals so
later work can use the feature without recomputing it -- and so the next
person can see the bound was applied.

    python nba_prop_gate.py                  # dry run
    python nba_prop_gate.py --apply
    python nba_prop_gate.py --date 2026-10-27 --apply
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

# measured quintile boundaries, fixed
OVER_SKIP_ABOVE = 1.36      # OVER Q4 starts here: -18.18% then -34.82%
UNDER_LEAN_ABOVE = 1.07     # UNDER Q3 starts here: the only >=breakeven band
MIN_WINDOW = 5
CHUNK = 60

STAT = {'pts': 'points', 'reb': 'rebounds', 'ast': 'assists',
        'steals': 'steals', 'blocks': 'blocks', 'turnovers': 'turnovers'}


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


def as_dict(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return dict(v) if isinstance(v, dict) else {}


def val_of(g, base):
    if base == 'pra':
        a, b, c = g.get('points'), g.get('rebounds'), g.get('assists')
        if None in (a, b, c):
            return None
        return float(a) + float(b) + float(c)
    if base == 'threes':
        v = g.get('fg3m')
        return f(v if v is not None else g.get('threes_made'))
    col = STAT.get(base)
    return f(g.get(col)) if col else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('=' * 76)
    print('NBA PROP GATE  (%s)%s'
          % ('APPLY' if a.apply else 'DRY RUN',
             ('  date=%s' % a.date) if a.date else ''))
    print('=' * 76)
    print('  measured thresholds (fixed, not re-fitted):')
    print('    OVER  ratio > %.2f  -> SKIP   (-18.18%% then -34.82%% ROI)'
          % OVER_SKIP_ABOVE)
    print('    UNDER ratio >= %.2f -> LEAN   (the only band at breakeven)'
          % UNDER_LEAN_ABOVE)

    pfilt = {'game_date': 'eq.%s' % a.date} if a.date else {}
    props = fetch_all('nba_pipeline_props', **pfilt)
    print('  props in scope: %d' % len(props))
    if not props:
        return 0

    logs = fetch_all('nba_player_game_logs',
                     select='player_name,game_date,points,rebounds,assists,'
                            'steals,blocks,turnovers,fg3m,threes_made,'
                            'minutes')
    hist = collections.defaultdict(list)
    for g in logs:
        nm = str(g.get('player_name') or '').strip()
        d = str(g.get('game_date'))[:10]
        if nm and d:
            hist[nm].append((d, g))
    for k in hist:
        hist[k].sort(key=lambda z: z[0])
    print('  player-game logs: %d over %d players' % (len(logs), len(hist)))

    plans = []
    n_short = n_badline = 0
    viol = 0
    for p in props:
        base = str(p.get('prop_type') or '').replace('_over', '') \
                                           .replace('_under', '')
        gd = str(p.get('game_date'))[:10]
        nm = str(p.get('player_name') or '').strip()
        line = f(p.get('prop_line'))
        d = str(p.get('direction') or '').lower()
        if line is None or line <= 0 or d not in ('over', 'under'):
            n_badline += 1
            continue
        prior = [(dd, g) for dd, g in hist.get(nm, []) if dd < gd]
        viol += sum(1 for dd, _g in prior if dd >= gd)
        vals = [val_of(g, base) for _dd, g in reversed(prior)]
        vals = [v for v in vals if v is not None][:MIN_WINDOW]
        if len(vals) < MIN_WINDOW:
            n_short += 1
            continue
        avg = sum(vals) / len(vals)
        if avg <= 0:
            n_short += 1
            continue
        ratio = line / avg
        if d == 'over' and ratio > OVER_SKIP_ABOVE:
            tier = 'SKIP'
        elif d == 'under' and ratio >= UNDER_LEAN_ABOVE:
            tier = 'LEAN'
        else:
            tier = 'COVERAGE'
        sig = as_dict(p.get('signals'))
        sig['_stat_avg_l5'] = round(avg, 2)
        sig['_stat_recent_n'] = len(vals)
        sig['_line_vs_l5'] = round(ratio, 3)
        sig['_gate'] = ('nba_prop_gate 2026-10-26: %s ratio %.2f -> %s'
                        % (d, ratio, tier))
        plans.append((p.get('id'), tier, sig))

    print('')
    print('  bound assertion -- windows containing the game or later: %d'
          ' (must be 0)' % viol)
    print('  planned: %d   skipped short window: %d   bad line/direction: %d'
          % (len(plans), n_short, n_badline))
    c = collections.Counter(t for _i, t, _s in plans)
    print('  tier assignment: %s' % dict(c))
    cur = collections.Counter(str(p.get('tier')) for p in props)
    print('  current tiers in scope: %s' % dict(cur))
    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    ok = fail = reqs = 0
    # signals differ per row, so group only by tier and PATCH signals
    # individually; tier moves in batches.
    bytier = collections.defaultdict(list)
    for pid, tier, _sig in plans:
        if pid is not None:
            bytier[tier].append(pid)
    for tier, ids in bytier.items():
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            flt = 'in.(%s)' % ','.join(str(x) for x in chunk)
            _r, st = _req('PATCH', 'nba_pipeline_props', {'id': flt},
                          {'tier': tier}, {'Prefer': 'return=minimal'})
            reqs += 1
            if st in (200, 204):
                ok += len(chunk)
            else:
                fail += len(chunk)
    print('  tier written on %d rows (%d failed) in %d requests'
          % (ok, fail, reqs))

    sok = sfail = 0
    for pid, _tier, sig in plans:
        if pid is None:
            continue
        _r, st = _req('PATCH', 'nba_pipeline_props', {'id': 'eq.%s' % pid},
                      {'signals': sig}, {'Prefer': 'return=minimal'})
        if st in (200, 204):
            sok += 1
        else:
            sfail += 1
        if (sok + sfail) % 2000 == 0:
            print('     signals ... %d/%d' % (sok + sfail, len(plans)))
    print('  signals written on %d rows (%d failed)' % (sok, sfail))

    # assert the OUTPUT moved
    back = fetch_all('nba_pipeline_props', select='tier', **pfilt)
    now = collections.Counter(str(x.get('tier')) for x in back)
    print('  tiers after write: %s' % dict(now))
    if len(now) <= 1 and 'COVERAGE' in now:
        print('  ABORT-LEVEL: writes reported success but every row is still'
              ' one tier.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
