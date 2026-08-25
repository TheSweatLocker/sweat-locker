"""Backtest ensemble_scorer vs current compute_primary_play (2026-08-16).

Replay N days of resolved MLB games:
  For each game_context row that has a mlb_game_results row:
    1. Score with ensemble_scorer.score_game(sport='MLB', ctx=ctx)
    2. Read the current primary_play (already stored in ctx.primary_play)
    3. Grade both picks against the actual outcome
    4. Aggregate hit rate + ROI per tier, per market

Reports:
  - Per-tier record + hit rate + ROI for both systems
  - Overlap analysis: when both pick, do they agree? Who wins when they disagree?
  - Concentration analysis: how many picks does ensemble make vs current?
  - Sample-size caveats

CLI:
  python backtest_ensemble_scorer.py --days 30
  python backtest_ensemble_scorer.py --days 60 --sport MLB
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

import requests

_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']
KEY = os.environ['SUPABASE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

from ensemble_scorer import score_game, Decision


def american_to_win_payout(odds) -> float:
    """1u wager, decimal payout on a WIN."""
    if odds is None: return 0.91  # -110 default
    try: odds = int(odds)
    except (TypeError, ValueError): return 0.91
    if odds >= 100: return odds / 100.0
    if odds <= -100: return 100.0 / abs(odds)
    return 0.91


def grade_pick(pick_type: str, pick_side: str, pick_line, home_score: int,
                away_score: int, close_total: float, close_spread: float,
                home_ml: int, away_ml: int) -> tuple[str, float]:
    """Return ('Win'|'Loss'|'Push', units_delta @ 1u stake).

    Uses closing prices for grading — most honest ROI given we don't
    have the exact odds each pick was made at."""
    total_runs = home_score + away_score
    home_won = home_score > away_score

    if pick_type == 'ml':
        won = (pick_side == 'HOME' and home_won) or (pick_side == 'AWAY' and not home_won)
        payout = american_to_win_payout(home_ml if pick_side == 'HOME' else away_ml)
        return ('Win' if won else 'Loss', payout if won else -1.0)

    if pick_type == 'rl':
        if pick_line is None:
            # Assume MLB run line +/- 1.5
            pick_line = -1.5 if pick_side == 'HOME' else 1.5
        try: line = float(pick_line)
        except (TypeError, ValueError): return ('Push', 0.0)
        # side + line: HOME -1.5 wins if home wins by 2+; AWAY +1.5 wins if away doesn't lose by 2+
        margin = home_score - away_score
        if pick_side == 'HOME':
            covered = margin > -line if line < 0 else margin >= -line  # HOME -1.5 → home_margin > 1.5
        else:
            covered = -margin > -line if line < 0 else -margin >= -line
        # Simpler: adjusted_margin vs line
        adj = margin + (line if pick_side == 'HOME' else -line)
        if adj > 0: return ('Win', 0.91)  # RL usually ~-110 either side
        if adj == 0: return ('Push', 0.0)
        return ('Loss', -1.0)

    if pick_type == 'total':
        if pick_line is None or close_total is None:
            return ('Push', 0.0)
        try: line = float(pick_line or close_total)
        except (TypeError, ValueError): return ('Push', 0.0)
        if abs(total_runs - line) < 0.01:
            return ('Push', 0.0)
        went_over = total_runs > line
        won = (pick_side == 'OVER' and went_over) or (pick_side == 'UNDER' and not went_over)
        return ('Win' if won else 'Loss', 0.91 if won else -1.0)

    return ('Push', 0.0)


def fetch_games(days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    all_rows = []
    for off in range(0, 5000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_context'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&select=*&limit=1000&offset={off}',
            headers=H, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        all_rows += chunk
        if len(chunk) < 1000: break
    return all_rows


def fetch_results(days: int) -> dict:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    all_rows = []
    for off in range(0, 5000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_results'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&select=game_id,home_score,away_score&limit=1000&offset={off}',
            headers=H, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        all_rows += chunk
        if len(chunk) < 1000: break
    return {r['game_id']: r for r in all_rows}


def _tier_bucket(tier: str | None) -> str:
    if tier in ('PRIME', 'STRONG', 'LEAN', 'PASS'): return tier
    return 'OTHER'


def run(days: int = 30):
    print(f'=== ensemble_scorer backtest — MLB — last {days} days ===\n')
    games = fetch_games(days)
    results = fetch_results(days)
    print(f'  {len(games)} games fetched, {len(results)} with results\n')

    # Aggregators
    ens_stats = defaultdict(lambda: {'w': 0, 'l': 0, 'p': 0, 'units': 0.0})
    cur_stats = defaultdict(lambda: {'w': 0, 'l': 0, 'p': 0, 'units': 0.0})
    market_stats_ens = defaultdict(lambda: {'w': 0, 'l': 0, 'p': 0, 'units': 0.0})
    market_stats_cur = defaultdict(lambda: {'w': 0, 'l': 0, 'p': 0, 'units': 0.0})
    agreement = {'both_pick_same': 0, 'both_pick_diff': 0, 'ens_only': 0, 'cur_only': 0, 'both_pass': 0}
    disagreements = []  # list of (game, ens_pick, cur_pick, both_results)

    graded = 0
    for ctx in games:
        gid = ctx.get('game_id')
        if not gid: continue
        res = results.get(gid)
        if not res: continue
        try:
            hs = int(res['home_score'])
            asc = int(res['away_score'])
        except (TypeError, ValueError, KeyError): continue

        close_total = ctx.get('close_total')
        close_spread = ctx.get('close_spread')
        home_ml = ctx.get('home_ml_close')
        away_ml = ctx.get('away_ml_close')

        # ── Ensemble decision ──
        ens = None
        try:
            ens = score_game('MLB', ctx)
        except Exception as e:
            pass

        # ── Current primary_play ──
        cur_pp = ctx.get('primary_play')
        if isinstance(cur_pp, str):
            try: cur_pp = json.loads(cur_pp)
            except Exception: cur_pp = None
        cur_type = (cur_pp or {}).get('type') if isinstance(cur_pp, dict) else None
        cur_tier = (cur_pp or {}).get('tier') if isinstance(cur_pp, dict) else None
        cur_side = (cur_pp or {}).get('side') if isinstance(cur_pp, dict) else None
        cur_line = (cur_pp or {}).get('line') if isinstance(cur_pp, dict) else None

        # Grade ensemble
        if ens is not None:
            grade_ens = grade_pick(ens.type, ens.side, ens.line, hs, asc,
                                    close_total, close_spread, home_ml, away_ml)
            if grade_ens[0] == 'Win':
                ens_stats[ens.tier]['w'] += 1; ens_stats[ens.tier]['units'] += grade_ens[1]
                market_stats_ens[ens.type]['w'] += 1; market_stats_ens[ens.type]['units'] += grade_ens[1]
            elif grade_ens[0] == 'Loss':
                ens_stats[ens.tier]['l'] += 1; ens_stats[ens.tier]['units'] += grade_ens[1]
                market_stats_ens[ens.type]['l'] += 1; market_stats_ens[ens.type]['units'] += grade_ens[1]
            else:
                ens_stats[ens.tier]['p'] += 1
                market_stats_ens[ens.type]['p'] += 1

        # Grade current
        cur_graded = False
        if cur_pp and cur_type in ('ml', 'rl', 'total') and cur_side:
            grade_cur = grade_pick(cur_type, cur_side, cur_line, hs, asc,
                                    close_total, close_spread, home_ml, away_ml)
            if grade_cur[0] == 'Win':
                cur_stats[cur_tier or 'OTHER']['w'] += 1
                cur_stats[cur_tier or 'OTHER']['units'] += grade_cur[1]
                market_stats_cur[cur_type]['w'] += 1
                market_stats_cur[cur_type]['units'] += grade_cur[1]
                cur_graded = True
            elif grade_cur[0] == 'Loss':
                cur_stats[cur_tier or 'OTHER']['l'] += 1
                cur_stats[cur_tier or 'OTHER']['units'] += grade_cur[1]
                market_stats_cur[cur_type]['l'] += 1
                market_stats_cur[cur_type]['units'] += grade_cur[1]
                cur_graded = True
            else:
                cur_stats[cur_tier or 'OTHER']['p'] += 1
                market_stats_cur[cur_type]['p'] += 1

        # Agreement tracking
        if ens is not None and cur_pp and cur_side:
            same_side = (ens.type == cur_type and ens.side == cur_side)
            if same_side:
                agreement['both_pick_same'] += 1
            else:
                agreement['both_pick_diff'] += 1
                disagreements.append({
                    'game': f'{ctx.get("away_team")} @ {ctx.get("home_team")}',
                    'date': ctx.get('game_date'),
                    'ens': f'{ens.display_label} [{ens.tier}]',
                    'cur': f'{(cur_pp or {}).get("label", "?")} [{cur_tier}]',
                    'winner': 'HOME' if hs > asc else 'AWAY',
                    'score': f'{asc}-{hs}',
                })
        elif ens is not None:
            agreement['ens_only'] += 1
        elif cur_pp and cur_side:
            agreement['cur_only'] += 1
        else:
            agreement['both_pass'] += 1

        graded += 1

    # ── REPORT ──
    def _fmt(stats):
        w, l, p, u = stats['w'], stats['l'], stats['p'], stats['units']
        n_dec = w + l
        if n_dec == 0:
            return f'{w}-{l}-{p}  HR=--   ROI=--     (empty)'
        hr = round(100 * w / n_dec, 1)
        roi = round(100 * u / n_dec, 1)
        return f'{w}-{l}-{p}  HR={hr}%  ROI={roi:+.1f}%  ({n_dec}u risked, {u:+.2f}u net)'

    print(f'--- Graded games: {graded} ---\n')

    print('ENSEMBLE by tier:')
    for tier in ['PRIME', 'STRONG', 'LEAN']:
        s = ens_stats.get(tier, {'w': 0, 'l': 0, 'p': 0, 'units': 0.0})
        if s['w'] + s['l'] + s['p'] > 0:
            print(f'  {tier:<7} {_fmt(s)}')
    total_ens = {'w': sum(ens_stats[t]['w'] for t in ens_stats),
                 'l': sum(ens_stats[t]['l'] for t in ens_stats),
                 'p': sum(ens_stats[t]['p'] for t in ens_stats),
                 'units': sum(ens_stats[t]['units'] for t in ens_stats)}
    print(f'  {"TOTAL":<7} {_fmt(total_ens)}\n')

    print('CURRENT primary_play by tier:')
    for tier in ['PRIME', 'STRONG', 'LEAN', 'OTHER']:
        s = cur_stats.get(tier, {'w': 0, 'l': 0, 'p': 0, 'units': 0.0})
        if s['w'] + s['l'] + s['p'] > 0:
            print(f'  {tier:<7} {_fmt(s)}')
    total_cur = {'w': sum(cur_stats[t]['w'] for t in cur_stats),
                 'l': sum(cur_stats[t]['l'] for t in cur_stats),
                 'p': sum(cur_stats[t]['p'] for t in cur_stats),
                 'units': sum(cur_stats[t]['units'] for t in cur_stats)}
    print(f'  {"TOTAL":<7} {_fmt(total_cur)}\n')

    print('BY MARKET:')
    for mkt in ['ml', 'rl', 'total']:
        e = market_stats_ens.get(mkt, {'w': 0, 'l': 0, 'p': 0, 'units': 0.0})
        c = market_stats_cur.get(mkt, {'w': 0, 'l': 0, 'p': 0, 'units': 0.0})
        if e['w'] + e['l'] + e['p'] > 0 or c['w'] + c['l'] + c['p'] > 0:
            print(f'  {mkt.upper():<6} ENS: {_fmt(e)}')
            print(f'  {mkt.upper():<6} CUR: {_fmt(c)}')
    print()

    print('AGREEMENT:')
    for k, v in agreement.items():
        print(f'  {k}: {v}')
    print()

    if disagreements:
        print(f'--- DISAGREEMENTS (first 10 of {len(disagreements)}) ---')
        for d in disagreements[:10]:
            print(f'  {d["date"]}  {d["game"][:35]:<35}  ENS={d["ens"]:<25} CUR={d["cur"]:<25} · {d["winner"]} won {d["score"]}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=30)
    args = p.parse_args()
    run(days=args.days)


if __name__ == '__main__':
    main()
