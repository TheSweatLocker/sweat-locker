"""Sport-universal sharp scenario matrix computation (2026-08-12).

Recomputes hit rates for EVERY meaningful public/sharp/model pattern
combination on the last N days of graded games. Emits:
  * `sharp_scenario_matrix` — aggregated hit rates per pattern
  * `sharp_scenario_game_matches` — per-game today's matches (for Jerry consumption)

Categories tracked (30+ patterns per sport):

  A. UNIVARIATE — single-signal buckets
     A1. money_bucket (5 buckets × 2 sides × 2 markets = 20 keys)
     A2. bets_bucket (same)

  B. BIVARIATE — 2-signal interactions
     B1. money_x_bets (5×5 grid per side per market)
     B2. money_x_model_agree (money band × MC direction agreement)
     B3. money_x_confluence_agree (money band × confluence direction)

  C. DIVERGENCE — the whale vs square axis
     C1. whale_divergence (money-bets ≥ 15) by side
     C2. square_divergence (bets-money ≥ 15) by side
     C3. balanced (|money-bets| < 5)

  D. LINE MOVEMENT — market response
     D1. rlm_alignment (line moved AGAINST public $ → sharp on other side)
     D2. line_move_direction (open→current delta by magnitude)

  E. CROSS-SIGNAL — the money-shot combinations
     E1. sharp_x_MC_aligned (high money% + MC agrees)
     E2. sharp_x_MC_split (high money% + MC opposes)
     E3. multi_signal_consensus (money + MC + confluence + Jerry all agree)

Sport-universal via `SPORT_CONFIG`. Adding a sport = 3-line config
entry. MLB fully wired; NFL/NCAAF ready when their game_context tables
carry oddscrowd_snapshot + align_status.

## Downstream

  * generate_prop_jerry_synthesis.py — reads game matches, feeds Jerry
  * generate_jerry_synthesis.py — same
  * apply_refit_verdict_override.py — auto-adjust when BACK/FADE fires
  * generate_sweat_card.py — surface matches per game

Usage:
    python compute_sharp_scenario_matrix.py [--sport MLB|ALL] [--window 90]
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

from pathlib import Path
_env = Path(__file__).parent / '.env'
if _env.exists():
    for line in _env.read_text().split('\n'):
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

SB = os.environ['SUPABASE_URL']; KEY = os.environ['SUPABASE_KEY']
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


# ─── Sport plugin registry ────────────────────────────────────────────
SPORT_CONFIG = {
    'MLB': {
        'ctx_table': 'mlb_game_context',
        'results_table': 'mlb_game_results',
    },
    'NFL': {
        'ctx_table': 'nfl_game_context',
        'results_table': 'nfl_game_results',
    },
    'NCAAF': {
        'ctx_table': 'ncaaf_game_context',
        'results_table': 'ncaaf_game_results',
    },
}


def _f(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _parse_json(v):
    if not v: return {}
    if isinstance(v, str):
        try: return json.loads(v)
        except: return {}
    return v if isinstance(v, dict) else {}


def _bucket_pct(pct):
    if pct is None: return None
    if pct >= 75: return '75+'
    if pct >= 65: return '65-74'
    if pct >= 55: return '55-64'
    if pct >= 40: return '40-54'
    return '<40'


def _grade_ml(pick_side, hs, as_):
    if hs == as_: return 'push'
    won = (pick_side == 'HOME' and hs > as_) or (pick_side == 'AWAY' and as_ > hs)
    return 'win' if won else 'loss'


def _grade_total(pick_side, line, hs, as_):
    if line is None: return None
    total = hs + as_
    if total == line: return 'push'
    if pick_side == 'OVER': return 'win' if total > line else 'loss'
    if pick_side == 'UNDER': return 'win' if total < line else 'loss'
    return None


def extract_scenarios(ctx, sport):
    """Return list of (market, category, scenario_key, scenario_label, pick_side)
    tuples for this game. Each tuple represents ONE observed pattern in the
    game's context — later we grade by side and aggregate hit rates."""
    scenarios = []
    snap = _parse_json(ctx.get('oddscrowd_snapshot'))
    align = _parse_json(ctx.get('align_status'))

    conf_net = ctx.get('signal_confluence_net') or 0
    # POSITIVE = AWAY favored per confirmed convention
    conf_side = 'AWAY' if conf_net > 0 else 'HOME' if conf_net < 0 else None
    mc_side = str(ctx.get('mc_high_conf_side') or '').upper()
    mc_pct = ctx.get('mc_high_conf_pct') or 0
    mc_conf_side = mc_side if mc_side in ('HOME', 'AWAY') and mc_pct >= 0.65 else None

    for market_key in ('ml', 'total'):
        seg = snap.get(market_key, {}) or {}
        pick = (seg.get('pick') or '').upper()
        money = seg.get('money')
        bets = seg.get('bets')
        if pick not in ('HOME', 'AWAY', 'OVER', 'UNDER'): continue
        if money is None or bets is None: continue

        m_bucket = _bucket_pct(money)
        b_bucket = _bucket_pct(bets)
        div = money - bets

        # A1. money_bucket alone
        if m_bucket:
            scenarios.append((market_key, 'money_bucket',
                              f'money_{m_bucket}_{pick.lower()}',
                              f'{market_key.upper()} · public $ {m_bucket}% on {pick}',
                              pick))
        # A2. bets_bucket alone
        if b_bucket:
            scenarios.append((market_key, 'bets_bucket',
                              f'bets_{b_bucket}_{pick.lower()}',
                              f'{market_key.upper()} · bets {b_bucket}% on {pick}',
                              pick))
        # B1. money x bets grid
        if m_bucket and b_bucket:
            scenarios.append((market_key, 'money_x_bets',
                              f'grid_m{m_bucket}_b{b_bucket}_{pick.lower()}',
                              f'{market_key.upper()} · $ {m_bucket}% / bets {b_bucket}% on {pick}',
                              pick))
        # C1. whale divergence (money > bets + 15)
        if div >= 15:
            scenarios.append((market_key, 'whale_divergence',
                              f'whale_${money}_bets{bets}_{pick.lower()}'[:60],
                              f'{market_key.upper()} · $-heavy divergent (${money}/bets{bets}) on {pick}',
                              pick))
            # Consolidated key
            scenarios.append((market_key, 'whale_divergence',
                              f'whale_div15+_{pick.lower()}',
                              f'{market_key.upper()} · whale div ≥15pp on {pick}',
                              pick))
        # C2. square divergence
        elif bets - money >= 15:
            scenarios.append((market_key, 'square_divergence',
                              f'square_div15+_{pick.lower()}',
                              f'{market_key.upper()} · square div ≥15pp on {pick}',
                              pick))
        # C3. balanced
        elif abs(div) < 5:
            scenarios.append((market_key, 'balanced',
                              f'balanced_{m_bucket}_{pick.lower()}',
                              f'{market_key.upper()} · balanced $/bets {m_bucket}% on {pick}',
                              pick))

        # E1/E2. money × MC alignment
        if money >= 65 and mc_conf_side:
            if mc_conf_side == pick:
                scenarios.append((market_key, 'money_x_model',
                                  f'money65+_MC_agree_{pick.lower()}',
                                  f'{market_key.upper()} · $65+% + MC agrees on {pick}',
                                  pick))
            else:
                scenarios.append((market_key, 'money_x_model',
                                  f'money65+_MC_opposes_{pick.lower()}',
                                  f'{market_key.upper()} · $65+% but MC opposes ({pick} vs MC {mc_conf_side})',
                                  pick))

        # E3. Multi-signal consensus (money + confluence + MC all same side)
        if money >= 65 and conf_side == pick and mc_conf_side == pick:
            scenarios.append((market_key, 'cross_signal',
                              f'triple_consensus_{pick.lower()}',
                              f'{market_key.upper()} · triple consensus ($+conf+MC) on {pick}',
                              pick))

        # D1. RLM by direction.
        # 2026-08-12: use align_status.sharp_side (correct field). Prior
        # version read lens_side which is the MODEL-LENS consensus side,
        # not the RLM sharp side. sharp_side is populated by align_status_
        # common._compute_rlm() when line movement contradicts public $.
        # 90d verification: RLM_TOTAL sharp_side hits 57.6% (n=33) → real
        # BACK signal. RLM_ML thin sample (n=19) at 47% neutral.
        seg_align = align.get(market_key) if isinstance(align.get(market_key), dict) else {}
        if seg_align.get('rlm'):
            sharp_side = str(seg_align.get('sharp_side') or '').upper()
            # Normalize single letters
            if sharp_side == 'H': sharp_side = 'HOME'
            elif sharp_side == 'A': sharp_side = 'AWAY'
            elif sharp_side == 'O': sharp_side = 'OVER'
            elif sharp_side == 'U': sharp_side = 'UNDER'
            if sharp_side in ('HOME', 'AWAY', 'OVER', 'UNDER'):
                scenarios.append((market_key, 'rlm_alignment',
                                  f'rlm_sharp_{sharp_side.lower()}',
                                  f'{market_key.upper()} · RLM sharp on {sharp_side}',
                                  sharp_side))

    return scenarios


