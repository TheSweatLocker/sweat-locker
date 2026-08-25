"""Model dissent hit-rate audit (2026-08-08).

Answers: "when V4 dissents from Jerry + Panel consensus, does V4 win?"
Reads from mlb_game_context_snapshots (already-collected historical
predictions, snapshotted nightly by snapshot_mlb_game_context.py) and
joins to mlb_game_results (final scores).

CLI:
    python model_dissent_audit.py [--days 30] [--market total|ml]

Output: hit-rate breakdown per dissent pattern for ML and TOTAL.
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timedelta, timezone
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
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

_NOISE = 0.5  # ignore fractional edges under half-run


def _side_total(pred, line):
    if pred is None or line is None: return None
    p, l = float(pred), float(line)
    if p > l + _NOISE: return 'OVER'
    if p < l - _NOISE: return 'UNDER'
    return None


def _side_spread(pred, close_spread):
    """+jerry_pred_spread means home_favored beyond market. Positive delta → HOME lean."""
    if pred is None or close_spread is None: return None
    delta = float(pred) + float(close_spread)
    if delta > _NOISE: return 'HOME'
    if delta < -_NOISE: return 'AWAY'
    return None


def audit(days: int = 30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d')
    # Snapshots
    r = requests.get(f'{SB}/rest/v1/mlb_game_context_snapshots', headers=H,
                     params=[('snapshot_date', f'gte.{cutoff}'),
                             ('select', 'game_id,snapshot_date,close_total,close_spread,'
                                        'jerry_pred_total,model_pred_total,panel_implied_total,'
                                        'jerry_pred_spread,model_pred_spread'),
                             ('limit', '5000')],
                     timeout=30)
    snaps = r.json() if isinstance(r.json(), list) else []
    print(f'  {len(snaps)} snapshots pulled (>= {cutoff})')

    # Results
    gids = list({s['game_id'] for s in snaps})
    results = {}
    for i in range(0, len(gids), 100):
        chunk = gids[i:i+100]
        rr = requests.get(f'{SB}/rest/v1/mlb_game_results', headers=H,
                          params=[('game_id', f'in.({",".join(chunk)})'),
                                  ('select', 'game_id,total_runs,home_score,away_score,total_result')],
                          timeout=15)
        for row in (rr.json() if isinstance(rr.json(), list) else []):
            # 2026-08-08: mlb_game_results.total_runs is often null even when
            # home_score/away_score are populated. Derive total from scores.
            h, a = row.get('home_score'), row.get('away_score')
            if row.get('total_runs') is None and h is not None and a is not None:
                row['total_runs'] = h + a
            if row.get('total_runs') is not None:
                results[row['game_id']] = row
    print(f'  {len(results)} results found\n')

    v4_diss = {'OVER': [], 'UNDER': []}
    v4_agree = {'OVER': [], 'UNDER': []}
    jerry_diss = {'OVER': [], 'UNDER': []}
    panel_diss = {'OVER': [], 'UNDER': []}
    ml_v4_diss = {'HOME': [], 'AWAY': []}

    total_evaluated = 0
    for snap in snaps:
        res = results.get(snap['game_id'])
        if not res: continue
        line = snap.get('close_total'); tr = res.get('total_runs')
        if line is None or tr is None: continue
        if float(tr) == float(line): continue  # push
        actual = 'OVER' if float(tr) > float(line) else 'UNDER'
        v4 = _side_total(snap.get('model_pred_total'), line)
        jerry = _side_total(snap.get('jerry_pred_total'), line)
        panel = _side_total(snap.get('panel_implied_total'), line)
        others = [s for s in (jerry, panel) if s]
        if not (v4 and others): continue
        total_evaluated += 1
        if all(s != v4 for s in others):
            v4_diss[v4].append(v4 == actual)
        elif all(s == v4 for s in others):
            v4_agree[v4].append(v4 == actual)
        # Also break out jerry dissent (jerry vs v4+panel)
        others_j = [s for s in (v4, panel) if s]
        if jerry and others_j and all(s != jerry for s in others_j):
            jerry_diss[jerry].append(jerry == actual)
        others_p = [s for s in (v4, jerry) if s]
        if panel and others_p and all(s != panel for s in others_p):
            panel_diss[panel].append(panel == actual)

        # ML: v4 vs jerry spread direction (use jerry_pred_spread + close_spread)
        home_win = (res.get('home_score') or 0) > (res.get('away_score') or 0)
        ml_actual = 'HOME' if home_win else 'AWAY'
        v4_ml = _side_spread(snap.get('model_pred_spread'), snap.get('close_spread'))
        j_ml = _side_spread(snap.get('jerry_pred_spread'), snap.get('close_spread'))
        if v4_ml and j_ml and v4_ml != j_ml:
            ml_v4_diss[v4_ml].append(v4_ml == ml_actual)

    def _r(lst):
        n = len(lst); h = sum(1 for x in lst if x)
        return f'{h:>3}/{n:<3} = {round(100*h/max(n,1),1):5.1f}%'

    print(f'=== TOTAL DISSENT (n={total_evaluated} game-days) ===')
    print(f'  V4 OVER  dissents (Jerry+Panel say UNDER): V4 hit? {_r(v4_diss["OVER"])}')
    print(f'  V4 UNDER dissents (Jerry+Panel say OVER):  V4 hit? {_r(v4_diss["UNDER"])}')
    print(f'  V4 OVER  agrees w/ Jerry+Panel:            hit%   {_r(v4_agree["OVER"])}')
    print(f'  V4 UNDER agrees w/ Jerry+Panel:            hit%   {_r(v4_agree["UNDER"])}')
    print()
    print(f'  JERRY OVER  dissents (V4+Panel say UNDER):  Jerry hit? {_r(jerry_diss["OVER"])}')
    print(f'  JERRY UNDER dissents (V4+Panel say OVER):   Jerry hit? {_r(jerry_diss["UNDER"])}')
    print(f'  PANEL OVER  dissents:                       Panel hit? {_r(panel_diss["OVER"])}')
    print(f'  PANEL UNDER dissents:                       Panel hit? {_r(panel_diss["UNDER"])}')
    print()
    print(f'=== ML DISSENT (V4 vs Jerry spread) ===')
    print(f'  V4 HOME dissent (Jerry says AWAY): V4 hit? {_r(ml_v4_diss["HOME"])}')
    print(f'  V4 AWAY dissent (Jerry says HOME): V4 hit? {_r(ml_v4_diss["AWAY"])}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    args = ap.parse_args()
    audit(days=args.days)
