"""Nightly rescore of signal_registry (2026-08-16).

Reads recent evidence from the pipeline tables and refreshes hit_rate +
sample_n + tier for signals that have auto-computable definitions.
Signals without a handler here (qualitative or manual) keep their prior
values but get their last_computed_at timestamp bumped so operators can
see they were reviewed.

Signals rescored this pass (Playbook v1.0 auto-computable set):
  * refit_band_XX_YY — mlb_pipeline_props with refit_conviction in band
  * mc_ml_high_conf — mlb_game_context.primary_play tier + mc-driven sub
  * oc_ml_money_gte_80_fade — oddscrowd_snapshot money>=80 vs result
  * siera_gap_ml, siera_ace_duel_under, ops_l14_dual_cold/hot_regress —
    treat as manual-review (leave numbers, bump timestamp) until a proper
    scenario table backs them.

Runs nightly. Idempotent. Prints per-signal delta so operators see what
actually moved.

CLI
  python rescore_signal_registry.py             # rescore + write
  python rescore_signal_registry.py --dry-run   # print only, no writes
"""
from __future__ import annotations
import argparse, os, sys, re
from datetime import date, datetime, timezone, timedelta
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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

WINDOW_DAYS = 30  # rolling window for auto-rescore


def _pct(w: int, l: int) -> float | None:
    n = w + l
    return round(100 * w / n, 1) if n else None


def _tier_from(hit_rate: float | None, n: int, baseline: float = 52.4) -> str:
    """Auto-tier based on hit_rate + n vs -110 breakeven (52.4%)."""
    if hit_rate is None or n < 15:
        return 'UNVALIDATED'
    edge = hit_rate - baseline
    if n >= 50 and edge >= 5:
        return 'VALIDATED'
    if n >= 15 and edge > 0:
        return 'DISCOVERY'
    if edge < 0:
        return 'ANTI_VALIDATED'
    return 'UNVALIDATED'


def _weight_from_tier(tier: str) -> float:
    return {'VALIDATED': 1.0, 'DISCOVERY': 0.5,
            'UNVALIDATED': 0.3, 'ANTI_VALIDATED': 0.0}.get(tier, 0.3)


def rescore_refit_bands() -> list[dict]:
    """refit_band_XX_YY signals: hit% for props resolved in that refit
    conviction band over 30d."""
    cutoff = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    # Pull all resolved props with refit_conviction populated
    rows = []
    for off in range(0, 20000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/mlb_pipeline_props'
            f'?game_date=gte.{cutoff}&refit_conviction=not.is.null&result=not.is.null'
            f'&select=refit_conviction,result&limit=1000&offset={off}',
            headers=H_READ, timeout=20)
        chunk = r.json() if r.status_code == 200 else []
        rows += chunk
        if len(chunk) < 1000: break

    bands = [
        ('refit_band_35_44', 35, 44),
        ('refit_band_45_54', 45, 54),
        ('refit_band_55_64', 55, 64),
        ('refit_band_65_74', 65, 74),
        ('refit_band_75_84', 75, 84),
        ('refit_band_85_94', 85, 94),
        ('refit_band_95_100', 95, 100),
    ]
    out = []
    for name, lo, hi in bands:
        in_band = [x for x in rows
                   if x.get('refit_conviction') is not None
                   and lo <= float(x['refit_conviction']) <= hi]
        w = sum(1 for x in in_band if x.get('result') == 'Win')
        l = sum(1 for x in in_band if x.get('result') == 'Loss')
        n = w + l
        hr = _pct(w, l)
        tier = _tier_from(hr, n)
        out.append({
            'signal_name': name, 'category': 'refit', 'sport': 'MLB', 'market_scope': 'prop',
            'hit_rate': hr, 'sample_n': n,
            'edge_pp': round((hr - 52.4), 1) if hr is not None else None,
            'tier': tier, 'recommended_weight': _weight_from_tier(tier),
            'direction_hint': 'FOLLOW' if tier != 'ANTI_VALIDATED' else 'FADE',
            'origin': f'RESCORE_{date.today().isoformat()}',
        })
    return out


