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
    """Grade the primary_play against game result. Returns 'W'/'L'/'P'/None-safe.

    CORRECTED 2026-09-01 (guardrail pass): field names match the actual
    canonical grader in aggregate_daily_records.py (line 460+):
      pp.type   'ml' | 'rl' | 'spread' | 'total'  (NOT 'market')
      pp.side   'HOME' | 'AWAY' | 'OVER' | 'UNDER'
      home_win  bool
      spread_result  'home_covered' | 'away_covered' | 'push' (lowercase)
      total_result   'over' | 'under' | 'push'  (case-normalized via .lower())
    """
    pp = _primary_play(game)
    ptype = str(pp.get('type') or '').lower()
    side  = str(pp.get('side') or '').upper()
    if not ptype or not side:
        return 'P'
    if ptype == 'ml':
        hw = game.get('home_win')
        if hw is None: return 'P'
        if side == 'HOME': return 'W' if hw else 'L'
        if side == 'AWAY': return 'L' if hw else 'W'
        return 'P'
    if ptype in ('rl', 'spread', 'puckline', 'runline'):
        sr = str(game.get('spread_result') or '').lower()
        if sr == 'push': return 'P'
        if sr == 'home_covered': return 'W' if side == 'HOME' else 'L'
        if sr == 'away_covered': return 'W' if side == 'AWAY' else 'L'
        return 'P'
    if ptype == 'total':
        tr = str(game.get('total_result') or '').lower()
        if tr == 'push': return 'P'
        if tr == 'over':  return 'W' if side == 'OVER' else 'L'
        if tr == 'under': return 'W' if side == 'UNDER' else 'L'
        return 'P'
    return 'P'


def _spread_outcome_home_covered(game: dict) -> str:
    """Standalone spread outcome for patterns that back HOME regardless
    of primary_play. Used by e.g. nfl_home_div_dog. Reads spread_result."""
    sr = str(game.get('spread_result') or '').lower()
    if sr == 'push': return 'P'
    if sr == 'home_covered': return 'W'
    if sr == 'away_covered': return 'L'
    return 'P'


def _spread_outcome_away_covered(game: dict) -> str:
    sr = str(game.get('spread_result') or '').lower()
    if sr == 'push': return 'P'
    if sr == 'away_covered': return 'W'
    if sr == 'home_covered': return 'L'
    return 'P'


def _pp_result_outcome_faded(game: dict) -> str:
    """FADE variant of _pp_result_outcome — returns W when the primary_play
    side LOST (i.e., the fade won). For FADE patterns whose implied bet
    is the opposite of pp.side. Push stays push."""
    oc = _pp_result_outcome(game)
    if oc == 'W': return 'L'
    if oc == 'L': return 'W'
    return oc  # P stays P


def _road_favorite_spread_fn(threshold: float):
    """Factory: matches_fn that fires when road team is favored by
    `threshold` or more. NFL/NCAAF convention: close_spread > 0 = home
    favored, so road fav = close_spread < -threshold."""
    def _fn(g: dict) -> bool:
        cs = g.get('close_spread')
        if cs is None: return False
        try:
            return float(cs) <= -threshold
        except (ValueError, TypeError):
            return False
    return _fn


def _rest_edge_underdog_fn(threshold_days: int):
    """Factory: matches_fn that fires when the underdog has at least
    `threshold_days` more rest than the favorite. NFL convention:
    days_rest_home / days_rest_away in ctx; close_spread > 0 = home fav."""
    def _fn(g: dict) -> bool:
        cs = g.get('close_spread')
        rh = g.get('days_rest_home')
        ra = g.get('days_rest_away')
        if cs is None or rh is None or ra is None:
            return False
        try:
            cs = float(cs); rh = int(rh); ra = int(ra)
        except (ValueError, TypeError):
            return False
        # Home favored (cs > 0) → dog is away → back away if ra - rh >= threshold
        if cs > 0 and (ra - rh) >= threshold_days:
            return True
        # Away favored (cs < 0) → dog is home → back home if rh - ra >= threshold
        if cs < 0 and (rh - ra) >= threshold_days:
            return True
        return False
    return _fn


