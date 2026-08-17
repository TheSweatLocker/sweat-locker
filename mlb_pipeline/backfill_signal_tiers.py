"""Backfill signal_registry tiers from actual predictive power (2026-08-16).

For EVERY row in signal_sources, replay the last N days of resolved MLB
games: apply the condition_expr + side_expr, grade the picked side
against the actual outcome, compute hit rate + sample size. Assign a
tier based on evidence + write to signal_registry.

Tiering rule:
  VALIDATED     — n >= 50 AND hit_rate >= 55.0%  (proven edge, real n)
  DISCOVERY     — n >= 15 AND hit_rate >= 52.4%  (positive edge, thin n)
  UNVALIDATED   — n <  15  OR (n < 50 AND positive edge)
  ANTI_VALIDATED— n >= 25 AND hit_rate <= 48.0%  (proven fade — invert or drop)

The ensemble scorer reads these tiers via signal_registry.recommended_weight
and edge_weight() → real weights instead of the UNVALIDATED 0.20 floor.

Handler-based signals (split/scenario/external_pick) are skipped — they
already carry their own hit rates in their respective source tables.

CLI:
  python backfill_signal_tiers.py --days 60
  python backfill_signal_tiers.py --days 60 --dry-run
  python backfill_signal_tiers.py --days 60 --signal-key home_pitcher_on_heater  (single)
"""
from __future__ import annotations
import argparse, os, sys, json, math
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

from signal_expr import evaluate_bool, evaluate_str, AttrDict

# Handler classes skipped (they have their own historical hit rates elsewhere)
HANDLER_CLASSES = {'split', 'scenario', 'external_pick'}


def fetch_signals(sport: str = 'MLB') -> list[dict]:
    r = requests.get(f'{SB}/rest/v1/signal_sources',
                     headers=H_READ,
                     params={'select': '*', 'sport': f'eq.{sport}', 'enabled': 'eq.true'},
                     timeout=15)
    return r.json() if r.status_code == 200 else []


# Sport → (context_table, results_table)
SPORT_TABLES = {
    'MLB':   ('mlb_game_context',   'mlb_game_results'),
    'NFL':   ('nfl_game_context',   'nfl_game_results'),
    'NCAAF': ('ncaaf_game_context', 'ncaaf_game_results'),
    'NCAAB': ('ncaab_game_context', 'ncaab_game_results'),
}


