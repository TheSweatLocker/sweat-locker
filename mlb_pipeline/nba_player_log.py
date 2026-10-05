#!/usr/bin/env python3
"""NBA player lookback — L5/L10/season form for the prop board.

Reads `nba_player_game_logs` (populated by nba_player_log_ingest.py from ESPN
box scores) and answers the questions a prop card has to answer:

    how many of the last 10 cleared this line?
    what is the average, and how volatile?
    what has this player done against THIS opponent?
    home vs road?

THE LEAK GUARD IS NOT OPTIONAL
------------------------------
`before_date` excludes the game being predicted and everything after it, and
the filter runs BEFORE the slice, not after. Slicing first and filtering after
silently returns fewer than n games — or, worse, includes the game whose
outcome you are trying to predict.

This is the same defect that invalidated every MLB prop PRIME number before
2026-09-22: the L5 lookback included the game being scored, the published
record read +21.6% ROI, and the clean number was -6.5%. It had to be fixed by
hand in nhl_player_log.py afterwards. Building it in from the start here.

Callers that are scoring a slate MUST pass before_date=<slate date>. The
functions do not default to "today" on purpose — a silent default is how a
leak gets reintroduced.

    python nba_player_log.py --player "Jayson Tatum" --prop points --line 26.5
"""
from __future__ import annotations
import argparse, collections, os, statistics, sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_HERE = Path(__file__).parent
_env = _HERE / '.env'
if _env.exists():
    for _l in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _l and not _l.startswith('#'):
            k, v = _l.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
TABLE = 'nba_player_game_logs'

# prop_type -> the column(s) that produce its value. Combos are summed, which
# is how the books define them (PRA = points + rebounds + assists).
PROP_COLUMNS = {
    'points': ('points',),
    'rebounds': ('rebounds',),
    'assists': ('assists',),
    'threes': ('fg3m',),
    'steals': ('steals',),
    'blocks': ('blocks',),
    'turnovers': ('turnovers',),
    'pra': ('points', 'rebounds', 'assists'),
    'pr': ('points', 'rebounds'),
    'pa': ('points', 'assists'),
    'ra': ('rebounds', 'assists'),
    'stocks': ('steals', 'blocks'),
}


def _norm_prop(prop_type: str) -> str:
    t = str(prop_type or '').strip().lower()
    t = t.replace('player_', '')
    for suf in ('_over', '_under'):
        if t.endswith(suf):
            t = t[: -len(suf)]
    aliases = {'pts': 'points', 'reb': 'rebounds', 'ast': 'assists',
               'fg3m': 'threes', 'three_pointers': 'threes', '3pt': 'threes',
               'stl': 'steals', 'blk': 'blocks', 'to': 'turnovers',
               'points_rebounds_assists': 'pra',
               'points_rebounds': 'pr', 'points_assists': 'pa',
               'rebounds_assists': 'ra'}
    return aliases.get(t, t)


def fetch_log(player_name: str = None, player_id: str = None,
              before_date: str = None, limit: int = 400) -> list[dict]:
    """Raw rows for one player, newest first, strictly before `before_date`.

    The date bound is applied in the QUERY, so a leaked row cannot reach the
    caller even if the caller forgets to filter.
    """
    if not (player_name or player_id):
        raise ValueError('need player_name or player_id')
    params = {'select': '*', 'order': 'game_date.desc', 'limit': limit}
    if player_id:
        params['player_id'] = f'eq.{player_id}'
    else:
        params['player_name'] = f'eq.{player_name}'
    if before_date:
        params['game_date'] = f'lt.{str(before_date)[:10]}'
    r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=60, params=params)
    if r.status_code not in (200, 206):
        return []
    return r.json()


def _value(row: dict, cols) -> float | None:
    tot = 0.0
    for c in cols:
        v = row.get(c)
        if v is None:
            return None            # a missing component makes the combo unknown
        tot += float(v)
    return tot


