"""Join nfl_game_context to nfl_game_results despite incompatible ids.

Andy 2026-09-27, asking whether the sharp-money indicator has actually
worked in the NFL: it cannot be answered, because the two tables share
no key.

    nfl_game_context.game_id   60e1d27b083a335b722c2c852ea85612
    nfl_game_results.game_id   2023_13_CAR_TB
    overlap between the id sets                              ZERO

Consequences, all measured on 2026-09-27:
  * no NFL pick, signal or money-flow reading has EVER been joined to a
    result, so nothing NFL has been validated
  * only ~15 NFL side picks have graded receipts, against 935 for the
    sport overall — the rest could not be matched
  * the SHARP badge ships on NFL cards having never once been checked

Logged as project_nfl_game_id_mismatch_911 on 09-11 and still open.

WHY A BRIDGE RATHER THAN A SCHEMA CHANGE. Rewriting either table's
primary key touches every writer and every foreign reference, and this
pipeline has repeatedly been bitten by exactly that kind of wide change.
The two tables already agree on everything needed to identify a game —
both use the same team codes (ARI, ATL, BAL...) and carry a date — so a
lookup is enough, costs nothing, and cannot corrupt either side.

DATE TOLERANCE IS REQUIRED. Matching on (game_date, home, away) alone
resolves 274 of 316 current rows; the missing 42 are games the two
tables date a day apart (late kickoffs crossing UTC midnight). A +/- 1
day window recovers them without introducing ambiguity, because no team
plays twice inside two days.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import requests

_CACHE: dict = {}


def _env():
    sb = os.environ.get('SUPABASE_URL')
    key = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
           or os.environ.get('SUPABASE_KEY'))
    if not sb or not key:
        raise RuntimeError('SUPABASE_URL / key not in environment')
    return sb, {'apikey': key, 'Authorization': f'Bearer {key}'}


def _page(path: str, params: dict) -> list:
    sb, h = _env()
    out, off = [], 0
    while True:
        r = requests.get(f'{sb}/rest/v1/{path}', headers=h,
                         params=dict(params, limit='1000', offset=str(off)),
                         timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{path} -> {r.status_code}: {(r.text or "")[:160]}')
        body = r.json()
        if not isinstance(body, list):
            return out
        out += body
        if len(body) < 1000:
            return out
        off += 1000


def _d(v):
    try:
        return datetime.strptime(str(v)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def build_bridge(select: str = 'game_id,game_date,home_team,away_team,'
                                'spread_result,close_spread,home_score,away_score'
                 ) -> dict:
    """-> {context_game_id: results_row}. Cached per process."""
    key = select
    if key in _CACHE:
        return _CACHE[key]

    ctx = _page('nfl_game_context',
                {'select': 'game_id,game_date,home_team,away_team'})
    res = _page('nfl_game_results', {'select': select})

    # (home, away) -> [rows], so a date window can pick the right season.
    by_teams: dict = {}
    for r in res:
        by_teams.setdefault((r.get('home_team'), r.get('away_team')), []).append(r)

    out: dict = {}
    for c in ctx:
        cand = by_teams.get((c.get('home_team'), c.get('away_team')))
        if not cand:
            continue
        cd = _d(c.get('game_date'))
        if cd is None:
            continue
        best, best_gap = None, None
        for r in cand:
            rd = _d(r.get('game_date'))
            if rd is None:
                continue
            gap = abs((rd - cd).days)
            if gap <= 1 and (best_gap is None or gap < best_gap):
                best, best_gap = r, gap
        if best is not None:
            out[c['game_id']] = best

    _CACHE[key] = out
    return out


def coverage() -> tuple[int, int]:
    """-> (matched, total_context_rows). For health checks."""
    ctx = _page('nfl_game_context', {'select': 'game_id'})
    return len(build_bridge()), len(ctx)


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    for _l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '.env'), encoding='utf-8'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
    m, t = coverage()
    print(f'NFL context->results bridge: {m}/{t} rows resolved '
          f'({100.0*m/t:.0f}%)' if t else 'no context rows')
