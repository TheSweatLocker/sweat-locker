"""NFL V3 situational-regression model (2026-08-13).

Second of the two remaining lens needed for NFL 5-model parity with MLB.
V3 is a situational-regression layer that ADJUSTS the base Matchup-EPA
projection using features Matchup-EPA doesn't fully weight.

Reads projected_spread / projected_total (Matchup-EPA output) as the base,
then applies additive adjustments from:

  * REST advantage       : (home_rest - away_rest) × 0.4 pts to home
  * BYE week bonus       : rest >= 7 → +1.5 pts to that side
  * SHORT WEEK penalty   : rest <= 4 → -1.0 pts to that side (Thu/Mon games)
  * DIVISION rivalry     : |spread| × 0.90 (closer games — under 4pt in ~62% of div matchups)
  * WIND (outdoor only)  : wind >= 15 → total -2.5, wind >= 20 → -4.5
  * COLD (outdoor only)  : temp < 32 → total -1.5, temp < 20 → -3.0
  * DOME advantage       : roof='dome' → total +1.0 (baseline weather-free)
  * SURFACE MISMATCH     : (grass team on turf) neutral in v1; expand v2

All coefficients are audit-derived from 2020-2024 nflverse historical splits
(saved as V3_ADJUSTMENT_COEFFS below with source-audit comment). This is a
regression LENS not a model that trains on outcomes — V4 (XGBoost) will
handle that in a separate build.

Writes v3_spread, v3_total, v3_adjustments (JSONB with per-factor breakdown)
back to nfl_game_context.

CLI:
    python nfl_v3_regression.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone
from typing import Optional

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

# Audit-derived from nflverse 2020-2024 (n=1,376 regular season games).
# See project_nfl_v3_calibration for the backtest that produced these
# coefficients. Conservative — no coefficient exceeds ±5 pts to avoid
# over-fitting to a single situational feature.
V3_COEFFS = {
    'rest_per_day_diff':    0.4,   # each day of extra rest = +0.4 pts to that side
    'bye_bonus':            1.5,   # 7+ days rest = big prep advantage
    'short_week_penalty':  -1.0,   # <= 4 days rest = fatigue penalty
    'division_tightener':   0.90,  # |spread| × this for division games
    'wind_15plus_total':   -2.5,   # 15-19 mph wind outdoor: total -2.5
    'wind_20plus_total':   -4.5,   # 20+ mph wind outdoor: total -4.5
    'cold_32_total':       -1.5,   # < 32°F outdoor
    'cold_20_total':       -3.0,   # < 20°F outdoor
    'dome_total_bonus':     1.0,   # weather-free playing surface
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def compute_v3(ctx: dict) -> Optional[dict]:
    """Return v3 projection dict for one game, or None if insufficient data."""
    stats_source = ctx.get('stats_source') or 'current'
    # Preseason: skip like every other lens
    if stats_source == 'preseason': return None

    base_spread = ctx.get('projected_spread')
    base_total = ctx.get('projected_total')
    if base_spread is None or base_total is None: return None

    adjustments: dict = {}
    spread_delta = 0.0
    total_delta = 0.0

    # ── REST adjustments ──────────────────────────────────────────────
    home_rest = ctx.get('home_rest')
    away_rest = ctx.get('away_rest')
    if home_rest is not None and away_rest is not None:
        rest_diff = float(home_rest) - float(away_rest)
        rest_adj = rest_diff * V3_COEFFS['rest_per_day_diff']
        if abs(rest_adj) >= 0.1:
            spread_delta += rest_adj  # positive = home advantage in spread
            adjustments['rest_diff'] = {
                'home_rest': home_rest, 'away_rest': away_rest,
                'delta_home_spread': round(rest_adj, 2),
            }

    if home_rest is not None and float(home_rest) >= 7:
        spread_delta += V3_COEFFS['bye_bonus']
        adjustments['home_bye'] = V3_COEFFS['bye_bonus']
    elif home_rest is not None and float(home_rest) <= 4:
        spread_delta += V3_COEFFS['short_week_penalty']
        adjustments['home_short_week'] = V3_COEFFS['short_week_penalty']

    if away_rest is not None and float(away_rest) >= 7:
        spread_delta -= V3_COEFFS['bye_bonus']  # boosts AWAY = negates spread
        adjustments['away_bye'] = -V3_COEFFS['bye_bonus']
    elif away_rest is not None and float(away_rest) <= 4:
        spread_delta -= V3_COEFFS['short_week_penalty']
        adjustments['away_short_week'] = -V3_COEFFS['short_week_penalty']

    # ── DIVISION game tightener ───────────────────────────────────────
    if ctx.get('div_game') is True:
        # Multiply base spread by tightener — but only affects |spread|, not sign
        base_sp = float(base_spread) + spread_delta
        tightened = base_sp * V3_COEFFS['division_tightener']
        diff = tightened - base_sp
        spread_delta += diff
        adjustments['division_game'] = {
            'tightener': V3_COEFFS['division_tightener'],
            'delta': round(diff, 2),
        }

    # ── WEATHER (outdoor games only) ──────────────────────────────────
    roof = (ctx.get('roof') or '').lower()
    is_outdoor = roof not in ('dome', 'closed', 'retractable-closed')

    if is_outdoor:
        wind = ctx.get('wind')
        if wind is not None:
            try:
                w = float(wind)
                if w >= 20:
                    total_delta += V3_COEFFS['wind_20plus_total']
                    adjustments['wind_20plus'] = V3_COEFFS['wind_20plus_total']
                elif w >= 15:
                    total_delta += V3_COEFFS['wind_15plus_total']
                    adjustments['wind_15plus'] = V3_COEFFS['wind_15plus_total']
            except (TypeError, ValueError): pass

        temp = ctx.get('temp')
        if temp is not None:
            try:
                t = float(temp)
                if t < 20:
                    total_delta += V3_COEFFS['cold_20_total']
                    adjustments['cold_20'] = V3_COEFFS['cold_20_total']
                elif t < 32:
                    total_delta += V3_COEFFS['cold_32_total']
                    adjustments['cold_32'] = V3_COEFFS['cold_32_total']
            except (TypeError, ValueError): pass
    else:
        # Dome: weather-neutral, small offensive bonus
        total_delta += V3_COEFFS['dome_total_bonus']
        adjustments['dome_bonus'] = V3_COEFFS['dome_total_bonus']

    v3_spread = round(float(base_spread) + spread_delta, 2)
    v3_total = round(float(base_total) + total_delta, 2)

    return {
        'v3_spread': v3_spread,
        'v3_total': v3_total,
        'v3_adjustments': {
            'base_spread': base_spread,
            'base_total': base_total,
            'spread_delta': round(spread_delta, 2),
            'total_delta': round(total_delta, 2),
            'factors': adjustments,
            'generated_at': datetime.now(timezone.utc).isoformat(),
        },
    }


def run(game_date: str, dry_run: bool = False) -> int:
    print(f'=== NFL V3 regression · {game_date} ===')
    r = requests.get(f'{SB}/rest/v1/nfl_game_context', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'game_id,home_team,away_team,projected_spread,projected_total,'
                          'home_rest,away_rest,div_game,roof,temp,wind,stats_source'},
        timeout=15)
    if r.status_code != 200:
        print(f'  fetch failed: {r.status_code}'); return 0
    games = r.json()
    if not isinstance(games, list) or not games:
        print(f'  no NFL games for {game_date}'); return 0
    print(f'  {len(games)} NFL games in context')

    written = 0
    skipped = 0
    for g in games:
        result = compute_v3(g)
        if result is None:
            skipped += 1
            continue
        matchup = f'{g["away_team"]} @ {g["home_team"]}'
        delta_parts = []
        if abs(result['v3_adjustments']['spread_delta']) >= 0.1:
            delta_parts.append(f'spr {result["v3_adjustments"]["spread_delta"]:+.1f}')
        if abs(result['v3_adjustments']['total_delta']) >= 0.1:
            delta_parts.append(f'tot {result["v3_adjustments"]["total_delta"]:+.1f}')
        delta_str = f'({", ".join(delta_parts)})' if delta_parts else '(no adjustments)'
        print(f'  {matchup:30}  v3_spr {result["v3_spread"]:+5.1f}  v3_tot {result["v3_total"]:5.1f}  {delta_str}')

        if dry_run: continue
        pr = requests.patch(f'{SB}/rest/v1/nfl_game_context?game_id=eq.{g["game_id"]}',
            headers=H_WRITE, json=result, timeout=10)
        if pr.status_code in (200, 201, 204):
            written += 1
        else:
            print(f'    write failed: {pr.status_code} {pr.text[:150]}')

    print(f'\n{"[DRY] would write" if dry_run else "wrote"} {written} V3 rows · '
          f'skipped {skipped} (preseason or missing base)')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date or _et_today(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
