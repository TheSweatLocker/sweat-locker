"""Seed signal_registry from docs/PLAYBOOK.md content (2026-08-15 pm).

Populates the registry with every signal documented in Playbook Part 1
with its real hit rate + tier + weight. One-shot seed; nightly rescores
happen via separate rescore_signal_registry.py (TODO).

CLI
  python seed_signal_registry.py            # seed / upsert all
  python seed_signal_registry.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# Signals derived from docs/PLAYBOOK.md Part 1
SIGNALS = [
    # MODELS
    {'signal_name': 'mc_ml_high_conf', 'category': 'model', 'sport': 'MLB', 'market_scope': 'ml',
     'hit_rate': 46.6, 'sample_n': 1121, 'edge_pp': -5.8, 'tier': 'ANTI_VALIDATED',
     'recommended_weight': 0, 'direction_hint': 'FOLLOW',
     'notes': 'MC alone below breakeven; cannot be primary basis for PRIME pick'},
    {'signal_name': 'mc_total_high_conf', 'category': 'model', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': 51.4, 'sample_n': 1042, 'edge_pp': -1.0, 'tier': 'UNVALIDATED',
     'recommended_weight': 0.3, 'direction_hint': 'FOLLOW'},
    {'signal_name': 'v4_projected_total', 'category': 'model', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': 51.4, 'sample_n': 1042, 'edge_pp': -1.0, 'tier': 'UNVALIDATED',
     'recommended_weight': 0.5, 'direction_hint': 'FOLLOW'},
    {'signal_name': 'panel_implied_total', 'category': 'model', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': None, 'sample_n': None, 'tier': 'DISCOVERY',
     'recommended_weight': 0.5, 'direction_hint': 'FOLLOW'},
    {'signal_name': 'jerry_pred_total', 'category': 'model', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': None, 'sample_n': None, 'tier': 'UNVALIDATED',
     'recommended_weight': 0.3, 'notes': 'Jerry historically noisy; do not use as primary basis'},
    {'signal_name': 'lens_consensus', 'category': 'model', 'sport': 'MLB', 'market_scope': 'multi',
     'hit_rate': None, 'sample_n': None, 'tier': 'VALIDATED',
     'recommended_weight': 1.0, 'direction_hint': 'FOLLOW',
     'notes': '5-of-6 lens agreement — consensus IS the signal'},

    # PITCHER
    {'signal_name': 'siera_gap_ml', 'category': 'pitcher', 'sport': 'MLB', 'market_scope': 'ml',
     'hit_rate': 42.0, 'sample_n': 172, 'edge_pp': -10.4, 'tier': 'ANTI_VALIDATED',
     'recommended_weight': 0, 'origin': 'BACKTEST_20260815',
     'notes': 'SIERA gap fav-arm ML: 37-48% across gap sizes; failed as directional signal'},
    {'signal_name': 'siera_ace_duel_under', 'category': 'pitcher', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': 64.7, 'sample_n': 17, 'edge_pp': 12.3, 'tier': 'DISCOVERY',
     'recommended_weight': 0.7, 'direction_hint': 'FOLLOW',
     'origin': 'BACKTEST_20260815',
     'notes': 'Both SIERA ≤ 3.00 → UNDER; small sample, promising'},

    # TEAM OFFENSE
    {'signal_name': 'ops_l14_dual_ice', 'category': 'team_offense', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': 64.7, 'sample_n': 34, 'edge_pp': 12.3, 'tier': 'DISCOVERY',
     'recommended_weight': 1.0, 'direction_hint': 'FOLLOW',
     'notes': 'Both teams OPS L14 ≤ .65 → UNDER'},
    {'signal_name': 'ops_l14_dual_cold', 'category': 'team_offense', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': 56.4, 'sample_n': 165, 'edge_pp': 4.0, 'tier': 'VALIDATED',
     'recommended_weight': 1.0, 'direction_hint': 'FOLLOW',
     'notes': 'Both teams OPS L14 ≤ .70 → UNDER — validated at largest n'},
    {'signal_name': 'ops_l14_dual_hot_regress', 'category': 'team_offense', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': 60.3, 'sample_n': 68, 'edge_pp': 7.9, 'tier': 'VALIDATED',
     'recommended_weight': 1.0, 'direction_hint': 'FADE',
     'notes': 'Both teams OPS L14 ≥ .78 → UNDER regression fade'},
    {'signal_name': 'ops_vs_opp_hand_loud', 'category': 'team_offense', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': 55.0, 'sample_n': 84, 'edge_pp': 2.6, 'tier': 'DISCOVERY',
     'recommended_weight': 0.5, 'direction_hint': 'FOLLOW',
     'notes': 'Avg OPS vs opp hand ≥ 0.75 → OVER'},
    {'signal_name': 'handedness_tight_edge', 'category': 'team_offense', 'sport': 'MLB', 'market_scope': 'ml',
     'hit_rate': 63.6, 'sample_n': 22, 'edge_pp': 11.2, 'tier': 'DISCOVERY',
     'recommended_weight': 0.5, 'direction_hint': 'FOLLOW',
     'notes': 'wRC+ delta ≥ 15 + edge ≥ 110 — small sample'},

    # BABIP (as R/G adjuster, not directional)
    {'signal_name': 'babip_regression_flag', 'category': 'team_offense', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': None, 'sample_n': 143, 'tier': 'VALIDATED',
     'recommended_weight': 0.3, 'direction_hint': 'ADJUST',
     'notes': 'R/G adjuster ±1.65-1.89 R, NOT directional pick. 94-96% is direction of regression, not OVER hit rate.'},

    # PUBLIC SPLITS
    {'signal_name': 'oc_sharp_div_ml', 'category': 'public_splits', 'sport': 'MLB', 'market_scope': 'ml',
     'hit_rate': 60.7, 'sample_n': 28, 'edge_pp': 8.3, 'tier': 'DISCOVERY',
     'recommended_weight': 0.7, 'direction_hint': 'FOLLOW',
     'notes': 'OC money%≥60 + bets%<55 sharp divergence'},
    {'signal_name': 'oc_ml_money_gte_80_fade', 'category': 'public_splits', 'sport': 'MLB', 'market_scope': 'ml',
     'hit_rate': 63.9, 'sample_n': 36, 'edge_pp': 11.5, 'tier': 'DISCOVERY',
     'recommended_weight': 1.0, 'direction_hint': 'FADE',
     'origin': 'BACKTEST_20260815',
     'notes': 'Loud sharp $ on ML → fade signal (backtested — contradicts follow assumption)'},
    {'signal_name': 'oc_ml_div_gte_20_fade', 'category': 'public_splits', 'sport': 'MLB', 'market_scope': 'ml',
     'hit_rate': 62.1, 'sample_n': 29, 'edge_pp': 9.7, 'tier': 'DISCOVERY',
     'recommended_weight': 1.0, 'direction_hint': 'FADE',
     'origin': 'BACKTEST_20260815'},
    {'signal_name': 'oc_rl_money_gte_70', 'category': 'public_splits', 'sport': 'MLB', 'market_scope': 'rl',
     'hit_rate': 62.0, 'sample_n': 14, 'edge_pp': 9.6, 'tier': 'DISCOVERY',
     'recommended_weight': 0.7, 'direction_hint': 'FOLLOW',
     'notes': 'Sharps ARE right on RL — follow money%≥70'},
    {'signal_name': 'cross_source_sharp_confirmed', 'category': 'public_splits', 'sport': '*', 'market_scope': 'multi',
     'hit_rate': None, 'sample_n': None, 'tier': 'DISCOVERY',
     'recommended_weight': 1.0, 'direction_hint': 'FOLLOW',
     'notes': 'OC + FR + Cleatz all agree on sharp side — highest confidence gate; needs backtest'},

    # REFIT (props only)
    {'signal_name': 'refit_band_75_84', 'category': 'refit', 'sport': 'MLB', 'market_scope': 'prop',
     'hit_rate': 62.7, 'sample_n': 59, 'edge_pp': 10.3, 'tier': 'VALIDATED',
     'recommended_weight': 1.5, 'direction_hint': 'FOLLOW',
     'origin': 'BACKTEST_20260815',
     'notes': 'BEST refit band — beats 95-100 (55.2%); calibration NOT monotonic'},
    {'signal_name': 'refit_band_95_100', 'category': 'refit', 'sport': 'MLB', 'market_scope': 'prop',
     'hit_rate': 55.2, 'sample_n': 67, 'edge_pp': 2.8, 'tier': 'DISCOVERY',
     'recommended_weight': 1.0, 'direction_hint': 'FOLLOW',
     'notes': 'Refit=100 is NOT "guaranteed" — 57.1% n=63 at exactly 100'},
    {'signal_name': 'refit_band_65_74', 'category': 'refit', 'sport': 'MLB', 'market_scope': 'prop',
     'hit_rate': 49.4, 'sample_n': 85, 'edge_pp': -3.0, 'tier': 'ANTI_VALIDATED',
     'recommended_weight': 0, 'direction_hint': 'FOLLOW',
     'notes': 'Worst band — coinflip below breakeven'},
    {'signal_name': 'refit_band_35_44_fade', 'category': 'refit', 'sport': 'MLB', 'market_scope': 'prop',
     'hit_rate': 62.3, 'sample_n': 53, 'edge_pp': 9.9, 'tier': 'DISCOVERY',
     'recommended_weight': 1.0, 'direction_hint': 'FADE',
     'notes': 'Fade zone — betting OPPOSITE side hits 62%'},

    # SITUATIONAL
    {'signal_name': 'nrfi_temp_le_45', 'category': 'situational', 'sport': 'MLB', 'market_scope': 'total',
     'hit_rate': 79.2, 'sample_n': 60, 'edge_pp': 26.8, 'tier': 'VALIDATED',
     'recommended_weight': 1.0, 'direction_hint': 'FOLLOW',
     'notes': 'Weather ≤45°F → NRFI hits 79%'},
    {'signal_name': 'long_rest_home_ats', 'category': 'situational', 'sport': 'MLB', 'market_scope': 'spread',
     'hit_rate': 57.0, 'sample_n': 200, 'edge_pp': 4.6, 'tier': 'VALIDATED',
     'recommended_weight': 0.5, 'direction_hint': 'FOLLOW'},

    # RULE FIRE STATS
    {'signal_name': 'refit_override_unproven_band', 'category': 'refit', 'sport': 'MLB', 'market_scope': 'prop',
     'hit_rate': 62.1, 'sample_n': 29, 'edge_pp': 9.7, 'tier': 'DISCOVERY',
     'recommended_weight': 1.0,
     'notes': 'REFIT_BAND_UNPROVEN override — from rule_fire_stats 30d'},

    # TRAPS (anti-validated)
    {'signal_name': 'heavy_fav_ml_neg200_cover', 'category': 'cohort', 'sport': 'MLB', 'market_scope': 'spread',
     'hit_rate': 29.0, 'sample_n': None, 'edge_pp': -23.4, 'tier': 'ANTI_VALIDATED',
     'recommended_weight': 0, 'direction_hint': 'FADE',
     'notes': 'Heavy fav -200+ covers -1.5 only 29% — juice trap'},
]


def run(dry_run: bool = False):
    print(f'=== signal_registry seed · {len(SIGNALS)} signals ===')
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for s in SIGNALS:
        payload = {**s, 'last_computed_at': now_iso, 'updated_at': now_iso}
        if 'origin' not in payload:
            payload['origin'] = 'PLAYBOOK_SEED'
        payloads.append(payload)

    if dry_run:
        for p in payloads[:5]:
            print(f'  [DRY] {p["signal_name"]:<32} · {p["tier"]:<15} · hit={p.get("hit_rate")}% n={p.get("sample_n")} · w={p["recommended_weight"]}')
        print(f'  ...+{len(payloads)-5} more')
        return

    written = 0
    for i in range(0, len(payloads), 100):
        chunk = payloads[i:i+100]
        pr = requests.post(
            f'{SB}/rest/v1/signal_registry?on_conflict=signal_name,sport,market_scope',
            headers=H_WRITE, json=chunk, timeout=30)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:150]}')
    print(f'  ✓ wrote {written} signals')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
