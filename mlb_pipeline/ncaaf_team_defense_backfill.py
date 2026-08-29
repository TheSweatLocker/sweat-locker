"""NCAAF team defense stats backfill (2026-08-28).

Fills the gap where ncaaf_team_stats only stores offense-side EPA
metrics. Bettors + Jerry need defensive matchup stats to reason about
CFB games where efficiency gaps drive most of the edge.

Method: for each finalized game in ncaaf_game_results, opponent's
SEASONAL offense EPA (from ncaaf_team_stats) is attributed to this
team's defense. Averaged across games → per-team defensive coverage.

Writes to `ncaaf_team_defense_stats` (migration 20260828d). Upsert on
(team, season, season_type).

Fields produced:
  - def_ppg                    opponent points per game (from scores)
  - def_pass_epa_allowed       opponent pass EPA/play, avg
  - def_rush_epa_allowed       opponent rush EPA/play, avg
  - def_success_rate_allowed   opponent success rate, avg
  - def_explosiveness_allowed  opponent explosiveness, avg

NOTE on raw yards: ncaaf_team_stats doesn't carry raw pass_yards /
rush_yards (CFBD /stats/season/advanced returns EPA-based only).
For raw yards allowed we'd need a follow-up pull from
CFBD /stats/season (non-advanced). Not included here.

CLI:
    python ncaaf_team_defense_backfill.py --season 2025
    python ncaaf_team_defense_backfill.py --all-seasons
    python ncaaf_team_defense_backfill.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

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


def load_results(season: int) -> list:
    """Paginated pull — NCAAF has ~700 games per season."""
    out = []
    for off in range(0, 10000, 1000):
        r = requests.get(f'{SB}/rest/v1/ncaaf_game_results', headers=H,
            params={'season': f'eq.{season}', 'select': '*',
                    'limit': 1000, 'offset': off}, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list): break
        out.extend(chunk)
        if len(chunk) < 1000: break
    return out


def load_team_stats(season: int) -> dict:
    r = requests.get(f'{SB}/rest/v1/ncaaf_team_stats', headers=H,
        params={'season': f'eq.{season}', 'select': '*'}, timeout=30)
    return {row['team']: row for row in (r.json() if isinstance(r.json(), list) else [])}


def backfill(season: int, dry_run: bool = False) -> int:
    print(f'=== NCAAF defense backfill · season {season} ===')
    results = load_results(season)
    if not results:
        print(f'  no results for season {season}'); return 0
    print(f'  {len(results)} game results')
    stats_map = load_team_stats(season)
    print(f'  {len(stats_map)} team offense stat rows')

    def_agg = defaultdict(lambda: {
        'games': 0, 'pts_allowed': 0,
        'pass_epa_allowed': 0.0, 'rush_epa_allowed': 0.0,
        'success_rate_allowed': 0.0, 'explosiveness_allowed': 0.0,
        'stat_matches': 0,   # count games where we had opponent stats
    })

    def _attribute(defender: str, offender_stats: dict, pts_scored: int) -> None:
        def_agg[defender]['games'] += 1
        def_agg[defender]['pts_allowed'] += pts_scored
        if not offender_stats: return
        def_agg[defender]['stat_matches'] += 1
        def_agg[defender]['pass_epa_allowed']    += float(offender_stats.get('off_pass_epa') or 0)
        def_agg[defender]['rush_epa_allowed']    += float(offender_stats.get('off_rush_epa') or 0)
        def_agg[defender]['success_rate_allowed'] += float(offender_stats.get('off_success_rate') or 0)
        def_agg[defender]['explosiveness_allowed'] += float(offender_stats.get('off_explosiveness') or 0)

    for g in results:
        home, away = g.get('home_team'), g.get('away_team')
        hs, as_ = g.get('home_score'), g.get('away_score')
        if not home or not away or hs is None or as_ is None: continue
        _attribute(home, stats_map.get(away, {}), as_)
        _attribute(away, stats_map.get(home, {}), hs)

    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for team, d in def_agg.items():
        n = d['games']
        if n < 1: continue
        sn = d['stat_matches'] or 1   # avoid div/0 if no opponent stats matched
        rows.append({
            'team': team, 'season': season, 'season_type': 'regular',
            'games': n,
            'def_ppg': round(d['pts_allowed'] / n, 2),
            'def_pass_epa_allowed':    round(d['pass_epa_allowed'] / sn, 4),
            'def_rush_epa_allowed':    round(d['rush_epa_allowed'] / sn, 4),
            'def_success_rate_allowed': round(d['success_rate_allowed'] / sn, 4),
            'def_explosiveness_allowed': round(d['explosiveness_allowed'] / sn, 4),
            'updated_at': now,
        })

    if rows:
        rows_sorted = sorted(rows, key=lambda r: r['def_ppg'])
        print(f'\n  BEST DEFENSES (fewest ppg, min 5 games):')
        top = [r for r in rows_sorted if r['games'] >= 5][:8]
        for r in top:
            print(f'    {r["team"]:22s} n={r["games"]:2d}  PPG={r["def_ppg"]:5.1f}  '
                  f'pEPA={r["def_pass_epa_allowed"]:+.3f}  rEPA={r["def_rush_epa_allowed"]:+.3f}')
        print(f'  WORST DEFENSES:')
        worst = [r for r in rows_sorted if r['games'] >= 5][-5:]
        for r in worst:
            print(f'    {r["team"]:22s} n={r["games"]:2d}  PPG={r["def_ppg"]:5.1f}  '
                  f'pEPA={r["def_pass_epa_allowed"]:+.3f}  rEPA={r["def_rush_epa_allowed"]:+.3f}')

    if dry_run:
        print(f'\n  [DRY] would upsert {len(rows)} rows')
        return len(rows)

    written = 0
    for i in range(0, len(rows), 50):
        chunk = rows[i:i+50]
        wr = requests.post(
            f'{SB}/rest/v1/ncaaf_team_defense_stats?on_conflict=team,season,season_type',
            headers=H_W, data=json.dumps(chunk, default=str), timeout=15)
        if wr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ chunk {i} write failed {wr.status_code}: {wr.text[:200]}')
    print(f'\n  ✓ upserted {written}/{len(rows)} def stat rows for {season}')
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', type=int)
    ap.add_argument('--all-seasons', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.all_seasons:
        for s in (2022, 2023, 2024, 2025, 2026):
            backfill(s, dry_run=args.dry_run)
    elif args.season:
        backfill(args.season, dry_run=args.dry_run)
    else:
        current = (datetime.now(timezone.utc)).year
        print(f'no --season / --all-seasons — defaulting to {current}')
        backfill(current, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
