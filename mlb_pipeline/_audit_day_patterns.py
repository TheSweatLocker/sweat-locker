"""Day-of-week / day-vs-night pattern dig.

Initial finding from _audit_compound_patterns.py: Thursday HOME ML hits
46% over 96 games (-7.4pt below 53.3% baseline). Wednesday HOME hits
56% (+2.4pt). Is this real scheduling noise or a real signal?

Test plan:
  1. Each weekday × HOME ML hit rate (broader sample)
  2. Each weekday × OVER hit rate
  3. Each weekday × ML hit rate by interaction with favored side
  4. Day games (afternoon) vs night games (we don't have first-pitch
     time in results, but month/seasonal context might proxy)
  5. Compound: Thursday + away team (specific weekday × situation)

Gate: hit rate gap >= 5pt with n >= 60.
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
           'away_sp_xera,home_sp_xera,park_run_factor')
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


def main():
    games = pull(120)
    print(f'pulled {len(games)} graded games')
    base_home = sum(1 for g in games if g['home_score'] > g['away_score']) * 100 / len(games)
    over_games = [g for g in games if g.get('close_total') and (g['home_score']+g['away_score']) != g['close_total']]
    base_over = sum(1 for g in over_games if g['home_score']+g['away_score'] > g['close_total']) * 100 / max(1, len(over_games))
    print(f'baseline HOME {base_home:.1f}% | OVER {base_over:.1f}%')
    print()

    # ===== 1. By weekday — HOME ML and OVER =====
    print('=== 1. WEEKDAY × HOME ML + OVER ===')
    by_dow_ml = defaultdict(lambda: [0, 0])
    by_dow_tot = defaultdict(lambda: [0, 0])  # [over, under]
    for g in games:
        try:
            d = datetime.fromisoformat(g['game_date']).strftime('%A')
        except Exception:
            continue
        if g['home_score'] > g['away_score']: by_dow_ml[d][0] += 1
        else: by_dow_ml[d][1] += 1
        line = g.get('close_total')
        if line and (g['home_score']+g['away_score']) != line:
            if g['home_score']+g['away_score'] > line: by_dow_tot[d][0] += 1
            else: by_dow_tot[d][1] += 1
    print(f'{"day":>10s}  {"HOME":>14s}  {"vs base":>9s}  {"OVER":>14s}  {"vs base":>9s}')
    print('-' * 75)
    for d in ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']:
        hw, hl = by_dow_ml.get(d, [0, 0])
        n_h = hw + hl
        ow, ol = by_dow_tot.get(d, [0, 0])
        n_o = ow + ol
        if n_h < 20: continue
        h_rate = 100 * hw / n_h
        h_lift = h_rate - base_home
        o_rate = 100 * ow / max(1, n_o)
        o_lift = o_rate - base_over
        h_badge = '🔥' if abs(h_lift) >= 5 else '·'
        o_badge = '🔥' if abs(o_lift) >= 5 else '·'
        print(f'  {d:>10s}  {hw:>3d}-{hl:<3d} {h_rate:.0f}% (n={n_h:>3}) {h_lift:+5.1f}pt {h_badge}  '
              f'{ow:>3d}-{ol:<3d} {o_rate:.0f}% (n={n_o:>3}) {o_lift:+5.1f}pt {o_badge}')

    # ===== 2. Thursday-specific: when is the away dog hitting? =====
    print()
    print('=== 2. THURSDAY DEEP DIVE (initial -7.4pt HOME ML finding) ===')
    thur = [g for g in games
            if (datetime.fromisoformat(g['game_date']).weekday() == 3)]
    print(f'Thursdays: n={len(thur)}')
    # Split by home favored vs home dog
    home_fav = [g for g in thur if (fl(g.get('home_ml_close')) or 0) < 0]
    home_dog = [g for g in thur if (fl(g.get('home_ml_close')) or 0) > 0]
    for label, subset in [('home fav', home_fav), ('home dog', home_dog)]:
        hw = sum(1 for g in subset if g['home_score'] > g['away_score'])
        n = len(subset)
        if n < 15: continue
        print(f'  Thursday + {label}: HOME wins {hw}/{n} ({100*hw/n:.0f}%)')

    # ===== 3. Day of season — early-season vs late-season? =====
    print()
    print('=== 3. WITHIN-WEEK STREAK FOR HOME ML (Wed -> Thur dropoff?) ===')
    # Compute pairwise (Wed → Thu) deltas across teams?
    # Simpler: for each day, by month
    by_month_dow = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for g in games:
        try:
            dt = datetime.fromisoformat(g['game_date'])
        except Exception:
            continue
        d = dt.strftime('%A')
        m = dt.month
        if g['home_score'] > g['away_score']: by_month_dow[m][d][0] += 1
        else: by_month_dow[m][d][1] += 1
    for m in sorted(by_month_dow.keys()):
        print(f'  Month {m}:')
        for d in ['Wednesday', 'Thursday', 'Friday']:
            hw, hl = by_month_dow[m].get(d, [0, 0])
            n = hw + hl
            if n < 10: continue
            print(f'    {d}: {hw}-{hl} ({100*hw/n:.0f}%, n={n})')

    # ===== 4. Compound: Thursday + line bands =====
    print()
    print('=== 4. THURSDAY × LINE BAND (does the day-of-week matter at specific lines?) ===')
    for lo, hi, lbl in [(0, 7.5, 'low'), (7.5, 9, 'mid'), (9, 99, 'high')]:
        thur_band = [g for g in thur if (g.get('close_total') or 0) >= lo and (g.get('close_total') or 99) < hi]
        if len(thur_band) < 15: continue
        hw = sum(1 for g in thur_band if g['home_score'] > g['away_score'])
        ow = sum(1 for g in thur_band
                 if g.get('close_total')
                 and (g['home_score']+g['away_score']) > g['close_total'])
        over_eligible = [g for g in thur_band
                         if g.get('close_total')
                         and (g['home_score']+g['away_score']) != g['close_total']]
        print(f'  Thursday line {lbl}: HOME {hw}/{len(thur_band)} ({100*hw/len(thur_band):.0f}%, n={len(thur_band)}) | '
              f'OVER {ow}/{len(over_eligible)} ({100*ow/max(1,len(over_eligible)):.0f}%)')


if __name__ == '__main__':
    main()
