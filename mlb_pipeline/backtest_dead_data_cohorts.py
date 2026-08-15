"""Backtest the three cohorts wired in 2026-08-15 against historical results.

Cohorts under test:
  1. Handedness SIDE edge — wrc_vs_opp_hand delta >= 15 → back advantaged side
  2. Handedness TOTAL fade — avg wrc_vs_opp_hand <= 90 → UNDER
  3. L10 momentum SIDE — last10_run_diff delta >= 2.0 AND leader >= +1.0 → back leader

mlb_game_results has ALL these fields populated historically (verified 8/15):
  home_wrc_vs_opp_hand, away_wrc_vs_opp_hand
  home_ops_last7, away_ops_last7
  ops_vs_opp_hand fields

Late-inning × bullpen cohort NOT backtestable historically because
bullpen_effective_era is a new 8/15 field with no historical archive.
"""
from __future__ import annotations
import os, sys
from pathlib import Path
from datetime import datetime, timedelta

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


def fetch_results(days: int = 90):
    """Pull last N days of completed games with the fields we need."""
    since = (datetime.now().date() - timedelta(days=days)).isoformat()
    fields = ('game_date,home_team,away_team,home_score,away_score,'
              'close_total,total_result,'
              'home_wrc_vs_opp_hand,away_wrc_vs_opp_hand,'
              'home_ops_vs_opp_hand,away_ops_vs_opp_hand,'
              'home_ops_last7,away_ops_last7,'
              'home_ops_last14,away_ops_last14')
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_results'
        f'?select={fields}&game_date=gte.{since}'
        f'&home_score=not.is.null&away_score=not.is.null&limit=5000',
        headers=H, timeout=30)
    if r.status_code != 200:
        print(f'✗ fetch {r.status_code}: {r.text[:200]}')
        return []
    return r.json() or []


def bt_handedness_side(games):
    """Handedness SIDE: home_wrc_vs_opp_hand - away_wrc_vs_opp_hand >= 15
    → back HOME. Symmetric for AWAY.  Advantaged side needs wRC+ >= 105."""
    wins = 0; total = 0; n_home = 0; n_away = 0
    for g in games:
        h = g.get('home_wrc_vs_opp_hand'); a = g.get('away_wrc_vs_opp_hand')
        if h is None or a is None: continue
        hs = g.get('home_score'); as_ = g.get('away_score')
        if hs is None or as_ is None or hs == as_: continue
        try: delta = float(h) - float(a); h = float(h); a = float(a)
        except (TypeError, ValueError): continue
        pick = None
        if delta >= 15 and h >= 105: pick = 'HOME'; n_home += 1
        elif delta <= -15 and a >= 105: pick = 'AWAY'; n_away += 1
        if pick is None: continue
        total += 1
        actual = 'HOME' if hs > as_ else 'AWAY'
        if pick == actual: wins += 1
    return {'name': 'handedness_side_asymmetric_15+',
            'wins': wins, 'total': total,
            'rate': (wins / total) if total else 0,
            'n_home': n_home, 'n_away': n_away}


def bt_handedness_total_under(games):
    """TOTAL: avg wrc_vs_opp_hand <= 90 → UNDER."""
    wins = 0; total = 0
    for g in games:
        h = g.get('home_wrc_vs_opp_hand'); a = g.get('away_wrc_vs_opp_hand')
        tr = (g.get('total_result') or '').strip().lower()
        if h is None or a is None or tr not in ('over', 'under'): continue
        try:
            h = float(h); a = float(a)
        except (TypeError, ValueError): continue
        if h <= 0 or a <= 0: continue  # skip zero-pop rows
        if (h + a) / 2 > 90: continue
        total += 1
        if tr == 'under': wins += 1
    return {'name': 'handedness_total_under_avg<=90',
            'wins': wins, 'total': total,
            'rate': (wins / total) if total else 0}


def bt_ops_l7_hot_side(games):
    """SIDE: ops_last7 delta >= 0.100 AND leader >= 0.780 → back leader."""
    wins = 0; total = 0
    for g in games:
        h = g.get('home_ops_last7'); a = g.get('away_ops_last7')
        hs = g.get('home_score'); as_ = g.get('away_score')
        if None in (h, a, hs, as_): continue
        if hs == as_: continue
        try: h = float(h); a = float(a); delta = h - a
        except (TypeError, ValueError): continue
        pick = None
        if delta >= 0.100 and h >= 0.780: pick = 'HOME'
        elif delta <= -0.100 and a >= 0.780: pick = 'AWAY'
        if pick is None: continue
        total += 1
        actual = 'HOME' if hs > as_ else 'AWAY'
        if pick == actual: wins += 1
    return {'name': 'ops_l7_hot_side_delta>=0.100',
            'wins': wins, 'total': total,
            'rate': (wins / total) if total else 0}


def bt_ops_l14_regression_total_under(games):
    """TOTAL: both teams ops_last14 <= 0.680 → UNDER (bat drought)."""
    wins = 0; total = 0
    for g in games:
        h = g.get('home_ops_last14'); a = g.get('away_ops_last14')
        tr = (g.get('total_result') or '').strip().lower()
        if h is None or a is None or tr not in ('over', 'under'): continue
        try:
            h = float(h); a = float(a)
        except (TypeError, ValueError): continue
        if h > 0.680 or a > 0.680: continue
        total += 1
        if tr == 'under': wins += 1
    return {'name': 'ops_l14_dual_drought_total_under',
            'wins': wins, 'total': total,
            'rate': (wins / total) if total else 0}


