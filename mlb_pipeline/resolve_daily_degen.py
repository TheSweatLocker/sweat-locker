"""Resolve Daily Degen parlays.

For each un-resolved daily_degen row (result IS NULL and game_date <= yesterday):
  1. Walk the legs JSONB array
  2. For each leg, look up outcome:
     - PROP legs → mlb_pipeline_props.result via game_id + player + sub_type + direction + line
     - ML/runline legs → mlb_game_results (spread cover / straight ML)
  3. Compute parlay result:
     - Any Loss → Loss
     - Any Pending → Pending (unless already Loss)
     - Otherwise Win (Pushes remove that leg but don't kill parlay)
  4. PATCH daily_degen with legs_resolved + result + resolved_at

Usage:
    python resolve_daily_degen.py               # yesterday
    python resolve_daily_degen.py --date 2026-07-25
    python resolve_daily_degen.py --backfill    # all pending
    python resolve_daily_degen.py --dry-run
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
HW = {**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _yesterday_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4, days=1)).strftime('%Y-%m-%d')


# ── Leg outcome resolvers ────────────────────────────────────────────────

def _extract_player_from_pick(pick: str) -> str | None:
    """'Miles Mikolas — Under 17.5 Outs  ·  proj 15.9' → 'Miles Mikolas'"""
    if not pick: return None
    m = re.split(r'\s+[—\-]\s+', pick, maxsplit=1)
    return m[0].strip() if m else None


def _resolve_prop_leg(leg: dict) -> dict:
    """Look up mlb_pipeline_props for this leg's outcome."""
    game_id = leg.get('game_id')
    sub_type = leg.get('sub_type')
    player = _extract_player_from_pick(leg.get('pick', ''))
    if not (game_id and sub_type and player):
        return {'outcome': 'Pending', 'note': 'insufficient leg metadata'}

    # PostgREST ilike for player name (handles unicode dashes / whitespace)
    r = requests.get(
        f'{SB}/rest/v1/mlb_pipeline_props'
        f'?game_id=eq.{game_id}&prop_type=eq.{sub_type}'
        f'&player_name=ilike.{requests.utils.quote(player)}'
        f'&select=result,final_value,prop_line,direction',
        headers=H, timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        return {'outcome': 'Pending', 'note': f'no matching prop row for {player} {sub_type}'}
    row = rows[0]
    result = row.get('result')
    if result in ('Win', 'Loss', 'Push'):
        return {
            'outcome': result,
            'actual_value': row.get('final_value'),
            'line': row.get('prop_line'),
        }
    return {'outcome': 'Pending', 'actual_value': row.get('final_value')}


def _resolve_ml_leg(leg: dict) -> dict:
    """Look up mlb_game_results for ML / runline outcome.
    leg.pick example: 'Detroit Tigers -1.5' (runline) or 'Cubs ML'
    sub_type: 'runline' | 'ml'
    """
    game_id = leg.get('game_id')
    if not game_id:
        return {'outcome': 'Pending', 'note': 'missing game_id'}

    r = requests.get(
        f'{SB}/rest/v1/mlb_game_results'
        f'?game_id=eq.{game_id}'
        f'&select=home_team,away_team,home_score,away_score,home_win,spread_result,close_spread',
        headers=H, timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []
    if not rows:
        return {'outcome': 'Pending', 'note': 'no game_results row'}
    g = rows[0]
    if g.get('home_score') is None or g.get('away_score') is None:
        return {'outcome': 'Pending', 'note': 'game not final'}

    pick_txt = (leg.get('pick') or '')
    sub_type = leg.get('sub_type')
    home_win = bool(g.get('home_win'))
    home_team = (g.get('home_team') or '').lower()
    away_team = (g.get('away_team') or '').lower()

    # Figure out which side the leg is picking
    pick_home = home_team and home_team.split()[-1].lower() in pick_txt.lower()
    pick_away = away_team and away_team.split()[-1].lower() in pick_txt.lower()
    if not (pick_home or pick_away):
        # Try full team match
        pick_home = home_team in pick_txt.lower()
        pick_away = away_team in pick_txt.lower()
    if not (pick_home or pick_away):
        return {'outcome': 'Pending', 'note': f'could not parse team from "{pick_txt}"'}
    if pick_home and pick_away:
        # Take whichever has longer word overlap
        pick_home = len(home_team) >= len(away_team)
        pick_away = not pick_home

    picked_side = 'home' if pick_home else 'away'

    if sub_type == 'runline':
        # Runline: use spread_result if present. Else derive from margin +/- 1.5.
        sr = g.get('spread_result')
        if sr in ('HOME_COV', 'AWAY_COV', 'PUSH'):
            if sr == 'PUSH':
                return {'outcome': 'Push'}
            covered_side = 'home' if sr == 'HOME_COV' else 'away'
            return {'outcome': 'Win' if covered_side == picked_side else 'Loss',
                    'note': f'spread_result={sr}'}
        # Derive
        margin = g.get('home_score') - g.get('away_score')
        # Assume standard -1.5 runline: home -1.5 needs margin > 1.5; away +1.5 needs margin > -1.5 (i.e. away loses by <2)
        home_covers = margin > 1.5
        away_covers = margin < 1.5 and not (margin == 1.5)
        if margin == 1.5:
            return {'outcome': 'Push'}
        won = home_covers if picked_side == 'home' else away_covers
        return {'outcome': 'Win' if won else 'Loss',
                'note': f'margin {margin:+d} vs picked {picked_side} -1.5'}
    else:
        # Straight ML
        won = (picked_side == 'home' and home_win) or (picked_side == 'away' and not home_win)
        return {'outcome': 'Win' if won else 'Loss',
                'note': f'{"home" if home_win else "away"} won'}


def _resolve_nrfi_leg(leg: dict) -> dict:
    """NRFI legs — look up nrfi_result on mlb_game_results."""
    game_id = leg.get('game_id')
    if not game_id: return {'outcome': 'Pending', 'note': 'no game_id'}
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_results?game_id=eq.{game_id}&select=nrfi_result',
        headers=H, timeout=15,
    ).json()
    if not r: return {'outcome': 'Pending', 'note': 'no game row'}
    nr = r[0].get('nrfi_result')
    if nr not in ('NRFI', 'YRFI'):
        return {'outcome': 'Pending'}
    # Leg type: NRFI-lean means we're betting NO run in first inning
    pick_txt = (leg.get('pick') or '').upper()
    picking_nrfi = 'NRFI' in pick_txt or 'NO RUN' in pick_txt
    won = (nr == 'NRFI') == picking_nrfi
    return {'outcome': 'Win' if won else 'Loss', 'note': f'first inning {nr}'}


def _resolve_total_leg(leg: dict) -> dict:
    """Over/Under game total.
    leg.pick = 'Over 8.5' or 'Under 9.0'; sub_type = 'over' | 'under'.
    Grade via mlb_game_results home_score + away_score.
    """
    game_id = leg.get('game_id')
    if not game_id:
        return {'outcome': 'Pending', 'note': 'no game_id'}

    r = requests.get(
        f'{SB}/rest/v1/mlb_game_results'
        f'?game_id=eq.{game_id}'
        f'&select=home_score,away_score,total_result,close_total',
        headers=H, timeout=15,
    ).json()
    if not r:
        return {'outcome': 'Pending', 'note': 'no game_results row'}
    g = r[0]
    hs, as_ = g.get('home_score'), g.get('away_score')
    if hs is None or as_ is None:
        return {'outcome': 'Pending', 'note': 'game not final'}

    pick_txt = (leg.get('pick') or '').strip()
    m = re.search(r'(\d+(?:\.\d+)?)', pick_txt)
    line = float(m.group(1)) if m else None
    if line is None:
        # Fall back to close_total from context
        line = g.get('close_total')
    if line is None:
        return {'outcome': 'Pending', 'note': 'no line to grade against'}

    picking_over = leg.get('sub_type') == 'over' or pick_txt.lower().startswith('over')
    actual = hs + as_

    if abs(actual - line) < 1e-6:
        return {'outcome': 'Push', 'actual_value': actual, 'line': line}
    over_won = actual > line
    won = over_won if picking_over else not over_won
    return {
        'outcome': 'Win' if won else 'Loss',
        'actual_value': actual,
        'line': line,
        'note': f'{"Over" if picking_over else "Under"} {line} · actual {actual}',
    }


def resolve_leg(leg: dict) -> dict:
    """Dispatch on leg type."""
    ltype = (leg.get('type') or '').upper()
    if ltype == 'PROP':
        return _resolve_prop_leg(leg)
    if ltype in ('ML', 'RL', 'RUNLINE'):
        return _resolve_ml_leg(leg)
    if ltype == 'NRFI':
        return _resolve_nrfi_leg(leg)
    if ltype == 'TOTAL':
        return _resolve_total_leg(leg)
    return {'outcome': 'Pending', 'note': f'unknown leg type {ltype}'}


def compute_parlay_result(leg_outcomes: list) -> str:
    """All-or-nothing parlay math.
    Loss if any leg is Loss.
    Pending if no Loss but any Pending.
    Win otherwise (Pushes replace with the remaining legs — treated as won).
    """
    if any(l.get('outcome') == 'Loss' for l in leg_outcomes):
        return 'Loss'
    if any(l.get('outcome') == 'Pending' for l in leg_outcomes):
        return 'Pending'
    non_push = [l for l in leg_outcomes if l.get('outcome') != 'Push']
    if not non_push:
        return 'Push'   # every leg pushed → parlay pushes
    return 'Win'


def resolve_date(date_str: str, dry_run: bool = False) -> None:
    print(f'=== resolve_daily_degen · {date_str} ===')
    r = requests.get(
        f'{SB}/rest/v1/daily_degen?game_date=eq.{date_str}&select=*',
        headers=H, timeout=15,
    ).json()
    if not r:
        print('  no daily_degen row')
        return
    row = r[0]
    if row.get('result') and row.get('result') != 'Pending':
        print(f'  already resolved: {row["result"]}')
        return
    legs = row.get('legs') or []
    if not legs:
        print('  no legs to resolve')
        return

    leg_outcomes = []
    for leg in legs:
        outcome = resolve_leg(leg)
        outcome['pick'] = leg.get('pick')
        outcome['type'] = leg.get('type')
        leg_outcomes.append(outcome)

    parlay = compute_parlay_result(leg_outcomes)
    print(f'  parlay: {parlay}')
    for lo in leg_outcomes:
        mark = 'W' if lo['outcome'] == 'Win' else 'L' if lo['outcome'] == 'Loss' else 'P' if lo['outcome'] == 'Push' else '?'
        note = f' ({lo.get("note", "")})' if lo.get('note') else ''
        print(f'    [{mark}] {lo["pick"]}{note}')

    if dry_run:
        return

    resp = requests.patch(
        f'{SB}/rest/v1/daily_degen?game_date=eq.{date_str}',
        headers=HW,
        json={
            'legs_resolved': leg_outcomes,
            'result': parlay,
            'resolved_at': datetime.now(timezone.utc).isoformat(),
        },
        timeout=15,
    )
    if resp.status_code < 300:
        print(f'  ✓ patched')
    else:
        print(f'  ✗ patch failed {resp.status_code}: {resp.text[:200]}')


def backfill_all_pending(dry_run: bool = False) -> None:
    print(f'=== resolve_daily_degen · BACKFILL ===')
    yesterday = _yesterday_et()
    r = requests.get(
        f'{SB}/rest/v1/daily_degen'
        f'?or=(result.is.null,result.eq.Pending)'
        f'&game_date=lte.{yesterday}'
        f'&order=game_date.asc&select=game_date',
        headers=H, timeout=30,
    ).json()
    if not isinstance(r, list) or not r:
        print('  nothing to backfill')
        return
    dates = [row['game_date'] for row in r]
    print(f'  {len(dates)} dates to resolve')
    for d in dates:
        resolve_date(d, dry_run=dry_run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: yesterday ET)')
    ap.add_argument('--backfill', action='store_true', help='resolve all pending rows')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.backfill:
        backfill_all_pending(dry_run=args.dry_run)
    else:
        resolve_date(args.date or _yesterday_et(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