def recent_form(prop_type: str, player_name: str = None, player_id: str = None,
                n: int = 10, line: float = None, before_date: str = None,
                opponent: str = None, home: bool = None,
                rows: list = None) -> dict | None:
    """L-n form for a player/prop. Pass before_date when scoring a slate.

    `rows` lets a caller fetch once and compute several windows without
    re-querying; it must already be newest-first and date-bounded.
    """
    key = _norm_prop(prop_type)
    cols = PROP_COLUMNS.get(key)
    if not cols:
        return None
    log = rows if rows is not None else fetch_log(
        player_name=player_name, player_id=player_id, before_date=before_date)
    if not log:
        return None

    # FILTER FIRST, THEN SLICE. Reversing these two lines is the leak.
    elig = log
    if before_date:
        bd = str(before_date)[:10]
        elig = [g for g in elig
                if str(g.get('game_date') or '')[:10] and
                str(g.get('game_date'))[:10] < bd]
    if opponent:
        elig = [g for g in elig
                if g.get('opponent_abbrev')
                and str(g['opponent_abbrev']).upper() == opponent.upper()]
    if home is not None:
        # is_home arrives with migration 20261004a and is NULL until a row has
        # been re-ingested. bool(None) is False, so a naive comparison
        # classified EVERY game as a road game and made the home split vanish
        # while the road split silently reported the full season. A column we
        # do not have must yield NO answer, never a wrong one.
        elig = [g for g in elig
                if g.get('is_home') is not None
                and bool(g['is_home']) == bool(home)]

    vals, mins, dates = [], [], []
    for g in elig[:n]:
        v = _value(g, cols)
        if v is None:
            continue
        vals.append(v)
        if g.get('minutes') is not None:
            mins.append(float(g['minutes']))
        dates.append(str(g.get('game_date'))[:10])
    if not vals:
        return None

    out = {
        'prop': key, 'n': len(vals), 'values': vals, 'dates': dates,
        'avg': round(statistics.mean(vals), 2),
        'median': round(statistics.median(vals), 2),
        'sd': round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0,
        'min': min(vals), 'max': max(vals),
        'avg_minutes': round(statistics.mean(mins), 1) if mins else None,
    }
    if line is not None:
        over = sum(1 for v in vals if v > float(line))
        push = sum(1 for v in vals if abs(v - float(line)) < 1e-9)
        out.update({'line': float(line), 'hit_over': over,
                    'hit_under': len(vals) - over - push, 'push': push,
                    'hit_over_pct': round(100 * over / len(vals), 1)})
    return out


def full_profile(prop_type: str, line: float, player_name: str = None,
                 player_id: str = None, before_date: str = None,
                 opponent: str = None) -> dict:
    """Every lens the prop card shows, from ONE fetch."""
    log = fetch_log(player_name=player_name, player_id=player_id,
                    before_date=before_date)
    kw = dict(prop_type=prop_type, line=line, before_date=before_date, rows=log)
    prof = {
        'player': player_name or player_id,
        'games_available': len(log),
        'l5': recent_form(n=5, **kw),
        'l10': recent_form(n=10, **kw),
        'season': recent_form(n=400, **kw),
        'home': recent_form(n=400, home=True, **kw),
        'road': recent_form(n=400, home=False, **kw),
    }
    if opponent:
        prof['vs_opp'] = recent_form(n=400, opponent=opponent, **kw)
    return prof


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--player', required=True)
    ap.add_argument('--prop', default='points')
    ap.add_argument('--line', type=float)
    ap.add_argument('--before')
    ap.add_argument('--opponent')
    a = ap.parse_args()

    prof = full_profile(a.prop, a.line, player_name=a.player,
                        before_date=a.before, opponent=a.opponent)
    print(f'\n=== {prof["player"]} · {_norm_prop(a.prop)} '
          f'{"@ " + str(a.line) if a.line is not None else ""} ===')
    print(f'    {prof["games_available"]} games in the log'
          f'{" strictly before " + a.before if a.before else ""}\n')
    print('%-8s %4s %7s %7s %6s %6s %8s %s'
          % ('window', 'n', 'avg', 'median', 'sd', 'mins', 'over%', 'recent'))
    for k in ('l5', 'l10', 'season', 'home', 'road', 'vs_opp'):
        f = prof.get(k)
        if not f:
            continue
        print('%-8s %4d %7.2f %7.2f %6.2f %6s %8s %s'
              % (k, f['n'], f['avg'], f['median'], f['sd'],
                 f['avg_minutes'] if f['avg_minutes'] is not None else '-',
                 f'{f["hit_over_pct"]}%' if 'hit_over_pct' in f else '-',
                 f['values'][:8]))
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
