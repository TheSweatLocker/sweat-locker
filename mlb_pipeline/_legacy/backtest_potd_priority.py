"""Broader POTD strategy backtest.

Tests 7 selection strategies for daily Play of the Day, simulating each
against historical mlb_game_results. Uses fields already stored: nrfi_score,
nrfi_result, spread_delta, signal_confluence_net, projected_spread, projected_
total, close_total, home_xera, away_xera, total_result, home_win.

Strategies:

  A. CURRENT    PRIME confluence (≥+4 AND |delta|≥2.0) → STRONG (≥+2 AND
                |delta|≥1.5). NRFI 90-94 first.

  B. MAG_ONLY   |spread_delta| ≥3.0 → ≥2.0. NRFI 90-94 first. No confluence.

  C. NRFI_ONLY  Only NRFI 90-94 plays. Skips otherwise.

  D. XERA_GAP   ML team with better xERA when |xera_gap| ≥ 2.5. NRFI 90-94 first.

  E. TOTAL_UNDER  Total UNDER when projected_total ≤ close_total - 1.5.
                  NRFI 90-94 first.

  F. TOTAL_BOTH   Total OVER or UNDER when |projected - close| ≥ 1.5.
                  NRFI 90-94 first.

  G. NRFI_OR_XERA NRFI 90-94 → xERA gap ≥2.5 ML → none.

  H. NRFI_OR_TOTAL NRFI 90-94 → total |delta| ≥ 1.5 → none.

For each strategy, simulate POTD selection per date (highest-priority game wins
when multiple qualify), evaluate by actual outcome.

Usage: python backtest_potd_priority.py
"""
import os
import sys
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def sb_get(table, params):
    qs = urllib.parse.urlencode(params, safe=",.()")
    url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
    req = urllib.request.Request(
        url, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_resolved(start_date="2026-04-01"):
    """Pull resolved games with all signals + outcomes."""
    all_rows = []
    offset = 0
    while True:
        rows = sb_get("mlb_game_results", {
            "game_date": f"gte.{start_date}",
            "home_win": "not.is.null",
            "select": ("game_date,home_team,away_team,nrfi_score,nrfi_result,"
                       "signal_confluence_net,spread_delta,projected_spread,"
                       "projected_total,close_total,home_sp_xera,away_sp_xera,"
                       "home_first_inning_era,away_first_inning_era,"
                       "total_result,home_win"),
            "order": "game_date.asc",
            "limit": "1000",
            "offset": str(offset),
        })
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return all_rows


def _f(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


# ============================================================
# STRATEGY DEFINITIONS
# Each returns (pick_type, side_or_total_dir, magnitude_for_ranking)
# pick_type: 'nrfi' | 'ml' | 'total_over' | 'total_under'
# side: 'home' | 'away' (for ml), or None
# ============================================================

def s_a_current(g):
    nrfi = g.get('nrfi_score') or 0
    sd = _f(g.get('spread_delta'))
    cn = g.get('signal_confluence_net')
    if 90 <= nrfi <= 94:
        return ('nrfi', None, 100 + nrfi)
    if cn is not None and sd is not None and int(cn) >= 4 and abs(sd) >= 2.0:
        side = 'home' if _f(g.get('projected_spread')) and _f(g.get('projected_spread')) > 0 else 'away'
        return ('ml', side, 50 + abs(sd) + int(cn) * 0.5)
    if 88 <= nrfi <= 89:
        return ('nrfi', None, 80 + nrfi)
    if cn is not None and sd is not None and int(cn) >= 2 and abs(sd) >= 1.5:
        side = 'home' if _f(g.get('projected_spread')) and _f(g.get('projected_spread')) > 0 else 'away'
        return ('ml', side, 40 + abs(sd) + int(cn) * 0.5)
    return None


def s_b_magnitude(g):
    nrfi = g.get('nrfi_score') or 0
    sd = _f(g.get('spread_delta'))
    if 90 <= nrfi <= 94:
        return ('nrfi', None, 100 + nrfi)
    if sd is not None and abs(sd) >= 3.0:
        side = 'home' if _f(g.get('projected_spread')) and _f(g.get('projected_spread')) > 0 else 'away'
        return ('ml', side, 60 + abs(sd))
    if 88 <= nrfi <= 89:
        return ('nrfi', None, 80 + nrfi)
    if sd is not None and abs(sd) >= 2.0:
        side = 'home' if _f(g.get('projected_spread')) and _f(g.get('projected_spread')) > 0 else 'away'
        return ('ml', side, 40 + abs(sd))
    return None


def s_c_nrfi_only(g):
    nrfi = g.get('nrfi_score') or 0
    if 90 <= nrfi <= 94:
        return ('nrfi', None, 100 + nrfi)
    return None


def s_d_xera_gap(g):
    nrfi = g.get('nrfi_score') or 0
    if 90 <= nrfi <= 94:
        return ('nrfi', None, 100 + nrfi)
    hx, ax = _f(g.get('home_sp_xera')), _f(g.get('away_sp_xera'))
    if hx is not None and ax is not None:
        gap = abs(hx - ax)
        if gap >= 2.5:
            # Better xERA team's ML
            side = 'home' if hx < ax else 'away'
            return ('ml', side, 60 + gap)
        if gap >= 1.5:
            side = 'home' if hx < ax else 'away'
            return ('ml', side, 40 + gap)
    return None


def s_e_total_under(g):
    nrfi = g.get('nrfi_score') or 0
    if 90 <= nrfi <= 94:
        return ('nrfi', None, 100 + nrfi)
    pt, ct = _f(g.get('projected_total')), _f(g.get('close_total'))
    if pt is not None and ct is not None:
        delta = pt - ct
        if delta <= -1.5:
            return ('total_under', None, 60 + abs(delta))
    return None


def s_f_total_both(g):
    nrfi = g.get('nrfi_score') or 0
    if 90 <= nrfi <= 94:
        return ('nrfi', None, 100 + nrfi)
    pt, ct = _f(g.get('projected_total')), _f(g.get('close_total'))
    if pt is not None and ct is not None:
        delta = pt - ct
        if delta >= 1.5:
            return ('total_over', None, 60 + abs(delta))
        if delta <= -1.5:
            return ('total_under', None, 60 + abs(delta))
    return None


def s_g_nrfi_or_xera(g):
    nrfi = g.get('nrfi_score') or 0
    if 90 <= nrfi <= 94:
        return ('nrfi', None, 100 + nrfi)
    hx, ax = _f(g.get('home_sp_xera')), _f(g.get('away_sp_xera'))
    if hx is not None and ax is not None and abs(hx - ax) >= 2.5:
        side = 'home' if hx < ax else 'away'
        return ('ml', side, 60 + abs(hx - ax))
    return None


def s_h_nrfi_or_total(g):
    nrfi = g.get('nrfi_score') or 0
    if 90 <= nrfi <= 94:
        return ('nrfi', None, 100 + nrfi)
    pt, ct = _f(g.get('projected_total')), _f(g.get('close_total'))
    if pt is not None and ct is not None:
        delta = pt - ct
        if abs(delta) >= 1.5:
            return ('total_over' if delta > 0 else 'total_under', None, 60 + abs(delta))
    return None


# ============================================================
# OUTCOME EVALUATION
# ============================================================

def evaluate_pick(g, pick_type, side):
    if pick_type == 'nrfi':
        res = (g.get('nrfi_result') or '').upper()
        if res not in ('NRFI', 'YRFI'):
            return None
        return 1 if res == 'NRFI' else 0
    if pick_type == 'ml':
        hw = g.get('home_win')
        if hw is None:
            return None
        if side == 'home':
            return 1 if hw else 0
        return 0 if hw else 1
    if pick_type == 'total_over':
        res = (g.get('total_result') or '').lower()
        if res == 'push' or not res:
            return None
        return 1 if res == 'over' else 0
    if pick_type == 'total_under':
        res = (g.get('total_result') or '').lower()
        if res == 'push' or not res:
            return None
        return 1 if res == 'under' else 0
    return None


def simulate_strategy(games_by_date, strategy_fn):
    wins = losses = skips = 0
    by_type = defaultdict(lambda: {'w': 0, 'l': 0})
    for date in sorted(games_by_date.keys()):
        candidates = []
        for g in games_by_date[date]:
            r = strategy_fn(g)
            if r is None:
                continue
            pick_type, side, rank = r
            candidates.append((rank, g, pick_type, side))
        if not candidates:
            skips += 1
            continue
        candidates.sort(key=lambda x: -x[0])
        rank, g, pick_type, side = candidates[0]
        win = evaluate_pick(g, pick_type, side)
        if win is None:
            skips += 1
            continue
        if win == 1:
            wins += 1
            by_type[pick_type]['w'] += 1
        else:
            losses += 1
            by_type[pick_type]['l'] += 1
    return wins, losses, skips, by_type


def main():
    print("Fetching resolved games...")
    rows = fetch_resolved()
    print(f"Total resolved games: {len(rows)}")

    by_date = defaultdict(list)
    for r in rows:
        by_date[r['game_date']].append(r)
    dates = sorted(by_date.keys())
    print(f"Date range: {dates[0]} to {dates[-1]} ({len(dates)} days)")
    print()

    strategies = [
        ('A. CURRENT (PRIME confluence)',                s_a_current),
        ('B. MAG_ONLY (|spread_delta| ≥3.0 → ≥2.0)',     s_b_magnitude),
        ('C. NRFI_ONLY (90-94 only, skip otherwise)',     s_c_nrfi_only),
        ('D. XERA_GAP (xERA gap ≥2.5 ML)',                s_d_xera_gap),
        ('E. TOTAL_UNDER (proj ≤ close - 1.5)',           s_e_total_under),
        ('F. TOTAL_BOTH (|proj - close| ≥ 1.5)',          s_f_total_both),
        ('G. NRFI 90-94 OR xERA gap ≥2.5 ML',             s_g_nrfi_or_xera),
        ('H. NRFI 90-94 OR Total |delta| ≥1.5',           s_h_nrfi_or_total),
    ]

    print(f"{'STRATEGY':50s} {'RECORD':>14s} {'RATE':>8s} {'BREAKDOWN':>40s} {'SKIP':>6s}")
    print("-" * 130)
    rows_out = []
    for label, fn in strategies:
        w, l, sk, bt = simulate_strategy(by_date, fn)
        n = w + l
        rate = (w/n*100) if n else 0
        breakdown = ' / '.join(f"{k}:{v['w']}-{v['l']}" for k, v in sorted(bt.items()))
        rows_out.append((label, w, l, rate, breakdown, sk))
        print(f"{label:50s} {f'{w}-{l}':>14s} {rate:>7.1f}% {breakdown:>40s} {sk:>6d}")

    print()
    print("=== Best by hit rate (min 10 picks) ===")
    valid = [r for r in rows_out if (r[1] + r[2]) >= 10]
    valid.sort(key=lambda r: -r[3])
    for label, w, l, rate, breakdown, sk in valid:
        print(f"  {rate:5.1f}%  {w}-{l}  {label}")


if __name__ == '__main__':
    main()
