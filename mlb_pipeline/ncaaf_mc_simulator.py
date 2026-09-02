"""NCAAF Monte Carlo simulator (2026-09-01).

5th lens for NCAAF's model stack — mirrors NFL/MLB MC pattern. Runs
10,000-game Monte Carlo per matchup using the ctx's already-computed
`projected_spread` + `projected_total` (which fold in SP+, EPA,
returning production, HFA, neutral-site handling) as expected values.
Writes `mc_probabilities` JSONB blob to `ncaaf_game_context`.

Design choice — REUSE ctx projections, don't recompute:
  ncaaf_game_context.py already produces `projected_spread` (home-
  perspective margin) and `projected_total`. Those go through all the
  sport-specific adjustments — SP+ gap × K_PTS_SP + HFA, EPA fallback,
  returning-production tilt, neutral-site flag. Re-implementing that
  math here would drift over time. Instead we take the ctx projections
  as our expected values and add MC variance around them. The MC blob
  becomes a "how tight are these projections" chip alongside the model
  point estimate.

MC constants (calibrated to CFB, distinct from NFL):
  LEAGUE_AVG_PPG      ≈ 29    (CFB scores higher than NFL — bigger
                                dispersion between elite + FCS-adjacent)
  GAME_STDDEV         ≈ 13.5  (CFB has more variance than NFL's 10.5;
                                garbage-time swings, wider talent gap)
  HFA_POINTS          ≈ 3.0   (already baked into projected_spread by
                                ctx — do NOT double-count)
  N_SIMS              = 10000
  MIN_PROJ_CONFIDENCE = require both projected_spread + projected_total
                        present. Skip when either is null (Week 1 games
                        without SP+ baseline, FCS opponents, etc.)

Outputs (same shape as NFL/MLB MC blobs so LensGrid renders uniformly):
  mc_p_home, mc_p_away, mc_expected_margin, mc_expected_total,
  mc_stddev_margin, mc_p_over_line, mc_confidence_high, generated_at

CLI:
    python ncaaf_mc_simulator.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, math, random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# NCAAF-tuned MC constants
LEAGUE_AVG_PPG = 29.0
GAME_STDDEV = 13.5
N_SIMS = 10000


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def _f(v) -> Optional[float]:
    try:
        if v is None: return None
        return float(v)
    except (TypeError, ValueError):
        return None


def simulate_game(projected_spread: float, projected_total: float,
                  n_sims: int = N_SIMS,
                  posted_total: Optional[float] = None) -> dict:
    """Run n_sims of the game.

    projected_spread is home-perspective margin (positive = home wins by X).
    projected_total is game total. Both from ncaaf_game_context — already
    incorporate SP+, EPA, HFA, returning production, neutral-site.

    Expected scores derived from the projection split:
        home = (total + margin) / 2
        away = (total - margin) / 2
    Then sample around each with GAME_STDDEV, floor at 0.
    """
    home_expected = (projected_total + projected_spread) / 2.0
    away_expected = (projected_total - projected_spread) / 2.0

    # CFB scores rarely below 3 (safety) but can happen; floor at 3 to
    # avoid negative samples from wide gauss tails dominating the mean.
    home_expected = max(home_expected, 3.0)
    away_expected = max(away_expected, 3.0)

    home_wins = 0
    over_hits = 0
    total_margins = 0.0
    total_totals = 0.0
    margin_sq_sum = 0.0

    for _ in range(n_sims):
        home_score = max(random.gauss(home_expected, GAME_STDDEV), 0)
        away_score = max(random.gauss(away_expected, GAME_STDDEV), 0)
        margin = home_score - away_score
        total = home_score + away_score
        total_margins += margin
        total_totals += total
        margin_sq_sum += margin * margin
        if home_score > away_score: home_wins += 1
        if posted_total is not None and total > posted_total: over_hits += 1

    mean_margin = total_margins / n_sims
    mean_total = total_totals / n_sims
    var_margin = (margin_sq_sum / n_sims) - (mean_margin ** 2)
    std_margin = math.sqrt(max(var_margin, 0))

    p_home = home_wins / n_sims
    result = {
        'mc_p_home': round(p_home, 3),
        'mc_p_away': round(1 - p_home, 3),
        'mc_expected_margin': round(mean_margin, 2),
        'mc_expected_total': round(mean_total, 2),
        'mc_stddev_margin': round(std_margin, 2),
        # HIGH-CONF: > 7-pt expected margin AND stddev under ~19 (two
        # NCAAF score dists with GAME_STDDEV=13.5 each produce margin
        # stddev ≈ 19.1). Slightly wider than NFL threshold because
        # CFB variance is genuinely higher — HIGH-CONF should fire on
        # lopsided sims (elite vs middling), not just close-to-line.
        'mc_confidence_high': (abs(mean_margin) > 7.0 and std_margin < 19.5),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }
    if posted_total is not None:
        result['mc_p_over_line'] = round(over_hits / n_sims, 3)
    return result


def run(game_date: str, dry_run: bool = False) -> int:
    """Load NCAAF ctx rows for the date; simulate each with projections."""
    print(f'=== NCAAF MC simulator · {game_date} ===')

    r = requests.get(f'{SB}/rest/v1/ncaaf_game_context', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'game_id,home_team,away_team,close_total,'
                          'projected_spread,projected_total,neutral_site'},
        timeout=15)
    if r.status_code != 200:
        print(f'  fetch failed: {r.status_code}'); return 0
    games = r.json() if isinstance(r.json(), list) else []
    if not games:
        print(f'  no NCAAF games for {game_date}'); return 0
    print(f'  {len(games)} NCAAF games in context')

    written = 0
    skipped_no_projections = 0

    for g in games:
        home = g.get('home_team'); away = g.get('away_team')
        if not (home and away): continue
        proj_spread = _f(g.get('projected_spread'))
        proj_total = _f(g.get('projected_total'))
        if proj_spread is None or proj_total is None:
            skipped_no_projections += 1
            continue

        result = simulate_game(
            projected_spread=proj_spread,
            projected_total=proj_total,
            posted_total=_f(g.get('close_total')),
        )

        matchup = f'{away} @ {home}'
        neutral_marker = ' (N)' if g.get('neutral_site') else ''
        print(f'  {matchup:36}{neutral_marker}  home {result["mc_p_home"]*100:5.1f}%  '
              f'margin {result["mc_expected_margin"]:+5.1f}  '
              f'tot {result["mc_expected_total"]:5.1f}  '
              f'{"HIGH-CONF" if result["mc_confidence_high"] else ""}')

        if dry_run: continue

        pr = requests.patch(
            f'{SB}/rest/v1/ncaaf_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json={'mc_probabilities': result}, timeout=10)
        if pr.status_code in (200, 201, 204):
            written += 1
        else:
            print(f'    write failed: {pr.status_code} {pr.text[:150]}')

    print(f'\n{"[DRY] would write" if dry_run else "wrote"} {written} MC blobs · '
          f'skipped {skipped_no_projections} without projections '
          f'(usually FCS opps or missing SP+ baseline).')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--days', type=int, default=1,
                   help='Days to simulate starting from --date (default 1)')
    args = p.parse_args()
    start = args.date or _et_today()
    total = 0
    if args.days == 1:
        total = run(game_date=start, dry_run=args.dry_run)
    else:
        base = datetime.fromisoformat(start).date()
        for i in range(args.days):
            d = (base + timedelta(days=i)).isoformat()
            total += run(game_date=d, dry_run=args.dry_run)
    print(f'\nTotal MC blobs written: {total}')


if __name__ == '__main__':
    main()
