"""R126: should the fade quote ctx.close_spread or the MODAL POSTED line?

generate_the_fade picks its line from `ctx.close_spread` and then prices
that exact number. On NFL that column is the one documented to freeze stale
(project_nfl_stale_line_and_inverted_weights_1030), so the surface can
publish a number only a minority of books still post -- on 2026-11-01,
MIN read +3 from the context while 9 of 11 books posted +3.5, across the
key number 3.

This measures the change before it ships, two ways:

  FREQUENCY   how often the context line differs from the modal posted
              line, per sport, and by how much
  CONSEQUENCE for GRADED fade receipts, would the modal line have graded
              differently? Recomputed from raw final scores, never from
              spread_result (derived, and close_spread is the stale column
              under test -- grading the test against its own subject is
              circular)

A line change is only an improvement if it does not quietly move the record
it is measured by. Reported with n on every figure.

USAGE
  python _measure_fade_line_source_1031.py
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL'].rstrip('/')
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ['SUPABASE_KEY'])
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
RESULTS = {'NCAAF': 'ncaaf_game_results', 'NFL': 'nfl_game_results',
           'NBA': 'nba_game_results', 'NHL': 'nhl_game_results'}
MIN_BOOKS = 3


def g(path, **params):
    r = requests.get(SB + '/rest/v1/' + path, headers=H, timeout=120,
                     params=params)
    if r.status_code != 200:
        print('  ! %s HTTP %s %s' % (path, r.status_code, r.text[:140]))
        return None
    return r.json()


def page(path, params):
    out, off = [], 0
    while off < 20000:
        p = dict(params)
        p.update({'limit': 1000, 'offset': off})
        b = g(path, **p)
        if b is None or not b:
            break
        out.extend(b)
        off += 1000
    return out


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def team(lbl):
    s = ' '.join(str(lbl or '').split())
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    s = re.sub(r'\s+(ML|[+-]?\d+(?:\.\d+)?)$', '', s, flags=re.I)
    return s.strip()


def norm(s):
    return re.sub(r'[^a-z0-9]', '', str(s or '').lower())


def lh_key(sport, gid, date, away, home):
    """R80: NFL keys line_history differently from its context table."""
    if str(sport).upper() != 'NFL':
        return gid
    return '%s_%s_%s' % (str(date).replace('-', ''), away, home)


def main() -> int:
    recs = page('public_receipts',
                {'select': 'id,sport,game_date,game_id,market,pick_side,'
                           'pick_line,pick_label,pick_odds,result,matchup',
                 'surface': 'eq.fade', 'market': 'in.(rl,spread)',
                 'order': 'game_date.desc'})
    print('=== %d fade SPREAD receipts in the table ===' % len(recs))

    freq = collections.Counter()
    diffs = []
    flips = []
    same_grade = 0
    ungradeable = 0
    for x in recs:
        sport = str(x.get('sport'))
        mu = str(x.get('matchup') or '')
        if '@' not in mu:
            ungradeable += 1
            continue
        away, home = [t.strip() for t in mu.split('@', 1)]
        gid = lh_key(sport, str(x.get('game_id')), x.get('game_date'),
                     away, home)
        side = str(x.get('pick_side') or '').upper()
        pl = fl(x.get('pick_line'))
        if pl is None or side not in ('HOME', 'AWAY'):
            ungradeable += 1
            continue
        lh = g('line_history', game_id='eq.' + gid, sport='eq.' + sport,
               market='eq.spread',
               select='book,side,line,price,captured_at',
               order='captured_at.desc', limit=400)
        if not lh:
            freq[(sport, 'no book history')] += 1
            continue
        # the capture closest to (and not after) publication is the honest
        # comparison, but fade receipts carry no capture stamp, so use the
        # EARLIEST capture on file -- the number we would have held when the
        # pick was made, which is the same choice FADE_BANNED's NHL study made
        oldest = min(str(h['captured_at']) for h in lh)
        mine = [h for h in lh
                if str(h['captured_at']) == oldest
                and str(h.get('side') or '').lower() == side.lower()
                and h.get('line') is not None]
        if not mine:
            freq[(sport, 'no side row at that capture')] += 1
            continue
        cnt = collections.Counter(float(h['line']) for h in mine)
        modal, nbooks = cnt.most_common(1)[0]
        if nbooks < MIN_BOOKS:
            freq[(sport, 'under %d books' % MIN_BOOKS)] += 1
            continue
        if abs(modal - pl) < 0.01:
            freq[(sport, 'context MATCHES modal')] += 1
            continue
        freq[(sport, 'context DIFFERS from modal')] += 1
        diffs.append((sport, x, pl, modal, nbooks))

        # would it have graded differently?
        res = str(x.get('result') or '').upper()
        if res not in ('WIN', 'LOSS', 'PUSH'):
            continue
        rows = g(RESULTS.get(sport, ''), select='home_team,away_team,'
                                                'home_score,away_score',
                 game_date='eq.%s' % x.get('game_date'), limit=400) or []
        hit = [r for r in rows
               if norm(r.get('home_team')) == norm(home)
               and norm(r.get('away_team')) == norm(away)]
        if len(hit) != 1:
            continue
        r0 = hit[0]
        hs, aws = fl(r0.get('home_score')), fl(r0.get('away_score'))
        if hs is None or aws is None:
            continue
        ours, theirs = (hs, aws) if side == 'HOME' else (aws, hs)
        def grade(ln):
            m = (ours - theirs) + ln
            return 'WIN' if m > 0 else 'LOSS' if m < 0 else 'PUSH'
        g_ctx, g_mod = grade(pl), grade(modal)
        if g_ctx == g_mod:
            same_grade += 1
        else:
            flips.append((sport, x, pl, modal, g_ctx, g_mod))

    print('\n=== FREQUENCY: context line vs modal posted line ===')
    for k in sorted(freq):
        print('  %-6s %-30s %d' % (k[0], k[1], freq[k]))
    print('  (not comparable: %d receipts with no matchup/side/line)'
          % ungradeable)

    print('\n=== THE DIFFERENCES (%d) ===' % len(diffs))
    for sport, x, pl, modal, nbooks in diffs[:20]:
        print('  %-6s %s %-26s context %+g  modal %+g across %d books  '
              '(%+g)' % (sport, x['game_date'], str(x['pick_label'])[:26],
                         pl, modal, nbooks, modal - pl))
    if len(diffs) > 20:
        print('  ... and %d more' % (len(diffs) - 20))

    print('\n=== CONSEQUENCE on graded receipts ===')
    print('  graded and the modal line grades the SAME: %d' % same_grade)
    print('  graded and the modal line FLIPS it       : %d' % len(flips))
    for sport, x, pl, modal, gc, gm in flips:
        print('     %-6s %s %-24s context %+g -> %s | modal %+g -> %s'
              % (sport, x['game_date'], str(x['pick_label'])[:24], pl, gc,
                 modal, gm))
    n = same_grade + len(flips)
    if n:
        print('  => the line source changes the graded outcome on %d of %d '
              '(%.1f%%) of comparable receipts' % (len(flips), n,
                                                   100.0 * len(flips) / n))
    else:
        print('  => no graded receipt is comparable; frequency only')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
