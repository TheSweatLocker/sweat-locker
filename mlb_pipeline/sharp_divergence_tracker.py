"""Sharp money divergence tracker (2026-08-08).

Records hit-rate per sharp-money-divergence bucket per market, refreshed
nightly. Writes to `jerry_cache` under key `sharp_divergence_stats` so
Jerry's struct can reference the current stats when reasoning about
sharp-money signal on a game.

Motivation: 2026-08-08 7-day audit found sharp money divergence has been
a FADE signal in the current sample — sharp 30+pp on totals hit 17%
(n=6), sharp 10+pp on ML hit 41% (n=22). This tracker keeps the number
fresh so the auto-fade rule can be graduated in when sample grows.

Phase 1 (2026-08-08 ship): record + surface only. No auto-cap yet.
Phase 2 (n>=200 per bucket): soft cap to LEAN when Jerry aligns with
Sharp 20+pp divergence side.
Phase 3 (n>=500): full auto-fade.

Usage:
    python sharp_divergence_tracker.py           # nightly recompute
    python sharp_divergence_tracker.py --show    # print current stats
"""
from __future__ import annotations
import argparse, json, os, sys
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

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

CACHE_KEY = 'sharp_divergence_stats'


def _bucket(div: int, market: str) -> str:
    """Divergence bucket label. 2026-08-09: added sharp_15+ (div 15-19) between
    sharp_20+ and sharp_10+ per ML fade analysis showing 15+pp is the real
    inflection point (ML sharp_15+ = 35% hit / n=23 / +35% ROI)."""
    if div >= 30: return 'sharp_30+'
    if div >= 20: return 'sharp_20+'
    if div >= 15: return 'sharp_15+'
    if div >= 10: return 'sharp_10+'
    if div <= -10: return 'public_10+'
    return 'aligned'