def grade_scenario(sport, market, pick_side, ctx, res):
    """Return 'win'/'loss'/'push'/None for pick_side."""
    hs = res.get('home_score'); as_ = res.get('away_score')
    if hs is None or as_ is None: return None
    try: hs = int(hs); as_ = int(as_)
    except: return None
    if market == 'ml':
        return _grade_ml(pick_side, hs, as_) if pick_side in ('HOME', 'AWAY') else None
    if market == 'total':
        line = ctx.get('close_total')
        return _grade_total(pick_side, line, hs, as_) if pick_side in ('OVER', 'UNDER') else None
    return None


def compute_jerry_hint(hit_rate, n):
    """Standard hint mapping mirroring scenario_audit convention.

    2026-08-12 sample-size discipline: BACK/FADE full confidence requires
    n>=30. Below that, downgrade to LEAN_* to signal "trend exists but
    thin sample — treat as directional lean, not lock."
    """
    if n < 15: return 'PASS', 30
    if hit_rate >= 60:
        if n < 30: return 'LEAN_BACK', 55  # thin sample, lean only
        return 'BACK', min(90, 50 + int(hit_rate - 50))
    if hit_rate <= 42:
        if n < 30: return 'LEAN_FADE', 55
        return 'FADE', min(85, 50 + int(50 - hit_rate))
    if hit_rate >= 55: return 'LEAN_BACK', 55
    if hit_rate <= 45: return 'LEAN_FADE', 55
    return 'PASS', 40


