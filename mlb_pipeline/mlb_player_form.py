"""mlb_player_form — as-of-date player form, read from stored history.

2026-09-22.

WHY THIS EXISTS. `backfill_prop_lookback.fetch_mlb_player_recent` asks
the MLB Stats API for a player's season gameLog at generation time and
slices the last N. That is where the L5/L10 leak came from: the fetch
had no upper date bound, so once a game was played it became the newest
entry in its own lookback window and the model was partly reading the
answer it was predicting.

`20b443cc` added a `before_date` filter. That works, but it is a guard
bolted onto a live fetch — the leak stays one forgotten argument away,
and the exact same call from anywhere else reintroduces it.

Reading from `mlb_player_game_log` removes the failure mode instead of
policing it. Every row carries the date the game happened, so a query
for games before date X cannot return a game from date X. There is no
argument to forget: the filter IS the query.

The second thing this buys is the one that actually cost a season: the
lookback is now REPRODUCIBLE. Ask it what a player's L10 looked like on
2026-07-14 and it answers the same way today, next month, and during a
backtest. A live fetch could never answer that question at all, which
is why a prop tier ladder ran five months before anyone could measure
it against the closing price.

DELIBERATELY NO LIVE FALLBACK. If the table has no rows for a player
this returns empty and the caller omits the signal. Falling back to the
API would mean two players on the same card were evaluated by different
rules — and would quietly restore the leak path for whoever slipped
through. Missing data should look missing.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'} if KEY else {}
TABLE = 'mlb_player_game_log'

# prop family -> the column that holds its outcome. Identical mapping to
# backfill_mlb_player_game_log.FAMILY_COL; kept beside the reader so a
# caller needs one import, not two.
FAMILY_COL = {
    'total_bases': 'tb', 'hits': 'h', 'rbis': 'rbi', 'hr': 'hr',
    'runs': 'r', 'batter_ks': 'so',
    'ks': 'p_so', 'bb': 'p_bb', 'ha': 'p_h', 'outs': 'outs', 'er': 'er',
}

_CACHE: dict[tuple, list[dict]] = {}


def family_of(prop_type: str) -> str:
    """'hits_over' -> 'hits'. Leaves an already-bare family alone."""
    base = str(prop_type or '').rsplit('_', 1)
    return base[0] if len(base) == 2 and base[1] in ('over', 'under') else str(prop_type or '')


def column_for(prop_type: str) -> str | None:
    return FAMILY_COL.get(family_of(prop_type))


def games_before(player_name: str, before_date: str, limit: int = 60) -> list[dict]:
    """This player's games strictly before `before_date`, newest first.

    `before_date` is required and has no default. A lookback with an
    optional date bound is how the last leak happened; make the caller
    state the date it is standing on.
    """
    if not (SB and KEY and player_name and before_date):
        return []
    key = (player_name.strip().lower(), before_date, limit)
    if key in _CACHE:
        return _CACHE[key]
    cols = ('game_date,game_pk,opponent,home_away,batting_order,is_starter,'
            + ','.join(sorted(set(FAMILY_COL.values()))) + ',ab,pa,ip')
    r = requests.get(
        f'{SB}/rest/v1/{TABLE}', headers=H, timeout=25,
        params={'select': cols,
                'player_name': f'eq.{player_name}',
                'game_date': f'lt.{before_date}',
                'order': 'game_date.desc,game_pk.desc',
                'limit': str(limit)})
    if r.status_code != 200:
        # Not silently []. An empty history and a failed read mean very
        # different things, and conflating them is the B11 class that
        # let a whole pipeline run on holes.
        raise RuntimeError(f'{TABLE} read {r.status_code}: {r.text[:180]}')
    rows = r.json()
    if not isinstance(rows, list):
        raise RuntimeError(f'{TABLE} returned non-list')
    _CACHE[key] = rows
    return rows


def recent_values(player_name: str, prop_type: str, n: int,
                  before_date: str) -> list[float]:
    """Last `n` values of this prop's stat, newest first, before the date.

    Games where the player did not participate in that phase are skipped
    rather than counted as zero — a reliever who did not pitch has no
    strikeout total, and scoring it 0 would drag every pitcher's L10 down
    toward whoever sat the most.
    """
    col = column_for(prop_type)
    if not col:
        return []
    out = []
    for g in games_before(player_name, before_date, limit=max(n * 3, 40)):
        v = g.get(col)
        if v is None:
            continue
        out.append(float(v))
        if len(out) >= n:
            break
    return out


def recent_rows(player_name: str, prop_type: str, n: int,
                before_date: str) -> list[dict]:
    """Per-game rows for the recent-form table, newest first.

    Shape matches what `fetch_mlb_player_recent_rows` fed into
    `signals._stat_last10`: {date, value, opponent, home_away, ip}.

    The old shape also carried `decision` (a pitcher's W/L). Checked
    before dropping it: nothing reads that key — not the client, not
    the renderer — so it is not worth a column. If the table ever wants
    it, the boxscore has it and it is a migration, not a redesign.
    """
    col = column_for(prop_type)
    if not col:
        return []
    out = []
    for g in games_before(player_name, before_date, limit=max(n * 3, 40)):
        v = g.get(col)
        if v is None:
            continue
        out.append({
            'date': str(g.get('game_date'))[:10],
            'value': float(v),
            'opponent': g.get('opponent'),
            'home_away': g.get('home_away'),
            'ip': g.get('ip'),
        })
        if len(out) >= n:
            break
    return out


def hit_count(values: list[float], line: float, direction: str) -> int:
    """How many of these games would have cashed this side of the line.

    Pushes (value exactly on the line) count for neither side, matching
    how the grader settles them.
    """
    d = str(direction or '').lower()
    if d.startswith('o'):
        return sum(1 for v in values if v > line)
    if d.startswith('u'):
        return sum(1 for v in values if v < line)
    return 0


def form(player_name: str, prop_type: str, line: float, direction: str,
         before_date: str) -> dict:
    """The lookback block the prop pipeline stamps onto a row.

    Every figure carries its own denominator — `l5_n` and `l10_n` are the
    games actually found, not the window asked for. "4 of the last 5"
    read off 2 games is the kind of claim feedback_sample_size_with_pct
    exists to stop.
    """
    col = column_for(prop_type)
    if not col:
        return {}
    games = games_before(player_name, before_date, limit=200)
    vals = [float(g[col]) for g in games if g.get(col) is not None]
    l5, l10 = vals[:5], vals[:10]
    out = {
        'player_l5_hit_count': hit_count(l5, line, direction),
        'player_l10_hit_count': hit_count(l10, line, direction),
        'l5_n': len(l5),
        'l10_n': len(l10),
        'season_n': len(vals),
        'player_lookback_source': 'mlb_player_game_log',
        'player_lookback_asof': before_date,
    }
    if vals:
        out['player_season_hit_pct'] = round(
            100.0 * hit_count(vals, line, direction) / len(vals), 1)
        out['l10_mean'] = round(sum(l10) / len(l10), 3) if l10 else None
        out['season_mean'] = round(sum(vals) / len(vals), 3)
    # The extreme flags used to fire off a full window regardless of how
    # many games backed it. Require the window to actually be full.
    out['player_l5_extreme_flag'] = (
        len(l5) == 5 and out['player_l5_hit_count'] in (0, 5))
    out['player_l10_extreme_flag'] = (
        len(l10) == 10 and out['player_l10_hit_count'] in (0, 10))
    return out


def clear_cache() -> None:
    _CACHE.clear()


if __name__ == '__main__':
    import argparse
    import json
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--player', required=True)
    ap.add_argument('--prop', default='hits_over')
    ap.add_argument('--line', type=float, default=0.5)
    ap.add_argument('--date', required=True, help='as-of date (exclusive)')
    a = ap.parse_args()
    d = str(a.prop).rsplit('_', 1)[-1]
    print(json.dumps(form(a.player, a.prop, a.line, d, a.date), indent=1))
