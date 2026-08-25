"""NHL Elo rating model (2026-08-17).

Same pattern as nba_elo.py. Iterates historical NHL games in date order,
updates team Elo. NHL calibration:
  * Home advantage = 60 Elo (less than NBA due to less home-court impact)
  * K-factor = 6 (small — NHL variance is high)
  * Elo → puckline conversion: 100 Elo diff ≈ 0.15 puckline movement

We DON'T convert Elo to a spread — NHL puckline is always -1.5/+1.5.
Elo produces win probability which converts to ML pricing.

Total projection: avg of team goals_for and goals_against per game.

Reads from nhl_game_results (1,335 games from 2024-25 already
backfilled). Home_win populated even though puckline/total absent.

CLI:
  python nhl_elo.py train                         # train + persist
  python nhl_elo.py predict "Boston" "Toronto"    # ad-hoc
  python nhl_elo.py rankings                      # top by Elo
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

DEFAULT_ELO = 1500.0
HOME_ADVANTAGE = 60.0     # NHL — less than NBA (10 pts win prob boost)
K_FACTOR = 6              # Small — NHL variance is high (many 1-goal games)


def expected_win_prob(rating_a: float, rating_b: float, home_advantage: float = 0) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a - home_advantage) / 400.0))


def update(rating_a: float, rating_b: float, a_won: bool,
           home_advantage: float = 0, k: float = K_FACTOR) -> tuple[float, float]:
    ea = expected_win_prob(rating_a, rating_b, home_advantage)
    delta = k * ((1.0 if a_won else 0.0) - ea)
    return rating_a + delta, rating_b - delta


def load_games() -> list[dict]:
    """All resolved NHL games with home_win. Ordered by date."""
    params = ('home_win=not.is.null'
              '&order=game_date.asc,game_id.asc'
              '&select=game_date,home_team,away_team,home_score,away_score,total_goals,home_win,went_to_ot')
    all_rows = []
    for off in range(0, 30000, 1000):
        r = requests.get(f'{SB}/rest/v1/nhl_game_results?{params}&limit=1000&offset={off}',
                         headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        all_rows += chunk
        if len(chunk) < 1000: break
    return all_rows


def train() -> dict:
    games = load_games()
    print(f'  loaded {len(games)} NHL games')

    ratings: dict[str, float] = defaultdict(lambda: DEFAULT_ELO)
    games_played: dict[str, int] = defaultdict(int)
    goals_for: dict[str, int] = defaultdict(int)
    goals_against: dict[str, int] = defaultdict(int)

    for g in games:
        h = g['home_team']; a = g['away_team']
        if not h or not a: continue
        hs = g.get('home_score'); as_ = g.get('away_score')
        h_won = bool(g.get('home_win'))
        # NHL OT/SO adjust: reduced K for tie-broken games (0.5 weight)
        k = K_FACTOR * (0.5 if g.get('went_to_ot') else 1.0)
        new_h, new_a = update(ratings[h], ratings[a], h_won, HOME_ADVANTAGE, k=k)
        ratings[h] = new_h; ratings[a] = new_a
        games_played[h] += 1; games_played[a] += 1
        if hs is not None and as_ is not None:
            goals_for[h] += hs; goals_against[h] += as_
            goals_for[a] += as_; goals_against[a] += hs

    out = {}
    for t in ratings:
        gp = max(games_played[t], 1)
        out[t] = {
            'team': t,
            'elo': round(ratings[t], 1),
            'games_played': games_played[t],
            'avg_gf': round(goals_for[t] / gp, 2),
            'avg_ga': round(goals_against[t] / gp, 2),
        }
    return out


def predict(home_team: str, away_team: str, ratings: dict) -> dict:
    h = ratings.get(home_team, {}).get('elo', DEFAULT_ELO)
    a = ratings.get(away_team, {}).get('elo', DEFAULT_ELO)
    home_wp = expected_win_prob(h, a, HOME_ADVANTAGE)
    # Convert win prob → American ML.
    #   Favorite (wp >= 0.5): ml = -100 * wp / (1-wp)  → negative
    #   Dog     (wp <  0.5): ml = +100 * (1-wp) / wp   → positive
    if home_wp >= 0.5:
        home_ml = int(round(-100 * home_wp / max(1 - home_wp, 0.001)))
    else:
        home_ml = int(round(100 * (1 - home_wp) / max(home_wp, 0.001)))
    # Total projection: avg goal totals
    def _avg_scoring(team):
        rec = ratings.get(team, {})
        return rec.get('avg_gf', 3.0) + rec.get('avg_ga', 3.0)
    projected_total = round((_avg_scoring(home_team) + _avg_scoring(away_team)) / 2, 1)
    return {
        'home_team': home_team, 'away_team': away_team,
        'home_elo': round(h, 1), 'away_elo': round(a, 1),
        'projected_home_wp': round(home_wp, 3),
        'projected_home_ml': home_ml,
        'projected_total': projected_total,
    }


def rankings(ratings: dict, limit: int = 32) -> list[dict]:
    return sorted(ratings.values(), key=lambda r: -r['elo'])[:limit]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('cmd', choices=['train', 'predict', 'rankings'])
    p.add_argument('args', nargs='*')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    if args.cmd == 'train':
        print('=== nhl_elo train ===')
        ratings = train()
        print(f'  trained {len(ratings)} teams')
        top = rankings(ratings, 5)
        print('  Top 5 Elo:')
        for r in top: print(f'    {r["team"]:<20} elo={r["elo"]:.1f}  gp={r["games_played"]}  gf/ga={r["avg_gf"]}/{r["avg_ga"]}')

    elif args.cmd == 'predict':
        if len(args.args) < 2:
            raise SystemExit('usage: predict "Home" "Away"')
        home, away = args.args[0], args.args[1]
        ratings = train()
        pr = predict(home, away, ratings)
        print(f'\n{away} @ {home}')
        print(f'  Elo: {pr["home_elo"]} vs {pr["away_elo"]} (+HFA {HOME_ADVANTAGE})')
        print(f'  Home win prob: {pr["projected_home_wp"]*100:.1f}%  (ML {pr["projected_home_ml"]:+d})')
        print(f'  Projected total: {pr["projected_total"]} goals')

    elif args.cmd == 'rankings':
        ratings = train()
        top = rankings(ratings, 32)
        print('Top 32 by Elo:')
        for i, r in enumerate(top, 1):
            print(f'  {i:>2}. {r["team"]:<20} elo={r["elo"]:.1f}  gp={r["games_played"]}  gf/ga={r["avg_gf"]}/{r["avg_ga"]}')


if __name__ == '__main__':
    main()
