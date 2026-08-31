"""Standalone MC-dissent gate pass — safety net for cron ordering.

RCA finding 2026-08-31: recompute_primary_play.py can silently drop
in cron (platform load, silent skip, etc). When it drops, primary_play
stays at whatever game_context.py wrote BEFORE enrich_monte_carlo
populated mc_probabilities → MC-dissent gate never fires because the
ctx it saw had no MC yet.

Symptom: Braves 8/31 shipped PRIME 94 in the app while Jerry already
knew MC said 37% home win. `primary_play_computed_at` = 20:46 UTC
vs `mc_computed_at` = 21:20 UTC — 34 min gap where the gate would
have fired if anything re-ran.

This script is the belt-and-suspenders: reads every game's ctx, applies
ONLY the defensive gates (no ensemble rebuild — fast), writes patched
primary_play back. Runs in every cron immediately after
enrich_monte_carlo. If recompute succeeds later, it re-runs with fresh
data and this step's writes get overwritten (idempotent).

Usage:
    python apply_mc_gate_only.py                    # today
    python apply_mc_gate_only.py --date 2026-08-31
    python apply_mc_gate_only.py --sport NCAAF      # cross-sport
    python apply_mc_gate_only.py --dry-run
"""
import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from copy import deepcopy

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

SPORT_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _dict_summary(pp: dict) -> str:
    return f"{pp.get('tier','—')}·{pp.get('label','—')}·conv{pp.get('conviction','—')}"


def run(date: str, sport: str, dry_run: bool = False) -> None:
    table = SPORT_TABLE.get(sport)
    if not table:
        print(f'  unknown sport {sport}'); return
    print(f'=== apply_mc_gate_only · {sport} · {date}{"·DRY" if dry_run else ""} ===')
    r = requests.get(
        f'{SB}/rest/v1/{table}?game_date=eq.{date}&select=game_id,home_team,away_team,primary_play,mc_probabilities',
        headers=H_READ, timeout=15,
    )
    if r.status_code != 200:
        print(f'  ⚠ fetch failed {r.status_code}: {r.text[:150]}')
        return
    ctxs = r.json() or []
    if not ctxs:
        print('  no rows'); return

    # Import gate lazily so path issues surface loudly
    from defensive_gates import apply_all_defensive_gates

    patched = 0
    for c in ctxs:
        if isinstance(c, str): continue
        gid = c.get('game_id')
        pp = c.get('primary_play') or {}
        if not pp or not isinstance(pp, dict):
            continue
        # Copy so we can compare before/after
        original = deepcopy(pp)
        # Apply gates (mutates in place). Uses ctx.mc_probabilities.
        apply_all_defensive_gates(pp, c)
        # Detect change
        before = _dict_summary(original)
        after = _dict_summary(pp)
        if before == after:
            continue  # gate no-op — either MC absent or no dissent needed
        away = c.get('away_team', '?')[:20]; home = c.get('home_team', '?')[:20]
        print(f'  🚨 {away:20} @ {home:20} · {before}  →  {after}')
        if dry_run: patched += 1; continue
        pr = requests.patch(
            f'{SB}/rest/v1/{table}?game_id=eq.{gid}',
            headers=H_WRITE, json={'primary_play': pp}, timeout=15,
        )
        if pr.status_code in (200, 204):
            patched += 1
        else:
            print(f'    ⚠ patch failed {pr.status_code}: {pr.text[:120]}')
    print(f'✓ {patched} row(s) gated{" [DRY]" if dry_run else ""}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--sport', default='MLB', choices=list(SPORT_TABLE.keys()))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run(date=args.date or _et_today(), sport=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
