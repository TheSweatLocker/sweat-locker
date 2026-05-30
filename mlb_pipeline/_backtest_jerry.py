"""Backtest the Jerry Model against 30 days of resolved games.

Reports:
- MAE / RMSE on jerry_total vs actual total runs
- MAE / RMSE on jerry_spread vs actual margin
- Hit rate vs market (Over/Under decisions at market line)
- Side accuracy (which team won) vs jerry_spread direction
- Per-cohort errors (Coors, cold weather, ace duels, etc.)
- Head-to-head: Jerry vs v3 vs v4 on the same games

CAVEAT: this backtest enriches historical games with CURRENT
mlb_pitcher_stats / mlb_team_offense / mlb_bullpen_stats snapshots.
The bucket data wasn't preserved per-game historically, so we
approximate with today's values. Bias direction: overweights recent
team form, underweights early-season form. Fine for relative
ranking against v3/v4, less reliable for absolute MAE.

Run: python _backtest_jerry.py [days=30]
"""
import os, sys, io, json, urllib.request, math
from urllib.parse import quote
from datetime import date, timedelta
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jerry_model import compute_jerry_projection, enrich_ctx_for_jerry

URL = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def get(p):
    with urllib.request.urlopen(urllib.request.Request(URL + p, headers=H), timeout=30) as r:
        return json.loads(r.read())


def in_filter(field, vals):
    return ','.join(f'{field}.eq.{quote(v)}' for v in vals)


def mae(errors):
    return sum(abs(e) for e in errors) / len(errors) if errors else 0


def rmse(errors):
    if not errors:
        return 0
    return math.sqrt(sum(e * e for e in errors) / len(errors))


