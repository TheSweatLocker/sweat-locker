"""NCAAB Monte Carlo simulator (2026-08-14).

Session 2 of NCAAB 5-lens build. Same output shape as
nfl_mc_simulator.py + mlb_mc_simulator so the anti-consensus rule +
MC HIGH-CONF chip logic can read the same JSONB field across sports.

METHODOLOGY
  Pomeroy-style efficiency approximation (fast, well-calibrated for
  NCAAB — team stats aggregate enough sample that pure normal-
  distribution sim is within a point of possession-by-possession
  models on expected margin).

  For each game:
    home_eff = home_adj_oe × (away_adj_de / league_avg_eff)   # pts/100 poss
    away_eff = away_adj_oe × (home_adj_de / league_avg_eff)
    pace     = (home_tempo + away_tempo) / 2                  # poss/game
    home_expected = home_eff × pace / 100 + HFA
    away_expected = away_eff × pace / 100
    home_score ~ Normal(home_expected, SIGMA_TEAM_SCORE)
    away_score ~ Normal(away_expected, SIGMA_TEAM_SCORE)
    margin     = home_score - away_score
    total      = home_score + away_score

  10k sims → aggregate win probs, expected margin/total, stddev,
  p_over_line (if close_total is present).

INPUT SOURCE (chosen 2026-08-14):
  Reads home_adj_oe/de/tempo + away_adj_oe/de/tempo DIRECTLY from
  ncaab_game_context. Game context is populated at pick time from
  ncaab_team_stats; using its snapshot means MC + game_context share
  the same "as-of" state. Falls back to ncaab_team_stats lookup only
  when a row is missing rating fields.

CALIBRATION (defaults tuned to KenPom's own methodology + NCAAB priors)
  LEAGUE_AVG_EFF  = 106.0    # D-I median AdjOE ~106
  HFA_POINTS      = 3.5      # NCAAB HFA is well-studied at ~3-4 pts
  SIGMA_TEAM_SCORE= 9.5      # per-team score stddev (empirical)
  N_SIMS          = 10000

  Neutral-site games override HFA=0 (post-March, tournament) via
  is_neutral_site column.

  HIGH-CONF threshold (calibrated 2026-08-14 same pattern as NFL —
  will re-tune after Session 4 backtest):
    abs(mean_margin) > 5.0 AND std_margin < 13.5

OUTPUTS
  ncaab_game_context.mc_probabilities JSONB — see migration for shape

CLI
  python ncaab_mc_simulator.py                    # today's slate
  python ncaab_mc_simulator.py --game-date 2026-11-04
  python ncaab_mc_simulator.py --dry-run          # print, don't write
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone
from typing import Optional

import numpy as np
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

LEAGUE_AVG_EFF = 106.0
HFA_POINTS = 3.5
SIGMA_TEAM_SCORE = 9.5
N_SIMS = 10_000

HIGH_CONF_MARGIN_ABS = 5.0
HIGH_CONF_STDDEV_MAX = 13.5


def _load_games(game_date: date) -> list:
    """Return today's ncaab_game_context rows with rating fields already joined."""
    fields = ('game_id,game_date,home_team,away_team,close_total,close_spread,'
              'is_neutral_site,home_adj_oe,home_adj_de,home_tempo,'
              'away_adj_oe,away_adj_de,away_tempo')
    r = requests.get(
        f'{SB}/rest/v1/ncaab_game_context?select={fields}'
        f'&game_date=eq.{game_date.isoformat()}',
        headers=H_READ, timeout=30)
    if r.status_code != 200:
        print(f'  ✗ ncaab_game_context fetch {r.status_code}: {r.text[:200]}')
        return []
    return r.json() or []


def _fallback_ratings() -> dict:
    """{team_name: {adj_oe, adj_de, tempo}} from most recent ncaab_team_stats.
    Only queried when a game_context row is missing rating fields."""
    r = requests.get(
        f'{SB}/rest/v1/ncaab_team_stats?select=team_name,adj_oe,adj_de,tempo,updated_at'
        f'&order=updated_at.desc&limit=800',
        headers=H_READ, timeout=30)
    if r.status_code != 200: return {}
    out = {}
    for row in r.json() or []:
        name = row.get('team_name')
        if not name or name in out: continue
        oe, de, tempo = row.get('adj_oe'), row.get('adj_de'), row.get('tempo')
        if oe is None or de is None or tempo is None: continue
        out[name] = {'adj_oe': float(oe), 'adj_de': float(de),
                     'tempo': float(tempo)}
    return out


