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
# 2026-08-11 tighten (per Aug 10 audit — Prop Jerry FADE went 2-7 · 22%,
# suggesting force-fade at refit<40 was over-firing). Tightening the trap
# threshold to <30 means we FORCE FADE only on truly hammered signals,
# not gray-zone dissonance. Force PASS threshold stays at 45 so gray-zone
# cases still get sit-out treatment, they just don't get flipped to the
# opposite side aggressively.
DELTA_STRONG   = 35  # |Δ| >= 35 required for FORCE FADE (was 30)
DELTA_BOOST    = 20  # |Δ| >= 20 required for FORCE BACK when refit high
REFIT_TRAP     = 30  # refit < 30 = trap signal (was 40)
REFIT_BOOST    = 80  # refit >= 80 = strong confirmation
REFIT_PASS     = 45  # refit < 45 = insufficient conviction (unchanged)
NO_REFIT_CAP   = 55  # LEAN cap when refit missing


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def decide(raw: int, refit: float | None, current_verdict: str,
           prop_type: str | None = None, direction: str | None = None,
           sample_health: dict | None = None) -> tuple[str, str] | None:
    """Return (new_verdict, action_id) or None if HOLD.

    2026-08-11: sample_health gate added. When forcing a BACK on refit
    boost, we now check that the refit-100 band for THIS prop_type has
    hit >=55% over its recent history (n>=30). If not, force BACK
    downgrades to LEAN cap only, no verdict change. Prevents
    over-confident BACKs when the calibration model itself has been
    misfiring on this prop_type recently.

    sample_health dict shape (from compute_refit_band_health):
        {(prop_type, direction, '80-100'): {n: N, hit_pct: float}}
    """
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
        # Sample-size gate — only force BACK when the refit-100 band for
        # this prop_type has been healthy recently. Otherwise cap at LEAN.
        if sample_health and prop_type and direction:
            band = sample_health.get((prop_type, direction, '80-100'))
            if band and band['n'] >= 30 and band['hit_pct'] < 55:
                # Refit band under-performing recently — downgrade instead of force
                return ('LEAN_CAP', 'REFIT_BAND_UNHEALTHY')
        if current_verdict in ('BACK', 'PASS'):
            return ('BACK', 'FORCE_BACK_BOOST')
        # FADE with high refit means Jerry is fading a strong signal — flip to BACK
        if current_verdict == 'FADE':
            return ('BACK', 'FORCE_BACK_REFIT_OVERRIDE')
    if abs_d >= DELTA_BOOST and refit < REFIT_PASS:
        # Too conflicted — sit out
        return ('PASS', 'FORCE_PASS_CONFLICT')
    return None


def compute_refit_band_health(days: int = 30) -> dict:
    """Return refit-band hit rates over the last N days per (prop_type,
    direction, band). Used by the sample-size gate to detect when a
    refit band has been misfiring recently.

    Bands: '80-100' (BACK zone), '60-79', '40-59', '20-39', '0-19'.
    """
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = []
    offset = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/mlb_pipeline_props',
            headers={**H_READ, 'Range': f'{offset}-{offset+999}',
                     'Range-Unit': 'items'},
            params={'select': 'prop_type,direction,refit_conviction,result',
                    'result': 'in.(Win,Loss)',
                    'game_date': f'gte.{since}',
                    'refit_conviction': 'not.is.null'},
            timeout=30)
        chunk = r.json() if r.status_code in (200, 206) else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        offset += 1000
    from collections import defaultdict
    acc = defaultdict(lambda: {'w': 0, 'l': 0})
    for row in rows:
        rf = row.get('refit_conviction')
        if rf is None: continue
        band = ('80-100' if rf >= 80 else '60-79' if rf >= 60
                else '40-59' if rf >= 40 else '20-39' if rf >= 20 else '0-19')
        key = (row['prop_type'], row['direction'], band)
        if row['result'] == 'Win': acc[key]['w'] += 1
        elif row['result'] == 'Loss': acc[key]['l'] += 1
    health = {}
    for key, v in acc.items():
        total = v['w'] + v['l']
        if total == 0: continue
        health[key] = {'n': total, 'hit_pct': round(100 * v['w'] / total, 1)}
    return health


def run(game_date: str, dry_run: bool = False) -> int:
    # 2026-08-11: precompute refit band health for the sample-size gate.
    # Skip on failure — decide() gracefully treats missing health as no-gate.
    try:
        sample_health = compute_refit_band_health(days=30)
        healthy_bands = sum(1 for k, v in sample_health.items()
                            if k[2] == '80-100' and v['n'] >= 30 and v['hit_pct'] >= 55)
        unhealthy_bands = sum(1 for k, v in sample_health.items()
                              if k[2] == '80-100' and v['n'] >= 30 and v['hit_pct'] < 55)
        print(f'  refit-100 band health (30d): {healthy_bands} healthy · '
              f'{unhealthy_bands} unhealthy (n>=30 threshold)')
    except Exception as e:
        print(f'  refit band health unavailable: {e}'); sample_health = None

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
        result = decide(raw, refit, current,
                        prop_type=r.get('prop_type'),
                        direction=r.get('direction'),
                        sample_health=sample_health)
        if not result: continue
        new_verdict, action = result

        # 2026-08-11: REFIT_BAND_UNHEALTHY = downgrade to LEAN cap, no
        # verdict change. Refit band has been misfiring recently so we
        # don't fully trust the boost signal; just cap conviction lower.
        if action == 'REFIT_BAND_UNHEALTHY':
            note = (f'[Auto-refit-override 2026-08-11 REFIT_BAND_UNHEALTHY: '
                    f'refit={refit} but 80-100 band for {r["prop_type"]}/'
                    f'{r["direction"]} has been unhealthy last 30d — '
                    f'holding verdict at {current}, capping conviction LEAN. '
                    f'Original take: {(r.get("short_read") or "")[:250]}]')
            payload = {'conviction': min(r.get('conviction') or 55, 55),
                       'short_read': note[:1500]}
            print(f'  {r["player_name"]:22} {r["prop_type"]:12} {r["direction"]:5} '
                  f'raw={raw} refit={refit} · {current} conviction capped LEAN [BAND_UNHEALTHY]')
            if dry_run: flips += 1; continue
            pr = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{r["id"]}',
                                headers=H_WRITE, json=payload, timeout=10)
            if pr.status_code in (200, 204): flips += 1
            continue

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