def main(days=30):
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()
    print(f'Jerry backtest window: {start} -> {end} ({days} days)')
    print()

    games = get(f'/rest/v1/mlb_game_results?game_date=gte.{start}&game_date=lt.{end}&select=*')
    print(f'Resolved games in window: {len(games)}')

    # Pre-load enrichment caches (current snapshots used as proxy for historical state)
    pitcher_names = set()
    team_names = set()
    for g in games:
        for f in ('away_sp_name', 'home_sp_name', 'home_pitcher', 'away_pitcher'):
            v = g.get(f)
            if v: pitcher_names.add(v)
        if g.get('home_team'): team_names.add(g['home_team'])
        if g.get('away_team'): team_names.add(g['away_team'])

    # Pitcher stats — fetch in chunks to avoid URL length limits
    pitcher_stats = {}
    pname_list = list(pitcher_names)
    chunk = 30
    for i in range(0, len(pname_list), chunk):
        batch = pname_list[i:i+chunk]
        f = in_filter('player_name', batch)
        rows = get(f'/rest/v1/mlb_pitcher_stats?or=({f})&select=player_name,innings_1_3_era,innings_4_6_era,innings_7_9_era')
        for p in rows:
            pitcher_stats[p['player_name']] = p

    # Team offense / bullpen
    team_offense = {}
    bullpen_stats = {}
    tname_list = list(team_names)
    for i in range(0, len(tname_list), chunk):
        batch = tname_list[i:i+chunk]
        f = in_filter('team', batch)
        to_rows = get(f'/rest/v1/mlb_team_offense?or=({f})&select=*')
        for t in to_rows: team_offense[t['team']] = t
        bp_rows = get(f'/rest/v1/mlb_bullpen_stats?or=({f})&select=team,pitching_1_3_era,pitching_4_6_era,pitching_7_9_era')
        for b in bp_rows: bullpen_stats[b['team']] = b

    print(f'Caches: {len(pitcher_stats)} pitchers, {len(team_offense)} teams, {len(bullpen_stats)} bullpens')
    print()

    # Note: mlb_game_results uses *_sp_name not *_pitcher; normalize
    for g in games:
        if g.get('away_sp_name') and not g.get('away_pitcher'):
            g['away_pitcher'] = g['away_sp_name']
        if g.get('home_sp_name') and not g.get('home_pitcher'):
            g['home_pitcher'] = g['home_sp_name']

    # Run Jerry on each game with actual scores
    total_errors_jerry = []
    total_errors_v3 = []
    total_errors_v4 = []
    spread_errors_jerry = []

    jerry_over_under_correct = 0
    jerry_over_under_total = 0
    v3_over_under_correct = 0
    v3_over_under_total = 0
    v4_over_under_correct = 0
    v4_over_under_total = 0
    jerry_side_correct = 0
    jerry_side_total = 0

    # Per-cohort tracking
    by_park = defaultdict(list)
    by_temp_band = defaultdict(list)
    eligible_games = 0

    for g in games:
        hs = g.get('home_score'); as_ = g.get('away_score')
        if hs is None or as_ is None: continue
        actual_total = hs + as_
        actual_margin = hs - as_
        ct = g.get('close_total') or g.get('open_total')
        if ct is None: continue

        try:
            enriched = enrich_ctx_for_jerry(g, pitcher_stats, team_offense, bullpen_stats)
            r = compute_jerry_projection(enriched)
        except Exception as e:
            print(f'  scoring failed for {g.get("game_id")}: {e}')
            continue
        if r.get('missing_inputs'):
            continue  # skip games with missing critical inputs

        eligible_games += 1
        jt = r['jerry_total']
        js = r['jerry_spread']

        # Total errors
        err_jerry = jt - actual_total
        total_errors_jerry.append(err_jerry)
        if g.get('projected_total') is not None:
            total_errors_v3.append(g['projected_total'] - actual_total)
        if g.get('model_pred_total') is not None:
            total_errors_v4.append(g['model_pred_total'] - actual_total)

        # Spread errors (jerry_spread is home - away; actual_margin is home - away)
        spread_errors_jerry.append(js - actual_margin)

        # Over/Under decisions
        actual_over = actual_total > ct
        actual_under = actual_total < ct
        jerry_pred_over = jt > ct
        v3_pred_over = g.get('projected_total') is not None and g['projected_total'] > ct
        v4_pred_over = g.get('model_pred_total') is not None and g['model_pred_total'] > ct

        if abs(jt - ct) >= 0.5:  # only count when Jerry has a clear lean
            jerry_over_under_total += 1
            if (jerry_pred_over and actual_over) or (not jerry_pred_over and actual_under):
                jerry_over_under_correct += 1
        if g.get('projected_total') is not None and abs(g['projected_total'] - ct) >= 0.5:
            v3_over_under_total += 1
            if (v3_pred_over and actual_over) or (not v3_pred_over and actual_under):
                v3_over_under_correct += 1
        if g.get('model_pred_total') is not None and abs(g['model_pred_total'] - ct) >= 0.5:
            v4_over_under_total += 1
            if (v4_pred_over and actual_over) or (not v4_pred_over and actual_under):
                v4_over_under_correct += 1

        # Side (winner) accuracy from spread
        jerry_picks_home = js > 0
        actual_home_won = hs > as_
        if hs != as_:
            jerry_side_total += 1
            if jerry_picks_home == actual_home_won:
                jerry_side_correct += 1

        # Per-cohort
        park = g.get('park_run_factor')
        if park is not None:
            park_band = 'hitter (>=108)' if park >= 108 else ('pitcher (<=92)' if park <= 92 else 'neutral (93-107)')
            by_park[park_band].append(abs(err_jerry))
        temp = g.get('temperature')
        if temp is not None:
            temp_band = 'cold (<=55)' if temp <= 55 else ('hot (>=80)' if temp >= 80 else 'mild (56-79)')
            by_temp_band[temp_band].append(abs(err_jerry))

    print('=' * 70)
    print('JERRY MODEL — backtest summary')
    print('=' * 70)
    print(f'  Eligible games (full inputs present): {eligible_games}')
    print()
    print(f'Total MAE (jerry vs actual):  {mae(total_errors_jerry):.2f} runs  (RMSE {rmse(total_errors_jerry):.2f})')
    print(f'Total MAE (v3 vs actual):     {mae(total_errors_v3):.2f} runs  (RMSE {rmse(total_errors_v3):.2f})')
    print(f'Total MAE (v4 vs actual):     {mae(total_errors_v4):.2f} runs  (RMSE {rmse(total_errors_v4):.2f})')
    print()
    print(f'Spread MAE (jerry vs actual margin): {mae(spread_errors_jerry):.2f}  (RMSE {rmse(spread_errors_jerry):.2f})')
    print()
    print('Over/Under hit rate (when model lean >= 0.5 from market line):')
    def fmt(c, t):
        return f'{c}/{t} ({c/t*100:.1f}%)' if t else 'n/a'
    print(f'  jerry: {fmt(jerry_over_under_correct, jerry_over_under_total)}')
    print(f'  v3:    {fmt(v3_over_under_correct, v3_over_under_total)}')
    print(f'  v4:    {fmt(v4_over_under_correct, v4_over_under_total)}')
    print()
    print(f'Side (winner) accuracy from jerry_spread direction:')
    print(f'  jerry: {fmt(jerry_side_correct, jerry_side_total)}')
    print()
    print('Per park cohort (jerry total MAE):')
    for k, v in by_park.items():
        print(f'  {k}: MAE {sum(v)/len(v):.2f} (n={len(v)})')
    print()
    print('Per temperature cohort (jerry total MAE):')
    for k, v in by_temp_band.items():
        print(f'  {k}: MAE {sum(v)/len(v):.2f} (n={len(v)})')
    print()
    # Distribution: are Jerry errors biased OVER or UNDER?
    if total_errors_jerry:
        avg_err = sum(total_errors_jerry) / len(total_errors_jerry)
        over_count = sum(1 for e in total_errors_jerry if e > 0)
        under_count = sum(1 for e in total_errors_jerry if e < 0)
        print(f'Bias check (positive = jerry overshoots, negative = undershoots):')
        print(f'  Mean error: {avg_err:+.2f}  (over={over_count}, under={under_count}, exact={len(total_errors_jerry)-over_count-under_count})')


if __name__ == '__main__':
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    main(days)
