"""Backtests for 5-part advanced metrics pack (2026-08-15).

Runs 3 formal backtests on historical MLB data. Reports whether each
metric's coefficient is calibrated correctly for OUR data (the
sabermetric literature validates the EFFECT exists — the question is
whether OUR implementation matches).

BACKTESTS
  #1 SIERA vs xERA head-to-head — which better predicts next N starts
  #2 TTTO penalty calibration — does starter-goes-deep correlate with
     higher game totals in the direction/magnitude our formula predicts?
  #3 BABIP regression flag — do "hot BABIP" teams actually cool down
     over next 10 games? By how much?

CLI
  python backtest_advanced_metrics.py                # all 3 backtests
  python backtest_advanced_metrics.py --test siera   # single backtest
  python backtest_advanced_metrics.py --season 2025  # specific season
"""
from __future__ import annotations
import argparse, os, sys, statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
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
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def _compute_siera_simple(k_pct, bb_pct, gb_pct=0.43):
    if k_pct is None or bb_pct is None: return None
    try:
        k = float(k_pct); bb = float(bb_pct); gb = float(gb_pct)
    except (TypeError, ValueError): return None
    if k > 1.0: k /= 100.0
    if bb > 1.0: bb /= 100.0
    if gb > 1.0: gb /= 100.0
    siera = (6.145 - 16.986*k + 11.434*bb - 1.858*gb + 7.653*(k**2))
    return max(0.50, siera)


def _compute_xera_simple(k, bb, hr, ip):
    """Simplified xERA proxy: FIP-style."""
    if ip is None or ip <= 0: return None
    try: return (13 * float(hr or 0) + 3 * float(bb or 0) - 2 * float(k or 0)) / float(ip) + 3.10
    except (TypeError, ValueError, ZeroDivisionError): return None


# ────────────────────────────────────────────────────────────
# Backtest #1 — SIERA vs xERA head-to-head
# ────────────────────────────────────────────────────────────

