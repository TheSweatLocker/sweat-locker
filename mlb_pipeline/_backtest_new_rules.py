"""Walk-forward backtest of TODAY's new rules vs baseline.

What we're testing — for each day in last 30d:
  1. v5_ml prediction at STRONG-tier (|p-.5|>=.10)
  2. v5_total prediction at STRONG-tier
  3. v3+v4+jerry+v5 4-way unanimous on totals
  4. v5 dissent from v3+v4 ML consensus → fade rule
  5. Composite rank: v5 confidence + cohort net + 4-way bonus

Versus historical POTD picks (daily_best_bet_history): 4-7-3 (36%) last 14d.

The new rules are validated independently (each cohort hits 60-78%).
This backtest is the SYSTEM test: when we combine them into a single
pick per day, does the combined system beat the existing 36%?
"""
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from v5_inference import predict_ml, predict_total

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

BACKTEST_DAYS = 30


def cohort_net_total(g):
    """Compute the same NET cohort signal we ship in production for totals."""
    try:
        from cohort_signals import evaluate_game_for_play
    except Exception:
        return 0, None
    seen = set()
    o = u = 0
    for play in ('v3_tot', 'v4_tot', 'jerry_tot'):
        for rule in evaluate_game_for_play(g, play, direction=None):
            rid = rule.get('id', '')
            if rid in seen or rule.get('tier') not in ('LOCK', 'STRONG_EDGE'):
                continue
            if (rule.get('last30_n') or 0) < 15:
                continue
            rd = (rule.get('direction') or '').lower()
            if rd not in ('over', 'under'):
                continue
            seen.add(rid)
            pts = 10 if rule.get('tier') == 'LOCK' else 5
            if rd == 'over':
                o += pts
            else:
                u += pts
    diff = o - u
    net_dir = 'O' if diff > 0 else ('U' if diff < 0 else None)
    return min(10, abs(diff) // 8), net_dir


def model_dir(p, line, eps=0.3):
    if p is None or line is None:
        return None
    try:
        pf = float(p); lf = float(line)
    except (TypeError, ValueError):
        return None
    if pf > lf + eps: return 'O'
    if pf < lf - eps: return 'U'
    return None


def sp_dir(p):
    if p is None: return None
    try: v = float(p)
    except (TypeError, ValueError): return None
    return 'H' if v < 0 else ('A' if v > 0 else None)


def main():
    since = (date.today() - timedelta(days=BACKTEST_DAYS + 5)).isoformat()
    sel = ('game_date,home_score,away_score,close_total,close_spread,'
           'projected_total,model_pred_total,jerry_pred_total,'
           'projected_spread,model_pred_spread,jerry_pred_spread,'
           'signal_confluence_net,nrfi_score,'
           'away_sp_xera,home_sp_xera,away_pitcher_last_3_era,home_pitcher_last_3_era,'
           'away_bullpen_era,home_bullpen_era,away_wrc_plus,home_wrc_plus,'
           'away_ops_last7,home_ops_last7,away_team_k_pct,home_team_k_pct,'
           'park_run_factor,temperature,'
           'home_ml_close,away_ml_close,away_team,home_team')
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?game_date=gte.{since}&select={sel}'
            f'&order=game_date.asc&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    games = [g for g in rows if g.get('home_score') is not None]
    print(f'pulled {len(games)} graded games')

    # === Rule 1: v5_ml STRONG-tier (|p-.5|>=.10) ===
    print()
    print('=== RULE 1: v5_ml STRONG-tier picks ===')
    w = l = 0
    for g in games:
        p_ml = predict_ml(g)
        if p_ml is None or abs(p_ml - 0.5) < 0.10:
            continue
        pick = 'H' if p_ml >= 0.5 else 'A'
        actual = 'H' if g['home_score'] > g['away_score'] else 'A'
        if pick == actual: w += 1
        else: l += 1
    print(f'  Record: {w}-{l} ({100*w/max(1,w+l):.1f}%, n={w+l})')

    # === Rule 2: v5_total STRONG-tier ===
    print()
    print('=== RULE 2: v5_total STRONG-tier picks ===')
    w = l = 0
    for g in games:
        p_tot = predict_total(g)
        line = g.get('close_total')
        if p_tot is None or line is None or abs(p_tot - 0.5) < 0.10:
            continue
        actual = g['home_score'] + g['away_score']
        if actual == line: continue
        pick = 'O' if p_tot >= 0.5 else 'U'
        actual_dir = 'O' if actual > line else 'U'
        if pick == actual_dir: w += 1
        else: l += 1
    print(f'  Record: {w}-{l} ({100*w/max(1,w+l):.1f}%, n={w+l})')

    # === Rule 3: 4-way unanimous TOTAL ===
    print()
    print('=== RULE 3: v3+v4+jerry+v5 4-way unanimous TOTAL ===')
    w = l = 0
    for g in games:
        line = g.get('close_total')
        if line is None: continue
        actual = g['home_score'] + g['away_score']
        if actual == line: continue
        v3 = model_dir(g.get('projected_total'), line)
        v4 = model_dir(g.get('model_pred_total'), line)
        jr = model_dir(g.get('jerry_pred_total'), line)
        p_tot = predict_total(g)
        v5 = 'O' if (p_tot is not None and p_tot >= 0.5) else ('U' if p_tot is not None else None)
        if not (v3 and v4 and jr and v5): continue
        if not (v3 == v4 == jr == v5): continue
        pick = v3
        actual_dir = 'O' if actual > line else 'U'
        if pick == actual_dir: w += 1
        else: l += 1
    print(f'  Record: {w}-{l} ({100*w/max(1,w+l):.1f}%, n={w+l})')

    # === Rule 4: v5-dissent FADE on v3+v4 ML consensus ===
    print()
    print('=== RULE 4: v5-dissent on v3+v4 ML — FADE the v3+v4 pick ===')
    w = l = 0
    for g in games:
        v3 = sp_dir(g.get('projected_spread'))
        v4 = sp_dir(g.get('model_pred_spread'))
        p_ml = predict_ml(g)
        if v3 is None or v4 is None or p_ml is None: continue
        if v3 != v4: continue
        v5 = 'H' if p_ml >= 0.5 else 'A'
        if v5 == v3: continue  # need dissent
        if abs(p_ml - 0.5) < 0.05: continue  # need LEAN+ confidence
        # FADE v3+v4 means pick v5's side
        pick = v5
        actual = 'H' if g['home_score'] > g['away_score'] else 'A'
        if pick == actual: w += 1
        else: l += 1
    print(f'  Record: {w}-{l} ({100*w/max(1,w+l):.1f}%, n={w+l})')

    # === Rule 5: Composite — best pick per day using new signals ===
    print()
    print('=== RULE 5: COMPOSITE pick-of-day per game ===')
    # For each day, find game with best (v5 confidence + cohort net + 4-way match) score
    by_day = defaultdict(list)
    for g in games:
        by_day[g['game_date']].append(g)
    composite_w = composite_l = composite_push = 0
    composite_picks = []
    for day, day_games in sorted(by_day.items()):
        best = None
        best_score = -1
        best_pick = None  # ('M', 'H'/'A') or ('T', 'O'/'U')
        for g in day_games:
            line = g.get('close_total')
            actual = g['home_score'] + g['away_score']
            # v5_ml signal
            p_ml = predict_ml(g)
            ml_score = 0
            if p_ml is not None:
                ml_score = abs(p_ml - 0.5) * 100  # 0-50 scale
            # v5_total signal
            p_tot = predict_total(g)
            tot_score = 0
            if p_tot is not None:
                tot_score = abs(p_tot - 0.5) * 100
            # 4-way unanimous bonus
            v3 = model_dir(g.get('projected_total'), line) if line else None
            v4 = model_dir(g.get('model_pred_total'), line) if line else None
            jr = model_dir(g.get('jerry_pred_total'), line) if line else None
            v5_t = 'O' if (p_tot and p_tot >= 0.5) else ('U' if p_tot else None)
            four_way = (v3 and v4 and jr and v5_t and v3 == v4 == jr == v5_t)
            # Cohort net
            cohort_pts, cohort_dir = cohort_net_total(g)
            # Score each direction
            if four_way and cohort_dir == v3:
                t_score = tot_score + cohort_pts * 5 + 20
                t_pick = ('T', v3, line, actual)
                if t_score > best_score:
                    best_score = t_score
                    best_pick = t_pick
            elif p_tot is not None and abs(p_tot - 0.5) >= 0.10:
                pick_dir = 'O' if p_tot >= 0.5 else 'U'
                t_score = tot_score + (cohort_pts * 3 if cohort_dir == pick_dir else 0)
                if t_score > best_score:
                    best_score = t_score
                    best_pick = ('T', pick_dir, line, actual)
            if p_ml is not None and abs(p_ml - 0.5) >= 0.10:
                ml_pick = 'H' if p_ml >= 0.5 else 'A'
                m_score = ml_score + 5
                if m_score > best_score:
                    best_score = m_score
                    best_pick = ('M', ml_pick, g['home_score'], g['away_score'])
        if best_pick is None:
            continue
        if best_pick[0] == 'T':
            _, pdir, line, total_actual = best_pick
            if line is None or total_actual is None:
                continue  # bad data — skip
            if total_actual == line:
                composite_push += 1
                continue
            actual_dir = 'O' if total_actual > line else 'U'
            if pdir == actual_dir: composite_w += 1
            else: composite_l += 1
        else:
            _, pdir, hs, as_ = best_pick
            actual_ml = 'H' if hs > as_ else 'A'
            if pdir == actual_ml: composite_w += 1
            else: composite_l += 1
        composite_picks.append((day, best_pick))
    n = composite_w + composite_l
    print(f'  Composite POTD record: {composite_w}-{composite_l}-{composite_push} ({100*composite_w/max(1,n):.1f}%)')
    print(f'  Days with a confident pick: {len(composite_picks)} / {len(by_day)}')

    # === Baseline: actual POTD record (from daily_best_bet_history) ===
    print()
    print('=== BASELINE: actual POTD ===')
    r = requests.get(
        f'{SU}/rest/v1/daily_best_bet_history?bet_date=gte.{since}&select=bet_date,result',
        headers=H, timeout=15,
    )
    potd = r.json() if r.status_code == 200 else []
    pw = sum(1 for p in potd if p.get('result') == 'Win')
    pl = sum(1 for p in potd if p.get('result') == 'Loss')
    pp = sum(1 for p in potd if p.get('result') == 'Push')
    pn = pw + pl
    print(f'  Actual POTD: {pw}-{pl}-{pp} ({100*pw/max(1,pn):.1f}%) over last {BACKTEST_DAYS}d')

    # === Summary ===
    print()
    print('=== SUMMARY ===')
    print(f'  Rule 1 (v5_ml STRONG):       see above')
    print(f'  Rule 2 (v5_total STRONG):    see above')
    print(f'  Rule 3 (4-way unanimous):    see above')
    print(f'  Rule 4 (v5-dissent fade):    see above')
    print(f'  Rule 5 (composite POTD):     {composite_w}-{composite_l} ({100*composite_w/max(1,n):.1f}%) vs baseline POTD {100*pw/max(1,pn):.1f}%')


if __name__ == '__main__':
    main()
