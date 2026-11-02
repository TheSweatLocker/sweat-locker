"""Would a POTD IDENTITY check be a real detector, or a permanently-red step?

== 2026-11-02 - WHY ========================================================
On 2026-11-01 Andy's hand-set POTD (NFL, MIN +3.5 at -120) kept its `lean`
and `narrative` in daily_best_bet_history while its IDENTITY was rewritten:

    sport          NFL  ->  NBA
    game           MIN @ DET  ->  Boston Celtics @ Orlando Magic
    odds_american  -120  ->  -110

jerry_cache stayed correct, so the two surfaces disagreed about which bet the
day's headline play even was. consistency_watchdog.check_potd_grade_mirror
compares `result` and the no-play `lean` and NOTHING ELSE, so nothing in the
repo was watching the three fields that moved.

Before adding that comparison, measure it. A check that fires on dozens of
historical dates is a permanently-red step, and a permanently-red step is a
muted step (feedback_a_detector_must_know_the_intentional_case). This prints
every date where cache and history disagree on identity, so the intentional
and benign cases are known BEFORE the check ships rather than after.

  python _measure_potd_identity_drift_1102.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

for _l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}


def page(table, params):
    out, off = [], 0
    while off < 50000:
        r = requests.get(SB + '/rest/v1/' + table, headers=H, timeout=120,
                         params=dict(params, limit=1000, offset=off))
        if r.status_code != 200:
            print('  ! %s HTTP %s %s' % (table, r.status_code, r.text[:200]))
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000
    return out


def fl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cache_identity(data):
    """(sport, matchup, odds) as jerry_cache states them, or Nones."""
    if not isinstance(data, dict):
        return None, None, None
    g = data.get('game') if isinstance(data.get('game'), dict) else {}
    matchup = g.get('matchup') or data.get('matchup')
    if not matchup and g.get('away_team') and g.get('home_team'):
        matchup = '%s @ %s' % (g.get('away_team'), g.get('home_team'))
    ctx = data.get('context') if isinstance(data.get('context'), dict) else {}
    odds = fl(ctx.get('odds'))
    if odds is None:
        odds = fl(data.get('odds_american'))
    return data.get('sport'), matchup, odds


def norm(s):
    return ' '.join(str(s or '').strip().lower().split())


def main() -> int:
    cache = {}
    for row in page('jerry_cache', {'select': 'cache_key,data,sport',
                                    'cache_key': 'like.best_bet_2026-%'}):
        d = row.get('data') or {}
        if isinstance(d, str):
            continue
        if d.get('noPlay'):
            continue
        date = str(row['cache_key']).replace('best_bet_', '')
        sp, mu, od = cache_identity(d)
        cache[date] = {'sport': sp or row.get('sport'), 'matchup': mu,
                       'odds': od, 'lean': d.get('leanDisplay')}

    hist = {}
    for row in page('daily_best_bet_history',
                    {'select': 'bet_date,sport,game,odds_american,lean,result'}):
        hist[str(row['bet_date'])] = row

    dates = sorted(set(cache) & set(hist))
    print('POTD dates present in BOTH surfaces: %d  (%s .. %s)'
          % (len(dates), dates[0] if dates else '-', dates[-1] if dates else '-'))
    print()

    drift = {'sport': [], 'game': [], 'odds': []}
    for d in dates:
        c, h = cache[d], hist[d]
        if c['sport'] and h.get('sport') and norm(c['sport']) != norm(h['sport']):
            drift['sport'].append((d, c['sport'], h['sport']))
        if c['matchup'] and h.get('game') and norm(c['matchup']) != norm(h['game']):
            drift['game'].append((d, c['matchup'], h['game']))
        ho = fl(h.get('odds_american'))
        if c['odds'] is not None and ho is not None and abs(c['odds'] - ho) > 0.5:
            drift['odds'].append((d, c['odds'], ho))

    for field in ('sport', 'game', 'odds'):
        rows = drift[field]
        print('%-6s disagreements: %d of %d dates (%.1f%%)'
              % (field, len(rows), len(dates),
                 100.0 * len(rows) / len(dates) if dates else 0))
        for d, a, b in rows[:20]:
            print('    %s  cache=%-34r history=%r' % (d, a, b))
        if len(rows) > 20:
            print('    ... and %d more' % (len(rows) - 20))
        print()

    # Coverage: how often is each field even comparable? A check that can
    # only ever fire on a handful of rows is not much of a check.
    for field, ck, hk in (('sport', 'sport', 'sport'),
                          ('game', 'matchup', 'game'),
                          ('odds', 'odds', 'odds_american')):
        both = sum(1 for d in dates
                   if cache[d][ck] is not None
                   and hist[d].get(hk) is not None)
        print('%-6s comparable on %d of %d dates' % (field, both, len(dates)))

    print()
    print('== VERDICT, measured 2026-11-02 =====================================')
    print('  sport  1 of 138 dates, game 3 of 138, odds 1 of 138.')
    print('  All THREE game disagreements are genuine corruptions, not')
    print('  benign formatting: 08-22 is the SDP/NRFI incident named in')
    print('  20260828_potd_atomic_write.sql, 05-30 is a second one nobody')
    print('  ever reported, and 11-01 is the one Andy hit. So a sport+game')
    print('  check at CRITICAL fires on 3 of 138 dates and every firing is')
    print('  a real defect -- a detector, not a permanently-red step.')
    print()
    print('  ODDS IS EXCLUDED from the check: comparable on 1 of 138 dates,')
    print('  because jerry_cache POTD payloads almost never carry a price')
    print('  (only the hand-set 11-01 one did). A check that can fire on one')
    print('  row asserts nothing. The absence is itself the finding -- it is')
    print('  the same gap as the 383 of 487 POTD receipts with NULL odds.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
