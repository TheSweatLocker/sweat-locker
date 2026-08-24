"""Enrich nfl_game_context with Madden ratings + Top 100 fields (2026-08-24).

Joins nfl_madden_ratings + nfl_madden_player_ratings + nfl_top100_snapshot
into nfl_game_context so shadow signals can read via ctx.field.

Runs AFTER seed_nfl_madden_launch.py + AFTER nfl_game_context builder.
Idempotent — uses PATCH targeting existing rows (same pattern as
enrich_ctx_roster_physicality after 8/24 bug fix).

USAGE
─────
    python enrich_ctx_nfl_madden.py                  # current week, current season
    python enrich_ctx_nfl_madden.py --week 1
    python enrich_ctx_nfl_madden.py --season 2026
    python enrich_ctx_nfl_madden.py --days-ahead 14

FIELDS WRITTEN
──────────────
  home_madden_ovr / away_madden_ovr / home_madden_off / away_madden_off /
  home_madden_def / away_madden_def
  madden_ovr_gap_home = home_ovr - away_ovr
  madden_off_gap_home = home_off - away_def  (home offense vs opp defense)
  madden_off_gap_away = away_off - home_def
  home_qb_madden_ovr / away_qb_madden_ovr / madden_qb_delta_home
  home_top100_count / away_top100_count
  home_qb_top10_flag / away_qb_top10_flag
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}


def _get_latest_week(season: int) -> int:
    """Return the most recent week_snapshot with data (fallback to 0 = launch)."""
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_madden_ratings?season=eq.{season}'
        f'&select=week_snapshot&order=week_snapshot.desc&limit=1',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=10).read())
        return rows[0]['week_snapshot'] if rows else 0
    except Exception:
        return 0


def load_team_ratings(season: int, week: int) -> dict:
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_madden_ratings'
        f'?season=eq.{season}&week_snapshot=eq.{week}'
        f'&select=team,ovr,off_rating,def_rating,ovr_rank',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=15).read())
        return {row['team']: row for row in rows}
    except Exception as e:
        print(f'[err] team load: {e}', flush=True)
        return {}


def load_qb_ratings(season: int, week: int) -> dict:
    """Return {team: qb_ovr} — takes the highest-OVR QB per team as starter."""
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_madden_player_ratings'
        f'?season=eq.{season}&week_snapshot=eq.{week}&position=eq.QB'
        f'&select=team,player_name,ovr&order=ovr.desc',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception as e:
        print(f'[err] qb load: {e}', flush=True)
        return {}
    qbs = {}
    for row in rows:
        t = row['team']
        # Highest OVR wins (rows already sorted desc)
        if t not in qbs:
            qbs[t] = {'name': row['player_name'], 'ovr': float(row['ovr']) if row.get('ovr') is not None else None}
    return qbs


def load_top100(season: int) -> dict:
    """Return {team: [(rank, position), ...]} for aggregate counts + QB Top 10 check."""
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_top100_snapshot'
        f'?season=eq.{season}&select=team,rank,position,player_name',
        headers=H_READ)
    try:
        rows = json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception:
        return {}
    by_team = {}
    for row in rows:
        t = row.get('team')
        if not t: continue
        by_team.setdefault(t, []).append(row)
    return by_team


def load_upcoming(days_ahead: int) -> list[dict]:
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    start = date.today().isoformat()
    r = urllib.request.Request(
        f'{SB}/rest/v1/nfl_game_context'
        f'?game_date=gte.{start}&game_date=lte.{end}'
        f'&select=game_id,home_team,away_team,game_date',
        headers=H_READ)
    try:
        return json.loads(urllib.request.urlopen(r, timeout=15).read())
    except Exception as e:
        print(f'[err] ctx load: {e}', flush=True)
        return []


def compute_fields(home: str, away: str,
                   team_ratings: dict, qb_ratings: dict,
                   top100_by_team: dict) -> dict:
    """Compute all ctx fields for one game. Returns dict of only non-None values."""
    h = team_ratings.get(home) or {}
    a = team_ratings.get(away) or {}
    h_ovr = h.get('ovr'); a_ovr = a.get('ovr')
    h_off = h.get('off_rating'); a_off = a.get('off_rating')
    h_def = h.get('def_rating'); a_def = a.get('def_rating')

    out = {}
    if h_ovr is not None: out['home_madden_ovr'] = float(h_ovr)
    if a_ovr is not None: out['away_madden_ovr'] = float(a_ovr)
    if h_off is not None: out['home_madden_off'] = float(h_off)
    if a_off is not None: out['away_madden_off'] = float(a_off)
    if h_def is not None: out['home_madden_def'] = float(h_def)
    if a_def is not None: out['away_madden_def'] = float(a_def)
    if h_ovr is not None and a_ovr is not None:
        out['madden_ovr_gap_home'] = float(h_ovr) - float(a_ovr)
    if h_off is not None and a_def is not None:
        out['madden_off_gap_home'] = float(h_off) - float(a_def)
    if a_off is not None and h_def is not None:
        out['madden_off_gap_away'] = float(a_off) - float(h_def)

    # QB
    h_qb = qb_ratings.get(home) or {}
    a_qb = qb_ratings.get(away) or {}
    h_qb_ovr = h_qb.get('ovr'); a_qb_ovr = a_qb.get('ovr')
    if h_qb_ovr is not None: out['home_qb_madden_ovr'] = h_qb_ovr
    if a_qb_ovr is not None: out['away_qb_madden_ovr'] = a_qb_ovr
    if h_qb_ovr is not None and a_qb_ovr is not None:
        out['madden_qb_delta_home'] = h_qb_ovr - a_qb_ovr

    # Top 100
    h_t100 = top100_by_team.get(home) or []
    a_t100 = top100_by_team.get(away) or []
    out['home_top100_count'] = len(h_t100)
    out['away_top100_count'] = len(a_t100)
    # QB Top 10 flag (any QB from this team ranked 1-10)
    out['home_qb_top10_flag'] = any(r.get('position') == 'QB' and r.get('rank', 999) <= 10 for r in h_t100)
    out['away_qb_top10_flag'] = any(r.get('position') == 'QB' and r.get('rank', 999) <= 10 for r in a_t100)

    return out


def patch_ctx(game_id: str, fields: dict) -> bool:
    if not fields: return False
    qgid = urllib.parse.quote(game_id, safe='')
    url = f'{SB}/rest/v1/nfl_game_context?game_id=eq.{qgid}'
    body = json.dumps(fields).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers=H_WRITE, method='PATCH')
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception as e:
        print(f'[warn] patch failed for {game_id}: {str(e)[:120]}', flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int, default=datetime.now().year)
    ap.add_argument('--week', type=int, default=None,
                    help='week_snapshot to read. Default = latest available.')
    ap.add_argument('--days-ahead', type=int, default=14)
    args = ap.parse_args()

    season = args.season
    week = args.week if args.week is not None else _get_latest_week(season)

    print(f'\n[start] NFL ctx enrichment season={season} week={week} horizon={args.days_ahead}d',
          flush=True)

    team = load_team_ratings(season, week)
    qbs = load_qb_ratings(season, week)
    t100 = load_top100(season)
    if not team:
        print(f'[abort] no team ratings for season {season} week {week}. '
              f'Run seed_nfl_madden_launch.py first.', flush=True)
        return
    print(f'[data] teams={len(team)} qbs={len(qbs)} top100_teams={len(t100)}',
          flush=True)

    upcoming = load_upcoming(args.days_ahead)
    print(f'[games] {len(upcoming)} in horizon', flush=True)

    ok, no_data = 0, 0
    for g in upcoming:
        fields = compute_fields(g['home_team'], g['away_team'], team, qbs, t100)
        if not fields:
            no_data += 1
            continue
        if patch_ctx(g['game_id'], fields):
            ok += 1

    print(f'\n[done] enriched={ok} no_data={no_data}', flush=True)


if __name__ == '__main__':
    main()
