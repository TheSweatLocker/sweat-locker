"""Monitor ensemble health — self-regulation nightly job (2026-08-16).

Runs the ensemble against the last 30d of resolved games, grades every
pick, computes rolling hit rate + ROI, and updates ensemble_health.
The scorer reads the latest ensemble_health row at score time and
adjusts thresholds accordingly.

Self-regulation ladder:
  Day 0 (green):  status_flag = 'healthy'
  Day 1 (cold):   status_flag = 'watch'
                  cold_streak_days = 1
  Days 2-4:       status_flag = 'watch'
                  cold_streak_days = 2..4
  Days 5-9:       status_flag = 'soft_tighten'
                  lean_threshold_override = 0.8 (was 0.5)
  Days 10+:       status_flag = 'hard_suppress'
                  scorer skips → falls back to legacy compute_primary_play
  Recovery:       green day resets streak; status returns to 'healthy'.

CLI:
  python monitor_ensemble_health.py --sport MLB
  python monitor_ensemble_health.py --sport NFL --window 30
  python monitor_ensemble_health.py --sport MLB --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
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

from ensemble_scorer import score_game
from backfill_signal_tiers import grade_side

# Sport → (context table, results table)
SPORT_TABLES = {
    'MLB':   ('mlb_game_context',   'mlb_game_results'),
    'NFL':   ('nfl_game_context',   'nfl_game_results'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results'),
    'NCAAB': ('ncaab_game_context', 'ncaab_game_results'),
    'NHL':   ('nhl_game_context',   'nhl_game_results'),
}


def american_to_win_payout(odds) -> float:
    if odds is None: return 0.91
    try: odds = int(odds)
    except (TypeError, ValueError): return 0.91
    if odds >= 100: return odds / 100.0
    if odds <= -100: return 100.0 / abs(odds)
    return 0.91


def fetch_games_with_results(sport: str, days: int) -> list[dict]:
    ctx_table, res_table = SPORT_TABLES[sport]
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    # Games
    ctx_rows = []
    for off in range(0, 10000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/{ctx_table}'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}&select=*&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        ctx_rows += chunk
        if len(chunk) < 1000: break

    # Results
    results = {}
    for off in range(0, 10000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/{res_table}'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}&select=game_id,home_score,away_score&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        for row in chunk:
            results[row['game_id']] = row
        if len(chunk) < 1000: break

    out = []
    for c in ctx_rows:
        r = results.get(c.get('game_id'))
        if not r: continue
        try:
            c['_home_score'] = int(r['home_score'])
            c['_away_score'] = int(r['away_score'])
        except (TypeError, ValueError, KeyError): continue
        out.append(c)
    return out


def score_and_grade(sport: str, games: list[dict]) -> dict:
    """Score every game with ensemble, grade top pick + each market."""
    stats = {
        'top': {'w':0, 'l':0, 'p':0, 'u':0.0},
        'ml':  {'w':0, 'l':0, 'p':0, 'u':0.0},
        'rl':  {'w':0, 'l':0, 'p':0, 'u':0.0},
        'total':{'w':0, 'l':0, 'p':0, 'u':0.0},
    }
    for ctx in games:
        try: decision = score_game(sport, ctx)
        except Exception: continue
        if decision is None: continue

        # Grade top pick (most important — this is what the app surfaces)
        top = decision.top()
        if top.pick is not None:
            res = grade_side(top.pick, ctx)
            payout = 0.91  # default
            if top.market == 'ml':
                odds = ctx.get('home_ml_close') if top.side == 'HOME' else ctx.get('away_ml_close')
                payout = american_to_win_payout(odds)
            if res == 'W':
                stats['top']['w'] += 1; stats['top']['u'] += payout
            elif res == 'L':
                stats['top']['l'] += 1; stats['top']['u'] -= 1.0
            elif res == 'P':
                stats['top']['p'] += 1

        # Grade per market
        for market_name in ('ml', 'rl', 'total'):
            md = getattr(decision, market_name)
            if md.pick is None: continue
            res = grade_side(md.pick, ctx)
            payout = 0.91
            if market_name == 'ml':
                odds = ctx.get('home_ml_close') if md.side == 'HOME' else ctx.get('away_ml_close')
                payout = american_to_win_payout(odds)
            if res == 'W':
                stats[market_name]['w'] += 1; stats[market_name]['u'] += payout
            elif res == 'L':
                stats[market_name]['l'] += 1; stats[market_name]['u'] -= 1.0
            elif res == 'P':
                stats[market_name]['p'] += 1

    return stats


def _hr(s: dict) -> float | None:
    n = s['w'] + s['l']
    return round(100 * s['w'] / n, 1) if n else None


def _roi(s: dict) -> float | None:
    n = s['w'] + s['l']
    return round(100 * s['u'] / n, 1) if n else None


def determine_status(prior: dict | None, roi: float | None, breakeven: float = 0.0) -> tuple[str, int, int, float | None]:
    """Return (status_flag, new_cold_streak, new_green_streak, threshold_override)."""
    prior_cold = int((prior or {}).get('cold_streak_days') or 0)
    prior_green = int((prior or {}).get('green_streak_days') or 0)

    if roi is None:
        return ('watch', prior_cold, prior_green, None)

    if roi >= breakeven:
        # Green day — reset cold streak
        return ('healthy', 0, prior_green + 1, None)

    # Cold day — increment
    new_cold = prior_cold + 1
    if new_cold >= 10:
        return ('hard_suppress', new_cold, 0, None)
    if new_cold >= 5:
        return ('soft_tighten', new_cold, 0, 0.8)  # LEAN threshold raised
    return ('watch', new_cold, 0, None)


def fetch_prior(sport: str) -> dict | None:
    """Read yesterday's health row for streak carry-forward."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = requests.get(f'{SB}/rest/v1/ensemble_health'
                     f'?sport=eq.{sport}&computed_date=eq.{yesterday}'
                     '&select=cold_streak_days,green_streak_days,status_flag',
                     headers=H_READ, timeout=10)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None


