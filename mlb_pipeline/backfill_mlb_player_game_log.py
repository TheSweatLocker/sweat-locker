"""backfill_mlb_player_game_log — persist what we were throwing away.

2026-09-22.

WHY THIS EXISTS. Every MLB batter signal in the pipeline — L5/L10 form,
season hit rate, hitless streaks — was computed from a LIVE MLB Stats
API gameLog fetch at generation time and then discarded. The prop row
kept the verdict; nothing kept the inputs.

That cost us twice:

  * The lookback leak. `fetch_mlb_player_recent` pulled the season log
    with no upper date bound, so once a game was played it became the
    newest entry in its own L5/L10 window — the model was partly reading
    the answer. Fixed in 20b443cc with a `before_date` filter, but that
    is a guard on a live fetch. A stored, date-stamped log makes the
    leak structurally impossible: you cannot see a row that is not there
    yet, because every row carries the date it happened.

  * Nothing could be backtested. "What did the model know on 07-14" has
    no answer when the inputs were never written down. That is the real
    reason a prop tier ladder ran an entire season before anyone could
    measure it against the closing price.

WHAT WE ALREADY HAD. mlb_pipeline_props.final_value holds 24,048 real
outcomes over 785 players and 148 dates. Genuine, but sparse — only
players who drew a prop, ~49 player-games a day against a ~270-player
slate. This fills in the rest, and the sparse set becomes the CHECK:
--verify compares the backfill against those independently recorded
values and reports disagreements rather than assuming a clean pull.

SHAPE. One boxscore call per game, not one gameLog call per player.
~15 games a day means ~2,400 calls for a season, and it needs no roster
list — the boxscore names everyone who appeared, including the bench
player who pinch-hit once, whom a player-driven pull would miss.

IDEMPOTENT + RESUMABLE. UNIQUE (player_id, game_pk) with on_conflict
merge. A run that dies at game 1,400 is re-run, not restarted: --resume
skips game_pks already stored.

CLI
    python backfill_mlb_player_game_log.py --season 2026
    python backfill_mlb_player_game_log.py --start 2026-09-01 --end 2026-09-21
    python backfill_mlb_player_game_log.py --season 2026 --resume
    python backfill_mlb_player_game_log.py --verify        # vs final_value
    python backfill_mlb_player_game_log.py --dry-run --start 2026-09-20 --end 2026-09-20
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for _line in _env.read_text(encoding='utf-8').split('\n'):
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

STATS_API = 'https://statsapi.mlb.com/api/v1'
TABLE = 'mlb_player_game_log'
SEASON_BOUNDS = {2026: ('2026-03-01', '2026-11-15')}


# ── source ───────────────────────────────────────────────────────────
def _get(url: str, **params) -> dict:
    """GET that raises instead of returning {}.

    Deliberately not the `return r.json() if r.status_code == 200 else {}`
    pattern that appears 319 times in this pipeline — a 429 and an
    empty day must not look alike to a backfill, or it writes a hole and
    reports success.
    """
    for attempt in range(4):
        try:
            r = requests.get(url, params=params or None, timeout=30)
        except requests.exceptions.RequestException as e:
            if attempt == 3:
                raise RuntimeError(f'{url} unreachable: {e}') from e
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2.0 * (attempt + 1))
            continue
        raise RuntimeError(f'{url} -> {r.status_code}: {r.text[:200]}')
    raise RuntimeError(f'{url} exhausted retries')


def games_between(start: str, end: str) -> list[dict]:
    """Every completed regular/post-season game in the window."""
    d = _get(f'{STATS_API}/schedule', sportId=1, startDate=start, endDate=end)
    out = []
    for day in d.get('dates', []):
        for g in day.get('games', []):
            state = ((g.get('status') or {}).get('abstractGameState') or '')
            if state != 'Final':
                continue
            if (g.get('gameType') or 'R') not in ('R', 'F', 'D', 'L', 'W'):
                continue        # skip spring / exhibition
            out.append({
                'game_pk': g['gamePk'],
                'game_date': day.get('date'),
                'away': (g['teams']['away']['team'] or {}).get('name'),
                'home': (g['teams']['home']['team'] or {}).get('name'),
            })
    return out


def _i(v):
    """None stays None; '' and junk become None. 0 must survive."""
    if v is None or v == '':
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _f(v):
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rows_for_game(g: dict) -> list[dict]:
    """One row per player who appeared, batting and/or pitching."""
    bx = _get(f'{STATS_API}/game/{g["game_pk"]}/boxscore')
    out = []
    for side in ('away', 'home'):
        team = bx['teams'][side]
        team_name = (team.get('team') or {}).get('name') or g[side]
        opp = g['home'] if side == 'away' else g['away']
        for _key, p in (team.get('players') or {}).items():
            person = p.get('person') or {}
            pid = person.get('id')
            if not pid:
                continue
            st = p.get('stats') or {}
            bat = st.get('batting') or {}
            pit = st.get('pitching') or {}
            # A player listed but who neither batted nor pitched is a
            # bench body. Storing him adds rows that mean nothing and
            # skew any "games played" denominator.
            if not bat and not pit:
                continue
            order_raw = p.get('battingOrder')
            order = _i(order_raw)
            out.append({
                'player_id': pid,
                'player_name': person.get('fullName'),
                'team': team_name,
                'opponent': opp,
                'game_pk': g['game_pk'],
                'game_date': g['game_date'],
                'home_away': side,
                'pos': (p.get('position') or {}).get('abbreviation'),
                'batting_order': order,
                # battingOrder is spot*100; a trailing non-zero means a
                # substitute took that spot (701 = second man to bat 7th).
                'is_starter': (order % 100 == 0) if order is not None else None,
                'pa': _i(bat.get('plateAppearances')),
                'ab': _i(bat.get('atBats')),
                'h': _i(bat.get('hits')),
                'doubles': _i(bat.get('doubles')),
                'triples': _i(bat.get('triples')),
                'hr': _i(bat.get('homeRuns')),
                'rbi': _i(bat.get('rbi')),
                'r': _i(bat.get('runs')),
                'bb': _i(bat.get('baseOnBalls')),
                'so': _i(bat.get('strikeOuts')),
                'tb': _i(bat.get('totalBases')),
                'sb': _i(bat.get('stolenBases')),
                'hbp': _i(bat.get('hitByPitch')),
                'sf': _i(bat.get('sacFlies')),
                'outs': _i(pit.get('outs')),
                'ip': _f(pit.get('inningsPitched')),
                'p_h': _i(pit.get('hits')),
                'p_r': _i(pit.get('runs')),
                'er': _i(pit.get('earnedRuns')),
                'p_bb': _i(pit.get('baseOnBalls')),
                'p_so': _i(pit.get('strikeOuts')),
                'bf': _i(pit.get('battersFaced')),
                'pitches': _i(pit.get('pitchesThrown')),
            })
    return out


# ── sink ─────────────────────────────────────────────────────────────
def stored_game_pks() -> set[int]:
    seen, off = set(), 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H_READ, timeout=60,
                         params={'select': 'game_pk', 'limit': 1000,
                                 'offset': off})
        if r.status_code != 200:
            raise RuntimeError(f'{TABLE} read failed {r.status_code}: {r.text[:200]}')
        chunk = r.json()
        if not chunk:
            break
        seen |= {x['game_pk'] for x in chunk}
        off += 1000
        if len(chunk) < 1000:
            break
    return seen


def write(rows: list[dict]) -> int:
    if not rows:
        return 0
    # PostgREST needs an identical key set across a batch upsert
    # (PGRST102) — see feedback_postgrest_batch_normalize_keys.
    keys = set()
    for r in rows:
        keys |= set(r.keys())
    rows = [{k: r.get(k) for k in keys} for r in rows]
    total = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i + 500]
        r = requests.post(
            f'{SB}/rest/v1/{TABLE}?on_conflict=player_id,game_pk',
            headers=H_WRITE, json=batch, timeout=60)
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f'upsert failed {r.status_code}: {r.text[:300]}')
        total += len(batch)
    return total


# ── verify ───────────────────────────────────────────────────────────
FAMILY_COL = {
    'total_bases': 'tb', 'hits': 'h', 'rbis': 'rbi', 'hr': 'hr',
    'runs': 'r', 'batter_ks': 'so',
    'ks': 'p_so', 'bb': 'p_bb', 'ha': 'p_h', 'outs': 'outs', 'er': 'er',
}


def verify() -> int:
    """Cross-check the backfill against outcomes recorded independently.

    mlb_pipeline_props.final_value was graded off MLB box scores months
    ago by a different code path. If this pull is sound the two agree;
    where they disagree, one of them is wrong and we want to know which
    before anything is built on top.
    """
    print('=== verify: game log vs mlb_pipeline_props.final_value ===')
    props, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props', headers=H_READ,
                         timeout=60,
                         params={'select': 'game_date,player_name,prop_type,final_value',
                                 'final_value': 'not.is.null',
                                 'order': 'game_date.asc,id.asc',
                                 'limit': 1000, 'offset': off})
        if r.status_code != 200:
            raise RuntimeError(f'props read {r.status_code}: {r.text[:200]}')
        chunk = r.json()
        if not chunk:
            break
        props += chunk
        off += 1000
        if len(chunk) < 1000:
            break

    logs, off = {}, 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H_READ, timeout=60,
                         params={'select': 'player_name,game_date,' +
                                           ','.join(sorted(set(FAMILY_COL.values()))),
                                 'limit': 1000, 'offset': off})
        if r.status_code != 200:
            raise RuntimeError(f'{TABLE} read {r.status_code}: {r.text[:200]}')
        chunk = r.json()
        if not chunk:
            break
        for x in chunk:
            # A doubleheader gives a player two rows on one date; the
            # prop row does not say which leg, so sum them. A same-day
            # total is the only comparison that can be made honestly.
            k = (str(x['player_name']).strip().lower(), str(x['game_date'])[:10])
            acc = logs.setdefault(k, {})
            for c in set(FAMILY_COL.values()):
                if x.get(c) is not None:
                    acc[c] = (acc.get(c) or 0) + x[c]
        off += 1000
        if len(chunk) < 1000:
            break

    checked = agree = 0
    misses = []
    for p in props:
        fam = str(p['prop_type']).rsplit('_', 1)[0]
        col = FAMILY_COL.get(fam)
        if not col:
            continue
        k = (str(p['player_name']).strip().lower(), str(p['game_date'])[:10])
        got = logs.get(k, {}).get(col)
        if got is None:
            continue
        checked += 1
        if abs(float(got) - float(p['final_value'])) < 1e-6:
            agree += 1
        elif len(misses) < 12:
            misses.append((p['player_name'], p['game_date'], p['prop_type'],
                           p['final_value'], got))
    print(f'  comparable (player, date, family) pairs : {checked}')
    if checked:
        print(f'  agree                                   : {agree} '
              f'({100 * agree / checked:.2f}%)')
        print(f'  disagree                                : {checked - agree}')
    for m in misses:
        print(f'    {str(m[0])[:22]:22} {str(m[1])[:10]} {m[2]:14} '
              f'prop={m[3]}  log={m[4]}')
    return checked - agree


# ── main ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int)
    ap.add_argument('--start')
    ap.add_argument('--end')
    ap.add_argument('--resume', action='store_true',
                    help='skip game_pks already stored')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--sleep', type=float, default=0.12)
    a = ap.parse_args()

    if a.verify:
        sys.exit(1 if verify() else 0)

    if a.season:
        start, end = SEASON_BOUNDS.get(
            a.season, (f'{a.season}-03-01', f'{a.season}-11-15'))
    else:
        start, end = a.start, a.end
    if not (start and end):
        ap.error('need --season or --start/--end')
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()
    end = min(end, today)

    print(f'=== backfill {TABLE} · {start} → {end}'
          f'{" · DRY" if a.dry_run else ""} ===')
    games = games_between(start, end)
    print(f'  final games in window: {len(games)}')

    if a.resume and not a.dry_run:
        have = stored_game_pks()
        before = len(games)
        games = [g for g in games if g['game_pk'] not in have]
        print(f'  already stored: {before - len(games)} · to pull: {len(games)}')

    written = failed = 0
    buf: list[dict] = []
    for i, g in enumerate(games, 1):
        try:
            rows = rows_for_game(g)
        except RuntimeError as e:
            failed += 1
            print(f'  ⚠ {g["game_date"]} pk={g["game_pk"]}: {e}')
            continue
        buf += rows
        if i % 25 == 0 or i == len(games):
            if a.dry_run:
                print(f'  [DRY] {i}/{len(games)} games · {len(buf)} rows buffered')
                if i >= 50:
                    break
            else:
                written += write(buf)
                print(f'  {i}/{len(games)} games · {written} rows written')
            buf = []
        time.sleep(a.sleep)
    if buf and not a.dry_run:
        written += write(buf)

    print(f'\n  games processed: {len(games)}  rows written: {written}  '
          f'failed games: {failed}')
    if failed:
        print('  ⚠ re-run with --resume to pick up the failures')


if __name__ == '__main__':
    main()
