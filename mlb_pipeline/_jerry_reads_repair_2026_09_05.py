"""Immediate repair of contaminated jerry_reads for 9/5 + 9/6.

Root fixes shipped earlier this session:
  1. 'this season' prose bug → templates now say 'trailing L13'
  2. auto-sim-repair now realigns to primary_play instead of forced PASS
  3. short_read truncation guard added to jerry_reads_dual_write

This script REPAIRS the already-written reads for today so users see
clean state without waiting for next full pipeline cron:

  (a) For MLB reads with primary_play mismatch (LR overrode ML but read
      still on totals): realign short_read/long_read to primary_play.sub.
  (b) For MLB reads whose short_read starts with "Our model has this
      total closer to" (auto-sim-repair PASS ghost): realign to
      primary_play or drop to 'model backs {team}' template.
  (c) For NCAAF reads with short_read < 100 chars but long_read >= 100:
      regenerate short from first 2 sentences of long.

Usage: python _jerry_reads_repair_2026_09_05.py
"""
from __future__ import annotations
import os, sys, io, re, requests
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

for line in Path(__file__).parent.joinpath('.env').read_text().split('\n'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}


def _short_from_long(long_text: str) -> str:
    sents = re.split(r'(?<=[.!?])\s+(?=[A-Z])', long_text.strip())
    return (' '.join(sents[:2])[:400]) or long_text[:400]


def repair_for_date(game_date: str, sport: str, ctx_table: str) -> None:
    print(f'\n=== Repairing jerry_reads for {sport} {game_date} ===')
    # Load reads
    reads = requests.get(f'{SB}/rest/v1/jerry_reads',
        headers=H_READ,
        params={'game_date': f'eq.{game_date}', 'sport': f'eq.{sport}',
                'select': 'id,game_id,call_market,call_side,call_text,short_read,long_read,conviction'},
        timeout=20).json()
    if not isinstance(reads, list):
        print(f'  fetch failed: {reads}'); return

    # Load primary_play for each game
    ctx = requests.get(f'{SB}/rest/v1/{ctx_table}',
        headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'game_id,primary_play'},
        timeout=20).json()
    pp_by_gid = {c['game_id']: (c.get('primary_play') or {})
                  for c in (ctx if isinstance(ctx, list) else [])}

    realigned = short_fixed = passed = 0
    for r in reads:
        gid = r.get('game_id')
        pp = pp_by_gid.get(gid) or {}
        current_short = r.get('short_read') or ''
        current_long = r.get('long_read') or ''
        current_market = (r.get('call_market') or '').lower()
        current_side = (r.get('call_side') or '').upper()

        pp_type = (pp.get('type') or '').lower()
        pp_tier = pp.get('tier') or ''
        pp_side = (pp.get('side') or '').upper()
        pp_label = pp.get('label') or ''
        pp_sub = pp.get('sub') or ''
        pp_conv = pp.get('conviction') or 60

        # ── (a)+(b) Realign when primary_play differs from read OR
        #    when short_read is the auto-sim-repair PASS ghost.
        pp_valid = pp_type in ('ml', 'rl', 'total', 'prop') and pp_tier in ('PRIME', 'STRONG')
        market_mismatch = pp_valid and (current_market != pp_type or (pp_type != 'total' and current_side != pp_side))
        ghost_prefix = current_short.startswith('Our model has this total closer to')

        if pp_valid and (market_mismatch or ghost_prefix):
            new_short = pp_sub if pp_sub else f'Model backs {pp_label} — {pp_conv}% confidence'
            new_long = (
                f'The published call is {pp_label} ({pp_tier}, {pp_conv}%). '
                f'{pp_sub}. '
                f'(Read realigned from prior stale market pick.)'
            )
            payload = {
                'call_market': pp_type,
                'call_side': pp_side or None,
                'call_text': pp_label,
                'call_line': None,
                'conviction': pp_conv,
                'short_read': new_short[:2000],
                'long_read': new_long[:2000],
            }
            pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{r["id"]}',
                                headers=H_WRITE, json=payload, timeout=15)
            if pr.status_code < 300:
                realigned += 1
                print(f'  ↺ realigned {gid[:40]:40s} → {pp_label}')
            continue

        # ── (c) short_read truncation — regenerate from long_read
        if current_short and len(current_short) < 100 and current_long and len(current_long) >= 100:
            new_short = _short_from_long(current_long)
            if new_short and len(new_short) > len(current_short):
                pr = requests.patch(f'{SB}/rest/v1/jerry_reads?id=eq.{r["id"]}',
                                    headers=H_WRITE, json={'short_read': new_short[:2000]}, timeout=15)
                if pr.status_code < 300:
                    short_fixed += 1
                    if short_fixed <= 5:
                        print(f'  ✂ short_fix {gid[:40]:40s} ({len(current_short)} → {len(new_short)} chars)')
                    continue

        passed += 1

    print(f'  realigned to primary_play: {realigned}')
    print(f'  short_read regenerated:    {short_fixed}')
    print(f'  untouched:                 {passed}')


def main():
    for date in ('2026-09-05', '2026-09-06'):
        repair_for_date(date, 'MLB', 'mlb_game_context')
        repair_for_date(date, 'NCAAF', 'ncaaf_game_context')


if __name__ == '__main__':
    main()