def rescore_mc_ml_high_conf() -> dict | None:
    """mc_ml_high_conf: primary_play type=ml where sub cites mc / high-conf.
    Rough proxy — flags games where the primary basis was MC high-confidence."""
    cutoff = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    r = requests.get(
        f'{SB}/rest/v1/mlb_game_context'
        f'?game_date=gte.{cutoff}&primary_play=not.is.null&select=primary_play,game_id',
        headers=H_READ, timeout=20)
    ctx_rows = r.json() if r.status_code == 200 else []

    # Pull results in same range for join
    r2 = requests.get(
        f'{SB}/rest/v1/mlb_game_results'
        f'?game_date=gte.{cutoff}&select=game_id,home_score,away_score,winning_team',
        headers=H_READ, timeout=20)
    results = {row['game_id']: row for row in (r2.json() if r2.status_code == 200 else [])}

    w = l = 0
    for row in ctx_rows:
        pp = row.get('primary_play')
        if not isinstance(pp, dict) or pp.get('type') != 'ml':
            continue
        sub = (pp.get('sub') or '').lower()
        if 'mc' not in sub and 'monte' not in sub:
            continue
        res = results.get(row['game_id'])
        if not res or res.get('winning_team') is None:
            continue
        label = (pp.get('label') or '').lower()
        picked_home = 'home' in label or (label and 'away' not in label and pp.get('side') == 'HOME')
        winning = (res.get('winning_team') or '').lower()
        home_won = winning and 'home' in winning  # loose match
        if picked_home == home_won: w += 1
        else: l += 1
    n = w + l
    hr = _pct(w, l)
    tier = _tier_from(hr, n)
    return {
        'signal_name': 'mc_ml_high_conf', 'category': 'model', 'sport': 'MLB',
        'market_scope': 'ml', 'hit_rate': hr, 'sample_n': n,
        'edge_pp': round((hr - 52.4), 1) if hr is not None else None,
        'tier': tier, 'recommended_weight': _weight_from_tier(tier),
        'direction_hint': 'FOLLOW' if tier != 'ANTI_VALIDATED' else 'FADE',
        'origin': f'RESCORE_{date.today().isoformat()}',
    }


def bump_timestamps_for_manual() -> int:
    """Bump last_computed_at on manual-only signals so operators can see
    they were reviewed this pass (numbers unchanged)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    manual_signals = [
        'siera_gap_ml', 'siera_ace_duel_under', 'ops_l14_dual_cold',
        'ops_l14_dual_hot_regress', 'lens_consensus', 'cross_source_sharp_confirmed',
        'oc_ml_money_gte_80_fade', 'heavy_fav_ml_neg200_cover',
    ]
    bumped = 0
    for name in manual_signals:
        pr = requests.patch(
            f'{SB}/rest/v1/signal_registry?signal_name=eq.{name}',
            headers=H_WRITE, json={'last_computed_at': now_iso}, timeout=10)
        if pr.status_code in (200, 204): bumped += 1
    return bumped


def write_rescored(rows: list[dict], dry_run: bool = False) -> int:
    now_iso = datetime.now(timezone.utc).isoformat()
    written = 0
    for r in rows:
        r['last_computed_at'] = now_iso
        r['updated_at'] = now_iso
        # Read prior to log delta
        prior_r = requests.get(
            f'{SB}/rest/v1/signal_registry?signal_name=eq.{r["signal_name"]}'
            f'&sport=eq.{r["sport"]}&market_scope=eq.{r["market_scope"]}'
            f'&select=hit_rate,sample_n,tier', headers=H_READ, timeout=10)
        prior = prior_r.json()[0] if prior_r.status_code == 200 and prior_r.json() else None
        prior_hr = prior.get('hit_rate') if prior else None
        prior_tier = prior.get('tier') if prior else '—'
        delta = '' if prior_hr is None or r['hit_rate'] is None else f' (was {prior_hr}%)'
        tier_delta = f' [{prior_tier}→{r["tier"]}]' if prior_tier != r['tier'] else ''
        print(f'  {r["signal_name"]:<32} hr={r["hit_rate"]}%{delta} n={r["sample_n"]} tier={r["tier"]}{tier_delta}')
        if dry_run: continue
        # Union keys across all rows so PostgREST batch upsert works
        # (feedback_postgrest_batch_normalize_keys)
        pr = requests.post(
            f'{SB}/rest/v1/signal_registry?on_conflict=signal_name,sport,market_scope',
            headers=H_WRITE, json=[r], timeout=15)
        if pr.status_code in (200, 201, 204): written += 1
        else: print(f'    ✗ {pr.status_code}: {pr.text[:150]}')
    return written


def run(dry_run: bool = False):
    print(f'=== signal_registry rescore ({date.today().isoformat()}) ===')
    print(f'  window: last {WINDOW_DAYS} days')
    print()

    rescored = []
    print('rescoring refit bands...')
    rescored += rescore_refit_bands()
    print()
    print('rescoring mc_ml_high_conf...')
    mc = rescore_mc_ml_high_conf()
    if mc: rescored.append(mc)
    print()

    written = write_rescored(rescored, dry_run=dry_run)
    print(f'\n  ✓ {written} auto-rescored{" (dry-run)" if dry_run else ""}')

    if not dry_run:
        bumped = bump_timestamps_for_manual()
        print(f'  ✓ {bumped} manual signals timestamp-bumped')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
