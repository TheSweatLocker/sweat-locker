#!/usr/bin/env python3
"""Replay EVERY enabled game-path signal condition against live context rows.

== WHY (2026-10-28) =========================================================
Two different defects were found in NBA's signals today, and neither is
visible by reading the rows:

  * nba_home_b2b_fade / nba_away_b2b_fade read ctx.home_b2b / ctx.away_b2b.
    The columns are home_is_b2b / away_is_b2b. A column-existence check finds
    this one.
  * every oddscrowd_*_fade_boost row used isinstance(X, dict), and signal_expr
    whitelists isinstance but not dict, so the NameError was swallowed as
    False. A column-existence check PASSES all 17 of these -- the column is
    there and populated; the type check is what failed.

So the only test that catches both is to run the engine's own evaluator over
real rows and count the fires. This does that for every sport, reports:

  * expressions that RAISE (NameError/SyntaxError/AttributeError = dead for
    every row, forever; TypeError = usually a None comparison, data-dependent)
  * expressions that reference a ctx column that does not exist, or exists and
    is entirely NULL
  * expressions that evaluate cleanly and still never fire -- a threshold
    outside the data's range, like nba_pace_both_slow_under needing pace <= 96
    when the league p10 is 96.5
  * for the ones that do fire, the side distribution, so a signal emitting one
    side on every game is visible

It writes NOTHING. Prop-scope rows are excluded: they are evaluated by
prop_ensemble_scorer._safe_eval against a prop, not a game, and that
namespace has dict/list/bool.

    python audit_signal_conditions_all_sports.py
    python audit_signal_conditions_all_sports.py --sport NHL
    python audit_signal_conditions_all_sports.py --since 2026-09-01
"""
from __future__ import annotations
import argparse
import collections
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signal_expr                                           # noqa: E402
from signal_expr import AttrDict                             # noqa: E402

CTX_TBL = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
           'NBA': 'nba_game_context', 'NHL': 'nhl_game_context',
           'MLB': 'mlb_game_context', 'NCAAB': 'ncaab_game_context'}
#: classes the GAME scorer dispatches to a python handler, so their
#: condition_expr is never evaluated and a dead expression there is harmless
HANDLER_CLASSES = {'split', 'scenario', 'external_pick'}
ALWAYS_A_BUG = ('NameError', 'SyntaxError', 'AttributeError', 'KeyError')
CTX_REF = re.compile(r"ctx\.([A-Za-z_][A-Za-z0-9_]*)"
                     r"|ctx\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]")


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
HDR = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}


