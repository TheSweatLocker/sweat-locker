"""Backtest weighted_composite_spread with alternative weight configs
(2026-08-07). Follow-up to backtest_ensemble finding that MODEL_SPREAD
(V4) has degraded from 58% ATS at set-time (7/21) to 44.4% ML in last
14 days — heavily weighted in current spread composite (0.3-0.5).

Backtests candidate reweights against 776 games in mlb_game_results
that have all 4 spread lenses populated (jerry_pred_spread,
model_pred_spread, projected_spread, panel_implied_margin) + close +
outcome. Chronological 60/40 train/test split.

Configs tested (both no-panel and with-panel variants):
  current:  matches current tier_discipline_gate.weighted_composite_spread
  proposed: v4 → 0.1, jerry → 0.4-0.5 (based on 14d hit rates)
  v4_zero:  v4 fully removed (upper-bound scenario)
  jerry_solo: jerry_spread alone
  panel_solo: panel_implied_margin alone (when available)

Method: for each config on each game, compute weighted composite → pick
= HOME if composite > 0, AWAY if composite < 0. Grade vs actual winner
(home_won = home_score > away_score). Push games (home_score == away_score)
excluded. Report W-L, hit%, ROI% at -110.
"""
from __future__ import annotations
import os
import sys
from collections import defaultdict
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


def load_games() -> list:
    """Pull all resolved games with the spread lenses populated."""
    all_rows = []
    offset = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_results',
            headers={**H, 'Range': f'{offset}-{offset+999}'},
            params={
                'jerry_pred_spread': 'not.is.null',
                'model_pred_spread': 'not.is.null',
                'close_spread': 'not.is.null',
                'home_score': 'not.is.null',
                'away_score': 'not.is.null',
                'select': ('game_date,game_id,home_score,away_score,'
                           'close_spread,jerry_pred_spread,model_pred_spread,'
                           'projected_spread,panel_implied_margin'),
                'order': 'game_date.asc',
            },
            timeout=30,
        )
        if r.status_code not in (200, 206): break
        batch = r.json() or []
        if not batch: break
        all_rows.extend(batch)
        if len(batch) < 1000: break
        offset += 1000
    return all_rows


def _weighted(parts: list) -> float | None:
    """parts = [(weight, value), ...]. Ignores None values. Normalizes."""
    used = [(w, v) for w, v in parts if v is not None and w > 0]
    if not used: return None
    norm = sum(w for w, _ in used)
    if norm == 0: return None
    return sum(w * float(v) for w, v in used) / norm


def compute_composite(g, config):
    """config is a dict of weights: {v3, v4, jerry, panel}. Returns
    (composite_spread, used_panel_variant_bool)."""
    v3 = g.get('projected_spread')
    v4 = g.get('model_pred_spread')
    jerry = g.get('jerry_pred_spread')
    panel = g.get('panel_implied_margin')

    used_panel = panel is not None and config.get('panel', 0) > 0
    if used_panel:
        parts = [(config.get('v3',0), v3), (config.get('v4',0), v4),
                 (config.get('jerry',0), jerry), (config.get('panel',0), panel)]
    else:
        # Fall back to no-panel variant (use no_panel_ prefix if provided)
        parts = [(config.get('v3_np', config.get('v3',0)), v3),
                 (config.get('v4_np', config.get('v4',0)), v4),
                 (config.get('jerry_np', config.get('jerry',0)), jerry)]
    return _weighted(parts), used_panel


