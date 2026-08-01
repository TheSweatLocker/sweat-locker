"""Compute game-level bucket ROI (2026-08-01 · R-2 companion).

Mines historical primary_play + game_context to build ROI per
(sport, tier, market, direction) bucket. Jerry synth reads this for
ML/RL/Total decisions.

MLB first. Sport-universal via RESULTS_TABLE dispatch.
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

# Sport-universal dispatch — same architecture as grade_jerry_reads.
# Each sport has its own game_context + game_results tables; all write to
# the shared game_bucket_roi table with sport tag.
RESULTS_TABLE = {
    'MLB':   'mlb_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    # 'NBA':   'nba_game_results',
}
CONTEXT_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
    # 'NBA':   'nba_game_context',
}
# Field-name dispatch — sports vary. NFL uses 'points' not 'runs',
# 'close_home_ml' not 'home_ml_close', etc. NCAAF + NCAAB share NFL's
# column naming since they were built off the same college template.
FIELD_MAP = {
    'MLB':   {'home_ml':'home_ml_close','away_ml':'away_ml_close','total':'close_total','spread':'close_spread'},
    'NFL':   {'home_ml':'close_home_ml','away_ml':'close_away_ml','total':'close_total','spread':'close_spread'},
    'NCAAF': {'home_ml':'close_home_ml','away_ml':'close_away_ml','total':'close_total','spread':'close_spread'},
    'NCAAB': {'home_ml':'close_home_ml','away_ml':'close_away_ml','total':'close_total','spread':'close_spread'},
}

# Note (2026-08-01): NFL context uses hash game_ids while nfl_game_results
# uses '{season}_{week}_{away}_{home}' format — game_id JOIN currently
# fails. NCAAF/NCAAB may hit similar mismatches. Bridge fix needed at
# ingest time (either normalize game_id or add a mapping table). Until
# that ships, NFL/NCAAF/NCAAB will populate 0 rows even with data.


def american_to_decimal(a):
    if a is None: return None
    try: a = int(a)
    except (ValueError, TypeError): return None
    if a > 0: return 1 + a/100
    if a < 0: return 1 + 100/abs(a)


def compute_hint(hit_rate, roi_pct, n):
    if n < 20: return 'PASS', 20
    if roi_pct is None:
        if hit_rate is None: return 'PASS', 20
        if hit_rate >= 65: return 'BACK', 55
        if hit_rate <= 35: return 'FADE', 55
        return 'PASS', 30
    if roi_pct >= 10: return 'BACK', min(90, 50 + int(roi_pct))
    if roi_pct >= 3: return 'BACK', max(45, 40 + int(roi_pct))
    if roi_pct <= -10: return 'FADE', min(90, 50 + int(abs(roi_pct)))
    if roi_pct <= -3: return 'FADE', max(45, 40 + int(abs(roi_pct)))
    return 'PASS', 30


def compute_sport(sport: str, window: str = 'lifetime') -> None:
    ctx_table = CONTEXT_TABLE.get(sport); res_table = RESULTS_TABLE.get(sport)
    fmap = FIELD_MAP.get(sport)
    if not ctx_table or not res_table or not fmap:
        print(f'  [{sport}] no registry entry — skip'); return
    print(f'=== compute_game_bucket_roi · {sport} · window={window} ===')

    ctx_all = []
    offset = 0
    home_ml_col = fmap['home_ml']; away_ml_col = fmap['away_ml']
    total_col = fmap['total']; spread_col = fmap['spread']
    # Include home_team + away_team for natural-key join fallback (NFL/NCAAF/NCAAB
    # have hash game_ids in context but season-format in results — join fails on
    # game_id, works on (game_date, home_team, away_team))
    select_cols = f'game_id,game_date,home_team,away_team,{home_ml_col},{away_ml_col},{total_col},primary_play,{spread_col}'
    while True:
        params = {'select': select_cols,
                  'limit': '500', 'offset': str(offset)}
        r = requests.get(f'{SB}/rest/v1/{ctx_table}',
                         headers=H_READ, params=params, timeout=30).json()
        if not isinstance(r, list) or not r: break
        ctx_all += r
        if len(r) < 500: break
        offset += 500
    print(f'  {len(ctx_all)} {sport} game contexts available')

    if not ctx_all:
        print(f'  [{sport}] no context data — skip'); return

    # Try game_id join first (works for MLB), fall back to natural key join
    # (works for NFL / NCAAF / NCAAB where ID formats differ between tables).
    gids = [c['game_id'] for c in ctx_all if isinstance(c, dict) and c.get('game_id')]
    results = {}
    for i in range(0, len(gids), 100):
        chunk = gids[i:i+100]
        in_clause = ','.join(f'"{g}"' for g in chunk)
        try:
            r = requests.get(f'{SB}/rest/v1/{res_table}',
                             headers=H_READ,
                             params={'game_id': f'in.({in_clause})',
                                     'select': 'game_id,home_score,away_score,run_line_result,total_result,home_team,away_team,game_date',
                                     'limit': '500'}, timeout=15).json()
            if isinstance(r, dict) and r.get('code') == '42703':
                r = requests.get(f'{SB}/rest/v1/{res_table}',
                                 headers=H_READ,
                                 params={'game_id': f'in.({in_clause})',
                                         'select': 'game_id,home_score,away_score,home_team,away_team,game_date',
                                         'limit': '500'}, timeout=15).json()
        except Exception:
            r = []
        for x in (r if isinstance(r, list) else []):
            results[x['game_id']] = x

    # Natural-key fallback: for context rows without a game_id match, look up
    # by (game_date, home_team, away_team). Team names normalized to lower to
    # forgive case differences (NFL uses 'DAL', context might use 'DAL' too but
    # NCAAF/NCAAB could differ).
    orphans = [c for c in ctx_all if isinstance(c, dict) and c.get('game_id') not in results
               and c.get('home_team') and c.get('away_team') and c.get('game_date')]
    if orphans:
        print(f'  [{sport}] {len(orphans)} contexts without game_id match — trying natural-key join')
        # Fetch results by date-range covering all orphan dates
        dates = sorted({c['game_date'] for c in orphans})
        # Chunk date query to avoid huge IN clause
        by_natural = {}
        for i in range(0, len(dates), 30):
            date_chunk = dates[i:i+30]
            in_dates = ','.join(f'"{d}"' for d in date_chunk)
            try:
                r = requests.get(f'{SB}/rest/v1/{res_table}',
                                 headers=H_READ,
                                 params={'game_date': f'in.({in_dates})',
                                         'select': 'game_id,home_score,away_score,run_line_result,total_result,home_team,away_team,game_date',
                                         'limit': '2000'}, timeout=20).json()
                if isinstance(r, dict) and r.get('code') == '42703':
                    r = requests.get(f'{SB}/rest/v1/{res_table}',
                                     headers=H_READ,
                                     params={'game_date': f'in.({in_dates})',
                                             'select': 'game_id,home_score,away_score,home_team,away_team,game_date',
                                             'limit': '2000'}, timeout=20).json()
                for x in (r if isinstance(r, list) else []):
                    k = (str(x.get('game_date')), (x.get('home_team') or '').upper(), (x.get('away_team') or '').upper())
                    by_natural[k] = x
            except Exception: pass
        matched = 0
        for c in orphans:
            k = (str(c['game_date']), (c['home_team'] or '').upper(), (c['away_team'] or '').upper())
            if k in by_natural:
                results[c['game_id']] = by_natural[k]
                matched += 1
        print(f'  [{sport}] natural-key matched {matched}/{len(orphans)} orphans')

    # Buckets: (tier, market, direction) → stats
    buckets = defaultdict(lambda: {'w':0, 'n':0, 'push':0, 'odds_sum':0, 'odds_n':0})

    for c in ctx_all:
        if not isinstance(c, dict): continue
        res = results.get(c['game_id'])
        if not res or res.get('home_score') is None: continue
        hs, as_ = res['home_score'], res['away_score']

        pp = c.get('primary_play') or {}
        if not isinstance(pp, dict): continue
        tier = pp.get('tier'); ptype = (pp.get('type') or '').lower(); label = pp.get('label') or ''
        if not tier or not ptype: continue

        # Determine direction + outcome (uses sport's home_ml column via fmap)
        if ptype == 'ml':
            market = 'ML'
            direction = 'HOME' if 'home' in label.lower() or (c.get(home_ml_col) and str(c.get(home_ml_col)) in label) else 'AWAY'
            # Better: extract from label text — for now, just check if home_team in label
            # Fall back: use sign of home_ml_close
            # Match by team hint
            if hs == as_: outcome = 'push'
            elif direction == 'HOME': outcome = 'W' if hs > as_ else 'L'
            else: outcome = 'W' if as_ > hs else 'L'
            odds = american_to_decimal(c.get(home_ml_col) if direction=='HOME' else c.get(away_ml_col))
        elif ptype == 'spread':
            # NFL / NCAAF / NBA / NCAAB use spread as the primary side market
            market = 'SPREAD'
            direction = 'HOME' if 'home' in label.lower() or (c.get(spread_col) is not None and c.get(spread_col) < 0 and 'home' in label.lower()) else 'AWAY'
            line = c.get(spread_col)
            if line is None: continue
            try: line = float(line)
            except (ValueError, TypeError): continue
            # spread cover: direction team's margin + line > 0
            margin_picked = (hs - as_) if direction == 'HOME' else (as_ - hs)
            if margin_picked + line == 0: outcome = 'push'
            elif margin_picked + line > 0: outcome = 'W'
            else: outcome = 'L'
            odds = 1.91  # standard -110 on spreads
            k = (tier, market, direction)
            buckets[k]['n'] += 1
            if outcome == 'W': buckets[k]['w'] += 1
            elif outcome == 'push': buckets[k]['push'] += 1
            if odds: buckets[k]['odds_sum'] += odds; buckets[k]['odds_n'] += 1
            continue
        elif ptype == 'total':
            market = 'TOTAL'
            sub = (pp.get('sub') or '').lower()
            direction = 'OVER' if 'over' in sub or 'over' in label.lower() else 'UNDER'
            line = c.get(total_col)
            if line is None: continue
            try: line = float(line)
            except: continue
            total = hs + as_
            if total == line: outcome = 'push'
            elif direction == 'OVER': outcome = 'W' if total > line else 'L'
            else: outcome = 'W' if total < line else 'L'
            odds = 1.91  # standard -110
        elif ptype == 'rl':
            market = 'RL'
            direction = 'HOME' if 'home' in label.lower() else 'AWAY'
            rl_res = (res.get('run_line_result') or '').lower()
            outcome = 'W' if (direction == 'HOME' and rl_res == 'home') or (direction == 'AWAY' and rl_res == 'away') else 'L'
            odds = 1.91
        else:
            continue

        k = (tier, market, direction)
        buckets[k]['n'] += 1
        if outcome == 'W': buckets[k]['w'] += 1
        elif outcome == 'push': buckets[k]['push'] += 1
        if odds:
            buckets[k]['odds_sum'] += odds; buckets[k]['odds_n'] += 1

    written = 0
    print(f'\n{"tier":<10} {"mkt":<7} {"dir":<6} {"W-L":<9} {"hit%":<7} {"ROI":<9} {"hint":<8} n')
    for (tier, market, direction), v in sorted(buckets.items(), key=lambda x: -x[1]['n']):
        n = v['n']
        if n < 5: continue
        wins = v['w']; losses = n - v['w'] - v['push']
        graded = wins + losses
        if graded == 0: continue
        hit_rate = wins / graded
        avg_dec = v['odds_sum'] / v['odds_n'] if v['odds_n'] else None
        roi = 100 * (hit_rate * (avg_dec - 1) - (1 - hit_rate)) if avg_dec else None
        hint, conf = compute_hint(hit_rate * 100, roi, n)
        payload = {
            'sport': sport, 'tier': tier, 'market': market, 'direction': direction,
            'bucket_window': window,
            'wins': wins, 'losses': losses, 'pushes': v['push'], 'sample_n': n,
            'hit_rate': round(hit_rate * 100, 1),
            'avg_decimal_odds': round(avg_dec, 3) if avg_dec else None,
            'roi_pct': round(roi, 1) if roi is not None else None,
            'jerry_hint': hint, 'hint_confidence': conf,
            'computed_at': datetime.now(timezone.utc).isoformat(),
        }
        r = requests.post(
            f'{SB}/rest/v1/game_bucket_roi?on_conflict=sport,tier,market,direction,bucket_window',
            headers=H_WRITE, json=payload, timeout=15)
        if r.status_code in (200, 201, 204):
            written += 1
            roi_s = f'{roi:+.1f}%' if roi is not None else '   -   '
            print(f'  {tier[:9]:<10} {market:<7} {direction[:5]:<6} {wins:>3}-{losses:<3}  '
                  f'{hit_rate*100:>5.1f}%  {roi_s:<9} {hint:<8} n={n}')
        else:
            print(f'  ⚠ upsert {r.status_code}: {r.text[:120]}')
    print(f'\n=== wrote {written} game bucket rows ===')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--window', default='lifetime', choices=['lifetime', '90d', '30d'])
    p.add_argument('--sport', default='ALL')
    args = p.parse_args()
    sports = list(CONTEXT_TABLE.keys()) if args.sport == 'ALL' else [args.sport]
    for s in sports:
        compute_sport(s, window=args.window)
