"""Fade Jerry PASS on NFL props (2026-09-14 · Week 1 backtest signal).

Andy: "Yes on fade jerry pass."

Backtest agent Week 1 finding (n=110 Jerry PASS-verdict props graded):
  PASS verdict → 37-73 = 33.6% hit
  → Inverse (fade the PASS = take opposite direction) = 66.4% hit
  → +9pp above -110 breakeven, statistically real.

This script materializes the fade signal:

  1. Query prop_jerry_reads WHERE sport=NFL, call_verdict=PASS, game_date >= today
  2. For each PASS row, locate the opposite-direction nfl_pipeline_props row
     (same game + player + prop-family + flipped direction).
  3. Upgrade the opposite direction:
       nfl_pipeline_props.tier: SKIP/LIGHT → LEAN (only if not already >=LEAN)
                        .conviction: 66 (inverse hit rate)
                        .signals.fade_jerry_pass = True (audit)
       prop_jerry_reads.call_verdict: PASS → BACK
                       .conviction: 66
                       .short_read: prepends "🔄 FADE-JERRY-PASS (Wk 1 backtest 66.4% hit): "
  4. Ships as SPORT_FADE badge downstream so users see the reversal.

Idempotent — signals.fade_jerry_pass guard prevents double-processing.

Runs after generate_prop_jerry_synthesis.py + nfl_prop_signal_discipline.py
(needs both the PASS verdicts + the discipline tier reassignments to
exist before we flip).

CLI:
  python nfl_prop_fade_jerry_pass.py           # today+8d
  python nfl_prop_fade_jerry_pass.py --days 3
  python nfl_prop_fade_jerry_pass.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
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

SB = os.environ['SUPABASE_URL']
KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_R = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_W = {**H_R, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Backtest inverse hit rate — recalibrate weekly as more grades land.
FADE_HIT_PCT = 66  # 100 * (1 - 0.336)
FADE_TIER = 'LEAN'
FADE_NOTE_PREFIX = '🔄 FADE-JERRY-PASS (Wk 1 backtest 66.4% hit): '


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def _opposite_dir(d: str) -> str:
    d = str(d).lower().strip()
    if d == 'over': return 'under'
    if d == 'under': return 'over'
    return d


def _opposite_prop_type(pt: str) -> str:
    """Return the opposite-direction prop_type. Our naming convention:
    every prop_type embeds direction as suffix (`ha_over`/`ha_under`,
    `pass_attempts_over`/`pass_attempts_under`). If suffix is present,
    flip it; otherwise return as-is (caller handles fallback lookup)."""
    if not pt: return pt
    if pt.endswith('_over'): return pt[:-5] + '_under'
    if pt.endswith('_under'): return pt[:-6] + '_over'
    return pt


def run(days: int, dry_run: bool = False) -> None:
    today = _et_today()
    horizon = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    print(f'=== nfl_prop_fade_jerry_pass · {today}→{horizon} · dry={dry_run} ===')

    # 1. PASS verdicts on upcoming NFL
    r = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
                     params={'sport': 'eq.NFL',
                             'call_verdict': 'eq.PASS',
                             'and': f'(game_date.gte.{today},game_date.lte.{horizon})',
                             'select': 'id,game_id,player_name,prop_type,direction,'
                                       'conviction,short_read,game_date'},
                     headers={**H_R, 'Range-Unit': 'items', 'Range': '0-999'},
                     timeout=30)
    pass_reads = r.json() if r.status_code == 200 else []
    if not isinstance(pass_reads, list):
        print(f'  PASS query err: {pass_reads}'); return
    print(f'  {len(pass_reads)} PASS verdicts on upcoming NFL props')

    flipped_props = 0
    flipped_reads = 0
    skipped_already_processed = 0
    skipped_no_opposite = 0

    for pr in pass_reads:
        if not isinstance(pr, dict): continue
        gid = pr.get('game_id')
        pname = pr.get('player_name')
        pt = pr.get('prop_type')
        dir_ = pr.get('direction')
        if not (gid and pname and pt and dir_): continue

        opp_dir = _opposite_dir(dir_)
        opp_pt = _opposite_prop_type(pt)

        # 2. Find opposite-direction props row (nfl_pipeline_props)
        pp_r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props',
                            params={'game_id': f'eq.{gid}',
                                    'player_name': f'eq.{pname}',
                                    'prop_type': f'eq.{opp_pt}',
                                    'direction': f'eq.{opp_dir}',
                                    'select': 'id,tier,conviction,signals'},
                            headers=H_R, timeout=15)
        opp_props = pp_r.json() if pp_r.status_code == 200 else []
        if not opp_props:
            skipped_no_opposite += 1
            continue
        opp_p = opp_props[0]
        sigs = opp_p.get('signals') or {}
        if not isinstance(sigs, dict): sigs = {}
        if sigs.get('fade_jerry_pass'):
            skipped_already_processed += 1
            continue

        # 3. Upgrade opposite prop row (only if tier is SKIP or LIGHT — don't
        # downgrade a real STRONG pick)
        cur_tier = str(opp_p.get('tier') or '').upper()
        if cur_tier not in ('SKIP', 'LIGHT', ''):
            # tier already better than LEAN — just tag the audit flag but
            # don't rewrite tier.
            new_props_patch = {'signals': {**sigs, 'fade_jerry_pass': True,
                                            'fade_jerry_pass_orig_tier': cur_tier}}
        else:
            new_props_patch = {
                'tier': FADE_TIER,
                'conviction': FADE_HIT_PCT,
                'signals': {**sigs, 'fade_jerry_pass': True,
                            'fade_jerry_pass_orig_tier': cur_tier},
            }

        # 4. Find opposite direction's prop_jerry_reads row (may or may not
        # exist — write a fresh BACK-verdict short_read either way).
        pj_r = requests.get(f'{SB}/rest/v1/prop_jerry_reads',
                            params={'game_id': f'eq.{gid}',
                                    'player_name': f'eq.{pname}',
                                    'prop_type': f'eq.{opp_pt}',
                                    'direction': f'eq.{opp_dir}',
                                    'select': 'id,short_read,call_verdict'},
                            headers=H_R, timeout=15)
        opp_pj = pj_r.json() if pj_r.status_code == 200 else []

        _display_tier = cur_tier if cur_tier not in ('SKIP','LIGHT','') else FADE_TIER
        _tier_action = 'audit-tag' if cur_tier not in ('SKIP','LIGHT','') else f'{cur_tier}->{FADE_TIER}'
        if dry_run:
            print(f'  [DRY] {pname:22s}  {dir_} -> {opp_dir}  {_tier_action}  '
                  f'jerry_row_exists={bool(opp_pj)}')
            flipped_props += 1
            continue

        # PATCH the opposite-direction props row
        wr = requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{opp_p["id"]}',
                            headers=H_W, json=new_props_patch, timeout=10)
        if wr.status_code in (200, 204): flipped_props += 1

        # PATCH the opposite-direction prop_jerry_reads row (if one exists)
        # to BACK with the fade note. If it doesn't exist, we skip creating
        # one — the app reads from nfl_pipeline_props' surface primarily
        # and the tag on that row already routes the fade through.
        if opp_pj and isinstance(opp_pj, list):
            row = opp_pj[0]
            existing_read = str(row.get('short_read') or '')
            if not existing_read.startswith(FADE_NOTE_PREFIX):
                new_read = FADE_NOTE_PREFIX + (existing_read or 'Take the other side.')
                pj_patch = {'call_verdict': 'BACK', 'conviction': FADE_HIT_PCT,
                            'short_read': new_read[:2000]}  # column length safety
                pw = requests.patch(f'{SB}/rest/v1/prop_jerry_reads?id=eq.{row["id"]}',
                                     headers=H_W, json=pj_patch, timeout=10)
                if pw.status_code in (200, 204): flipped_reads += 1

    print(f'  ✓ props patched: {flipped_props}')
    print(f'  ✓ jerry reads flipped to BACK: {flipped_reads}')
    print(f'  skipped (already fade-processed): {skipped_already_processed}')
    print(f'  skipped (no opposite-direction row exists): {skipped_no_opposite}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=8)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
