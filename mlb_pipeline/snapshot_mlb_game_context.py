"""Nightly snapshot of mlb_game_context (2026-08-02 · Option B).

Copies every current mlb_game_context row into
mlb_game_context_snapshots with snapshot_date=today. Write-once per
(game_id, snapshot_date) — safe to run multiple times per day, only
the first per date sticks.

Purpose: preserve per-model predictions (mc_probabilities,
jerry_pred_*, model_pred_*) as historical training data for the
adaptive ensemble weighting workstream (Phase 1 Option B).

Wire into GHA cron post-generate_mlb_game_reads so we snapshot AFTER
predictions land but BEFORE they get overwritten tomorrow.

Usage:
    python snapshot_mlb_game_context.py [--date YYYY-MM-DD]
"""
import argparse, os, sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

FIELDS = [
    'game_id','game_date','home_team','away_team',
    'mc_probabilities','jerry_pred_home_runs','jerry_pred_away_runs',
    'jerry_pred_total','jerry_pred_spread',
    'model_pred_home_runs','model_pred_away_runs',
    'model_pred_total','model_pred_spread',
    'panel_implied_total','panel_implied_margin',
    'signal_confluence_net','signal_confluence_v2_net',
    'align_status','oddscrowd_snapshot','primary_play',
    'home_ml_close','away_ml_close','close_spread','close_total',
]


def snapshot(target_date: str | None = None) -> None:
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')
    snap_date = target_date or today
    print(f'=== snapshot_mlb_game_context · snapshot_date={snap_date} ===')

    # 2026-08-28 bug fix: was pulling first 500 rows across ALL dates
    # in mlb_game_context (no `game_date` filter), silently truncating
    # to whatever PostgREST returned first — corrupting the historical
    # training set. Snapshotter is nightly for a SINGLE date; scope
    # the query to that date and cap generously.
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
                     headers=H_READ,
                     params={'game_date': f'eq.{snap_date}',
                             'select': ','.join(FIELDS),
                             'limit': '500'}, timeout=30).json()
    if not isinstance(r, list):
        print(f'  ⚠ fetch failed: {r}'); return
    print(f'  {len(r)} game context rows to snapshot')

    payload = []
    for row in r:
        if not isinstance(row, dict) or not row.get('game_id'): continue
        rec = {k: row.get(k) for k in FIELDS}
        rec['snapshot_date'] = snap_date
        payload.append(rec)

    written = 0
    for i in range(0, len(payload), 100):
        chunk = payload[i:i+100]
        wr = requests.post(
            f'{SB}/rest/v1/mlb_game_context_snapshots?on_conflict=game_id,snapshot_date',
            headers=H_WRITE, json=chunk, timeout=30)
        if wr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ⚠ upsert {wr.status_code}: {wr.text[:200]}')

    print(f'  wrote {written} snapshot rows')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='snapshot_date (default: today ET)')
    args = p.parse_args()
    snapshot(target_date=args.date)