def _rest_edge_underdog_outcome(g: dict) -> str:
    """W when the underdog with rest edge covered. Determines which side
    from close_spread sign, then reads spread_result."""
    cs = g.get('close_spread')
    if cs is None: return 'P'
    try:
        cs = float(cs)
    except (ValueError, TypeError):
        return 'P'
    if cs > 0:  # dog is away
        return _spread_outcome_away_covered(g)
    if cs < 0:  # dog is home
        return _spread_outcome_home_covered(g)
    return 'P'


def _public_overload_pp_fn(threshold_pct: float):
    """Factory: matches_fn — public betting >= threshold on primary_play
    side. Reads splits_summary.per_market for the pp market/side, checks
    public bet% or money%. If splits_summary shape doesn't expose this,
    returns False (silent skip)."""
    def _fn(g: dict) -> bool:
        pp = _primary_play(g)
        pp_side = str(pp.get('side') or '').upper()
        pp_type = str(pp.get('type') or '').lower()
        if not pp_side or not pp_type:
            return False
        ss = g.get('splits_summary') or {}
        if not isinstance(ss, dict): return False
        pm = ss.get('per_market') or {}
        # Map pp.type to splits key
        market_key = {'ml': 'ml', 'rl': 'rl', 'spread': 'rl', 'total': 'total'}.get(pp_type)
        if not market_key: return False
        market = pm.get(market_key) or {}
        # For side-based markets (ml/rl), splits store per-side pct
        side_key = pp_side.lower()  # 'home'/'away'/'over'/'under'
        side_data = market.get(side_key) or {}
        # Try common field names — bet_pct, public_pct, tickets_pct
        for k in ('public_pct', 'bet_pct', 'tickets_pct', 'public_bets_pct'):
            v = side_data.get(k)
            if v is not None:
                try:
                    return float(v) >= threshold_pct
                except (ValueError, TypeError):
                    pass
        return False
    return _fn


def _confluence_backs_pp_fn(min_abs_net: int):
    """Factory: matches_fn — signal_confluence_net has absolute value
    >= min_abs_net AND agrees direction with primary_play side."""
    def _fn(g: dict) -> bool:
        net = g.get('signal_confluence_net')
        if net is None: return False
        try:
            net = int(net)
        except (ValueError, TypeError):
            return False
        if abs(net) < min_abs_net: return False
        pp = _primary_play(g)
        pp_side = str(pp.get('side') or '').upper()
        # net > 0 = home, net < 0 = away
        if net > 0 and pp_side == 'HOME': return True
        if net < 0 and pp_side == 'AWAY': return True
        return False
    return _fn


def _sp_plus_underdog_fn(g: dict) -> bool:
    """NCAAF: SP+ predicts the market underdog. Fires when the sign of
    SP+ implied margin disagrees with the sign of close_spread."""
    sp_h = g.get('sp_plus_pred_home_pts')
    sp_a = g.get('sp_plus_pred_away_pts')
    cs = g.get('close_spread')
    if sp_h is None or sp_a is None or cs is None: return False
    try:
        sp_margin = float(sp_h) - float(sp_a)  # positive = home favored
        cs = float(cs)  # positive = home favored (nflverse convention)
    except (ValueError, TypeError):
        return False
    # SP+ favors home (sp_margin > 0) but market favors away (cs < 0) → back home dog
    if sp_margin > 3.0 and cs < -3.0: return True
    # SP+ favors away (sp_margin < 0) but market favors home (cs > 0) → back away dog
    if sp_margin < -3.0 and cs > 3.0: return True
    return False


