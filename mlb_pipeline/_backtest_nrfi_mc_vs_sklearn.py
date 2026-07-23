"""NRFI head-to-head: rich MC's native p_nrfi vs sklearn NRFI model.

Rich MC (simulate_game) natively outputs p_nrfi from the same per-inning
simulation used for run totals. Sklearn NRFI model outputs nrfi_score
(0-100) via logistic regression trained on separate features.

For every graded game since 2026-05-30 with both signals available:
  - MC pick: NRFI if p_nrfi > 0.5, else YRFI
  - Sklearn pick: NRFI if nrfi_score >= 50, else YRFI
  - Actual: nrfi_result column ('NRFI' or 'YRFI')

Reports hit rate for each + high-confidence buckets.

USAGE:
    python _backtest_nrfi_mc_vs_sklearn.py
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

from monte_carlo import simulate_game


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def fetch_games(since='2026-05-30'):
    rows = []; off = 0
    filt = (f'&nrfi_result=not.is.null&game_date=gte.{since}'
            f'&nrfi_score=not.is.null'
            f'&home_sp_xera=not.is.null&away_sp_xera=not.is.null')
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


def grade_one(g):
    """Return (mc_result, sklearn_result) dicts each with pick/conf/hit or None."""
    nrfi = str(g.get('nrfi_result') or '').upper()
    if nrfi not in ('NRFI', 'YRFI'):
        return None, None
    # MC path
    g_bridged = dict(g)
    g_bridged.setdefault('home_pitcher', g.get('home_sp_name'))
    g_bridged.setdefault('away_pitcher', g.get('away_sp_name'))
    sim = simulate_game(g_bridged, n_iter=2000, line=None, seed=42)
    mc_res = None
    if sim and sim.get('p_nrfi') is not None:
        p_nrfi = float(sim['p_nrfi'])
        mc_pick = 'NRFI' if p_nrfi > 0.5 else 'YRFI'
        conf = max(p_nrfi, 1 - p_nrfi)
        mc_res = {'pick': mc_pick, 'conf': conf, 'won': mc_pick == nrfi}
    # Sklearn path
    ns = _f(g.get('nrfi_score'))
    sk_res = None
    if ns is not None:
        # nrfi_score is 0-100; higher = more likely NRFI. Threshold 50 for pick.
        # Convert to 0-1 for confidence
        sk_pick = 'NRFI' if ns >= 50 else 'YRFI'
        # Confidence = distance from 50 (rescale to 0.5-1.0 range)
        conf = 0.5 + abs(ns - 50) / 100.0
        sk_res = {'pick': sk_pick, 'conf': conf, 'won': sk_pick == nrfi, 'raw_score': ns}
    return mc_res, sk_res


def summarize(name, tallies):
    print(f'\n=== {name} ===')
    w = tallies.get('W', 0); l = tallies.get('L', 0)
    n = w + l
    pct = round(100 * w / max(1, n), 1)
    print(f'  overall: {w}-{l} = {pct}% (n={n})')
    print(f'  confidence bands:')
    for band in ('50-59%', '60-69%', '70-79%', '80+%'):
        b = tallies['bands'].get(band, [0,0])
        bn = b[0] + b[1]
        bp = round(100 * b[0] / max(1, bn), 1)
        print(f'    {band}: {b[0]}-{b[1]} = {bp}% (n={bn})')


def run():
    print('=== NRFI: MC vs sklearn ===')
    games = fetch_games()
    print(f'  games w/ both signals + graded nrfi_result: {len(games)}')
    mc_t = {'W':0, 'L':0, 'bands': defaultdict(lambda:[0,0])}
    sk_t = {'W':0, 'L':0, 'bands': defaultdict(lambda:[0,0])}
    disagree_mc_right = 0
    disagree_sk_right = 0
    both_right = 0
    both_wrong = 0

    for i, g in enumerate(games):
        if i and i % 100 == 0:
            print(f'  ...{i}/{len(games)}', file=sys.stderr)
        mc_r, sk_r = grade_one(g)
        for res, tally in [(mc_r, mc_t), (sk_r, sk_t)]:
            if not res: continue
            k = 'W' if res['won'] else 'L'
            tally[k] += 1
            band = '50-59%' if res['conf'] < 0.60 else '60-69%' if res['conf'] < 0.70 else '70-79%' if res['conf'] < 0.80 else '80+%'
            idx = 0 if res['won'] else 1
            tally['bands'][band][idx] += 1
        # Disagreement analysis
        if mc_r and sk_r:
            if mc_r['pick'] != sk_r['pick']:
                if mc_r['won']: disagree_mc_right += 1
                else: disagree_sk_right += 1
            else:
                if mc_r['won']: both_right += 1
                else: both_wrong += 1

    summarize('MC (rich, p_nrfi)', mc_t)
    summarize('sklearn (nrfi_score)', sk_t)
    print(f'\n=== DISAGREEMENT (both signals present) ===')
    total_disagree = disagree_mc_right + disagree_sk_right
    print(f'  both agree, both right: {both_right}')
    print(f'  both agree, both wrong: {both_wrong}')
    print(f'  disagree, MC right:     {disagree_mc_right}')
    print(f'  disagree, sklearn right: {disagree_sk_right}')
    if total_disagree:
        mc_pct = round(100 * disagree_mc_right / total_disagree, 1)
        print(f'  MC win rate on disagreements: {mc_pct}% (n={total_disagree})')


if __name__ == '__main__':
    run()
