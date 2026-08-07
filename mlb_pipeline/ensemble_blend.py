"""Weighted ensemble of model outputs for pick selection (2026-08-07).

Reads recent per-model hit rates from model_track_records and blends
each game's model predictions weighted by recent performance.

The insight: individual models drift — v4/XGBoost went from lift to
-11% ROI on ML. Jerry's Panel-based resolver stayed +2-3% ROI. A
static equal-weight blend punishes the winner; a weighted blend can
adapt as models drift in and out of form.

Sport-universal. Reads model_track_records filtered by sport, uses
whichever windows are populated (prefers 14d > 30d > lifetime for
recency).

Interface:
    from ensemble_blend import blend_picks
    blended = blend_picks(model_outputs, sport='MLB', market='ml')
    # → {'pick': 'HOME'|'AWAY'|'OVER'|'UNDER'|None, 'confidence': 0-1,
    #    'contributors': [{model, pick, weight, hit_rate}, ...],
    #    'agreement': 0-1}

model_outputs shape:
    {
      'RESOLVER_SIDE': 'HOME',
      'MODEL_SPREAD': 'AWAY',
      'PANEL_TOTAL': None,  # unavailable for this game
      ...
    }
Only non-None models contribute.

DESIGN CHOICES:
- Weight = model's recommended_weight from tracker (already tuned in
  compute_hint_weight — 0.3 for losing models, up to 2.5 for winning)
- Recency: use 14d window if sample n>=25; else 30d; else lifetime.
  A model with 46% hit rate lifetime but 60% over last 14d SHOULD get
  a higher weight — recency matters more than lifetime baseline.
- Pick = argmax(sum of weights per side)
- Confidence = winning_side_weight / total_weight (0.5 = split, 1.0 = unanimous)
- Agreement = fraction of models that voted for the winning side
"""
from __future__ import annotations
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

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

SB = os.environ.get('SUPABASE_URL', '')
KEY = os.environ.get('SUPABASE_KEY', '')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'} if KEY else {}


# Recency preference — 14d most recent, fall back to broader windows
# when samples are thin.
_WINDOW_PRIORITY = ['14d', '30d', '90d', 'lifetime']
_MIN_N_FOR_WINDOW = 25  # need this many graded picks in a window to trust it


# Cache: (sport, market) → {model_name: (weight, hit_rate, window, n)}
# Refreshed when read_weights() is called with force=True; otherwise
# reuses within a script run to avoid hammering PostgREST.
_WEIGHTS_CACHE = {}


def read_weights(sport: str, market: str,
                 force: bool = False) -> dict:
    """Return {model_name: (weight, hit_rate_pct, window, n)} for the
    best-available window per model.

    Chooses per-model window by walking _WINDOW_PRIORITY and picking
    the first window where n >= _MIN_N_FOR_WINDOW. Falls back to any
    window with data if none qualify.
    """
    # Normalize inputs — table stores sport/market in uppercase
    sport = (sport or '').upper()
    market = (market or '').upper()
    key = (sport, market)
    if not force and key in _WEIGHTS_CACHE:
        return _WEIGHTS_CACHE[key]
    if not SB or not KEY:
        _WEIGHTS_CACHE[key] = {}
        return {}
    r = requests.get(
        f'{SB}/rest/v1/model_track_records',
        headers=H,
        params={
            'sport': f'eq.{sport}',
            'market': f'eq.{market}',
            'select': 'model_name,bucket_window,hit_rate,sample_n,recommended_weight',
            'limit': '500',
        },
        timeout=15,
    )
    if r.status_code != 200:
        _WEIGHTS_CACHE[key] = {}
        return {}
    rows = r.json() or []
    # Group by model_name → {window: row}
    by_model = defaultdict(dict)
    for row in rows:
        m = row.get('model_name')
        w = row.get('bucket_window')
        if not m or not w: continue
        by_model[m][w] = row

    out = {}
    for model, windows in by_model.items():
        chosen = None
        # Prefer the most recent window with sufficient sample
        for wname in _WINDOW_PRIORITY:
            r = windows.get(wname)
            if r and (r.get('sample_n') or 0) >= _MIN_N_FOR_WINDOW:
                chosen = (r, wname)
                break
        # Fall back to any window that has ANY data
        if chosen is None:
            for wname in _WINDOW_PRIORITY:
                r = windows.get(wname)
                if r:
                    chosen = (r, wname)
                    break
        if chosen is None: continue
        row, wname = chosen
        weight = row.get('recommended_weight') or 1.0
        hit_pct = row.get('hit_rate')
        n = row.get('sample_n') or 0
        out[model] = (float(weight), hit_pct, wname, n)

    _WEIGHTS_CACHE[key] = out
    return out


