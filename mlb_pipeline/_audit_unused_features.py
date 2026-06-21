"""Test each currently-unused mlb_game_context feature for predictive lift.

We store 212 columns but only ~40 feed the sweat dim. The rest sit on disk
unused. For each candidate feature, define a "loud" cohort + a directional
prediction, then check hit rate vs baseline over last 90d.

Promotion gate: hit rate >= 58% on n >= 30 → write a sweat dim driver.
Drop gate: hit rate < 55% on n >= 30 → leave it dead, don't burn complexity.

Features tested:
  catcher_framing — does home/away framing edge predict K or run suppression?
  lineup_ops — does today's confirmed lineup OPS predict score better than
              season team OPS?
  line_movement — does sharp $ moving the line predict the picked side?
  platoon_advantage — explicit L/R flag → predict offense lift?
  consecutive_road_games — road-trip fatigue → AWAY underperforms?
  injury_count — depth disadvantage → side picks?
  timezone_change — jet lag → AWAY underperforms?
  L5 / L20 form (vs L10) — do shorter/longer windows beat L10?
  lineup_weight — composite lineup quality
"""
import os
from collections import Counter
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()
SU = os.environ['SUPABASE_URL']
SK = os.environ['SUPABASE_KEY']
H = {'apikey': SK, 'Authorization': f'Bearer {SK}'}

MIN_N = 25
PROMOTE = 0.58
DROP = 0.55


def pull(days=120):
    since = (date.today() - timedelta(days=days)).isoformat()
    # Pull every field we want to test against
    # Note: dropped fields not in mlb_game_results:
    #   away_lineup_ops, home_lineup_ops (only in mlb_game_context — live state)
    #   line_movement, line_movements_count (only in context)
    #   last20 variants (only in context)
    sel = ('game_date,home_score,away_score,close_total,close_spread,'
           'home_ml_close,away_ml_close,'
           'away_catcher_framing,home_catcher_framing,'
           'away_lineup_weight,home_lineup_weight,'
           'away_platoon_advantage,home_platoon_advantage,'
           'away_ops_vs_opp_hand,home_ops_vs_opp_hand,'
           'away_consecutive_road_games,timezone_change,'
           'home_travel_distance_last_game,'
           'away_injury_count,home_injury_count,'
           'away_last5_run_diff,home_last5_run_diff,'
           'away_last10_runs_per_game,home_last10_runs_per_game,'
           'away_wrc_plus,home_wrc_plus')
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


