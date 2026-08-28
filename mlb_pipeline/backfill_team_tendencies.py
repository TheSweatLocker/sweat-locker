"""Backfill team ATS / O/U / ML tendencies for MLB games (2026-08-17).

For each team playing today (or --date), computes rolling last-10-game
tendencies from resolved history + writes to mlb_game_context. Activates
the 10 team_form signals seeded yesterday (home/away_team_ats_hot,
home/away_team_over_trend, home/away_covers_as_fav/dog, etc.).

Per team, from most recent 20 resolved games:
  * ATS last 10: covers-losses vs close_spread
  * O/U last 10: overs-unders count vs close_total
  * ML last 10: straight-up W-L
  * Covers-as-fav %: covered ATS when the team was favored (season pool)
  * Covers-as-dog %: covered ATS when the team was underdog (season pool)

Writes back to each home_team / away_team's row on today's mlb_game_context.

CLI:
  python backfill_team_tendencies.py                # today
  python backfill_team_tendencies.py --date 2026-08-17
  python backfill_team_tendencies.py --dry-run
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


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def fetch_history(days: int = 90) -> list[dict]:
    """Pull all resolved games from the last N days.

    2026-08-28 bug fix: previously joined mlb_game_results → mlb_game_context
    for close_spread/close_total. Ctx rows for games >30 days old often
    get pruned or never existed, so `if not ctx: continue` silently dropped
    ~85% of the games (172 kept vs 1200 real). Result: dog cover% and
    every other tendency was computed on a truncated dataset that skewed
    heavily toward recent games with ctx.

    Fix: pull spread/total/ML directly from mlb_game_results (those
    columns ARE on results — verified 8/28). Ctx no longer needed for
    the tendency compute.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    results = []
    for off in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_results'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&select=game_id,game_date,home_team,away_team,home_score,away_score,'
            f'close_spread,close_total,home_ml_close,away_ml_close'
            f'&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        results += chunk
        if len(chunk) < 1000: break
    return results


def compute_team_tendencies(games: list[dict]) -> dict:
    """For each team, compute rolling last-10 tendencies + season fav/dog splits.
    Returns dict keyed by team name."""
    # Group games by team (either home or away)
    per_team: dict = defaultdict(list)
    for g in games:
        # 2026-08-28 bug fix: `not g.get('home_score')` treated 0 (a valid
        # shutout score) as falsy → any 0-run game was silently dropped.
        # For dogs, most non-cover losses include 0-run outings, so filtering
        # them inflated `covers_as_dog_pct` (e.g. SEA showed 75% actual 50%).
        # Explicit None check now includes real 0-scores.
        if g.get('home_score') is None or g.get('away_score') is None: continue
        # Order both sides consistently — sorted by date
        entry = {
            'game_date': g['game_date'],
            'home_score': int(g['home_score']),
            'away_score': int(g['away_score']),
            'close_spread': g.get('close_spread'),
            'close_total': g.get('close_total'),
            'home_ml_close': g.get('home_ml_close'),
            'away_ml_close': g.get('away_ml_close'),
        }
        per_team[g['home_team']].append({**entry, 'as': 'HOME'})
        per_team[g['away_team']].append({**entry, 'as': 'AWAY'})

    tendencies: dict = {}
    for team, entries in per_team.items():
        # Sort most-recent first
        entries.sort(key=lambda x: x['game_date'], reverse=True)
        last10 = entries[:10]
        season = entries  # season pool = full window we pulled

        ats_covers = 0; ats_losses = 0
        ml_wins = 0; ml_losses = 0
        overs = 0; unders = 0
        for e in last10:
            hs = e['home_score']; as_ = e['away_score']
            total = hs + as_
            team_score = hs if e['as'] == 'HOME' else as_
            opp_score = as_ if e['as'] == 'HOME' else hs
            # ATS
            spread = e.get('close_spread')
            if spread is not None:
                try: spread = float(spread)
                except (TypeError, ValueError): spread = None
            if spread is not None:
                # spread is HOME perspective; team_spread depends on which side
                team_spread = spread if e['as'] == 'HOME' else -spread
                margin = team_score - opp_score
                cover_margin = margin + team_spread
                if cover_margin > 0: ats_covers += 1
                elif cover_margin < 0: ats_losses += 1
            # ML
            if team_score > opp_score: ml_wins += 1
            elif team_score < opp_score: ml_losses += 1
            # O/U
            tot = e.get('close_total')
            if tot is not None:
                try: tot = float(tot)
                except (TypeError, ValueError): tot = None
            if tot is not None:
                if total > tot: overs += 1
                elif total < tot: unders += 1

        # Season fav/dog cover splits
        fav_covers = 0; fav_games = 0
        dog_covers = 0; dog_games = 0
        for e in season:
            spread = e.get('close_spread')
            if spread is None: continue
            try: spread = float(spread)
            except (TypeError, ValueError): continue
            team_spread = spread if e['as'] == 'HOME' else -spread
            hs = e['home_score']; as_ = e['away_score']
            team_score = hs if e['as'] == 'HOME' else as_
            opp_score = as_ if e['as'] == 'HOME' else hs
            margin = team_score - opp_score
            cover_margin = margin + team_spread
            if team_spread < 0:  # team was favored
                fav_games += 1
                if cover_margin > 0: fav_covers += 1
            elif team_spread > 0:  # team was underdog
                dog_games += 1
                if cover_margin > 0: dog_covers += 1

        tendencies[team] = {
            'ats_last10': ats_covers, 'ats_last10_losses': ats_losses,
            'ou_last10_overs': overs, 'ou_last10_unders': unders,
            'ml_last10': ml_wins, 'ml_last10_losses': ml_losses,
            'covers_as_fav_pct': round(100 * fav_covers / fav_games, 1) if fav_games >= 5 else None,
            'covers_as_dog_pct': round(100 * dog_covers / dog_games, 1) if dog_games >= 5 else None,
        }
    return tendencies