def blend_picks(model_outputs: dict, sport: str, market: str,
                weights_override: Optional[dict] = None) -> dict:
    """Blend model picks into a single ensemble decision.

    model_outputs: {'RESOLVER_SIDE': 'HOME', 'MODEL_SPREAD': 'AWAY', ...}
                   None values are skipped (model didn't fire for this game).
    sport: 'MLB' | 'NFL' | ...
    market: 'ml' | 'spread' | 'total' (must match model_track_records)
    weights_override: for backtest — pass a fixed weights dict instead
                      of reading from the tracker

    Returns:
        {
          'pick': 'HOME'|'AWAY'|'OVER'|'UNDER'|None,
          'confidence': float 0-1,
          'agreement': float 0-1,
          'contributors': [{'model', 'pick', 'weight', 'hit_rate', 'window', 'n'}, ...],
          'total_weight': float,
          'per_side': {'HOME': ..., 'AWAY': ..., ...},
        }
    """
    weights = weights_override if weights_override is not None else read_weights(sport, market)
    per_side = defaultdict(float)
    contributors = []
    total_weight = 0.0
    for model, pick in model_outputs.items():
        if pick is None: continue
        pick = str(pick).upper()
        entry = weights.get(model)
        if entry is None:
            # Unknown model — give equal weight = 1.0
            weight, hit_rate, window, n = 1.0, None, 'no_data', 0
        else:
            weight, hit_rate, window, n = entry
        per_side[pick] += weight
        total_weight += weight
        contributors.append({
            'model': model, 'pick': pick, 'weight': round(weight, 3),
            'hit_rate': hit_rate, 'window': window, 'n': n,
        })

    if not per_side or total_weight <= 0:
        return {'pick': None, 'confidence': 0.0, 'agreement': 0.0,
                'contributors': contributors, 'total_weight': 0.0,
                'per_side': {}}

    winner = max(per_side.keys(), key=lambda k: per_side[k])
    winner_weight = per_side[winner]
    confidence = winner_weight / total_weight
    n_for_winner = sum(1 for c in contributors if c['pick'] == winner)
    agreement = n_for_winner / len(contributors) if contributors else 0.0

    return {
        'pick': winner,
        'confidence': round(confidence, 3),
        'agreement': round(agreement, 3),
        'contributors': contributors,
        'total_weight': round(total_weight, 3),
        'per_side': {k: round(v, 3) for k, v in per_side.items()},
    }


if __name__ == '__main__':
    # Quick smoke test — sample a live weight lookup
    for market in ('ml', 'total'):
        w = read_weights('MLB', market)
        print(f'MLB {market}: {len(w)} models tracked')
        for m, (weight, hit, window, n) in sorted(w.items(), key=lambda x: -x[1][0]):
            hit_s = f'{hit:.1f}%' if hit is not None else '-'
            print(f'  {m:<20} weight={weight:.2f}  hit={hit_s:>6}  window={window:<10}  n={n}')
        print()

    # Sample blend
    sample = {'RESOLVER_SIDE': 'HOME', 'MODEL_SPREAD': 'AWAY'}
    print(f'Blend sample: {sample}')
    result = blend_picks(sample, sport='MLB', market='ml')
    print(f'  pick={result["pick"]} · conf={result["confidence"]} · agreement={result["agreement"]}')
    for c in result['contributors']:
        print(f'    {c}')
