#!/usr/bin/env python3
"""Re-score the NHL pucklines we lost on, with the external signal now live.

== WHY (2026-10-28) ========================================================
Andy: "witht he sharp fnding adn nhl picks underperfoming we need a wayt o
remedy that".

NHL pucklines are the worst market we publish:

    NHL rl on the Sharp          n=20   7W-13L   35.0%   -4.93u
    NHL puckline, all surfaces   n=40  15W-25L   37.5%  -10.76u

The mechanism is specific and was invisible until externals were wired:
oddscrowd is on the OPPOSITE side of our puckline 30 of those 40 times, and
oddscrowd on NHL rl is 61.6% [53.9, 68.8] on n=159, holding split-half
(63% then 60%). So we were taking the other side of the one source that
demonstrably works in that market, three times out of four.

    our picks oddscrowd OPPOSED   n=30  10W-20L  33.3%  -10.39u
    our picks oddscrowd AGREED    n=10   5W- 5L  50.0%   -0.37u

Nearly the entire loss sits in the disagreements.

NHL got its external_pick signal today (it had none, while 1,147 graded NHL
external picks sat unused). This replays the engine over the games we LOST
with that signal live, and asks one question: does the pick change?

It writes NOTHING. It re-scores in memory and compares the rl candidate the
scorer now produces against the side we actually published, then grades the
new side off the stored result.

HONEST LIMITS, stated rather than buried:
  * this is a RETRO re-score. Track records are read as they stand today, so
    oddscrowd's record includes the very games being re-scored. That is
    look-ahead and it inflates the result. The number here is an upper bound
    on the remedy, not a forecast.
  * n is small. Treat the DIRECTION as the finding and the magnitude as
    provisional.
  * a flip is only counted when the scorer's rl candidate is the opposite
    SIDE of the same game, not when the market merely drops off the top.

    python measure_nhl_external_remedy.py
"""
from __future__ import annotations
import collections
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


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


def get(table, **p):
    out, off = [], 0
    p.setdefault('select', '*')
    p.setdefault('order', 'game_date.asc')
    while True:
        q = dict(p)
        q['limit'] = '1000'
        q['offset'] = str(off)
        u = BASE + table + '?' + urllib.parse.urlencode(q, safe='.,()*:')
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(u, headers=HDR), timeout=60) as r:
                rows = json.loads(r.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            print('  HTTP %s on %s' % (e.code, table))
            return out
        if not isinstance(rows, list):
            return out
        out += rows
        if len(rows) < 1000:
            break
        off += 1000
    return out


def wil(w, n):
    if n == 0:
        return (0.0, 0.0)
    p = w / n
    z = 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - m), 100 * (c + m))


