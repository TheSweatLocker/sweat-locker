"""Backtest weighted ensemble vs individual models (2026-08-07).

Runs the ensemble_blend logic against historical jerry_cache game reads
+ mlb_game_results grades. Answers the question: does the weighted
ensemble actually beat the best individual model?

Method:
1. Pull jerry_cache game_read entries (~2500 rows spanning Mar-Aug 2026)
2. Split chronologically: TRAIN = first 60%, TEST = last 40%
   (proper out-of-sample — weights come from train only)
3. Compute per-model weights from TRAIN half only
4. On TEST half, grade every game using:
   a) each individual model's pick
   b) equal-weight ensemble (naive baseline)
   c) weighted ensemble (trained weights)
5. Report hit rate + ROI for each method

If weighted > equal-weight > best individual → ensemble is real edge,
ship shadow mode.
If weighted ~= equal-weight → weighting doesn't help, but blending
still might (multi-signal agreement gate).
If ensemble < best individual → don't ship, use best individual only.

Usage:
    python backtest_ensemble.py [--market ml|total] [--sport MLB]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

from ensemble_blend import blend_picks


def compute_hint_weight(hit_rate, n):
    """Mirror of compute_model_track_records.compute_hint_weight so
    train-only weights match production tuning."""
    if n < 25: return 1.0
    if hit_rate is None: return 1.0
    edge = hit_rate - 0.5
    if edge >= 0.10: return min(2.5, 1.0 + edge * 10)
    if edge >= 0.05: return 1.3
    if edge >= 0.03: return 1.15
    if edge <= -0.10: return max(0.3, 1.0 + edge * 4)
    if edge <= -0.05: return 0.75
    return 1.0


def load_predictions(sport: str = 'MLB') -> list:
    """Same extraction as compute_model_track_records — returns list of
    {game_id, game_date, away_team, home_team, model preds, close_lines}."""
    all_reads = []
    offset = 0
    while True:
        params = {'sport': f'eq.{sport.lower()}', 'cache_key': 'like.game_read_%',
                  'select': 'cache_key,data,created_at', 'limit': '500',
                  'offset': str(offset), 'order': 'created_at.asc'}
        r = requests.get(f'{SB}/rest/v1/jerry_cache',
                         headers=H, params=params, timeout=30).json()
        if not isinstance(r, list) or not r: break
        all_reads += r
        if len(r) < 500: break
        offset += 500
    print(f'  loaded {len(all_reads)} jerry_cache game_reads')

    predictions = []
    for row in all_reads:
        try:
            data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict): continue
        matchup = data.get('matchup', '')
        parts = matchup.split(' @ ')
        if len(parts) != 2: continue
        away_team, home_team = parts[0].strip(), parts[1].strip()
        meta = data.get('meta') or {}
        game_date = meta.get('game_date') or row['created_at'][:10]
        market = data.get('market') or {}
        resolver = data.get('resolver') or {}
        resolver_side = data.get('resolver_side') or {}
        predictions.append({
            'game_id': data.get('game_id'),
            'game_date': game_date,
            'away_team': away_team, 'home_team': home_team,
            'model_total': market.get('model_total'),
            'close_total': market.get('close_total'),
            'model_spread': market.get('model_spread'),
            'close_spread': market.get('close_spread'),
            'panel_implied_total': market.get('panel_implied_total'),
            'resolver_dir': resolver.get('direction'),
            'resolver_side_dir': resolver_side.get('direction'),
        })
    predictions.sort(key=lambda p: p['game_date'] or '')
    return predictions


def load_results(dates: list) -> dict:
    """Return {game_id or (date, home, away): result_row}."""
    out = {}
    dates = sorted(set(d for d in dates if d))
    for i in range(0, len(dates), 30):
        chunk = dates[i:i+30]
        in_dates = ','.join(f'"{d}"' for d in chunk)
        r = requests.get(f'{SB}/rest/v1/mlb_game_results',
                         headers=H,
                         params={'game_date': f'in.({in_dates})',
                                 'select': 'game_id,home_team,away_team,home_score,away_score,game_date',
                                 'limit': '2000'}, timeout=20).json()
        for x in (r if isinstance(r, list) else []):
            if x.get('home_score') is None: continue
            k = (x['game_date'], (x['home_team'] or '').upper(), (x['away_team'] or '').upper())
            out[k] = x
            if x.get('game_id'): out[x['game_id']] = x
    return out


def get_actual(p, res):
    """Return dict of actual outcomes: {'total_actual': 'OVER'|'UNDER'|'PUSH'|None,
    'ml_actual': 'HOME'|'AWAY'|'PUSH'|None, 'actual_total': int, 'hs': int, 'as_': int}."""
    if not res: return None
    hs, as_ = res['home_score'], res['away_score']
    tot = hs + as_
    ct = p['close_total']
    ml_actual = 'PUSH' if hs == as_ else ('HOME' if hs > as_ else 'AWAY')
    total_actual = None
    if ct is not None:
        try:
            ct_f = float(ct)
            if tot > ct_f: total_actual = 'OVER'
            elif tot < ct_f: total_actual = 'UNDER'
            else: total_actual = 'PUSH'
        except (TypeError, ValueError): pass
    return {'total_actual': total_actual, 'ml_actual': ml_actual,
            'actual_total': tot, 'hs': hs, 'as_': as_}


def model_picks_for_ml(p) -> dict:
    """Extract per-model ML picks."""
    out = {}
    rsd = (p.get('resolver_side_dir') or '').upper()
    if rsd in ('HOME','AWAY'): out['RESOLVER_SIDE'] = rsd
    if p.get('model_spread') is not None and p.get('close_spread') is not None:
        try:
            delta = float(p['model_spread']) + float(p['close_spread'])
            if delta != 0:
                out['MODEL_SPREAD'] = 'HOME' if delta > 0 else 'AWAY'
        except (TypeError, ValueError): pass
    return out


def model_picks_for_total(p) -> dict:
    """Extract per-model total picks."""
    out = {}
    rd = (p.get('resolver_dir') or '').upper()
    if rd in ('OVER','UNDER'): out['RESOLVER'] = rd
    if p.get('model_total') is not None and p.get('close_total') is not None:
        try:
            mt = float(p['model_total']); ct = float(p['close_total'])
            if mt > ct: out['MODEL_TOTAL'] = 'OVER'
            elif mt < ct: out['MODEL_TOTAL'] = 'UNDER'
        except (TypeError, ValueError): pass
    if p.get('panel_implied_total') is not None and p.get('close_total') is not None:
        try:
            pt = float(p['panel_implied_total']); ct = float(p['close_total'])
            if pt > ct: out['PANEL_TOTAL'] = 'OVER'
            elif pt < ct: out['PANEL_TOTAL'] = 'UNDER'
        except (TypeError, ValueError): pass
    return out


def compute_train_weights(train_predictions, results, market):
    """Compute per-model hit rate + weight from training split."""
    tallies = defaultdict(lambda: {'w':0, 'n':0})
    picker = model_picks_for_ml if market == 'ml' else model_picks_for_total
    result_key = 'ml_actual' if market == 'ml' else 'total_actual'
    for p in train_predictions:
        r = results.get(p['game_id']) or results.get(
            (p['game_date'], (p['home_team'] or '').upper(), (p['away_team'] or '').upper()))
        if not r: continue
        actual = get_actual(p, r)
        if not actual: continue
        act = actual[result_key]
        if act in (None, 'PUSH'): continue
        picks = picker(p)
        for model, pick in picks.items():
            tallies[model]['n'] += 1
            if pick == act: tallies[model]['w'] += 1
    weights = {}
    for m, v in tallies.items():
        if v['n'] == 0: continue
        hit = v['w'] / v['n']
        w = compute_hint_weight(hit, v['n'])
        weights[m] = (w, hit * 100, 'train', v['n'])
    return weights


def evaluate(test_predictions, results, weights, market):
    """Return dict of methods → {w, n, hit_pct, roi_pct}."""
    picker = model_picks_for_ml if market == 'ml' else model_picks_for_total
    result_key = 'ml_actual' if market == 'ml' else 'total_actual'
    tally = defaultdict(lambda: {'w':0, 'n':0})
    equal_weights = {m: (1.0, None, 'equal', 0) for m in weights}
    for p in test_predictions:
        r = results.get(p['game_id']) or results.get(
            (p['game_date'], (p['home_team'] or '').upper(), (p['away_team'] or '').upper()))
        if not r: continue
        actual = get_actual(p, r)
        if not actual: continue
        act = actual[result_key]
        if act in (None, 'PUSH'): continue
        picks = picker(p)
        # Individual models
        for m, pk in picks.items():
            tally[f'individual:{m}']['n'] += 1
            if pk == act: tally[f'individual:{m}']['w'] += 1
        # Equal-weight ensemble
        if picks:
            eq = blend_picks(picks, 'MLB', market, weights_override=equal_weights)
            if eq['pick']:
                tally['ensemble:equal']['n'] += 1
                if eq['pick'] == act: tally['ensemble:equal']['w'] += 1
        # Weighted ensemble (from train)
        if picks:
            wt = blend_picks(picks, 'MLB', market, weights_override=weights)
            if wt['pick']:
                tally['ensemble:weighted']['n'] += 1
                if wt['pick'] == act: tally['ensemble:weighted']['w'] += 1
                # Also confidence-gated version: only pick when agreement is unanimous
                if wt['agreement'] >= 0.99:
                    tally['ensemble:weighted_unanimous']['n'] += 1
                    if wt['pick'] == act: tally['ensemble:weighted_unanimous']['w'] += 1
    out = {}
    for method, v in tally.items():
        n = v['n']; w = v['w']
        if n == 0: continue
        hit = w / n
        # ROI at -110: win = +0.909, loss = -1.0
        roi = ((w * 0.909) - (n - w) * 1.0) / n * 100
        out[method] = {'w': w, 'n': n, 'hit_pct': hit * 100, 'roi_pct': roi}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', default='MLB')
    ap.add_argument('--market', default='both', choices=['ml','total','both'])
    ap.add_argument('--train-frac', type=float, default=0.6)
    args = ap.parse_args()

    print(f'=== backtest_ensemble · {args.sport} ===')
    print(f'  loading predictions...')
    preds = load_predictions(args.sport)
    print(f'  loading results...')
    dates = list({p['game_date'] for p in preds})
    results = load_results(dates)
    print(f'  results loaded: {len(results)} game rows')

    split_idx = int(len(preds) * args.train_frac)
    train = preds[:split_idx]
    test = preds[split_idx:]
    print(f'  train: {len(train)} games ({train[0]["game_date"]} → {train[-1]["game_date"]})')
    print(f'  test:  {len(test)} games ({test[0]["game_date"]} → {test[-1]["game_date"]})')

    markets = ['ml', 'total'] if args.market == 'both' else [args.market]
    for market in markets:
        print()
        print(f'══════ MARKET: {market.upper()} ══════')
        weights = compute_train_weights(train, results, market)
        print(f'  weights from train:')
        for m, (w, hit, _, n) in sorted(weights.items(), key=lambda x: -x[1][0]):
            print(f'    {m:<20} weight={w:.2f}  train_hit={hit:.1f}%  n={n}')

        eval_ = evaluate(test, results, weights, market)
        print()
        print(f'  TEST results ({args.market}):')
        print(f'  {"method":<40}  {"W-L":<12}  {"hit%":<7}  {"ROI%":<7}')
        print(f'  {"-"*40}  {"-"*12}  {"-"*7}  {"-"*7}')
        # Individuals first
        for method, v in sorted(eval_.items()):
            if method.startswith('individual:'):
                print(f'  {method:<40}  {v["w"]}-{v["n"]-v["w"]:<7} n={v["n"]:<3}  {v["hit_pct"]:>5.1f}%  {v["roi_pct"]:>+5.1f}%')
        # Ensembles last (highlighted)
        for method in ('ensemble:equal', 'ensemble:weighted', 'ensemble:weighted_unanimous'):
            if method in eval_:
                v = eval_[method]
                marker = ' <--' if 'weighted' in method else ''
                print(f'  {method:<40}  {v["w"]}-{v["n"]-v["w"]:<7} n={v["n"]:<3}  {v["hit_pct"]:>5.1f}%  {v["roi_pct"]:>+5.1f}%{marker}')


if __name__ == '__main__':
    main()
