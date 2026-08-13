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
    'NFL': 'nfl_pipeline_props',        # enabled 2026-08-03 Sprint 2
    # 'NBA': 'nba_pipeline_props',      # add when NBA prop pipeline ships
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


# Estimated juice for prop types where odds aren't captured in DB
# (hits_over/hits_under generator never attached odds). Industry-standard
# midpoints by (prop_type, direction, prop_line). Marks ROI as an estimate
# in the display but is CLOSE ENOUGH to prevent us from missing edge on the
# 540+ hits_over graded rows.
def estimate_juice_american(prop_type: str, direction: str, prop_line):
    if not prop_type or not direction: return None
    try: line = float(prop_line) if prop_line is not None else None
    except (TypeError, ValueError): return None
    family = FAMILY_MAP.get(prop_type, prop_type)
    if family != 'hits': return None   # only fill hits for now
    d = direction.lower()
    # Hits (batter to get X hits) — typical juice midpoints
    if line == 0.5:
        return -320 if d == 'over' else 250          # first-hit
    if line == 1.5:
        return -140 if d == 'over' else 105          # 2+ hits
    if line == 2.5:
        return 120 if d == 'over' else -160          # 3+ hits
    return -110


def compute_jerry_hint(hit_rate, roi_pct, sample_n):
    """BACK / FADE / PASS with confidence 0-100.

    2026-08-12 sample-size discipline (user-caught):
      * n<30 → LEAN cap only (not full BACK). Even 60% n=20 has ±22pp
        confidence interval — can't call it a lock.
      * n<20 → PASS regardless (insufficient data).
      * Above n>=30, hit-rate + ROI can drive full BACK/FADE.
    Prior version let n=20 pop as PRIME BACK based on 60% hit rate.
    """
    if sample_n < 20:
        return 'PASS', 20            # insufficient data → default cautious
    if roi_pct is None:
        # No odds — fall back to hit rate signal only, be conservative
        if hit_rate is None: return 'PASS', 20
        if hit_rate >= 65 and sample_n >= 30: return 'BACK', 55
        if hit_rate <= 35 and sample_n >= 30: return 'FADE', 55
        return 'PASS', 30

    # ROI-driven decision — but cap confidence when sample is thin
    if roi_pct >= 10:
        conf = min(90, 50 + int(roi_pct))                  # +10% ROI → 60 conf, +40% → 90
        if sample_n < 30: conf = min(conf, 55)  # cap at LEAN when n<30
        return 'BACK', conf
    if roi_pct >= 3:
        conf = max(45, 40 + int(roi_pct))
        if sample_n < 30: conf = min(conf, 50)
        return 'BACK', conf
    if roi_pct <= -10:
        conf = min(90, 50 + int(abs(roi_pct)))
        if sample_n < 30: conf = min(conf, 55)
        return 'FADE', conf
    if roi_pct <= -3:
        conf = max(45, 40 + int(abs(roi_pct)))
        if sample_n < 30: conf = min(conf, 50)
        return 'FADE', conf
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
    elif window == '14d':
        date_filter = (datetime.now(timezone.utc) - timedelta(days=14)).strftime('%Y-%m-%d')

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
        if dec is None:
            # Fallback: industry-standard estimated juice for hits family
            est = estimate_juice_american(pt, d, p.get('prop_line'))
            dec = american_to_decimal(est)
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