def _s(v):
    v = str(v or '').upper()
    return 'HOME' if v in ('HOME', 'H') else ('AWAY' if v in ('AWAY', 'A')
                                              else None)


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def main():
    print('=' * 96)
    print('NHL PUCKLINE REMEDY: re-score the losers with the external signal live')
    print('=' * 96)

    rec = [x for x in get('public_receipts',
                          select='sport,market,game_id,game_date,pick_label,'
                                 'pick_side,pick_odds,result')
           if str(x.get('sport')) == 'NHL'
           and str(x.get('market') or '').lower() in ('rl', 'pl', 'puckline')
           and str(x.get('result') or '').upper() in ('WIN', 'LOSS')]
    ext = collections.defaultdict(list)
    for x in get('external_picks', select='source,sport,game_id,surface,pick_side'):
        if str(x.get('sport')) == 'NHL' and str(x.get('surface')) == 'rl':
            ext[str(x.get('game_id'))].append(x)

    opposed = []
    for x in rec:
        oc = [e for e in ext.get(str(x.get('game_id')), [])
              if str(e.get('source')) == 'oddscrowd']
        if oc and _s(oc[0].get('pick_side')) != _s(x.get('pick_side')):
            opposed.append(x)
    print('  graded NHL puckline picks: %d   oddscrowd opposed: %d'
          % (len(rec), len(opposed)))

    res = {str(x['game_id']): x for x in get(
        'nhl_game_results',
        select='game_id,home_score,away_score,close_puckline')}

    import ensemble_scorer as E
    ctx_rows = {str(x['game_id']): x for x in get(
        'nhl_game_context', select='*', **{'game_date': 'gte.2026-09-01'})}

    flipped = same = gone = unscored = 0
    graded_new = []
    print('')
    print('  %-11s %-26s %-6s %-26s %-6s %s'
          % ('date', 'we published', 'res', 'scorer now says', 'side', 'new'))
    print('  ' + '-' * 94)
    for x in sorted(opposed, key=lambda z: str(z['game_date'])):
        gid = str(x.get('game_id'))
        ctx = ctx_rows.get(gid)
        if not ctx:
            unscored += 1
            continue
        try:
            E._SOURCES_CACHE = None
            E._TRACK_CACHE = None
            dec = E.score_game('NHL', ctx)
        except Exception as e:                                 # noqa: BLE001
            print('  %-11s %-26s  !! re-score failed: %s'
                  % (str(x['game_date'])[:11], str(x.get('pick_label'))[:26],
                     str(e)[:40]))
            unscored += 1
            continue
        rl = getattr(dec, 'rl', None) if dec is not None else None
        ours = _s(x.get('pick_side'))
        if rl is None or not getattr(rl, 'side', None):
            gone += 1
            newside, newlabel = None, 'PASS (no rl candidate)'
        else:
            newside = _s(str(rl.side).replace('_RL', ''))
            newlabel = getattr(rl, 'display_label', '') or str(rl.side)
            if newside == ours:
                same += 1
            else:
                flipped += 1
        # grade the new side off the stored result
        newres = ''
        r = res.get(gid)
        if newside and r:
            hs, as_ = _f(r.get('home_score')), _f(r.get('away_score'))
            ln = _f(r.get('close_puckline'))
            if hs is not None and as_ is not None and ln is not None:
                h = ln * -1.0 * -1.0  # NHL: negative = home favoured
                adj = (hs - as_) + h
                if adj != 0:
                    newres = 'WIN' if ((adj > 0) == (newside == 'HOME')) \
                             else 'LOSS'
                    graded_new.append(newres)
        print('  %-11s %-26s %-6s %-26s %-6s %s'
              % (str(x['game_date'])[:11], str(x.get('pick_label'))[:26],
                 str(x.get('result'))[:6], str(newlabel)[:26],
                 newside or '-', newres or '-'))

    print('')
    print('  OUTCOME OF THE RE-SCORE on %d opposed picks:' % len(opposed))
    print('     flipped to the other side : %d' % flipped)
    print('     kept the same side        : %d' % same)
    print('     no rl candidate at all    : %d' % gone)
    print('     could not re-score        : %d' % unscored)
    c = collections.Counter(str(x['result']).upper() for x in opposed)
    d = c['WIN'] + c['LOSS']
    lo, hi = wil(c['WIN'], d)
    print('')
    print('     what we PUBLISHED on these: %dW-%dL  %.1f%% [%.1f,%.1f]'
          % (c['WIN'], c['LOSS'], 100.0 * c['WIN'] / max(d, 1), lo, hi))
    if graded_new:
        nw = graded_new.count('WIN')
        lo2, hi2 = wil(nw, len(graded_new))
        print('     what the NEW side would be: %dW-%dL  %.1f%% [%.1f,%.1f]'
              % (nw, len(graded_new) - nw,
                 100.0 * nw / len(graded_new), lo2, hi2))
    print('')
    print('  REMINDER: retro re-score. oddscrowd\'s track record as read today')
    print('  includes these very games, so this is an UPPER BOUND, not a')
    print('  forecast. The direction is the finding; the magnitude is not.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
