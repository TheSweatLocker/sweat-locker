"""NBA Elo rating model (2026-08-17).

Simple, sound, self-training. Iterates historical games in date order,
updates team Elo ratings after each game. Home-court advantage baked in
as +100 Elo (NBA-standard). K-factor = 20.

Predict function returns:
  - projected_spread (home perspective, negative = home fav)
  - projected_home_wp (0-1)
  - projected_total (using home + away recent averages)

Elo → spread conversion: 100 Elo diff ≈ 4 points in NBA (25 Elo/pt).

Data source: nba_game_results (populated by backfill_nba_history.py +
nba_resolve_results.py daily).

Storage: nba_team_elo table (team, season, elo, games_played, last_updated).

CLI:
  python nba_elo.py train --season 2024-25          # (re)train from history
  python nba_elo.py predict "LAL" "BOS"             # ad-hoc prediction
  python nba_elo.py rankings --season 2024-25       # top 30 by Elo
"""
from __future__ import annotations
import argparse, os, sys
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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# ═══════════════════════════════════════════════════════════════════════
# Elo core
# ═══════════════════════════════════════════════════════════════════════

DEFAULT_ELO = 1500.0
HOME_ADVANTAGE = 100.0       # Elo boost for home team (NBA standard)
K_FACTOR = 20                # Update magnitude per game
ELO_PER_POINT = 25           # 100 Elo diff ≈ 4-point spread


def expected_win_prob(rating_a: float, rating_b: float, home_advantage: float = 0) -> float:
    """Elo win probability: side A wins vs side B.
    home_advantage: +N added to A's rating if A is home."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a - home_advantage) / 400.0))


def update(rating_a: float, rating_b: float, a_won: bool,
           home_advantage: float = 0, k: float = K_FACTOR) -> tuple[float, float]:
    """Update both ratings after a game. Returns (new_a, new_b)."""
    ea = expected_win_prob(rating_a, rating_b, home_advantage)
    actual = 1.0 if a_won else 0.0
    delta = k * (actual - ea)
    return rating_a + delta, rating_b - delta


def elo_to_spread(home_elo: float, away_elo: float) -> float:
    """Convert Elo diff to home-perspective spread. Negative = home fav.
    NBA rule of thumb: 100 Elo ≈ 4 points, home court ≈ 100 Elo."""
    diff = home_elo - away_elo + HOME_ADVANTAGE
    return round(-diff / ELO_PER_POINT, 1)


# ═══════════════════════════════════════════════════════════════════════
# Training
# ═══════════════════════════════════════════════════════════════════════

def load_games(season: str | None = None) -> list[dict]:
    """Load resolved games from nba_game_results, ordered by date."""
    params = 'game_date=not.is.null&home_score=not.is.null&order=game_date.asc,game_id.asc'
    if season: params += f'&season=eq.{season}'
    all_rows = []
    for off in range(0, 20000, 1000):
        r = requests.get(f'{SB}/rest/v1/nba_game_results?{params}'
                         f'&select=game_date,home_team,away_team,home_score,away_score,total_points,season'
                         f'&limit=1000&offset={off}',
                         headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        all_rows += chunk
        if len(chunk) < 1000: break
    return all_rows


def train(season: str | None = None, reset_all: bool = True) -> dict:
    """Iterate all games in order, evolve ratings. Returns final ratings
    by team + per-team stats (games_played, avg_points_for, avg_points_against)."""
    games = load_games(season)
    print(f'  loaded {len(games)} games')

    ratings: dict[str, float] = defaultdict(lambda: DEFAULT_ELO)
    games_played: dict[str, int] = defaultdict(int)
    points_for: dict[str, int] = defaultdict(int)
    points_against: dict[str, int] = defaultdict(int)

    for g in games:
        h = g['home_team']; a = g['away_team']
        hs = g['home_score']; as_ = g['away_score']
        if hs is None or as_ is None: continue
        h_won = hs > as_
        new_h, new_a = update(ratings[h], ratings[a], h_won, HOME_ADVANTAGE)
        ratings[h] = new_h; ratings[a] = new_a
        games_played[h] += 1; games_played[a] += 1
        points_for[h] += hs; points_against[h] += as_
        points_for[a] += as_; points_against[a] += hs

    out = {}
    for t in ratings:
        gp = games_played[t] or 1
        out[t] = {
            'team': t,
            'elo': round(ratings[t], 1),
            'games_played': games_played[t],
            'avg_pts_for':  round(points_for[t] / gp, 1),
            'avg_pts_against': round(points_against[t] / gp, 1),
        }
    return out


def save_ratings(ratings: dict, season: str) -> int:
    """Upsert per-team Elo into nba_team_stats (season-scoped)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for team, d in ratings.items():
        payloads.append({
            'team_abbrev': team[:8],  # some ESPN full names > 8 chars but this is scope-note
            'season':      season,
            'off_rating':  d.get('avg_pts_for'),
            'def_rating':  d.get('avg_pts_against'),
            'net_rating':  round(d.get('avg_pts_for', 0) - d.get('avg_pts_against', 0), 1),
            'wins':        d.get('games_played', 0),  # placeholder
            'losses':      0,
            'updated_at':  now_iso,
        })
    written = 0
    for i in range(0, len(payloads), 50):
        chunk = payloads[i:i+50]
        pr = requests.post(f'{SB}/rest/v1/nba_team_stats?on_conflict=team_abbrev,season',
                           headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200,201,204): written += len(chunk)
        else: print(f'    ✗ upsert: {pr.status_code} {pr.text[:120]}')
    return written


