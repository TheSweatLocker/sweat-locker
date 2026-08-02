"""Compute model_track_records from jerry_cache historical struct.

Phase 1b (2026-08-02). Mines jerry_cache (2599 rows spanning
March-August 2026) — each row's data JSON has per-game struct with
model outputs (resolver direction, model_total, market_total, etc).
Joins with mlb_game_results by game_id or natural key. Grades each
model's historical predictions. Writes rolling W/L/hit_rate per
(sport, model_name, market, direction, bucket_window).

Currently backtestable from jerry_cache struct (Option A):
  RESOLVER    struct.resolver.direction  (OVER/UNDER/HOME/AWAY)
  MODEL_TOTAL struct.market.model_total  (vs close_total → OVER/UNDER)
  MODEL_SPREAD struct.market.model_spread (vs close_spread → HOME/AWAY)
  PANEL_TOTAL struct.market.panel_implied_total (when present)

MC / PANEL / V4 individual outputs aren't cleanly separated in jerry_cache
struct. Option B (mlb_game_context_snapshot table + nightly snapshot)
starts preserving those going forward from tonight.

Populates model_track_records. Jerry reads this via new lookup helper
(built after this ships) to weight signals adaptively.
"""
import argparse, os, sys, json
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


def compute_hint_weight(hit_rate, n):
    """Recommended weight 0.0-3.0 based on hit_rate + sample size."""
    if n < 25: return 1.0                       # too thin — default weight
    if hit_rate is None: return 1.0
    edge = hit_rate - 0.5
    if edge >= 0.10: return min(2.5, 1.0 + edge * 10)   # +10pp = weight 2, +15pp = weight 2.5
    if edge >= 0.05: return 1.3
    if edge >= 0.03: return 1.15
    if edge <= -0.10: return max(0.3, 1.0 + edge * 4)   # -10pp = weight 0.6
    if edge <= -0.05: return 0.75
    return 1.0


