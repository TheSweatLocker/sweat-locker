"""30-day historical A/B: v1 vs v2 edge_weight on graded games.

For each historical MLB game_context.primary_play row where mlb_game_results
exists:
  1. Extract _ensemble_sources (each chip has signal_key, class, side,
     n, strength, and precomputed v1 contribution).
  2. For each chip, look up CURRENT signal_registry (hit_rate, tier).
     Same registry used for both v1 and v2 rescoring — the goal is to
     isolate the effect of the WEIGHTING FORMULA change alone, not
     registry drift.
  3. Rescore each side per-market with v1 and v2 formulas. Pick winner
     per side.
  4. Grade v1 winner and v2 winner against mlb_game_results (total /
     ML — RL/side/spread grading omitted for now, focus on the two
     markets that most picks ride).
  5. Report hit rate delta.

Positive result: v2 hits more picks than v1 on same slates.
"""
import os
import sys
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')
SB = os.environ['SUPABASE_URL']
K = os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': f'Bearer {K}'}

BREAKEVEN = 0.524
MAX_CLASS_SHARE = 0.40
FADE_MAX_SHARE = 0.35
SAMPLE_MIN_N = 25
RAMP_UP_PRIOR = 0.50
BAYES_PRIOR_STRENGTH = 20
BAYES_PRIOR_MEAN = BREAKEVEN

CAND_BY_MARKET = {'ml': ('HOME_ML','AWAY_ML'), 'rl': ('HOME_RL','AWAY_RL'), 'total': ('OVER','UNDER')}


def edge_weight_v1(hit_rate, n, tier):
    if tier == 'ANTI_VALIDATED':
        return 0.0
    if hit_rate is None or n <= 0:
        return 0.20 if tier in ('DISCOVERY', 'UNVALIDATED', 'VALIDATED') else 0.15
    if n < SAMPLE_MIN_N:
        return RAMP_UP_PRIOR
    edge_pp = hit_rate - BREAKEVEN
    if edge_pp <= 0:
        return 0.0
    edge_component = min(edge_pp / 0.12, 1.0)
    n_component = min(math.log1p(n) / math.log(101), 1.0)
    return round(edge_component * n_component, 4)


def edge_weight_v2(hit_rate, n, tier):
    if tier == 'ANTI_VALIDATED':
        return 0.0
    if hit_rate is None or n <= 0:
        return 0.20 if tier in ('DISCOVERY', 'UNVALIDATED', 'VALIDATED') else 0.15
    wins = hit_rate * n
    alpha_0 = BAYES_PRIOR_MEAN * BAYES_PRIOR_STRENGTH
    beta_0 = (1 - BAYES_PRIOR_MEAN) * BAYES_PRIOR_STRENGTH
    posterior_mean = (alpha_0 + wins) / (alpha_0 + beta_0 + n)
    edge_pp = posterior_mean - BREAKEVEN
    if edge_pp <= 0:
        return 0.0
    edge_component = min(edge_pp / 0.12, 1.0)
    n_eff = n + BAYES_PRIOR_STRENGTH
    n_component = min(math.log1p(n_eff) / math.log(101 + BAYES_PRIOR_STRENGTH), 1.0)
    return round(edge_component * n_component, 4)


def load_registry():
    """Snapshot current signal_registry — signal_name → (hit_rate, sample_n, tier)."""
    out = {}
    offset = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/signal_registry',
                         headers=H,
                         params={'select': 'signal_name,tier,hit_rate,sample_n',
                                 'limit': '1000', 'offset': str(offset)}, timeout=15)
        rows = r.json() if r.status_code == 200 else []
        if not isinstance(rows, list) or not rows: break
        for row in rows:
            name = row.get('signal_name')
            if not name: continue
            hr = row.get('hit_rate')
            if hr is not None:
                try: hr = float(hr)
                except (TypeError, ValueError): hr = None
                if hr is not None and hr > 1.0: hr = hr / 100.0
            out[name] = {
                'hit_rate': hr,
                'sample_n': int(row.get('sample_n') or 0),
                'tier': row.get('tier') or 'UNVALIDATED',
            }
        if len(rows) < 1000: break
        offset += 1000
    return out