def backtest_siera(season: int = 2025):
    """For each pitcher with 10+ starts, split at midseason. Use pre-split
    starts to compute SIERA + xERA. Predict post-split ER rate. Compare RMSE."""
    print(f'\n{"="*70}')
    print(f'BACKTEST #1 · SIERA vs xERA · season {season}')
    print(f'{"="*70}')

    # Get all pitchers who started for MLB teams that season
    print(f'  building pitcher list via MLB Stats API...')
    r = requests.get('https://statsapi.mlb.com/api/v1/teams',
                     params={'sportId': 1, 'season': season}, timeout=15)
    teams = r.json().get('teams', [])
    all_pitchers = set()
    for t in teams[:5]:  # sample 5 teams for speed — 30-team full run takes ~10min
        r2 = requests.get(f'https://statsapi.mlb.com/api/v1/teams/{t["id"]}/roster',
                          params={'season': season, 'rosterType': 'fullSeason'},
                          timeout=15)
        for p in r2.json().get('roster', []):
            pos = p.get('position', {}).get('abbreviation')
            if pos in ('P', 'TWP'):
                all_pitchers.add((p['person']['id'], p['person']['fullName']))
    print(f'  pitchers to test: {len(all_pitchers)}')

    siera_errors = []
    xera_errors = []
    n_evaluated = 0

    for pid, pname in all_pitchers:
        # Fetch full gameLog for season
        try:
            r = requests.get(f'https://statsapi.mlb.com/api/v1/people/{pid}/stats',
                params={'stats': 'gameLog', 'group': 'pitching', 'season': season},
                timeout=12)
            splits = r.json().get('stats', [])
            if not splits or not splits[0].get('splits'): continue
            games = splits[0]['splits']
            starts = [g for g in games if int(g.get('stat', {}).get('gamesStarted', 0) or 0) == 1]
            if len(starts) < 10: continue

            # Sort by date ascending
            starts.sort(key=lambda g: g.get('date', ''))
            mid = len(starts) // 2
            pre = starts[:mid]; post = starts[mid:]

            def _agg(g_list):
                tot = {'k':0,'bb':0,'hr':0,'er':0,'bf':0,'ip':0.0,'gb':0,'ao':0}
                for g in g_list:
                    s = g.get('stat', {})
                    tot['k']  += int(s.get('strikeOuts', 0) or 0)
                    tot['bb'] += int(s.get('baseOnBalls', 0) or 0)
                    tot['hr'] += int(s.get('homeRuns', 0) or 0)
                    tot['er'] += int(s.get('earnedRuns', 0) or 0)
                    tot['bf'] += int(s.get('battersFaced', 0) or 0)
                    tot['gb'] += int(s.get('groundOuts', 0) or 0)
                    tot['ao'] += int(s.get('airOuts', 0) or 0)
                    ip = str(s.get('inningsPitched', '0') or '0')
                    if '.' in ip:
                        w, f = ip.split('.'); tot['ip'] += int(w) + int(f)/3
                    else: tot['ip'] += float(ip)
                return tot

            pre_a = _agg(pre); post_a = _agg(post)
            if pre_a['bf'] < 100 or post_a['ip'] < 15: continue

            # Pre-period rates
            k_pct = pre_a['k'] / pre_a['bf']
            bb_pct = pre_a['bb'] / pre_a['bf']
            gb_pct = pre_a['gb'] / (pre_a['gb'] + pre_a['ao']) if (pre_a['gb']+pre_a['ao']) > 0 else 0.43
            # Compute both predictors
            siera = _compute_siera_simple(k_pct, bb_pct, gb_pct)
            xera = _compute_xera_simple(pre_a['k'], pre_a['bb'], pre_a['hr'], pre_a['ip'])
            if siera is None or xera is None: continue
            # Actual post-period ERA (target)
            actual_era = (post_a['er'] * 9) / post_a['ip'] if post_a['ip'] > 0 else None
            if actual_era is None or actual_era > 15 or actual_era < 0: continue

            siera_errors.append((siera - actual_era) ** 2)
            xera_errors.append((xera - actual_era) ** 2)
            n_evaluated += 1
        except Exception as e:
            continue

    if not siera_errors:
        print(f'  ✗ no valid predictions'); return
    siera_rmse = (sum(siera_errors) / len(siera_errors)) ** 0.5
    xera_rmse = (sum(xera_errors) / len(xera_errors)) ** 0.5
    print(f'  n pitcher-splits evaluated: {n_evaluated}')
    print(f'  SIERA RMSE vs actual post-period ERA: {siera_rmse:.3f}')
    print(f'  xERA  RMSE vs actual post-period ERA: {xera_rmse:.3f}')
    improvement = (xera_rmse - siera_rmse) / xera_rmse * 100
    print(f'  SIERA improvement over xERA: {improvement:+.1f}%')
    if improvement > 2:
        print(f'  ✓ VERDICT: SIERA meaningfully beats xERA — integrate as forward-projection lens')
    elif improvement > 0:
        print(f'  ~ VERDICT: SIERA modestly better — small edge, worth including in ensemble')
    else:
        print(f'  ✗ VERDICT: SIERA does not beat xERA on this data — check formula (may need GB-FB-PU terms)')


# ────────────────────────────────────────────────────────────
# Backtest #2 — TTTO penalty calibration
# ────────────────────────────────────────────────────────────

