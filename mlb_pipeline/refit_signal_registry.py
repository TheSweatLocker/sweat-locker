"""refit_signal_registry — compute per-signal hit_rate from graded history.

2026-08-21 companion to the ramp-up prior shipped same day. Fresh signals
get RAMP_UP_PRIOR=0.50 for their first 30 days. After that, they need a
real registry entry — otherwise they drop to the 0.15/0.20 floor and get
silently ignored again.

This script walks recent graded outcomes and rebuilds signal_registry
rows for every signal that fired ≥ min_n times. Two data sources:

  * prop_playbook_decisions (result in W/L/Push) — prop signals
  * mlb_game_context.primary_play (grade against home/away score,
    close_spread, close_total) — game-ensemble signals

Grading rule (identical for both):
  A signal contributed to a specific side. If that side won the outcome,
  the signal was "right." If it lost, wrong. Push is excluded from the
  sample (no signal on push).

Tier assignment:
  VALIDATED     — n ≥ 50 AND hit_rate ≥ 55%   (proven edge, high sample)
  DISCOVERY     — n ≥ 20 AND hit_rate ≥ 55%   (promising, small sample)
  ANTI_VALIDATED — n ≥ 25 AND hit_rate ≤ 47%   (real fade edge)
  UNVALIDATED   — otherwise                     (noise or too thin)

Run weekly (or nightly). Idempotent: full recompute + upsert each run.

CLI:
  python refit_signal_registry.py                      # last 90d, all sports
  python refit_signal_registry.py --days 60 --sport MLB
  python refit_signal_registry.py --dry-run
  python refit_signal_registry.py --min-n 10           # dev: include thin samples
"""
from __future__ import annotations
import argparse, os, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

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

