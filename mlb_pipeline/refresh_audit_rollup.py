"""Live-refresh audit_roll_up in sweat_card cache — no snapshot lag.

2026-09-10 fix. Home page 'Live Audit (rolling 30D)' block snapshots
mlb_tier_calibration values at sweat_card generation (once/day, 12:32
UTC). Any calibration recomputes AFTER that show stale numbers on
home page. User saw YRFI LEAN 27-14 on 41 total when live 30d was
26-26 on 52 — snapshot lag.

Fix: PATCH just the `data.audit_roll_up` field of the existing
sweat_card_YYYY-MM-DD cache row every 15-30 min. Zero client change,
sweat_card items stay locked (per 9/9 publish-lock design) but the
audit rollup floats with the latest calibration.

Cron: run every 30 min alongside pipeline crons. Cheap (~2 sec).
"""
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H_READ = {'apikey': K, 'Authorization': f'Bearer {K}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


AUDIT_COHORTS = ('nrfi_prime_90_94', 'yrfi_lean_le40', 'confluence_prime_ge4',
                 'autofade_dog_high_conv', 'total_extreme_under_ge3')


def _fetch_live_rollup() -> dict:
    """Same shape as generate_sweat_card.fetch_audit_roll_up() but always fresh."""
    tiers_csv = ','.join(AUDIT_COHORTS)
    r = requests.get(f'{SB}/rest/v1/mlb_tier_calibration', headers=H_READ, params={
        'window_label': 'in.(7d,30d,std)',
        'sport': 'eq.mlb',
        'tier': f'in.({tiers_csv})',
        'select': 'tier,window_label,hits,total,hit_rate,computed_date',
        'order': 'computed_date.desc',
    }, timeout=15)
    if r.status_code != 200:
        print(f'  ⚠ calibration fetch failed {r.status_code}')
        return {}
    by_tier = {}
    for row in r.json():
        if (row.get('total') or 0) < 10:
            continue
        # De-dupe: prefer newest computed_date per (tier, window) since sort
        # is desc, first entry wins.
        tier = row['tier']
        w = row['window_label']
        by_tier.setdefault(tier, {})
        if w not in by_tier[tier]:
            by_tier[tier][w] = {
                'hits': row['hits'],
                'total': row['total'],
                'hit_rate': row['hit_rate'],
                'computed_date': row.get('computed_date'),
            }
    return by_tier


def today_et():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime('%Y-%m-%d')


def refresh(dry_run: bool = False):
    today = today_et()
    print(f'== refresh_audit_rollup · {today} · dry={dry_run} ==')

    # 2026-09-11 KILLED — the "Live Audit (rolling 30D)" section was removed
    # from the app render (generate_sweat_card.py:2076 sets audit_roll_up=None
    # permanently). This refresher was clobbering that null on every scheduled
    # run, re-populating the field so old TestFlight bundles (that still
    # render the section) kept showing MLB cohort jargon under the sweat card.
    # Neuter the whole write path — the rollup data isn't rendered anywhere.
    # Cohort record display now lives in project_cohort_signal_ux_909 queue
    # for a plain-english redesign, separate table.
    print('  ⏭  refresher DISABLED 2026-09-11 (see generate_sweat_card.py comment)')
    return 0

    fresh = _fetch_live_rollup()
    print(f'  fresh rollup: {len(fresh)} cohorts')
    if not fresh:
        print('  ⚠ nothing to write'); return 0

    # PATCH just data.audit_roll_up on existing sweat_card row.
    # PostgreSQL JSONB supports nested update via jsonb_set. Rather than
    # RPC we can do a full read-modify-write of the data column here.
    r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H_READ, params={
        'cache_key': f'eq.sweat_card_{today}',
        'select': 'data',
    }, timeout=15)
    if r.status_code != 200 or not r.json():
        print(f'  ⚠ sweat_card_{today} not found — nothing to patch'); return 0
    row = r.json()[0]
    current_data = row.get('data') or {}
    if isinstance(current_data, str):
        try: current_data = json.loads(current_data)
        except Exception: current_data = {}

    # Compare — bail if identical (no wasted write)
    current_rollup = current_data.get('audit_roll_up') or {}
    if _rollup_equal(current_rollup, fresh):
        print('  ⏭  no change — rollup already fresh')
        return 0

    # Update the field and PATCH back
    current_data['audit_roll_up'] = fresh
    current_data['audit_roll_up_refreshed_at'] = datetime.now(timezone.utc).isoformat()

    # Log the changes so we can see what moved
    for cohort in fresh.keys():
        new = fresh.get(cohort, {}).get('30d') or fresh.get(cohort, {}).get('std')
        old = current_rollup.get(cohort, {}).get('30d') or current_rollup.get(cohort, {}).get('std')
        if new and old:
            new_str = f"{new['hits']}-{new['total']-new['hits']} ({int(new['hit_rate']*100)}%)"
            old_str = f"{old['hits']}-{old['total']-old['hits']} ({int(old['hit_rate']*100)}%)"
            if new_str != old_str:
                print(f'  Δ {cohort:<30} {old_str}  →  {new_str}')
        elif new:
            print(f'  + {cohort:<30} NEW: {new["hits"]}-{new["total"]-new["hits"]} ({int(new["hit_rate"]*100)}%)')

    if dry_run:
        print('  [DRY] would PATCH sweat_card')
        return 1

    patch_r = requests.patch(f'{SB}/rest/v1/jerry_cache',
        headers=H_WRITE, params={'cache_key': f'eq.sweat_card_{today}'},
        json={'data': current_data}, timeout=15)
    if patch_r.status_code in (200, 204):
        print(f'  ✅ PATCHed sweat_card_{today}.audit_roll_up')
        return 1
    else:
        print(f'  ✗ PATCH failed {patch_r.status_code}: {patch_r.text[:200]}')
        return 0


def _rollup_equal(a: dict, b: dict) -> bool:
    """Compare two rollups by the numbers users actually see."""
    if set(a.keys()) != set(b.keys()): return False
    for k in a:
        wa = a[k]; wb = b[k]
        if not isinstance(wa, dict) or not isinstance(wb, dict): return False
        for w in ('7d', '30d', 'std'):
            av = wa.get(w); bv = wb.get(w)
            if bool(av) != bool(bv): return False
            if av and bv:
                if av.get('hits') != bv.get('hits'): return False
                if av.get('total') != bv.get('total'): return False
    return True


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    refresh(dry_run=args.dry_run)
