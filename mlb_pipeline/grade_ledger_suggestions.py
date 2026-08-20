"""Grade ledger_suggestions (2026-08-20).

Users can see the Ledger surface every day (chalk parlay + teased combos)
but the `result` field on `ledger_suggestions` was never being populated —
audit today showed 3/3 rows for 2026-08-19 with `result='UNGRADED'`.
User rightly asked: "Is the ledger record even tracking?"

Answer was: no. Now it does.

This is the sibling script to grade_ledger_snapshots.py — same grading
logic, different table. `ledger_suggestions` holds the auto-generated
daily card; `ledger_snapshots` holds user-visible snapshots at display
time. Both need grading; the snapshot grader existed, this one didn't.

Grading:
  - ML leg: home_score vs away_score, match against pick name (contains team)
  - Total leg: sum vs line (from `original_line` or `teased_line`)
  - Spread leg: home margin vs line (with sign)
  - Combo wins if ALL legs win; any leg loss → combo loss.
  - Push handling: drop the pushed leg, recompute — if 2-leg parlay
    becomes 1-leg after push, it becomes a straight bet.

CLI:
  python grade_ledger_suggestions.py                       # yesterday
  python grade_ledger_suggestions.py --date 2026-08-19
  python grade_ledger_suggestions.py --backfill 21         # last 21 days
  python grade_ledger_suggestions.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timedelta, timezone
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


def _fetch_result(sport: str, game_id: str) -> dict | None:
    table = SPORT_RESULTS.get(sport, SPORT_RESULTS['MLB'])
    r = requests.get(f'{SB}/rest/v1/{table}',
                     headers=H_READ,
                     params={'game_id': f'eq.{game_id}',
                             'select': 'game_id,home_team,away_team,home_score,away_score',
                             'limit': '1'},
                     timeout=10)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None


def _grade_leg(leg: dict, result: dict) -> str:
    """Return 'W', 'L', 'P', or 'NR' (not resolved)."""
    hs = result.get('home_score'); as_ = result.get('away_score')
    if hs is None or as_ is None:
        return 'NR'
    market = str(leg.get('market') or '').lower()
    pick = str(leg.get('pick') or '')
    pick_lower = pick.lower()
    home_team = (result.get('home_team') or '').lower()
    away_team = (result.get('away_team') or '').lower()
    # Use teased line if present, else original
    line = leg.get('teased_line') if leg.get('teased_line') is not None else leg.get('original_line')

    if market == 'ml':
        # Team name in pick determines side
        picked_home = home_team and home_team in pick_lower
        picked_away = away_team and away_team in pick_lower
        if picked_home:
            if hs > as_: return 'W'
            if hs < as_: return 'L'
            return 'P'  # ties (rare in MLB, exists in NFL)
        if picked_away:
            if as_ > hs: return 'W'
            if as_ < hs: return 'L'
            return 'P'
        return 'NR'
    if market in ('spread', 'runline', 'rl'):
        if line is None: return 'NR'
        # Determine picked side + spread applied
        picked_home = home_team and home_team in pick_lower
        picked_away = away_team and away_team in pick_lower
        margin = hs - as_  # home margin
        try: line_f = float(line)
        except (TypeError, ValueError): return 'NR'
        if picked_home:
            adj = margin + line_f  # e.g. home -1.5, margin 2 → adj 0.5 > 0 = W
            if adj > 0: return 'W'
            if adj < 0: return 'L'
            return 'P'
        if picked_away:
            adj = (-margin) + line_f
            if adj > 0: return 'W'
            if adj < 0: return 'L'
            return 'P'
        return 'NR'
    if market in ('total', 'over', 'under'):
        if line is None: return 'NR'
        total = hs + as_
        try: line_f = float(line)
        except (TypeError, ValueError): return 'NR'
        is_over = 'over' in pick_lower or market == 'over'
        is_under = 'under' in pick_lower or market == 'under'
        if is_over:
            if total > line_f: return 'W'
            if total < line_f: return 'L'
            return 'P'
        if is_under:
            if total < line_f: return 'W'
            if total > line_f: return 'L'
            return 'P'
        return 'NR'
    return 'NR'


def grade_row(row: dict, dry_run: bool = False) -> str | None:
    """Grade a single ledger_suggestions row. Returns 'W' | 'L' | 'P' | 'NR'.
    NR = not enough data yet (skip, don't overwrite)."""
    legs = row.get('legs') or []
    if not legs:
        return 'NR'
    verdicts = []
    for leg in legs:
        sport = str(leg.get('sport') or 'MLB').upper()
        gid = leg.get('game_id')
        if not gid:
            return 'NR'
        result = _fetch_result(sport, gid)
        if not result:
            return 'NR'
        v = _grade_leg(leg, result)
        if v == 'NR':
            return 'NR'  # can't grade whole combo without full data
        verdicts.append(v)
    if not verdicts:
        return 'NR'
    # Combo logic: all W (pushes drop) = W; any L = L; all P = P
    real = [v for v in verdicts if v != 'P']
    if not real:
        return 'P'  # all pushed
    if any(v == 'L' for v in real):
        return 'L'
    if all(v == 'W' for v in real):
        return 'W'
    return 'L'  # shouldn't reach


def run_date(gd: str, dry_run: bool = False) -> tuple[int, int, int]:
    """Returns (graded, still_ungraded, skipped_no_change)."""
    r = requests.get(f'{SB}/rest/v1/ledger_suggestions',
        headers=H_READ,
        params={'game_date': f'eq.{gd}', 'result': 'is.null',
                'select': 'id,kind,legs,legs_resolved'},
        timeout=15)
    rows = r.json() if r.status_code == 200 else []
    if not isinstance(rows, list) or not rows:
        print(f'  {gd}: no ungraded rows')
        return 0, 0, 0
    graded = 0; still = 0
    for row in rows:
        v = grade_row(row)
        if v == 'NR':
            still += 1
            print(f'  {gd}: id={row["id"]} {row["kind"]:22s} → NR (data pending)')
            continue
        legs_resolved = []
        for leg in (row.get('legs') or []):
            sport = str(leg.get('sport') or 'MLB').upper()
            gid = leg.get('game_id')
            res = _fetch_result(sport, gid) if gid else None
            legs_resolved.append(_grade_leg(leg, res) if res else 'NR')
        payload = {
            'result': v,
            'legs_resolved': legs_resolved,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        }
        if dry_run:
            print(f'  [DRY] {gd}: id={row["id"]} {row["kind"]:22s} → {v} (legs={legs_resolved})')
            graded += 1; continue
        pr = requests.patch(f'{SB}/rest/v1/ledger_suggestions?id=eq.{row["id"]}',
                            headers=H_WRITE, json=payload, timeout=10)
        if pr.status_code in (200, 201, 204):
            graded += 1
            print(f'  ✓ {gd}: id={row["id"]} {row["kind"]:22s} → {v} (legs={legs_resolved})')
        else:
            print(f'    ✗ patch failed {pr.status_code}: {pr.text[:120]}')
    return graded, still, 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: yesterday ET)')
    p.add_argument('--backfill', type=int, help='Backfill last N days')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    print(f'=== grade_ledger_suggestions ===\n')

    if args.backfill:
        end = datetime.strptime(args.date, '%Y-%m-%d').date() if args.date else \
              (datetime.now(timezone.utc) - timedelta(hours=4)).date() - timedelta(days=1)
        total_graded = total_still = 0
        for i in range(args.backfill):
            d = (end - timedelta(days=i)).isoformat()
            g, s, _ = run_date(d, dry_run=args.dry_run)
            total_graded += g; total_still += s
        print(f'\ntotal: graded={total_graded} still_ungraded={total_still}')
    else:
        d = args.date or ((datetime.now(timezone.utc) - timedelta(hours=4)).date() - timedelta(days=1)).isoformat()
        g, s, _ = run_date(d, dry_run=args.dry_run)
        print(f'\ngraded={g} still_ungraded={s}')


if __name__ == '__main__':
    main()
