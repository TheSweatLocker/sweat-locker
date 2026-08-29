"""Backfill NCAAF team ATS/O/U/ML tendencies (2026-08-17).

Sport-specific port of backfill_team_tendencies.py adapted for NCAAF:
  * Window is L5 (not L10) — NCAAF regular season is 12 games
  * Reads ncaaf_game_results.spread_result / total_result directly
    (already resolved by pull_externals_ncaaf.py), avoiding the
    per-game close_spread math the MLB version does
  * Uses prior season(s) rolled-forward for the first weeks of the
    new season when in-season sample < 3 games; falls back to None
    if no history at all

Writes to today's ncaaf_game_context rows for home + away teams.

CLI:
  python backfill_ncaaf_team_tendencies.py                # today
  python backfill_ncaaf_team_tendencies.py --date 2026-08-23
  python backfill_ncaaf_team_tendencies.py --dry-run
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

WINDOW = 5              # L5 rolling window
MIN_FAVDOG_SAMPLE = 3   # smaller than MLB (5) because NCAAF has fewer games


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_history(days_lookback: int = 400) -> list[dict]:
    """Pull all resolved NCAAF games from last N days. 400d covers current
    + prior season (~14 mo)."""
    cutoff = (date.today() - timedelta(days=days_lookback)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    results = []
    for off in range(0, 30000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/ncaaf_game_results'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&select=game_id,game_date,home_team,away_team,home_score,away_score,'
            'close_spread,close_total,spread_result,total_result,home_win'
            f'&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        results += chunk
        if len(chunk) < 1000: break
    return results


def compute_team_tendencies(games: list[dict]) -> dict:
    """For each team, compute rolling L5 tendencies + season fav/dog splits.

    Uses spread_result ('home_covered'/'away_covered'/'push') and
    total_result ('over'/'under'/'push') directly from resolver rather
    than recomputing margins — resolver has better handling of ties,
    forfeits, cancellations."""
    per_team: dict = defaultdict(list)
    for g in games:
        if g.get('home_score') is None or g.get('away_score') is None: continue
        entry = {
            'game_date': g['game_date'],
            'close_spread': g.get('close_spread'),
            'close_total': g.get('close_total'),
            'spread_result': g.get('spread_result'),
            'total_result': g.get('total_result'),
            'home_win': g.get('home_win'),
        }
        per_team[g['home_team']].append({**entry, 'as': 'HOME'})
        per_team[g['away_team']].append({**entry, 'as': 'AWAY'})

    tendencies: dict = {}
    for team, entries in per_team.items():
        entries.sort(key=lambda x: x['game_date'], reverse=True)
        last5 = entries[:WINDOW]

        ats_covers = 0; ats_losses = 0
        ml_wins = 0; ml_losses = 0
        overs = 0; unders = 0
        for e in last5:
            sr = e.get('spread_result')
            team_covered = ((sr == 'home_covered' and e['as'] == 'HOME') or
                            (sr == 'away_covered' and e['as'] == 'AWAY'))
            team_lost_ats = ((sr == 'home_covered' and e['as'] == 'AWAY') or
                             (sr == 'away_covered' and e['as'] == 'HOME'))
            if team_covered: ats_covers += 1
            elif team_lost_ats: ats_losses += 1
            # ML
            hw = e.get('home_win')
            if hw is True:
                if e['as'] == 'HOME': ml_wins += 1
                else: ml_losses += 1
            elif hw is False:
                if e['as'] == 'AWAY': ml_wins += 1
                else: ml_losses += 1
            # O/U
            tr = e.get('total_result')
            if tr == 'over': overs += 1
            elif tr == 'under': unders += 1

        # Season fav/dog cover splits — full window
        fav_covers = 0; fav_games = 0
        dog_covers = 0; dog_games = 0
        for e in entries:
            spread = e.get('close_spread')
            if spread is None: continue
            try: spread = float(spread)
            except (TypeError, ValueError): continue
            team_spread = spread if e['as'] == 'HOME' else -spread
            sr = e.get('spread_result')
            team_covered = ((sr == 'home_covered' and e['as'] == 'HOME') or
                            (sr == 'away_covered' and e['as'] == 'AWAY'))
            if team_spread < 0:
                fav_games += 1
                if team_covered: fav_covers += 1
            elif team_spread > 0:
                dog_games += 1
                if team_covered: dog_covers += 1

        tendencies[team] = {
            'ats_last5': ats_covers, 'ats_last5_losses': ats_losses,
            'ou_last5_overs': overs, 'ou_last5_unders': unders,
            'ml_last5': ml_wins, 'ml_last5_losses': ml_losses,
            'covers_as_fav_pct': round(100 * fav_covers / fav_games, 1)
                                 if fav_games >= MIN_FAVDOG_SAMPLE else None,
            'covers_as_dog_pct': round(100 * dog_covers / dog_games, 1)
                                 if dog_games >= MIN_FAVDOG_SAMPLE else None,
        }
    return tendencies


def write_to_today(tendencies: dict, game_date: str, dry_run: bool = False) -> int:
    r = requests.get(
        f'{SB}/rest/v1/ncaaf_game_context'
        f'?game_date=eq.{game_date}&select=game_id,home_team,away_team',
        headers=H_READ, timeout=15)
    games = r.json() if r.status_code == 200 else []
    if not games:
        print(f'  no games on {game_date}')
        return 0

    now_iso = datetime.now(timezone.utc).isoformat()
    written = 0
    for g in games:
        home_t = g.get('home_team')
        away_t = g.get('away_team')
        home_tend = tendencies.get(home_t, {})
        away_tend = tendencies.get(away_t, {})

        patch = {
            'home_ats_last5':           home_tend.get('ats_last5'),
            'home_ats_last5_losses':    home_tend.get('ats_last5_losses'),
            'away_ats_last5':           away_tend.get('ats_last5'),
            'away_ats_last5_losses':    away_tend.get('ats_last5_losses'),
            'home_ou_last5_overs':      home_tend.get('ou_last5_overs'),
            'home_ou_last5_unders':     home_tend.get('ou_last5_unders'),
            'away_ou_last5_overs':      away_tend.get('ou_last5_overs'),
            'away_ou_last5_unders':     away_tend.get('ou_last5_unders'),
            'home_covers_as_fav_pct':   home_tend.get('covers_as_fav_pct'),
            'home_covers_as_dog_pct':   home_tend.get('covers_as_dog_pct'),
            'away_covers_as_fav_pct':   away_tend.get('covers_as_fav_pct'),
            'away_covers_as_dog_pct':   away_tend.get('covers_as_dog_pct'),
            'home_ml_last5':            home_tend.get('ml_last5'),
            'home_ml_last5_losses':     home_tend.get('ml_last5_losses'),
            'away_ml_last5':            away_tend.get('ml_last5'),
            'away_ml_last5_losses':     away_tend.get('ml_last5_losses'),
            'team_tendencies_updated_at': now_iso,
        }

        print(f'  {away_t:<28} @ {home_t:<28}  '
              f'ats={patch["away_ats_last5"]}-{patch["away_ats_last5_losses"]}/'
              f'{patch["home_ats_last5"]}-{patch["home_ats_last5_losses"]} '
              f'ou={patch["away_ou_last5_overs"]}o-{patch["away_ou_last5_unders"]}u/'
              f'{patch["home_ou_last5_overs"]}o-{patch["home_ou_last5_unders"]}u')

        if dry_run: written += 1; continue
        pr = requests.patch(
            f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json=patch, timeout=15)
        if pr.status_code in (200, 204): written += 1
        else: print(f'    ✗ patch failed: {pr.status_code} {pr.text[:120]}')
    return written


def run(game_date: str | None = None, days: int = 1, dry_run: bool = False):
    # 2026-08-28: added `days` window so the workflow can pre-seed a full
    # weekend's slate in one call (Fri+Sat+Sun games). Prior single-date
    # default meant off-day cron runs found "no games" and never wrote,
    # leaving pre-week card generation with empty tendency fields.
    gd = game_date or _et_today()
    print(f'=== backfill NCAAF team tendencies · {gd} +{days-1}d ===')

    history = fetch_history(days_lookback=400)
    print(f'  fetched {len(history)} resolved NCAAF games (last 400d)')

    tendencies = compute_team_tendencies(history)
    print(f'  computed tendencies for {len(tendencies)} teams')

    from datetime import date as _date, timedelta as _td
    start = _date.fromisoformat(gd)
    total_written = 0
    for i in range(days):
        d = (start + _td(days=i)).isoformat()
        w = write_to_today(tendencies, d, dry_run=dry_run)
        total_written += w
    print(f'\n  {"[DRY] " if dry_run else ""}wrote tendencies for {total_written} games across {days} day(s) starting {gd}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--days', type=int, default=1, help='Days from --date to cover (default: 1)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
