"""Recompute prop_bucket_roi from graded props (2026-08-01 · R-2).

Nightly job that mines mlb_pipeline_props to build juice-adjusted ROI
per (sport, tier, prop_type, direction) bucket. Jerry synth reads
this table to inform BACK/FADE/PASS decisions.

MVP: MLB only. Extend to NBA/NFL/UFC when their prop pipelines mature.

Output: upserts to prop_bucket_roi table.

Usage:
    python compute_prop_bucket_roi.py [--window lifetime|90d|30d]
"""
import argparse, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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

# Sport-universal props table dispatch — same pattern as grade_prop_jerry_reads.
# All sports write to the SAME prop_bucket_roi table with sport tag; the mining
# reads from the sport-specific graded-props table.
PROPS_TABLE = {
    'MLB': 'mlb_pipeline_props',
    # 'NBA': 'nba_pipeline_props',      # add when NBA prop pipeline ships
    # 'NFL': 'nfl_pipeline_props',
    # 'NCAAF': 'ncaaf_pipeline_props',
    # 'NCAAB': 'ncaab_pipeline_props',
}

# Prop-type family normalization — collapse variants like 'ks_over' + 'ks' into 'ks'
FAMILY_MAP = {
    'ks_over': 'ks', 'ks_under': 'ks', 'ks': 'ks',
    'bb_over': 'bb', 'bb_under': 'bb', 'bb': 'bb',
    'er_over': 'er', 'er_under': 'er', 'er': 'er',
    'ha_over': 'ha', 'ha_under': 'ha', 'ha': 'ha',
    'outs_over': 'outs', 'outs_under': 'outs', 'outs': 'outs',
    'hits_over': 'hits', 'hits_under': 'hits', 'hits': 'hits',
}


def american_to_decimal(a):
    if a is None: return None
    try: a = int(a)
    except (ValueError, TypeError): return None
    if a > 0: return 1 + a/100
    if a < 0: return 1 + 100/abs(a)
    return None


def compute_jerry_hint(hit_rate, roi_pct, sample_n):
    """BACK / FADE / PASS with confidence 0-100."""
    if sample_n < 20:
        return 'PASS', 20            # insufficient data → default cautious
    if roi_pct is None:
        # No odds — fall back to hit rate signal only, be conservative
        if hit_rate is None: return 'PASS', 20
        if hit_rate >= 65: return 'BACK', 55
        if hit_rate <= 35: return 'FADE', 55
        return 'PASS', 30

    # ROI-driven decision
    if roi_pct >= 10:
        conf = min(90, 50 + int(roi_pct))                  # +10% ROI → 60 conf, +40% → 90
        return 'BACK', conf
    if roi_pct >= 3:
        return 'BACK', max(45, 40 + int(roi_pct))
    if roi_pct <= -10:
        conf = min(90, 50 + int(abs(roi_pct)))
        return 'FADE', conf
    if roi_pct <= -3:
        return 'FADE', max(45, 40 + int(abs(roi_pct)))
    return 'PASS', 30


def compute(window: str = 'lifetime', sport: str = 'MLB') -> None:
    table = PROPS_TABLE.get(sport)
    if not table:
        print(f'  [{sport}] no props table registered — skip'); return
    print(f'=== compute_prop_bucket_roi · sport={sport} · window={window} ===')

    date_filter = None
    if window == '90d':
        date_filter = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
    elif window == '30d':
        date_filter = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')

    all_props = []
    offset = 0
    while True:
        params = {'select': 'prop_type,direction,tier,result,book_over_odds,book_under_odds',
                  'result': 'in.(Win,Loss)', 'limit': '1000', 'offset': str(offset)}
        if date_filter: params['game_date'] = f'gte.{date_filter}'
        r = requests.get(f'{SB}/rest/v1/{table}',
                         headers=H_READ, params=params, timeout=30).json()
        if not isinstance(r, list) or not r: break
        all_props += r
        if len(r) < 1000: break
        offset += 1000
    print(f'  {len(all_props)} graded rows for {sport}')

    # Bucket: (tier, prop_family, direction) — merges prop_type variants
    buckets = defaultdict(lambda: {'w':0, 'n':0, 'push':0, 'odds_sum':0, 'odds_n':0})
    for p in all_props:
        pt = p.get('prop_type') or ''
        family = FAMILY_MAP.get(pt, pt.split('_')[0] if '_' in pt else pt)
        d = (p.get('direction') or '').lower()
        tier = p.get('tier') or 'UNK'
        k = (tier, family, d)
        buckets[k]['n'] += 1
        if p['result'] == 'Win': buckets[k]['w'] += 1
        elif p['result'] == 'Push': buckets[k]['push'] += 1
        odds = p.get('book_over_odds') if d == 'over' else p.get('book_under_odds')
        dec = american_to_decimal(odds)
        if dec:
            buckets[k]['odds_sum'] += dec
            buckets[k]['odds_n'] += 1

    written = 0
    print(f'\n{"tier":<10} {"family":<8} {"dir":<6} {"W-L":<9} {"hit%":<7} {"ROI":<9} {"hint":<8} n')
    for (tier, family, d), v in sorted(buckets.items(), key=lambda x: -x[1]['n']):
        n = v['n']
        if n < 10: continue                    # skip tiny buckets
        wins, losses = v['w'], n - v['w'] - v['push']
        graded = wins + losses
        if graded == 0: continue
        hit_rate = wins / graded
        avg_dec = v['odds_sum'] / v['odds_n'] if v['odds_n'] else None
        roi = None
        if avg_dec and graded > 0:
            roi = 100 * (hit_rate * (avg_dec - 1) - (1 - hit_rate))
        hint, hint_conf = compute_jerry_hint(hit_rate * 100, roi, n)
        payload = {
            'sport': sport, 'tier': tier, 'prop_type': family, 'direction': d,
            'bucket_window': window,
            'wins': wins, 'losses': losses, 'pushes': v['push'], 'sample_n': n,
            'hit_rate': round(hit_rate * 100, 1),
            'avg_decimal_odds': round(avg_dec, 3) if avg_dec else None,
            'roi_pct': round(roi, 1) if roi is not None else None,
            'jerry_hint': hint,
            'hint_confidence': hint_conf,
            'computed_at': datetime.now(timezone.utc).isoformat(),
        }
        r = requests.post(
            f'{SB}/rest/v1/prop_bucket_roi?on_conflict=sport,tier,prop_type,direction,bucket_window',
            headers=H_WRITE, json=payload, timeout=15)
        if r.status_code in (200, 201, 204):
            written += 1
            roi_s = f'{roi:+.1f}%' if roi is not None else '   -   '
            print(f'  {tier[:9]:<10} {family[:7]:<8} {d[:5]:<6} '
                  f'{wins:>3}-{losses:<3}  {hit_rate*100:>5.1f}%  {roi_s:<9} {hint:<8} n={n}')
        else:
            print(f'  ⚠ upsert {r.status_code}: {r.text[:120]}')

    print(f'\n=== wrote {written} bucket rows ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--window', default='lifetime', choices=['lifetime', '90d', '30d'])
    p.add_argument('--sport', default='ALL',
                   help='MLB / NBA / NFL / NCAAF / NCAAB / UFC / ALL (loops)')
    args = p.parse_args()
    sports = list(PROPS_TABLE.keys()) if args.sport == 'ALL' else [args.sport]
    for s in sports:
        compute(window=args.window, sport=s)
