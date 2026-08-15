"""archive_public_splits — permanent snapshot of OC + Fadereport splits
side-by-side per (sport, game_id, market, side, ts).

The pattern miner joins THIS archive against game results to score
"public agreed / disagreed / one-loud" hypotheses over long windows.
Fadereport 14d retention DOES NOT AFFECT the archive — we snapshot at
each pipeline run and the archive holds forever.

Sport-universal. Runs after write_line_snapshot.py + write_line_history.py.

CLI
  python archive_public_splits.py                    # all sports
  python archive_public_splits.py --sport MLB
  python archive_public_splits.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

SUPPORTED_SPORTS = ['MLB', 'NFL', 'NCAAF', 'NCAAB', 'NHL', 'UFC']


def latest_oc(sport: str, since_hrs: int = 6) -> dict:
    """Return {(game_id, market, side): row} latest OC snapshot per key."""
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hrs)).isoformat().replace('+', '%2B')
    r = requests.get(
        f'{SB}/rest/v1/line_snapshot'
        f'?sport=eq.{sport}&source=eq.oddscrowd&snapshot_ts=gte.{since}'
        f'&select=game_id,market,pick_side,money_pct,bets_pct,divergence,line,snapshot_ts'
        f'&order=snapshot_ts.desc&limit=3000',
        headers=H_READ, timeout=30)
    if r.status_code != 200: return {}
    idx = {}
    for row in (r.json() or []):
        key = (row.get('game_id'), (row.get('market') or '').lower(),
               (row.get('pick_side') or '').upper())
        if key not in idx: idx[key] = row
    return idx


def latest_fr(sport: str, since_hrs: int = 6) -> dict:
    """Same shape for fadereport_signals; returns empty dict if table missing."""
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hrs)).isoformat().replace('+', '%2B')
    r = requests.get(
        f'{SB}/rest/v1/fadereport_signals'
        f'?sport=eq.{sport}&captured_at=gte.{since}'
        f'&select=game_id,market,pick_side,handle_pct,bettors_pct,captured_at'
        f'&order=captured_at.desc&limit=3000',
        headers=H_READ, timeout=15)
    if r.status_code != 200: return {}
    idx = {}
    for row in (r.json() or []):
        key = (row.get('game_id'), (row.get('market') or '').lower(),
               (row.get('pick_side') or '').upper())
        if key not in idx: idx[key] = row
    return idx


def build_rows(sport: str, oc_idx: dict, fr_idx: dict) -> list:
    """Zip both sources on (game, market, side) → archive rows. A key with
    only one source still gets archived so we can measure source coverage."""
    now_iso = datetime.now(timezone.utc).isoformat()
    all_keys = set(oc_idx.keys()) | set(fr_idx.keys())
    rows = []
    for key in all_keys:
        gid, market, side = key
        if not gid or not market or not side: continue
        oc = oc_idx.get(key) or {}
        fr = fr_idx.get(key) or {}
        rows.append({
            'sport':          sport,
            'game_id':        gid,
            'market':         market,
            'pick_side':      side,
            'oc_money_pct':   oc.get('money_pct'),
            'oc_bets_pct':    oc.get('bets_pct'),
            'oc_divergence':  oc.get('divergence'),
            'fr_handle_pct':  fr.get('handle_pct'),
            'fr_bettors_pct': fr.get('bettors_pct'),
            'current_line':   oc.get('line'),
            'captured_at':    now_iso,
        })
    return rows


def run_sport(sport: str, dry_run: bool = False) -> int:
    oc = latest_oc(sport)
    fr = latest_fr(sport)
    rows = build_rows(sport, oc, fr)
    print(f'  {sport}: OC keys={len(oc)}  FR keys={len(fr)}  archive rows={len(rows)}')
    if not rows or dry_run:
        if dry_run and rows:
            print(f'    [DRY] sample: {rows[0]}')
        return len(rows) if dry_run else 0
    written = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i+200]
        r = requests.post(
            f'{SB}/rest/v1/public_splits_archive'
            f'?on_conflict=sport,game_id,market,pick_side,captured_at',
            headers=H_WRITE, json=chunk, timeout=30)
        if r.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {r.status_code} {r.text[:150]}')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', choices=SUPPORTED_SPORTS + ['ALL'], default='ALL')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    sports = SUPPORTED_SPORTS if args.sport == 'ALL' else [args.sport]
    print(f'=== archive_public_splits · {"/".join(sports)} '
          f'{"[DRY]" if args.dry_run else ""} ===')
    total = 0
    for s in sports:
        total += run_sport(s, dry_run=args.dry_run)
    print(f'\n  ✓ {total} archive rows written')


if __name__ == '__main__':
    main()
