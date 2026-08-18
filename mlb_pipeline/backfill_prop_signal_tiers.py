"""Backfill signal_registry tiers for PROP signals (2026-08-18).

Sister to backfill_signal_tiers.py — but iterates *props* instead of games.
Prop signals live in signal_sources with class LIKE 'prop_%' and reference
`p` (the prop row) in their condition_expr. The game-level backfill can't
evaluate them because it binds `ctx` to a game_context row; prop signals
need the prop row with its `signals` JSON column populated.

Grading model:
  For each resolved prop (result IN Win/Loss/Push):
    - Evaluate condition_expr with {'ctx': None, 'p': prop_dict}
    - If fires, evaluate side_expr → 'BACK' or 'FADE'
    - BACK: side agrees with the prop's direction (endorsed the pick)
        → W if prop.result == 'Win', L if 'Loss', P if 'Push'
    - FADE: side opposes the prop's direction (would have flipped it)
        → W if prop.result == 'Loss', L if 'Win', P if 'Push'
  Then n = W + L; hit_rate = W/n; tier assignment matches game backfill.

Same tier rules + weight mapping as backfill_signal_tiers.py so the
playbook ensemble consumes both from a uniform signal_registry.

CLI:
  python backfill_prop_signal_tiers.py                      # MLB, 60 days
  python backfill_prop_signal_tiers.py --sport MLB --days 90
  python backfill_prop_signal_tiers.py --dry-run
  python backfill_prop_signal_tiers.py --signal-key pitcher_l5_confirm
"""
from __future__ import annotations
import argparse, os, sys, json
from datetime import date, datetime, timezone, timedelta
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
H_READ  = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}


PROP_TABLES = {
    'MLB':   'mlb_pipeline_props',
    'NFL':   'nfl_pipeline_props',
    'NCAAF': 'ncaaf_pipeline_props',
    'NBA':   'nba_pipeline_props',
    'NHL':   'nhl_pipeline_props',
}


def fetch_prop_signals(sport: str) -> list[dict]:
    r = requests.get(f'{SB}/rest/v1/signal_sources',
                     headers=H_READ,
                     params={'select': '*', 'sport': f'eq.{sport}',
                             'class': 'like.prop_%', 'enabled': 'eq.true'},
                     timeout=15)
    return r.json() if r.status_code == 200 else []


def fetch_resolved_props(sport: str, days: int) -> list[dict]:
    """Pull graded props from the last N days. Only rows with result set."""
    table = PROP_TABLES.get(sport)
    if not table: return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for off in range(0, 50000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/{table}'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&result=in.(Win,Loss,Push)'
            f'&select=*&limit=1000&offset={off}',
            headers=H_READ, timeout=45)
        chunk = r.json() if r.status_code == 200 else []
        rows += chunk
        if len(chunk) < 1000: break
    return rows


def _coerce_signals(p: dict) -> dict:
    """PostgREST may return jsonb as string on some paths — parse it once."""
    s = p.get('signals')
    if isinstance(s, str):
        try: p['signals'] = json.loads(s)
        except Exception: p['signals'] = {}
    return p


def _matches_market(source_row: dict, prop: dict) -> bool:
    """Mirror prop_ensemble_scorer._matches_market so we grade the same
    subset the live scorer would evaluate."""
    scope = (source_row.get('market_scope') or '').lower()
    if not scope or scope == '*': return True
    ptype = (prop.get('prop_type') or '').lower()
    if scope == 'pitcher':
        return any(ptype.startswith(x) for x in ('bb_', 'ha_', 'ks_', 'outs_', 'er_'))
    if scope == 'hits':
        return ptype.startswith('hits_')
    if scope == ptype: return True
    if ptype.startswith(scope + '_'): return True
    return False


_SAFE_BUILTINS = {'min': min, 'max': max, 'abs': abs, 'int': int, 'float': float,
                   'str': str, 'sum': sum, 'len': len, 'any': any, 'all': all,
                   'round': round, 'None': None, 'True': True, 'False': False,
                   'isinstance': isinstance, 'dict': dict, 'list': list, 'bool': bool}


def _safe_eval(expr: str, env: dict):
    """Same sandbox as prop_ensemble_scorer._safe_eval so evaluation semantics
    are identical between live scoring and backfill grading."""
    if not expr: return None
    globs = {'__builtins__': {}}
    locs = {**_SAFE_BUILTINS, **env}
    try:
        return eval(compile(expr, '<prop_signal_expr>', 'eval'), globs, locs)
    except Exception:
        return None


def grade_prop_signal(side: str, prop_result: str) -> str | None:
    """BACK endorses direction → W on prop Win, L on prop Loss.
    FADE opposes → W on prop Loss (correctly faded), L on prop Win."""
    if prop_result == 'Push': return 'P'
    if side == 'BACK':
        if prop_result == 'Win': return 'W'
        if prop_result == 'Loss': return 'L'
    elif side == 'FADE':
        if prop_result == 'Loss': return 'W'
        if prop_result == 'Win': return 'L'
    return None


