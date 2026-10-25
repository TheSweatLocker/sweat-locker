#!/usr/bin/env python3
"""Set the Play of the Day by hand, when the engine passes but Andy wants one.

══ WHY THIS EXISTS ══════════════════════════════════════════════════════
jerry_anchor_potd.py selects on CONVICTION with a threshold of 70. On the
2026-10-25 NFL slate its highest read was 63, so it correctly marked No Play
-- and there was no way to publish a POTD short of lowering the threshold.

Lowering it would have been the wrong fix. Conviction is MEASURED to be a
useless ranker: on NCAAF spreads it is non-monotonic and the 65-74 bucket is
the WORST of all at 35.3% n=51 (B178). Dropping the bar to 63 would have
selected TEN +2.5, a COVERAGE pick on 3 books, chosen by a number we know is
noise. That is manufacturing a play.

So this takes the pick as an explicit argument and records WHY, rather than
pretending a score produced it. The selection basis belongs in the narrative
where a subscriber can judge it.

SAFETY
  * refuses a side/line that does not match the game's stored teams
  * refuses past the 12:00 ET card lock unless --force-past-lock
  * writes BOTH places the app reads: daily_best_bet_history (the record) and
    jerry_cache.best_bet_<date> (the card), because writing one leaves the
    surfaces disagreeing
  * re-reads both after writing, since a 204 is not a write
  * stamps _manual with who/why so the trail never implies the engine chose it

Usage:
  python set_potd_manual.py --sport NFL --game <game_id> --side HOME \
      --type rl --line 5 --label "ARI +5" --odds -112 \
      --narrative-file potd.md --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV = os.path.join(_HERE, '.env')
if os.path.exists(_ENV):
    for _ln in open(_ENV, encoding='utf-8'):
        if '=' in _ln and not _ln.strip().startswith('#'):
            _k, _v = _ln.strip().split('=', 1)
            os.environ.setdefault(_k, _v)

SB = os.environ['SUPABASE_URL']
KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
       or os.environ.get('SUPABASE_KEY'))
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = dict(H, **{'Content-Type': 'application/json'})
HW_UP = dict(HW, **{'Prefer': 'resolution=merge-duplicates,return=minimal'})

CTX = {'NFL': 'nfl_game_context', 'NCAAF': 'ncaaf_game_context',
       'NBA': 'nba_game_context', 'NHL': 'nhl_game_context',
       'MLB': 'mlb_game_context'}
LOCK_ET_HOUR = 12


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', required=True, choices=sorted(CTX))
    ap.add_argument('--game', required=True)
    ap.add_argument('--side', required=True,
                    choices=['HOME', 'AWAY', 'OVER', 'UNDER'])
    ap.add_argument('--type', dest='mtype', required=True,
                    choices=['rl', 'spread', 'ml', 'total'])
    ap.add_argument('--line', type=float, default=None)
    ap.add_argument('--label', required=True)
    ap.add_argument('--odds', type=int, default=None)
    ap.add_argument('--score', type=int, default=None,
                    help='headline score shown on the card')
    ap.add_argument('--narrative-file', required=True)
    ap.add_argument('--date', default=None)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--force-past-lock', action='store_true')
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    et = now - dt.timedelta(hours=4)
    date = a.date or et.date().isoformat()
    print(f'=== set_potd_manual · {date} · ET {et:%H:%M} · '
          f'{"APPLY" if a.apply else "DRY RUN"} ===')
    if et.hour >= LOCK_ET_HOUR and not a.force_past_lock:
        print(f'  REFUSED — past the {LOCK_ET_HOUR:02d}:00 ET card lock. '
              f'--force-past-lock to override.')
        return 1

    # select=* rather than naming kickoff columns: they differ per sport
    # (kickoff_utc on football, commence_time_utc on NBA/NHL) and naming a
    # missing one 400s the WHOLE select, which then reads as "no such game".
    # That is exactly what happened on the first run here.
    r = requests.get(f'{SB}/rest/v1/{CTX[a.sport]}', headers=H, timeout=30,
                     params={'select': '*', 'game_id': f'eq.{a.game}',
                             'limit': '1'})
    if r.status_code != 200:
        print(f'  REFUSED — ctx read HTTP {r.status_code}: {r.text[:200]}')
        return 1
    rows = r.json() or []
    if not rows:
        print(f'  REFUSED — no {a.sport} game {a.game!r}')
        return 1
    g = rows[0]
    home, away = str(g['home_team']), str(g['away_team'])
    matchup = f'{away} @ {home}'
    team = home if a.side == 'HOME' else away
    if a.side in ('HOME', 'AWAY') and team.lower() not in a.label.lower():
        print(f'  REFUSED — label {a.label!r} does not name {team!r}')
        return 1
    narrative = open(a.narrative_file, encoding='utf-8').read().strip()
    if len(narrative) < 120:
        print('  REFUSED — narrative under 120 chars; a POTD needs a case')
        return 1

    print(f'  game   : {matchup}')
    print(f'  pick   : {a.label}  ({a.mtype}/{a.side}/{a.line})  odds {a.odds}')
    print(f'  words  : {len(narrative.split())}')
    if not a.apply:
        print('  [DRY] nothing written. Re-run with --apply.')
        return 0

    hist = {'bet_date': date, 'sport': a.sport, 'game': matchup,
            'lean': a.label, 'narrative': narrative, 'result': None,
            'resolved_at': None, 'sweat_score': a.score}
    up = requests.post(f'{SB}/rest/v1/daily_best_bet_history'
                       f'?on_conflict=bet_date',
                       headers=HW_UP, timeout=30, data=json.dumps(hist))
    print(f'  history upsert -> HTTP {up.status_code}'
          + ('' if up.status_code in (200, 201, 204) else f'  {up.text[:200]}'))

    payload = {
        '_source': 'manual', 'anchor': 'manual_owner_pick',
        'confidence': 'lean', 'generatedAt': date,
        'game': {'matchup': matchup, 'away_team': away, 'home_team': home,
                 'commence_time': g.get('kickoff_utc')
                 or g.get('commence_time_utc')},
        'matchup': matchup, 'leanDisplay': a.label, 'narrative': narrative,
        'pipelineGenerated': False, 'sport': a.sport,
        'score': {'total': a.score, 'source': 'manual'},
        'context': {'market': a.mtype, 'side': a.side, 'line': a.line,
                    'odds': a.odds},
        '_manual': {'by': 'andy', 'at': now.isoformat(),
                    'why': 'engine passed (top conviction below threshold); '
                           'pick selected on measured supports, not score'},
    }
    cu = requests.post(f'{SB}/rest/v1/jerry_cache?on_conflict=cache_key',
                       headers=HW_UP, timeout=30,
                       data=json.dumps({
                           'cache_key': f'best_bet_{date}',
                           'game_id': f'best_bet_{date}', 'sport': a.sport,
                           'narrative': narrative, 'data': payload,
                           'fetched_at': now.isoformat()}))
    print(f'  cache   upsert -> HTTP {cu.status_code}'
          + ('' if cu.status_code in (200, 201, 204) else f'  {cu.text[:200]}'))

    # a 204 is not a write — read both back
    b1 = requests.get(f'{SB}/rest/v1/daily_best_bet_history', headers=H,
                      timeout=30, params={'bet_date': f'eq.{date}',
                                          'select': 'lean,sport,result',
                                          'limit': '1'})
    b2 = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H, timeout=30,
                      params={'cache_key': f'eq.best_bet_{date}',
                              'select': 'data', 'limit': '1'})
    h = (b1.json() or [{}])[0] if b1.status_code == 200 else {}
    c = (b2.json() or [{}])[0].get('data') or {} if b2.status_code == 200 else {}
    if isinstance(c, str):
        c = json.loads(c)
    print(f'  re-read history: lean={h.get("lean")!r} sport={h.get("sport")} '
          f'result={h.get("result")!r}')
    print(f'  re-read cache  : leanDisplay={c.get("leanDisplay")!r} '
          f'noPlay={c.get("noPlay")!r}')
    ok = (h.get('lean') == a.label and c.get('leanDisplay') == a.label
          and not c.get('noPlay'))
    print('  ✓ POTD set on both surfaces' if ok
          else '  ⚠ MISMATCH — one surface did not take the write')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
