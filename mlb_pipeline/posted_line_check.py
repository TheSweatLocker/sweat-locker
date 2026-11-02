"""Is a line one books are posting TODAY?

══ 2026-11-01 · WHY ═════════════════════════════════════════════════════
ANDY: "Stale lines being pinned needs a discussion as well, that need sto be
fix this is unsat."

He is describing the slate-commitment guard I added on 2026-10-30. It exists
for a real reason — the Lakers game went out as Warriors +1.5 on one surface
and +3.5 on another, eight hours apart, because both composers read
`close_spread` at their own run time. First-publisher-wins fixed that.

But it pins to the first published number FOREVER, including when that
number has left the market. Today it pinned the card to `IND +7.5` while
every book was at +10: 0 captures at 7.5 today against 227 at +10. The
receipt behind it carried no price, so the pick published and could not even
grade.

So the rule becomes: **pin to the committed number only while it is still a
number someone can bet.** If the commitment is gone from today's market and
the fresh number is in it, the fresh number wins and the override is printed.

══ THE JOIN IS NOT THE OBVIOUS ONE (R137) ══════════════════════════════
`line_history` keys football by a STRING, '20261101_IND_JAX', while
nfl_game_context uses a 32-hex id. 0 of 12 join by game_id. NBA/NHL use the
numeric event id in both places. So this tries the context id first and
falls back to the composed football key — self-correcting rather than
assuming either.

══ FAIL-OPEN, DELIBERATELY ═════════════════════════════════════════════
`posted_today` returns None when it cannot tell (no rows for the game, no
captures today, a bad response). Callers must treat None as "leave the
commitment alone". A line-history outage must never silently start moving
published numbers — that is the failure this guard was built to stop.

  python posted_line_check.py          # self-test against live data
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import requests

_ENV = Path(__file__).with_name('.env')
if _ENV.exists():
    for _l in _ENV.read_text().split('\n'):
        if '=' in _l and not _l.startswith('#'):
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL', '').rstrip('/')
_K = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
      or os.environ.get('SUPABASE_KEY') or '')
H = {'apikey': _K, 'Authorization': 'Bearer ' + _K}

#: line_history market names differ from our internal ones
_MKT = {'rl': 'spread', 'spread': 'spread', 'puckline': 'spread', 'pl': 'spread',
        'total': 'total', 'ml': 'ml'}
_FOOTBALL = ('NFL', 'NCAAF')


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _count(**params):
    """Exact row count via Content-Range. No paging, so nothing truncates."""
    if not SB:
        return None
    p = dict(params); p['select'] = 'id'; p['limit'] = 1
    try:
        r = requests.get(SB + '/rest/v1/line_history',
                         headers=dict(H, **{'Prefer': 'count=exact',
                                            'Range': '0-0'}),
                         params=p, timeout=45)
    except Exception:                                          # noqa: BLE001
        return None
    if r.status_code not in (200, 206):
        return None
    cr = r.headers.get('content-range') or ''
    try:
        return int(cr.split('/')[-1])
    except (ValueError, IndexError):
        return None


def history_key(sport, game_id, game_date=None, away=None, home=None):
    """The game_id line_history actually uses, or None if nothing matches."""
    su = str(sport).upper()
    if game_id and _count(sport='eq.%s' % su, game_id='eq.%s' % game_id):
        return str(game_id)
    if su in _FOOTBALL and game_date and away and home:
        k = '%s_%s_%s' % (str(game_date).replace('-', ''), away, home)
        if _count(sport='eq.%s' % su, game_id='eq.%s' % k):
            return k
    return None


def posted_today(sport, game_id, market, side, line, game_date=None,
                 away=None, home=None, today=None):
    """True/False whether this exact line is posted today. None = cannot tell.

    `side` is 'home'/'away' for sides, 'over'/'under' for totals. `line` is
    that side's own number, the way line_history stores it.
    """
    ln = _f(line)
    if ln is None:
        return None
    mkt = _MKT.get(str(market or '').lower())
    if not mkt or mkt == 'ml':
        return None
    key = history_key(sport, game_id, game_date, away, home)
    if not key:
        return None
    day = today or (dt.datetime.now(dt.timezone.utc)
                    - dt.timedelta(hours=4)).strftime('%Y-%m-%d')
    base = dict(sport='eq.%s' % str(sport).upper(), game_id='eq.%s' % key,
                market='eq.%s' % mkt, captured_at='gte.%s' % day)
    total = _count(**base)
    if not total:
        return None                      # no captures today -> cannot tell
    sd = str(side or '').lower()
    q = dict(base, line='eq.%s' % ('%g' % ln))
    if sd in ('home', 'away', 'over', 'under'):
        q['side'] = 'eq.%s' % sd
    return bool(_count(**q))


if __name__ == '__main__':
    print('posted_line_check self-test (live data, 2026-11-01)')
    # IND +7.5 was the pinned-but-gone number; +10 was the market.
    cases = [
        ('NFL', '159dbf0253dd2038eda2212f84387f0c', 'rl', 'away', 7.5,
         '2026-11-01', 'IND', 'JAX', False, 'IND +7.5 -- pinned but GONE'),
        ('NFL', '159dbf0253dd2038eda2212f84387f0c', 'rl', 'away', 10.0,
         '2026-11-01', 'IND', 'JAX', True, 'IND +10 -- the real number'),
        ('NFL', '52e7ee9c42525e6deec02da32dc2fad8', 'rl', 'away', 3.5,
         '2026-11-01', 'MIN', 'DET', True, 'MIN +3.5 -- posted'),
    ]
    ok = True
    for sp, gid, mkt, sd, ln, gd, aw, hm, want, note in cases:
        got = posted_today(sp, gid, mkt, sd, ln, gd, aw, hm, today='2026-11-01')
        flag = 'ok  ' if got is want else 'FAIL'
        if got is not want:
            ok = False
        print('  %s %-34s -> %-5s (wanted %s)  %s' % (flag, note, got, want, ''))
    print('  key resolution: %s'
          % history_key('NFL', '159dbf0253dd2038eda2212f84387f0c',
                        '2026-11-01', 'IND', 'JAX'))
    print('  a market we do not test (ml) ->',
          posted_today('NFL', '52e7ee9c42525e6deec02da32dc2fad8', 'ml',
                       'home', None, '2026-11-01', 'MIN', 'DET'))
    print('  unknown game (fail-open) ->',
          posted_today('NFL', 'nope', 'rl', 'away', 3.5, '2026-11-01', 'X', 'Y'))
    print('self-test %s' % ('PASSED' if ok else 'FAILED'))
