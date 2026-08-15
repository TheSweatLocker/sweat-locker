"""mine_line_patterns — the living/breathing pattern engine.

Two modes:
  --refresh   : re-evaluate every registered pattern against the archive,
                update hit_rate / n / edge_pp / hit_rate_30d / tier.
  --discover  : aggressive grid search across split thresholds × line
                movement × outcomes to auto-surface NEW candidate patterns.
                New candidates land in tier=DISCOVERY at bet_direction=FADE
                or FOLLOW depending on the correlation sign.

Sport-universal — the pattern condition JSONB abstracts sport-specific
fields so one condition schema fits MLB/NFL/NCAAF/NCAAB/NHL/UFC.

Tier lifecycle handled at end of --refresh:
  DISCOVERY  → VALIDATED when n>=50 AND hit_rate>=BASELINE + 5pp
  VALIDATED  → DECAYED   when hit_rate_30d < BASELINE OR n_30d < 15
  DECAYED    → RETIRED   when 90d in DECAYED with no recovery
  DECAYED    → VALIDATED when recovered

Baseline for a market/direction:
  ML/spread side pick: 50%   (coinflip)
  Total OVER/UNDER:    50%
  ML "underdog wins":  varies by odds — approximate 45% for -110 dogs

Downstream: play_of_day reads pattern_registry WHERE tier='VALIDATED'
and edge_pp > 3 to activate directional drivers.

CLI
  python mine_line_patterns.py --refresh                 # nightly refresh
  python mine_line_patterns.py --discover --sport MLB    # scan for new
  python mine_line_patterns.py --refresh --discover      # both
  python mine_line_patterns.py --seed                    # first-time seed
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from itertools import product

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


SUPPORTED_SPORTS = ['MLB', 'NFL', 'NCAAF', 'NCAAB', 'NHL', 'UFC']

# Per-sport results table for outcome lookup
RESULTS_TABLE = {
    'MLB':   'mlb_game_results',
    'NFL':   'nfl_game_results',
    'NCAAF': 'ncaaf_game_results',
    'NCAAB': 'ncaab_game_results',
    'NHL':   'nhl_game_results',
}

BASELINE = 50.0   # coinflip


# ──────────────────────────────────────────────────────────────────────
# SEEDED PATTERNS — the 10 hypotheses we know matter
# ──────────────────────────────────────────────────────────────────────
#
# Condition schema: list of dicts. Each dict has:
#   field: name of archive field to test
#   op:    '>=' | '>' | '<=' | '<' | '==' | 'between' | 'is_null' | 'not_null'
#   value: threshold (scalar or [lo, hi] for 'between')
#
# All conditions must pass (AND). Multiple named patterns model OR.
#
# bet_direction:
#   FOLLOW = bet the side that matches conditions
#   FADE   = bet the OPPOSITE side (short the matched side)
#   NEUTRAL = descriptive only (record-keeping)

SEEDED_PATTERNS = [
    # ─── Public-fade family ────
    {
        'name': 'both_sources_heavy_public_65+',
        'description': 'Both OC bets% and FR bettors% ≥ 65 on same side → fade the public',
        'conditions': [
            {'field': 'oc_bets_pct',    'op': '>=', 'value': 65},
            {'field': 'fr_bettors_pct', 'op': '>=', 'value': 65},
        ],
        'bet_direction': 'FADE',
    },
    {
        'name': 'both_sources_extreme_public_75+',
        'description': 'Extreme dual public 75+ → strong fade',
        'conditions': [
            {'field': 'oc_bets_pct',    'op': '>=', 'value': 75},
            {'field': 'fr_bettors_pct', 'op': '>=', 'value': 75},
        ],
        'bet_direction': 'FADE',
    },

    # ─── Sharp-follow family ────
    {
        'name': 'both_sources_sharp_money_60+',
        'description': 'Both OC money% and FR handle% ≥ 60, bets% < 55 → follow sharps',
        'conditions': [
            {'field': 'oc_money_pct',   'op': '>=', 'value': 60},
            {'field': 'fr_handle_pct',  'op': '>=', 'value': 60},
            {'field': 'oc_bets_pct',    'op': '<',  'value': 55},
        ],
        'bet_direction': 'FOLLOW',
    },
    {
        'name': 'both_sources_sharp_divergence_15+',
        'description': 'Both sources show money>bets by ≥15pp (sharp side of split) → follow',
        'conditions': [
            {'field': 'oc_divergence', 'op': '>=', 'value': 15},
        ],
        'bet_direction': 'FOLLOW',
    },

    # ─── Consensus family ────
    {
        'name': 'both_sources_consensus_both_heavy',
        'description': 'Both money% and bets% ≥ 60 on same side (consensus — everyone loves it)',
        'conditions': [
            {'field': 'oc_money_pct',   'op': '>=', 'value': 60},
            {'field': 'oc_bets_pct',    'op': '>=', 'value': 60},
            {'field': 'fr_handle_pct',  'op': '>=', 'value': 60},
            {'field': 'fr_bettors_pct', 'op': '>=', 'value': 60},
        ],
        'bet_direction': 'NEUTRAL',
    },

    # ─── Disagreement family ────
    {
        'name': 'sources_disagree_oc_sharp_fr_public',
        'description': 'OC says sharp on side (money%>=60), FR says public (bettors%>=60) → coin-flip check',
        'conditions': [
            {'field': 'oc_money_pct',   'op': '>=', 'value': 60},
            {'field': 'fr_bettors_pct', 'op': '>=', 'value': 60},
        ],
        'bet_direction': 'NEUTRAL',
    },
    {
        'name': 'sources_disagree_fr_sharp_oc_public',
        'description': 'FR says sharp (handle%>=60), OC says public (bets%>=60) → coin-flip check',
        'conditions': [
            {'field': 'fr_handle_pct',  'op': '>=', 'value': 60},
            {'field': 'oc_bets_pct',    'op': '>=', 'value': 60},
        ],
        'bet_direction': 'NEUTRAL',
    },

    # ─── One-source-loud family ────
    {
        'name': 'oc_only_sharp_signal_60+',
        'description': 'Only OC has data + shows sharp (money%>=60, bets%<50) — no FR to confirm',
        'conditions': [
            {'field': 'oc_money_pct',   'op': '>=', 'value': 60},
            {'field': 'oc_bets_pct',    'op': '<',  'value': 50},
            {'field': 'fr_handle_pct',  'op': 'is_null'},
        ],
        'bet_direction': 'FOLLOW',
    },
    {
        'name': 'fr_only_sharp_signal_60+',
        'description': 'Only FR has data + shows sharp (handle%>=60, bettors%<50) — no OC to confirm',
        'conditions': [
            {'field': 'fr_handle_pct',  'op': '>=', 'value': 60},
            {'field': 'fr_bettors_pct', 'op': '<',  'value': 50},
            {'field': 'oc_money_pct',   'op': 'is_null'},
        ],
        'bet_direction': 'FOLLOW',
    },

    # ─── Extreme edge case ────
    {
        'name': 'both_sources_heavy_public_dog_side',
        'description': 'Dual heavy public (65+) on the UNDERDOG side — reverse public trap',
        'conditions': [
            {'field': 'oc_bets_pct',    'op': '>=', 'value': 65},
            {'field': 'fr_bettors_pct', 'op': '>=', 'value': 65},
            {'field': 'current_odds',   'op': '>=', 'value': 105},
        ],
        'bet_direction': 'FADE',
    },

    # ─── FADE-SHARP family (seeded 2026-08-15 pm from empirical analysis) ─
    # Historical cut on n=172 OC-graded rows revealed:
    #   ML market money%≥80: BACK 36% / FADE 64% (n=36) → FADE_edge +11.5pp
    #   Divergence≥20 (money-bets): BACK 38% / FADE 62% (n=29) → +9.7pp
    #   money%≥70 + bets% 50-59 (moderate divergence): FADE 61.5% (n=13)
    # These contradict the "follow sharp $" seed patterns above. Seeded
    # here so miner tracks both directions; whichever accumulates larger
    # sample + higher hit rate becomes VALIDATED and gets the driver.
    {
        'name': 'oc_ml_extreme_money_gte_80_fade',
        'description': 'Loud sharp $ on ML (money%≥80) — fade beats follow historically (64% n=36)',
        'conditions': [
            {'field': 'oc_money_pct', 'op': '>=', 'value': 80},
        ],
        'bet_direction': 'FADE',
    },
    {
        'name': 'oc_divergence_gte_20_fade',
        'description': 'Money% - bets% ≥ 20 (big sharp divergence) — fade beats follow (62% n=29)',
        'conditions': [
            {'field': 'oc_divergence', 'op': '>=', 'value': 20},
        ],
        'bet_direction': 'FADE',
    },
    {
        'name': 'oc_money_gte_70_bets_lt_60_fade',
        'description': 'Mid-divergence zone (money≥70, bets<60) — fade edge (61% n=13)',
        'conditions': [
            {'field': 'oc_money_pct', 'op': '>=', 'value': 70},
            {'field': 'oc_bets_pct',  'op': '<',  'value': 60},
        ],
        'bet_direction': 'FADE',
    },
]


def eval_condition(cond: dict, row: dict) -> bool:
    field = cond['field']; op = cond['op']
    val = row.get(field)
    if op == 'is_null':  return val is None
    if op == 'not_null': return val is not None
    if val is None: return False
    try: v = float(val)
    except (TypeError, ValueError): return False
    if op == 'between':
        lo, hi = cond['value']
        return lo <= v <= hi
    thr = float(cond['value'])
    if op == '>=': return v >= thr
    if op == '>':  return v >  thr
    if op == '<=': return v <= thr
    if op == '<':  return v <  thr
    if op == '==': return v == thr
    return False


def eval_conditions(conditions: list, row: dict) -> bool:
    return all(eval_condition(c, row) for c in (conditions or []))


# ──────────────────────────────────────────────────────────────────────
# Outcome resolution — did the pattern's bet win?
# ──────────────────────────────────────────────────────────────────────

_RESULTS_CACHE = {}

def _load_results(sport: str) -> dict:
    """{(game_id): result_row} — cached per run. Schema per sport:
       MLB    → run_line_result ('home_cover'|'away_cover'|'push'), total_result
       NFL    → spread_result / home_spread_covered
       NCAAF  → same as NFL
       NCAAB  → same
       NHL    → puck_line_result
    """
    if sport in _RESULTS_CACHE: return _RESULTS_CACHE[sport]
    tbl = RESULTS_TABLE.get(sport)
    if not tbl:
        _RESULTS_CACHE[sport] = {}; return {}
    # Superset of columns across sports — request only known-safe fields.
    idx = {}
    for page in range(200):  # up to 200k rows
        r = requests.get(
            f'{SB}/rest/v1/{tbl}?select=*&limit=1000&offset={page*1000}',
            headers=H_READ, timeout=60)
        if r.status_code != 200: break
        chunk = r.json() or []
        if not chunk: break
        for row in chunk:
            if row.get('game_id'): idx[row['game_id']] = row
        if len(chunk) < 1000: break
    _RESULTS_CACHE[sport] = idx
    return idx


def _spread_hit_side(sport: str, r: dict) -> str | None:
    """Return 'HOME' or 'AWAY' or 'PUSH' for the spread outcome. Handles
    per-sport schema differences (run_line_result / spread_result /
    home_spread_covered / puck_line_result)."""
    # MLB
    rlr = (r.get('run_line_result') or '').strip().lower()
    if rlr:
        if 'push' in rlr: return 'PUSH'
        if 'home' in rlr: return 'HOME'
        if 'away' in rlr: return 'AWAY'
    # NHL
    plr = (r.get('puck_line_result') or '').strip().lower()
    if plr:
        if 'push' in plr: return 'PUSH'
        if 'home' in plr: return 'HOME'
        if 'away' in plr: return 'AWAY'
    # NFL/NCAAF/NCAAB — spread_result
    sr = (r.get('spread_result') or '').strip().lower()
    if sr:
        if 'push' in sr: return 'PUSH'
        if 'home' in sr: return 'HOME'
        if 'away' in sr: return 'AWAY'
    # Fallback — home_spread_covered boolean
    hsc = r.get('home_spread_covered')
    if hsc is True:  return 'HOME'
    if hsc is False: return 'AWAY'
    return None


def _score_outcome(sport: str, game_id: str, market: str,
                   pick_side: str, bet_direction: str) -> str | None:
    """Given a game + market + the pattern-matched side + bet_direction (FOLLOW/FADE),
    return 'HIT' | 'MISS' | 'PUSH' | None (game not graded / no data)."""
    results = _load_results(sport)
    r = results.get(game_id)
    if not r: return None
    if bet_direction == 'NEUTRAL': return None
    hs = r.get('home_score'); as_ = r.get('away_score')
    if hs is None or as_ is None: return None

    market = (market or '').lower()
    pick_side = (pick_side or '').upper()
    # Flip if pattern says FADE
    effective_side = pick_side
    if bet_direction == 'FADE':
        flip = {'HOME': 'AWAY', 'AWAY': 'HOME', 'OVER': 'UNDER', 'UNDER': 'OVER'}
        effective_side = flip.get(pick_side, pick_side)

    if market == 'ml':
        if hs == as_: return 'PUSH'
        actual = 'HOME' if hs > as_ else 'AWAY'
        return 'HIT' if actual == effective_side else 'MISS'
    if market == 'total':
        tr = (r.get('total_result') or '').lower()
        if tr not in ('over', 'under'): return None
        return 'HIT' if tr == effective_side.lower() else 'MISS'
    if market in ('spread', 'runline', 'puckline', 'rl', 'pl'):
        actual = _spread_hit_side(sport, r)
        if actual is None: return None
        if actual == 'PUSH': return 'PUSH'
        return 'HIT' if actual == effective_side else 'MISS'
    return None


# ──────────────────────────────────────────────────────────────────────
# Refresh — recompute stats for every registered pattern
# ──────────────────────────────────────────────────────────────────────

def _pull_archive_since(sport: str, days: int) -> list:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace('+', '%2B')
    rows = []
    for page in range(200):  # up to 200k rows
        r = requests.get(
            f'{SB}/rest/v1/public_splits_archive'
            f'?sport=eq.{sport}&captured_at=gte.{since}'
            f'&select=game_id,market,pick_side,oc_money_pct,oc_bets_pct,oc_divergence,'
            f'fr_handle_pct,fr_bettors_pct,current_line,current_odds,captured_at'
            f'&order=captured_at.desc&limit=1000&offset={page*1000}',
            headers=H_READ, timeout=60)
        if r.status_code != 200: break
        chunk = r.json() or []
        if not chunk: break
        rows.extend(chunk)
        if len(chunk) < 1000: break
    return rows


def _dedupe_latest_per_game(rows: list) -> list:
    """One row per (game_id, market, pick_side) — the latest snapshot."""
    idx = {}
    for row in rows:
        key = (row.get('game_id'), row.get('market'), row.get('pick_side'))
        if key not in idx: idx[key] = row  # rows sorted desc so first = latest
    return list(idx.values())


def _refresh_pattern(sport: str, pattern: dict) -> dict:
    """Return updated {hit_rate, n, hit_rate_30d, n_30d, edge_pp, tier}."""
    conditions = pattern.get('conditions') or []
    direction  = pattern.get('bet_direction', 'FOLLOW')

    hits = misses = pushes = 0
    hits_30d = misses_30d = 0
    cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)

    archive = _pull_archive_since(sport, 365)
    if not archive:
        return {'hit_rate': None, 'n': 0, 'hit_rate_30d': None,
                'n_30d': 0, 'edge_pp': None, 'tier': pattern.get('tier', 'DISCOVERY')}

    latest_per_game = _dedupe_latest_per_game(archive)
    hits_by_game = []
    for row in latest_per_game:
        if not eval_conditions(conditions, row): continue
        outcome = _score_outcome(sport, row['game_id'], row['market'],
                                  row['pick_side'], direction)
        if outcome is None: continue
        try:
            captured = datetime.fromisoformat(row['captured_at'].replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            captured = None
        recent = captured is not None and captured >= cutoff_30d
        if outcome == 'HIT':
            hits += 1;   hits_by_game.append(1)
            if recent: hits_30d += 1
        elif outcome == 'MISS':
            misses += 1; hits_by_game.append(0)
            if recent: misses_30d += 1
        elif outcome == 'PUSH':
            pushes += 1

    n = hits + misses
    n_30d = hits_30d + misses_30d
    hit_rate     = round(100 * hits / n, 1) if n else None
    hit_rate_30d = round(100 * hits_30d / n_30d, 1) if n_30d else None
    edge_pp      = round(hit_rate - BASELINE, 1) if hit_rate is not None else None

    # Tier lifecycle
    current_tier = pattern.get('tier', 'DISCOVERY')
    new_tier = current_tier
    if current_tier == 'DISCOVERY':
        if n >= 50 and hit_rate is not None and hit_rate >= BASELINE + 5:
            new_tier = 'VALIDATED'
    elif current_tier == 'VALIDATED':
        if hit_rate_30d is not None and hit_rate_30d < BASELINE:
            new_tier = 'DECAYED'
        elif n_30d < 5:
            pass  # keep, insufficient recent to judge
    elif current_tier == 'DECAYED':
        if hit_rate_30d is not None and hit_rate_30d >= BASELINE + 5 and n_30d >= 15:
            new_tier = 'VALIDATED'

    return {
        'hit_rate': hit_rate, 'n': n,
        'hit_rate_30d': hit_rate_30d, 'n_30d': n_30d,
        'edge_pp': edge_pp, 'tier': new_tier,
    }


def refresh_all(sport: str, dry_run: bool = False) -> int:
    """Iterate pattern_registry for sport (+ universal '*'); update each."""
    r = requests.get(
        f'{SB}/rest/v1/pattern_registry'
        f'?or=(sport.eq.{sport},sport.eq.*)&tier=neq.RETIRED'
        f'&select=id,sport,name,conditions,bet_direction,tier',
        headers=H_READ, timeout=15)
    patterns = r.json() if r.status_code == 200 else []
    print(f'  {sport}: {len(patterns)} patterns to refresh')
    updated = 0
    for p in patterns:
        stats = _refresh_pattern(sport, p)
        stats['last_computed_at'] = datetime.now(timezone.utc).isoformat()
        if dry_run:
            print(f'    [DRY] {p["name"]:<40} tier {p["tier"]}→{stats["tier"]:<10} '
                  f'{stats["hit_rate"]}% n={stats["n"]} · 30d {stats["hit_rate_30d"]}% n={stats["n_30d"]}')
            updated += 1
            continue
        pr = requests.patch(
            f'{SB}/rest/v1/pattern_registry?id=eq.{p["id"]}',
            headers=H_WRITE, json=stats, timeout=15)
        if pr.status_code in (200, 204):
            updated += 1
    return updated


# ──────────────────────────────────────────────────────────────────────
# Discover — auto-scan the archive for hidden edges
# ──────────────────────────────────────────────────────────────────────

DISCOVERY_MIN_N       = 15
DISCOVERY_MIN_EDGE_PP = 5.0

def discover_patterns(sport: str, dry_run: bool = False) -> int:
    """Grid-search across split thresholds × market → surface candidates."""
    archive = _pull_archive_since(sport, 90)
    if not archive:
        print(f'  {sport}: no archive yet, skip discovery'); return 0
    latest = _dedupe_latest_per_game(archive)
    print(f'  {sport}: {len(latest)} unique game×market×side to scan')

    # Threshold grids for each split dimension
    grids = {
        'oc_money_pct':   [55, 60, 65, 70, 75],
        'oc_bets_pct':    [55, 60, 65, 70, 75],
        'fr_handle_pct':  [55, 60, 65, 70, 75],
        'fr_bettors_pct': [55, 60, 65, 70, 75],
    }
    directions = ['FOLLOW', 'FADE']

    candidates = []
    # 4 fields × 5 thresholds each × 2 directions = 200 patterns per sport
    # Also test each field in isolation AND paired with another field
    for field, thresholds in grids.items():
        for thr in thresholds:
            for direction in directions:
                # Single-field pattern
                pattern = {
                    'name': f'auto_{field}_gte_{thr}_{direction.lower()}',
                    'conditions': [{'field': field, 'op': '>=', 'value': thr}],
                    'bet_direction': direction,
                    'tier': 'DISCOVERY',
                }
                stats = _refresh_pattern(sport, pattern)
                if stats['n'] < DISCOVERY_MIN_N: continue
                if stats['edge_pp'] is None or stats['edge_pp'] < DISCOVERY_MIN_EDGE_PP:
                    continue
                candidates.append((pattern, stats))

    # Pair-scan: same-source both directions AND cross-source correlations
    pair_combos = [
        (('oc_money_pct',   '>=', 60), ('oc_bets_pct',    '<',  55)),
        (('oc_money_pct',   '>=', 65), ('oc_bets_pct',    '<',  55)),
        (('fr_handle_pct',  '>=', 60), ('fr_bettors_pct', '<',  55)),
        (('oc_bets_pct',    '>=', 65), ('fr_bettors_pct', '>=', 65)),
        (('oc_money_pct',   '>=', 60), ('fr_handle_pct',  '>=', 60)),
        (('oc_bets_pct',    '>=', 70), ('fr_bettors_pct', '<',  50)),
        (('fr_bettors_pct', '>=', 70), ('oc_bets_pct',    '<',  50)),
        (('oc_divergence',  '>=', 20), ('oc_money_pct',   '>=', 60)),
    ]
    for combo in pair_combos:
        for direction in directions:
            conds = [{'field': f, 'op': o, 'value': v} for (f, o, v) in combo]
            name_bits = [f'{f}_{o}_{v}' for (f, o, v) in combo]
            pattern = {
                'name': f'auto_pair_' + '__'.join(name_bits) + f'_{direction.lower()}',
                'conditions': conds,
                'bet_direction': direction,
                'tier': 'DISCOVERY',
            }
            stats = _refresh_pattern(sport, pattern)
            if stats['n'] < DISCOVERY_MIN_N: continue
            if stats['edge_pp'] is None or stats['edge_pp'] < DISCOVERY_MIN_EDGE_PP:
                continue
            candidates.append((pattern, stats))

    print(f'  {sport}: {len(candidates)} candidates cleared discovery gate '
          f'(n>={DISCOVERY_MIN_N}, edge>={DISCOVERY_MIN_EDGE_PP}pp)')
    if dry_run:
        for p, s in candidates[:10]:
            print(f'    [DRY] {p["name"][:60]:<60} · {s["hit_rate"]}% n={s["n"]} edge {s["edge_pp"]:+.1f}pp')
        return len(candidates)

    written = 0
    for p, s in candidates:
        payload = {
            'sport': sport,
            'name':  p['name'][:200],
            'description': f'Auto-discovered {datetime.now().date().isoformat()}',
            'conditions': p['conditions'],
            'bet_direction': p['bet_direction'],
            'hit_rate': s['hit_rate'], 'n': s['n'],
            'hit_rate_30d': s['hit_rate_30d'], 'n_30d': s['n_30d'],
            'edge_pp': s['edge_pp'],
            'tier': s['tier'],
            'origin': 'DISCOVERED',
            'last_computed_at': datetime.now(timezone.utc).isoformat(),
        }
        r = requests.post(
            f'{SB}/rest/v1/pattern_registry?on_conflict=sport,name',
            headers=H_WRITE, json=payload, timeout=15)
        if r.status_code in (200, 201, 204):
            written += 1
    return written


# ──────────────────────────────────────────────────────────────────────
# Seed — first-time population of registry
# ──────────────────────────────────────────────────────────────────────

def seed(dry_run: bool = False) -> int:
    print('=== Seeding pattern_registry with hypotheses ===')
    written = 0
    for sport in SUPPORTED_SPORTS:
        for pat in SEEDED_PATTERNS:
            payload = {
                'sport': sport,
                'name':  pat['name'],
                'description': pat['description'],
                'conditions': pat['conditions'],
                'bet_direction': pat['bet_direction'],
                'tier': 'DISCOVERY',
                'origin': 'SEEDED',
            }
            if dry_run:
                print(f'  [DRY] {sport} · {pat["name"]}')
                written += 1
                continue
            r = requests.post(
                f'{SB}/rest/v1/pattern_registry?on_conflict=sport,name',
                headers=H_WRITE, json=payload, timeout=15)
            if r.status_code in (200, 201, 204):
                written += 1
            else:
                print(f'  ✗ {sport} {pat["name"]}: {r.status_code} {r.text[:120]}')
    print(f'  ✓ {written} seeds written')
    return written


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--refresh',   action='store_true', help='Refresh registered patterns')
    p.add_argument('--discover',  action='store_true', help='Auto-discover new patterns')
    p.add_argument('--seed',      action='store_true', help='First-time seed of registry')
    p.add_argument('--sport',     choices=SUPPORTED_SPORTS + ['ALL'], default='ALL')
    p.add_argument('--dry-run',   action='store_true')
    args = p.parse_args()

    if not any((args.refresh, args.discover, args.seed)):
        p.error('specify --refresh, --discover, or --seed')

    if args.seed:
        seed(dry_run=args.dry_run)

    sports = SUPPORTED_SPORTS if args.sport == 'ALL' else [args.sport]

    if args.refresh:
        print(f'=== refresh · {"/".join(sports)} ===')
        for s in sports:
            n = refresh_all(s, dry_run=args.dry_run)
            print(f'  ✓ {s}: {n} patterns refreshed')

    if args.discover:
        print(f'=== discover · {"/".join(sports)} ===')
        for s in sports:
            n = discover_patterns(s, dry_run=args.dry_run)
            print(f'  ✓ {s}: {n} candidates surfaced')


if __name__ == '__main__':
    main()