def backfill_prop_signal(source: dict, props: list[dict]) -> dict:
    condition = source.get('condition_expr') or ''
    side_expr = source.get('side_expr') or ''
    if not condition or not side_expr:
        return {'skipped': True, 'reason': 'missing expr'}

    w = l = p = 0
    fires = 0
    for prop in props:
        if not _matches_market(source, prop): continue
        prop = _coerce_signals(prop)
        env = {'ctx': None, 'p': prop}
        matched = _safe_eval(condition, env)
        if not matched: continue
        fires += 1
        side_raw = _safe_eval(side_expr, env)
        side = str(side_raw).upper() if side_raw else ''
        if side not in ('BACK', 'FADE'): continue
        result = grade_prop_signal(side, prop.get('result'))
        if result == 'W': w += 1
        elif result == 'L': l += 1
        elif result == 'P': p += 1

    n_dec = w + l
    hit_rate = round(100 * w / n_dec, 1) if n_dec else None
    edge_pp = round(hit_rate - 52.4, 1) if hit_rate is not None else None

    # Tier assignment — same rules as game-level backfill for uniformity
    if n_dec < 15:
        tier = 'UNVALIDATED'
    elif hit_rate is not None and n_dec >= 25 and hit_rate <= 48.0:
        tier = 'ANTI_VALIDATED'
    elif hit_rate is not None and n_dec >= 50 and hit_rate >= 55.0:
        tier = 'VALIDATED'
    elif hit_rate is not None and hit_rate >= 52.4:
        tier = 'DISCOVERY'
    else:
        tier = 'UNVALIDATED'

    weight = {'VALIDATED': 1.0, 'DISCOVERY': 0.5,
              'UNVALIDATED': 0.3, 'ANTI_VALIDATED': 0.0}[tier]

    return {
        'fires': fires, 'w': w, 'l': l, 'p': p, 'n_dec': n_dec,
        'hit_rate': hit_rate, 'edge_pp': edge_pp, 'tier': tier,
        'recommended_weight': weight,
    }


def write_registry(source: dict, stats: dict, dry_run: bool = False) -> bool:
    if dry_run: return True
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        'signal_name': source['signal_key'],
        'sport': source['sport'],
        'market_scope': source.get('market_scope', 'prop'),
        'category': source['class'],
        'description': source.get('description') or source.get('display_prose_template') or '',
        'hit_rate': stats['hit_rate'],
        'sample_n': stats['n_dec'],
        'edge_pp': stats['edge_pp'],
        'tier': stats['tier'],
        'recommended_weight': stats['recommended_weight'],
        'direction_hint': 'FADE' if stats['tier'] == 'ANTI_VALIDATED' else 'FOLLOW',
        'origin': f'PROP_BACKFILL_{date.today().isoformat()}',
        'last_computed_at': now_iso,
        'updated_at': now_iso,
    }
    pr = requests.post(
        f'{SB}/rest/v1/signal_registry?on_conflict=signal_name,sport,market_scope',
        headers=H_WRITE, json=[payload], timeout=15)
    if pr.status_code not in (200, 201, 204):
        print(f'    x write failed: {pr.status_code} {pr.text[:150]}')
        return False
    return True


def run(days: int = 60, dry_run: bool = False,
         signal_key_filter: str | None = None, sport: str = 'MLB'):
    print(f'=== prop signal backfill-to-tier · {sport} · last {days} days ===')
    signals = fetch_prop_signals(sport)
    if signal_key_filter:
        signals = [s for s in signals if s['signal_key'] == signal_key_filter]
    print(f'  {len(signals)} prop signal_sources rows to evaluate')

    props = fetch_resolved_props(sport, days)
    print(f'  {len(props)} resolved props in window\n')
    if not props:
        print('  no resolved props — abort')
        return

    tier_counts = defaultdict(int)
    written = 0
    for source in signals:
        cls = source.get('class', '')
        key = source['signal_key']
        stats = backfill_prop_signal(source, props)
        if stats.get('skipped'):
            print(f'  {key:<40} [{cls:<18}] SKIP ({stats["reason"]})')
            continue
        hr = stats['hit_rate']; n = stats['n_dec']
        fires = stats['fires']; tier = stats['tier']; edge = stats['edge_pp']
        hr_str = f'{hr}%' if hr is not None else '--'
        edge_str = f'{edge:+.1f}pp' if edge is not None else ''
        print(f'  {key:<40} [{cls:<18}] fires={fires:>4} n={n:>3} '
              f'{stats["w"]}-{stats["l"]}-{stats["p"]}  HR={hr_str:<7} {edge_str:<8} tier={tier}')
        tier_counts[tier] += 1
        if write_registry(source, stats, dry_run=dry_run):
            written += 1

    print(f'\n--- summary ---')
    for t in ('VALIDATED', 'DISCOVERY', 'UNVALIDATED', 'ANTI_VALIDATED'):
        if tier_counts.get(t):
            print(f'  {t:<16} {tier_counts[t]}')
    print(f'\n  {"[DRY] " if dry_run else ""}wrote {written} signal_registry rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=60)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--signal-key')
    p.add_argument('--sport', default='MLB',
                   choices=list(PROP_TABLES.keys()) + ['ALL'])
    args = p.parse_args()
    if args.sport == 'ALL':
        for s in PROP_TABLES:
            run(days=args.days, dry_run=args.dry_run,
                signal_key_filter=args.signal_key, sport=s)
    else:
        run(days=args.days, dry_run=args.dry_run,
            signal_key_filter=args.signal_key, sport=args.sport)


if __name__ == '__main__':
    main()
