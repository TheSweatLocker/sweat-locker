"""backfill_nhl_season_results — recover a missing NHL season of results.

THE GAP. nhl_game_results coverage as of 2026-09-21:

    2024-10 .. 2025-04    1,335 games   full 2024-25 season
    2025-05 .. 2026-08        0 games   <- ENTIRE 2025-26 SEASON MISSING
    2026-09                  22 games   preseason
    2026-10                  25 games   future (odds pulls)

A seventeen-month hole. Consequences, all live:

  * nhl_elo trains on nhl_game_results, so Elo ratings are TWO SEASONS
    stale — which is why they sit near the 1500 default (CAR 1520.9,
    FLA 1525.5 after a full season of K=6 updates).
  * backfill_nhl_team_tendencies looks back 400 days and found 14 resolved
    games, so every ATS/ML/OU form column came back None for every team.
  * No L10 goals-for/against, so those confluence dimensions cannot vote.

WHAT THIS WRITES, AND WHAT IT DELIBERATELY DOES NOT. Scores, winner, OT/SO
flags and total_goals come from the NHL API scoreboard and are facts. It
does NOT write close_puckline or close_total: this source has no closing
lines, and the last thing this table needs is another column filled with
whatever was nearest to hand. close_puckline is already contaminated with
MONEYLINE values on all 1,120 historical rows (see backfill_nhl_grades) —
that is exactly what happens when a backfill writes a field it does not
actually have. Lines stay NULL and the rows stay honestly ungraded for
sides and totals until a real line source backfills them.

Team names come from nhl_data_client._full_team_name (placeName +
commonName), so they match the odds feed and do not repeat the
"New York" collapse that made Rangers and Islanders the same string.

IDEMPOTENT: upserts on game_id, so a re-run repairs rather than duplicates.

CLI
  python backfill_nhl_season_results.py --dry-run
  python backfill_nhl_season_results.py --start 2025-10-01 --end 2026-06-30
"""
from __future__ import annotations
import argparse, os, sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except ImportError:
    Retry = None

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

_S = requests.Session()
if Retry is not None:
    _S.mount('https://', HTTPAdapter(
        max_retries=Retry(total=4, backoff_factor=0.5,
                          status_forcelist=(500, 502, 503, 504, 429),
                          allowed_methods=frozenset(['GET', 'POST'])),
        pool_connections=8, pool_maxsize=8))

from nhl_data_client import get_scoreboard

# 2025-26 regular season + playoffs. Defaults chosen to cover the hole
# without re-walking 2024-25, which is already complete.
DEFAULT_START = '2025-10-01'
DEFAULT_END = '2026-06-30'


def existing_ids() -> set:
    out = set()
    for off in range(0, 20000, 1000):
        r = _S.get(f'{SB}/rest/v1/nhl_game_results', headers=H_READ, timeout=40,
                   params={'select': 'game_id', 'limit': 1000, 'offset': off})
        if r.status_code != 200:
            break
        chunk = r.json()
        if not isinstance(chunk, list):
            break
        out |= {str(x['game_id']) for x in chunk if x.get('game_id')}
        if len(chunk) < 1000:
            break
    return out


def run(start: str, end: str, dry_run: bool = False) -> int:
    d0 = datetime.fromisoformat(start).date()
    d1 = datetime.fromisoformat(end).date()
    have = existing_ids()
    print(f'=== backfill_nhl_season_results · {start} .. {end} '
          f'{"(DRY)" if dry_run else "(APPLY)"} ===')
    print(f'  nhl_game_results already holds {len(have)} game_ids')

    batch, stats = [], Counter()
    empty_streak = 0
    day = d0
    while day <= d1:
        ds = day.isoformat()
        try:
            games = get_scoreboard(ds)
        except Exception as e:
            print(f'  ⚠ {ds}: {type(e).__name__}: {str(e)[:60]}')
            games = []
            stats['fetch_error'] += 1
        if not games:
            empty_streak += 1
            # The NHL has real off-days and an off-season; a run of empty
            # dates is normal, not a failure. Only report it so a genuinely
            # dead endpoint is visible rather than looking like a quiet
            # calendar.
            if empty_streak == 40:
                print(f'  … 40 consecutive empty dates ending {ds} '
                      f'(off-season is expected in this range)')
        else:
            empty_streak = 0
        for g in games:
            gid = str(g.get('game_id') or '')
            hs, as_ = g.get('home_score'), g.get('away_score')
            if not gid or hs is None or as_ is None:
                stats['incomplete'] += 1
                continue
            if gid in have:
                stats['already_present'] += 1
                continue
            batch.append({
                'game_id': gid,
                'game_date': ds,
                'home_team': g.get('home_team'),
                'away_team': g.get('away_team'),
                'home_score': int(hs),
                'away_score': int(as_),
                'home_win': int(hs) > int(as_),
                'total_goals': int(hs) + int(as_),
                'went_to_ot': bool(g.get('went_to_ot')),
                'went_to_so': bool(g.get('went_to_so')),
                # close_puckline / close_total intentionally omitted — this
                # source has no lines and inventing them is how the existing
                # contamination happened.
            })
            stats['new'] += 1
        day += timedelta(days=1)

    print(f'  new games found: {stats["new"]}  already present: '
          f'{stats["already_present"]}  incomplete: {stats["incomplete"]}'
          + (f'  fetch errors: {stats["fetch_error"]}' if stats['fetch_error'] else ''))
    if not batch:
        return 0
    dates = sorted({b['game_date'] for b in batch})
    print(f'  span: {dates[0]} .. {dates[-1]} across {len(dates)} dates')
    if dry_run:
        for b in batch[:5]:
            print(f'    [DRY] {b["game_date"]} {b["away_team"]} {b["away_score"]} '
                  f'@ {b["home_team"]} {b["home_score"]}')
        print(f'  [DRY] would upsert {len(batch)}')
        return len(batch)

    ok = 0
    for i in range(0, len(batch), 200):
        chunk = batch[i:i + 200]
        r = _S.post(f'{SB}/rest/v1/nhl_game_results?on_conflict=game_id',
                    headers=H_WRITE, json=chunk, timeout=60)
        if r.status_code in (200, 201, 204):
            ok += len(chunk)
        else:
            print(f'  ⚠ upsert {r.status_code}: {r.text[:200]}')
    print(f'  upserted {ok}')
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--start', default=DEFAULT_START)
    p.add_argument('--end', default=DEFAULT_END)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    run(a.start, a.end, dry_run=a.dry_run)


if __name__ == '__main__':
    main()