def fl(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def report(label, cohort_w, cohort_l, baseline_w, baseline_l, gate_hint):
    """Pretty-print a single cohort test."""
    cn = cohort_w + cohort_l
    bn = baseline_w + baseline_l
    if cn < MIN_N:
        print(f'  {label:>50s}: n={cn} too thin')
        return None
    cr = cohort_w / cn
    br = baseline_w / max(1, bn)
    lift = (cr - br) * 100
    badge = '🔥' if abs(lift) >= 5 else ('✓' if abs(lift) >= 3 else '·')
    verdict = ''
    if cr >= PROMOTE: verdict = ' → PROMOTE (wire as driver)'
    elif cr < DROP: verdict = ' → DROP (no signal)'
    print(f'  {label:>50s}: {cohort_w}-{cohort_l} ({cr*100:.0f}%, n={cn}) vs base {br*100:.0f}% (lift {lift:+.1f}pt) {badge}{verdict}  [{gate_hint}]')
    return cr


def main():
    games = pull(120)
    print(f'pulled {len(games)} graded games')
    print()

    # baseline rates
    base_home_wins = sum(1 for g in games if g['home_score'] > g['away_score'])
    base_total_n = sum(1 for g in games if g.get('close_total') and (g['home_score'] + g['away_score']) != g['close_total'])
    base_over = sum(1 for g in games
                    if g.get('close_total')
                    and (g['home_score'] + g['away_score']) > g['close_total'])
    print(f'Baseline: HOME wins {base_home_wins}/{len(games)} ({100*base_home_wins/len(games):.1f}%) | '
          f'OVER hits {base_over}/{base_total_n} ({100*base_over/max(1,base_total_n):.1f}%)')
    print()

    # ──────────── 1. CATCHER FRAMING ────────────
    print('=== 1. CATCHER FRAMING (home edge → does home pitcher suppress runs?) ===')
    # Cohort: HOME catcher framing significantly better than AWAY (gap >= some threshold)
    # Prediction: HOME pitcher gets more Ks → total UNDER more likely
    for gap in (3, 5, 8):
        cw = cl = bw = bl = 0
        for g in games:
            line = g.get('close_total')
            hc = fl(g.get('home_catcher_framing'))
            ac = fl(g.get('away_catcher_framing'))
            actual = g['home_score'] + g['away_score']
            if line is None or actual == line: continue
            actual_dir = 'O' if actual > line else 'U'
            bw += 1 if actual_dir == 'U' else 0
            bl += 1 if actual_dir == 'O' else 0
            if hc is None or ac is None: continue
            if (hc - ac) < gap: continue
            # Predict UNDER (home framer suppresses)
            if actual_dir == 'U': cw += 1
            else: cl += 1
        report(f'home_framing - away >= {gap} → UNDER', cw, cl, bw, bl, 'TOTAL UNDER')
    print()

    # ──────────── 2. LINEUP WEIGHT ────────────
    print('=== 2. LINEUP WEIGHT (composite lineup quality) ===')
    for threshold in (0.95, 1.00, 1.05):
        cw = cl = bw = bl = 0
        for g in games:
            line = g.get('close_total')
            aw = fl(g.get('away_lineup_weight')); hw = fl(g.get('home_lineup_weight'))
            actual = g['home_score'] + g['away_score']
            if line is None or actual == line: continue
            actual_dir = 'O' if actual > line else 'U'
            bw += 1 if actual_dir == 'O' else 0
            bl += 1 if actual_dir == 'U' else 0
            if aw is None or hw is None: continue
            if aw < threshold or hw < threshold: continue
            if actual_dir == 'O': cw += 1
            else: cl += 1
        report(f'both lineup_weight >= {threshold} → OVER', cw, cl, bw, bl, 'TOTAL OVER')
    print()

    # ──────────── 4. PLATOON ADVANTAGE ────────────
    print('=== 4. PLATOON ADVANTAGE (explicit L/R flag) ===')
    cw = cl = bw = bl = 0
    for g in games:
        pa = g.get('away_platoon_advantage')
        actual_ml = 'H' if g['home_score'] > g['away_score'] else 'A'
        bw += 1 if actual_ml == 'A' else 0
        bl += 1 if actual_ml == 'H' else 0
        if not pa: continue
        if actual_ml == 'A': cw += 1
        else: cl += 1
    report('away_platoon_advantage → AWAY ML', cw, cl, bw, bl, 'side')
    cw = cl = bw = bl = 0
    for g in games:
        pa = g.get('home_platoon_advantage')
        actual_ml = 'H' if g['home_score'] > g['away_score'] else 'A'
        bw += 1 if actual_ml == 'H' else 0
        bl += 1 if actual_ml == 'A' else 0
        if not pa: continue
        if actual_ml == 'H': cw += 1
        else: cl += 1
    report('home_platoon_advantage → HOME ML', cw, cl, bw, bl, 'side')
    print()

    # ──────────── 5. CONSECUTIVE ROAD GAMES (away fatigue) ────────────
    print('=== 5. CONSECUTIVE ROAD GAMES (away fatigue) ===')
    for thresh in (5, 6, 8, 10):
        cw = cl = bw = bl = 0
        for g in games:
            crg = fl(g.get('away_consecutive_road_games'))
            actual_ml = 'H' if g['home_score'] > g['away_score'] else 'A'
            bw += 1 if actual_ml == 'H' else 0
            bl += 1 if actual_ml == 'A' else 0
            if crg is None or crg < thresh: continue
            if actual_ml == 'H': cw += 1
            else: cl += 1
        report(f'away_consecutive_road >= {thresh} → HOME ML', cw, cl, bw, bl, 'side')
    print()

    # ──────────── 6. INJURY COUNT ────────────
    print('=== 6. INJURY COUNT (depth signal) ===')
    for thresh in (3, 5, 7):
        cw = cl = bw = bl = 0
        for g in games:
            aic = fl(g.get('away_injury_count')) or 0
            hic = fl(g.get('home_injury_count')) or 0
            actual_ml = 'H' if g['home_score'] > g['away_score'] else 'A'
            bw += 1 if actual_ml == 'H' else 0
            bl += 1 if actual_ml == 'A' else 0
            # Cohort: AWAY has way more injuries than HOME
            if aic - hic < thresh: continue
            if actual_ml == 'H': cw += 1
            else: cl += 1
        report(f'away_injuries - home >= {thresh} → HOME ML', cw, cl, bw, bl, 'side')
    print()

    # ──────────── 7. TIMEZONE CHANGE ────────────
    print('=== 7. TIMEZONE CHANGE (jet lag) ===')
    cw = cl = bw = bl = 0
    for g in games:
        tz = fl(g.get('timezone_change'))
        actual_ml = 'H' if g['home_score'] > g['away_score'] else 'A'
        bw += 1 if actual_ml == 'H' else 0
        bl += 1 if actual_ml == 'A' else 0
        if tz is None or abs(tz) < 1: continue
        if actual_ml == 'H': cw += 1
        else: cl += 1
    report('any timezone change (|tz|>=1) → HOME ML', cw, cl, bw, bl, 'side')
    print()

    # ──────────── 8. L5 RUN-DIFF FORM ────────────
    print('=== 8. L5 RUN-DIFF (hot/cold short-window form) ===')
    for thresh in (1.0, 1.5, 2.0):
        cw = cl = bw = bl = 0
        for g in games:
            actual_ml = 'H' if g['home_score'] > g['away_score'] else 'A'
            bw += 1 if actual_ml == 'H' else 0
            bl += 1 if actual_ml == 'A' else 0
            arf = fl(g.get('away_last5_run_diff'))
            hrf = fl(g.get('home_last5_run_diff'))
            if arf is None or hrf is None: continue
            # Cohort: HOME run-diff much hotter than AWAY → HOME ML
            if hrf - arf < thresh: continue
            if actual_ml == 'H': cw += 1
            else: cl += 1
        report(f'home_l5_rd - away_l5_rd >= {thresh} → HOME ML', cw, cl, bw, bl, 'side')
    print()
    # L10 combined r/g
    cw = cl = bw = bl = 0
    for g in games:
        line = g.get('close_total')
        a_rpg = fl(g.get('away_last10_runs_per_game'))
        h_rpg = fl(g.get('home_last10_runs_per_game'))
        actual = g['home_score'] + g['away_score']
        if line is None or actual == line: continue
        actual_dir = 'O' if actual > line else 'U'
        bw += 1 if actual_dir == 'O' else 0
        bl += 1 if actual_dir == 'U' else 0
        if a_rpg is None or h_rpg is None: continue
        combined = a_rpg + h_rpg
        if combined - line < 1.5: continue
        if actual_dir == 'O': cw += 1
        else: cl += 1
    report('l10 combined r/g - line >= 1.5 → OVER', cw, cl, bw, bl, 'TOTAL OVER')

    # ──────────── 9. OPS vs OPP HAND (platoon-aware offense) ────────────
    print()
    print('=== 9. OPS vs OPP HAND (does the hand-aware OPS predict more than vanilla wRC+?) ===')
    for thresh in (0.75, 0.80, 0.85):
        cw = cl = bw = bl = 0
        for g in games:
            line = g.get('close_total')
            ao = fl(g.get('away_ops_vs_opp_hand'))
            ho = fl(g.get('home_ops_vs_opp_hand'))
            actual = g['home_score'] + g['away_score']
            if line is None or actual == line: continue
            actual_dir = 'O' if actual > line else 'U'
            bw += 1 if actual_dir == 'O' else 0
            bl += 1 if actual_dir == 'U' else 0
            if ao is None or ho is None: continue
            if (ao + ho) / 2 < thresh: continue
            if actual_dir == 'O': cw += 1
            else: cl += 1
        report(f'avg OPS vs opp hand >= {thresh} → OVER', cw, cl, bw, bl, 'TOTAL OVER')


if __name__ == '__main__':
    main()