def score_market(chips, weight_fn, registry, diag=None):
    """Given chips (list from _ensemble_sources), rescore with weight_fn.
    Returns dict side -> adjusted_total.

    Uses HISTORICAL sample_n from the chip (n at scoring time) and
    CURRENT registry hit_rate + tier (best available proxy). This
    isolates the effect of the weighting formula change.
    """
    per_side = defaultdict(list)
    for c in chips:
        key = c.get('signal_key') or ''
        # Strip __fade to look up base signal in registry
        lookup_key = key.rstrip('_').replace('__fade', '')
        reg = registry.get(lookup_key) or registry.get(key) or {}
        hr = reg.get('hit_rate')
        # Use HISTORICAL n from chip (what was known at scoring time),
        # not current registry n (may have grown since).
        n = c.get('n', 0) or reg.get('sample_n', 0)
        tier = reg.get('tier', 'UNVALIDATED')
        if diag is not None:
            diag['total'] += 1
            if hr is None: diag['no_hr'] += 1
            elif n < 25: diag['low_n'] += 1
            elif tier == 'ANTI_VALIDATED': diag['anti'] += 1
            else: diag['edge_path'] += 1
        # Handle fade signals: they invert hr in the OPPOSITE side
        if key.endswith('__fade') or key.endswith('_fade'):
            if hr is not None:
                hr = 1.0 - hr
                # Fade tier is promoted to VALIDATED/DISCOVERY per fade rules
                tier = 'VALIDATED' if (hr >= 0.55 and n >= 50) else 'DISCOVERY'
        w = weight_fn(hr, n, tier)
        strength = c.get('strength') or 0.5
        contrib = w * strength
        if contrib == 0: continue
        per_side[c.get('side')].append({
            'signal_key': key,
            'signal_class': c.get('class') or 'other',
            'weight': w,
            'contribution': contrib,
        })

    scored = {}
    for side, side_chips in per_side.items():
        raw_total = sum(c['contribution'] for c in side_chips)
        class_share = defaultdict(float)
        for c in side_chips:
            class_share[c['signal_class']] += c['contribution']
        adjusted = raw_total
        if raw_total > 0:
            cap_ratio = MAX_CLASS_SHARE / (1.0 - MAX_CLASS_SHARE)
            for cls_name, share in class_share.items():
                others = raw_total - share
                if others <= 0:
                    max_allowed = raw_total * MAX_CLASS_SHARE
                else:
                    max_allowed = others * cap_ratio
                if share > max_allowed:
                    adjusted -= (share - max_allowed)
        # Aggregate FADE cap (v2 only in production but apply here for both to isolate weight-formula effect)
        fade_share = sum(c['contribution'] for c in side_chips
                         if c['signal_key'].endswith('__fade') or c['signal_key'].endswith('_fade'))
        if fade_share > 0 and adjusted > 0:
            fade_cap_ratio = FADE_MAX_SHARE / (1.0 - FADE_MAX_SHARE)
            others = adjusted - fade_share
            if others > 0:
                max_fade = others * fade_cap_ratio
                if fade_share > max_fade:
                    adjusted -= (fade_share - max_fade)
        scored[side] = adjusted
    return scored


def pick_winner(scored, market):
    cands = CAND_BY_MARKET[market]
    scored_here = {s: v for s, v in scored.items() if s in cands}
    if not scored_here: return None
    return max(scored_here, key=scored_here.get)


def side_from_market(market, home_score, away_score, close_total, close_spread):
    """Given actuals, what side won?"""
    if market == 'total':
        if close_total is None: return None
        tot = home_score + away_score
        if tot == close_total: return None  # push
        return 'OVER' if tot > close_total else 'UNDER'
    if market == 'ml':
        if home_score == away_score: return None
        return 'HOME_ML' if home_score > away_score else 'AWAY_ML'
    if market == 'rl':
        if close_spread is None: return None
        margin = home_score - away_score
        # close_spread is home spread (negative if home favored)
        adj_margin = margin + float(close_spread)  # home covers if adj_margin > 0
        if adj_margin == 0: return None
        return 'HOME_RL' if adj_margin > 0 else 'AWAY_RL'
    return None