def compute_for_sport(sport: str = 'MLB', window: str = 'lifetime') -> None:
    print(f'=== compute_model_track_records · {sport} · window={window} ===')
    date_filter = None
    if window == '90d':
        date_filter = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
    elif window == '30d':
        date_filter = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
    elif window == '14d':
        date_filter = (datetime.now(timezone.utc) - timedelta(days=14)).strftime('%Y-%m-%d')

    # Pull jerry_cache game_read entries (paginated)
    all_reads = []
    offset = 0
    while True:
        params = {'sport': f'eq.{sport.lower()}', 'cache_key': 'like.game_read_%',
                  'select': 'cache_key,data,created_at', 'limit': '500',
                  'offset': str(offset), 'order': 'created_at.desc'}
        r = requests.get(f'{SB}/rest/v1/jerry_cache',
                         headers=H_READ, params=params, timeout=30).json()
        if not isinstance(r, list) or not r: break
        all_reads += r
        if len(r) < 500: break
        offset += 500
    print(f'  jerry_cache game_reads: {len(all_reads)}')

    # Extract per-game predictions
    predictions = []
    for row in all_reads:
        try:
            data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict): continue
        gid = data.get('game_id')
        meta = data.get('meta') or {}
        game_date = meta.get('game_date') or row['created_at'][:10]
        if date_filter and game_date < date_filter: continue
        matchup = data.get('matchup', '')
        # Parse away/home from matchup ("A @ B")
        parts = matchup.split(' @ ')
        if len(parts) != 2: continue
        away_team, home_team = parts[0].strip(), parts[1].strip()

        market = data.get('market') or {}
        resolver = data.get('resolver') or {}
        resolver_side = data.get('resolver_side') or {}

        rec = {'game_id': gid, 'game_date': game_date,
               'away_team': away_team, 'home_team': home_team,
               'model_total': market.get('model_total'),
               'close_total': market.get('close_total'),
               'model_spread': market.get('model_spread'),
               'close_spread': market.get('close_spread'),
               'panel_implied_total': market.get('panel_implied_total'),
               'resolver_dir': resolver.get('direction'),
               'resolver_side_dir': resolver_side.get('direction')}
        predictions.append(rec)

    print(f'  extracted predictions: {len(predictions)}')

    # Load mlb_game_results
    game_dates = list({p['game_date'] for p in predictions})
    results_by_natural = {}
    # Chunk by date
    for i in range(0, len(game_dates), 30):
        chunk = game_dates[i:i+30]
        in_dates = ','.join(f'"{d}"' for d in chunk)
        r = requests.get(f'{SB}/rest/v1/mlb_game_results',
                         headers=H_READ,
                         params={'game_date': f'in.({in_dates})',
                                 'select': 'game_id,home_team,away_team,home_score,away_score,total_result,spread_result,run_line_result,game_date',
                                 'limit': '2000'}, timeout=20).json()
        for x in (r if isinstance(r, list) else []):
            if x.get('home_score') is None: continue
            k = (x['game_date'], (x['home_team'] or '').upper(), (x['away_team'] or '').upper())
            results_by_natural[k] = x
            # Also index by game_id
            if x.get('game_id'):
                results_by_natural[x['game_id']] = x

    graded_gids = {v['game_id'] for v in results_by_natural.values() if isinstance(v, dict) and v.get('game_id')}
    print(f'  graded games in date range: {len(graded_gids)}')

    # Buckets: (model_name, market, direction_filter) → stats
    buckets = defaultdict(lambda: {'w':0, 'n':0, 'push':0})

    for p in predictions:
        # Find matching result
        res = results_by_natural.get(p['game_id'])
        if not res:
            k = (p['game_date'], (p['home_team'] or '').upper(), (p['away_team'] or '').upper())
            res = results_by_natural.get(k)
        if not res: continue
        hs, as_ = res['home_score'], res['away_score']
        if hs == as_:
            # push on ML; other markets have their own logic
            pass
        actual_total = hs + as_

        # === RESOLVER (side or total, based on direction letter) ===
        rd = (p['resolver_dir'] or '').upper()
        if rd in ('OVER','UNDER'):
            if p['close_total'] is not None:
                try:
                    line = float(p['close_total'])
                    if actual_total != line:
                        actual = 'OVER' if actual_total > line else 'UNDER'
                        buckets[('RESOLVER','TOTAL',None)]['n'] += 1
                        if rd == actual: buckets[('RESOLVER','TOTAL',None)]['w'] += 1
                except (TypeError, ValueError): pass

        # RESOLVER_SIDE for ML
        rsd = (p['resolver_side_dir'] or '').upper()
        if rsd in ('HOME','AWAY') and hs != as_:
            winner = 'HOME' if hs > as_ else 'AWAY'
            buckets[('RESOLVER_SIDE','ML',None)]['n'] += 1
            if rsd == winner: buckets[('RESOLVER_SIDE','ML',None)]['w'] += 1

        # === MODEL_TOTAL ===
        if p['model_total'] is not None and p['close_total'] is not None:
            try:
                mt = float(p['model_total']); ct = float(p['close_total'])
                if actual_total != ct:
                    actual = 'OVER' if actual_total > ct else 'UNDER'
                    pick = 'OVER' if mt > ct else ('UNDER' if mt < ct else None)
                    if pick:
                        buckets[('MODEL_TOTAL','TOTAL',None)]['n'] += 1
                        if pick == actual: buckets[('MODEL_TOTAL','TOTAL',None)]['w'] += 1
            except (TypeError, ValueError): pass

        # === PANEL_TOTAL (when present) ===
        if p['panel_implied_total'] is not None and p['close_total'] is not None:
            try:
                pt = float(p['panel_implied_total']); ct = float(p['close_total'])
                if actual_total != ct:
                    actual = 'OVER' if actual_total > ct else 'UNDER'
                    pick = 'OVER' if pt > ct else ('UNDER' if pt < ct else None)
                    if pick:
                        buckets[('PANEL_TOTAL','TOTAL',None)]['n'] += 1
                        if pick == actual: buckets[('PANEL_TOTAL','TOTAL',None)]['w'] += 1
            except (TypeError, ValueError): pass

        # === MODEL_SPREAD ===
        # CORRECTED 2026-08-02 (spread_delta convention from game_context.py:14):
        #   spread_delta = model_pred_spread + close_spread
        #   POSITIVE = model leans more home than market → back HOME
        #   NEGATIVE = model leans more away than market → back AWAY
        # Prior version compared model_spread < close_spread directly, which
        # inverted the signal (46.7% W) because model_spread is positive-for-home
        # while close_spread is negative-for-home (opposite signs).
        if p['model_spread'] is not None and p['close_spread'] is not None:
            try:
                delta = float(p['model_spread']) + float(p['close_spread'])
                if hs != as_ and delta != 0:
                    pick = 'HOME' if delta > 0 else 'AWAY'
                    winner = 'HOME' if hs > as_ else 'AWAY'
                    buckets[('MODEL_SPREAD','ML',None)]['n'] += 1
                    if pick == winner: buckets[('MODEL_SPREAD','ML',None)]['w'] += 1
            except (TypeError, ValueError): pass

    # Write to model_track_records
    print(f'\n  === RESULTS ===')
    print(f'  {"model":<18} {"market":<7} {"W-L":<10} {"hit%":<7} {"weight"}')
    written = 0
    for (name, market, dir_filter), v in sorted(buckets.items(), key=lambda x: -x[1]['n']):
        n = v['n']
        if n < 5: continue
        w = v['w']; l = n - w - v['push']
        hit = w / (w + l) if (w + l) else None
        weight = compute_hint_weight(hit, n)
        payload = {
            'sport': sport, 'model_name': name, 'market': market,
            'direction_filter': dir_filter, 'bucket_window': window,
            'wins': w, 'losses': l, 'pushes': v['push'], 'sample_n': n,
            'hit_rate': round(hit * 100, 1) if hit is not None else None,
            'roi_pct': round(100 * ((hit * (100/110)) - (1 - hit)), 1) if hit is not None else None,
            'recommended_weight': round(weight, 2),
            'computed_at': datetime.now(timezone.utc).isoformat(),
        }
        r = requests.post(
            f'{SB}/rest/v1/model_track_records?on_conflict=sport,model_name,market,direction_filter,bucket_window',
            headers=H_WRITE, json=payload, timeout=15)
        if r.status_code in (200, 201, 204):
            written += 1
            hit_str = f'{hit*100:.1f}%' if hit else '-'
            print(f'  {name[:17]:<18} {market:<7} {w}-{l:<4} {hit_str:<7} {weight:.2f}')
        else:
            print(f'  ⚠ upsert {r.status_code}: {r.text[:150]}')
    print(f'\n=== wrote {written} model track record rows ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--window', default='lifetime', choices=['lifetime','90d','30d','14d'])
    p.add_argument('--sport', default='MLB')
    args = p.parse_args()
    compute_for_sport(sport=args.sport, window=args.window)
