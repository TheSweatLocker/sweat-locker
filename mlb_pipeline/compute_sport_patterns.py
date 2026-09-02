"""Vault Match pattern registry — nightly recompute.

Iterates PATTERN_CATALOG entries, evaluates each against graded
history in the pattern's lookback window, upserts hit rates into
sport_pattern_registry table.

A pattern is a (sport, key, label, description, matches_fn, outcome_fn)
tuple:
  matches_fn(game_ctx)   → bool     — does this pattern fire on this game?
  outcome_fn(game_ctx)   → str      — 'W'/'L'/'P' — did the pattern's
                                      implied side hit the result?

Both functions read from a merged dict of {game_context row} + {game_results
row} joined on game_id. matches_fn uses pre-game fields (splits, tier, etc);
outcome_fn uses post-game fields (winner, cover, total_hit).

Adding a new pattern = one entry in PATTERN_CATALOG. No schema change,
no migration. The badge fires when the pattern's rolling n_total >= 15
AND hit_pct >= 65% (thresholds enforced by context-builder attach step,
not by this script — this script computes and stores; consumers decide
what to badge).

USAGE:
    python compute_sport_patterns.py                        # all sports
    python compute_sport_patterns.py --sport MLB
    python compute_sport_patterns.py --dry-run
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
SB = os.environ.get('SUPABASE_URL')
KEY = os.environ.get('SUPABASE_KEY')
H_READ = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


# ─── Pattern helpers (read from merged game_ctx dict) ────────────────

def _splits_triple_confirmed(game: dict) -> list:
    """Extract triple_confirmed list from splits_summary jsonb."""
    ss = game.get('splits_summary') or {}
    tc = ss.get('triple_confirmed') if isinstance(ss, dict) else []
    return tc if isinstance(tc, list) else []


def _primary_play(game: dict) -> dict:
    pp = game.get('primary_play') or {}
    if isinstance(pp, str):
        import json
        try: pp = json.loads(pp)
        except Exception: pp = {}
    return pp if isinstance(pp, dict) else {}


def _pp_result_outcome(game: dict) -> str:
    """Grade the primary_play against game result. Returns 'W'/'L'/'P'.
    Assumes game_result row is merged into game dict."""
    pp = _primary_play(game)
    market = str(pp.get('market') or '').lower()
    side = str(pp.get('side') or '').upper()
    if not market or not side:
        return 'P'
    # ML
    if market == 'ml':
        winner = str(game.get('winner') or '').upper()
        if not winner: return 'P'
        return 'W' if winner == side else 'L'
    # Spread — check ats_winner if present
    if market in ('spread', 'runline', 'puckline'):
        ats = str(game.get('ats_winner') or '').upper()
        if ats == 'PUSH': return 'P'
        if not ats: return 'P'
        return 'W' if ats == side else 'L'
    # Total
    if market == 'total':
        total_hit = str(game.get('total_result') or '').upper()
        if total_hit == 'PUSH': return 'P'
        pp_side = side if side in ('OVER','UNDER') else str(pp.get('label') or '').upper()
        if 'OVER' in pp_side and total_hit == 'OVER': return 'W'
        if 'UNDER' in pp_side and total_hit == 'UNDER': return 'W'
        return 'L'
    return 'P'


# ─── PATTERN CATALOG ─────────────────────────────────────────────────
# Add new patterns here. Each is (sport, key, label, description,
# lookback_days, matches_fn, outcome_fn).

PATTERN_CATALOG = [
    # ─── MLB ─────────────────────────────────────────────────────────
    {
        'sport': 'MLB',
        'key': 'mlb_sharp_confirmed_prime',
        'label': 'Sharp $ + PRIME',
        'description': 'MLB PRIMEs with 2+ sources confirming the sharp side. Backs the model when the market agrees.',
        'lookback_days': 30,
        'matches': lambda g: (
            len(_splits_triple_confirmed(g)) > 0
            and str(_primary_play(g).get('tier') or '').upper() == 'PRIME'
        ),
        'outcome': _pp_result_outcome,
    },
    {
        'sport': 'MLB',
        'key': 'mlb_sharp_confirmed_strong',
        'label': 'Sharp $ + STRONG',
        'description': 'MLB STRONGs with 2+ sources confirming the sharp side.',
        'lookback_days': 30,
        'matches': lambda g: (
            len(_splits_triple_confirmed(g)) > 0
            and str(_primary_play(g).get('tier') or '').upper() == 'STRONG'
        ),
        'outcome': _pp_result_outcome,
    },

    # ─── NFL ─────────────────────────────────────────────────────────
    {
        'sport': 'NFL',
        'key': 'nfl_home_div_dog',
        'label': 'Home Div Dog',
        'description': 'NFL home team is a divisional underdog. Historically hits the spread at an above-market rate.',
        'lookback_days': 365,  # NFL games sparse — need wider window
        'matches': lambda g: (
            g.get('div_game') is True
            and g.get('close_spread') is not None
            and float(g.get('close_spread', 0)) < 0
        ),
        # Custom outcome: home team covered the spread?
        'outcome': lambda g: (
            'P' if g.get('ats_winner') in (None, '', 'PUSH')
            else 'W' if str(g.get('ats_winner') or '').upper() in ('HOME', str(g.get('home_team') or '').upper())
            else 'L'
        ),
    },

    # ─── NCAAF / NBA / NCAAB / NHL — add as data thickens ──────────
    # (starter set kept intentionally small — extend in follow-up
    # batches once we validate these compute correctly + hit rates
    # are meaningful. Silent-hide badge means no user impact if a
    # pattern is missing.)
]


# ─── Data fetch ──────────────────────────────────────────────────────

def fetch_games(sport: str, lookback_days: int) -> list:
    """Fetch graded game_context rows joined with results for a sport.
    Returns merged dicts with pre-game ctx + post-game result fields."""
    cutoff = (_et_now().date() - timedelta(days=lookback_days)).isoformat()
    ctx_table = f'{sport.lower()}_game_context'
    res_table = f'{sport.lower()}_game_results'

    # Fetch context rows in the window
    ctx_rows = []
    off = 0
    while True:
        r = requests.get(
            f'{SB}/rest/v1/{ctx_table}?'
            f'select=*&game_date=gte.{cutoff}&limit=1000&offset={off}',
            headers=H_READ, timeout=30,
        )
        chunk = r.json() if r.status_code == 200 else []
        if not chunk:
            break
        ctx_rows.extend(chunk)
        if len(chunk) < 1000:
            break
        off += 1000

    if not ctx_rows:
        return []

    # Fetch matching results
    game_ids = [str(g.get('game_id')) for g in ctx_rows if g.get('game_id')]
    if not game_ids:
        return []

    # Chunk in batches of 200 (PostgREST IN clause limit)
    res_by_gid = {}
    for i in range(0, len(game_ids), 200):
        batch = game_ids[i:i+200]
        r = requests.get(
            f'{SB}/rest/v1/{res_table}?'
            f'select=*&game_id=in.({",".join(batch)})',
            headers=H_READ, timeout=30,
        )
        if r.status_code == 200:
            for row in r.json():
                res_by_gid[str(row.get('game_id'))] = row

    # Merge: ctx + result. Only keep games with a result (graded).
    merged = []
    for g in ctx_rows:
        gid = str(g.get('game_id'))
        res = res_by_gid.get(gid)
        if not res:
            continue
        merged.append({**g, **res})

    return merged


# ─── Compute + upsert ────────────────────────────────────────────────

def compute_pattern(games: list, pattern: dict) -> Optional[dict]:
    """Evaluate one pattern against a list of merged games."""
    matches_fn = pattern['matches']
    outcome_fn = pattern['outcome']
    w = l = p = 0
    for g in games:
        try:
            if not matches_fn(g):
                continue
            oc = outcome_fn(g)
        except Exception:
            continue
        if oc == 'W': w += 1
        elif oc == 'L': l += 1
        elif oc == 'P': p += 1

    n = w + l
    if n == 0 and p == 0:
        return None
    hit_pct = round(100.0 * w / n, 2) if n > 0 else None
    return {
        'sport': pattern['sport'],
        'pattern_key': pattern['key'],
        'pattern_label': pattern['label'],
        'pattern_description': pattern.get('description') or '',
        'lookback_days': pattern.get('lookback_days', 30),
        'n_wins': w,
        'n_losses': l,
        'n_pushes': p,
        'n_total': n + p,
        'hit_pct': hit_pct,
        'last_computed_at': _et_now().isoformat(),
    }


def upsert_patterns(records: list, dry_run: bool = False) -> int:
    if not records:
        return 0
    if dry_run:
        for r in records:
            print(f"  [DRY] {r['sport']:5} {r['pattern_key']:32} "
                  f"{r['n_wins']}-{r['n_losses']}-{r['n_pushes']} = "
                  f"{r['hit_pct']}% (n={r['n_total']}) — {r['pattern_label']}")
        return len(records)

    r = requests.post(
        f'{SB}/rest/v1/sport_pattern_registry?'
        f'on_conflict=sport,pattern_key',
        headers=H_WRITE, json=records, timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        print(f'  ⚠ upsert failed {r.status_code}: {r.text[:200]}')
        return 0
    return len(records)


# ─── Entry ───────────────────────────────────────────────────────────

def run(sport_filter: Optional[str] = None, dry_run: bool = False) -> None:
    print('=== Vault Match pattern recompute ===')
    catalog = [p for p in PATTERN_CATALOG
               if not sport_filter or p['sport'] == sport_filter.upper()]
    if not catalog:
        print(f'  no patterns matched filter {sport_filter!r}')
        return

    # Group by (sport, lookback_days) so we fetch each window once
    fetch_keys = defaultdict(list)
    for p in catalog:
        fetch_keys[(p['sport'], p['lookback_days'])].append(p)

    all_records = []
    for (sport, days), patterns in fetch_keys.items():
        print(f'\n  fetching {sport} games (last {days}d)...')
        games = fetch_games(sport, days)
        print(f'    → {len(games)} graded games')
        if not games:
            continue
        for p in patterns:
            rec = compute_pattern(games, p)
            if rec:
                all_records.append(rec)

    if not all_records:
        print('\n  no pattern records to write')
        return

    n = upsert_patterns(all_records, dry_run)
    verb = '[DRY]' if dry_run else 'wrote'
    print(f'\n  {verb} {n} pattern rows')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport', help='Only recompute this sport (MLB/NFL/NCAAF/etc)')
    ap.add_argument('--dry-run', action='store_true', help='Print without writing')
    args = ap.parse_args()
    run(sport_filter=args.sport, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