def recompute():
    """Read snapshots + results, produce bucket hit-rates."""
    r = requests.get(f'{SB}/rest/v1/mlb_game_context_snapshots', headers=H,
                     params=[('oddscrowd_snapshot', 'not.is.null'),
                             ('select', 'game_id,snapshot_date,oddscrowd_snapshot,close_total,close_spread'),
                             ('limit', '10000')],
                     timeout=30)
    snaps = r.json() if isinstance(r.json(), list) else []
    print(f'  {len(snaps)} snapshots with oddscrowd data')

    # Latest snapshot per game
    by_gid = {}
    for s in snaps:
        oc = s.get('oddscrowd_snapshot')
        if isinstance(oc, str):
            try: oc = json.loads(oc)
            except Exception: oc = None
        if not isinstance(oc, dict): continue
        s['oddscrowd_snapshot'] = oc
        prev = by_gid.get(s['game_id'])
        if not prev or s['snapshot_date'] > prev['snapshot_date']:
            by_gid[s['game_id']] = s
    snaps = list(by_gid.values())
    gids = [s['game_id'] for s in snaps]

    # Results
    results = {}
    for i in range(0, len(gids), 100):
        chunk = gids[i:i+100]
        rr = requests.get(f'{SB}/rest/v1/mlb_game_results', headers=H,
                          params=[('game_id', f'in.({",".join(chunk)})'),
                                  ('select', 'game_id,home_score,away_score,total_result')],
                          timeout=15)
        for row in (rr.json() if isinstance(rr.json(), list) else []):
            h, a = row.get('home_score'), row.get('away_score')
            if h is None or a is None: continue
            row['ml_winner'] = 'HOME' if h > a else ('AWAY' if a > h else 'PUSH')
            results[row['game_id']] = row
    print(f'  {len(results)} resolved games\n')

    # 2026-08-08: track recency window (last 7 game-dates) alongside full sample.
    # Powers the recency-safety kill switch — cap auto-disables if the sharp
    # side has been WINNING >55% in a bucket over the last 7 dates.
    from datetime import date, timedelta
    all_game_dates = sorted({s['snapshot_date'] for s in snaps}, reverse=True)
    recent_cutoff = all_game_dates[6] if len(all_game_dates) > 6 else (all_game_dates[-1] if all_game_dates else '')

    # Bucket accumulators: {market: {bucket: {sharp_hits, n, recent_hits, recent_n}}}
    stats = {'ml': {}, 'total': {}}
    for s in snaps:
        res = results.get(s['game_id'])
        if not res: continue
        oc = s['oddscrowd_snapshot']
        is_recent = s['snapshot_date'] >= recent_cutoff if recent_cutoff else False
        for mkt in ('ml', 'total'):
            blob = oc.get(mkt) or {}
            pick = blob.get('pick'); div = blob.get('div')
            if pick is None or div is None or div == -1: continue
            actual = res.get('ml_winner') if mkt == 'ml' else (res.get('total_result','') or '').upper()
            if not actual or actual == 'PUSH': continue
            bucket = _bucket(div, mkt)
            slot = stats[mkt].setdefault(bucket, {'sharp_hits': 0, 'n': 0,
                                                    'recent_sharp_hits': 0, 'recent_n': 0})
            slot['n'] += 1
            if pick == actual: slot['sharp_hits'] += 1
            if is_recent:
                slot['recent_n'] += 1
                if pick == actual: slot['recent_sharp_hits'] += 1

    # Phase 2A activation rules (2026-08-08 evening — user approved Option A):
    #   cap_active = TRUE iff:
    #     - bucket is sharp_20+ or sharp_30+ (widest fade signal)
    #     - lifetime n >= 12
    #     - lifetime sharp_hit_pct < 45  (fade edge exists)
    #     - RECENCY SAFETY: last 7d sharp_hit_pct < 55  (edge hasn't reversed)
    #   For sharp_10+ and coin buckets: cap_active stays False (recency unstable)
    # 2026-08-09: extended to include sharp_15+ per deep-dive audit.
    # ML sharp div>=15 (combined 15+/20+/30+ meta-bucket) cashed ~65% fade
    # over n=19-23, +35% ROI at market ML prices. TOTAL sharp_15+ has
    # weaker signal (~50%). Cap only fires if lifetime hit%<45.
    PHASE_2A_ELIGIBLE = {'sharp_15+', 'sharp_20+', 'sharp_30+'}
    # Lower n threshold specifically for sharp_15+ (n≥8 accepted) so the
    # smaller buckets can activate. Aggregate div≥15 sample is what
    # justifies the cap; individual buckets stay small until sample grows.
    PHASE_2A_MIN_N = {'sharp_15+': 8, 'sharp_20+': 12, 'sharp_30+': 12}
    payload = {
        'computed_at': datetime.now(timezone.utc).isoformat(),
        'n_snapshots': len(snaps),
        'n_resolved': len(results),
        'recent_cutoff_date': recent_cutoff,
        'buckets': {},
        'headline_finding': None,
        'phase': 'PHASE_2A_ACTIVE (cap-to-LEAN on sharp_20+ AND sharp_30+ with recency-safety)',
    }
    for mkt in ('ml', 'total'):
        payload['buckets'][mkt] = {}
        for bucket, slot in stats[mkt].items():
            n = slot['n']; h = slot['sharp_hits']
            rn = slot['recent_n']; rh = slot['recent_sharp_hits']
            lifetime_pct = round(100 * h / max(n, 1), 1)
            recent_pct = round(100 * rh / max(rn, 1), 1) if rn else None
            min_n = PHASE_2A_MIN_N.get(bucket, 12)
            cap_active = (bucket in PHASE_2A_ELIGIBLE
                          and n >= min_n
                          and lifetime_pct < 45
                          and (recent_pct is None or recent_pct < 55))
            recency_kill_switch = bool(rn and recent_pct is not None and recent_pct >= 55)
            payload['buckets'][mkt][bucket] = {
                'n': n, 'sharp_hits': h, 'sharp_hit_pct': lifetime_pct,
                'recent_n': rn, 'recent_sharp_hits': rh, 'recent_sharp_hit_pct': recent_pct,
                'cap_active': cap_active,
                'recency_kill_switch_tripped': recency_kill_switch,
                'phase_2_ready': n >= 200,  # legacy field — future Phase 3 threshold
            }

    # Headline: the most extreme bucket with meaningful n
    worst = None
    for mkt in ('ml', 'total'):
        for bucket, s in payload['buckets'][mkt].items():
            if s['n'] < 5: continue
            if bucket.startswith('sharp_') and s['sharp_hit_pct'] < 45:
                if not worst or s['sharp_hit_pct'] < worst['pct']:
                    worst = {'mkt': mkt, 'bucket': bucket, 'pct': s['sharp_hit_pct'], 'n': s['n']}
    if worst:
        payload['headline_finding'] = (
            f"Sharp {worst['bucket']} on {worst['mkt'].upper()} hits "
            f"{worst['pct']}% (n={worst['n']}) — FADE signal in current sample.")

    print('=== SHARP DIVERGENCE STATS ===')
    for mkt in ('ml', 'total'):
        print(f'\n{mkt.upper()}:')
        for bucket in ('sharp_30+', 'sharp_20+', 'sharp_15+', 'sharp_10+', 'aligned', 'public_10+'):
            s = payload['buckets'][mkt].get(bucket)
            if not s: continue
            recent = f' · recent: {s["recent_sharp_hits"]}/{s["recent_n"]}={s["recent_sharp_hit_pct"]}%' if s['recent_n'] else ''
            tag = ''
            if s['cap_active']: tag = '  🚨 CAP ACTIVE'
            elif s['recency_kill_switch_tripped']: tag = '  ⚠️ recency kill switch tripped'
            print(f'  {bucket:12s}  {s["sharp_hits"]:>3}/{s["n"]:<3}={s["sharp_hit_pct"]:5.1f}%{recent}{tag}')
    if payload['headline_finding']:
        print(f'\nHEADLINE: {payload["headline_finding"]}')

    # Write to jerry_cache
    # jerry_cache lacks a unique constraint on (cache_key,game_id) so
    # we can't upsert. Delete-then-insert instead — this row is a
    # singleton (GLOBAL scope for the sharp-div aggregate).
    requests.delete(
        f'{SB}/rest/v1/jerry_cache?cache_key=eq.{CACHE_KEY}&game_id=eq.GLOBAL',
        headers={**H_WRITE, 'Prefer': 'return=minimal'}, timeout=10,
    )
    wr = requests.post(f'{SB}/rest/v1/jerry_cache',
                       headers={**H_WRITE, 'Prefer': 'return=minimal'},
                       data=json.dumps({'cache_key': CACHE_KEY,
                                         'game_id': 'GLOBAL',
                                         'sport': 'MLB',
                                         'narrative': payload.get('headline_finding') or 'Sharp divergence stats (Phase 1: log only)',
                                         'data': payload,
                                         'fetched_at': datetime.now(timezone.utc).isoformat()},
                                        default=str),
                       timeout=15)
    if wr.status_code in (200, 201, 204):
        print(f'\n✓ written to jerry_cache["{CACHE_KEY}"]')
    else:
        print(f'\n⚠ jerry_cache write failed {wr.status_code}: {wr.text[:200]}')


def show():
    r = requests.get(f'{SB}/rest/v1/jerry_cache', headers=H,
                     params={'cache_key': f'eq.{CACHE_KEY}',
                             'select': 'data,fetched_at'},
                     timeout=15)
    rows = r.json() if isinstance(r.json(), list) else []
    if not rows:
        print(f'no cache row for {CACHE_KEY} yet — run without --show first')
        return
    print(json.dumps(rows[0], indent=2, default=str)[:3000])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--show', action='store_true')
    args = ap.parse_args()
    if args.show:
        show()
    else:
        recompute()


if __name__ == '__main__':
    main()
