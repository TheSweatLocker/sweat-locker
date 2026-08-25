"""ESPN Fantasy NFL projections puller (2026-08-09 · Panel ensemble source #2).

ESPN publishes weekly fantasy projections via their public league API.
Different scoring / assumptions than Sleeper → we get consensus by
averaging with Sleeper when both cover a player.

Endpoint (public league defaults, no auth needed):
  GET https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/
      seasons/{season}/segments/0/leaguedefaults/3?view=kona_player_info
      &scoringPeriodId={week}
  Headers: x-fantasy-filter (JSON) — filter to reduce 12MB payload

Position codes (defaultPositionId):
  1=QB · 2=RB · 3=WR · 4=TE · 5=K · 16=DEF
Pro team abbrev in `proTeamId` (int → team lookup)

Projection fields under `stats[]`:
  Each stat entry has:
    statSourceId: 0=real, 1=projected
    scoringPeriodId: week (0 = season)
    seasonId: season
    appliedTotal: fantasy pts (PPR-style based on league scoring)
    stats: dict of raw stat name → value
  Filter to statSourceId=1 (projected) + matching scoringPeriodId.

CLI:
  python nfl_espn_projections_pull.py --season 2025 --week 1
  python nfl_espn_projections_pull.py --season 2025 --all-weeks
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

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
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H, 'Content-Type': 'application/json',
       'Prefer': 'resolution=merge-duplicates,return=minimal'}


ESPN_URL = ('https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/'
            'seasons/{season}/segments/0/leaguedefaults/3')

# ESPN's defaultPositionId → position
POSITION_MAP = {1: 'QB', 2: 'RB', 3: 'WR', 4: 'TE', 5: 'K', 16: 'DEF'}

# ESPN proTeamId → team abbrev
PRO_TEAM_MAP = {
    1: 'ATL', 2: 'BUF', 3: 'CHI', 4: 'CIN', 5: 'CLE', 6: 'DAL', 7: 'DEN',
    8: 'DET', 9: 'GB', 10: 'TEN', 11: 'IND', 12: 'KC', 13: 'LV', 14: 'LAR',
    15: 'MIA', 16: 'MIN', 17: 'NE', 18: 'NO', 19: 'NYG', 20: 'NYJ',
    21: 'PHI', 22: 'ARI', 23: 'PIT', 24: 'LAC', 25: 'SF', 26: 'SEA',
    27: 'TB', 28: 'WAS', 29: 'CAR', 30: 'JAX', 33: 'BAL', 34: 'HOU',
}

# ESPN raw stat id → our field name
# Key numeric stat IDs from ESPN kona_player_info
STAT_MAP = {
    '3': 'proj_pass_yds',      # passing yards
    '4': 'proj_pass_tds',      # passing TDs
    '20': 'proj_pass_ints',    # interceptions thrown
    '0': 'proj_pass_attempts', # attempts
    '24': 'proj_rush_yds',
    '25': 'proj_rush_tds',
    '23': 'proj_rush_attempts',
    '42': 'proj_rec_yds',
    '43': 'proj_rec_tds',
    '53': 'proj_receptions',
    '58': 'proj_targets',
    '86': 'proj_fg_made',      # 0-99 yd made
    '89': 'proj_xp_made',
    # DEF stats (position=16)
    '99': 'proj_def_sacks',
    '95': 'proj_def_ints',
    '96': 'proj_def_fumbles',
    '93': 'proj_def_tds',
    '127': 'proj_def_pts_allowed',
}


def fetch_espn_projections(season: int, week: int) -> list:
    """Return list of ESPN player+projection payloads for a scoring period."""
    url = ESPN_URL.format(season=season)
    # Filter narrows the response size
    # Working filter shape verified 2026-08-09. ESPN requires sortPriority
    # + descending sortDirection when using sortAppliedStatTotal.
    x_filter = {
        'players': {
            'filterStatsForExternalIds': {'value': [season]},
            'filterStatsForSourceIds': {'value': [1]},
            'useFullProjectionTable': {'value': True},
            'sortAppliedStatTotal': {
                'sortAsc': False, 'sortPriority': 3,
                'value': f'10{week:02d}',
            },
            'sortAppliedStatTotalForScoringPeriodId': {
                'sortAsc': False, 'sortPriority': 2,
                'value': week,
            },
            'sortDraftRanks': {
                'sortPriority': 100, 'sortAsc': True, 'value': 'STANDARD',
            },
            'sortPercOwned': {
                'sortPriority': 4, 'sortAsc': False,
            },
            'limit': 2000,
            'offset': 0,
            'statSplitTypeId': 0,
            'scoringPeriodIds': [week],
        }
    }
    hdrs = {'x-fantasy-filter': json.dumps(x_filter),
            'User-Agent': 'Mozilla/5.0 (compatible; SweatLocker/1.0)'}
    r = requests.get(url, headers=hdrs, params={'view':'kona_player_info',
                                                  'scoringPeriodId': week}, timeout=45)
    if r.status_code != 200:
        print(f'  ⚠ ESPN {r.status_code}: {r.text[:200]}')
        return []
    return r.json().get('players', [])


def normalize(raw: dict, season: int, week: int) -> dict:
    """Map ESPN player payload → nfl_player_projections row shape."""
    p = raw.get('player') or {}
    pid = str(p.get('id')) if p.get('id') is not None else None
    if not pid: return None
    pos = POSITION_MAP.get(p.get('defaultPositionId'))
    if not pos: return None
    team = PRO_TEAM_MAP.get(p.get('proTeamId'))
    # Find projected stats for this scoring period
    # 2026-08-09: ESPN doesn't return per-scoringPeriod projections in the
    # default view — instead we get:
    #   statSplitTypeId=0: season total projection
    #   statSplitTypeId=2: per-game average projection ← use this for weekly
    # Take statSplit=2 as the "weekly projected" — stable per-game baseline.
    # Sleeper covers week-specific variance; ESPN is the ensemble anchor.
    proj_stats = None; fp_total = None
    for s in p.get('stats') or []:
        if s.get('statSourceId') != 1: continue
        if s.get('seasonId') != season: continue
        if s.get('statSplitTypeId') != 2: continue  # per-game avg
        proj_stats = s.get('stats') or {}
        fp_total = s.get('appliedTotal')
        break
    if proj_stats is None: return None
    row = {
        'source': 'espn_fantasy',
        'season': season, 'week': week, 'season_type': 'reg',
        'player_id': pid,
        'player_name': p.get('fullName'),
        'team': team, 'position': pos,
        'proj_fantasy_pts': round(fp_total, 2) if fp_total is not None else None,
        'proj_fantasy_pts_std': None,  # ESPN default is PPR
        'status': (p.get('injuryStatus') or 'ACTIVE'),
        'pulled_at': datetime.now(timezone.utc).isoformat(),
    }
    for stat_id, field in STAT_MAP.items():
        v = proj_stats.get(stat_id)
        if v is not None and v > 0:
            row[field] = round(v, 2)
    return row


def upsert(rows: list, dry_run: bool = False) -> int:
    """PostgREST batch upsert requires all rows share the same key set
    (per feedback_postgrest_batch_normalize_keys). Normalize each row to
    the union of all keys, filling missing with None."""
    if not rows: return 0
    if dry_run:
        print(f'  DRY: would upsert {len(rows)} ESPN rows')
        for r in rows[:5]:
            print(f'    {r["team"] or "??":3s} {r["position"]:3s} {r["player_name"]:22s} '
                  f'fp={r["proj_fantasy_pts"]}')
        return len(rows)
    # Union of all keys across all rows
    all_keys = set()
    for r in rows: all_keys.update(r.keys())
    # Normalize
    normalized = [{k: r.get(k) for k in all_keys} for r in rows]
    written = 0
    for i in range(0, len(normalized), 100):
        chunk = normalized[i:i+100]
        wr = requests.post(
            f'{SB}/rest/v1/nfl_player_projections?on_conflict=source,season,week,player_id',
            headers=H_W, data=json.dumps(chunk, default=str), timeout=15)
        if wr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ chunk {i} {wr.status_code}: {wr.text[:200]}')
    return written


def pull(season: int, week: int, dry_run: bool = False) -> int:
    print(f'=== ESPN NFL projections · {season} week {week} ===')
    raw = fetch_espn_projections(season, week)
    print(f'  {len(raw)} ESPN player payloads')
    if not raw: return 0
    rows = [normalize(p, season, week) for p in raw]
    valid = [r for r in rows if r and r.get('proj_fantasy_pts') is not None
                                  and r.get('team')]
    print(f'  {len(valid)} valid projections (team+fp)')
    if not valid: return 0
    n = upsert(valid, dry_run=dry_run)
    print(f'  ✓ upserted {n}')
    top = sorted(valid, key=lambda r: -(r.get('proj_fantasy_pts') or 0))[:5]
    print(f'  top-5 by fantasy pts:')
    for r in top:
        print(f'    {r["team"]:3s} {r["position"]:3s} {r["player_name"]:22s}  {r["proj_fantasy_pts"]:5.1f}')
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, required=True)
    ap.add_argument('--week', type=int)
    ap.add_argument('--all-weeks', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.all_weeks:
        for w in range(1, 19):
            pull(args.season, w, dry_run=args.dry_run)
    elif args.week:
        pull(args.season, args.week, dry_run=args.dry_run)
    else:
        print('specify --week N or --all-weeks')


if __name__ == '__main__':
    main()
