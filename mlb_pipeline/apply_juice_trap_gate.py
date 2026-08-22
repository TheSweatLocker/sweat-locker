"""Juice-trap gate (2026-08-19) — smart demote of hits_over PRIME/STRONG
when juice is too rich for the underlying refit conviction.

Per user memory `feedback_batter_hits_juice_trap_803`: "Batter Hits O 0.5 at
-200+ trap (PRIME ~69%); swap teammate."

Sliding rule (2026-08-19 pm — refined from flat -200 cutoff after user
feedback that blunt rule kills legit confident plays at moderate juice):

  Juice band       Break-even   Keep STRONG if refit_conviction ≥
  -200 to -224     66.7-69.1%   65 (≥5pp margin over BE ceiling)
  -225 to -249     69.2-71.4%   70 (matches BE ceiling with buffer)
  -250 to -299     71.5-74.9%   75 (needs strong confluence to clear)
  -300+            75%+         (always demote — no historical PRIME clears)

Rationale: a hits_over pick with refit 70 at -220 has real edge (implied win%
69% + margin). Same juice with refit 50 = pure trap. Refit_conviction is the
best single scalar for "how much signal actually stacks here" — post-hoc
logistic-fit trained on 60d graded outcomes, so it already knows what wins.

Runs AFTER sweep_prop_coverage.py (which attaches book_over_odds) and BEFORE
apply_prop_refit / generate_prop_jerry_synthesis / generate_daily_degen.

CLI:
  python apply_juice_trap_gate.py               # today
  python apply_juice_trap_gate.py --date 2026-08-19
  python apply_juice_trap_gate.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
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

JUICE_CAP = -200  # any odds ≤ -200 gets evaluated against refit sliding scale

# (min_odds_inclusive, refit_conviction_floor) — juice band → refit needed to keep STRONG.
# odds ≤ -300 always demote (no floor high enough). odds > -200 never demote here.
JUICE_REFIT_SCALE = [
    (-300, None),  # -300+: always demote
    (-250,   75),  # -250 to -299: need refit ≥ 75
    (-225,   70),  # -225 to -249: need refit ≥ 70
    (-200,   65),  # -200 to -224: need refit ≥ 65
]


def _refit_floor(odds: int) -> tuple[float | None, str]:
    """Return (floor, band_label). floor=None means always-demote band."""
    for min_odds, floor in JUICE_REFIT_SCALE:
        if odds <= min_odds:
            band = (f'≤-300 always-demote' if floor is None
                    else f'{min_odds} to {min_odds+24}: refit≥{floor}')
            return floor, band
    return None, 'no-demote'


def _today_et() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def run(game_date: str | None = None, dry_run: bool = False) -> None:
    gd = game_date or _today_et()
    print(f'=== juice-trap gate · {gd}{" [DRY]" if dry_run else ""} ===')

    r = requests.get(
        f'{SB}/rest/v1/mlb_pipeline_props'
        f'?game_date=eq.{gd}&prop_type=eq.hits_over&direction=eq.over'
        f'&tier=in.(PRIME,STRONG)&book_over_odds=lte.{JUICE_CAP}'
        f'&select=id,player_name,prop_type,direction,prop_line,tier,'
        f'book_over_odds,refit_conviction,conviction,signals',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        print(f'  ✗ fetch failed {r.status_code}: {r.text[:150]}')
        return
    candidates = r.json() or []
    print(f'  candidates: {len(candidates)} hits_over PRIME/STRONG @ ≤{JUICE_CAP} juice')
    if not candidates:
        return

    demoted = 0; spared = 0
    for p in candidates:
        prev_tier = p['tier']
        odds = int(p['book_over_odds'])
        refit = p.get('refit_conviction')
        if refit is None: refit = p.get('conviction') or 0
        floor, band = _refit_floor(odds)

        # SPARED path: refit clears the floor at this juice band
        if floor is not None and float(refit) >= floor:
            spared += 1
            print(f'  ↳ SPARED {p["player_name"]:<22} @{odds}  refit={refit} ≥ {floor} ({band}) — kept {prev_tier}')
            continue

        # DEMOTED path
        sigs = p.get('signals') or {}
        if not isinstance(sigs, dict): sigs = {}
        reason = (
            f'{prev_tier} → LEAN @ {odds}: refit={refit} '
            f'{"below floor " + str(floor) if floor is not None else "in always-demote band"} ({band}). '
            f'Per feedback_batter_hits_juice_trap_803 — margin does not clear vig.'
        )
        sigs['juice_trap_demoted'] = reason
        payload = {'tier': 'LEAN', 'signals': sigs}
        print(f'  ✕ DEMOTE {p["player_name"]:<22} @{odds}  refit={refit}  {prev_tier} → LEAN ({band})')
        if dry_run:
            demoted += 1; continue
        # 2026-08-22 ROOT-CAUSE FIX: PATCH ALL COPIES of this prop, not just the
        # id with juice-populated odds. Prior code filtered `WHERE book_over_odds
        # <= JUICE_CAP` (line 83) and PATCHed only that id — but the props table
        # has duplicate rows (same player+prop_type+direction+line, some with
        # book_over_odds attached, some NULL). The odds-NULL copy escaped the
        # gate and stayed at PRIME/STRONG. Sweat Card then picked the un-demoted
        # copy. Root cause: dedup happens PRE-scoring but sweep_prop_coverage
        # re-adds coverage stubs post-scoring. Fix: patch on natural key so ALL
        # copies of the same prop get demoted uniformly. Bandaid-free: even if
        # dedup fails somewhere upstream, this ensures the demote is complete.
        import urllib.parse as _up
        pn = _up.quote(p['player_name'], safe='')
        patch_url = (
            f'{SB}/rest/v1/mlb_pipeline_props'
            f'?game_date=eq.{gd}'
            f'&player_name=eq.{pn}'
            f'&prop_type=eq.{p["prop_type"]}'
            f'&direction=eq.{p["direction"]}'
            f'&prop_line=eq.{p["prop_line"]}'
            f'&tier=in.(PRIME,STRONG)'  # only touch tier if still elevated
        )
        pr = requests.patch(patch_url, headers=H_WRITE, json=payload, timeout=10)
        if pr.status_code in (200, 204):
            demoted += 1
        else:
            print(f'    ✗ patch failed {pr.status_code}: {pr.text[:120]}')

    print(f'\n  {"[DRY] " if dry_run else "✓ "}demoted {demoted}, spared {spared} '
          f'of {len(candidates)} candidates')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today ET)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(game_date=args.date, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
