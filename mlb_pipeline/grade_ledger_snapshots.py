"""Grade ledger snapshots (2026-08-18).

Runs after all sports have resolved (nightly). For each ungraded
ledger_snapshot, look up per-leg results:
  - ML leg: home_score vs away_score (from sport's game_results table)
  - RL/spread leg: home_score - away_score vs spread (teased line if teased)
  - Total leg: home_score + away_score vs total (teased line if teased)

Combo wins if ALL legs win. Any leg push → drop that leg (recompute odds).
Any leg loss → whole combo loses. Compute unit_pnl at 1u stake.

Idempotent: only regrades snapshots where result IS NULL.
"""
from __future__ import annotations
import argparse, os, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

SPORT_RESULTS = {
    'MLB':   'mlb_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    'NHL':   'nhl_game_results',
    'NBA':   'nba_game_results',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _american_to_decimal(o: float) -> float:
    return 1 + (o / 100 if o >= 0 else 100 / abs(o))


def _combined_odds(decs: list[float]) -> int:
    prod = 1.0
    for d in decs: prod *= d
    payout = prod - 1
    return int(round(100 * payout)) if payout >= 1 else int(round(-100 / payout))


def _fetch_result(sport: str, game_id: str) -> dict | None:
    table = SPORT_RESULTS.get(sport)
    if not table: return None
    r = requests.get(f'{SB}/rest/v1/{table}',
                     headers=H_READ,
                     params={'game_id': f'eq.{game_id}',
                             'select': 'game_id,home_score,away_score',
                             'limit': '1'},
                     timeout=10)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None


def _grade_leg(leg: dict, result: dict) -> str:
    """Return 'W', 'L', 'P', or 'NR' (not resolved)."""
    hs = result.get('home_score'); as_ = result.get('away_score')
    if hs is None or as_ is None: return 'NR'
    market = str(leg.get('market') or '').lower()
    # Use teased line if present, else original_line
    line = leg.get('teased_line') if leg.get('teased_line') is not None else leg.get('original_line')
    side = (leg.get('pick') or '').upper()
    if market == 'ml':
        home_won = hs > as_
        pick_side = 'HOME' if 'home' in side.lower() or leg.get('side') == 'HOME' else 'AWAY'
        # Extract side from label — 'X ML' where X is a team name
        # Use `side` field from snapshot if present
        pick_side = (leg.get('side') or pick_side).upper()
        actual = 'HOME' if hs > as_ else 'AWAY' if as_ > hs else 'PUSH'
        if actual == 'PUSH': return 'P'
        return 'W' if pick_side == actual else 'L'
    if market in ('rl', 'spread', 'runline'):
        if line is None: return 'NR'
        pick_side = (leg.get('side') or '').upper() or ('HOME' if 'home' in side.lower() else 'AWAY')
        margin_home = hs - as_
        # Line is HOME's line (negative if home favored)
        # If pick_side=HOME, home covers if margin_home + line > 0
        # If pick_side=AWAY, away covers if margin_home + line < 0 (i.e. -line > margin_home)
        cover = margin_home + float(line)
        if abs(cover) < 0.01: return 'P'
        if pick_side == 'HOME': return 'W' if cover > 0 else 'L'
        else:                    return 'W' if cover < 0 else 'L'
    if market == 'total':
        if line is None: return 'NR'
        pick_side = (leg.get('side') or '').upper() or ('OVER' if 'over' in side.lower() else 'UNDER')
        actual = hs + as_
        if abs(actual - float(line)) < 0.01: return 'P'
        return ('W' if actual > float(line) else 'L') if pick_side == 'OVER' \
               else ('W' if actual < float(line) else 'L')
    return 'NR'


def grade_date(gd: str, dry_run: bool = False) -> tuple[int, int]:
    r = requests.get(f'{SB}/rest/v1/ledger_snapshots',
                     headers=H_READ,
                     params={'game_date': f'eq.{gd}', 'result': 'is.null',
                             'select': '*'},
                     timeout=20)
    snaps = r.json() if r.status_code == 200 else []
    if not snaps: return 0, 0

    graded = skipped = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for snap in snaps:
        legs = snap.get('legs') or []
        results_per_leg = []
        decs_after_pushes = []
        combo_status = 'W'  # assume win, downgrade on any L/NR
        legs_hit = legs_pushed = 0
        for leg in legs:
            sport = leg.get('sport')
            gid = leg.get('game_id')
            if not sport or not gid:
                combo_status = 'NR'; break
            res = _fetch_result(sport, gid)
            if not res:
                combo_status = 'NR'; break
            outcome = _grade_leg(leg, res)
            results_per_leg.append(outcome)
            if outcome == 'W':
                legs_hit += 1
                odds = leg.get('teased_odds') if leg.get('teased_odds') is not None else leg.get('original_odds')
                if odds is not None:
                    try: decs_after_pushes.append(_american_to_decimal(float(odds)))
                    except (TypeError, ValueError): pass
            elif outcome == 'P':
                legs_pushed += 1
            elif outcome == 'L':
                combo_status = 'L'
            elif outcome == 'NR':
                combo_status = 'NR'

        if combo_status == 'NR':
            skipped += 1
            continue
        # Recompute odds after pushes (dropped legs)
        if combo_status == 'W' and decs_after_pushes:
            new_combined = _combined_odds(decs_after_pushes)
            pnl = new_combined / 100 if new_combined >= 0 else 100 / abs(new_combined)
        elif combo_status == 'L':
            pnl = -1.0
        else:
            pnl = 0.0

        payload = {
            'result': combo_status,
            'legs_hit': legs_hit,
            'legs_pushed': legs_pushed,
            'graded_at': now_iso,
            'unit_pnl': round(pnl, 3),
        }
        if dry_run:
            graded += 1
            continue
        pr = requests.patch(
            f'{SB}/rest/v1/ledger_snapshots?id=eq.{snap["id"]}',
            headers=H_WRITE, json=payload, timeout=10,
        )
        if pr.status_code in (200, 204):
            graded += 1
    return graded, skipped


def run(game_date: str = None, days: int = 1, dry_run: bool = False):
    if game_date:
        dates = [game_date]
    else:
        dates = [(date.today() - timedelta(days=d+1)).isoformat() for d in range(days)]
    total_g = total_s = 0
    for gd in dates:
        g, s = grade_date(gd, dry_run=dry_run)
        total_g += g; total_s += s
        print(f'  {gd}: graded={g} skipped={s}')
    print(f'\n  {"[DRY] " if dry_run else ""}total: graded={total_g} skipped={total_s}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    p.add_argument('--days', type=int, default=7, help='backfill last N days')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date, days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
