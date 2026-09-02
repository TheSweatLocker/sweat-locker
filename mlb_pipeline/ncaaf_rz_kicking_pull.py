"""NCAAF red-zone + kicking stats pull (2026-09-02).

Extends ncaaf_team_stats with red-zone TD rate + FG% + opponent RZ
defense. Also patches ncaaf_game_context with home/away rz_td_rate
for Vault Match matches_fn convenience.

CFBD /stats/season/advanced has scoringOpportunities per team:
  {'opportunities': N, 'points': X, 'pointsPerOpportunity': Y}
Approximation: rz_td_rate ≈ pointsPerOpportunity / 6.5 (avg pts/RZ trip
in college is ~5, TD trips avg ~6.5-7 pts w/ PAT). Not exact but
directional.

FG% requires /stats/season kicking fields — not currently exposed.
Placeholder for future CFBD roster-stats API. rz_td_rate is the
primary signal.

Usage:
    python ncaaf_rz_kicking_pull.py                # current season
    python ncaaf_rz_kicking_pull.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
SB_KEY = os.environ.get('SUPABASE_KEY')
CFBD_KEY = os.environ.get('CFBD_API_KEY')
H_READ  = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}
CFBD_BASE = 'https://api.collegefootballdata.com'

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _current_cfb_season() -> int:
    today = datetime.now(timezone.utc).date()
    return today.year if today.month >= 6 else today.year - 1


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def fetch_rz_data(season: int) -> dict:
    """Return {team: {rz_td_rate, opp_rz_td_rate, rz_score_rate}}."""
    if not CFBD_KEY:
        print('  ⚠ CFBD_API_KEY missing')
        return {}
    r = requests.get(
        f'{CFBD_BASE}/stats/season/advanced',
        headers={'Authorization': f'Bearer {CFBD_KEY}'},
        params={'year': season},
        timeout=30,
    )
    if r.status_code != 200:
        print(f'  ⚠ CFBD advanced {r.status_code}')
        return {}
    out = {}
    for row in r.json() or []:
        team = row.get('team')
        if not team: continue
        off = row.get('offense') or {}
        deff = row.get('defense') or {}
        # Offense scoringOpportunities
        off_so = off.get('fieldPosition') or {}  # placeholder if key differs
        # Actual key varies by CFBD version — try both
        off_ppo = _f(off.get('pointsPerOpportunity')) or _f((off.get('scoringOpportunities') or {}).get('pointsPerOpportunity'))
        def_ppo = _f(deff.get('pointsPerOpportunity')) or _f((deff.get('scoringOpportunities') or {}).get('pointsPerOpportunity'))
        # Approximation: TD rate = points/opp / 6.5 (avg pts per TD w/ PAT)
        # Bounded 0-1
        rz_td_rate = min(1.0, off_ppo / 6.5) if off_ppo else None
        opp_rz_td_rate = min(1.0, def_ppo / 6.5) if def_ppo else None
        # Score rate approximation: points/opp / 5.0 (avg CFB pts/RZ trip ≈ 5)
        rz_score_rate = min(1.0, off_ppo / 5.0) if off_ppo else None
        out[team] = {
            'rz_td_rate': round(rz_td_rate, 3) if rz_td_rate else None,
            'opp_rz_td_rate': round(opp_rz_td_rate, 3) if opp_rz_td_rate else None,
            'rz_score_rate': round(rz_score_rate, 3) if rz_score_rate else None,
        }
    return out


def upsert_team_stats(season: int, data: dict, dry_run: bool = False) -> int:
    if not data: return 0
    n = 0
    for team, vals in data.items():
        if all(v is None for v in vals.values()): continue
        payload = {'team': team, 'season': season, **vals}
        if dry_run:
            print(f'  [DRY] {team[:30]:30} {vals}')
            n += 1; continue
        r = requests.post(
            f'{SB}/rest/v1/ncaaf_team_stats?on_conflict=team,season',
            headers={**H_WRITE, 'Prefer': 'resolution=merge-duplicates'},
            json=payload, timeout=15,
        )
        if r.status_code in (200, 201, 204): n += 1
    return n


def patch_game_context(data: dict, dry_run: bool = False) -> int:
    """Patch home/away rz_td_rate onto upcoming games for Vault Match."""
    today_iso = datetime.now(timezone.utc).date().isoformat()
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_context',
        headers=H_READ,
        params={
            'select': 'game_id,home_team,away_team',
            'game_date': f'gte.{today_iso}',
            'limit': '400',
        },
        timeout=20,
    )
    games = r.json() if r.status_code == 200 else []
    n = 0
    for g in games:
        home = g.get('home_team'); away = g.get('away_team')
        h_d = data.get(home) or {}
        a_d = data.get(away) or {}
        patch = {
            'home_rz_td_rate':     h_d.get('rz_td_rate'),
            'away_rz_td_rate':     a_d.get('rz_td_rate'),
            'home_opp_rz_td_rate': h_d.get('opp_rz_td_rate'),
            'away_opp_rz_td_rate': a_d.get('opp_rz_td_rate'),
        }
        if all(v is None for v in patch.values()): continue
        if dry_run:
            n += 1; continue
        pr = requests.patch(
            f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json=patch, timeout=15,
        )
        if pr.status_code in (200, 204): n += 1
    return n


def run(dry_run: bool = False) -> None:
    season = _current_cfb_season()
    print(f'== NCAAF RZ/kicking pull · season {season}'
          f'{" [DRY]" if dry_run else ""} ==')
    data = fetch_rz_data(season)
    if not data:
        print('  no data (early season or CFBD missing scoringOpportunities)')
        return
    print(f'  RZ data for {len(data)} teams')
    n1 = upsert_team_stats(season, data, dry_run)
    n2 = patch_game_context(data, dry_run)
    prefix = '[DRY] ' if dry_run else '✓ '
    print(f'{prefix}team_stats upserted: {n1} · game_context patched: {n2}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