def run(sport='MLB', window_days=90):
    cfg = SPORT_CONFIG.get(sport)
    if not cfg:
        print(f'  [{sport}] not registered — skip'); return 0
    ctx_table = cfg['ctx_table']; res_table = cfg['results_table']

    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).date().isoformat()
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()

    print(f'=== sharp_scenario_matrix · sport={sport} · window={window_days}d ===')

    # Pull ctx via pagination
    all_ctx = []; offset = 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{ctx_table}',
            headers={**H_READ, 'Range': f'{offset}-{offset+999}',
                     'Range-Unit': 'items'},
            params={'select': '*',
                    'game_date': f'gte.{since}',
                    'and': f'(game_date.gte.{since},game_date.lt.{today})'},
            timeout=30)
        if r.status_code not in (200, 206): break
        rows = r.json()
        if not rows: break
        all_ctx += rows
        if len(rows) < 1000: break
        offset += 1000
        if offset > 20000: break
    print(f'  {len(all_ctx)} historical games loaded')

    # Pull results
    gids = [c['game_id'] for c in all_ctx if c.get('game_id')]
    all_res = {}
    for i in range(0, len(gids), 80):
        chunk = gids[i:i+80]
        url = (f'{SB}/rest/v1/{res_table}?game_id=in.({",".join(chunk)})'
               f'&select=game_id,home_score,away_score')
        rr = requests.get(url, headers=H_READ, timeout=30)
        for row in (rr.json() if rr.status_code == 200 else []):
            if row.get('home_score') is not None:
                all_res[row['game_id']] = row

    # Aggregate
    accum = defaultdict(list)  # {(market, key): [(outcome, pick_side)]}
    labels = {}; categories = {}
    for c in all_ctx:
        res = all_res.get(c.get('game_id'))
        if not res: continue
        for market, category, key, label, pick_side in extract_scenarios(c, sport):
            outcome = grade_scenario(sport, market, pick_side, c, res)
            if outcome:
                accum[(market, key)].append((outcome, pick_side))
                labels[(market, key)] = label
                categories[(market, key)] = category

    # Emit
    written = 0
    print(f'\n  {len(accum)} scenario keys with data')
    for (market, key), outcomes in sorted(accum.items(), key=lambda x: -len(x[1])):
        wins = sum(1 for o, s in outcomes if o == 'win')
        losses = sum(1 for o, s in outcomes if o == 'loss')
        pushes = sum(1 for o, s in outcomes if o == 'push')
        n = wins + losses + pushes
        if n < 10: continue
        graded = wins + losses
        if graded == 0: continue
        hit = round(100 * wins / graded, 2)
        # Simple ROI @ -110 baseline
        p = wins / graded
        roi = round(100 * (p * (1 / 1.10) - (1 - p)) * 100, 2) / 100 if False else round(100 * (p * 0.909 - (1 - p)), 2)
        # Cleaner: at 1.909 decimal (-110)
        dec = 1.909
        roi = round(100 * (p * (dec - 1) - (1 - p)), 2)
        hint, conf = compute_jerry_hint(hit, n)
        back_or_fade = ('BACK' if hit >= 55 else 'FADE' if hit <= 45 else 'NEUTRAL')
        # Get pick_side (all outcomes for a key share the same side since it's derived from the key)
        pick_side = outcomes[0][1]

        payload = {
            'sport': sport, 'market': market,
            'category': categories.get((market, key), 'unknown'),
            'scenario_key': key,
            'scenario_label': labels.get((market, key)),
            'side': pick_side,
            'wins': wins, 'losses': losses, 'pushes': pushes, 'total_n': n,
            'hit_rate': hit,
            'roi_pct': roi,
            'back_or_fade': back_or_fade,
            'jerry_hint': hint,
            'hint_confidence': conf,
            'window_days': window_days,
            'computed_at': datetime.now(timezone.utc).isoformat(),
        }
        pr = requests.post(
            f'{SB}/rest/v1/sharp_scenario_matrix?on_conflict=sport,market,scenario_key,window_days',
            headers=H_WRITE, json=payload, timeout=15)
        if pr.status_code in (200, 201, 204):
            written += 1

    print(f'  wrote {written} scenarios')

    # Populate today's per-game matches (so Jerry can consume)
    tr = requests.get(f'{SB}/rest/v1/{ctx_table}',
        headers=H_READ,
        params={'game_date': f'eq.{today}', 'select': '*'},
        timeout=30).json()
    if not isinstance(tr, list): tr = []
    # Rebuild in-memory lookup from what we just wrote
    matrix = {}
    m_rows = requests.get(f'{SB}/rest/v1/sharp_scenario_matrix',
        headers=H_READ,
        params={'sport': f'eq.{sport}', 'window_days': f'eq.{window_days}',
                'select': 'market,scenario_key,side,hit_rate,total_n,back_or_fade,jerry_hint,hint_confidence'},
        timeout=30).json()
    for row in (m_rows if isinstance(m_rows, list) else []):
        matrix[(row['market'], row['scenario_key'])] = row

    matches_written = 0
    for c in tr:
        gid = c.get('game_id')
        if not gid: continue
        for market, category, key, label, pick_side in extract_scenarios(c, sport):
            match_row = matrix.get((market, key))
            if not match_row: continue
            # Only emit actionable matches (n>=15 and not NEUTRAL)
            if (match_row.get('total_n') or 0) < 15: continue
            if match_row.get('back_or_fade') == 'NEUTRAL': continue
            payload = {
                'game_id': gid, 'sport': sport, 'game_date': today,
                'market': market, 'scenario_key': key, 'side': pick_side,
                'hit_rate': match_row.get('hit_rate'),
                'n': match_row.get('total_n'),
                'back_or_fade': match_row.get('back_or_fade'),
                'jerry_hint': match_row.get('jerry_hint'),
                'hint_confidence': match_row.get('hint_confidence'),
                'matched_at': datetime.now(timezone.utc).isoformat(),
            }
            pr = requests.post(
                f'{SB}/rest/v1/sharp_scenario_game_matches?on_conflict=game_id,market,scenario_key',
                headers=H_WRITE, json=payload, timeout=15)
            if pr.status_code in (200, 201, 204):
                matches_written += 1
    print(f'  wrote {matches_written} per-game matches for today')

    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', default='MLB',
                   help='MLB / NFL / NCAAF / ALL')
    p.add_argument('--window', type=int, default=None,
                   help='Rolling window in days. Omit to compute 30/60/90 in one run.')
    args = p.parse_args()
    sports = list(SPORT_CONFIG.keys()) if args.sport == 'ALL' else [args.sport]
    windows = [args.window] if args.window else [30, 60, 90]
    for s in sports:
        for w in windows:
            run(sport=s, window_days=w)


if __name__ == '__main__':
    main()