def bt_handedness_side_thresh(games, min_delta, min_edge):
    wins = 0; total = 0
    for g in games:
        h = g.get('home_wrc_vs_opp_hand'); a = g.get('away_wrc_vs_opp_hand')
        hs = g.get('home_score'); as_ = g.get('away_score')
        if None in (h, a, hs, as_): continue
        if hs == as_: continue
        try: h = float(h); a = float(a); delta = h - a
        except (TypeError, ValueError): continue
        pick = None
        if delta >= min_delta and h >= min_edge: pick = 'HOME'
        elif delta <= -min_delta and a >= min_edge: pick = 'AWAY'
        if pick is None: continue
        total += 1
        actual = 'HOME' if hs > as_ else 'AWAY'
        if pick == actual: wins += 1
    return {'name': f'hand_side_d>={min_delta}_edge>={min_edge}',
            'wins': wins, 'total': total,
            'rate': (wins / total) if total else 0}


def bt_ops_hot_side_thresh(games, key, min_delta, min_edge):
    wins = 0; total = 0
    for g in games:
        h = g.get(f'home_{key}'); a = g.get(f'away_{key}')
        hs = g.get('home_score'); as_ = g.get('away_score')
        if None in (h, a, hs, as_): continue
        if hs == as_: continue
        try: h = float(h); a = float(a); delta = h - a
        except (TypeError, ValueError): continue
        pick = None
        if delta >= min_delta and h >= min_edge: pick = 'HOME'
        elif delta <= -min_delta and a >= min_edge: pick = 'AWAY'
        if pick is None: continue
        total += 1
        actual = 'HOME' if hs > as_ else 'AWAY'
        if pick == actual: wins += 1
    return {'name': f'{key}_side_d>={min_delta}_edge>={min_edge}',
            'wins': wins, 'total': total,
            'rate': (wins / total) if total else 0}


def bt_dual_drought_thresh(games, key, max_ops):
    wins = 0; total = 0
    for g in games:
        h = g.get(f'home_{key}'); a = g.get(f'away_{key}')
        tr = (g.get('total_result') or '').strip().lower()
        if h is None or a is None or tr not in ('over', 'under'): continue
        try:
            h = float(h); a = float(a)
        except (TypeError, ValueError): continue
        if h > max_ops or a > max_ops: continue
        total += 1
        if tr == 'under': wins += 1
    return {'name': f'dual_{key}<={max_ops}_UNDER',
            'wins': wins, 'total': total,
            'rate': (wins / total) if total else 0}


def bt_dual_hot_over(games, key, min_ops):
    wins = 0; total = 0
    for g in games:
        h = g.get(f'home_{key}'); a = g.get(f'away_{key}')
        tr = (g.get('total_result') or '').strip().lower()
        if h is None or a is None or tr not in ('over', 'under'): continue
        try:
            h = float(h); a = float(a)
        except (TypeError, ValueError): continue
        if h < min_ops or a < min_ops: continue
        total += 1
        if tr == 'over': wins += 1
    return {'name': f'dual_{key}>={min_ops}_OVER',
            'wins': wins, 'total': total,
            'rate': (wins / total) if total else 0}


def run():
    print('=== Dead-data cohort backtest v2 (threshold sweep) 2026-08-15 ===')
    games90 = fetch_results(days=90)
    print(f'\n── Last 90d ({len(games90)} completed games) ──')

    print('\n[HANDEDNESS SIDE — asymmetric wrc_vs_opp_hand]')
    for delta in (15, 20, 25, 30):
        for edge in (100, 105, 110, 115):
            r = bt_handedness_side_thresh(games90, delta, edge)
            if r['total'] >= 20:
                wl = f'{r["wins"]}-{r["total"]-r["wins"]}'
                print(f'  {r["name"]:<40} · {wl:>10} ({r["rate"]*100:5.1f}%) · n={r["total"]}')

    print('\n[OPS L7 SIDE — asymmetric hot bats]')
    for delta in (0.080, 0.100, 0.130, 0.160):
        for edge in (0.750, 0.780, 0.820, 0.850):
            r = bt_ops_hot_side_thresh(games90, 'ops_last7', delta, edge)
            if r['total'] >= 30:
                wl = f'{r["wins"]}-{r["total"]-r["wins"]}'
                print(f'  {r["name"]:<40} · {wl:>10} ({r["rate"]*100:5.1f}%) · n={r["total"]}')

    print('\n[DUAL OPS DROUGHT — TOTAL UNDER]')
    for key in ('ops_last7', 'ops_last14'):
        for cap in (0.650, 0.680, 0.700, 0.720):
            r = bt_dual_drought_thresh(games90, key, cap)
            if r['total'] >= 20:
                wl = f'{r["wins"]}-{r["total"]-r["wins"]}'
                print(f'  {r["name"]:<40} · {wl:>10} ({r["rate"]*100:5.1f}%) · n={r["total"]}')

    print('\n[DUAL OPS HOT — TOTAL OVER]')
    for key in ('ops_last7', 'ops_last14'):
        for floor in (0.780, 0.820, 0.850, 0.900):
            r = bt_dual_hot_over(games90, key, floor)
            if r['total'] >= 20:
                wl = f'{r["wins"]}-{r["total"]-r["wins"]}'
                print(f'  {r["name"]:<40} · {wl:>10} ({r["rate"]*100:5.1f}%) · n={r["total"]}')


if __name__ == '__main__':
    run()
