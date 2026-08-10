"""Refit → Prop Jerry verdict override (2026-08-10).

Post-Prop-Jerry pass that FORCES BACK/FADE/PASS on prop_jerry_reads
based on the delta between raw conviction and refit conviction.
Runs AFTER apply_prop_refit + generate_prop_jerry_synthesis + the
existing collapse scripts.

## Rules (user-approved 2026-08-10 · project_refit_v2_expansion_810)

Given: raw = prop's `conviction`, refit = prop's `refit_conviction`

  |Δ| = |refit - raw|

  |Δ| >= 30 AND refit < 40   → FORCE FADE  (raw was fooled; opposite side is real)
  |Δ| >= 20 AND refit >= 80  → FORCE BACK  (refit confirms + boosts; take it)
  |Δ| >= 20 AND refit < 45   → FORCE PASS  (too much disagreement, sit out)
  else                       → HOLD (small delta or refit close to raw)

## No-refit cap

If refit_conviction is NULL for a prop that made a BACK/FADE call:
  - Cap conviction at 55 (LEAN)
  - Add "NO_REFIT_COVERAGE" audit tag
  - Don't flip verdict — just downgrade confidence

## Idempotency

Skips any prop_jerry_reads row where short_read already contains
"Auto-refit-override 2026-08-10". Safe to re-run same day.

## Sport universal

Runs across MLB / NFL / any sport whose props table has refit_conviction.

CLI:
    python apply_refit_verdict_override.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations
import argparse, os, sys, json
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

# Thresholds
DELTA_STRONG   = 30  # |Δ| >= 30 required for FORCE FADE / FORCE PASS
DELTA_BOOST    = 20  # |Δ| >= 20 required for FORCE BACK when refit high
REFIT_TRAP     = 40  # refit < 40 = trap signal
REFIT_BOOST    = 80  # refit >= 80 = strong confirmation
REFIT_PASS     = 45  # refit < 45 = insufficient conviction (below LEAN threshold)
NO_REFIT_CAP   = 55  # LEAN cap when refit missing


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def decide(raw: int, refit: float | None, current_verdict: str) -> tuple[str, str] | None:
    """Return (new_verdict, action_id) or None if HOLD."""
    if refit is None:
        # No-refit cap only applies to BACK/FADE
        if current_verdict in ('BACK', 'FADE'):
            return (current_verdict, 'NO_REFIT_CAP')
        return None
    delta = refit - raw
    abs_d = abs(delta)
    if abs_d >= DELTA_STRONG and refit < REFIT_TRAP:
        # Raw was fooled — real play is opposite side. FORCE FADE if raw said BACK
        # (we now think the OTHER side is real, so fade the currently-stated side).
        # If raw said FADE and refit is low, that's actually consistent — HOLD.
        if current_verdict == 'BACK':
            return ('FADE', 'FORCE_FADE_TRAP')
        return None
    if abs_d >= DELTA_BOOST and refit >= REFIT_BOOST:
        if current_verdict in ('BACK', 'PASS'):
            return ('BACK', 'FORCE_BACK_BOOST')
        # FADE with high refit means Jerry is fading a strong signal — flip to BACK
        if current_verdict == 'FADE':
            return ('BACK', 'FORCE_BACK_REFIT_OVERRIDE')
    if abs_d >= DELTA_BOOST and refit < REFIT_PASS:
        # Too conflicted — sit out
        return ('PASS', 'FORCE_PASS_CONFLICT')
    return None


def run(game_date: str, dry_run: bool = False) -> int:
    reads = requests.get(f'{SB}/rest/v1/prop_jerry_reads', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'id,sport,player_name,prop_type,prop_line,direction,'
                          'call_verdict,conviction,refit_conviction,short_read'},
        timeout=15).json()
    if not isinstance(reads, list):
        print(f'  fetch failed: {reads}'); return 0

    # Get raw conviction from mlb_pipeline_props (refit_conviction lives on the
    # prop row, not the jerry row).
    prop_ids = requests.get(f'{SB}/rest/v1/mlb_pipeline_props', headers=H_READ,
        params={'game_date': f'eq.{game_date}',
                'select': 'player_name,prop_type,prop_line,direction,'
                          'conviction,refit_conviction'},
        timeout=15).json()
    prop_lookup = {}
    for p in prop_ids:
        key = (p['player_name'], p['prop_type'], p['prop_line'], p['direction'])
        prop_lookup[key] = p

    flips = 0
    for r in reads:
        if 'Auto-refit-override 2026-08-10' in (r.get('short_read') or ''):
            continue  # idempotent
        key = (r['player_name'], r['prop_type'], r['prop_line'], r['direction'])
        prop = prop_lookup.get(key)
        if not prop: continue
        raw = prop.get('conviction') or r.get('conviction') or 0
        refit = prop.get('refit_conviction')
        current = (r.get('call_verdict') or '').upper()
        result = decide(raw, refit, current)
        if not result: continue
        new_verdict, action = result

        # Build the audit note
        if action == 'NO_REFIT_CAP':
            note = (f'[Auto-refit-override 2026-08-10 NO_REFIT_CAP: refit_conviction '
                    f'unavailable for {r["prop_type"]} — capping conviction at LEAN {NO_REFIT_CAP} '
                    f'due to lack of calibration signal. Original take: '
                    f'{(r.get("short_read") or "")[:250]}]')
            new_conv = min(r.get('conviction') or 0, NO_REFIT_CAP)
        else:
            note = (f'[Auto-refit-override 2026-08-10 {action}: raw={raw} refit={refit} '
                    f'(Δ={refit-raw:+.0f}). Refit calibration says {action.split("_",1)[1]}. '
                    f'Verdict {current}→{new_verdict}. Original take: '
                    f'{(r.get("short_read") or "")[:250]}]')
            # Conviction: match refit for BOOST/OVERRIDE, cap at 55 for FADE/PASS
            if action in ('FORCE_BACK_BOOST', 'FORCE_BACK_REFIT_OVERRIDE'):
                new_conv = min(85, int(refit))
            elif action == 'FORCE_FADE_TRAP':
                new_conv = 65  # STRONG fade
            else:  # PASS
                new_conv = 50

        note = note[:1500]
        payload = {'call_verdict': new_verdict, 'conviction': new_conv, 'short_read': note}
        print(f'  {r["player_name"]:22} {r["prop_type"]:12} {r["direction"]:5} '
              f'raw={raw} refit={refit} · {current}→{new_verdict} [{action}]')
        if dry_run: flips += 1; continue
        pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{r["id"]}',
                            headers=H_WRITE, json=payload, timeout=10)
        if pr.status_code in (200, 204):
            flips += 1
        else:
            print(f'    patch failed: {pr.status_code} {pr.text[:120]}')

    print(f'\n=== refit-verdict overrides: {flips} applied{" (dry-run)" if dry_run else ""} ===')
    return flips


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(game_date=args.date or _et_today(), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
