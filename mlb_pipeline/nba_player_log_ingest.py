#!/usr/bin/env python3
"""NBA per-player game logs from ESPN box scores.

WHY (2026-10-04)
----------------
NBA opens 10-21 and `nba_player_game_logs` has ZERO rows, so there is no
prop board and no L5/L10 lookback. Sides and totals were tested first and
carry no edge over the closing line (ATS 51.1% on n=2,203, breakeven 52.4%),
so props are the NBA opportunity — our highest-volume surface, 99.8% priced.

SOURCE — ESPN, because the alternatives are dead
------------------------------------------------
Probed live 2026-10-04:

    balldontlie v1          401 Unauthorized (BDL_API_KEY unset)
    stats.nba.com           connection timeout (blocked)
    ESPN summary            200, full player box scores

and `nba_game_results.game_id` IS the ESPN event id — 401902644 returns
MIA 129 @ TOR 105 for 2026-10-03, matching our row exactly. So every game we
already store is directly fetchable with no id mapping.

ESPN labels per player, which map to the prop markets we care about:

    MIN PTS FG 3PT FT REB AST TO STL BLK OREB DREB PF +/-

DNP HANDLING, WHICH MATTERS FOR L10
-----------------------------------
ESPN emits a row for inactive players with an empty or '--' stat line. Those
are SKIPPED, not written as zeros. A 0-minute row and a did-not-play row mean
different things to a lookback average: writing DNPs as 0 would drag every
injured star's L10 toward zero and manufacture fake UNDER edges. This is the
same class of error as the MLB prop L5 leak — a lookback that quietly
includes rows it should not.

SCHEMA TOLERANT
---------------
The columns props really need (fg3m, opponent_abbrev, is_home, shooting
splits) arrive with migration 20261004a, which Andy applies by hand. This
script discovers which columns actually exist and writes only those, so it
works before AND after the migration — and reports what it had to drop.

    python nba_player_log_ingest.py --since 2025-10-01            # dry
    python nba_player_log_ingest.py --since 2025-10-01 --apply
    python nba_player_log_ingest.py --all --apply                 # full history
"""
from __future__ import annotations
import argparse, collections, json, os, sys, time
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
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}

ESPN = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'}
SLEEP = 0.45             # polite; ESPN is unauthenticated and we want it to stay up
TABLE = 'nba_player_game_logs'

# ESPN label -> our column
LABELS = {
    'MIN': 'minutes', 'PTS': 'points', 'REB': 'rebounds', 'AST': 'assists',
    'STL': 'steals', 'BLK': 'blocks', 'TO': 'turnovers', 'OREB': 'oreb',
    'DREB': 'dreb', 'PF': 'pf', '+/-': 'plus_minus',
}
# Labels of the form "made-attempted"
SPLITS = {'FG': ('fgm', 'fga'), '3PT': ('fg3m', 'fg3a'), 'FT': ('ftm', 'fta')}

_SESSION = requests.Session()


def page(table: str, params: dict) -> list:
    out, off = [], 0
    while True:
        p = dict(params)
        p.update({'limit': 1000, 'offset': off})
        r = requests.get(f'{SB}/rest/v1/{table}', headers=H, timeout=120, params=p)
        if r.status_code not in (200, 206):
            print(f'  ⚠ {table} {r.status_code}: {r.text[:140]}')
            return out
        b = r.json()
        out += b
        if len(b) < 1000:
            return out
        off += 1000


def live_columns() -> set:
    """Which columns the table actually has right now."""
    cand = (['game_id', 'game_date', 'player_id', 'player_name', 'team_abbrev',
             'minutes', 'points', 'rebounds', 'assists', 'steals', 'blocks',
             'turnovers', 'plus_minus']
            + ['season', 'opponent_abbrev', 'is_home', 'started', 'fg3m',
               'fg3a', 'fgm', 'fga', 'ftm', 'fta', 'oreb', 'dreb', 'pf'])
    have = set()
    for c in cand:
        r = requests.get(f'{SB}/rest/v1/{TABLE}', headers=H, timeout=20,
                         params={'select': c, 'limit': 1})
        if r.status_code in (200, 206):
            have.add(c)
    return have


