"""NRFI counter-pattern deep dive.

Finding from _audit_compound_patterns.py:
  NRFI score >= 85 + both pitchers elite (xERA <= 3.8) → UNDER fails 42%
  (n=102, -6.1pt lift). OVER hits 58%.

Saved memory project_nrfi_demotion + feedback_no_nrfi_on_cards already
demoted NRFI from POTD/cards. project_may17_nrfi_coors_audit found
NRFI 95+ stratification REJECTED on n=71.

This audit asks the right question:
  When NRFI score is loud, what ACTUALLY happens to the game total?
  And: is the relationship different at NRFI 85 vs 95? Pitcher xERA 3.0
  vs 3.5 vs 3.8? Line band (low/mid/high)?

If a loud finding survives — ship a "NRFI loud → OVER" driver and
explicitly flip the existing NRFI logic.
"""
import os
from collections import defaultdict
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}


def fl(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def pull(days=120):
    since = (date.today() - timedelta(days=days)).isoformat()
    sel = ('game_date,home_score,away_score,close_total,close_spread,'
           'away_sp_xera,home_sp_xera,'
           'away_pitcher_last_3_era,home_pitcher_last_3_era,'
           'away_first_inning_era,home_first_inning_era,'
           'nrfi_score,park_run_factor,temperature,'
           'projected_total,model_pred_total,jerry_pred_total')
    rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SU}/rest/v1/mlb_game_results?game_date=gte.{since}'
            f'&select={sel}&order=game_date.asc&limit=1000&offset={offset}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return [r for r in rows if r.get('home_score') is not None]


def report(label, w_o, w_u, n, base_over):
    if n < 20:
        print(f'  {label:>72s}: n={n} thin')
        return
    o_rate = 100 * w_o / n
    u_rate = 100 * w_u / n
    lift_o = o_rate - base_over
    badge = '🔥' if abs(lift_o) >= 6 else ('✓' if abs(lift_o) >= 3 else '·')
    verdict = ''
    if u_rate >= 60: verdict = ' → SHIP UNDER'
    elif o_rate >= 60: verdict = ' → SHIP OVER'
    elif u_rate >= 58 and n >= 50: verdict = ' → SHIP UNDER'
    elif o_rate >= 58 and n >= 50: verdict = ' → SHIP OVER'
    print(f'  {label:>72s}: O {w_o}-{w_u} ({o_rate:.0f}% over, lift {lift_o:+.1f}pt, n={n}) {badge}{verdict}')


