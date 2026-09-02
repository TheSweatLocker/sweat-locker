"""MLB team pitching puller (2026-09-01).

Persists MLB team-wide pitching stats to `mlb_team_pitching` on a
nightly cron. Fills the last gap in team_stats_rolling MLB coverage
(offense from mlb_team_offense + bullpen from mlb_bullpen_stats are
already persisted; team-wide staff pitching was live-only via
generate_mlb_game_reads.py fetch_team_pitching_snapshots).

Same fetch logic as fetch_team_pitching_snapshots — MLB StatsAPI
`/teams/{team_id}/stats?stats=season&group=pitching&season=YYYY`
per team. 30 calls total per run, ~3-5 seconds wall clock.

Derived per-9 metrics computed here so team_stats_rolling can rank
directly without ratio math in SQL:
    k_per_9    = k * 9 / ip
    bb_per_9   = bb * 9 / ip
    hr_per_9   = hr_allowed * 9 / ip
    k_bb_ratio = k / bb (dimensionless)

Schema: supabase/migrations/20260901k_mlb_team_pitching.sql
CLI:
    python mlb_team_pitching_pull.py                 # current season
    python mlb_team_pitching_pull.py --season 2025
    python mlb_team_pitching_pull.py --dry-run
"""
from __future__ import annotations
import argparse, json, os, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_WRITE = {'apikey': KEY, 'Authorization': f'Bearer {KEY}',
           'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Reuse the team ID map from generate_mlb_game_reads (kept in sync manually).
# 30 MLB teams + Athletics fallback for the mid-2024 team-name shift.
_MLB_TEAM_IDS = {
    "Arizona Diamondbacks": 109, "Atlanta Braves": 144, "Baltimore Orioles": 110,
    "Boston Red Sox": 111, "Chicago Cubs": 112, "Chicago White Sox": 145,
    "Cincinnati Reds": 113, "Cleveland Guardians": 114, "Colorado Rockies": 115,
    "Detroit Tigers": 116, "Houston Astros": 117, "Kansas City Royals": 118,
    "Los Angeles Angels": 108, "Los Angeles Dodgers": 119, "Miami Marlins": 146,
    "Milwaukee Brewers": 158, "Minnesota Twins": 142, "New York Mets": 121,
    "New York Yankees": 147, "Athletics": 133,
    "Philadelphia Phillies": 143, "Pittsburgh Pirates": 134, "San Diego Padres": 135,
    "San Francisco Giants": 137, "Seattle Mariners": 136, "St. Louis Cardinals": 138,
    "Tampa Bay Rays": 139, "Texas Rangers": 140, "Toronto Blue Jays": 141,
    "Washington Nationals": 120,
}


def _f(v):
    if v is None: return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    if v is None: return None
    try: return int(float(v))
    except (TypeError, ValueError): return None


def _per9(count: Optional[float], ip: Optional[float]) -> Optional[float]:
    if count is None or ip is None or ip <= 0: return None
    return round(count * 9.0 / ip, 2)


def fetch_team(team_name: str, team_id: int, season: int) -> Optional[dict]:
    """Fetch season pitching aggregate for one team. Returns dict or None."""
    url = (f'https://statsapi.mlb.com/api/v1/teams/{team_id}/stats'
           f'?stats=season&group=pitching&season={season}')
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f'  ⚠ {team_name}: fetch failed ({e})')
        return None
    for split in data.get('stats', []):
        for sp in split.get('splits', []):
            st = sp.get('stat', {})
            ip = _f(st.get('inningsPitched'))
            k = _i(st.get('strikeOuts'))
            bb = _i(st.get('baseOnBalls'))
            hr = _i(st.get('homeRuns'))
            row = {
                'team':       team_name,
                'season':     season,
                'era':        _f(st.get('era')),
                'whip':       _f(st.get('whip')),
                'baa':        _f(st.get('avg')),
                'k':          k,
                'bb':         bb,
                'hr_allowed': hr,
                'ip':         ip,
                'k_per_9':    _per9(k, ip),
                'bb_per_9':   _per9(bb, ip),
                'hr_per_9':   _per9(hr, ip),
                'k_bb_ratio': round(k / bb, 2) if (k and bb and bb > 0) else None,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }
            return row
    return None


def run(season: int, dry_run: bool = False) -> int:
    print(f'=== MLB team pitching pull · season {season} ===')
    rows = []
    for team_name, team_id in _MLB_TEAM_IDS.items():
        row = fetch_team(team_name, team_id, season)
        if row:
            rows.append(row)
            print(f'  {team_name:25}  era {row["era"] or "—":<5}  '
                  f'whip {row["whip"] or "—":<5}  '
                  f'K/9 {row["k_per_9"] or "—":<5}  '
                  f'BB/9 {row["bb_per_9"] or "—"}')
    if not rows:
        print('  no rows fetched — MLB StatsAPI down?'); return 0
    if dry_run:
        print(f'\n[DRY] would upsert {len(rows)} rows'); return len(rows)

    written = 0
    for i in range(0, len(rows), 30):
        chunk = rows[i:i+30]
        r = requests.post(f'{SB}/rest/v1/mlb_team_pitching?on_conflict=team,season',
                          headers=H_WRITE, json=chunk, timeout=30)
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ upsert failed: {r.status_code} {r.text[:150]}')
    print(f'\nwrote {written} rows to mlb_team_pitching')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--season', type=int, default=None,
                   help='MLB season year (default: current)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    season = args.season or datetime.now(timezone.utc).year
    run(season=season, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
