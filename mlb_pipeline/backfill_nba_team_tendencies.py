"""Backfill NBA team ATS/O/U/ML tendencies (2026-08-17).

L10 window (82-game season, ~12% of games = recent form).
Reads nba_game_results.spread_result / total_result / home_win.

CLI:
  python backfill_nba_team_tendencies.py                # today ET
  python backfill_nba_team_tendencies.py --date 2026-10-22
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

WINDOW = 10
MIN_FAVDOG_SAMPLE = 5


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_history(days_lookback: int = 400) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days_lookback)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    results = []
    for off in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/nba_game_results'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            '&select=game_date,home_team,away_team,home_score,away_score,'
            'close_spread,close_total,spread_result,total_result,home_win'
            f'&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        results += chunk
        if len(chunk) < 1000: break
    return results


def compute_team_tendencies(games: list[dict]) -> dict:
    per_team: dict = defaultdict(list)
    for g in games:
        if g.get('home_score') is None or g.get('away_score') is None: continue
        entry = {
            'game_date': g['game_date'],
            'close_spread': g.get('close_spread'),
            'spread_result': g.get('spread_result'),
            'total_result': g.get('total_result'),
            'home_win': g.get('home_win'),
        }
        per_team[g['home_team']].append({**entry, 'as': 'HOME'})
        per_team[g['away_team']].append({**entry, 'as': 'AWAY'})

    tendencies: dict = {}
    for team, entries in per_team.items():
        entries.sort(key=lambda x: x['game_date'], reverse=True)
        last10 = entries[:WINDOW]

        ats_covers = ats_losses = 0
        ml_wins = ml_losses = 0
        overs = unders = 0
        for e in last10:
            sr = e.get('spread_result')
            team_covered = ((sr == 'home_covered' and e['as'] == 'HOME') or
                            (sr == 'away_covered' and e['as'] == 'AWAY'))
            team_lost_ats = ((sr == 'home_covered' and e['as'] == 'AWAY') or
                             (sr == 'away_covered' and e['as'] == 'HOME'))
            if team_covered: ats_covers += 1
            elif team_lost_ats: ats_losses += 1
            hw = e.get('home_win')
            if hw is True:
                if e['as'] == 'HOME': ml_wins += 1
                else: ml_losses += 1
            elif hw is False:
                if e['as'] == 'AWAY': ml_wins += 1
                else: ml_losses += 1
            tr = e.get('total_result')
            if tr == 'over': overs += 1
            elif tr == 'under': unders += 1

        fav_covers = fav_games = 0
        dog_covers = dog_games = 0
        for e in entries:
            sp = e.get('close_spread')
            if sp is None: continue
            try: sp = float(sp)
            except (TypeError, ValueError): continue
            team_sp = sp if e['as'] == 'HOME' else -sp
            sr = e.get('spread_result')
            team_covered = ((sr == 'home_covered' and e['as'] == 'HOME') or
                            (sr == 'away_covered' and e['as'] == 'AWAY'))
            if team_sp < 0:
                fav_games += 1
                if team_covered: fav_covers += 1
            elif team_sp > 0:
                dog_games += 1
                if team_covered: dog_covers += 1

        tendencies[team] = {
            'ats_last10': ats_covers, 'ats_last10_losses': ats_losses,
            'ou_last10_overs': overs, 'ou_last10_unders': unders,
            'ml_last10': ml_wins, 'ml_last10_losses': ml_losses,
            'covers_as_fav_pct': round(100 * fav_covers / fav_games, 1) if fav_games >= MIN_FAVDOG_SAMPLE else None,
            'covers_as_dog_pct': round(100 * dog_covers / dog_games, 1) if dog_games >= MIN_FAVDOG_SAMPLE else None,
        }
    return tendencies


def write_to_today(tendencies: dict, game_date: str, dry_run: bool = False) -> int:
    r = requests.get(f'{SB}/rest/v1/nba_game_context'
                     f'?game_date=eq.{game_date}&select=game_id,home_team,away_team',
                     headers=H_READ, timeout=15)
    games = r.json() if r.status_code == 200 else []
    if not games:
        print(f'  no games on {game_date}')
        return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    written = 0
    for g in games:
        h = g.get('home_team'); a = g.get('away_team')
        ht = tendencies.get(h, {}); at = tendencies.get(a, {})
        patch = {
            'home_ats_last10':        ht.get('ats_last10'),
            'home_ats_last10_losses': ht.get('ats_last10_losses'),
            'away_ats_last10':        at.get('ats_last10'),
            'away_ats_last10_losses': at.get('ats_last10_losses'),
            'home_ou_last10_overs':   ht.get('ou_last10_overs'),
            'home_ou_last10_unders':  ht.get('ou_last10_unders'),
            'away_ou_last10_overs':   at.get('ou_last10_overs'),
            'away_ou_last10_unders':  at.get('ou_last10_unders'),
            'home_covers_as_fav_pct': ht.get('covers_as_fav_pct'),
            'home_covers_as_dog_pct': ht.get('covers_as_dog_pct'),
            'away_covers_as_fav_pct': at.get('covers_as_fav_pct'),
            'away_covers_as_dog_pct': at.get('covers_as_dog_pct'),
            'home_ml_last10':         ht.get('ml_last10'),
            'home_ml_last10_losses':  ht.get('ml_last10_losses'),
            'away_ml_last10':         at.get('ml_last10'),
            'away_ml_last10_losses':  at.get('ml_last10_losses'),
            'team_tendencies_updated_at': now_iso,
        }
        print(f'  {a:<22} @ {h:<22}  '
              f'ats={patch["away_ats_last10"]}-{patch["away_ats_last10_losses"]}/'
              f'{patch["home_ats_last10"]}-{patch["home_ats_last10_losses"]} '
              f'ou={patch["away_ou_last10_overs"]}o-{patch["away_ou_last10_unders"]}u')
        if dry_run: written += 1; continue
        pr = requests.patch(f'{SB}/rest/v1/nba_game_context?game_id=eq.{g["game_id"]}',
                            headers=H_WRITE, json=patch, timeout=15)
        if pr.status_code in (200, 204): written += 1
        else: print(f'    ✗ patch failed: {pr.status_code} {pr.text[:120]}')
    return written


def run(game_date: str | None = None, dry_run: bool = False):
    gd = game_date or _et_today()
    print(f'=== backfill NBA team tendencies · {gd} ===')
    history = fetch_history(days_lookback=400)
    print(f'  fetched {len(history)} resolved games (last 400d)')
    tendencies = compute_team_tendencies(history)
    print(f'  computed tendencies for {len(tendencies)} teams')
    written = write_to_today(tendencies, gd, dry_run=dry_run)
    print(f'\n  {"[DRY] " if dry_run else ""}wrote tendencies for {written} games on {gd}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