# ═══════════════════════════════════════════════════════════════════════
# Predict (given ratings)
# ═══════════════════════════════════════════════════════════════════════

def predict(home_team: str, away_team: str, ratings: dict) -> dict:
    """Project spread + home win prob + total for a matchup."""
    h_rating = ratings.get(home_team, {}).get('elo', DEFAULT_ELO)
    a_rating = ratings.get(away_team, {}).get('elo', DEFAULT_ELO)
    projected_spread = elo_to_spread(h_rating, a_rating)
    projected_home_wp = expected_win_prob(h_rating, a_rating, HOME_ADVANTAGE)

    # Simple total projection: avg of home_pts_for + away_pts_for + home_pts_against + away_pts_against
    def _avg_scoring(team):
        rec = ratings.get(team, {})
        return (rec.get('avg_pts_for', 115) + rec.get('avg_pts_against', 115)) / 2.0

    projected_total = round((_avg_scoring(home_team) + _avg_scoring(away_team)) / 2 * 2, 1)

    return {
        'home_team': home_team,
        'away_team': away_team,
        'home_elo': round(h_rating, 1),
        'away_elo': round(a_rating, 1),
        'projected_spread': projected_spread,
        'projected_home_wp': round(projected_home_wp, 3),
        'projected_total': projected_total,
    }


def rankings(ratings: dict, limit: int = 30) -> list[dict]:
    """Return teams sorted by Elo desc."""
    sorted_r = sorted(ratings.values(), key=lambda r: -r['elo'])
    return sorted_r[:limit]


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser()
    p.add_argument('cmd', choices=['train', 'predict', 'rankings'])
    p.add_argument('args', nargs='*')
    p.add_argument('--season', help='Restrict/label season (2024-25 etc.)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    if args.cmd == 'train':
        print(f'=== nba_elo train · season {args.season or "all"} ===')
        ratings = train(season=args.season)
        print(f'  trained {len(ratings)} teams')
        top = rankings(ratings, 5)
        print('  Top 5 by Elo:')
        for r in top: print(f'    {r["team"]:<25} elo={r["elo"]:.1f} gp={r["games_played"]}')
        bottom = list(reversed(rankings(ratings, len(ratings))))[:5]
        print('  Bottom 5:')
        for r in bottom: print(f'    {r["team"]:<25} elo={r["elo"]:.1f} gp={r["games_played"]}')
        if args.dry_run:
            print('  [DRY] not persisted')
        elif args.season:
            written = save_ratings(ratings, args.season)
            print(f'  saved {written} team ratings to nba_team_stats')

    elif args.cmd == 'predict':
        if len(args.args) < 2:
            raise SystemExit('usage: predict "Home Team" "Away Team"')
        home, away = args.args[0], args.args[1]
        ratings = train(season=args.season)
        p_ = predict(home, away, ratings)
        print(f'\n{p_["away_team"]} @ {p_["home_team"]}')
        print(f'  Elo: {p_["home_elo"]} vs {p_["away_elo"]} (+HFA {HOME_ADVANTAGE})')
        print(f'  Projected spread: {p_["projected_spread"]:+g} (home perspective)')
        print(f'  Home win prob: {p_["projected_home_wp"]*100:.1f}%')
        print(f'  Projected total: {p_["projected_total"]}')

    elif args.cmd == 'rankings':
        ratings = train(season=args.season)
        top = rankings(ratings, 30)
        print(f'Top 30 by Elo (season={args.season or "all"}):')
        for i, r in enumerate(top, 1):
            print(f'  {i:>2}. {r["team"]:<25} elo={r["elo"]:.1f}  gp={r["games_played"]}  '
                  f'ppg={r["avg_pts_for"]:.1f}/{r["avg_pts_against"]:.1f}')


if __name__ == '__main__':
    main()