def main():
    games = pull(120)
    print(f'pulled {len(games)} graded games')
    over_eligible = [g for g in games
                     if g.get('close_total')
                     and (g['home_score'] + g['away_score']) != g['close_total']]
    base_over = 100 * sum(1 for g in over_eligible
                          if g['home_score'] + g['away_score'] > g['close_total']) / len(over_eligible)
    print(f'baseline OVER rate: {base_over:.1f}% (n={len(over_eligible)})')
    print()

    # ===== 1. NRFI SCORE BANDS — what does the game total actually do? =====
    print('=== 1. NRFI SCORE × GAME TOTAL DIRECTION ===')
    for lo, hi, label in [(0, 60, '<60'), (60, 70, '60-69'), (70, 80, '70-79'),
                           (80, 85, '80-84'), (85, 90, '85-89'), (90, 95, '90-94'),
                           (95, 100, '95-100')]:
        w_o = w_u = 0; n = 0
        for g in over_eligible:
            nrfi = fl(g.get('nrfi_score'))
            if nrfi is None or nrfi < lo or nrfi >= hi: continue
            actual = g['home_score'] + g['away_score']
            line = g['close_total']
            n += 1
            if actual > line: w_o += 1
            else: w_u += 1
        report(f'NRFI {label}', w_o, w_u, n, base_over)

    # ===== 2. NRFI LOUD + PITCHER QUALITY BANDS =====
    print()
    print('=== 2. NRFI LOUD (>=85) × PITCHER QUALITY (xERA banding) ===')
    for nrfi_thr in (80, 85, 90):
        for xera_cap in (3.0, 3.5, 3.8, 4.2):
            w_o = w_u = 0
            for g in over_eligible:
                nrfi = fl(g.get('nrfi_score'))
                axe = fl(g.get('away_sp_xera')); hxe = fl(g.get('home_sp_xera'))
                if None in (nrfi, axe, hxe): continue
                if nrfi < nrfi_thr: continue
                if axe > xera_cap or hxe > xera_cap: continue
                actual = g['home_score'] + g['away_score']
                line = g['close_total']
                if actual > line: w_o += 1
                else: w_u += 1
            report(f'NRFI>={nrfi_thr} + both xERA<={xera_cap}', w_o, w_u, w_o+w_u, base_over)

    # ===== 3. NRFI LOUD + LINE BAND =====
    print()
    print('=== 3. NRFI LOUD (>=85) × LINE BAND ===')
    for nrfi_thr in (80, 85, 90):
        for lo_l, hi_l, lbl in [(0, 7.5, 'low (<=7.5)'), (7.5, 9.0, 'mid (7.5-9)'),
                                  (9.0, 99, 'high (>=9)')]:
            w_o = w_u = 0
            for g in over_eligible:
                nrfi = fl(g.get('nrfi_score'))
                line = g['close_total']
                if nrfi is None: continue
                if nrfi < nrfi_thr: continue
                if line < lo_l or line >= hi_l: continue
                actual = g['home_score'] + g['away_score']
                if actual > line: w_o += 1
                else: w_u += 1
            report(f'NRFI>={nrfi_thr} + line {lbl}', w_o, w_u, w_o+w_u, base_over)

    # ===== 4. NRFI LOUD + FIRST-INNING ERA REALITY CHECK =====
    print()
    print('=== 4. NRFI LOUD × ACTUAL 1st-INN ERA OF BOTH STARTERS ===')
    for nrfi_thr in (85, 90):
        for inn_era_cap in (2.0, 3.0, 4.0):
            w_o = w_u = 0
            for g in over_eligible:
                nrfi = fl(g.get('nrfi_score'))
                a_inn = fl(g.get('away_first_inning_era'))
                h_inn = fl(g.get('home_first_inning_era'))
                if None in (nrfi, a_inn, h_inn): continue
                if nrfi < nrfi_thr: continue
                if a_inn > inn_era_cap or h_inn > inn_era_cap: continue
                actual = g['home_score'] + g['away_score']
                line = g['close_total']
                if actual > line: w_o += 1
                else: w_u += 1
            report(f'NRFI>={nrfi_thr} + both 1st-inn ERA<={inn_era_cap}', w_o, w_u, w_o+w_u, base_over)

    # ===== 5. NRFI LOUD + COLD/HOT PARK =====
    print()
    print('=== 5. NRFI LOUD × PARK FACTOR ===')
    for nrfi_thr in (85, 90):
        for park_lo, park_hi, lbl in [(0, 95, 'pitcher (<95)'),
                                        (95, 105, 'neutral (95-105)'),
                                        (105, 999, 'hitter (>=105)')]:
            w_o = w_u = 0
            for g in over_eligible:
                nrfi = fl(g.get('nrfi_score'))
                prf = fl(g.get('park_run_factor')) or 100
                if nrfi is None: continue
                if nrfi < nrfi_thr: continue
                if prf < park_lo or prf >= park_hi: continue
                actual = g['home_score'] + g['away_score']
                line = g['close_total']
                if actual > line: w_o += 1
                else: w_u += 1
            report(f'NRFI>={nrfi_thr} + park {lbl}', w_o, w_u, w_o+w_u, base_over)

    # ===== 6. Counter test: does the existing v3 OVER model do BETTER or WORSE
    #          on NRFI-loud games? =====
    print()
    print('=== 6. NRFI LOUD × v3 model direction (does v3 know NRFI is wrong?) ===')
    for nrfi_thr in (85, 90):
        for v3_dir, dir_label in (('over', 'OVER'), ('under', 'UNDER')):
            w = l = 0
            for g in over_eligible:
                nrfi = fl(g.get('nrfi_score'))
                pt = fl(g.get('projected_total'))
                line = g['close_total']
                if None in (nrfi, pt): continue
                if nrfi < nrfi_thr: continue
                delta = pt - line
                if v3_dir == 'over' and delta <= 0.3: continue
                if v3_dir == 'under' and delta >= -0.3: continue
                actual = g['home_score'] + g['away_score']
                actual_dir = 'over' if actual > line else 'under'
                if actual_dir == v3_dir: w += 1
                else: l += 1
            print(f'  NRFI>={nrfi_thr} + v3 picks {dir_label}: {w}-{l} ({100*w/max(1,w+l):.0f}%, n={w+l})')


if __name__ == '__main__':
    main()
