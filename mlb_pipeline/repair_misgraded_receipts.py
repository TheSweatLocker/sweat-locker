#!/usr/bin/env python3
"""G1 · Correct receipts whose stored result contradicts the final score.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
Re-grading every receipt from the box score found 19 rows in `game_read`
whose stored result is the opposite of what happened. Each joins the correct
game on the correct date, so none is a join artefact:

    08-26  Under 9.0     HOU@NYY  3-9  = 12 runs   stored WIN   truth LOSS
    08-26  Under 7.0     CIN@SF  10-9  = 19 runs   stored WIN   truth LOSS
    08-25  STL ML home   BAL 13, STL 1             stored WIN   truth LOSS
    09-26  New Mexico -11.5  won by 24             stored LOSS  truth WIN

**8 are wrong in our favour and 4 against — net +4 wins we did not earn.**
The bias is the reason this goes out ahead of everything else on the list.

HOW THE TRUTH IS DERIVED, and the two traps it avoids
  * The signed handicap is parsed out of `pick_label` ("GB +4" -> +4.0). It
    is NEVER taken from `pick_line`, which holds a MAGNITUDE on 174 rl rows
    and a SIGNED value on 258 — one column, two conventions (G7). A single
    formula over that column is what made my first audit pass report 25
    false positives, identically, in two independent stores.
  * NFL receipts carry a 32-char hash game_id and full team names while
    nfl_game_results carries '20261026_DAL_PHI' and 'DAL'/'PHI', so a result
    is looked up by game_id OR by (sport, date, away, home) off `matchup`.
    A row that resolves by neither is left alone, never guessed at.

SAFETY
  * `result` only. pick_side / pick_line / pick_odds are immutable by
    trigger (B181) and are not touched; neither is any score or line.
  * only rows where the truth is COMPUTABLE and DISAGREES are written. A
    push, an unknown market or a missing score is skipped, not defaulted.
  * the original result is preserved under `audit._g1_regrade` so every
    correction is reversible and auditable.
  * dry-run by default; every write is read back and the net effect on the
    published record is printed before and after.

    python repair_misgraded_receipts.py
    python repair_misgraded_receipts.py --apply
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
                 e.read().decode('utf-8', 'replace')[:300]))
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


_SIGNED = re.compile(r'([+-]\s*\d+(?:\.\d+)?)')
_NUM = re.compile(r'(\d+(?:\.\d+)?)')

STORED_WIN = ('win', 'won')
STORED_LOSS = ('loss', 'lost', 'lose')


def truth(r, h, a):
    """True win / False loss / None when not computable or a push."""
    mkt = str(r.get('market') or '').lower()
    side = str(r.get('pick_side') or '').upper()
    label = str(r.get('pick_label') or '')
    if mkt == 'ml':
        if h == a:
            return None
        if side == 'HOME':
            return h > a
        if side == 'AWAY':
            return a > h
        return None
    if mkt == 'total':
        m = _NUM.search(label)
        if m:
            ln = float(m.group(1))
        else:
            try:
                ln = abs(float(r.get('pick_line')))
            except (TypeError, ValueError):
                return None
        tot = h + a
        if abs(tot - ln) < 1e-9:
            return None
        if side == 'OVER':
            return tot > ln
        if side == 'UNDER':
            return tot < ln
        return None
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
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('=' * 78)
    print('G1 · REPAIR MIS-GRADED RECEIPTS  (%s)'
          % ('APPLY' if a.apply else 'DRY RUN'))
    print('=' * 78)

    res_id, res_tm = {}, {}
    for sp, tbl in (('NFL', 'nfl_game_results'),
                    ('NCAAF', 'ncaaf_game_results'),
                    ('NHL', 'nhl_game_results'),
                    ('NBA', 'nba_game_results'),
                    ('MLB', 'mlb_game_results')):
        n = 0
        for x in fetch_all(tbl):
            hs, as_ = x.get('home_score'), x.get('away_score')
            if hs is None or as_ is None:
                continue
            rec = (float(hs), float(as_))
            gid = str(x.get('game_id') or '')
            if gid:
                res_id[(sp, gid)] = rec
            d = str(x.get('game_date'))[:10]
            hm, aw = str(x.get('home_team') or ''), str(x.get('away_team') or '')
            if hm and aw:
                res_tm[(sp, d, aw.lower(), hm.lower())] = rec
            n += 1
        print('  %-6s %-22s finals: %d' % (sp, tbl, n))

    def final(r):
        sp = str(r.get('sport'))
        gid = str(r.get('game_id') or '')
        if (sp, gid) in res_id:
            return res_id[(sp, gid)]
        m = str(r.get('matchup') or '')
        if ' @ ' in m:
            aw, hm = m.split(' @ ', 1)
            k = (sp, str(r.get('game_date'))[:10], aw.strip().lower(),
                 hm.strip().lower())
            if k in res_tm:
                return res_tm[k]
        return None

    rec = fetch_all('public_receipts')
    print('  receipts: %d' % len(rec))

    bad = []
    for r in rec:
        st = str(r.get('result') or '').strip().lower()
        if st not in STORED_WIN and st not in STORED_LOSS:
            continue
        f = final(r)
        if not f:
            continue
        t = truth(r, f[0], f[1])
        if t is None:
            continue
        if (t is True and st in STORED_WIN) or (t is False and st in STORED_LOSS):
            continue
        bad.append((r, f, t))

    print('')
    print('  stored result contradicts the final score on %d row(s)' % len(bad))
    if not bad:
        print('  nothing to repair.')
        return 0

    favour = sum(1 for _r, _f, t in bad if t is False)   # stored WIN, really a loss
    against = len(bad) - favour
    print('  wrong in OUR FAVOUR: %d     wrong against us: %d     net %+d wins'
          % (favour, against, favour - against))
    print('')
    by_surface = collections.Counter(str(r.get('surface')) for r, _f, _t in bad)
    print('  by surface: %s' % dict(by_surface.most_common()))
    print('')
    for r, f, t in sorted(bad, key=lambda z: str(z[0].get('game_date'))):
        print('   id=%-9s %s %-6s %-11s %-24s side=%-5s mkt=%-6s '
              'final a%d-h%d  %s -> %s'
              % (r.get('id'), r.get('game_date'), r.get('sport'),
                 r.get('surface'), str(r.get('pick_label'))[:24],
                 r.get('pick_side'), r.get('market'), f[1], f[0],
                 r.get('result'), 'Win' if t else 'Loss'))

    def record(rows):
        w = sum(1 for x in rows
                if str(x.get('result') or '').strip().lower() in STORED_WIN)
        ls = sum(1 for x in rows
                 if str(x.get('result') or '').strip().lower() in STORED_LOSS)
        return w, ls

    w0, l0 = record(rec)
    print('')
    print('  published record now        : %d-%d  (%.2f%%)'
          % (w0, l0, 100.0 * w0 / (w0 + l0) if (w0 + l0) else 0))
    print('  published record after this  : %d-%d  (%.2f%%)'
          % (w0 - favour + against, l0 + favour - against,
             100.0 * (w0 - favour + against) /
             (w0 + l0) if (w0 + l0) else 0))

    if not a.apply:
        print('')
        print('  DRY RUN — nothing written. Re-run with --apply.')
        return 0

    ok = fail = 0
    for r, _f, t in bad:
        new = 'Win' if t else 'Loss'
        aud = r.get('audit')
        aud = dict(aud) if isinstance(aud, dict) else {}
        aud['_g1_regrade'] = {
            'was': r.get('result'),
            'now': new,
            'reason': 'stored result contradicted the final score',
            'at': '2026-10-27',
        }
        _x, st = _req('PATCH', 'public_receipts',
                      {'id': 'eq.%s' % r.get('id')},
                      {'result': new, 'audit': aud},
                      {'Prefer': 'return=minimal'})
        if st in (200, 204):
            ok += 1
        else:
            fail += 1
    print('')
    print('  corrected %d, failed %d' % (ok, fail))

    # assert the OUTPUT moved — a green step may write nothing
    after = fetch_all('public_receipts')
    still = 0
    for r in after:
        st = str(r.get('result') or '').strip().lower()
        if st not in STORED_WIN and st not in STORED_LOSS:
            continue
        f = final(r)
        if not f:
            continue
        t = truth(r, f[0], f[1])
        if t is None:
            continue
        if (t is True and st in STORED_WIN) or (t is False and st in STORED_LOSS):
            continue
        still += 1
    w1, l1 = record(after)
    print('  rows still contradicting the score: %d (was %d)' % (still, len(bad)))
    print('  published record now: %d-%d  (%.2f%%)'
          % (w1, l1, 100.0 * w1 / (w1 + l1) if (w1 + l1) else 0))
    if ok and still >= len(bad):
        print('  ABORT-LEVEL: writes reported success but nothing moved.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
