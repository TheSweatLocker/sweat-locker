"""Prop playbook phase 1: audit prop signals + write tier to registry.

Prop pipeline already writes a `signals` JSON to each mlb_pipeline_props
row — a dict of {signal_name: value/contribution} that fired for that
prop. Different from game signals which live in signal_sources rows.

This script:
  1. Reads all resolved MLB props (result != null) over N days
  2. For each unique signal name that has appeared in any prop's `signals`
     JSON, computes hit rate + sample size across the props where it fired
  3. Writes tier (VALIDATED/DISCOVERY/UNVALIDATED/ANTI_VALIDATED) to
     signal_registry with market_scope='prop' + category='prop_signal'
  4. Optionally: writes tier per (signal_name, prop_type) pair so a
     signal that works for K props but not hits props gets nuanced

Then apply_refit_verdict_override.py can:
  - Read signal_registry for the prop's `signals` keys
  - If PRIME prop's dominant signal is ANTI_VALIDATED, demote to STRONG
  - If ALL top signals are UNVALIDATED, cap at LEAN

This mirrors the game ensemble's Playbook wiring without needing a full
parallel scorer — the mechanical prop pipeline already picks the side,
we just gate its tier assignment against proven signal evidence.

CLI:
  python audit_prop_signals.py --days 90
  python audit_prop_signals.py --days 60 --by-prop-type
  python audit_prop_signals.py --days 60 --dry-run
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

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


# Signals in the JSON that are internal artifacts, not predictive features
INTERNAL_SIGNAL_PREFIXES = ('_', 'display_', 'src_', 'lineup_')


def is_prediction_signal(name: str) -> bool:
    """Filter out audit-tag / display-only fields from the signals JSON."""
    if not name or not isinstance(name, str):
        return False
    if any(name.startswith(p) for p in INTERNAL_SIGNAL_PREFIXES):
        return False
    return True


def fetch_resolved_props(days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for off in range(0, 30000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/mlb_pipeline_props'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&result=not.is.null'
            f'&select=prop_type,direction,tier,result,signals,book_line'
            f'&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        rows += chunk
        if len(chunk) < 1000: break
    return rows


def audit(props: list[dict], by_prop_type: bool = False) -> list[dict]:
    """Compute hit rate per signal (or per signal × prop_type when
    by_prop_type is True). Returns list of registry-shaped dicts."""
    # (key) → [wins, losses, pushes]
    buckets = defaultdict(lambda: [0, 0, 0])

    for p in props:
        sig = p.get('signals') or {}
        if isinstance(sig, str):
            try: sig = json.loads(sig)
            except: sig = {}
        if not isinstance(sig, dict): continue

        result = str(p.get('result') or '').strip().upper()
        if result in ('W', 'WIN'): bucket_idx = 0
        elif result in ('L', 'LOSS'): bucket_idx = 1
        elif result in ('P', 'PUSH'): bucket_idx = 2
        else: continue

        prop_type = p.get('prop_type') or 'unknown'
        for name, val in sig.items():
            if not is_prediction_signal(name): continue
            # Boolean flags fire when truthy; numeric contributions fire when >0
            fires = False
            if isinstance(val, bool):
                fires = val
            elif isinstance(val, (int, float)):
                fires = val > 0
            elif isinstance(val, str) and val:
                fires = True
            if not fires: continue

            key = (name, prop_type) if by_prop_type else (name, 'all')
            buckets[key][bucket_idx] += 1

    # Convert to registry rows
    rows = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for (name, prop_type), (w, l, p) in buckets.items():
        n_dec = w + l
        if n_dec == 0: continue
        hit_rate = round(100 * w / n_dec, 1)
        edge_pp = round(hit_rate - 52.4, 1)

        if n_dec < 15: tier = 'UNVALIDATED'
        elif n_dec >= 25 and hit_rate <= 48.0: tier = 'ANTI_VALIDATED'
        elif n_dec >= 50 and hit_rate >= 55.0: tier = 'VALIDATED'
        elif hit_rate >= 52.4: tier = 'DISCOVERY'
        else: tier = 'UNVALIDATED'

        weight = {'VALIDATED': 1.0, 'DISCOVERY': 0.5,
                  'UNVALIDATED': 0.3, 'ANTI_VALIDATED': 0.0}[tier]

        signal_name = f'prop:{name}:{prop_type}' if prop_type != 'all' else f'prop:{name}'
        rows.append({
            'signal_name': signal_name, 'sport': 'MLB', 'market_scope': 'prop',
            'category': 'prop_signal',
            'description': f'Prop signal `{name}`{" scoped to " + prop_type if prop_type != "all" else ""}',
            'hit_rate': hit_rate, 'sample_n': n_dec, 'edge_pp': edge_pp,
            'tier': tier, 'recommended_weight': weight,
            'direction_hint': 'FADE' if tier == 'ANTI_VALIDATED' else 'FOLLOW',
            'origin': f'PROP_AUDIT_{date.today().isoformat()}',
            'last_computed_at': now_iso, 'updated_at': now_iso,
        })
    return rows


def write(rows: list[dict], dry_run: bool = False):
    if not rows:
        print('  no rows to write'); return
    # Union keys
    all_keys = set()
    for r in rows: all_keys.update(r.keys())
    normalized = [{k: r.get(k) for k in all_keys} for r in rows]

    written = 0
    for i in range(0, len(normalized), 100):
        chunk = normalized[i:i+100]
        if dry_run:
            for r in chunk:
                print(f'  [DRY] {r["signal_name"]:<50} HR={r["hit_rate"]}%  n={r["sample_n"]}  tier={r["tier"]}')
            written += len(chunk)
            continue
        pr = requests.post(
            f'{SB}/rest/v1/signal_registry?on_conflict=signal_name,sport,market_scope',
            headers=H_WRITE, json=chunk, timeout=15)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')
    print(f'  ✓ wrote {written} prop_signal registry rows{" (dry-run)" if dry_run else ""}')


def run(days: int = 90, by_prop_type: bool = False, dry_run: bool = False):
    print(f'=== audit prop signals · MLB · last {days} days ===')
    props = fetch_resolved_props(days)
    print(f'  {len(props)} resolved props')
    rows = audit(props, by_prop_type=by_prop_type)
    print(f'  {len(rows)} unique signals aggregated ({("per prop_type" if by_prop_type else "global")})\n')

    # Summary counts
    tier_counts = defaultdict(int)
    for r in rows: tier_counts[r['tier']] += 1
    for tier in ('VALIDATED', 'DISCOVERY', 'UNVALIDATED', 'ANTI_VALIDATED'):
        c = tier_counts.get(tier, 0)
        if c: print(f'  {tier:<16} {c}')

    # Top 10 validated / top 5 anti
    validated = sorted([r for r in rows if r['tier'] == 'VALIDATED'], key=lambda r: -r['hit_rate'])[:10]
    if validated:
        print('\n  TOP VALIDATED prop signals:')
        for r in validated:
            print(f'    {r["signal_name"]:<50} HR={r["hit_rate"]}% n={r["sample_n"]}')
    anti = sorted([r for r in rows if r['tier'] == 'ANTI_VALIDATED'], key=lambda r: r['hit_rate'])[:5]
    if anti:
        print('\n  ANTI_VALIDATED prop signals (fade indicators):')
        for r in anti:
            print(f'    {r["signal_name"]:<50} HR={r["hit_rate"]}% n={r["sample_n"]}')

    print()
    write(rows, dry_run=dry_run)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=90)
    p.add_argument('--by-prop-type', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(days=args.days, by_prop_type=args.by_prop_type, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