def simulate_game(home_adj_oe: float, home_adj_de: float, home_tempo: float,
                  away_adj_oe: float, away_adj_de: float, away_tempo: float,
                  close_total: Optional[float] = None,
                  neutral: bool = False) -> dict:
    """Run N_SIMS 10k sims. Return probability blob."""
    home_eff = home_adj_oe * (away_adj_de / LEAGUE_AVG_EFF)
    away_eff = away_adj_oe * (home_adj_de / LEAGUE_AVG_EFF)
    pace = (home_tempo + away_tempo) / 2.0

    home_expected = home_eff * pace / 100.0 + (0.0 if neutral else HFA_POINTS)
    away_expected = away_eff * pace / 100.0

    rng = np.random.default_rng()
    home_scores = rng.normal(home_expected, SIGMA_TEAM_SCORE, N_SIMS)
    away_scores = rng.normal(away_expected, SIGMA_TEAM_SCORE, N_SIMS)
    # NCAAB score floor: games essentially never go below 40 per team
    home_scores = np.maximum(home_scores, 40.0)
    away_scores = np.maximum(away_scores, 40.0)

    margins = home_scores - away_scores
    totals = home_scores + away_scores

    mean_margin = float(np.mean(margins))
    std_margin = float(np.std(margins))
    mean_total = float(np.mean(totals))
    p_home = float(np.mean(margins > 0))

    blob = {
        'mc_p_home': round(p_home, 4),
        'mc_p_away': round(1.0 - p_home, 4),
        'mc_expected_margin': round(mean_margin, 2),
        'mc_expected_total': round(mean_total, 2),
        'mc_stddev_margin': round(std_margin, 2),
        'mc_home_expected_pts': round(float(np.mean(home_scores)), 2),
        'mc_away_expected_pts': round(float(np.mean(away_scores)), 2),
        'mc_pace_estimate': round(pace, 2),
        'mc_confidence_high': bool(
            abs(mean_margin) > HIGH_CONF_MARGIN_ABS and
            std_margin < HIGH_CONF_STDDEV_MAX),
        'mc_neutral_site': bool(neutral),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }
    if close_total is not None:
        blob['mc_p_over_line'] = round(float(np.mean(totals > float(close_total))), 4)
        blob['mc_line_used'] = float(close_total)
    return blob


def _row_ratings(row: dict, fallback: dict) -> Optional[tuple]:
    """Extract (home_oe, home_de, home_tempo, away_oe, away_de, away_tempo)
    from row, falling back to team_stats lookup when a field is null."""
    def _get(prefix: str) -> Optional[dict]:
        oe = row.get(f'{prefix}_adj_oe')
        de = row.get(f'{prefix}_adj_de')
        tempo = row.get(f'{prefix}_tempo')
        if oe is not None and de is not None and tempo is not None:
            return {'adj_oe': float(oe), 'adj_de': float(de), 'tempo': float(tempo)}
        team = row.get(f'{prefix}_team')
        return fallback.get(team)
    h = _get('home'); a = _get('away')
    if not (h and a): return None
    return (h['adj_oe'], h['adj_de'], h['tempo'],
            a['adj_oe'], a['adj_de'], a['tempo'])


def _write_mc(game_id, blob: dict) -> bool:
    r = requests.patch(
        f'{SB}/rest/v1/ncaab_game_context?game_id=eq.{game_id}',
        headers=H_WRITE, json={'mc_probabilities': blob}, timeout=15)
    if r.status_code not in (200, 204):
        print(f'  ✗ write game {game_id}: {r.status_code} {r.text[:150]}')
        return False
    return True


def run(game_date: Optional[date] = None, dry_run: bool = False) -> int:
    if game_date is None:
        game_date = datetime.now(timezone.utc).date()
    print(f'=== ncaab_mc_simulator · {game_date.isoformat()} ===')

    try:
        from data_quality import DQ
        dq = DQ(source='ncaab_mc_simulator.py', sport='NCAAB')
    except Exception:
        dq = None

    games = _load_games(game_date)
    print(f'  games on slate: {len(games)}')
    if not games:
        print('  ° no games scheduled — MC no-op (expected off-season)')
        return 0

    # Fallback ratings only fetched lazily if a row is missing fields
    fallback = {}

    n_ok = 0; n_missing = 0
    for g in games:
        ratings = _row_ratings(g, fallback)
        if ratings is None and not fallback:
            fallback = _fallback_ratings()
            print(f'  ° fallback ratings loaded: {len(fallback)} teams')
            ratings = _row_ratings(g, fallback)

        if ratings is None:
            n_missing += 1
            print(f'  ° skip {g.get("away_team")} @ {g.get("home_team")} — no ratings')
            continue

        blob = simulate_game(*ratings, close_total=g.get('close_total'),
                              neutral=bool(g.get('is_neutral_site')))
        if dry_run:
            print(f'  [DRY] {g.get("away_team")} @ {g.get("home_team")}: '
                  f'p_home={blob["mc_p_home"]:.3f} margin={blob["mc_expected_margin"]:+.1f} '
                  f'total={blob["mc_expected_total"]:.1f} conf={blob["mc_confidence_high"]}')
            n_ok += 1
        else:
            if _write_mc(g['game_id'], blob):
                n_ok += 1

    if dq:
        dq.assert_range(n_ok + n_missing, 0, 200, 'ncaab_mc_slate_size',
                        context={'n_games': n_ok + n_missing})

    print(f'  ✓ {n_ok} MC blobs written; {n_missing} skipped (rating gap)')
    return n_ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--game-date', help='YYYY-MM-DD; defaults to today')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    gd = date.fromisoformat(args.game_date) if args.game_date else None
    run(gd, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
