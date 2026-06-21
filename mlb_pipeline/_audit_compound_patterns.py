"""Compound + interaction-pattern audit.

Individual feature audit (_audit_unused_features.py) found 3 weak-medium
signals. The bigger lifts often live in INTERACTIONS — two-feature
combinations where each is mild but the combo is loud.

Hypotheses tested:
  A. Two ace duels (both xERA<=3.5) + both cold lineups → UNDER
  B. Both bad pitchers (xERA>=4.5) + both warm lineups → OVER
  C. Hot away offense (L7 OPS > 0.78) + bad home starter → away R/G lift
  D. Cold home offense (L7 OPS < 0.65) + ace away starter → UNDER
  E. Compound: away on road trip 5+ AND home favored AND home BP fresh
  F. Bullpen vs starter compound: bad bullpens + early-hook starters → OVER
  G. xERA gap (>=1.5 runs) + lineup ops gap (>=0.10) — compound side
  H. Total line band × first-inning ERA combo → NRFI/YRFI predictions
  I. Day-of-week patterns
  J. NRFI score × pitcher matchup → total UNDER

Gate: hit rate >= 60% on n >= 30 → wire. >= 58% on n >= 50 → wire.
"""
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

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
           'home_ml_close,away_ml_close,'
           'away_sp_xera,home_sp_xera,'
           'away_pitcher_last_3_era,home_pitcher_last_3_era,'
           'away_sp_k_pct,home_sp_k_pct,'
           'away_bullpen_era,home_bullpen_era,'
           'away_bp_relievers_3d,home_bp_relievers_3d,'
           'away_wrc_plus,home_wrc_plus,'
           'away_ops_last7,home_ops_last7,'
           'away_team_k_pct,home_team_k_pct,'
           'away_ops_vs_opp_hand,home_ops_vs_opp_hand,'
           'park_run_factor,temperature,'
           'nrfi_score,signal_confluence_net,'
           'projected_total,model_pred_total,jerry_pred_total,'
           'projected_spread,model_pred_spread,jerry_pred_spread,'
           'away_first_inning_era,home_first_inning_era,'
           'away_consecutive_road_games,'
           'away_injury_count,home_injury_count')
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


MIN_N = 25


def report(label, w, l, base_pct, gate='ANY'):
    n = w + l
    if n < MIN_N:
        print(f'  {label:>62s}: n={n} thin')
        return None
    rate = 100 * w / n
    lift = rate - base_pct
    badge = '🔥' if lift >= 6 else ('✓' if lift >= 3 else '·')
    verdict = ''
    if rate >= 60: verdict = ' → SHIP'
    elif rate >= 58 and n >= 50: verdict = ' → SHIP'
    elif rate < 45: verdict = ' → SHIP FADE'
    print(f'  {label:>62s}: {w}-{l} ({rate:.0f}%, n={n}) lift {lift:+.1f}pt {badge}{verdict}')
    return rate


