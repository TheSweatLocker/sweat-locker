#!/usr/bin/env python3
"""G2 · Make NFL receipts joinable to NFL results, and verify NFL grading.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
No NFL receipt could be checked against a box score. The two sides of the
join never matched:

    public_receipts   game_id = '4853610e63c246f2027a6a248e642c9a'  (32-char
                      hash), matchup NULL on 4,431 of 4,605 rows (96.2%)
    nfl_game_results  game_id = '20261026_DAL_PHI', away='DAL' home='PHI'

So every NFL accuracy figure in the product came from `jerry_reads`
.actual_outcome — the same source that produced the grade. There was no
independent check anywhere in the NFL chain, which means "NFL is 45.8% on
spreads" was unverifiable rather than wrong.

**The bridge already existed and nothing used it.** `nfl_game_context`
carries BOTH the hash and `results_game_id`, populated on 316 of 316 rows:

    2922c573ec3d... -> 20261108_GB_NE   (GB @ NE 2026-11-08)

316 of 316 populated, 274 matching a played game (the rest are future
fixtures), and end to end it resolves 4,554 of 4,605 NFL receipts (98.9%)
through to a final score. Same lesson as B180: check the join something
else already bridges before concluding two tables cannot be joined.

This module is the resolver plus a verification pass. It writes NOTHING by
default -- `--verify` only reports. `--backfill-matchup` fills the NULL
`matchup` column from the context row so the join also works for anything
that reads receipts without this module.

    python nfl_receipt_bridge.py --verify
    python nfl_receipt_bridge.py --backfill-matchup
    python nfl_receipt_bridge.py --backfill-matchup --apply
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
        with urllib.request.urlopen(r, timeout=90) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return (json.loads(raw) if raw.strip() else None), resp.status
    except urllib.error.HTTPError as e:
        print('    HTTP %s on %s %s :: %s'
              % (e.code, method, path,
                 e.read().decode('utf-8', 'replace')[:240]))
        return None, e.code


def fetch_all(table, **filt):
    out, off = [], 0
    while True:
        p = {'select': '*', 'limit': '1000', 'offset': str(off)}
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


def canonical(ctx_row):
    """results_game_id, else YYYYMMDD_AWAY_HOME rebuilt from the context."""
    rid = str(ctx_row.get('results_game_id') or '').strip()
    if rid:
        return rid
    d = str(ctx_row.get('game_date') or '')[:10].replace('-', '')
    a = str(ctx_row.get('away_team') or '').strip()
    h = str(ctx_row.get('home_team') or '').strip()
    return '%s_%s_%s' % (d, a, h) if (d and a and h) else None


def build_bridge():
    """hash game_id -> {'key', 'away', 'home', 'date'}."""
    out = {}
    for c in fetch_all('nfl_game_context'):
        gid = str(c.get('game_id') or '')
        if not gid:
            continue
        k = canonical(c)
        if not k:
            continue
        out[gid] = {'key': k, 'away': str(c.get('away_team') or ''),
                    'home': str(c.get('home_team') or ''),
                    'date': str(c.get('game_date') or '')[:10]}
    return out


_SIGNED = re.compile(r'([+-]\s*\d+(?:\.\d+)?)')
_NUM = re.compile(r'(\d+(?:\.\d+)?)')
WIN = ('win', 'won')
LOSS = ('loss', 'lost', 'lose')
DEAD = ('void', 'push', 'no_action', 'no action', 'no play', 'no-play')


def truth(r, h, a):
    """True win / False loss / None when not computable or a push.

    The signed handicap comes from pick_label, never from pick_line -- that
    column is a MAGNITUDE on 174 rl rows and SIGNED on 258 (B224).
    """
    mkt = str(r.get('market') or '').lower()
    side = str(r.get('pick_side') or '').upper()
    label = str(r.get('pick_label') or '')
    if mkt == 'ml':
        if h == a:
            return None
        return (h > a) if side == 'HOME' else ((a > h) if side == 'AWAY'
                                               else None)
    if mkt == 'total':
        m = _NUM.search(label)
        if m:
            ln = float(m.group(1))
        else:
            try:
                ln = abs(float(r.get('pick_line')))
            except (TypeError, ValueError):
                return None
        t = h + a
        if abs(t - ln) < 1e-9:
            return None
        return (t > ln) if side == 'OVER' else ((t < ln) if side == 'UNDER'
                                                else None)
    if mkt in ('rl', 'spread'):
        m = _SIGNED.search(label)
        if not m:
            return None
        ln = float(m.group(1).replace(' ', ''))
        if side == 'HOME':
            margin = h - a
        elif side == 'AWAY':
            margin = a - h
        else:
            return None
        d = margin + ln
        return None if abs(d) < 1e-9 else d > 0
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--backfill-matchup', action='store_true')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not (a.verify or a.backfill_matchup):
        a.verify = True

    print('=' * 80)
    print('G2 · NFL RECEIPT BRIDGE')
    print('=' * 80)
    bridge = build_bridge()
    print('  nfl_game_context bridge entries: %d' % len(bridge))

    finals = {}
    for x in fetch_all('nfl_game_results'):
        gid = str(x.get('game_id') or '')
        hs, as_ = x.get('home_score'), x.get('away_score')
        if gid and hs is not None and as_ is not None:
            finals[gid] = (float(hs), float(as_))
    print('  nfl_game_results with a final score: %d' % len(finals))

    rec = fetch_all('public_receipts', sport='eq.NFL')
    print('  NFL receipts: %d' % len(rec))

    resolved = 0
    scored = 0
    for r in rec:
        b = bridge.get(str(r.get('game_id') or ''))
        if b:
            resolved += 1
            if b['key'] in finals:
                scored += 1
    print('  resolving through the bridge: %d (%.1f%%)'
          % (resolved, 100.0 * resolved / len(rec) if rec else 0))
    print('  reaching a FINAL SCORE:       %d (%.1f%%)   <-- was 0'
          % (scored, 100.0 * scored / len(rec) if rec else 0))

    if a.verify:
        print('')
        print('=' * 80)
        print('IS NFL GRADING CORRECT? re-graded from the box score')
        print('=' * 80)
        agree = dis = 0
        ungr_done = 0
        dead_done = 0
        bysurf = collections.defaultdict(lambda: [0, 0])
        bad = []
        for r in rec:
            b = bridge.get(str(r.get('game_id') or ''))
            if not b:
                continue
            fin = finals.get(b['key'])
            if not fin:
                continue
            h, a_ = fin
            st = str(r.get('result') or '').strip().lower()
            surf = str(r.get('surface'))
            if not st:
                ungr_done += 1
                continue
            if st in DEAD:
                t = truth(r, h, a_)
                if t is not None and str(r.get('market') or '').lower() == 'ml':
                    dead_done += 1
                continue
            if st not in WIN and st not in LOSS:
                continue
            t = truth(r, h, a_)
            if t is None:
                continue
            ok = (t and st in WIN) or ((not t) and st in LOSS)
            if ok:
                agree += 1
                bysurf[surf][0] += 1
            else:
                dis += 1
                bysurf[surf][1] += 1
                bad.append((r, h, a_, t))
        tot = agree + dis
        print('  independently re-gradable NFL receipts: %d' % tot)
        print('  AGREE %d   DISAGREE %d   (%.2f%% wrong)'
              % (agree, dis, 100.0 * dis / tot if tot else 0))
        print('')
        print('  by surface:')
        for s in sorted(bysurf, key=lambda k: -sum(bysurf[k])):
            ok_, no = bysurf[s]
            print('     %-12s agree %-5d disagree %-4d  (%.1f%% wrong)'
                  % (s, ok_, no, 100.0 * no / (ok_ + no) if (ok_ + no) else 0))
        print('')
        print('  NFL receipts with NO result although the game finished: %d'
              % ungr_done)
        print('  NFL ML receipts void/no_action on a game that had a winner: %d'
              % dead_done)
        if bad:
            print('')
            print('  the disagreements:')
            for r, h, a_, t in sorted(
                    bad, key=lambda z: str(z[0].get('game_date')),
                    reverse=True)[:20]:
                print('     %s %-11s %-26s side=%-5s mkt=%-5s final a%d-h%d '
                      ' stored=%-6s truth=%s'
                      % (r.get('game_date'), r.get('surface'),
                         str(r.get('pick_label'))[:26], r.get('pick_side'),
                         r.get('market'), a_, h, r.get('result'),
                         'WIN' if t else 'LOSS'))

    if a.backfill_matchup:
        print('')
        print('=' * 80)
        print('BACKFILL matchup  (%s)' % ('APPLY' if a.apply else 'DRY RUN'))
        print('=' * 80)
        todo = []
        for r in rec:
            if r.get('matchup'):
                continue
            b = bridge.get(str(r.get('game_id') or ''))
            if not b or not b['away'] or not b['home']:
                continue
            todo.append((r, '%s @ %s' % (b['away'], b['home'])))
        print('  receipts with matchup NULL that the bridge can fill: %d'
              % len(todo))
        for r, m in todo[:8]:
            print('     id=%-9s %s  ->  %s' % (r.get('id'),
                                               r.get('game_date'), m))
        if not a.apply:
            print('  DRY RUN -- nothing written.')
            return 0
        ok = fail = 0
        for r, m in todo:
            _x, st = _req('PATCH', 'public_receipts',
                          {'id': 'eq.%s' % r.get('id')},
                          {'matchup': m}, {'Prefer': 'return=minimal'})
            if st in (200, 204):
                ok += 1
            else:
                fail += 1
        print('  wrote %d, failed %d' % (ok, fail))
        after = fetch_all('public_receipts', sport='eq.NFL')
        still = sum(1 for x in after if not x.get('matchup'))
        print('  NFL receipts still missing matchup: %d' % still)
        if ok and still >= sum(1 for x in rec if not x.get('matchup')):
            print('  ABORT-LEVEL: writes succeeded but nothing moved.')
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
