#!/usr/bin/env python3
"""Rewrite `isinstance(X, dict)` to `is_dict(X)` in GAME-path signal rows.

== WHY (2026-10-28) =========================================================
signal_expr._SAFE_BUILTINS whitelists `isinstance` but not `dict`, so
    isinstance(ctx.oddscrowd_snapshot, dict)
raised NameError, evaluate() caught it and returned the default, and
evaluate_bool turned that into False. The condition could never be true no
matter what the snapshot held.

Measured: 17 enabled GAME-path signals were dead this way -- the entire
oddscrowd_*_fade_boost family, one per market per sport, across all six
sports (MLB 3, NFL 3, NCAAF 3, NHL 3, NCAAB 3, NBA 2). That is every
public-fade signal the ensemble has, and the Fade is the one surface with a
measured edge.

Verified piece by piece on a game whose snapshot really does hold
ml.fade == 'boost':
    isinstance(ctx.oddscrowd_snapshot, dict)             -> False
    ctx.oddscrowd_snapshot['ml'].get('fade') == 'boost'   -> True
The subscript worked. Only the type check failed.

27 further rows use the same idiom at PROP scope and are untouched: they are
evaluated by prop_ensemble_scorer._safe_eval, which has dict/list/bool in its
own namespace. Two evaluators, two namespaces, one of them short -- so this
script only rewrites rows whose subject_scope is NOT prop/player_prop, and
leaves the prop rows exactly as they are.

signal_expr now exposes is_dict/is_list (plain predicates). The TYPES stay out
deliberately: signal_sources is a table and the app's anon key can write
schema-wide with RLS off, so a row that reaches eval with `dict` bound gets
dict.__subclasses__() -- the standard escape from a locked-down eval.

SAFETY
  * only rows where subject_scope is not prop/player_prop.
  * only the exact substring `isinstance(<expr>, dict)` -> `is_dict(<expr>)`,
    by regex, leaving everything else in the expression untouched.
  * every rewritten expression is COMPILED before it is written; a row whose
    new text does not compile is skipped and reported.
  * each rewritten condition is then evaluated against live context rows and
    the fire count printed, so a rewrite that still cannot fire is visible
    here rather than discovered in a week.
  * dry-run by default.

    python fix_isinstance_dict_conditions.py
    python fix_isinstance_dict_conditions.py --apply
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_expr import AttrDict, evaluate_bool          # noqa: E402

FIELDS = ('condition_expr', 'side_expr', 'strength_expr')
# isinstance( <anything without a comma at depth 0> , dict )
PAT = re.compile(r'isinstance\s*\(\s*([^,()]*(?:\([^()]*\))?[^,()]*)\s*,\s*dict\s*\)')
CTX_TBL = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
           'NBA': 'nba_game_context', 'NHL': 'nhl_game_context',
           'MLB': 'mlb_game_context', 'NCAAB': 'ncaab_game_context'}


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
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return (json.loads(raw) if raw.strip() else None), resp.status
    except urllib.error.HTTPError as e:
        print('  HTTP %s :: %s'
              % (e.code, e.read().decode('utf-8', 'replace')[:200]))
        return None, e.code


def ctx_rows(sport, since='2026-10-01'):
    tbl = CTX_TBL.get(sport)
    if not tbl:
        return []
    rows, _st = req('GET', tbl, {'select': '*', 'game_date': 'gte.' + since,
                                 'order': 'game_date.asc', 'limit': '500'})
    return rows if isinstance(rows, list) else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    print('=' * 92)
    print('REWRITE isinstance(X, dict) -> is_dict(X) ON GAME-PATH SIGNALS  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 92)

    src, st = req('GET', 'signal_sources', {'select': '*', 'limit': '2000'})
    if not isinstance(src, list):
        sys.exit('could not read signal_sources (status %s)' % st)
    print('  signal_sources rows: %d' % len(src))

    todo = []
    for s in src:
        if str(s.get('subject_scope')) in ('prop', 'player_prop'):
            continue
        if not any(PAT.search(str(s.get(f) or '')) for f in FIELDS):
            continue
        todo.append(s)
    print('  game-path rows needing the rewrite: %d' % len(todo))
    if not todo:
        print('  nothing to do.')
        return 0

    _ctx_cache = {}
    ok_rows, bad_rows = [], []
    for s in sorted(todo, key=lambda z: (str(z.get('sport')),
                                         str(z.get('signal_key')))):
        patch = {}
        for f in FIELDS:
            old = str(s.get(f) or '')
            if not PAT.search(old):
                continue
            new = PAT.sub(r'is_dict(\1)', old)
            try:
                compile(new, '<rewrite>', 'eval')
            except SyntaxError as e:
                bad_rows.append((s, f, new, 'does not compile: %s' % e))
                patch = None
                break
            patch[f] = new
        if patch is None or not patch:
            continue

        sport = str(s.get('sport'))
        if sport not in _ctx_cache:
            _ctx_cache[sport] = ctx_rows(sport)
        rows = _ctx_cache[sport]
        cond_new = patch.get('condition_expr') or str(s.get('condition_expr') or '')
        before = sum(1 for c in rows
                     if evaluate_bool(str(s.get('condition_expr') or ''),
                                      AttrDict(c)))
        after = sum(1 for c in rows if evaluate_bool(cond_new, AttrDict(c)))
        print('')
        print('  %-7s %-36s fires %d -> %d  (of %d ctx rows)'
              % (sport, str(s.get('signal_key'))[:36], before, after, len(rows)))
        for f, v in patch.items():
            print('     %-15s %s' % (f, v[:130]))
        ok_rows.append((s, patch, before, after))

    for s, f, new, why in bad_rows:
        print('')
        print('  !! SKIPPED %s.%s - %s' % (s.get('signal_key'), f, why))

    still_dead = [r for r in ok_rows if r[3] == 0]
    print('')
    print('  rewrites that compile: %d   still never fire: %d'
          % (len(ok_rows), len(still_dead)))
    for s, _p, _b, _af in still_dead:
        print('     %-7s %s - rewrite is correct but the DATA never matches'
              % (str(s.get('sport')), s.get('signal_key')))

    if not a.apply:
        print('')
        print('  DRY RUN -- nothing written. Re-run with --apply.')
        return 0

    wrote = fail = 0
    for s, patch, _b, _af in ok_rows:
        _x, st2 = req('PATCH', 'signal_sources', {'id': 'eq.%s' % s['id']},
                      patch, {'Prefer': 'return=minimal'})
        if st2 in (200, 204):
            wrote += 1
        else:
            fail += 1
            print('  !! write failed for %s' % s.get('signal_key'))
    print('')
    print('  rewritten %d, failed %d' % (wrote, fail))

    after_src, _st = req('GET', 'signal_sources', {'select': '*', 'limit': '2000'})
    left = [s for s in (after_src or [])
            if str(s.get('subject_scope')) not in ('prop', 'player_prop')
            and any(PAT.search(str(s.get(f) or '')) for f in FIELDS)]
    print('  game-path rows still using isinstance(.., dict): %d (was %d)'
          % (len(left), len(todo)))
    if wrote and len(left) >= len(todo):
        print('  ABORT-LEVEL: writes returned OK but nothing moved.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