def upsert_health(payload: dict, dry_run: bool = False) -> bool:
    if dry_run: return True
    pr = requests.post(f'{SB}/rest/v1/ensemble_health?on_conflict=sport,computed_date',
                       headers=H_WRITE, json=[payload], timeout=15)
    if pr.status_code in (200, 201, 204): return True
    print(f'  ✗ upsert failed: {pr.status_code} {pr.text[:150]}')
    return False


def run(sport: str, window: int = 30, dry_run: bool = False):
    print(f'=== ensemble_health · {sport} · rolling {window}d ===\n')
    games = fetch_games_with_results(sport, window)
    print(f'  {len(games)} games with results')
    if not games:
        print('  no games — abort')
        return

    stats = score_and_grade(sport, games)
    top = stats['top']
    hr = _hr(top); roi = _roi(top)

    print(f'\n  TOP-PICK   {top["w"]}-{top["l"]}-{top["p"]}  HR={hr}%  ROI={roi:+.1f}%  ({top["u"]:+.2f}u)')
    for mkt in ('ml','rl','total'):
        s = stats[mkt]
        if s['w']+s['l']+s['p'] == 0: continue
        print(f'  {mkt.upper():<7} {s["w"]}-{s["l"]}-{s["p"]}  HR={_hr(s)}%  ROI={_roi(s):+.1f}%')

    prior = fetch_prior(sport)
    status, cold, green, override = determine_status(prior, roi)
    print(f'\n  status: {status}')
    print(f'  cold_streak: {cold}   green_streak: {green}')
    if override is not None:
        print(f'  lean_threshold_override: {override}')

    payload = {
        'sport': sport,
        'computed_date': date.today().isoformat(),
        'window_days': window,
        'n_picks': top['w'] + top['l'] + top['p'],
        'n_wins': top['w'], 'n_losses': top['l'], 'n_pushes': top['p'],
        'hit_rate': hr, 'roi_pct': roi,
        'ml_hit_rate': _hr(stats['ml']), 'ml_n': stats['ml']['w'] + stats['ml']['l'] + stats['ml']['p'],
        'rl_hit_rate': _hr(stats['rl']), 'rl_n': stats['rl']['w'] + stats['rl']['l'] + stats['rl']['p'],
        'total_hit_rate': _hr(stats['total']), 'total_n': stats['total']['w'] + stats['total']['l'] + stats['total']['p'],
        'cold_streak_days': cold, 'green_streak_days': green,
        'status_flag': status,
        'lean_threshold_override': override,
        'notes': None,
    }
    if upsert_health(payload, dry_run=dry_run):
        print(f'  ✓ wrote ensemble_health row{" (dry-run)" if dry_run else ""}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB')
    p.add_argument('--window', type=int, default=30)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(sport=args.sport, window=args.window, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