def write_to_today(tendencies: dict, game_date: str, dry_run: bool = False) -> int:
    """For each game_context row on `game_date`, stamp home + away tendencies."""
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context'
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
            'home_ats_last10':          home_tend.get('ats_last10'),
            'home_ats_last10_losses':   home_tend.get('ats_last10_losses'),
            'away_ats_last10':          away_tend.get('ats_last10'),
            'away_ats_last10_losses':   away_tend.get('ats_last10_losses'),
            'home_ou_last10_overs':     home_tend.get('ou_last10_overs'),
            'home_ou_last10_unders':    home_tend.get('ou_last10_unders'),
            'away_ou_last10_overs':     away_tend.get('ou_last10_overs'),
            'away_ou_last10_unders':    away_tend.get('ou_last10_unders'),
            'home_covers_as_fav_pct':   home_tend.get('covers_as_fav_pct'),
            'home_covers_as_dog_pct':   home_tend.get('covers_as_dog_pct'),
            'away_covers_as_fav_pct':   away_tend.get('covers_as_fav_pct'),
            'away_covers_as_dog_pct':   away_tend.get('covers_as_dog_pct'),
            'home_ml_last10':           home_tend.get('ml_last10'),
            'home_ml_last10_losses':    home_tend.get('ml_last10_losses'),
            'away_ml_last10':           away_tend.get('ml_last10'),
            'away_ml_last10_losses':    away_tend.get('ml_last10_losses'),
            'team_tendencies_updated_at': now_iso,
        }

        print(f'  {away_t:<25} @ {home_t:<25}  '
              f'ats={patch["away_ats_last10"]}-{patch["away_ats_last10_losses"]}/{patch["home_ats_last10"]}-{patch["home_ats_last10_losses"]} '
              f'ou={patch["away_ou_last10_overs"]}o-{patch["away_ou_last10_unders"]}u / {patch["home_ou_last10_overs"]}o-{patch["home_ou_last10_unders"]}u')

        if dry_run: written += 1; continue

        pr = requests.patch(
            f'{SB}/rest/v1/mlb_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json=patch, timeout=15)
        if pr.status_code in (200, 204): written += 1
        else: print(f'    ✗ patch failed: {pr.status_code} {pr.text[:120]}')
    return written


def run(game_date: str | None = None, dry_run: bool = False):
    gd = game_date or _et_today()
    print(f'=== backfill team tendencies · {gd} ===')

    history = fetch_history(days=90)
    print(f'  fetched {len(history)} resolved games from last 90d')

    tendencies = compute_team_tendencies(history)
    print(f'  computed tendencies for {len(tendencies)} teams')

    written = write_to_today(tendencies, gd, dry_run=dry_run)
    print(f'\n  {"[DRY] " if dry_run else ""}wrote tendencies for {written} games on {gd}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