def compute_time_weighted(sport: str = 'MLB') -> None:
    """2026-08-10: Build time-weighted priors from 14d/30d/90d/lifetime rows.

    Recent hit-rates matter more than 90-day-old ones. Applies exponential-
    decay weights [14d × 3, 30d × 1.5, 90d × 1, lifetime × 0.5] normalized
    by sample size within each bucket. Writes back with
    bucket_window='time_weighted'.

    Downstream Jerry synth reads 'time_weighted' rows (via jerry_hint) to
    inform BACK/FADE decisions with recency-aware historical priors.

    Sharpens trap detection ~5-10pp per prior audits — when a bucket was
    58% lifetime but 40% L14, blend now reflects the fade signal instead
    of getting drowned out by stale data.

    Runs after compute(sport, 'all') so all 4 windows exist to blend.
    """
    print(f'\n=== compute_prop_bucket_roi · TIME-WEIGHTED BLEND · sport={sport} ===')
    # Pull all 4 windows for this sport
    resp = requests.get(f'{SB}/rest/v1/prop_bucket_roi', headers=H_READ,
        params={'sport': f'eq.{sport}',
                'bucket_window': 'in.(lifetime,90d,30d,14d)',
                'select': 'tier,prop_type,direction,bucket_window,wins,losses,pushes,'
                          'sample_n,hit_rate,avg_decimal_odds,roi_pct',
                'limit': '2000'},
        timeout=15)
    r = resp.json() if resp.status_code == 200 else []
    if not isinstance(r, list) or not r:
        print(f'  no windowed rows found for {sport} — run compute() first'); return

    # Weights
    W = {'14d': 3.0, '30d': 1.5, '90d': 1.0, 'lifetime': 0.5}

    # Group by (tier, prop_type, direction)
    from collections import defaultdict
    grouped = defaultdict(dict)  # key -> {window: row}
    for row in r:
        key = (row['tier'], row['prop_type'], row['direction'])
        grouped[key][row['bucket_window']] = row

    written = 0
    print(f'\n{"tier":<10} {"family":<8} {"dir":<6} {"blended%":<10} {"blended_ROI":<13} {"total_n":<8}')
    for key, wins_by_window in grouped.items():
        tier, family, d = key
        # Weighted average of hit_rate and roi using sample size × decay weight
        num_hit = num_roi = denom = 0.0
        total_n = 0
        avg_dec_num = avg_dec_denom = 0
        for w_key, w_val in W.items():
            row = wins_by_window.get(w_key)
            if not row: continue
            n = row.get('sample_n', 0) or 0
            if n <= 0: continue
            weight = w_val * n
            hr = row.get('hit_rate')
            if hr is not None:
                num_hit += weight * hr
                denom += weight
            roi = row.get('roi_pct')
            if roi is not None:
                num_roi += weight * roi
            ad = row.get('avg_decimal_odds')
            if ad:
                avg_dec_num += n * ad
                avg_dec_denom += n
            total_n = max(total_n, n)  # use lifetime n as reference
        if denom == 0: continue
        blended_hit = round(num_hit / denom, 1)
        blended_roi = round(num_roi / denom, 1)
        blended_dec = round(avg_dec_num / avg_dec_denom, 3) if avg_dec_denom else None
        hint, hint_conf = compute_jerry_hint(blended_hit, blended_roi, total_n)
        payload = {
            'sport': sport, 'tier': tier, 'prop_type': family, 'direction': d,
            'bucket_window': 'time_weighted',
            # Blended row — no discrete W/L/P counts (those live on windowed rows).
            # Table has NOT NULL constraint so use 0 placeholders. Downstream
            # consumers should read sample_n + hit_rate + roi_pct for blends.
            'wins': 0, 'losses': 0, 'pushes': 0,
            'sample_n': total_n,
            'hit_rate': blended_hit,
            'avg_decimal_odds': blended_dec,
            'roi_pct': blended_roi,
            'jerry_hint': hint,
            'hint_confidence': hint_conf,
            'computed_at': datetime.now(timezone.utc).isoformat(),
        }
        pr = requests.post(
            f'{SB}/rest/v1/prop_bucket_roi?on_conflict=sport,tier,prop_type,direction,bucket_window',
            headers=H_WRITE, json=payload, timeout=15)
        if pr.status_code in (200, 201, 204):
            written += 1
            print(f'  {tier[:9]:<10} {family[:7]:<8} {d[:5]:<6} '
                  f'{blended_hit:>6.1f}%   {blended_roi:>+7.1f}%    n={total_n}')
        else:
            print(f'  UPSERT FAILED {pr.status_code} for {tier}/{family}/{d}: {pr.text[:150]}')
    print(f'\n  wrote {written} time-weighted rows')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--window', default='all',
                   choices=['lifetime', '90d', '30d', '14d', 'all'],
                   help="'all' computes lifetime + 90d + 30d + 14d + time_weighted blend")
    p.add_argument('--sport', default='ALL',
                   help='MLB / NBA / NFL / NCAAF / NCAAB / UFC / ALL (loops)')
    args = p.parse_args()
    sports = list(PROPS_TABLE.keys()) if args.sport == 'ALL' else [args.sport]
    # 'all' → run every window + time_weighted blend on top.
    windows = ['lifetime', '90d', '30d', '14d'] if args.window == 'all' else [args.window]
    for s in sports:
        for w in windows:
            compute(window=w, sport=s)
        # 2026-08-10: after all windows computed, blend into time-weighted row
        if args.window == 'all':
            compute_time_weighted(sport=s)
