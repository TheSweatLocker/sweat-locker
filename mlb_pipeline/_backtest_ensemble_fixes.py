"""Backtest: compare v1 (current) vs v2 (Bayesian + fade-consensus + fade-cap)
ensemble scoring on today's slate.

Runs both scoring modes against the same ctx rows, compares:
  - tier distribution
  - contradictions (published pick vs MC/jerry_pred/OC money)
  - fade-share of adjusted totals
  - number of games where pick FLIPPED

Success criteria (all must hold):
  1. Contradictions drop (v2 < v1)
  2. Total slate size NOT cut by >30%
  3. PRIME picks that survive v2 all clear the consensus check

Not persisting anything — pure comparison.
"""
import os
import sys
import subprocess
import json
from pathlib import Path
from dotenv import load_dotenv
import requests

load_dotenv(Path(__file__).parent / '.env')
SB = os.environ['SUPABASE_URL']
K = os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': f'Bearer {K}'}
GD = os.environ.get('BACKTEST_DATE', '2026-08-26')


def score_slate_with(version: str) -> list:
    """Run ensemble_scorer against today's slate under the given weight version.
    Returns list of {game_id, tier, side, label, conviction, fade_share, contradictions_count}.
    """
    env = {**os.environ, 'ENSEMBLE_WEIGHT_VERSION': version}
    from ensemble_scorer import score_game

    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context?game_date=eq.{GD}'
        f'&select=*',
        headers=H, timeout=15
    )
    if r.status_code != 200:
        raise RuntimeError(f'ctx fetch failed: {r.status_code}')
    games = r.json()
    if not isinstance(games, list):
        raise RuntimeError(f'unexpected ctx shape: {games}')

    # Temporarily set env var for the score_game runs
    prev = os.environ.get('ENSEMBLE_WEIGHT_VERSION')
    os.environ['ENSEMBLE_WEIGHT_VERSION'] = version
    try:
        rows = []
        for ctx in games:
            try:
                decision = score_game('MLB', ctx)
            except Exception as e:
                rows.append({'game_id': ctx.get('game_id'), 'error': str(e)})
                continue

            pp = decision.top()
            if pp is None or pp.pick is None:
                rows.append({'game_id': ctx.get('game_id'), 'no_pick': True})
                continue

            # Extract fade share
            fade_contrib = sum(
                c.contribution for c in pp.contributions
                if c.signal_key.endswith('__fade') or c.signal_key.endswith('_fade')
            )
            total_contrib = sum(c.contribution for c in pp.contributions) or 1e-9
            fade_pct = fade_contrib / total_contrib

            # Detect contradictions with MC + Jerry + OC
            contradictions = _count_contradictions(pp, ctx)

            rows.append({
                'game_id': ctx.get('game_id'),
                'away': ctx.get('away_team'),
                'home': ctx.get('home_team'),
                'tier': pp.tier,
                'side': pp.side,
                'label': pp.display_label,
                'conviction': pp.conviction,
                'market': pp.market,
                'fade_share': round(fade_pct, 3),
                'contradictions': contradictions,
                'contrib_count': len(pp.contributions),
                'score': round(pp.score, 3),
            })
        return rows
    finally:
        if prev is None:
            os.environ.pop('ENSEMBLE_WEIGHT_VERSION', None)
        else:
            os.environ['ENSEMBLE_WEIGHT_VERSION'] = prev


def _count_contradictions(pp, ctx: dict) -> int:
    """Count independent live signals that contradict pp.side."""
    if pp.market not in ('total', 'ml', 'rl'):
        return 0
    count = 0
    # MC contradicts?
    mc = ctx.get('mc_probabilities') or {}
    if isinstance(mc, dict):
        try:
            if pp.market == 'total':
                p_our = float(mc.get('mc_p_under' if pp.side == 'UNDER' else 'mc_p_over') or 0.5)
                if p_our <= 0.35:  # MC 65%+ against us
                    count += 1
            elif pp.market == 'ml':
                p_our = float(mc.get('mc_p_home_win' if pp.side == 'HOME_ML' else 'mc_p_away_win') or 0.5)
                if p_our <= 0.35:
                    count += 1
        except (TypeError, ValueError):
            pass
    # Jerry pred contradicts on totals?
    if pp.market == 'total':
        try:
            jpred = ctx.get('jerry_pred_total')
            cline = ctx.get('close_total')
            if jpred is not None and cline is not None:
                diff = float(jpred) - float(cline)
                if pp.side == 'UNDER' and diff >= 1.5:
                    count += 1
                elif pp.side == 'OVER' and diff <= -1.5:
                    count += 1
        except (TypeError, ValueError):
            pass
    # OC money contradicts?
    oc = ctx.get('oddscrowd_snapshot') or {}
    if isinstance(oc, dict):
        seg = oc.get(pp.market) or {}
        if isinstance(seg, dict):
            try:
                oc_side = str(seg.get('pick', '')).upper()
                oc_money = float(seg.get('money') or 0)
                matches_ours = (
                    (pp.market == 'total' and oc_side == pp.side) or
                    (pp.market in ('ml', 'rl') and (
                        (oc_side == 'HOME' and pp.side.startswith('HOME')) or
                        (oc_side == 'AWAY' and pp.side.startswith('AWAY'))
                    ))
                )
                if not matches_ours and oc_money >= 70:
                    count += 1
            except (TypeError, ValueError):
                pass
    return count