def _int(v):
    s = str(v or '').strip()
    if s in ('', '--', '-'):
        return None
    neg = s.startswith('-')
    s2 = s.lstrip('+-')
    if not s2.isdigit():
        return None
    n = int(s2)
    return -n if neg else n


def parse_summary(doc: dict, game_id: str, game_date: str, season) -> list:
    """-> list of per-player dicts. DNPs are omitted, never zeroed."""
    bs = doc.get('boxscore') or {}
    teams = bs.get('players') or []
    hdr = (doc.get('header') or {})
    comp = (hdr.get('competitions') or [{}])[0]
    home_id = None
    for c in comp.get('competitors', []):
        if c.get('homeAway') == 'home':
            home_id = str(c.get('team', {}).get('id'))
    # team id -> abbrev, for opponent resolution
    abbrevs = {}
    for t in teams:
        tm = t.get('team') or {}
        abbrevs[str(tm.get('id'))] = (tm.get('abbreviation')
                                      or tm.get('shortDisplayName'))
    out = []
    for t in teams:
        tm = t.get('team') or {}
        tid = str(tm.get('id'))
        my = abbrevs.get(tid)
        opp = next((v for k, v in abbrevs.items() if k != tid), None)
        for block in (t.get('statistics') or []):
            labels = block.get('labels') or []
            for ath in (block.get('athletes') or []):
                stats = ath.get('stats') or []
                # DNP: ESPN gives an empty list, or a single note like 'DNP'
                if len(stats) < len(labels) or not stats:
                    continue
                person = ath.get('athlete') or {}
                row = {
                    'game_id': str(game_id),
                    'game_date': game_date,
                    'player_id': str(person.get('id') or ''),
                    'player_name': person.get('displayName'),
                    'team_abbrev': my,
                    'opponent_abbrev': opp,
                    'is_home': (tid == home_id) if home_id else None,
                    'started': bool(ath.get('starter')),
                    'season': season,
                }
                for lab, val in zip(labels, stats):
                    if lab in LABELS:
                        row[LABELS[lab]] = _int(val)
                    elif lab in SPLITS:
                        made, att = SPLITS[lab]
                        parts = str(val or '').split('-')
                        row[made] = _int(parts[0]) if len(parts) == 2 else None
                        row[att] = _int(parts[1]) if len(parts) == 2 else None
                if not row['player_id'] or row.get('minutes') is None:
                    continue       # no id, or did not actually play
                out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2025-10-01')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0, help='cap games (testing)')
    ap.add_argument('--refill', action='store_true',
                    help='re-fetch games already stored, to fill columns that '
                         'did not exist on the first pass')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    cols = live_columns()
    wanted = {'season', 'opponent_abbrev', 'is_home', 'started', 'fg3m',
              'fg3a', 'fgm', 'fga', 'ftm', 'fta', 'oreb', 'dreb', 'pf'}
    missing = sorted(wanted - cols)
    print(f'=== nba_player_log_ingest · {"APPLY" if args.apply else "DRY"} ===')
    print(f'  table columns available: {len(cols)}')
    if missing:
        print(f'  ⚠ migration 20261004a NOT applied yet — dropping: {missing}')
        print('    (core stats still ingest; re-run after the migration to fill these)')

    params = {'select': 'game_id,game_date,season,home_team,away_team,home_score',
              'home_score': 'not.is.null', 'order': 'game_date.desc'}
    if not args.all:
        params['game_date'] = f'gte.{args.since}'
    games = page('nba_game_results', params)
    # Skip games already ingested, so this is resumable.
    #
    # 2026-10-05: --refill exists because the first full pass ran BEFORE
    # migration 20261004a was applied, so live_columns() correctly dropped
    # fg3m / opponent_abbrev / is_home and 58,600 rows landed without them.
    # Resume-by-game would then skip every one of those games forever and the
    # columns would stay empty — a backfill that can never complete. With the
    # unique index now in place the upsert path merges, so re-fetching fills
    # the new columns without duplicating rows.
    done = set() if args.refill else {
        str(x['game_id']) for x in page(TABLE, {'select': 'game_id'})}
    todo = [g for g in games if str(g['game_id']) not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f'  games with a final score: {len(games)} · already ingested: '
          f'{len(done)} · to fetch: {len(todo)}\n')
    if not todo:
        print('nothing to do')
        return 0

    batch, written, fetched, skipped = [], 0, 0, collections.Counter()
    for i, g in enumerate(todo, 1):
        try:
            r = _SESSION.get(ESPN, params={'event': g['game_id']},
                             headers=UA, timeout=25)
        except Exception as e:
            skipped[f'fetch error: {type(e).__name__}'] += 1
            continue
        if r.status_code != 200:
            skipped[f'ESPN {r.status_code}'] += 1
            continue
        rows = parse_summary(r.json(), g['game_id'], g['game_date'], g.get('season'))
        if not rows:
            skipped['no player rows in box score'] += 1
        fetched += 1
        for row in rows:
            batch.append({k: v for k, v in row.items() if k in cols})
        if i % 25 == 0 or i == len(todo):
            print(f'  {i}/{len(todo)} games · {len(batch)} rows queued '
                  f'· written {written}')
        if args.apply and len(batch) >= 500:
            written += _flush(batch)
            batch = []
        time.sleep(SLEEP)

    if args.apply:
        written += _flush(batch)
        print(f'\nwrote {written} player-game rows from {fetched} box scores')
        # Verify by read-back rather than trusting the status code.
        n = page(TABLE, {'select': 'game_id'})
        print(f'read-back: {TABLE} now holds {len(n)} rows')
    else:
        print(f'\n[DRY] would write {len(batch)} rows from {fetched} box scores')
        if batch:
            print('  sample:')
            for b in batch[:4]:
                print('   ', {k: b.get(k) for k in
                              ('game_date', 'player_name', 'team_abbrev',
                               'opponent_abbrev', 'minutes', 'points',
                               'rebounds', 'assists', 'fg3m')})
    if skipped:
        print(f'  skipped: {dict(skipped)}')
    return 0