# Sport → (ctx table for live picks, resolved-results table with final scores).
# game_context is where fresh primary_play lives BEFORE the game. results tables
# store the graded final state including primary_play snapshot at write time.
SPORTS_WITH_CTX = {
    'MLB':   ('mlb_game_context',   'mlb_game_results'),
    'NFL':   ('nfl_game_context',   'nfl_game_results'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results'),
    'NBA':   ('nba_game_context',   'nba_game_results'),
    'NHL':   ('nhl_game_context',   'nhl_game_results'),
}


def _tier_for(n: int, hit_rate_frac: float) -> str:
    if n >= 25 and hit_rate_frac <= 0.47:
        return 'ANTI_VALIDATED'
    if n >= 50 and hit_rate_frac >= 0.55:
        return 'VALIDATED'
    if n >= 20 and hit_rate_frac >= 0.55:
        return 'DISCOVERY'
    return 'UNVALIDATED'


def _recommended_weight_for(n: int, hit_rate_frac: float, tier: str) -> float:
    """Coarse mapping to signal_registry.recommended_weight; ensemble
    scorer's edge_weight() is the runtime authority — this is a hint."""
    if tier == 'ANTI_VALIDATED':
        return 0.0
    import math
    BREAKEVEN = 0.5238  # -110 juice
    edge = max(0.0, hit_rate_frac - BREAKEVEN)
    edge_c = min(edge / 0.12, 1.0)
    n_c = min(math.log1p(n) / math.log(101), 1.0)
    return round(edge_c * n_c, 4)


def collect_prop_firings(sport: str, days: int) -> dict[str, dict]:
    """Return {signal_key: {right, wrong}} from prop_playbook_decisions."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    r = requests.get(f'{SB}/rest/v1/prop_playbook_decisions',
                     headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-9999'},
                     params={'sport': f'eq.{sport}',
                             'game_date': f'gte.{cutoff}',
                             'result': 'in.(W,L,Win,Loss)',
                             'select': 'result,playbook_side,playbook_sources'},
                     timeout=30)
    if r.status_code not in (200, 206):
        print(f'  ! prop query failed {r.status_code}: {r.text[:200]}')
        return {}
    rows = r.json() or []
    print(f'  [{sport}] prop decisions graded (last {days}d): {len(rows)}')

    firings: dict[str, dict] = defaultdict(lambda: {'right': 0, 'wrong': 0})
    for row in rows:
        result = str(row.get('result', '')).upper()
        won = result in ('W', 'WIN')
        pb_side = str(row.get('playbook_side', '')).upper()
        srcs = row.get('playbook_sources') or []
        if isinstance(srcs, str):
            try:
                import json as _j; srcs = _j.loads(srcs)
            except Exception:
                srcs = []
        for s in srcs:
            if not isinstance(s, dict): continue
            key = s.get('signal_key')
            if not key: continue
            # Strip __fade suffix so fade opinions count under parent
            base_key = str(key).replace('__fade', '')
            src_side = str(s.get('side', '')).upper()
            # Signal "agreed with pick" AND pick won → correct.
            # Signal "disagreed with pick" AND pick lost → also correct (they
            # were voting the winning side and the winning side did win).
            signal_agreed_with_pick = (src_side == pb_side)
            correct = (signal_agreed_with_pick == won)
            if correct: firings[base_key]['right'] += 1
            else:       firings[base_key]['wrong'] += 1
    return dict(firings)


def _game_market_result(market: str, ctx_row: dict) -> Optional[str]:
    """Return the winning candidate for a market on a completed game.

    market: 'ml' | 'rl' | 'total'
    Returns one of HOME_ML/AWAY_ML/HOME_RL/AWAY_RL/OVER/UNDER or None if
    push / cannot resolve.
    """
    hs, as_ = ctx_row.get('home_score'), ctx_row.get('away_score')
    if hs is None or as_ is None: return None
    if market == 'ml':
        if hs > as_: return 'HOME_ML'
        if as_ > hs: return 'AWAY_ML'
        return None
    if market == 'rl':
        cs = ctx_row.get('close_spread')
        if cs is None: return None
        try: cs = float(cs)
        except (TypeError, ValueError): return None
        margin = hs - as_
        if margin + cs > 0.0001:  return 'HOME_RL'
        if margin + cs < -0.0001: return 'AWAY_RL'
        return None
    if market == 'total':
        ct = ctx_row.get('close_total')
        if ct is None: return None
        try: ct = float(ct)
        except (TypeError, ValueError): return None
        total = hs + as_
        if total > ct + 0.0001: return 'OVER'
        if total < ct - 0.0001: return 'UNDER'
        return None
    return None


def _market_of_side(side: str) -> Optional[str]:
    """HOME_ML → ml; HOME_RL → rl; OVER/UNDER → total."""
    side = str(side).upper()
    if side in ('HOME_ML', 'AWAY_ML'): return 'ml'
    if side in ('HOME_RL', 'AWAY_RL'): return 'rl'
    if side in ('OVER', 'UNDER'):      return 'total'
    return None


def collect_game_firings(sport: str, days: int) -> dict[str, dict]:
    """Walk primary_play._ensemble_sources on completed games, grade each
    source's side against the actual game outcome. Same right/wrong tally.

    Reads from the results table (mlb_game_results etc.) which carries the
    primary_play snapshot at grade time PLUS home_score/away_score. This is
    the historical audit trail — ctx tables get overwritten on recompute
    so they can't be trusted for backward-looking analysis."""
    tables = SPORTS_WITH_CTX.get(sport)
    if not tables: return {}
    _ctx_table, results_table = tables
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    r = requests.get(f'{SB}/rest/v1/{results_table}',
                     headers={**H_READ, 'Range-Unit': 'items', 'Range': '0-4999'},
                     params={'game_date': f'gte.{cutoff}',
                             'home_score': 'not.is.null',
                             'select': 'game_id,home_score,away_score,close_spread,close_total,primary_play'},
                     timeout=30)
    if r.status_code not in (200, 206):
        # Sports without primary_play on results (NFL/NCAAF/NBA/NHL currently)
        # just don't have game-scope grading data yet. Not an error — return
        # empty and let prop grading carry the sport if it has anything.
        msg = ''
        try:
            msg = r.json().get('message', '')
        except Exception:
            msg = r.text[:80]
        if 'primary_play' in msg or 'close_spread' in msg or 'home_score' in msg:
            print(f'  [{sport}] game results schema lacks primary_play/close_spread — game grading skipped')
        else:
            print(f'  ! game query failed {r.status_code}: {r.text[:200]}')
        return {}
    rows = r.json() or []
    print(f'  [{sport}] game results rows (last {days}d): {len(rows)}')

    firings: dict[str, dict] = defaultdict(lambda: {'right': 0, 'wrong': 0})
    for row in rows:
        pp = row.get('primary_play') or {}
        if not isinstance(pp, dict): continue
        # Prefer the exhaustive per-market sources if present; fall back to
        # winning-side-only sources.
        srcs = pp.get('_ensemble_sources') or []
        if not isinstance(srcs, list): continue
        # Cache per-market outcomes so we don't recompute for every source.
        market_winner_cache: dict[str, Optional[str]] = {}
        for s in srcs:
            if not isinstance(s, dict): continue
            key = s.get('signal_key')
            if not key: continue
            base_key = str(key).replace('__fade', '')
            src_side = str(s.get('side', '')).upper()
            market = _market_of_side(src_side)
            if market is None: continue
            if market not in market_winner_cache:
                market_winner_cache[market] = _game_market_result(market, row)
            winner = market_winner_cache[market]
            if winner is None:
                # Push / unresolved — skip for this signal on this game.
                continue
            correct = (src_side == winner)
            if correct: firings[base_key]['right'] += 1
            else:       firings[base_key]['wrong'] += 1
    return dict(firings)


def merge_firings(*dicts) -> dict[str, dict]:
    """Combine multiple {signal_key → {right,wrong}} dicts, summing counts."""
    out: dict[str, dict] = defaultdict(lambda: {'right': 0, 'wrong': 0})
    for d in dicts:
        for k, v in d.items():
            out[k]['right'] += v['right']
            out[k]['wrong'] += v['wrong']
    return dict(out)


def upsert_registry(rows: list[dict], dry_run: bool = False) -> int:
    """PATCH existing rows by (signal_name, sport), POST new. Two-step
    because signal_registry lacks a (signal_name, sport) unique constraint —
    can't use PostgREST's on_conflict. Not ideal (2 round-trips per row)
    but the row count is tiny (dozens, not thousands) so latency is fine."""
    if dry_run or not rows:
        print(f'  [dry] would upsert {len(rows)} rows to signal_registry')
        return len(rows)

    # Pull existing rows to build (signal_name, sport) → id map
    sports = sorted({r['sport'] for r in rows})
    existing_ids: dict[tuple[str, str], int] = {}
    for sp in sports:
        rr = requests.get(f'{SB}/rest/v1/signal_registry', headers=H_READ,
                          params={'sport': f'eq.{sp}',
                                  'select': 'id,signal_name,sport'}, timeout=15)
        for row in (rr.json() or []):
            existing_ids[(row['signal_name'], row['sport'])] = row['id']

    total = 0
    for row in rows:
        key = (row['signal_name'], row['sport'])
        if key in existing_ids:
            rid = existing_ids[key]
            payload = {k: v for k, v in row.items() if k not in ('signal_name', 'sport')}
            resp = requests.patch(f'{SB}/rest/v1/signal_registry?id=eq.{rid}',
                                  headers=H_WRITE, json=payload, timeout=15)
            if resp.status_code in (200, 204): total += 1
            else: print(f'  ! patch {row["signal_name"]} failed {resp.status_code}: {resp.text[:150]}')
        else:
            resp = requests.post(f'{SB}/rest/v1/signal_registry',
                                 headers=H_WRITE, json=[row], timeout=15)
            if resp.status_code in (201, 204): total += 1
            else: print(f'  ! insert {row["signal_name"]} failed {resp.status_code}: {resp.text[:150]}')
    return total


def build_rows(sport: str, firings: dict[str, dict], min_n: int) -> list[dict]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    out = []
    dropped = 0
    for signal_key, ct in firings.items():
        n = ct['right'] + ct['wrong']
        if n < min_n:
            dropped += 1
            continue
        hr_frac = ct['right'] / n if n > 0 else 0.0
        tier = _tier_for(n, hr_frac)
        weight = _recommended_weight_for(n, hr_frac, tier)
        out.append({
            'signal_name': signal_key,
            'sport': sport,
            'hit_rate': round(hr_frac * 100, 2),
            'sample_n': n,
            'tier': tier,
            'recommended_weight': weight,
            'direction_hint': 'FADE' if tier == 'ANTI_VALIDATED' else 'FOLLOW',
            'origin': 'REFIT_SCRIPT',
            'last_computed_at': now,
            'updated_at': now,
        })
    print(f'  [{sport}] {len(out)} signals meet min_n={min_n} threshold ({dropped} dropped)')
    return out


def run(sport: str, days: int, dry_run: bool = False, min_n: int = 20) -> None:
    print(f'\n=== refit_signal_registry · {sport} · last {days}d ===')
    prop = collect_prop_firings(sport, days)
    game = collect_game_firings(sport, days)
    merged = merge_firings(prop, game)
    print(f'  [{sport}] unique signals with firings: {len(merged)} '
          f'(prop={len(prop)}, game={len(game)})')
    rows = build_rows(sport, merged, min_n)

    # Tier distribution summary before write
    from collections import Counter
    tier_dist = Counter(r['tier'] for r in rows)
    print(f'  [{sport}] tier dist: {dict(tier_dist)}')

    # Show a few examples
    if rows:
        print(f'  [{sport}] examples:')
        for r in sorted(rows, key=lambda x: -x['sample_n'])[:5]:
            print(f'    {r["signal_name"]:45s} tier={r["tier"]:14s} '
                  f'hr={r["hit_rate"]}% n={r["sample_n"]} w={r["recommended_weight"]}')

    written = upsert_registry(rows, dry_run=dry_run)
    print(f'  [{sport}] ✓ wrote {written} registry rows')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sport', help='specific sport (default: all)')
    p.add_argument('--days', type=int, default=90)
    p.add_argument('--min-n', type=int, default=20,
                   help='minimum firings before writing a row (default 20)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sports = [args.sport] if args.sport else list(SPORTS_WITH_CTX.keys())
    for s in sports:
        run(s, days=args.days, dry_run=args.dry_run, min_n=args.min_n)


if __name__ == '__main__':
    main()