def grade_config(games, config):
    """Return {w, l, n_panel, n_nopanel, w_panel, w_nopanel, hit_pct, roi_pct}."""
    w = l = n_panel = n_nopanel = w_panel = w_nopanel = 0
    for g in games:
        comp, used_panel = compute_composite(g, config)
        if comp is None: continue
        hs, as_ = g['home_score'], g['away_score']
        if hs == as_: continue  # push — extra innings
        home_won = hs > as_
        pick_home = comp > 0
        if pick_home == home_won:
            w += 1
            if used_panel: w_panel += 1
            else: w_nopanel += 1
        else:
            l += 1
        if used_panel: n_panel += 1
        else: n_nopanel += 1
    n = w + l
    hit_pct = 100 * w / n if n else 0
    roi_pct = ((w * 0.909) - l * 1.0) / n * 100 if n else 0
    return {
        'w': w, 'l': l, 'n': n, 'hit_pct': hit_pct, 'roi_pct': roi_pct,
        'n_panel': n_panel, 'w_panel': w_panel,
        'hit_panel_pct': 100*w_panel/n_panel if n_panel else 0,
        'n_nopanel': n_nopanel, 'w_nopanel': w_nopanel,
        'hit_nopanel_pct': 100*w_nopanel/n_nopanel if n_nopanel else 0,
    }


CONFIGS = {
    'CURRENT (v4-heavy)':          {'v3':0.1, 'v4':0.5, 'jerry':0.0, 'panel':0.4,
                                    'v3_np':0.5, 'v4_np':0.3, 'jerry_np':0.2},
    'PROPOSED (v4 → 0.1)':         {'v3':0.1, 'v4':0.1, 'jerry':0.4, 'panel':0.4,
                                    'v3_np':0.4, 'v4_np':0.1, 'jerry_np':0.5},
    'V4-ZERO (kill v4)':           {'v3':0.2, 'v4':0.0, 'jerry':0.4, 'panel':0.4,
                                    'v3_np':0.4, 'v4_np':0.0, 'jerry_np':0.6},
    'JERRY-SOLO':                  {'jerry':1.0, 'v3_np':0.0, 'v4_np':0.0, 'jerry_np':1.0},
    'JERRY+PANEL':                 {'v3':0.0, 'v4':0.0, 'jerry':0.5, 'panel':0.5,
                                    'v3_np':0.0, 'v4_np':0.0, 'jerry_np':1.0},
    'V3+JERRY only':               {'v3':0.5, 'v4':0.0, 'jerry':0.5, 'panel':0.0,
                                    'v3_np':0.5, 'v4_np':0.0, 'jerry_np':0.5},
}


def main():
    print('=== backtest_composite_weights ===')
    print('  loading games...')
    games = load_games()
    print(f'  {len(games)} games with all lenses + outcomes')
    if not games:
        return

    # Chronological 60/40 split
    split_idx = int(len(games) * 0.6)
    train = games[:split_idx]
    test = games[split_idx:]
    print(f'  train: {len(train)} games ({train[0]["game_date"]} → {train[-1]["game_date"]})')
    print(f'  test:  {len(test)} games ({test[0]["game_date"]} → {test[-1]["game_date"]})')
    print()

    for split_label, subset in [('FULL', games), ('TEST-ONLY', test)]:
        print(f'══════ {split_label} ({len(subset)} games) ══════')
        print(f'  {"config":<30}  {"W-L":<10}  {"hit%":>6}  {"ROI%":>7}  {"panel-var":>18}  {"no-panel":>15}')
        print(f'  {"-"*30}  {"-"*10}  {"-"*6}  {"-"*7}  {"-"*18}  {"-"*15}')
        for name, cfg in CONFIGS.items():
            r = grade_config(subset, cfg)
            panel_s = f'{r["w_panel"]}-{r["n_panel"]-r["w_panel"]} ({r["hit_panel_pct"]:.1f}%)' if r['n_panel'] else '-'
            np_s = f'{r["w_nopanel"]}-{r["n_nopanel"]-r["w_nopanel"]} ({r["hit_nopanel_pct"]:.1f}%)' if r['n_nopanel'] else '-'
            marker = ' <--' if 'PROPOSED' in name else ''
            print(f'  {name:<30}  {r["w"]}-{r["l"]:<5}  {r["hit_pct"]:>5.1f}%  {r["roi_pct"]:>+6.1f}%  {panel_s:>18}  {np_s:>15}{marker}')
        print()


if __name__ == '__main__':
    main()