def _flush(batch: list) -> int:
    """Upsert if the unique index exists; plain insert if it does not.

    The (game_id, player_id) unique index ships with migration 20261004a,
    which Andy applies by hand — and until then PostgREST rejects the whole
    batch with 42P10 "no unique or exclusion constraint matching the ON
    CONFLICT specification". Tested, not assumed: the first --apply run wrote
    0 rows for exactly this reason.

    Falling back to a plain insert is safe because idempotency here is at GAME
    granularity, not row granularity: main() skips any game_id already present
    in the table, so a re-run never re-inserts a game it already has. Once the
    migration lands the upsert path takes over and becomes row-exact.
    """
    if not batch:
        return 0
    r = requests.post(f'{SB}/rest/v1/{TABLE}?on_conflict=game_id,player_id',
                      headers=H_W, timeout=120, data=json.dumps(batch))
    if r.status_code in (200, 201, 204):
        return len(batch)
    if r.status_code == 400 and '42P10' in r.text:
        r2 = requests.post(f'{SB}/rest/v1/{TABLE}',
                           headers={**H, 'Content-Type': 'application/json',
                                    'Prefer': 'return=minimal'},
                           timeout=120, data=json.dumps(batch))
        if r2.status_code in (200, 201, 204):
            return len(batch)
        print(f'  ⚠ insert fallback {r2.status_code}: {r2.text[:200]}')
        return 0
    print(f'  ⚠ write {r.status_code}: {r.text[:240]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
