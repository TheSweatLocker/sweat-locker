"""Sleeper NFL projections puller (2026-08-09 · Panel model primary source).

Sleeper is a free fantasy football app with a well-documented public API.
Their weekly projections are used by hundreds of thousands of fantasy
players and their model is competitive with the paid tiers.

Endpoints used:
  GET https://api.sleeper.app/v1/players/nfl
    → All active NFL players + team + position (one-shot metadata)
  GET https://api.sleeper.app/projections/nfl/regular/{season}/{week}
    → Per-player weekly projected stats

Sleeper's projection payload keys (empirically verified):
  pass_yd, pass_td, pass_int, pass_att, pass_cmp,
  rush_yd, rush_td, rush_att,
  rec, rec_yd, rec_td, rec_tgt,
  fgm, xpm,
  def_sack, def_int, def_ff, def_td,
  pts_ppr, pts_std, pts_half_ppr

CLI:
    python nfl_sleeper_projections_pull.py --season 2026 --week 1
    python nfl_sleeper_projections_pull.py --season 2026 --week 1 --preseason
    python nfl_sleeper_projections_pull.py --season 2026 --all-weeks
"""
from __future__ import annotations
import argparse, json, os, sys, time
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


SLEEPER_PLAYERS_URL = 'https://api.sleeper.app/v1/players/nfl'
SLEEPER_PROJ_URL = 'https://api.sleeper.app/v1/projections/nfl/{season_type}/{season}/{week}'


# Cache players metadata for a day (Sleeper updates infrequently)
_PLAYERS_CACHE_PATH = Path.home() / '.sweatlocker_sleeper_players.json'
_PLAYERS_CACHE_TTL = 86400


def load_players_meta() -> dict:
    """Load {player_id → {name, team, position}} — cached daily."""
    now = time.time()
    if _PLAYERS_CACHE_PATH.exists():
        try:
            data = json.loads(_PLAYERS_CACHE_PATH.read_text())
            if now - data.get('_fetched', 0) < _PLAYERS_CACHE_TTL:
                return data.get('players', {})
        except Exception: pass
    r = requests.get(SLEEPER_PLAYERS_URL, timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ Sleeper players {r.status_code}')
        return {}
    raw = r.json()
    # Slim payload
    out = {}
    for pid, p in raw.items():
        if not isinstance(p, dict): continue
        pos = p.get('position') or (p.get('fantasy_positions') or ['?'])[0]
        if pos not in ('QB','RB','WR','TE','K','DEF','DL','LB','DB','FLEX'):
            continue
        out[pid] = {
            'name': p.get('full_name') or f'{p.get("first_name","")} {p.get("last_name","")}',
            'team': p.get('team'), 'position': pos,
            'status': p.get('status') or p.get('injury_status'),
        }
    try:
        _PLAYERS_CACHE_PATH.write_text(json.dumps({'_fetched': now, 'players': out}))
    except Exception: pass
    return out


def fetch_projections(season: int, week: int, season_type: str = 'regular') -> list:
    """Fetch weekly projections from Sleeper. season_type: 'regular' | 'pre' | 'post'."""
    url = SLEEPER_PROJ_URL.format(season_type=season_type, season=season, week=week)
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        print(f'  ⚠ Sleeper projections {r.status_code} for {season}/{week}/{season_type}: {r.text[:150]}')
        return []
    data = r.json()
    if isinstance(data, dict):
        # {player_id: {stats}} shape
        return [{'player_id': pid, **stats} for pid, stats in data.items()]
    return data if isinstance(data, list) else []


def normalize(raw_row: dict, meta: dict, season: int, week: int, season_type: str) -> dict:
    """Map Sleeper's stat dict → nfl_player_projections row."""
    pid = raw_row.get('player_id')
    pm = meta.get(pid, {})
    st = raw_row.get('stats') if isinstance(raw_row.get('stats'), dict) else raw_row
    # Fantasy pts fields differ per endpoint version
    fp_ppr = st.get('pts_ppr') or st.get('fantasy_points_ppr')
    fp_std = st.get('pts_std') or st.get('fantasy_points')
    return {
        'source': 'sleeper',
        'season': season, 'week': week,
        'season_type': 'reg' if season_type == 'regular' else season_type[:3],
        'player_id': pid,
        'player_name': pm.get('name'),
        'team': pm.get('team'),
        'position': pm.get('position'),
        'proj_pass_yds': st.get('pass_yd'),
        'proj_pass_tds': st.get('pass_td'),
        'proj_pass_ints': st.get('pass_int'),
        'proj_pass_attempts': st.get('pass_att'),
        'proj_rush_yds': st.get('rush_yd'),
        'proj_rush_tds': st.get('rush_td'),
        'proj_rush_attempts': st.get('rush_att'),
        'proj_rec_yds': st.get('rec_yd'),
        'proj_rec_tds': st.get('rec_td'),
        'proj_receptions': st.get('rec'),
        'proj_targets': st.get('rec_tgt'),
        'proj_fg_made': st.get('fgm'),
        'proj_xp_made': st.get('xpm'),
        'proj_def_sacks': st.get('def_sack') or st.get('sack'),
        'proj_def_ints': st.get('def_int') or st.get('int'),
        'proj_def_fumbles': st.get('def_ff') or st.get('ff'),
        'proj_def_tds': st.get('def_td'),
        'proj_def_pts_allowed': st.get('pts_allow'),
        'proj_fantasy_pts': fp_ppr,
        'proj_fantasy_pts_std': fp_std,
        'status': pm.get('status'),
        'pulled_at': datetime.now(timezone.utc).isoformat(),
    }


def upsert_projections(rows: list, dry_run: bool = False) -> int:
    if not rows: return 0
    if dry_run:
        print(f'  DRY: would upsert {len(rows)} rows')
        for r in rows[:5]:
            print(f'    {r["team"]} {r["position"]} {r["player_name"]} — '
                  f'ppr={r["proj_fantasy_pts"]} pass_yd={r["proj_pass_yds"]}')
        return len(rows)
    written = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i+100]
        wr = requests.post(
            f'{SB}/rest/v1/nfl_player_projections?on_conflict=source,season,week,player_id',
            headers=H_W, data=json.dumps(chunk, default=str), timeout=15)
        if wr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ chunk {i} failed {wr.status_code}: {wr.text[:200]}')
    return written


