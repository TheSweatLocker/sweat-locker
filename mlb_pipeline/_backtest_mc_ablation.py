"""MC v2 ablation study — disable each new multiplier one at a time to
isolate which ones actually move the hit rate.

Runs v2 rich MC with each of the 3 new multipliers (mastery, umpire,
defense) disabled in turn. Compares side hit rate + 80%+ conf hit
rate to full v2. If a multiplier's absence produces the SAME result,
that multiplier is noise. If a multiplier's absence HURTS hit rate,
it's real signal.

USAGE:
    python _backtest_mc_ablation.py
"""
import os
import sys
from collections import defaultdict
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

import monte_carlo as mc


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def fetch_games(since='2026-05-30'):
    rows = []; off = 0
    filt = (f'&home_score=not.is.null&game_date=gte.{since}'
            f'&home_sp_xera=not.is.null&away_sp_xera=not.is.null'
            f'&home_bullpen_era=not.is.null&away_bullpen_era=not.is.null'
            f'&home_last10_runs_per_game=not.is.null')
    while True:
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_results?select=*{filt}'
            f'&order=game_date.desc&limit=1000&offset={off}',
            headers=H, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
        off += 1000
    return rows


def _run_variant(games, disable=None):
    """disable ∈ {'mastery', 'umpire', 'defense', 'all_new'}"""
    orig_m = mc._pitcher_vs_team_mult
    orig_u = mc._umpire_over_mult
    orig_d = mc._defense_mult
    if disable == 'mastery' or disable == 'all_new':
        mc._pitcher_vs_team_mult = lambda *a, **k: 1.0
    if disable == 'umpire' or disable == 'all_new':
        mc._umpire_over_mult = lambda *a, **k: 1.0
    if disable == 'defense' or disable == 'all_new':
        mc._defense_mult = lambda *a, **k: 1.0

    w = l = 0
    conf_buckets = defaultdict(lambda: [0, 0])
    for g in games:
        g_bridged = dict(g)
        g_bridged.setdefault('home_pitcher', g.get('home_sp_name'))
        g_bridged.setdefault('away_pitcher', g.get('away_sp_name'))
        sim = mc.simulate_game(g_bridged, n_iter=2000, line=_f(g.get('close_total')), seed=42)
        if not sim: continue
        p = sim.get('p_home_win')
        if p is None or g.get('home_win') is None: continue
        pick_home = p > 0.5
        actual_home = bool(g['home_win'])
        won = pick_home == actual_home
        if won: w += 1
        else: l += 1
        conf = max(p, 1-p)
        band = '50-59' if conf < 0.60 else '60-69' if conf < 0.70 else '70-79' if conf < 0.80 else '80+'
        if won: conf_buckets[band][0] += 1
        else: conf_buckets[band][1] += 1

    # Restore
    mc._pitcher_vs_team_mult = orig_m
    mc._umpire_over_mult = orig_u
    mc._defense_mult = orig_d

    n = w + l
    pct = round(100 * w / max(1, n), 2)
    high_conf = conf_buckets.get('80+', [0, 0])
    hc_n = high_conf[0] + high_conf[1]
    hc_pct = round(100 * high_conf[0] / max(1, hc_n), 2) if hc_n else None
    return {'w': w, 'l': l, 'pct': pct, 'n': n, 'hc_pct': hc_pct, 'hc_n': hc_n}


def run():
    print('=== MC v2 ablation study ===')
    games = fetch_games()
    print(f'  games: {len(games)}')

    baseline = _run_variant(games, disable=None)
    print(f'\n{"variant":<15} {"W-L":<10} {"side%":<8} {"80+% conf":<12}')
    print(f'{"FULL v2":<15} {baseline["w"]}-{baseline["l"]:<7} {baseline["pct"]:<8} {baseline["hc_pct"]}% (n={baseline["hc_n"]})')

    for disable in ('mastery', 'umpire', 'defense', 'all_new'):
        v = _run_variant(games, disable=disable)
        delta = round(v['pct'] - baseline['pct'], 2)
        hc_delta = round((v['hc_pct'] or 0) - (baseline['hc_pct'] or 0), 2) if v['hc_pct'] else None
        label = f'no {disable}' if disable != 'all_new' else 'v2 core only'
        print(f'{label:<15} {v["w"]}-{v["l"]:<7} {v["pct"]:<8} {v["hc_pct"]}% (n={v["hc_n"]})   Δ side {delta:+.2f}pt · Δ 80+% {hc_delta}pt')


if __name__ == '__main__':
    run()
