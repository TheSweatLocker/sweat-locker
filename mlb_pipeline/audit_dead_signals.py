"""Why does an enabled signal never fire? Name the column, not the symptom.

ANDY 2026-10-11/12, on the football signal work: "we need propietrary data
pattern and signals ... full deep dive on signals and imporvements in
processes".

Measured yesterday: 63 of 123 enabled NFL signals and 46 of 106 NCAAF ones
have never appeared in a single game's _ensemble_sources. "Dead signal" is
not a diagnosis though — it could be a rare condition legitimately waiting
for the right game, a threshold nothing ever reaches, or a condition
reading a column that is NULL on every row. Those need three different
responses, and only the third is a bug.

So this evaluates each signal's own condition_expr against real context
rows and separates them:

  NEVER TRUE / NULL INPUT   the condition references a column that is NULL
                            on ~every row. It cannot fire. This is a wiring
                            bug: the signal was written against a feature
                            that is not populated for this sport.
  NEVER TRUE / THRESHOLD    the columns are populated, the condition is
                            simply never satisfied. Either the threshold is
                            wrong or the pattern does not occur.
  FIRES                     it does fire; absence from _ensemble_sources is
                            a scoring/selection question, not a wiring one.

The distinction matters because a NULL-input signal inflates the registry.
123 "enabled" NFL signals reads like depth; if half cannot physically fire,
the real number is the honest one and the gap is a to-do list.

WRITES NOTHING.

CLI
    python audit_dead_signals.py --sport NFL
    python audit_dead_signals.py --sport NCAAF --limit-rows 400
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                          # noqa: BLE001
    pass

import market_line as ML

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'NHL': 'nhl_game_context', 'NBA': 'nba_game_context',
       'MLB': 'mlb_game_context'}

_COL = re.compile(r'\bctx\.([A-Za-z_][A-Za-z0-9_]*)')


class AttrDict(dict):
    """ctx.foo -> dict['foo'], returning None for anything absent.

    Mirrors how backfill_signal_tiers evaluates conditions, so a signal
    that is dead here is dead there too.
    """
    def __getattr__(self, k):
        return self.get(k)


def page(table, params, cap=4000):
    out, off = [], 0
    while True:
        q = dict(params)
        q['limit'] = '1000'
        q['offset'] = str(off)
        body = None
        for _ in range(4):
            try:
                r = requests.get(f'{ML.SB}/rest/v1/{table}', headers=ML.H,
                                 params=q, timeout=180)
                if r.status_code in (200, 206):
                    body = r.json()
                    break
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(4)
        if body is None:
            return out
        out += body
        if len(body) < 1000 or len(out) >= cap:
            return out
        off += 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='NFL', choices=sorted(CTX))
    ap.add_argument('--since', default='2026-08-01')
    ap.add_argument('--limit-rows', type=int, default=1000, dest='limit_rows')
    a = ap.parse_args()

    sigs = page('signal_sources', {
        'select': 'signal_key,class,enabled,condition_expr,side_expr',
        'sport': f'eq.{a.sport}', 'enabled': 'eq.true'})
    rows = page(CTX[a.sport], {'select': '*', 'game_date': f'gte.{a.since}'},
                cap=a.limit_rows)
    print(f'=== dead-signal triage · {a.sport} · read-only ===')
    print(f'  {len(sigs)} enabled signals · {len(rows)} context rows '
          f'since {a.since}\n')
    if not rows or not sigs:
        print('  nothing to evaluate')
        return 0

    ctxs = [AttrDict(r) for r in rows]
    # NULL rate per column, measured once.
    nullrate = {}
    for col in rows[0].keys():
        n_null = sum(1 for r in rows if r.get(col) is None)
        nullrate[col] = n_null / len(rows)

    # ══ SCOPE FIRST, OR THE COUNT IS FICTION ══
    # First run of this reported "37 of 123 cannot fire", and 32 of those
    # were NameError on `p.…`. Those are PROP-scope signals: they are
    # evaluated against a prop row (`p`) in the prop pipeline, not against
    # a game-context row, so failing here says nothing about them. Handler
    # classes (split / scenario / external_pick) likewise contribute via
    # HANDLERS[cls] and carry their own track records.
    #
    # Counting either as "dead" would have manufactured a crisis out of a
    # harness bug — the same shape as the MLB-only dark-signal watchdog
    # that started this whole thread.
    HANDLER_CLASSES = {'split', 'scenario', 'external_pick'}
    buckets = defaultdict(list)
    for s in sigs:
        cond = s.get('condition_expr') or ''
        key = s['signal_key']
        cls = str(s.get('class') or '')
        if cls in HANDLER_CLASSES:
            buckets['OTHER SCOPE (handler)'].append((key, cls, 0))
            continue
        if re.search(r'\bp\s*[\.\[]', cond) or cls.startswith('prop_'):
            buckets['OTHER SCOPE (prop row)'].append((key, cls, 0))
            continue
        if not cond:
            buckets['NO CONDITION'].append((key, '', 0))
            continue
        cols = sorted(set(_COL.findall(cond)))
        missing = [c for c in cols if c not in nullrate]
        allnull = [c for c in cols if nullrate.get(c, 1.0) >= 0.99]
        fires = 0
        err = None
        for c in ctxs:
            try:
                # The namespace must cover every builtin a condition
                # actually uses. A first pass omitted isinstance and sum and
                # reported 8 signals as EVAL ERROR / dead when the only
                # thing broken was this harness.
                if eval(cond, {'__builtins__': {}},
                        {'ctx': c, 'float': float, 'abs': abs, 'min': min,
                         'max': max, 'len': len, 'str': str, 'int': int,
                         'sum': sum, 'isinstance': isinstance, 'dict': dict,
                         'list': list, 'round': round, 'any': any,
                         'all': all, 'bool': bool, 'set': set,
                         'sorted': sorted, 'tuple': tuple, 'None': None,
                         'True': True, 'False': False}):
                    fires += 1
            except Exception as e:                         # noqa: BLE001
                err = type(e).__name__
                break
        if err:
            buckets['EVAL ERROR'].append((key, f'{err} · {cond[:60]}', 0))
        elif missing:
            buckets['COLUMN DOES NOT EXIST'].append(
                (key, ','.join(missing), 0))
        elif fires == 0 and allnull:
            buckets['NULL INPUT — cannot fire'].append(
                (key, ','.join(f'{c} {nullrate[c]*100:.0f}% null'
                               for c in allnull), 0))
        elif fires == 0:
            buckets['THRESHOLD never met'].append((key, cond[:70], 0))
        else:
            buckets['FIRES'].append((key, '', fires))

    order = ['COLUMN DOES NOT EXIST', 'NULL INPUT — cannot fire',
             'EVAL ERROR', 'THRESHOLD never met', 'NO CONDITION',
             'OTHER SCOPE (prop row)', 'OTHER SCOPE (handler)', 'FIRES']
    for b in order:
        items = buckets.get(b) or []
        if not items:
            continue
        print(f'  ── {b}: {len(items)}')
        if b == 'FIRES':
            for k, _d, n in sorted(items, key=lambda z: -z[2])[:8]:
                print(f'       {k[:44]:<46} fires on {n}/{len(rows)} rows')
            if len(items) > 8:
                print(f'       … and {len(items) - 8} more')
        else:
            for k, d, _n in sorted(items)[:14]:
                print(f'       {k[:40]:<42} {d[:72]}')
            if len(items) > 14:
                print(f'       … and {len(items) - 14} more')
        print()

    dead = sum(len(buckets.get(b) or []) for b in
               ('COLUMN DOES NOT EXIST', 'NULL INPUT — cannot fire',
                'EVAL ERROR', 'NO CONDITION'))
    other = (len(buckets.get('OTHER SCOPE (prop row)') or [])
             + len(buckets.get('OTHER SCOPE (handler)') or []))
    scored = len(sigs) - other
    print('  ' + '=' * 68)
    print(f'  Of {len(sigs)} enabled {a.sport} signals, {other} are scored '
          f'in another scope (prop row / handler)')
    print(f'  and are not judged here. {scored} are game-context signals:')
    print(f'  {dead} CANNOT fire on current data.')
    print(f'  {len(buckets.get("THRESHOLD never met") or [])} are wired '
          f'correctly but never satisfied.')
    print(f'  {len(buckets.get("FIRES") or [])} do fire.')
    print('\n  A signal that cannot fire is not depth. Fix the feature, '
          'retire the\n  signal, or stop counting it as enabled.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