def _sp_plus_underdog_outcome(g: dict) -> str:
    """Grade: did the SP+-favored (market underdog) side cover?"""
    sp_h = g.get('sp_plus_pred_home_pts')
    sp_a = g.get('sp_plus_pred_away_pts')
    if sp_h is None or sp_a is None: return 'P'
    try:
        sp_margin = float(sp_h) - float(sp_a)
    except (ValueError, TypeError):
        return 'P'
    if sp_margin > 0:  # SP+ favors home
        return _spread_outcome_home_covered(g)
    if sp_margin < 0:  # SP+ favors away
        return _spread_outcome_away_covered(g)
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
        'direction': 'BACK',
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
        'direction': 'BACK',
        'description': 'MLB STRONGs with 2+ sources confirming the sharp side.',
        'lookback_days': 30,
        'matches': lambda g: (
            len(_splits_triple_confirmed(g)) > 0
            and str(_primary_play(g).get('tier') or '').upper() == 'STRONG'
        ),
        'outcome': _pp_result_outcome,
    },
    {
        'sport': 'MLB',
        'key': 'mlb_confluence_backs_prime',
        'label': 'Confluence + PRIME',
        'direction': 'BACK',
        'description': 'MLB PRIMEs where signal_confluence_net has |value| ≥ 3 and agrees direction with our pick. Deep multi-signal agreement.',
        'lookback_days': 60,
        'matches': lambda g: (
            str(_primary_play(g).get('tier') or '').upper() == 'PRIME'
            and _confluence_backs_pp_fn(3)(g)
        ),
        'outcome': _pp_result_outcome,
    },
    {
        'sport': 'MLB',
        'key': 'mlb_public_overload_fade',
        'label': 'Public Trap',
        'direction': 'FADE',
        'description': 'MLB pick that Vegas public has piled 70%+ onto. Historically the public-heavy side underperforms — fade the pick.',
        'lookback_days': 90,
        'matches': _public_overload_pp_fn(70.0),
        'outcome': _pp_result_outcome_faded,  # W when pp side LOSES
    },

    # ─── NFL ─────────────────────────────────────────────────────────
    {
        'sport': 'NFL',
        'key': 'nfl_home_div_dog',
        'label': 'Home Div Dog',
        'direction': 'BACK',
        'description': 'NFL home team is a divisional underdog. Historically hits the spread at an above-market rate.',
        'lookback_days': 365,
        'matches': lambda g: (
            g.get('div_game') is True
            and g.get('close_spread') is not None
            and float(g.get('close_spread', 0)) < 0
        ),
        'outcome': _spread_outcome_home_covered,
    },
    {
        'sport': 'NFL',
        'key': 'nfl_road_fav_7plus_fade',
        'label': 'Road Fav 7+',
        'direction': 'FADE',
        'description': 'NFL road favorites of 7+ points. Classic angle — travel + inflated public perception. Fade the road favorite.',
        'lookback_days': 730,
        'matches': _road_favorite_spread_fn(7.0),
        # FADE the road fav means backing the home dog to cover
        'outcome': _spread_outcome_home_covered,
    },
    {
        'sport': 'NFL',
        'key': 'nfl_rest_edge_dog_back',
        'label': 'Rest Edge Dog',
        'direction': 'BACK',
        'description': 'NFL underdog with a 3+ day rest advantage (short-week fav vs bye-week dog, TNF fav vs SNF dog, etc). Back the rested dog.',
        'lookback_days': 730,
        'matches': _rest_edge_underdog_fn(3),
        'outcome': _rest_edge_underdog_outcome,
    },

    # ─── NCAAF ───────────────────────────────────────────────────────
    {
        'sport': 'NCAAF',
        'key': 'ncaaf_road_fav_10plus_fade',
        'label': 'CFB Road Fav 10+',
        'direction': 'FADE',
        'description': 'NCAAF road favorites of 10+ points. Road environments are punishing (crowd, travel, altitude). Fade the road favorite.',
        'lookback_days': 730,
        'matches': _road_favorite_spread_fn(10.0),
        'outcome': _spread_outcome_home_covered,
    },
    {
        'sport': 'NCAAF',
        'key': 'ncaaf_sp_underdog_edge',
        'label': 'SP+ Dog Edge',
        'direction': 'BACK',
        'description': 'NCAAF game where SP+ efficiency rating favors the market underdog by 3+ points. Model disagrees with market — back the SP+ pick.',
        'lookback_days': 730,
        'matches': _sp_plus_underdog_fn,
        'outcome': _sp_plus_underdog_outcome,
    },

    # ─── NBA / NCAAB / NHL — extend once season data thickens ──────
    # NBA opens 10/22; NCAAB 11/3; NHL 10/7. Add patterns once we have
    # enough graded games (30+) to compute meaningful hit rates.
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
        'direction': pattern.get('direction', 'BACK'),
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