def main():
    games = pull(120)
    print(f'pulled {len(games)} graded games')
    base_home = sum(1 for g in games if g['home_score'] > g['away_score']) * 100 / len(games)
    over_games = [g for g in games if g.get('close_total') and (g['home_score']+g['away_score']) != g['close_total']]
    base_over = sum(1 for g in over_games if g['home_score']+g['away_score'] > g['close_total']) * 100 / max(1, len(over_games))
    print(f'baselines: HOME {base_home:.1f}% | OVER {base_over:.1f}%\n')

    # ===== A. Ace duel + cold lineups → UNDER =====
    print('=== A. ACE DUEL + COLD LINEUPS → UNDER ===')
    for xera_thr in (3.5, 3.8):
        for ops_thr in (0.70, 0.72, 0.75):
            w = l = 0
            for g in games:
                line = g.get('close_total')
                if line is None: continue
                actual = g['home_score'] + g['away_score']
                if actual == line: continue
                axe = fl(g.get('away_sp_xera')); hxe = fl(g.get('home_sp_xera'))
                aop = fl(g.get('away_ops_last7')); hop = fl(g.get('home_ops_last7'))
                if None in (axe, hxe, aop, hop): continue
                if axe > xera_thr or hxe > xera_thr: continue
                if aop > ops_thr or hop > ops_thr: continue
                if actual < line: w += 1
                else: l += 1
            report(f'both xERA<={xera_thr}, both L7_OPS<={ops_thr} → UNDER', w, l, 100-base_over)

    # ===== B. Bad starters + warm lineups → OVER =====
    print()
    print('=== B. BAD STARTERS + WARM LINEUPS → OVER ===')
    for xera_thr in (4.5, 5.0):
        for ops_thr in (0.75, 0.78):
            w = l = 0
            for g in games:
                line = g.get('close_total')
                if line is None: continue
                actual = g['home_score'] + g['away_score']
                if actual == line: continue
                axe = fl(g.get('away_sp_xera')); hxe = fl(g.get('home_sp_xera'))
                aop = fl(g.get('away_ops_last7')); hop = fl(g.get('home_ops_last7'))
                if None in (axe, hxe, aop, hop): continue
                if axe < xera_thr or hxe < xera_thr: continue
                if aop < ops_thr or hop < ops_thr: continue
                if actual > line: w += 1
                else: l += 1
            report(f'both xERA>={xera_thr}, both L7_OPS>={ops_thr} → OVER', w, l, base_over)

    # ===== C. Hot away + bad home starter → AWAY OFFENSE / OVER =====
    print()
    print('=== C. HOT AWAY OFFENSE vs BAD HOME STARTER ===')
    for ops_thr in (0.78, 0.80):
        for xera_thr in (4.5, 5.0):
            w_o = l_o = w_a = l_a = 0
            for g in games:
                line = g.get('close_total')
                aop = fl(g.get('away_ops_last7')); hxe = fl(g.get('home_sp_xera'))
                if aop is None or hxe is None: continue
                if aop < ops_thr or hxe < xera_thr: continue
                if line is None: continue
                actual = g['home_score'] + g['away_score']
                if actual != line:
                    if actual > line: w_o += 1
                    else: l_o += 1
                actual_ml = 'H' if g['home_score'] > g['away_score'] else 'A'
                if actual_ml == 'A': w_a += 1
                else: l_a += 1
            report(f'away_OPS>={ops_thr} + home_xERA>={xera_thr} → OVER', w_o, l_o, base_over)
            report(f'away_OPS>={ops_thr} + home_xERA>={xera_thr} → AWAY ML', w_a, l_a, 100-base_home)

    # ===== D. Cold home + ace away → UNDER =====
    print()
    print('=== D. COLD HOME OFFENSE + ACE AWAY STARTER → UNDER ===')
    for ops_thr in (0.65, 0.70):
        for xera_thr in (3.0, 3.5):
            w = l = 0
            for g in games:
                line = g.get('close_total')
                if line is None: continue
                actual = g['home_score'] + g['away_score']
                if actual == line: continue
                hop = fl(g.get('home_ops_last7')); axe = fl(g.get('away_sp_xera'))
                if hop is None or axe is None: continue
                if hop > ops_thr or axe > xera_thr: continue
                if actual < line: w += 1
                else: l += 1
            report(f'home_OPS<={ops_thr} + away_xERA<={xera_thr} → UNDER', w, l, 100-base_over)

    # ===== E. xERA gap + lineup quality compound (side pick) =====
    print()
    print('=== E. xERA gap + offense gap (compound side pick) ===')
    for xera_gap in (1.0, 1.5):
        for ops_gap in (0.05, 0.10):
            w = l = 0
            for g in games:
                axe = fl(g.get('away_sp_xera')); hxe = fl(g.get('home_sp_xera'))
                aop = fl(g.get('away_ops_last7')); hop = fl(g.get('home_ops_last7'))
                if None in (axe, hxe, aop, hop): continue
                # Cohort: HOME has BOTH better starter AND better lineup
                pitch_edge = axe - hxe  # positive = home pitcher better
                offense_edge = hop - aop  # positive = home offense better
                if pitch_edge < xera_gap or offense_edge < ops_gap: continue
                actual_ml = 'H' if g['home_score'] > g['away_score'] else 'A'
                if actual_ml == 'H': w += 1
                else: l += 1
            report(f'home_pitch_edge>={xera_gap} AND home_offense_edge>={ops_gap} → HOME ML', w, l, base_home)

    # ===== F. Bullpen + early-hook starter combo → OVER =====
    print()
    print('=== F. SHAKY BULLPENS (both >=4.5 ERA) → OVER ===')
    for bp_thr in (4.5, 4.8, 5.0):
        w = l = 0
        for g in games:
            line = g.get('close_total')
            if line is None: continue
            actual = g['home_score'] + g['away_score']
            if actual == line: continue
            abp = fl(g.get('away_bullpen_era')); hbp = fl(g.get('home_bullpen_era'))
            if abp is None or hbp is None: continue
            if abp < bp_thr or hbp < bp_thr: continue
            if actual > line: w += 1
            else: l += 1
        report(f'both BP ERA >= {bp_thr} → OVER', w, l, base_over)

    # ===== G. NRFI score × bothpitcher quality =====
    print()
    print('=== G. NRFI SCORE BAND × pitcher quality → first-inning predictability ===')
    # NRFI score = our internal NRFI prediction. Test against actual first-inning runs.
    # We don't have 1st-inning scores directly but proxy: NRFI 90+ + both pitchers elite
    for nrfi_thr in (85, 90, 95):
        w = l = 0
        for g in games:
            line = g.get('close_total')
            if line is None: continue
            actual = g['home_score'] + g['away_score']
            if actual == line: continue
            nrfi = fl(g.get('nrfi_score'))
            axe = fl(g.get('away_sp_xera')); hxe = fl(g.get('home_sp_xera'))
            if nrfi is None or axe is None or hxe is None: continue
            if nrfi < nrfi_thr: continue
            if axe > 3.8 or hxe > 3.8: continue
            if actual < line: w += 1
            else: l += 1
        report(f'NRFI score>={nrfi_thr} + both xERA<=3.8 → UNDER', w, l, 100-base_over)

    # ===== H. Day-of-week pattern =====
    print()
    print('=== H. DAY OF WEEK (HOME ML rate by weekday) ===')
    by_dow = defaultdict(lambda: [0, 0])
    for g in games:
        try:
            d = datetime.fromisoformat(g['game_date']).strftime('%A')
        except Exception:
            continue
        actual_ml = 'H' if g['home_score'] > g['away_score'] else 'A'
        if actual_ml == 'H': by_dow[d][0] += 1
        else: by_dow[d][1] += 1
    for d in ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']:
        w, l = by_dow.get(d, [0, 0])
        n = w + l
        if n < 20: continue
        rate = 100 * w / n
        lift = rate - base_home
        badge = '🔥' if abs(lift) >= 5 else '·'
        print(f'  {d:>12s}: HOME {w}-{l} ({rate:.0f}%, n={n}) lift {lift:+.1f}pt {badge}')

    # ===== I. Park run factor × total line band interaction =====
    print()
    print('=== I. PARK × LINE BAND interaction (TOTAL hit rate by combo) ===')
    for park_band in [('hitter', lambda p: p and p >= 105),
                       ('neutral', lambda p: p and 95 <= p < 105),
                       ('pitcher', lambda p: p and p < 95)]:
        for line_band in [('low (≤7.5)', lambda l: l and l <= 7.5),
                          ('mid (8-9)', lambda l: l and 8 <= l <= 9),
                          ('high (≥9.5)', lambda l: l and l >= 9.5)]:
            pb_name, pb_fn = park_band
            lb_name, lb_fn = line_band
            w = l = 0
            for g in games:
                line = g.get('close_total')
                if not lb_fn(line): continue
                prf = fl(g.get('park_run_factor'))
                if not pb_fn(prf): continue
                actual = g['home_score'] + g['away_score']
                if actual == line: continue
                if actual > line: w += 1
                else: l += 1
            n = w + l
            if n < MIN_N: continue
            rate = 100*w/n
            lift = rate - base_over
            badge = '🔥' if abs(lift) >= 5 else '·'
            print(f'  park={pb_name:>7s} × line={lb_name:>12s} → OVER {w}-{l} ({rate:.0f}%, n={n}) lift {lift:+.1f}pt {badge}')


if __name__ == '__main__':
    main()
