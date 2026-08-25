"""NFL injury report puller — nfl_data_py-backed.

Pulls the weekly injury report from nfl_data_py.import_injuries() and
writes to nfl_injuries. Team abbrev normalized to match nfl_team_stats.

Cadence:
  - Wed 8pm ET (early practice reports)
  - Fri 8pm ET (final practice + game-status designations)
  - Sun 11am ET (final active/inactive check)

Usage:
  python nfl_injuries_pull.py                # current season
  python nfl_injuries_pull.py --season 2026
  python nfl_injuries_pull.py --week 3
  python nfl_injuries_pull.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

import requests

try:
    import nfl_data_py as nfl
except ImportError:
    print('nfl_data_py not installed — pip install nfl_data_py')
    sys.exit(1)

SB = os.environ['SUPABASE_URL']
SB_KEY = os.environ['SUPABASE_KEY']
H_WRITE = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}',
           'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# nfl_data_py uses standard abbrevs — matches our nfl_team_stats.team.
# Add remaps only if we hit divergences.
TEAM_MAP: dict[str, str] = {}
def _map_team(t): return TEAM_MAP.get(t, t)


def fetch_injuries(season: int) -> list:
    """import_injuries takes list of seasons, returns DataFrame.

    Handles pre-season 404s gracefully: nfl_data_py hits a per-season
    parquet URL on GitHub that doesn't get published until practice
    reports begin (Week 1 = first week of Sept). Off-season and August
    calls will 404 — that's expected, not a failure.
    """
    from urllib.error import HTTPError
    try:
        df = nfl.import_injuries([season])
    except HTTPError as e:
        if e.code == 404:
            print(f'  ℹ️ season {season} injuries parquet not published yet '
                  f'(pre-season / Week 1 not started) — 0 rows')
            return []
        raise
    except Exception as e:
        # Some pandas versions bubble the HTTPError inside a chained wrap
        msg = str(e)
        if '404' in msg and 'injuries' in msg.lower():
            print(f'  ℹ️ season {season} injuries parquet not published yet '
                  f'(pre-season / Week 1 not started) — 0 rows')
            return []
        raise
    if df is None or df.empty:
        return []
    return df.to_dict(orient='records')


def upsert_batch(rows: list, dry_run: bool = False) -> int:
    if not rows:
        return 0
    if dry_run:
        for r in rows[:5]:
            print(f'  [DRY] {r["team"]} W{r["week"]} {r["player_name"]} · {r.get("injury_status") or "—"}')
        print(f'  [DRY] ({len(rows)} total)')
        return len(rows)
    # batch of 500 max to keep PostgREST happy
    ok = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i+500]
        r = requests.post(
            f'{SB}/rest/v1/nfl_injuries?on_conflict=season,week,team,player_name',
            headers=H_WRITE, json=chunk, timeout=45,
        )
        if r.status_code in (200, 201, 204):
            ok += len(chunk)
        else:
            print(f'  ⚠ batch {i}-{i+len(chunk)}: {r.status_code} — {r.text[:200]}')
    return ok


def run(season: Optional[int] = None, week: Optional[int] = None,
        dry_run: bool = False) -> None:
    now_utc = datetime.now(timezone.utc)
    season = season or now_utc.year
    print(f'== NFL injuries · season {season}{" · W" + str(week) if week else ""} ==')

    raw = fetch_injuries(season)
    print(f'  {len(raw)} rows from nfl_data_py.import_injuries')

    rows = []
    for r in raw:
        wk = r.get('week')
        if week is not None and wk != week:
            continue
        team_raw = r.get('team') or r.get('club_code') or ''
        if not team_raw:
            continue
        player_name = r.get('full_name') or r.get('player_name') or r.get('player')
        if not player_name:
            continue
        rd = r.get('date_modified') or r.get('report_date')
        rd_str = None
        if rd is not None:
            try:
                rd_str = rd.isoformat() if hasattr(rd, 'isoformat') else str(rd)[:10]
            except Exception:
                rd_str = None

        def _clean(v):
            # nfl_data_py returns NaN (float) for empty cells → JSON-invalid, DB-noise
            if v is None: return None
            try:
                import math
                if isinstance(v, float) and math.isnan(v): return None
            except Exception:
                pass
            s = str(v).strip()
            return None if s.lower() in ('', 'nan', 'none') else s

        status = _clean(r.get('report_status') or r.get('game_status'))
        if not status:
            continue  # skip rows with no meaningful status — noise
        rows.append({
            'season': int(r.get('season') or season),
            'week': int(wk) if wk is not None else 0,
            'team': _map_team(team_raw),
            'player_name': player_name,
            'player_id': _clean(r.get('gsis_id') or r.get('player_id')),
            'position': _clean(r.get('position')),
            'injury_status': status,
            'practice_status': _clean(r.get('practice_status')),
            'body_part': _clean(r.get('report_primary_injury') or r.get('primary_injury')),
            'report_date': rd_str,
        })

    print(f'  {len(rows)} rows after filter')
    written = upsert_batch(rows, dry_run=dry_run)
    print(f'\nSummary: {written}/{len(rows)} injury rows persisted')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--season', type=int)
    p.add_argument('--week', type=int)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(season=args.season, week=args.week, dry_run=args.dry_run)