def fetch_games_with_results(days: int, sport: str = 'MLB') -> list[dict]:
    """Pull sport-appropriate game_context joined with results for window."""
    if sport == 'UFC':
        return fetch_ufc_fights(days)

    ctx_table, res_table = SPORT_TABLES.get(sport, SPORT_TABLES['MLB'])
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    ctx_rows = []
    for off in range(0, 10000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/{ctx_table}'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&select=*&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        ctx_rows += chunk
        if len(chunk) < 1000: break

    results_rows = []
    for off in range(0, 10000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/{res_table}'
            f'?game_date=gte.{cutoff}&game_date=lte.{yesterday}'
            f'&select=game_id,home_score,away_score&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        results_rows += chunk
        if len(chunk) < 1000: break

    results = {r['game_id']: r for r in results_rows}
    out = []
    for c in ctx_rows:
        r = results.get(c.get('game_id'))
        if not r: continue
        try:
            c['_home_score'] = int(r['home_score'])
            c['_away_score'] = int(r['away_score'])
        except (TypeError, ValueError, KeyError):
            continue
        out.append(c)
    return out


def fetch_ufc_fights(days: int) -> list[dict]:
    """Pull graded UFC fights + enrich each fight with fighter stats
    (SLpM, str_def, td_acc/def, etc.) so signal_sources referencing
    ctx.slpm_a / ctx.str_def_b etc. can fire during backfill."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = []
    for off in range(0, 10000, 1000):
        r = requests.get(
            f'{SB}/rest/v1/ufc_picks'
            f'?event_date=gte.{cutoff}&graded_at=not.is.null'
            f'&select=*&limit=1000&offset={off}',
            headers=H_READ, timeout=30)
        chunk = r.json() if r.status_code == 200 else []
        rows += chunk
        if len(chunk) < 1000: break

    # Bulk-fetch ALL fighter stats once — much faster than per-game HTTP
    fighter_stats: dict[str, dict] = {}
    try:
        for off in range(0, 5000, 1000):
            r = requests.get(f'{SB}/rest/v1/ufc_fighter_stats'
                             '?select=fighter_name,slpm,str_acc,str_def,sapm,'
                             'td_avg,td_acc,td_def,sub_avg,total_wins,total_losses,'
                             'total_draws,wins_by_dec,finishing_rate,reach,height,stance'
                             f'&limit=1000&offset={off}',
                             headers=H_READ, timeout=30)
            chunk = r.json() if r.status_code == 200 else []
            for f in chunk:
                key = str(f.get('fighter_name') or '').strip().lower()
                if key: fighter_stats[key] = f
            if len(chunk) < 1000: break
    except Exception:
        pass

    def _lookup_by_name(name: str) -> dict:
        if not name: return {}
        n = name.strip().lower()
        if n in fighter_stats: return fighter_stats[n]
        # Fallback: match by last name
        last = n.split(' ')[-1]
        for k, v in fighter_stats.items():
            if k.endswith(' ' + last): return v
        return {}

    # Enrich each fight with fighter A/B stats + normalize _winner
    out = []
    for c in rows:
        winner = c.get('winner_actual')
        if not winner: continue
        c['_winner'] = 'A' if str(winner).strip().lower() == str(c.get('fighter_a') or '').strip().lower() else 'B'
        for suffix, name in (('a', c.get('fighter_a')), ('b', c.get('fighter_b'))):
            fs = _lookup_by_name(name)
            for k, v in fs.items():
                if k == 'fighter_name': continue
                c[f'{k}_{suffix}'] = v
        out.append(c)
    return out


def grade_side(side: str, ctx: dict) -> str | None:
    """Grade a candidate side against the game result. Returns 'W'|'L'|'P'|None."""
    # UFC fight candidates use _winner ('A'|'B')
    if side in ('FIGHTER_A_ML', 'FIGHTER_B_ML'):
        winner = ctx.get('_winner')
        if winner is None: return None
        want = 'A' if side == 'FIGHTER_A_ML' else 'B'
        return 'W' if winner == want else 'L'

    hs = ctx.get('_home_score')
    as_ = ctx.get('_away_score')
    if hs is None or as_ is None:
        return None
    total = hs + as_
    home_won = hs > as_
    away_won = as_ > hs

    if side == 'HOME_ML':
        return 'W' if home_won else 'L' if away_won else 'P'
    if side == 'AWAY_ML':
        return 'W' if away_won else 'L' if home_won else 'P'
    if side == 'HOME_RL':
        # HOME -1.5 covers if margin > 1.5, i.e. home wins by 2+
        margin = hs - as_
        try: line = float(ctx.get('close_spread') or -1.5)
        except (TypeError, ValueError): line = -1.5
        adjusted = margin + line  # home gets `line` (negative if favored)
        if adjusted > 0: return 'W'
        if adjusted < 0: return 'L'
        return 'P'
    if side == 'AWAY_RL':
        margin = as_ - hs
        try: line = -float(ctx.get('close_spread') or -1.5)  # away's line = -home_line
        except (TypeError, ValueError): line = 1.5
        adjusted = margin + line
        if adjusted > 0: return 'W'
        if adjusted < 0: return 'L'
        return 'P'
    if side == 'OVER':
        try: line = float(ctx.get('close_total') or 0)
        except (TypeError, ValueError): return None
        if line == 0: return None
        if abs(total - line) < 0.01: return 'P'
        return 'W' if total > line else 'L'
    if side == 'UNDER':
        try: line = float(ctx.get('close_total') or 0)
        except (TypeError, ValueError): return None
        if line == 0: return None
        if abs(total - line) < 0.01: return 'P'
        return 'W' if total < line else 'L'
    return None


def backfill_signal(source: dict, games: list[dict]) -> dict:
    """Run one signal against all games, return {n, w, l, p, hit_rate, tier}."""
    cls = source.get('class', '')
    if cls in HANDLER_CLASSES:
        return {'skipped': True, 'reason': f'handler class {cls}'}

    condition = source.get('condition_expr', '')
    side_expr = source.get('side_expr', '')
    if not condition or not side_expr:
        return {'skipped': True, 'reason': 'missing expr'}

    w = l = p = 0
    fires = 0
    for game in games:
        ctx_attr = AttrDict(game)
        if not evaluate_bool(condition, ctx_attr):
            continue
        fires += 1
        side = evaluate_str(side_expr, ctx_attr)
        if not side:
            continue
        result = grade_side(side, game)
        if result == 'W': w += 1
        elif result == 'L': l += 1
        elif result == 'P': p += 1

    n_dec = w + l  # exclude pushes from hit rate math
    hit_rate = round(100 * w / n_dec, 1) if n_dec else None
    edge_pp = round(hit_rate - 52.4, 1) if hit_rate is not None else None

    # Tier assignment
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
    """Write tier + hit_rate to signal_registry."""
    if dry_run: return True
    now_iso = datetime.now(timezone.utc).isoformat()

    # signal_registry unique key is (signal_name, sport, market_scope)
    payload = {
        'signal_name': source['signal_key'],
        'sport': source['sport'],
        'market_scope': source.get('market_scope', 'multi'),
        'category': source['class'],
        'description': source.get('description') or source.get('display_prose_template') or '',
        'hit_rate': stats['hit_rate'],
        'sample_n': stats['n_dec'],
        'edge_pp': stats['edge_pp'],
        'tier': stats['tier'],
        'recommended_weight': stats['recommended_weight'],
        'direction_hint': 'FADE' if stats['tier'] == 'ANTI_VALIDATED' else 'FOLLOW',
        'origin': f'BACKFILL_{date.today().isoformat()}',
        'last_computed_at': now_iso,
        'updated_at': now_iso,
    }
    pr = requests.post(
        f'{SB}/rest/v1/signal_registry?on_conflict=signal_name,sport,market_scope',
        headers=H_WRITE, json=[payload], timeout=15)
    if pr.status_code not in (200, 201, 204):
        print(f'    ✗ write failed: {pr.status_code} {pr.text[:150]}')
        return False
    return True


def run(days: int = 60, dry_run: bool = False,
         signal_key_filter: str | None = None, sport: str = 'MLB'):
    print(f'=== signal backfill-to-tier · {sport} · last {days} days ===\n')
    signals = fetch_signals(sport=sport)
    if signal_key_filter:
        signals = [s for s in signals if s['signal_key'] == signal_key_filter]
    print(f'  {len(signals)} signal_sources rows to evaluate')

    games = fetch_games_with_results(days, sport=sport)
    print(f'  {len(games)} games with results in window\n')
    if not games:
        print('  no games — abort')
        return

    tier_counts = defaultdict(int)
    written = 0
    for source in signals:
        cls = source.get('class', '')
        key = source['signal_key']
        stats = backfill_signal(source, games)
        if stats.get('skipped'):
            print(f'  {key:<40} [{cls:<12}] SKIP ({stats["reason"]})')
            continue

        hr = stats['hit_rate']
        n = stats['n_dec']
        fires = stats['fires']
        tier = stats['tier']
        edge = stats['edge_pp']

        hr_str = f'{hr}%' if hr is not None else '--'
        edge_str = f'{edge:+.1f}pp' if edge is not None else ''
        print(f'  {key:<40} [{cls:<12}] fires={fires:>3} n={n:>3} '
              f'{stats["w"]}-{stats["l"]}-{stats["p"]}  '
              f'HR={hr_str:<7} {edge_str:<8} tier={tier}')

        tier_counts[tier] += 1
        if not dry_run:
            if write_registry(source, stats, dry_run=dry_run):
                written += 1

    print(f'\n--- summary ---')
    for tier, count in sorted(tier_counts.items()):
        print(f'  {tier:<16} {count}')
    print(f'\n  ✓ {written} signal_registry rows updated{" (dry-run)" if dry_run else ""}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=60)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--signal-key', default=None)
    p.add_argument('--sport', default='MLB',
                   choices=['MLB', 'NFL', 'NCAAF', 'NCAAB', 'UFC', 'ALL'])
    args = p.parse_args()
    if args.sport == 'ALL':
        for s in ('MLB', 'NFL', 'NCAAF', 'NCAAB', 'UFC'):
            try:
                run(days=args.days, dry_run=args.dry_run,
                    signal_key_filter=args.signal_key, sport=s)
            except Exception as e:
                print(f'  ✗ {s} failed: {e}')
    else:
        run(days=args.days, dry_run=args.dry_run,
            signal_key_filter=args.signal_key, sport=args.sport)


if __name__ == '__main__':
    main()
