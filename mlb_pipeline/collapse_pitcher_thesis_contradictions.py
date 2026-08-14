"""Pitcher-thesis contradiction resolver (2026-08-09).

Complements collapse_prop_jerry_contradictions.py (which handles OVER+UNDER
contradictions on the SAME line). This resolver handles CROSS-PROP thesis
contradictions on the SAME PITCHER.

The Cristian Javier bug (2026-08-09):
  outs_under 14.5 → BACK conv 55  ("early hook — L3 ERA 6.30")
  ha_over    4.5 → BACK conv 55  ("hittable — 7.94 xERA")
  ks_under   3.5 → FADE conv 48  ("edge too thin")

BACKing outs_under + ha_over is the "early hook / getting shelled" thesis.
That thesis implies ks_under should ALSO be BACK (early hook = fewer Ks).
FADEing ks_under while BACKing outs_under is per-prop myopia — Prop Jerry
evaluates each prop as a standalone Claude call without cross-referencing.

## Coherence model

Each prop maps to one of two theses per pitcher:

  Thesis EARLY_HOOK (pitcher getting shelled, pulled early):
    BACK: outs_under, er_over, ha_over, ks_under
    FADE: outs_over, er_under, ha_under, ks_over

  Thesis CRUISING (pitcher dominant, going deep):
    BACK: outs_over, er_under, ha_under, ks_over
    FADE: outs_under, er_over, ha_over, ks_under

Each Jerry verdict on a pitcher prop is a "vote" for one thesis (weighted
by conviction). The dominant thesis (>= 2 votes with conv >= 55) becomes
the pitcher's canonical read. Any prop verdict inconsistent with the
canonical thesis is capped:
  - Inconsistent BACK → downgraded to PASS with audit note
  - Inconsistent FADE → downgraded to PASS with audit note

## When there's no dominant thesis
No action — genuine mixed signals stay as-is (Jerry may have real reasons
each prop is priced differently).

## Runs
After generate_prop_jerry_synthesis.py + collapse_prop_jerry_contradictions.py
in the MLB cron chain.

CLI:
    python collapse_pitcher_thesis_contradictions.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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

# ── Thesis mapping ──────────────────────────────────────────────────
# Pitcher prop types only. Batter props are excluded from this resolver.
PITCHER_PROP_TYPES = {'outs_under','outs_over','er_under','er_over',
                       'ha_under','ha_over','ks_under','ks_over',
                       'bb_under','bb_over'}

# For each (prop_type, verdict), which thesis does it vote for?
# EARLY_HOOK = pitcher struggles; CRUISING = pitcher dominates.
# bb (walks) are ambiguous — omitted from thesis voting (a walking pitcher
# can be either dominant-and-nibbling or wild-and-getting-hit; can't tell).
THESIS_VOTES = {
    ('outs_under', 'BACK'): 'EARLY_HOOK',
    ('outs_under', 'FADE'): 'CRUISING',
    ('outs_over',  'BACK'): 'CRUISING',
    ('outs_over',  'FADE'): 'EARLY_HOOK',
    ('er_over',    'BACK'): 'EARLY_HOOK',
    ('er_over',    'FADE'): 'CRUISING',
    ('er_under',   'BACK'): 'CRUISING',
    ('er_under',   'FADE'): 'EARLY_HOOK',
    ('ha_over',    'BACK'): 'EARLY_HOOK',
    ('ha_over',    'FADE'): 'CRUISING',
    ('ha_under',   'BACK'): 'CRUISING',
    ('ha_under',   'FADE'): 'EARLY_HOOK',
    ('ks_under',   'BACK'): 'EARLY_HOOK',
    ('ks_under',   'FADE'): 'CRUISING',
    ('ks_over',    'BACK'): 'CRUISING',
    ('ks_over',    'FADE'): 'EARLY_HOOK',
}

DOMINANT_THESIS_MIN_VOTES = 2       # need 2+ votes to declare dominance
DOMINANT_THESIS_MIN_CONV  = 55      # each vote must be conv >= 55 to count
CONVICTION_GAP            = 15      # dominant thesis must beat opposing by 15pt total conviction


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def resolve(game_date: str, dry_run: bool = False) -> None:
    r = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H_READ,
        params={'sport': 'eq.MLB', 'game_date': f'eq.{game_date}',
                'select': 'id,player_name,prop_type,prop_line,direction,'
                          'call_verdict,conviction,short_read,game_id',
                'limit': '500'}, timeout=30).json()
    if not isinstance(r, list):
        print(f'  fetch failed: {r}'); return

    # Group by (game_id, player_name) — one bucket per pitcher-per-game
    by_pitcher = defaultdict(list)
    for row in r:
        if row.get('prop_type') not in PITCHER_PROP_TYPES: continue
        v = row.get('call_verdict')
        if v not in ('BACK', 'FADE'): continue  # PASS ignored for thesis vote
        by_pitcher[(row.get('game_id'), row.get('player_name'))].append(row)

    print(f'  {len(r)} prop reads · {len(by_pitcher)} pitchers w/ BACK|FADE calls')

    contradictions = 0
    for (gid, pitcher), rows in by_pitcher.items():
        if len(rows) < 2: continue  # need at least 2 props to have a thesis
        # Tally votes weighted by conviction
        tally = defaultdict(int)  # thesis -> total conviction
        counts = defaultdict(int)  # thesis -> vote count (with conv >= min)
        for row in rows:
            thesis = THESIS_VOTES.get((row['prop_type'], row['call_verdict']))
            if not thesis: continue
            conv = row.get('conviction') or 0
            tally[thesis] += conv
            if conv >= DOMINANT_THESIS_MIN_CONV:
                counts[thesis] += 1
        # Determine dominant thesis
        dominant = None
        for thesis in ('EARLY_HOOK', 'CRUISING'):
            other = 'CRUISING' if thesis == 'EARLY_HOOK' else 'EARLY_HOOK'
            if counts[thesis] >= DOMINANT_THESIS_MIN_VOTES and \
               tally[thesis] - tally[other] >= CONVICTION_GAP:
                dominant = thesis; break
        if not dominant: continue

        # Find inconsistent rows (voted for the OTHER thesis)
        other = 'CRUISING' if dominant == 'EARLY_HOOK' else 'EARLY_HOOK'
        offenders = [row for row in rows
                      if THESIS_VOTES.get((row['prop_type'], row['call_verdict'])) == other]
        if not offenders: continue

        contradictions += 1
        print(f'\n  {pitcher} ({gid[:8] if gid else "?"}): dominant={dominant} '
              f'({counts[dominant]}v/{tally[dominant]}c vs {counts[other]}v/{tally[other]}c)')
        for row in offenders:
            # 2026-08-09 (v2): FLIP the verdict to the thesis-aligned side
            # instead of just PASSing. If dominant thesis is EARLY_HOOK and
            # Jerry FADEd ks_under (which votes CRUISING), flip to BACK — the
            # thesis says ks_under SHOULD hit. Cap conviction at LEAN (55)
            # because we're inferring from other-prop coherence, not from
            # Jerry's own analysis of this specific prop. Include audit note
            # showing the flip source.
            #
            # This gets the "smart fade / smart back" behavior — instead of
            # leaving the edge on the table by PASSing, we act on the coherent
            # thesis and give the user a directional pick with honest tier.
            orig_verdict = row['call_verdict']
            new_verdict = 'BACK' if orig_verdict == 'FADE' else 'FADE'
            orig_conv = row.get('conviction') or 0
            flipped_conv = min(55, max(50, orig_conv))  # LEAN band
            print(f'    ! {row["prop_type"]:12} {orig_verdict:5} conv={orig_conv} '
                  f'(votes {other}) -> FLIP to {new_verdict} conv={flipped_conv} '
                  f'(inherits {dominant} thesis)')
            if dry_run: continue
            # 2026-08-13: audit_notes not short_read (leakage fix). Same-thesis
            # flip is a repair-class change; the note goes to audit_notes so
            # short_read keeps whatever Jerry originally analyzed.
            payload = {
                'call_verdict': new_verdict,
                'conviction': flipped_conv,
                'audit_notes': (f"[Auto-flipped 2026-08-09 pitcher-thesis: "
                                f"same-pitcher props back a {dominant} read "
                                f"({counts[dominant]} coherent votes at avg conv {tally[dominant]//counts[dominant]}). "
                                f"Flipped {orig_verdict}->{new_verdict} to align with thesis; "
                                f"conviction capped LEAN because inferred not directly analyzed.]")[:1500],
            }
            pu = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{row["id"]}',
                                headers=H_WRITE, json=payload, timeout=10)
            if pu.status_code not in (200, 204):
                print(f'      patch failed: {pu.status_code} {pu.text[:120]}')

    print(f'\n=== pitcher-thesis contradictions found: {contradictions}'
          f'{" (dry-run — no changes)" if dry_run else ""} ===')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    resolve(game_date=args.date or _et_today(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