def compare(v1_rows, v2_rows):
    """Compute summary + per-game diff."""
    v1_by_gid = {r['game_id']: r for r in v1_rows if 'game_id' in r}
    v2_by_gid = {r['game_id']: r for r in v2_rows if 'game_id' in r}
    gids = set(v1_by_gid) | set(v2_by_gid)

    def _tier_dist(rows):
        d = {}
        for r in rows:
            t = r.get('tier', 'ERROR')
            d[t] = d.get(t, 0) + 1
        return d

    def _contra_summary(rows):
        picks = [r for r in rows if 'tier' in r and r['tier'] in ('PRIME', 'STRONG', 'LEAN')]
        n = len(picks)
        contra = sum(1 for r in picks if r.get('contradictions', 0) >= 2)
        contra_prime = sum(1 for r in picks if r['tier'] == 'PRIME' and r.get('contradictions', 0) >= 1)
        avg_fade = sum(r.get('fade_share', 0) for r in picks) / max(1, n)
        return {'n_picks': n, 'multi_contradiction': contra, 'prime_with_contra': contra_prime, 'avg_fade_share': round(avg_fade, 3)}

    print('=' * 70)
    print(f'BACKTEST: {GD} — v1 (current) vs v2 (Bayes + fade-consensus + fade-cap)')
    print('=' * 70)
    print(f'\nTier distribution:')
    print(f'  v1: {_tier_dist(v1_rows)}')
    print(f'  v2: {_tier_dist(v2_rows)}')
    print(f'\nContradiction summary:')
    print(f'  v1: {_contra_summary(v1_rows)}')
    print(f'  v2: {_contra_summary(v2_rows)}')

    print(f'\nPer-game deltas:')
    flipped = 0
    demoted = 0
    same = 0
    for gid in sorted(gids):
        v1 = v1_by_gid.get(gid, {})
        v2 = v2_by_gid.get(gid, {})
        if v1.get('side') != v2.get('side'):
            flipped += 1
            print(f'  FLIP: {v1.get("away","?")} @ {v1.get("home","?")}: '
                  f'v1={v1.get("tier")}/{v1.get("side")}/{v1.get("conviction")} '
                  f'-> v2={v2.get("tier")}/{v2.get("side")}/{v2.get("conviction")}')
        elif v1.get('tier') != v2.get('tier'):
            demoted += 1
            v1t = v1.get('tier', '?')
            v2t = v2.get('tier', '?')
            arrow = 'DOWN' if _tier_rank(v2t) < _tier_rank(v1t) else 'UP'
            print(f'  {arrow}    {v1.get("away","?")} @ {v1.get("home","?")}: '
                  f'v1={v1t}/{v1.get("conviction")} -> v2={v2t}/{v2.get("conviction")} '
                  f'(contradictions {v1.get("contradictions")}to{v2.get("contradictions")}, '
                  f'fade_share {v1.get("fade_share")}to{v2.get("fade_share")})')
        else:
            same += 1
    print(f'\nSummary: {flipped} flips, {demoted} tier changes, {same} unchanged')


def _tier_rank(t):
    return {'PRIME': 3, 'STRONG': 2, 'LEAN': 1, 'NONE': 0}.get(t, 0)


def main():
    v1_rows = score_slate_with('v1')
    v2_rows = score_slate_with('v2')
    compare(v1_rows, v2_rows)


if __name__ == '__main__':
    main()
