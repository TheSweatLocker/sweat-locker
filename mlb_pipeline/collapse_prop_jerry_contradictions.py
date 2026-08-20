"""Prop Jerry contradiction resolver (2026-08-01 · C).

Kills Skenes-style paradoxes where Jerry BACKs BOTH over and under of
the same event (or FADEs both). Each Jerry synthesis is a separate
Claude call that doesn't know what the opposite-direction row said,
so both sides can accidentally get BACK/BACK when the LLM sees a
clean narrative in each direction independently.

Resolution rules per (player, prop_type, prop_line) group:

  BACK+BACK        → contradiction. Keep higher conviction; downgrade
                     other to PASS (result: NO_ACTION on grading).
  FADE+FADE        → same rule (means Jerry hates both sides — coin flip).
  BACK+FADE        → coherent (both endorse the same underlying side).
                     Keep both.
  BACK/FADE + PASS → coherent. Keep both.
  PASS+PASS        → coherent. Keep both.
  Single row       → keep as-is.

Runs after generate_prop_jerry_synthesis.py in the cron chain.
Also can be run manually to clean historical days.

Usage:
    python collapse_prop_jerry_contradictions.py [--date YYYY-MM-DD] [--dry-run]
"""
import argparse, os, sys
from collections import defaultdict
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def resolve(game_date: str, dry_run: bool = False) -> None:
    r = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
                     headers=H_READ,
                     params={'sport': 'eq.MLB', 'game_date': f'eq.{game_date}',
                             'select': 'id,player_name,prop_type,prop_line,direction,'
                                       'call_verdict,conviction,short_read',
                             'limit': '500'}, timeout=30).json()
    if not isinstance(r, list):
        print(f'  ⚠ fetch failed: {r}'); return
    print(f'  {len(r)} prop_jerry_reads on {game_date}')

    groups = defaultdict(list)
    for row in r:
        k = (row['player_name'], row['prop_type'], row['prop_line'])
        groups[k].append(row)

    contradictions = 0
    downgrades = []
    for k, entries in groups.items():
        if len(entries) < 2: continue
        # Only 2-entry groups can contradict (over + under)
        dirs = {e['direction']: e for e in entries if e.get('direction')}
        if 'over' not in dirs or 'under' not in dirs: continue
        over_v = dirs['over'].get('call_verdict')
        under_v = dirs['under'].get('call_verdict')
        if not over_v or not under_v: continue
        # BACK+BACK or FADE+FADE = contradiction
        if over_v == under_v and over_v in ('BACK', 'FADE'):
            contradictions += 1
            # Winner: higher conviction stays; loser → PASS
            over_conv = dirs['over'].get('conviction') or 0
            under_conv = dirs['under'].get('conviction') or 0
            if over_conv >= under_conv:
                loser = dirs['under']
                winner = dirs['over']
            else:
                loser = dirs['over']
                winner = dirs['under']
            downgrades.append((loser, winner, k))

    print(f'\n  contradictions: {contradictions}')
    for loser, winner, k in downgrades:
        print(f'  [{k[0]:<20} {k[1]}/{k[2]}] '
              f'winner: {winner["direction"]} {winner["call_verdict"]} conv {winner.get("conviction")} · '
              f'downgrade: {loser["direction"]} → PASS (was {loser["call_verdict"]} conv {loser.get("conviction")})')
        if dry_run: continue
        # 2026-08-20: user-facing short_read now carries a clean take.
        # Full audit diagnostic (winner conv, original take) stashed
        # elsewhere for support visibility if the table has a long_read
        # column, else discarded — user visible copy takes priority.
        user_short = (
            f"Data pushes the other direction on this one — the "
            f"opposite-side read on the same prop scored higher "
            f"conviction. Skipping rather than run two contradictory "
            f"takes on the same player."
        )
        audit_note = (
            f'[Auto-collapsed C: opposite-direction {winner["call_verdict"]} '
            f'at higher conviction ({winner.get("conviction")}).] '
            f'Original take: {loser.get("short_read","")[:400]}'
        )
        payload = {
            'call_verdict': 'PASS',
            'conviction': loser.get('conviction'),   # preserve original score for audit
            'short_read': user_short[:500],
            'long_read': audit_note[:1000],
        }
        pu = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{loser["id"]}',
                            headers=H_WRITE, json=payload, timeout=10)
        if pu.status_code not in (200, 204):
            print(f'    ⚠ patch failed: {pu.status_code} {pu.text[:120]}')

    if dry_run and contradictions:
        print(f'\n  DRY RUN — no changes written. Re-run without --dry-run to apply.')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    resolve(game_date=args.date or today_et(), dry_run=args.dry_run)