def backtest_ttto():
    """Compare actual game totals in games where BOTH starters went 6+ IP
    vs games where BOTH went <5 IP. If TTTO effect is real, the 6+ IP games
    should have HIGHER totals than projected (starters exposed to TTTO).

    Actually the effect is inverse — 6+ IP means the starter was effective,
    so runs may be LOWER. What matters is: after controlling for starter
    quality (SIERA/xERA), do games with more TTTO exposure hit OVER more?

    Simpler test: does starter IP correlate with runs BEYOND what projection
    predicted? Positive residual on high-IP games = TTTO effect.
    """
    print(f'\n{"="*70}')
    print(f'BACKTEST #2 · TTTO penalty calibration')
    print(f'{"="*70}')

    # Pull historical mlb_game_results with projections + actuals
    r = requests.get(f'{SB}/rest/v1/mlb_game_results?select=game_date,home_score,away_score,'
                     f'close_total,projected_total,model_pred_total,'
                     f'home_last_ip,away_last_ip,home_pitcher,away_pitcher'
                     f'&home_score=not.is.null&close_total=not.is.null'
                     f'&home_last_ip=not.is.null&order=game_date.desc&limit=1000',
                     headers=H_READ, timeout=30)
    rows = [r_ for r_ in (r.json() or []) if isinstance(r_, dict)]
    print(f'  historical games w/ projections + starter IP: {len(rows)}')

    if not rows:
        print(f'  ✗ no data — mlb_game_results does not carry historical projections?')
        return

    # Bucket games by combined starter IP (last game as proxy for expected)
    buckets = {'deep_both': [], 'shallow_both': [], 'mixed': []}
    for row in rows:
        hip = row.get('home_last_ip'); aip = row.get('away_last_ip')
        if hip is None or aip is None: continue
        try: hip = float(hip); aip = float(aip)
        except (TypeError, ValueError): continue
        proj = row.get('projected_total') or row.get('model_pred_total')
        if proj is None: continue
        total = (row.get('home_score', 0) or 0) + (row.get('away_score', 0) or 0)
        residual = total - float(proj)
        if hip >= 6.0 and aip >= 6.0: buckets['deep_both'].append(residual)
        elif hip < 5.0 and aip < 5.0: buckets['shallow_both'].append(residual)
        else: buckets['mixed'].append(residual)

    print(f'  bucket sizes: deep_both={len(buckets["deep_both"])} · '
          f'shallow_both={len(buckets["shallow_both"])} · mixed={len(buckets["mixed"])}')

    for bname, resids in buckets.items():
        if not resids: continue
        mean = statistics.mean(resids)
        median = statistics.median(resids)
        overs = sum(1 for r in resids if r > 0)
        n = len(resids)
        print(f'  {bname:<14} n={n:<4} mean residual={mean:+.2f} runs · median={median:+.1f} · overs={100*overs/n:.0f}%')

    # TTTO expectation: deep_both should have MORE positive residual than shallow_both
    if buckets['deep_both'] and buckets['shallow_both']:
        deep_mean = statistics.mean(buckets['deep_both'])
        shallow_mean = statistics.mean(buckets['shallow_both'])
        diff = deep_mean - shallow_mean
        print(f'  Δ deep vs shallow: {diff:+.2f} runs')
        # Our formula predicts +0.72 runs for both-starters-6IP games. If diff is close, calibrated.
        our_prediction = 0.72  # (6.0*4.2 - 18) * 0.05 * 2 = 0.72 runs
        print(f'  Our TTTO formula predicts: {our_prediction:+.2f} runs (both starters 6.0 IP)')
        if 0.5 <= diff <= 1.0:
            print(f'  ✓ VERDICT: TTTO calibration in the right range — safe to integrate')
        elif diff > 1.0:
            print(f'  ~ VERDICT: TTTO effect stronger than our formula — bump coefficient to ~0.07')
        elif 0 < diff < 0.5:
            print(f'  ~ VERDICT: TTTO effect weaker than our formula — reduce coefficient to ~0.03')
        else:
            print(f'  ✗ VERDICT: TTTO effect inverted or absent on this data — investigate')


# ────────────────────────────────────────────────────────────
# Backtest #3 — BABIP regression flag hit rate
# ────────────────────────────────────────────────────────────

