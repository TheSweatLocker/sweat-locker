"""FADE→PASS discipline for bomb-list prop_types (2026-08-14).

30-day audit of natural (non-Auto-repair) Jerry FADE verdicts uncovered
that certain prop_types have systematically wrong FADE outcomes — Jerry's
prompt logic conflates "negative ROI on this side" with "+EV on opposite
side" which doesn't hold when juice is symmetric.

Empirical FADE hit rates per prop_type over 30d (all conviction bands):
    outs_over    : 17% (1-5 high-conv, 13-18 overall)  🚨
    bb_under     : 27% (3-8 fires)                      🚨
    bb_over      : 33% (6-12 fires)                     🚨
    er_under     : 33% (15-30 fires)                    🚨
    ks_under     : 38% (3-5 fires)                     ⚠️

All below break-even. When Jerry FADEs these, ~60% of the time the pick
would have hit. Converting FADE→PASS protects users from what's essentially
a coin-flip fired with false confidence.

The structural fix — improving Jerry's FADE prompt semantics — is queued
separately. This is the immediate discipline gate that ships forward.

Bomb list is self-tunable: rebuild the constants from prop_jerry_reads
30d rolling audit. For now, hand-set from tonight's audit.

CLI:
    python apply_fade_type_discipline.py [--date YYYY-MM-DD] [--dry-run]

Runs AFTER generate_prop_jerry_synthesis + apply_refit_verdict_override.
"""
from __future__ import annotations
import argparse, os, sys
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

# Prop_types where FADE verdicts hit BELOW 45% over 30d (n>=5).
# Rebuild via audit query rerun; hand-set from 2026-08-14 audit.
# Note: hit rate = FADE cashed (i.e., faded side missed). Below 45% =
# blind inversion beats us and users see losing "don't take this" advice.
FADE_BOMB_TYPES = {
    'outs_over',   # 17% — near-inverse, catastrophic
    'er_under',    # 33-41%
    'bb_over',     # 33%
    'bb_under',    # 27%
    'ks_under',    # 38%
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def run(game_date: str, dry_run: bool = False) -> int:
    """Convert FADE verdicts on bomb-list prop_types to PASS.
    Preserves the original short_read + audit trail so we can audit later."""
    print(f'=== FADE→PASS discipline · {game_date} ===')
    print(f'  bomb list: {sorted(FADE_BOMB_TYPES)}')

    r = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H_READ,
        params={'sport': 'eq.MLB', 'game_date': f'eq.{game_date}',
                'call_verdict': 'eq.FADE',
                'select': 'id,player_name,prop_type,direction,conviction,short_read,audit_notes'},
        timeout=15)
    if r.status_code != 200:
        print(f'  fetch failed: {r.status_code}'); return 0
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        print(f'  no FADE rows for {game_date}'); return 0
    print(f'  {len(rows)} total FADE rows')

    converted = 0
    for row in rows:
        pt = (row.get('prop_type') or '').lower()
        if pt not in FADE_BOMB_TYPES: continue
        converted += 1
        if dry_run:
            print(f'  [DRY] would convert: {row.get("player_name","-"):22} {pt:12} {row.get("direction","-"):5} FADE→PASS')
            continue
        new_note = (f'[Auto-fade-discipline 2026-08-14 FADE_TYPE_BOMB: '
                    f'{pt} FADE historically hits <45% (30d audit). '
                    f'Converting FADE→PASS to protect users from likely-wrong direction.]')
        existing = row.get('audit_notes') or ''
        combined = (existing + '\n---\n' + new_note) if existing else new_note
        payload = {'call_verdict': 'PASS',
                   'conviction': min(row.get('conviction') or 30, 30),
                   'audit_notes': combined[:1500]}
        pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{row["id"]}',
            headers=H_WRITE, json=payload, timeout=10)
        if pr.status_code in (200, 201, 204):
            print(f'  ✓ {row.get("player_name","-"):22} {pt:12} {row.get("direction","-"):5} FADE→PASS')
        else:
            print(f'  ✗ patch failed: {pr.status_code} {pr.text[:150]}')

    print(f'\n{"[DRY] would convert" if dry_run else "converted"} {converted} FADE→PASS')
    return converted


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD; defaults to today ET')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date or _et_today(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
