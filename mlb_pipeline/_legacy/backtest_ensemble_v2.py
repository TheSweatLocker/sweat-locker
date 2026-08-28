"""Backtest ensemble_scorer v2 (with backfilled tiers) vs current
compute_primary_play (2026-08-16).

For every resolved MLB game in the window:
  1. Score with ensemble_scorer.score_game (returns per-market decisions
     + a top_market pointer)
  2. Read the current primary_play (already stored in ctx.primary_play)
  3. Grade both against actual outcome
  4. Aggregate: per-tier record, per-market record, top-pick record

Reports:
  * ENSEMBLE per market: ML/RL/Total records
  * ENSEMBLE top-pick record (the pick with highest conviction per game)
  * CURRENT primary_play record (baseline)
  * Agreement analysis: when both pick, do they agree? Who wins on disagreement?
  * ROI at flat 1u using closing prices for grading

CLI:
  python backtest_ensemble_v2.py --days 60
  python backtest_ensemble_v2.py --days 30 --top-only
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

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

from ensemble_scorer import score_game
from backfill_signal_tiers import grade_side


def american_to_win_payout(odds) -> float:
    if odds is None: return 0.91
    try: odds = int(odds)
    except (TypeError, ValueError): return 0.91
    if odds >= 100: return odds / 100.0
    if odds <= -100: return 100.0 / abs(odds)
    return 0.91


def payout_for_pick(market: str, side: str, ctx: dict) -> float:
    """Return the win payout for a pick using close prices."""
    if market == 'ml':
        odds = ctx.get('home_ml_close') if side == 'HOME' else ctx.get('away_ml_close')
        return american_to_win_payout(odds)
    # RL and Total default to -110 unless we have prices (we don't for RL/Total closing)
    return 0.91


def fetch_games(days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ctx_rows = []
    for off in range(0, 10000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_context'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&select=*&limit=1000&offset={off}',
            headers=H, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        ctx_rows += chunk
        if len(chunk) < 1000: break

    results = {}
    for off in range(0, 10000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/mlb_game_results'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&select=game_id,home_score,away_score&limit=1000&offset={off}',
            headers=H, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        for row in chunk:
            results[row['game_id']] = row
        if len(chunk) < 1000: break

    out = []
    for c in ctx_rows:
        r = results.get(c.get('game_id'))
        if not r: continue
        try:
            c['_home_score'] = int(r['home_score'])
            c['_away_score'] = int(r['away_score'])
        except (TypeError, ValueError, KeyError): continue
        out.append(c)
    return out


def run(days: int = 60, top_only: bool = False):
    print(f'=== ensemble v2 backtest · MLB · last {days} days ===\n')
    games = fetch_games(days)
    print(f'  {len(games)} resolved games\n')

    # Ensemble stats — split by tier + by market
    ens_by_tier = defaultdict(lambda: {'w':0, 'l':0, 'p':0, 'u':0.0})
    ens_by_market = defaultdict(lambda: {'w':0, 'l':0, 'p':0, 'u':0.0})
    ens_top = {'w':0, 'l':0, 'p':0, 'u':0.0}
    # Current primary_play stats
    cur_by_tier = defaultdict(lambda: {'w':0, 'l':0, 'p':0, 'u':0.0})
    cur_by_market = defaultdict(lambda: {'w':0, 'l':0, 'p':0, 'u':0.0})
    agreement = defaultdict(int)

    for ctx in games:
        # Score with ensemble
        decision = None
        try: decision = score_game('MLB', ctx)
        except Exception: pass

        # Grade each market picked by ensemble
        picks_made = []
        if decision is not None:
            for market_name in ('ml', 'rl', 'total'):
                md = getattr(decision, market_name)
                if md.pick is None: continue
                if top_only and market_name != decision.top_market: continue
                res = grade_side(md.pick, ctx)
                payout = payout_for_pick(market_name, md.side, ctx)
                if res == 'W':
                    ens_by_tier[md.tier]['w'] += 1; ens_by_tier[md.tier]['u'] += payout
                    ens_by_market[market_name]['w'] += 1; ens_by_market[market_name]['u'] += payout
                    picks_made.append((market_name, md, res, payout))
                elif res == 'L':
                    ens_by_tier[md.tier]['l'] += 1; ens_by_tier[md.tier]['u'] -= 1.0
                    ens_by_market[market_name]['l'] += 1; ens_by_market[market_name]['u'] -= 1.0
                    picks_made.append((market_name, md, res, -1.0))
                elif res == 'P':
                    ens_by_tier[md.tier]['p'] += 1
                    ens_by_market[market_name]['p'] += 1
                    picks_made.append((market_name, md, res, 0.0))

            # Also track top pick separately
            top = decision.top()
            if top.pick is not None:
                res = grade_side(top.pick, ctx)
                payout = payout_for_pick(top.market, top.side, ctx)
                if res == 'W':
                    ens_top['w'] += 1; ens_top['u'] += payout
                elif res == 'L':
                    ens_top['l'] += 1; ens_top['u'] -= 1.0
                elif res == 'P':
                    ens_top['p'] += 1

        # Grade current primary_play — parse side from label when 'side'
        # field is missing (Jerry-fallback primary_play rows don't set it).
        cur_pp = ctx.get('primary_play')
        if isinstance(cur_pp, str):
            try: cur_pp = json.loads(cur_pp)
            except: cur_pp = None
        if cur_pp and isinstance(cur_pp, dict):
            cur_type = cur_pp.get('type')
            cur_side = cur_pp.get('side')
            cur_label = cur_pp.get('label') or ''
            cur_tier = cur_pp.get('tier', 'OTHER')

            # Fallback: derive side from label
            if not cur_side and cur_label:
                lbl_low = cur_label.lower()
                if lbl_low.startswith('over ') or ' over ' in lbl_low:
                    cur_side = 'OVER'
                elif lbl_low.startswith('under ') or ' under ' in lbl_low:
                    cur_side = 'UNDER'
                else:
                    # e.g. "Dodgers ML" → match team names
                    home = str(ctx.get('home_team') or '')
                    away = str(ctx.get('away_team') or '')
                    if home and home in cur_label:
                        cur_side = 'HOME'
                    elif away and away in cur_label:
                        cur_side = 'AWAY'

            if cur_type in ('ml', 'rl', 'total') and cur_side:
                cand = f'{cur_side}_{cur_type.upper()}' if cur_type != 'total' else cur_side
                cand = cand.replace('_TOTAL', '')  # OVER/UNDER stay
                res = grade_side(cand, ctx)
                payout = payout_for_pick(cur_type, cur_side, ctx)
                if res == 'W':
                    cur_by_tier[cur_tier]['w'] += 1; cur_by_tier[cur_tier]['u'] += payout
                    cur_by_market[cur_type]['w'] += 1; cur_by_market[cur_type]['u'] += payout
                elif res == 'L':
                    cur_by_tier[cur_tier]['l'] += 1; cur_by_tier[cur_tier]['u'] -= 1.0
                    cur_by_market[cur_type]['l'] += 1; cur_by_market[cur_type]['u'] -= 1.0
                elif res == 'P':
                    cur_by_tier[cur_tier]['p'] += 1
                    cur_by_market[cur_type]['p'] += 1

    def _fmt(s):
        w, l, p, u = s['w'], s['l'], s['p'], s['u']
        n = w + l
        if n == 0: return '(no picks)'
        hr = round(100*w/n, 1)
        roi = round(100*u/n, 1)
        return f'{w}-{l}-{p}  HR={hr}%  ROI={roi:+.1f}%  ({n}u risked, {u:+.2f}u)'

    print('ENSEMBLE v2 by tier:')
    for tier in ('PRIME', 'STRONG', 'LEAN'):
        s = ens_by_tier.get(tier, {'w':0,'l':0,'p':0,'u':0.0})
        if s['w']+s['l']+s['p'] > 0: print(f'  {tier:<7} {_fmt(s)}')
    tot = {'w':sum(s['w'] for s in ens_by_tier.values()),
           'l':sum(s['l'] for s in ens_by_tier.values()),
           'p':sum(s['p'] for s in ens_by_tier.values()),
           'u':sum(s['u'] for s in ens_by_tier.values())}
    print(f'  {"TOTAL":<7} {_fmt(tot)}\n')

    print('ENSEMBLE v2 by market:')
    for mkt in ('ml', 'rl', 'total'):
        s = ens_by_market.get(mkt, {'w':0,'l':0,'p':0,'u':0.0})
        if s['w']+s['l']+s['p'] > 0: print(f'  {mkt.upper():<6} {_fmt(s)}')
    print()

    print(f'ENSEMBLE v2 top-pick only:')
    print(f'  {_fmt(ens_top)}\n')

    print('CURRENT primary_play by tier:')
    for tier in ('PRIME', 'STRONG', 'LEAN', 'OTHER'):
        s = cur_by_tier.get(tier, {'w':0,'l':0,'p':0,'u':0.0})
        if s['w']+s['l']+s['p'] > 0: print(f'  {tier:<7} {_fmt(s)}')
    tot_c = {'w':sum(s['w'] for s in cur_by_tier.values()),
             'l':sum(s['l'] for s in cur_by_tier.values()),
             'p':sum(s['p'] for s in cur_by_tier.values()),
             'u':sum(s['u'] for s in cur_by_tier.values())}
    print(f'  {"TOTAL":<7} {_fmt(tot_c)}\n')

    print('CURRENT primary_play by market:')
    for mkt in ('ml', 'rl', 'total'):
        s = cur_by_market.get(mkt, {'w':0,'l':0,'p':0,'u':0.0})
        if s['w']+s['l']+s['p'] > 0: print(f'  {mkt.upper():<6} {_fmt(s)}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=60)
    p.add_argument('--top-only', action='store_true')
    args = p.parse_args()
    run(days=args.days, top_only=args.top_only)


if __name__ == '__main__':
    main()