def backtest_babip():
    """For team-dates in the past where L14 BABIP > 0.320 (hot flag),
    check whether the team's next 10 games saw lower RPG than season avg."""
    print(f'\n{"="*70}')
    print(f'BACKTEST #3 · BABIP regression flag')
    print(f'{"="*70}')

    # Pull all team-day results with home+away scores + team ids
    r = requests.get(f'{SB}/rest/v1/mlb_game_results?select=game_date,home_team,away_team,'
                     f'home_score,away_score&home_score=not.is.null'
                     f'&order=game_date.asc&limit=5000', headers=H_READ, timeout=30)
    games = [g for g in (r.json() or []) if isinstance(g, dict)]
    print(f'  historical games loaded: {len(games)}')

    # Build team-by-team game log
    team_games = defaultdict(list)  # team -> [(date, runs_scored, runs_allowed)]
    for g in games:
        d = g.get('game_date'); hs = g.get('home_score'); as_ = g.get('away_score')
        h = g.get('home_team'); a = g.get('away_team')
        if None in (d, hs, as_, h, a): continue
        team_games[h].append((d, hs, as_))
        team_games[a].append((d, as_, hs))
    for t in team_games: team_games[t].sort(key=lambda x: x[0])

    # For each team, walk through, at each date compute L14 R/G. Flag when
    # rolling R/G is > team's season mean + 1 (proxy for hot BABIP without
    # per-plate-appearance data). Then check next 10 games' RPG.
    hot_regression = []
    cold_regression = []
    for team, logs in team_games.items():
        if len(logs) < 30: continue
        # Season baseline
        season_rpg = statistics.mean(r for _, r, _ in logs)
        for i in range(14, len(logs) - 10):
            l14 = logs[i-14:i]
            l14_rpg = statistics.mean(r for _, r, _ in l14)
            # Hot = L14 RPG > season + 1.0; Cold = L14 RPG < season - 1.0
            next10 = logs[i:i+10]
            next10_rpg = statistics.mean(r for _, r, _ in next10)
            if l14_rpg > season_rpg + 1.0:
                # Regression down expected
                hot_regression.append(next10_rpg - l14_rpg)
            elif l14_rpg < season_rpg - 1.0:
                # Regression up expected
                cold_regression.append(next10_rpg - l14_rpg)

    if not hot_regression and not cold_regression:
        print(f'  ✗ no regression cases found (need more historical data)')
        return

    print(f'  Note: proxying "hot BABIP" via L14 RPG > season+1 (real BABIP calc requires K/AB tracking)')
    if hot_regression:
        mean = statistics.mean(hot_regression)
        n = len(hot_regression)
        regressed = sum(1 for x in hot_regression if x < 0)
        print(f'  HOT teams (L14 RPG > season+1): n={n}')
        print(f'    next 10 games RPG - L14 RPG: mean={mean:+.2f} runs · regressed={100*regressed/n:.0f}%')
    if cold_regression:
        mean = statistics.mean(cold_regression)
        n = len(cold_regression)
        regressed = sum(1 for x in cold_regression if x > 0)
        print(f'  COLD teams (L14 RPG < season-1): n={n}')
        print(f'    next 10 games RPG - L14 RPG: mean={mean:+.2f} runs · rebounded={100*regressed/n:.0f}%')

    if hot_regression and cold_regression:
        h_mean = statistics.mean(hot_regression)
        c_mean = statistics.mean(cold_regression)
        if h_mean < -0.3 and c_mean > 0.3:
            print(f'  ✓ VERDICT: regression flag works — hot teams cool, cold teams heat up')
        elif h_mean < -0.1 and c_mean > 0.1:
            print(f'  ~ VERDICT: modest regression signal — usable but small edge')
        else:
            print(f'  ✗ VERDICT: no clear regression signal (or teams stay hot/cold longer than 14 days)')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--test', choices=['siera','ttto','babip','all'], default='all')
    p.add_argument('--season', type=int, default=2025)
    args = p.parse_args()
    if args.test in ('siera','all'): backtest_siera(args.season)
    if args.test in ('ttto','all'): backtest_ttto()
    if args.test in ('babip','all'): backtest_babip()


if __name__ == '__main__':
    main()
