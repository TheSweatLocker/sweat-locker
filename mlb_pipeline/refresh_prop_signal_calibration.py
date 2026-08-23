"""Bridge: signal_contribution_tracker → signal_registry (2026-08-23).

USER INSIGHT
------------
"If legacy conviction is working, inject those processes into playbook."

Legacy uses hand-tuned integer weights per signal. Playbook uses
hit-rate-weighted `edge_weight()`. Both systems READ the same fired
signals — the difference is only in how weights get assigned.

If we write legacy's PROVEN signal keys into `signal_registry` with
empirical hit rates from graded prop history, playbook automatically
uses those calibrated weights via `_resolve_weight()`. No code change
in playbook needed. The math holds.

HOW IT WORKS
------------
1. Run signal_contribution_tracker analysis on graded prop history
   (30d rolling default).
2. For each signal_key with n >= MIN_SAMPLE, compute hit_rate + edge_pp.
3. Assign tier per historical performance:
     VALIDATED       hit_rate >= 0.55 AND n >= 50   ← proven edge
     DISCOVERY       hit_rate >= 0.55 AND n <  50   ← promising, small n
     UNVALIDATED     hit_rate >= 0.50 AND n >= 50   ← neutral evidence
     ANTI_VALIDATED  hit_rate <= 0.47 AND n >= 50   ← proven anti-signal
     null            not enough evidence yet
4. Upsert into `signal_registry` keyed on (signal_name, sport, market_scope).
5. On next playbook cron, `_resolve_weight()` picks up the calibrated
   weights, weighting proven signals appropriately.

Sport-universal via SPORT_REG.

Usage
-----
    python refresh_prop_signal_calibration.py                  # MLB 30d
    python refresh_prop_signal_calibration.py --sport MLB --days 90
    python refresh_prop_signal_calibration.py --dry-run
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

MIN_SAMPLE = 20        # ignore signals with fewer than this
BREAKEVEN = 0.524      # -110 breakeven


def _tier_for(hit_rate: float, n: int) -> str | None:
    """Classify signal into registry tier by empirical evidence."""
    if hit_rate is None or n < MIN_SAMPLE:
        return None
    if hit_rate <= 0.47 and n >= 50:
        return 'ANTI_VALIDATED'
    if hit_rate >= 0.55 and n >= 50:
        return 'VALIDATED'
    if hit_rate >= 0.55 and n >= MIN_SAMPLE:
        return 'DISCOVERY'
    if hit_rate >= 0.50 and n >= 50:
        return 'UNVALIDATED'
    return 'UNVALIDATED'


def run(sport: str = 'MLB', days: int = 30, dry_run: bool = False) -> int:
    # Import tracker's analysis function
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from signal_contribution_tracker import analyze

    print(f'=== refresh_prop_signal_calibration · {sport} · last {days}d ===')
    rep = analyze(sport, days, family_filter=None, min_sample=MIN_SAMPLE)
    if not rep.get('signals'):
        print('  no signals to calibrate — abort')
        return 0
    print(f'  analyzing {rep["total_graded"]} graded props · {len(rep["signals"])} signals qualify')

    now_iso = datetime.now(timezone.utc).isoformat()
    written = skipped = 0
    payloads = []
    for s in rep['signals']:
        hit_rate = s['hit_pct'] / 100.0
        n = s['w'] + s['l']  # exclude pushes from denominator
        tier = _tier_for(hit_rate, n)
        edge_pp = round((hit_rate - BREAKEVEN) * 100, 2)

        # Per-family entries — one row per (signal, family) combo so
        # playbook's family-specific market_scope lookup finds them.
        for family, fd in (s.get('by_family') or {}).items():
            fhr = fd['hit_pct'] / 100.0
            fn = fd['w'] + fd['l']
            ftier = _tier_for(fhr, fn)
            if ftier is None:
                skipped += 1
                continue
            payloads.append({
                'signal_name': s['signal'],
                'sport': sport,
                'market_scope': family,     # e.g. 'ks_over', 'ha_under'
                'category': 'prop',
                'hit_rate': round(fhr * 100, 2),
                'sample_n': fn,
                'edge_pp': round((fhr - BREAKEVEN) * 100, 2),
                'tier': ftier,
                'recommended_weight': round(max(0.0, (fhr - BREAKEVEN) / 0.12), 3),
                'origin': 'auto_calibration_from_graded',
                'last_computed_at': now_iso,
                'notes': f'auto-calibrated 30d rolling window · from signal_contribution_tracker',
                'updated_at': now_iso,
            })

        # Also write an ALL-family aggregate row so lookups without family
        # scope get a reasonable weight (fallback path in _resolve_weight)
        if tier is not None:
            payloads.append({
                'signal_name': s['signal'],
                'sport': sport,
                'market_scope': 'prop',      # all-prop aggregate
                'category': 'prop',
                'hit_rate': round(hit_rate * 100, 2),
                'sample_n': n,
                'edge_pp': edge_pp,
                'tier': tier,
                'recommended_weight': round(max(0.0, (hit_rate - BREAKEVEN) / 0.12), 3),
                'origin': 'auto_calibration_from_graded',
                'last_computed_at': now_iso,
                'notes': f'auto-calibrated 30d rolling · aggregate across all prop families',
                'updated_at': now_iso,
            })

    # Union keys per feedback_postgrest_batch_normalize_keys memory
    all_keys = set()
    for p in payloads: all_keys.update(p.keys())
    normalized = [{k: p.get(k) for k in all_keys} for p in payloads]

    print(f'  {len(normalized)} calibration rows to write')
    print(f'  breakdown:')
    from collections import Counter
    tc = Counter(p.get('tier') for p in normalized)
    for t in ('VALIDATED', 'DISCOVERY', 'UNVALIDATED', 'ANTI_VALIDATED'):
        print(f'    {t:<20} {tc.get(t, 0)}')

    if dry_run:
        print('\n  [DRY-RUN] no writes. Top 10 rows preview:')
        for p in sorted(normalized, key=lambda x: -(x.get('hit_rate') or 0))[:10]:
            print(f'    {p["signal_name"]:<25} {p["market_scope"]:<15} {p["hit_rate"]}% n={p["sample_n"]} [{p["tier"]}]')
        return 0

    # Batch upsert to signal_registry
    CHUNK = 100
    for i in range(0, len(normalized), CHUNK):
        chunk = normalized[i:i+CHUNK]
        pr = requests.post(
            f'{SB}/rest/v1/signal_registry?on_conflict=signal_name,sport,market_scope',
            headers=H_WRITE, json=chunk, timeout=20)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')

    print(f'\n  ✅ wrote {written} calibration rows to signal_registry')
    print(f'     playbook + game ensemble will pick these up on next _load_registry() call')
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB')
    p.add_argument('--days', type=int, default=30)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(args.sport, args.days, args.dry_run)


if __name__ == '__main__':
    main()