def paged(table, select='*', order='game_date.asc', **filt):
    out, off = [], 0
    while True:
        p = {'select': select, 'limit': '1000', 'offset': str(off),
             'order': order}
        p.update(filt)
        u = BASE + table + '?' + urllib.parse.urlencode(p, safe='.,()*:')
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(u, headers=HDR), timeout=90) as r:
                rows = json.loads(r.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            print('  HTTP %s on %s :: %s'
                  % (e.code, table, e.read().decode('utf-8', 'replace')[:160]))
            return out
        if not isinstance(rows, list):
            return out
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def raw_eval(expr, ctx):
    """Evaluate WITHOUT the swallow, so the exception type is visible."""
    globs = {'__builtins__': {}}
    locs = {**signal_expr._SAFE_BUILTINS, 'ctx': ctx}
    return eval(compile(expr, '<audit>', 'eval'), globs, locs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', choices=sorted(CTX_TBL))
    ap.add_argument('--since', default='2026-09-01')
    a = ap.parse_args()

    print('=' * 112)
    print('REPLAY EVERY ENABLED GAME-PATH SIGNAL CONDITION  (since %s)'
          % a.since)
    print('=' * 112)

    src = [s for s in paged('signal_sources', '*', order='id.asc')
           if s.get('enabled') in (True, 'true', 'True')
           and str(s.get('subject_scope')) not in ('prop', 'player_prop')
           and str(s.get('class')) not in HANDLER_CLASSES]
    sports = [a.sport] if a.sport else sorted(CTX_TBL)
    print('  enabled game-path signal_sources: %d' % len(src))

    grand = collections.Counter()
    raises_all, deadcol_all, nofire_all = [], [], []

    for sport in sports:
        mine = [s for s in src if str(s.get('sport')) == sport]
        if not mine:
            continue
        ctx = paged(CTX_TBL[sport], '*', **{'game_date': 'gte.' + a.since})
        if not ctx:
            print('')
            print('  %-7s no context rows since %s - cannot judge its signals'
                  % (sport, a.since))
            continue
        cols = set()
        for c in ctx:
            cols |= set(c.keys())
        pop = {k: sum(1 for c in ctx if c.get(k) is not None) for k in cols}
        rows = [AttrDict(c) for c in ctx]

        fires, raises, nofire, deadcol = [], [], [], []
        for s in mine:
            cond = str(s.get('condition_expr') or '')
            key = str(s.get('signal_key'))
            if not cond:
                continue

            refs = set()
            for m in CTX_REF.finditer(cond):
                refs.add(m.group(1) or m.group(2))
            bad = [f for f in sorted(refs)
                   if f not in cols or pop.get(f, 0) == 0]
            if bad:
                deadcol.append((s, bad, [f for f in bad if f not in cols]))

            n_fire = 0
            exc_seen = collections.Counter()
            sides = collections.Counter()
            for c in rows:
                try:
                    ok = raw_eval(cond, c)
                except Exception as e:                        # noqa: BLE001
                    exc_seen['%s: %s' % (type(e).__name__, str(e)[:60])] += 1
                    continue
                if ok:
                    n_fire += 1
                    try:
                        sd = raw_eval(str(s.get('side_expr') or '""'), c)
                        sides[str(sd)] += 1
                    except Exception:                         # noqa: BLE001
                        sides['<side raised>'] += 1
            if exc_seen and n_fire == 0:
                raises.append((s, exc_seen))
            elif n_fire == 0:
                nofire.append(s)
            else:
                fires.append((s, n_fire, sides))

        print('')
        print('  %-7s %d signals, %d ctx rows: %d fire, %d RAISE, %d never '
              'fire cleanly, %d reference a dead column'
              % (sport, len(mine), len(ctx), len(fires), len(raises),
                 len(nofire), len(deadcol)))
        grand['fire'] += len(fires)
        grand['raise'] += len(raises)
        grand['nofire'] += len(nofire)
        raises_all += [(sport, s, e) for s, e in raises]
        deadcol_all += [(sport, s, b, m) for s, b, m in deadcol]
        nofire_all += [(sport, s) for s in nofire]

        for s, exc in raises:
            worst = exc.most_common(1)[0][0]
            tag = ('DEAD FOR EVERY ROW'
                   if worst.split(':')[0] in ALWAYS_A_BUG else 'raises')
            print('     !! %-34s %-20s %s'
                  % (str(s.get('signal_key'))[:34], tag, worst[:60]))
        for s, bad, missing in deadcol:
            print('     !! %-34s reads %s%s'
                  % (str(s.get('signal_key'))[:34], ', '.join(bad),
                     '  (NO SUCH COLUMN)' if missing else '  (all NULL)'))
        for s in nofire:
            print('     -- %-34s evaluates fine, never true (threshold vs '
                  'data range?)' % str(s.get('signal_key'))[:34])
        one_sided = [(s, n, sd) for s, n, sd in fires
                     if len(sd) == 1 and n >= 10]
        for s, n, sd in one_sided:
            print('     ~  %-34s fires %d, ALWAYS %s'
                  % (str(s.get('signal_key'))[:34], n,
                     sd.most_common(1)[0][0]))

    print('')
    print('=' * 112)
    print('TOTAL: %d fire, %d raise, %d never fire cleanly'
          % (grand['fire'], grand['raise'], grand['nofire']))
    print('=' * 112)
    hard = [(sp, s, e) for sp, s, e in raises_all
            if e.most_common(1)[0][0].split(':')[0] in ALWAYS_A_BUG]
    print('  EXPRESSIONS THAT CAN NEVER BE TRUE (a code defect, not a '
          'threshold): %d' % len(hard))
    for sp, s, e in hard:
        print('     %-7s %-36s %s' % (sp, str(s.get('signal_key'))[:36],
                                      e.most_common(1)[0][0][:60]))
    print('')
    print('  SIGNALS READING A COLUMN THAT IS ABSENT OR ALL-NULL: %d'
          % len(deadcol_all))
    for sp, s, bad, missing in deadcol_all:
        print('     %-7s %-36s %s%s'
              % (sp, str(s.get('signal_key'))[:36], ', '.join(bad),
                 '  (NO SUCH COLUMN)' if missing else ''))
    print('')
    print('  Nothing was written. A signal that never fires is not')
    print('  automatically wrong - it may wait for a rare spot. One that')
    print('  RAISES is dead code wearing an enabled flag.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