def pull(season: int, week: int, season_type: str = 'regular', dry_run: bool = False) -> int:
    print(f'=== Sleeper NFL projections · {season} week {week} ({season_type}) ===')
    meta = load_players_meta()
    print(f'  {len(meta)} players in meta cache')
    raw = fetch_projections(season, week, season_type)
    print(f'  {len(raw)} projection rows from Sleeper')
    if not raw:
        return 0
    # Only keep players with team + position (skip fantasy-only FLEX etc.)
    normalized = [normalize(r, meta, season, week, season_type) for r in raw]
    valid = [n for n in normalized if n.get('team') and n.get('position') and n.get('proj_fantasy_pts') is not None]
    print(f'  {len(valid)} rows have team + position + ppr projection')
    if not valid:
        print('  first 3 raw rows for debugging:')
        for r in raw[:3]: print(f'    {json.dumps(r, default=str)[:300]}')
        return 0
    n = upsert_projections(valid, dry_run=dry_run)
    print(f'  ✓ upserted {n} projections')
    # Sample top 5 by fantasy pts
    top = sorted(valid, key=lambda r: -(r.get('proj_fantasy_pts') or 0))[:5]
    print(f'  top-5 projected fantasy pts:')
    for r in top:
        print(f'    {r["team"] or "??":3s} {r["position"]:3s} {r["player_name"]:22s}  {r["proj_fantasy_pts"]:5.1f} ppr')
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, required=True)
    ap.add_argument('--week', type=int)
    ap.add_argument('--all-weeks', action='store_true')
    ap.add_argument('--preseason', action='store_true')
    ap.add_argument('--postseason', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    season_type = 'regular'
    if args.preseason: season_type = 'pre'
    elif args.postseason: season_type = 'post'

    if args.all_weeks:
        max_week = 4 if season_type == 'pre' else (18 if season_type == 'regular' else 4)
        for w in range(1, max_week + 1):
            pull(args.season, w, season_type=season_type, dry_run=args.dry_run)
    elif args.week:
        pull(args.season, args.week, season_type=season_type, dry_run=args.dry_run)
    else:
        print('specify --week N or --all-weeks')


if __name__ == '__main__':
    main()
