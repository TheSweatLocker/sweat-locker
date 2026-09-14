"""Seed refit_conviction on NFL props (2026-09-14 · Week 1 backtest fix).

Andy: "Jerry PASSes every NFL prop." Backtest agent traced the cause:
`generate_prop_jerry_synthesis` renders `{REFIT_CONVICTION}` from
`prop.refit_conviction`. On MLB props, `apply_prop_refit.py` populates
this per-prop via a logistic-fit weights model. On NFL there's no
equivalent writer — every NFL prop hits the LLM prompt with
"REFIT_CONVICTION: n/a", the LLM reads that as "no model consensus"
and defaults to PASS. Week 1 result: 158 PASSes on 446 NFL Jerry reads
(35% of the surface).

This script is the INTERIM bridge until a real `nfl_prop_logreg_predict.py`
ships. It maps each prop's tier + calibration state to a refit_conviction
value derived from the Week 1 tier hit rates measured in the backtest:

  Tier    Week 1 hit%   → refit_conviction
  PRIME    50.0%           50  (coin flip; capped to STRONG downstream)
  STRONG   62.7%           63  (real edge)
  LEAN     56.8%           57  (real edge)
  LIGHT    33.0%           33  (fade)
  SKIP     42.2%           42

Refit_conviction lives alongside legacy `conviction` (which drives tier),
so this doesn't change the tier assignment — only unblocks Jerry's
BACK/FADE emission by giving the LLM a number to reason against.

Runs after `nfl_generate_props.py` + `nfl_prop_signal_discipline.py`.
Idempotent — safe to rerun; overwrites existing values.

CLI:
  python nfl_prop_refit_seed.py                # today+8d
  python nfl_prop_refit_seed.py --days 3
  python nfl_prop_refit_seed.py --dry-run
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

# Week 1 tier hit rates (from 2026-09-14 backtest — 439 graded NFL props).
# Update after each week's grading cycle. Values are percentages 0-100.
TIER_REFIT_MAP: dict[str, int] = {
    'PRIME':  50,   # 50% actual — but currently capped to STRONG by discipline
    'STRONG': 63,   # 62.7% → 63
    'LEAN':   57,   # 56.8% → 57
    'LIGHT':  33,   # 33.0% (fade signal)
    'SKIP':   42,   # 42.2%
    'COVERAGE': 40, # not backtested, conservative default
    'PASS':   30,   # explicit no-play — very low
}


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def run(days: int, dry_run: bool = False) -> None:
    today = _et_today()
    horizon = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    print(f'=== nfl_prop_refit_seed · window {today}→{horizon} · dry={dry_run} ===')

    r = requests.get(f'{SB}/rest/v1/nfl_pipeline_props',
                     params={'and': f'(game_date.gte.{today},game_date.lte.{horizon})',
                             'select': 'id,tier,conviction,refit_conviction'},
                     headers={**H_R, 'Range-Unit': 'items', 'Range': '0-4999'},
                     timeout=30)
    rows = r.json() if r.status_code == 200 else []
    if not isinstance(rows, list):
        print(f'  fetch failed: {rows}')
        return
    print(f'  {len(rows)} props in window')
    from collections import Counter
    tiers = Counter(); patched = 0
    for p in rows:
        tier = (p.get('tier') or '').upper()
        tiers[tier] += 1
        target = TIER_REFIT_MAP.get(tier)
        if target is None: continue
        # Slight jitter from conviction so within-tier props aren't all
        # identical — LLM prompts can distinguish top-of-tier from
        # bottom-of-tier. ±5pt band, clamped 0-100.
        conv = p.get('conviction') or 0
        try: conv = int(conv)
        except (TypeError, ValueError): conv = 0
        # Higher conviction within tier → refit closer to tier ceiling.
        # Base band [target-5, target+5]; slide with conviction.
        # Simple linear: refit = target + (conv - tier_conv_mid) * scale.
        # Use a light scale so refit still reflects tier much more than conv.
        tier_conv_mid = {'PRIME': 88, 'STRONG': 75, 'LEAN': 59,
                         'LIGHT': 47, 'SKIP': 25, 'COVERAGE': 40,
                         'PASS': 20}.get(tier, 50)
        adjust = (conv - tier_conv_mid) * 0.15   # gentle
        refit = int(round(max(0, min(100, target + adjust))))
        if p.get('refit_conviction') == refit: continue
        if dry_run:
            patched += 1
            continue
        pr = requests.patch(f'{SB}/rest/v1/nfl_pipeline_props?id=eq.{p["id"]}',
                            headers=H_W, json={'refit_conviction': refit},
                            timeout=10)
        if pr.status_code in (200, 204): patched += 1
    print(f'  tier distribution: {dict(tiers)}')
    print(f'  {"[DRY] would patch" if dry_run else "patched"}: {patched} rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=8)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