def main():
    days = int(os.environ.get('BACKTEST_DAYS', '30'))
    today = date.today()
    end = today - timedelta(days=1)  # exclude today (unresolved)
    start = end - timedelta(days=days-1)
    print(f'=== 30d backtest: {start} to {end} ({days} days) ===')

    # Load registry snapshot
    print('Loading signal_registry ...')
    registry = load_registry()
    print(f'  {len(registry)} signal_name entries')

    # Load games with primary_play + results
    print('Loading games...')
    r = requests.get(f'{SB}/rest/v1/mlb_game_context',
                     headers=H,
                     params={'game_date': f'gte.{start}',
                             'select': 'game_date,game_id,home_team,away_team,primary_play,close_total,close_spread',
                             'limit': '2000'}, timeout=30)
    games = r.json() if r.status_code == 200 else []
    print(f'  {len(games)} game_context rows in window')

    # Load results indexed
    r2 = requests.get(f'{SB}/rest/v1/mlb_game_results',
                      headers=H,
                      params={'game_date': f'gte.{start}',
                              'select': 'game_id,home_score,away_score', 'limit': '2000'}, timeout=30)
    res_rows = r2.json() if r2.status_code == 200 else []
    results = {r['game_id']: r for r in res_rows if r.get('home_score') is not None}
    print(f'  {len(results)} graded games')

    stats = {
        'total_games': 0,
        'v1': {'total': {'w':0,'l':0}, 'ml': {'w':0,'l':0}, 'rl': {'w':0,'l':0}},
        'v2': {'total': {'w':0,'l':0}, 'ml': {'w':0,'l':0}, 'rl': {'w':0,'l':0}},
        'agree': 0,
        'diff': 0,
    }
    diag = {'total': 0, 'no_hr': 0, 'low_n': 0, 'anti': 0, 'edge_path': 0}

    for g in games:
        gid = g.get('game_id')
        res = results.get(gid)
        if not res: continue
        pp = g.get('primary_play') or {}
        chips = pp.get('_ensemble_sources') or []
        if not chips: continue

        stats['total_games'] += 1
        hs, ascore = res['home_score'], res['away_score']

        v1_scored = score_market(chips, edge_weight_v1, registry, diag=diag)
        v2_scored = score_market(chips, edge_weight_v2, registry)

        for mkt in ('total', 'ml'):
            actual = side_from_market(mkt, hs, ascore, g.get('close_total'), g.get('close_spread'))
            if actual is None: continue
            v1_pick = pick_winner(v1_scored, mkt)
            v2_pick = pick_winner(v2_scored, mkt)
            if v1_pick == v2_pick:
                stats['agree'] += 1
            else:
                stats['diff'] += 1
            if v1_pick:
                key = 'w' if v1_pick == actual else 'l'
                stats['v1'][mkt][key] += 1
            if v2_pick:
                key = 'w' if v2_pick == actual else 'l'
                stats['v2'][mkt][key] += 1

    print('\n' + '=' * 60)
    print(f'RESULTS on {stats["total_games"]} graded games')
    print('=' * 60)
    for mkt in ('total', 'ml'):
        v1_w, v1_l = stats['v1'][mkt]['w'], stats['v1'][mkt]['l']
        v2_w, v2_l = stats['v2'][mkt]['w'], stats['v2'][mkt]['l']
        v1_pct = 100 * v1_w / max(1, v1_w + v1_l)
        v2_pct = 100 * v2_w / max(1, v2_w + v2_l)
        delta = v2_pct - v1_pct
        print(f'\n{mkt.upper():5} market:')
        print(f'  v1: {v1_w}-{v1_l}  ({v1_pct:.1f}%)')
        print(f'  v2: {v2_w}-{v2_l}  ({v2_pct:.1f}%)   delta={delta:+.1f}pp')

    ag_pct = 100 * stats['agree'] / max(1, stats['agree'] + stats['diff'])
    print(f'\nv1/v2 agreement: {stats["agree"]}/{stats["agree"]+stats["diff"]} ({ag_pct:.1f}%)')
    print(f'Games where formula produced different pick: {stats["diff"]}')
    print(f'\nChip diagnostic (across all rescores):')
    print(f'  total chips seen: {diag["total"]}')
    print(f'  no_hr in registry (both formulas identical): {diag["no_hr"]}')
    print(f'  low_n (n<25) — v1 hits RAMP_UP=0.50, v2 diverges: {diag["low_n"]}')
    print(f'  anti_validated (both 0): {diag["anti"]}')
    print(f'  edge-path (formulas nearly identical at high n): {diag["edge_path"]}')


if __name__ == '__main__':
    main()
