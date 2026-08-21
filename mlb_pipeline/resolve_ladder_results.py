"""Ladder rung resolver (2026-08-13).

Backfills Win/Loss/Push on pending ladder_rung rows after their games grade,
then updates ladder_state.current_streak / longest_streak / last_result
based on the WIN-run tail.

Grading logic per market:
  ml     → home_score vs away_score, matched against pick_side (label
           contains team name)
  spread → apply the ladder rung's implied side to final margin
  total  → applies pick_side (over/under) to home_score + away_score
           vs the pick's line (parsed from label if present, else stored
           in the future line field)

Sport-universal — looks up the resolved score from each sport's canonical
results table (mlb_pipeline_results, nfl_pipeline_results, etc.).

Ladder discipline: streak resets on any LOSS. Push doesn't reset — it's
neutral. Sequence of results is chronological by qualified_at.

CLI:
    python resolve_ladder_results.py [--since 2026-08-01] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, re
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Per-sport results source. Each returns {game_id: (home_score, away_score)}.
# 2026-08-20: fixed table names — the *_pipeline_results tables never
# existed. Real tables are *_game_results. This bug silently produced
# "loaded 0 MLB results" every day since 8/13 → NO ladder rungs ever
# graded → streak counter stuck at 0. Caught during today's audit
# when user noticed ladder result=None on the Brewers pick.
RESULTS_TABLE = {
    'MLB': 'mlb_game_results',
    'NFL': 'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    'NBA': 'nba_game_results',
    'NHL': 'nhl_game_results',
}


def load_results(sport: str, since: str) -> dict:
    tbl = RESULTS_TABLE.get(sport)
    if not tbl: return {}
    r = requests.get(f'{SB}/rest/v1/{tbl}', headers=H_READ,
        params={'game_date': f'gte.{since}',
                'select': 'game_id,home_score,away_score,home_team,away_team'},
        timeout=30)
    if r.status_code != 200: return {}
    out = {}
    for row in r.json():
        gid = row.get('game_id')
        hs = row.get('home_score'); aws = row.get('away_score')
        if gid and hs is not None and aws is not None:
            out[gid] = {
                'home_score': int(hs), 'away_score': int(aws),
                'home_team': row.get('home_team'), 'away_team': row.get('away_team'),
            }
    return out


def grade_rung(rung: dict, res: dict) -> Optional[str]:
    """Return 'Win' / 'Loss' / 'Push' or None if unresolvable."""
    if not res: return None
    hs = res.get('home_score'); aws = res.get('away_score')
    if hs is None or aws is None: return None
    home = (res.get('home_team') or '').lower()
    away = (res.get('away_team') or '').lower()
    market = rung.get('market')
    pick = (rung.get('pick_side') or '').lower()

    if market == 'ml':
        pick_home = home and home in pick
        pick_away = away and away in pick
        if not (pick_home or pick_away): return None
        if hs > aws: return 'Win' if pick_home else 'Loss'
        if aws > hs: return 'Win' if pick_away else 'Loss'
        return 'Push'  # unlikely in ML markets

    if market == 'total':
        # Parse line from pick_side ("Under 8.5" / "Over 8.0")
        m = re.search(r'(over|under)\s+(\d+(?:\.\d+)?)', pick, re.I)
        if not m: return None
        side = m.group(1).lower()
        line = float(m.group(2))
        total = hs + aws
        if abs(total - line) < 0.01: return 'Push'
        if side == 'over':
            return 'Win' if total > line else 'Loss'
        return 'Win' if total < line else 'Loss'

    if market == 'spread':
        # Parse line from pick_side ("Team +1.5" / "Team -3")
        m = re.search(r'([+\-]?\d+(?:\.\d+)?)', pick)
        if not m: return None
        line = float(m.group(1))
        pick_home = home and home in pick
        pick_away = away and away in pick
        if not (pick_home or pick_away): return None
        # Home covers +line if hs + line > aws; -line same formula
        if pick_home:
            adj = (hs + line) - aws
        else:
            adj = (aws + line) - hs
        if abs(adj) < 0.01: return 'Push'
        return 'Win' if adj > 0 else 'Loss'
    return None


def update_state_from_rungs():
    """Recompute ladder_state.current_streak / longest_streak / last_result
    from all graded ladder_rung rows (chronological).

    2026-08-21 BUG FIX: prior version reset the state to 'reset' whenever
    the latest-graded result was Loss, even if the CURRENT active rung is
    a NEW rung locked after the loss and still in flight. Consequence:
    tonight's E-Rod Over 5.5 HA (rung 6) got flipped to reset because
    yesterday's Grayson rung 5 late-graded as Loss. Fix: only reset if
    the graded LOSS is on the currently-active rung id.
    """
    r = requests.get(f'{SB}/rest/v1/ladder_rung', headers=H_READ,
        params={'result': 'not.is.null', 'order': 'qualified_at.asc',
                'select': 'id,result,qualified_at'}, timeout=15)
    if r.status_code != 200: return
    rows = r.json()
    if not isinstance(rows, list): return
    current = 0; longest = 0; last_result = None; last_rung_id = None
    for row in rows:
        res = row.get('result')
        last_result = res
        last_rung_id = row.get('id')
        if res == 'Win':
            current += 1
            longest = max(longest, current)
        elif res == 'Loss':
            current = 0
        # Push: neither reset nor extend
    st = requests.get(f'{SB}/rest/v1/ladder_state?id=eq.1', headers=H_READ,
        timeout=10).json()
    cur_state = st[0] if isinstance(st, list) and st else {}
    new_status = cur_state.get('status') or 'waiting'
    note = cur_state.get('note') or ''
    active_rung_id = cur_state.get('active_rung_id')
    if (last_result == 'Loss' and cur_state.get('status') == 'active'
            and last_rung_id == active_rung_id):
        # Only reset when the graded LOSS is on the currently-active rung.
        # If a newer rung has been locked, keep the active status — the
        # loss on the older rung is history, not the current in-flight play.
        new_status = 'reset'
        note = f'Reset after loss. New run starts on next qualifier.'
    payload = {
        'status': new_status,
        'current_streak': current,
        'longest_streak': longest,
        'last_result': last_result,
        'last_updated_at': datetime.now(timezone.utc).isoformat(),
        'note': note,
    }
    requests.patch(f'{SB}/rest/v1/ladder_state?id=eq.1',
        headers=H_WRITE, json=payload, timeout=10)
    print(f'  ladder_state → streak {current} · longest {longest} · last {last_result}')


def run(since: str, dry_run: bool = False) -> int:
    r = requests.get(f'{SB}/rest/v1/ladder_rung', headers=H_READ,
        params={'result': 'is.null', 'game_date': f'gte.{since}',
                'select': 'id,sport,game_id,market,pick_side,game_date,matchup'},
        timeout=15)
    if r.status_code != 200:
        print(f'  fetch failed: {r.status_code}'); return 0
    pending = r.json()
    if not isinstance(pending, list) or not pending:
        print('  no pending rungs to resolve'); update_state_from_rungs(); return 0

    # Load results per sport once
    sports = set(r['sport'] for r in pending)
    results_by_sport = {}
    for s in sports:
        results_by_sport[s] = load_results(s, since)
        print(f'  loaded {len(results_by_sport[s])} {s} results')

    graded = 0
    for rung in pending:
        res = results_by_sport.get(rung['sport'], {}).get(rung['game_id'])
        if not res: continue
        verdict = grade_rung(rung, res)
        if not verdict: continue
        if dry_run:
            print(f'  [DRY] rung {rung["id"]} · {rung["matchup"]} · {rung["pick_side"]} → {verdict}')
            graded += 1
            continue
        pr = requests.patch(f'{SB}/rest/v1/ladder_rung?id=eq.{rung["id"]}',
            headers=H_WRITE, json={
                'result': verdict,
                'resolved_at': datetime.now(timezone.utc).isoformat(),
            }, timeout=10)
        if pr.status_code in (200, 201, 204):
            graded += 1
            print(f'  ✓ {rung["matchup"]} · {rung["pick_side"]} → {verdict}')
    print(f'\n{"[DRY] would grade" if dry_run else "graded"} {graded} rungs')
    if not dry_run: update_state_from_rungs()
    return graded


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--since', help='YYYY-MM-DD; defaults to 14 days back')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    since = args.since or (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat()
    print(f'=== resolve_ladder_results · since {since} ===')
    run(since, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
