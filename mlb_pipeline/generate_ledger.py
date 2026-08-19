"""The Ledger — auto-suggested chalk parlays + teasers (2026-08-17).

Cross-sport combo builder. Reads today's ensemble picks across all
sports and produces 2-4 suggestions of:
  * CHALK PARLAY: 2-3 ML favorites combined for +100 to +150 even money
  * TEASER:       spread or total moved into higher-probability zone
                  + correlated pair for even money math

Writes to ledger_suggestions table. App renders in Steam Room 4th tab.

Payout math (American odds combined):
  odds → decimal → multiply → back to American
  e.g. -140 + -160 + -180 → 1.71 * 1.63 * 1.56 = 4.35 → +335

Teaser conversion for totals/spreads:
  Standard MLB teaser: 0.5-1.5 runs, roughly -170 to -110 per leg
  Standard NFL teaser: 6-7 points, -110 to +100 depending on new juice

CLI:
    python generate_ledger.py               # today, all sports
    python generate_ledger.py --sport MLB
    python generate_ledger.py --dry-run
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone, timedelta
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
H_WRITE = {**H_READ, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

# Sport → game_context table
CTX_TABLE = {
    'MLB':   'mlb_game_context',
    'NFL':   'nfl_game_context',
    'NCAAF': 'ncaaf_game_context',
    'NCAAB': 'ncaab_game_context',
}
# Teaser step per sport (points/runs moved from original line)
TEASER_STEP = {
    'MLB':   1.5,   # 8.0 total → 6.5, or +1.5 spread → +3.0
    'NFL':   6.0,   # standard NFL teaser
    'NCAAF': 6.0,
    'NCAAB': 4.0,
}


# ═══════════════════════════════════════════════════════════════════════
# ODDS MATH
# ═══════════════════════════════════════════════════════════════════════

def _american_to_decimal(american: int | float) -> float:
    """Convert American odds to decimal payout multiplier (for a 1u stake)."""
    if american is None: return 1.91
    try: a = float(american)
    except (TypeError, ValueError): return 1.91
    if a >= 100: return 1 + a / 100.0
    if a <= -100: return 1 + 100.0 / abs(a)
    return 1.91


def _decimal_to_american(decimal: float) -> int:
    """Convert decimal odds back to American format."""
    if decimal >= 2.0: return int(round((decimal - 1) * 100))
    if decimal > 1.0: return int(round(-100 / (decimal - 1)))
    return -10000  # near-lock, cap


def combined_american_odds(american_list: list[int | float]) -> int:
    """Combined parlay odds from a list of American odds."""
    decimal = 1.0
    for a in american_list:
        decimal *= _american_to_decimal(a)
    return _decimal_to_american(decimal)


# ═══════════════════════════════════════════════════════════════════════
# TEASER PRICING (approximation)
# ═══════════════════════════════════════════════════════════════════════

def teaser_price(sport: str, market: str, original_odds: int | float,
                 line_move: float) -> int:
    """Approximate teaser leg pricing after moving the line `line_move`
    points in the favorable direction.

    Real books use lookup tables. This is a linear approximation good
    enough for suggestion math — final price should be verified at book.
    Approximation calibrated to typical DraftKings/FanDuel teaser tables:
      * MLB 1.5-run tease: adds ~140-180 juice to a -110 leg
      * NFL 6-point tease: makes -3 into +3, roughly -110 → -110 (even
        after tease because the market rebalances)

    Rough rule: each point/run of movement adds ~40-60 juice to the leg."""
    juice_per_point = {
        'MLB':   90.0,  # MLB moves are big — 1.5 run tease is significant
        'NFL':   25.0,  # NFL 6pt tease keeps close to even juice
        'NCAAF': 25.0,
        'NCAAB': 35.0,
    }.get(sport, 40.0)
    add_juice = int(line_move * juice_per_point)
    try:
        base = float(original_odds)
    except (TypeError, ValueError):
        base = -110
    # Move toward more negative (worse odds for us) by add_juice
    new_odds = base - abs(add_juice)
    if new_odds > -100:
        # Odds crossed into positive, convert conservatively
        new_odds = int(-100 - abs(add_juice))
    return int(new_odds)


# ═══════════════════════════════════════════════════════════════════════
# FETCH TODAY'S PICKS
# ═══════════════════════════════════════════════════════════════════════

def fetch_chalk_candidates(game_date: str, sports: list[str]) -> list[dict]:
    """Pull every game's chalk favorite ML (regardless of ensemble pick side).
    Feeds build_chalk_parlay so we can compose chalk trios even when the
    ensemble is backing dogs. 2026-08-18 addition per user redesign.

    Excludes games where the chalk ML is a coin flip (-100 to -124) — those
    aren't real chalk. Also excludes games with no close ML posted."""
    out = []
    for sport in sports:
        table = CTX_TABLE.get(sport)
        if not table: continue
        try:
            r = requests.get(
                f'{SB}/rest/v1/{table}',
                headers=H_READ,
                params={'game_date': f'eq.{game_date}',
                        'select': 'game_id,home_team,away_team,'
                                  'home_ml_close,away_ml_close,primary_play'},
                timeout=15)
            rows = r.json() if r.status_code == 200 else []
        except Exception:
            continue
        for row in rows:
            hml = row.get('home_ml_close'); aml = row.get('away_ml_close')
            if hml is None or aml is None: continue
            try: hml_i = int(hml); aml_i = int(aml)
            except (TypeError, ValueError): continue
            # Determine chalk side
            if hml_i < aml_i:
                chalk_side, chalk_odds = 'HOME', hml_i
                chalk_team = row.get('home_team')
            else:
                chalk_side, chalk_odds = 'AWAY', aml_i
                chalk_team = row.get('away_team')
            # Only include actual chalk (-125 or more juice)
            if chalk_odds > -125: continue
            # Determine a proxy tier: if ensemble picked the SAME side, promote;
            # if ensemble picked the DOG side, mark as CHALK_ONLY (not our edge
            # but a real chalk stack candidate).
            pp = row.get('primary_play') or {}
            if isinstance(pp, str):
                try: pp = json.loads(pp)
                except: pp = {}
            pp_side = (pp.get('side') or '').upper() if isinstance(pp, dict) else ''
            pp_type = (pp.get('type') or '').lower() if isinstance(pp, dict) else ''
            aligned = pp_type == 'ml' and pp_side == chalk_side
            tier = pp.get('tier') if aligned else 'CHALK_ONLY'
            conviction = pp.get('conviction', 0) if aligned else 55  # neutral proxy
            out.append({
                'sport': sport,
                'game_id': row.get('game_id'),
                'matchup': f"{row.get('away_team')} @ {row.get('home_team')}",
                'market': 'ml',
                'pick': f'{chalk_team} ML',
                'side': chalk_side,
                'original_line': None,
                'original_odds': chalk_odds,
                'tier': tier if tier in ('PRIME','STRONG','LEAN','CHALK_ONLY') else 'CHALK_ONLY',
                'conviction': conviction,
            })
    return out


def fetch_picks(game_date: str, sports: list[str]) -> list[dict]:
    """Pull all PRIME/STRONG picks with a real primary_play across sports.
    Returns list of leg-shaped dicts."""
    picks = []
    for sport in sports:
        table = CTX_TABLE.get(sport)
        if not table: continue
        try:
            r = requests.get(
                f'{SB}/rest/v1/{table}',
                headers=H_READ,
                params={'game_date': f'eq.{game_date}',
                        'select': 'game_id,home_team,away_team,close_spread,close_total,'
                                  'home_ml_close,away_ml_close,primary_play'},
                timeout=15)
            rows = r.json() if r.status_code == 200 else []
        except Exception:
            continue
        for row in rows:
            pp = row.get('primary_play')
            if isinstance(pp, str):
                try: pp = json.loads(pp)
                except: continue
            if not isinstance(pp, dict): continue
            tier = pp.get('tier')
            if tier not in ('PRIME', 'STRONG', 'LEAN'): continue
            side = pp.get('side')
            pick_type = pp.get('type', '').lower()
            label = pp.get('label')
            if not label: continue
            # Determine odds based on market
            odds = None
            line = None
            if pick_type == 'ml':
                odds = row.get('home_ml_close') if side == 'HOME' else row.get('away_ml_close')
            elif pick_type == 'rl' or pick_type == 'spread':
                line = pp.get('line') or row.get('close_spread')
                odds = -110  # rl typically -110
            elif pick_type == 'total':
                line = pp.get('line') or row.get('close_total')
                odds = -110
            picks.append({
                'sport': sport,
                'game_id': row.get('game_id'),
                'matchup': f"{row.get('away_team')} @ {row.get('home_team')}",
                'market': pick_type,
                'pick': label,
                'side': side,
                'original_line': line,
                'original_odds': odds,
                'tier': tier,
                'conviction': pp.get('conviction', 0),
            })
    return picks


# ═══════════════════════════════════════════════════════════════════════
# BUILDER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def build_chalk_parlay(picks: list[dict], target_odds_range: tuple = (-180, 175),
                        exclude_games: set = None) -> Optional[dict]:
    """Combine 2-3 ML CHALK favorites into a parlay near even money.
    2026-08-18 REDESIGN per user: 'high-confidence chalky parlays that
    roughly equal even money, -140 even alright' — not longshot dogs.

    Target: 2-3 favorites at -125 to -300 each → combined -180 to -100.
    Prior version targeted +100 to +200 (light dog territory) which
    conflicts with the Ledger's chalk-confidence identity. Bold-dog
    combos live nowhere now (dog_parlay killed 2026-08-18).

    exclude_games: set of game_ids already used in prior suggestions this
    run — prevents Reds appearing in multiple suggestions."""
    exclude_games = exclude_games or set()
    # 2026-08-18: accept CHALK_ONLY tier alongside PRIME/STRONG/LEAN so
    # the trio composes even on nights where ensemble backs the dog side
    # of every game. Ledger's chalk product is about HIT-RATE stacks, not
    # ensemble edge — chalk favorites are inherently high hit-rate.
    candidates = [p for p in picks
                  if p['market'] == 'ml'
                  and p['original_odds'] is not None
                  and -300 <= float(p['original_odds']) <= -125
                  and p['tier'] in ('PRIME', 'STRONG', 'LEAN', 'CHALK_ONLY')
                  and p.get('game_id') not in exclude_games]
    # Prefer ensemble-aligned chalks first, then CHALK_ONLY
    tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2, 'CHALK_ONLY': 3}
    candidates.sort(key=lambda p: (tier_rank.get(p['tier'], 9), -p.get('conviction', 0)))

    # Try 3-leg first (bigger chalk stack), then 2-leg
    for n_legs in (3, 2):
        if len(candidates) < n_legs: continue
        legs = candidates[:n_legs]
        combined = combined_american_odds([p['original_odds'] for p in legs])
        if target_odds_range[0] <= combined <= target_odds_range[1]:
            return {
                'kind': 'chalk_parlay',
                'sport_scope': 'MLB' if all(l['sport']=='MLB' for l in legs) else 'MULTI',
                'legs': [{
                    'sport': l['sport'], 'game_id': l.get('game_id'),
                    'matchup': l['matchup'], 'market': l['market'],
                    'pick': l['pick'], 'original_odds': l['original_odds'],
                    'original_line': l['original_line'],
                    'teased_line': None, 'teased_odds': None,
                    'tier': l['tier'], 'conviction': l.get('conviction'),
                } for l in legs],
                'combined_odds': combined,
                'reasoning': f'{n_legs}-leg chalk stack: {" · ".join(l["pick"] for l in legs)}. '
                             f'Combined {combined:+d} — chalk favorites at near-even money.',
            }
    return None


def fetch_alt_line(game_id: str, market: str, side: str, target_line: float,
                    game_date: str) -> Optional[int]:
    """Look up real Odds API alt-line price at target_line (exact match).
    Returns None if not cached — caller falls back to teaser_price estimator.
    market: 'alternate_totals' | 'alternate_spreads'
    side:   'OVER' | 'UNDER' | 'HOME' | 'AWAY'
    """
    try:
        r = requests.get(f'{SB}/rest/v1/alt_line_snapshots',
                         headers=H_READ,
                         params={
                             'game_id': f'eq.{game_id}',
                             'market': f'eq.{market}',
                             'side': f'eq.{side}',
                             'line': f'eq.{target_line}',
                             'snapshot_date': f'eq.{game_date}',
                             'select': 'price', 'limit': '1'},
                         timeout=8)
        rows = r.json() if r.status_code == 200 else []
        return int(rows[0]['price']) if rows else None
    except Exception:
        return None


def build_teased_totals_combo(picks: list[dict], exclude_games: set = None) -> Optional[dict]:
    """Pair TWO total picks — tease each by 1.5-2 into higher-hit zones —
    combined for near-even money. 2026-08-18 per user: 'Red Sox game
    teased down to O 6.5 or 5.5 depending on value with another total
    we liked teased up or down.'

    This is the highest-EV Ledger product because both legs move to
    easier lines simultaneously. Only PRIME/STRONG total picks eligible."""
    exclude_games = exclude_games or set()
    # 2026-08-18: include LEAN totals — playbook total picks land LEAN on
    # v4-fallback games where MC probabilities are unavailable. Teasing
    # LEAN totals to easier lines still produces a defensible combo since
    # the tease itself moves us into higher-hit-rate territory.
    tease_candidates = [p for p in picks
                        if p['market'] == 'total'
                        and p['original_line'] is not None
                        and p['tier'] in ('PRIME', 'STRONG', 'LEAN')
                        and p.get('game_id') not in exclude_games]
    tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
    tease_candidates.sort(key=lambda p: (tier_rank.get(p['tier'], 9), -p.get('conviction', 0)))
    if len(tease_candidates) < 2: return None

    def tease_leg(p, game_date):
        sport = p['sport']
        step = TEASER_STEP.get(sport, 1.5)
        orig_line = float(p['original_line'])
        orig_odds = p['original_odds']
        side = (p.get('side') or '').upper()
        if side == 'OVER': teased_line = orig_line - step
        else:              teased_line = orig_line + step
        # 2026-08-18: prefer REAL alt-line price from cache (pull_alt_lines.py)
        # over teaser_price estimator. Falls back to estimator if not cached.
        real_price = fetch_alt_line(p.get('game_id',''), 'alternate_totals',
                                     side, teased_line, game_date)
        teased_odds = real_price if real_price is not None \
                     else teaser_price(sport, 'total', orig_odds, step)
        price_source = 'book' if real_price is not None else 'estimator'
        return ({
            'sport': sport, 'game_id': p.get('game_id'),
            'matchup': p['matchup'], 'market': 'total',
            'pick': p['pick'], 'side': side,
            'original_odds': orig_odds,
            'original_line': orig_line, 'teased_line': teased_line,
            'teased_odds': teased_odds, 'price_source': price_source,
            'tier': p['tier'], 'conviction': p.get('conviction'),
        })

    gd = tease_candidates[0].get('game_date') or _et_today()
    leg_a = tease_leg(tease_candidates[0], gd)
    leg_b = tease_leg(tease_candidates[1], gd)
    combined = combined_american_odds([leg_a['teased_odds'], leg_b['teased_odds']])
    sports_in = {leg_a['sport'], leg_b['sport']}
    real_count = sum(1 for l in (leg_a, leg_b) if l.get('price_source') == 'book')
    return {
        'kind': 'teased_totals_combo',
        'sport_scope': next(iter(sports_in)) if len(sports_in) == 1 else 'MULTI',
        'legs': [leg_a, leg_b],
        'combined_odds': combined,
        'reasoning': f'Teased totals combo: {leg_a["pick"]} → {leg_a["teased_line"]} + '
                     f'{leg_b["pick"]} → {leg_b["teased_line"]}. '
                     f'Combined {combined:+d}. '
                     f'({real_count}/2 legs at real book prices, rest estimated.)',
    }


def build_teased_spreads_combo(picks: list[dict], exclude_games: set = None) -> Optional[dict]:
    """Pair TWO spread/RL picks — tease each into safer coverage zone.
    2026-08-18 per user: 'Could also tease spreads across sports like
    Reds +2.5.' Mirror of teased_totals_combo for run-line / point-spread.

    Tease direction:
      Favorite (line < 0): move toward 0 or positive (e.g., -1.5 → +0.5)
      Dog (line > 0):     move to more coverage (e.g., +1.5 → +3.0)
    Cross-sport eligible (MLB RL, NFL/NCAAF/NCAAB spread) — TEASER_STEP
    per sport keeps the math sane."""
    exclude_games = exclude_games or set()
    tease_candidates = [p for p in picks
                        if p['market'] in ('rl', 'spread', 'runline')
                        and p['original_line'] is not None
                        and p['tier'] in ('PRIME', 'STRONG', 'LEAN')
                        and p.get('game_id') not in exclude_games]
    tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
    tease_candidates.sort(key=lambda p: (tier_rank.get(p['tier'], 9), -p.get('conviction', 0)))
    if len(tease_candidates) < 2: return None

    def tease_leg(p, game_date):
        sport = p['sport']
        step = TEASER_STEP.get(sport, 1.5)
        orig_line = float(p['original_line'])
        orig_odds = p['original_odds']
        # Line always moves toward MORE cover
        teased_line = orig_line + step
        side = (p.get('side') or '').upper()
        real_price = fetch_alt_line(p.get('game_id',''), 'alternate_spreads',
                                     side, teased_line, game_date)
        teased_odds = real_price if real_price is not None \
                     else teaser_price(sport, p['market'], orig_odds, step)
        price_source = 'book' if real_price is not None else 'estimator'
        return {
            'sport': sport, 'game_id': p.get('game_id'),
            'matchup': p['matchup'], 'market': p['market'],
            'pick': p['pick'], 'side': side,
            'original_odds': orig_odds,
            'original_line': orig_line, 'teased_line': teased_line,
            'teased_odds': teased_odds, 'price_source': price_source,
            'tier': p['tier'], 'conviction': p.get('conviction'),
        }

    gd = tease_candidates[0].get('game_date') or _et_today()
    leg_a = tease_leg(tease_candidates[0], gd)
    leg_b = tease_leg(tease_candidates[1], gd)
    combined = combined_american_odds([leg_a['teased_odds'], leg_b['teased_odds']])
    sports_in = {leg_a['sport'], leg_b['sport']}
    real_count = sum(1 for l in (leg_a, leg_b) if l.get('price_source') == 'book')
    return {
        'kind': 'teased_spreads_combo',
        'sport_scope': next(iter(sports_in)) if len(sports_in) == 1 else 'MULTI',
        'legs': [leg_a, leg_b],
        'combined_odds': combined,
        'reasoning': f'Teased spreads combo: {leg_a["pick"]} → {leg_a["teased_line"]:+g} + '
                     f'{leg_b["pick"]} → {leg_b["teased_line"]:+g}. '
                     f'Combined {combined:+d}. '
                     f'({real_count}/2 legs at real book prices.)',
    }


def _dead_dog_parlay_KILLED(picks: list[dict], target_odds_range: tuple = (250, 700)) -> Optional[dict]:
    """DEPRECATED 2026-08-18: user feedback: 'bold dog does not make the
    cut in what should be chalky high confidence parlays and plays.'
    Kept only as a marker so future me doesn't reintroduce dog parlays.
    Ledger is the CHALK CONFIDENCE product. Dog moonshots don't fit."""
    return None


def _dead_dog_parlay_original(picks: list[dict], target_odds_range: tuple = (250, 700)) -> Optional[dict]:
    """Placeholder for the deleted dog-parlay body — real code lived here.
    See _dead_dog_parlay_KILLED for the rationale."""
    candidates = [p for p in picks
                  if p['market'] == 'ml'
                  and p['original_odds'] is not None
                  and 100 <= float(p['original_odds']) <= 220
                  and p['tier'] in ('PRIME', 'STRONG', 'LEAN')]
    tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
    candidates.sort(key=lambda p: (tier_rank.get(p['tier'], 9), -p.get('conviction', 0)))
    for n_legs in (3, 2):
        if len(candidates) < n_legs: continue
        legs = candidates[:n_legs]
        combined = combined_american_odds([p['original_odds'] for p in legs])
        if target_odds_range[0] <= combined <= target_odds_range[1]:
            sports_in = set(l['sport'] for l in legs)
            return {
                'kind': 'dog_parlay',
                'sport_scope': legs[0]['sport'] if len(sports_in) == 1 else 'MULTI',
                'legs': [{
                    'sport': l['sport'], 'game_id': l.get('game_id'),
                    'matchup': l['matchup'], 'market': l['market'],
                    'pick': l['pick'], 'original_odds': l['original_odds'],
                    'original_line': l['original_line'],
                    'teased_line': None, 'teased_odds': None,
                    'tier': l['tier'], 'conviction': l.get('conviction'),
                } for l in legs],
                'combined_odds': combined,
                'reasoning': f'{n_legs}-leg dog parlay: {" · ".join(l["pick"] for l in legs)}. '
                             f'Combined +{combined} — smaller hit but bigger payoff. '
                             f'All legs ensemble-endorsed at PRIME/STRONG/LEAN tier.',
            }
    return None


def build_mixed_parlay(picks: list[dict], target_odds_range: tuple = (150, 400)) -> Optional[dict]:
    """Combine 2-3 picks of mixed types (favs + dogs + totals + RLs) into a
    parlay landing in target_odds_range. Correlation-agnostic — takes best
    tier-ranked picks that produce clean parlay math. Filler when chalk +
    dog specialized parlays don't fire but the slate has good legs."""
    candidates = [p for p in picks
                  if p['original_odds'] is not None
                  and -350 <= float(p['original_odds']) <= 250
                  and p['tier'] in ('PRIME', 'STRONG', 'LEAN')]
    # Dedupe by game — no two legs from same game (correlation risk)
    seen_games = set()
    unique = []
    tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
    candidates.sort(key=lambda p: (tier_rank.get(p['tier'], 9), -p.get('conviction', 0)))
    for p in candidates:
        gid = p.get('game_id')
        if gid and gid in seen_games: continue
        seen_games.add(gid)
        unique.append(p)
    for n_legs in (3, 2):
        if len(unique) < n_legs: continue
        legs = unique[:n_legs]
        combined = combined_american_odds([p['original_odds'] for p in legs])
        if target_odds_range[0] <= combined <= target_odds_range[1]:
            sports_in = set(l['sport'] for l in legs)
            return {
                'kind': 'mixed_parlay',
                'sport_scope': legs[0]['sport'] if len(sports_in) == 1 else 'MULTI',
                'legs': [{
                    'sport': l['sport'], 'game_id': l.get('game_id'),
                    'matchup': l['matchup'], 'market': l['market'],
                    'pick': l['pick'], 'original_odds': l['original_odds'],
                    'original_line': l['original_line'],
                    'teased_line': None, 'teased_odds': None,
                    'tier': l['tier'], 'conviction': l.get('conviction'),
                } for l in legs],
                'combined_odds': combined,
                'reasoning': f'{n_legs}-leg mixed parlay: {" · ".join(l["pick"] for l in legs)}. '
                             f'Combined {"+" if combined > 0 else ""}{combined} — '
                             f'cross-market/cross-game ensemble picks.',
            }
    return None


def build_teaser(picks: list[dict], sport_filter: Optional[str] = None) -> Optional[dict]:
    """Build a teaser: move total or spread pick to higher-prob zone,
    pair with correlated leg to get even-money math."""
    # Find a total or spread pick to tease
    tease_candidates = [p for p in picks
                        if p['market'] in ('total', 'rl', 'spread')
                        and p['original_line'] is not None
                        and p['tier'] in ('PRIME', 'STRONG')
                        and (sport_filter is None or p['sport'] == sport_filter)]
    if not tease_candidates: return None

    # Take strongest teaser candidate
    tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
    tease_candidates.sort(key=lambda p: (tier_rank.get(p['tier'], 9), -p.get('conviction', 0)))
    tease_pick = tease_candidates[0]
    sport = tease_pick['sport']
    step = TEASER_STEP.get(sport, 1.5)

    # Compute teased line + odds
    orig_line = float(tease_pick['original_line'])
    orig_odds = tease_pick['original_odds']
    side = (tease_pick.get('side') or '').upper()
    market = tease_pick['market']
    # Which direction is favorable?
    if market == 'total':
        # UNDER favors LOWER line, OVER favors LOWER line? No — over benefits from
        # LOWER line too... wait: teasing an OVER means bringing the line DOWN
        # (over 8.0 → over 6.5 is easier), UNDER means bringing UP (under 8.0
        # → under 9.5 is easier). Line move is toward the pick side.
        line_move = step
        if side == 'OVER': teased_line = orig_line - line_move
        else:               teased_line = orig_line + line_move
    elif market in ('rl', 'spread'):
        # RL fav (-1.5) teased to (+0.0 or +1.5) — line moves toward pick's advantage
        # Dog (+1.5) teased to (+3.0) — line moves toward more coverage
        line_move = step
        if orig_line < 0:  # favorite
            teased_line = orig_line + line_move
        else:              # dog
            teased_line = orig_line + line_move
    else:
        return None

    teased_odds = teaser_price(sport, market, orig_odds, line_move)

    # Pair with correlated leg: find another pick in the SAME sport at reasonable odds
    pair_candidates = [p for p in picks
                       if p['sport'] == sport and p['game_id'] != tease_pick['game_id']
                       and p['tier'] in ('PRIME', 'STRONG')
                       and p['original_odds'] is not None
                       and -200 <= float(p['original_odds']) <= 150]
    if not pair_candidates: return None
    pair_candidates.sort(key=lambda p: (tier_rank.get(p['tier'], 9), -p.get('conviction', 0)))
    pair = pair_candidates[0]

    legs_out = [
        {
            'sport': sport, 'game_id': tease_pick.get('game_id'),
            'matchup': tease_pick['matchup'], 'market': market,
            'pick': tease_pick['pick'], 'original_odds': orig_odds,
            'original_line': orig_line, 'teased_line': teased_line,
            'teased_odds': teased_odds, 'tier': tease_pick['tier'],
            'conviction': tease_pick.get('conviction'),
        },
        {
            'sport': sport, 'game_id': pair.get('game_id'),
            'matchup': pair['matchup'], 'market': pair['market'],
            'pick': pair['pick'], 'original_odds': pair['original_odds'],
            'original_line': pair['original_line'],
            'teased_line': None, 'teased_odds': None,
            'tier': pair['tier'], 'conviction': pair.get('conviction'),
        },
    ]
    combined = combined_american_odds([teased_odds, pair['original_odds']])

    return {
        'kind': 'teaser',
        'sport_scope': sport,
        'legs': legs_out,
        'combined_odds': combined,
        'reasoning': f'Teaser: {tease_pick["pick"]} → {teased_line} (higher hit %). '
                     f'Paired with {pair["pick"]} for {"+" if combined>0 else ""}{combined} even-money math.',
    }


# ═══════════════════════════════════════════════════════════════════════
# WRITE + RUN
# ═══════════════════════════════════════════════════════════════════════

def clear_todays(game_date: str) -> None:
    """Delete today's auto-generated suggestions so re-runs replace cleanly."""
    try:
        requests.delete(f'{SB}/rest/v1/ledger_suggestions'
                        f'?game_date=eq.{game_date}&auto_generated=eq.true',
                        headers={**H_WRITE}, timeout=10)
    except Exception: pass


def write_suggestion(sugg: dict, game_date: str, rank: int, dry_run: bool = False) -> bool:
    payload = {
        'game_date': game_date,
        'kind': sugg['kind'],
        'sport_scope': sugg['sport_scope'],
        'legs': sugg['legs'],
        'combined_odds': sugg['combined_odds'],
        'reasoning': sugg.get('reasoning', ''),
        'rank': rank,
        'auto_generated': True,
    }
    if dry_run: return True
    try:
        r = requests.post(f'{SB}/rest/v1/ledger_suggestions', headers=H_WRITE, json=payload, timeout=10)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def _et_today() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def run(game_date: Optional[str] = None, sports: Optional[list[str]] = None, dry_run: bool = False):
    gd = game_date or _et_today()
    sports = sports or list(CTX_TABLE.keys())
    print(f'=== generate_ledger · {gd} · {"/".join(sports)}{" [DRY]" if dry_run else ""} ===\n')

    picks = fetch_picks(gd, sports)
    chalk_pool = fetch_chalk_candidates(gd, sports)
    # Union: ensemble picks (for teasers + non-chalk) + chalk candidates
    # (for chalk trio when ensemble didn't back the fav side).
    combined_pool = picks + chalk_pool
    print(f'  fetched {len(picks)} ensemble picks + {len(chalk_pool)} chalk candidates')
    if not combined_pool:
        print('  no picks to build combos from — skipping')
        return

    if not dry_run:
        clear_todays(gd)

    suggestions = []
    # Track games used across suggestions so Reds ML doesn't appear in
    # 3 different combos when the slate has other viable picks (user's
    # 8/18 feedback: "two teaser options both on Reds when idk if Reds
    # was that high confident").
    used_games: set = set()

    def register(sugg):
        for l in sugg.get('legs', []):
            gid = l.get('game_id')
            if gid: used_games.add(gid)

    # 1. Chalk trio — 2-3 heavy favorites, combined near even money.
    # THIS IS THE FLAGSHIP LEDGER PRODUCT per 2026-08-18 user redesign.
    chalk = build_chalk_parlay(combined_pool, exclude_games=used_games)
    if chalk:
        suggestions.append(chalk); register(chalk)
        print(f'  ✓ CHALK TRIO: {chalk["combined_odds"]:+d} · {len(chalk["legs"])} legs')

    # 2. Teased totals combo — pair two PRIME/STRONG totals, each teased
    # 1.5-2 runs to easier zone. High-EV per user vision: "Red Sox game
    # teased down to O 6.5 or 5.5 with another total we liked teased."
    teased_totals = build_teased_totals_combo(picks, exclude_games=used_games)
    if teased_totals:
        suggestions.append(teased_totals); register(teased_totals)
        print(f'  ✓ TEASED TOTALS COMBO: {teased_totals["combined_odds"]:+d}')

    # 3. Teased spreads combo — 2026-08-18 per user: "Could also tease
    # spreads across sports like Reds +2.5." Mirror of totals combo for
    # RL/spread picks (MLB RL, NFL/NCAAF/NCAAB spreads).
    teased_spreads = build_teased_spreads_combo(picks, exclude_games=used_games)
    if teased_spreads:
        suggestions.append(teased_spreads); register(teased_spreads)
        print(f'  ✓ TEASED SPREADS COMBO: {teased_spreads["combined_odds"]:+d}')

    # 3. Single-sport teaser (spread/total tease + strong pair leg). Kept
    # as fallback when neither trio nor totals-combo composes.
    mlb_teaser = build_teaser(picks, sport_filter='MLB')
    if mlb_teaser and all(l.get('game_id') not in used_games for l in mlb_teaser['legs']):
        suggestions.append(mlb_teaser); register(mlb_teaser)
        print(f'  ✓ MLB TEASER: {mlb_teaser["combined_odds"]:+d} · '
              f'{mlb_teaser["legs"][0]["pick"]} → {mlb_teaser["legs"][0]["teased_line"]}')

    nfl_teaser = build_teaser(picks, sport_filter='NFL')
    if nfl_teaser and all(l.get('game_id') not in used_games for l in nfl_teaser['legs']):
        suggestions.append(nfl_teaser); register(nfl_teaser)
        print(f'  ✓ NFL TEASER: {nfl_teaser["combined_odds"]:+d}')

    ncaaf_teaser = build_teaser(picks, sport_filter='NCAAF')
    if ncaaf_teaser and all(l.get('game_id') not in used_games for l in ncaaf_teaser['legs']):
        suggestions.append(ncaaf_teaser); register(ncaaf_teaser)
        print(f'  ✓ NCAAF TEASER: {ncaaf_teaser["combined_odds"]:+d}')

    ncaab_teaser = build_teaser(picks, sport_filter='NCAAB')
    if ncaab_teaser and all(l.get('game_id') not in used_games for l in ncaab_teaser['legs']):
        suggestions.append(ncaab_teaser); register(ncaab_teaser)
        print(f'  ✓ NCAAB TEASER: {ncaab_teaser["combined_odds"]:+d}')

    written = 0
    for i, sugg in enumerate(suggestions, 1):
        if write_suggestion(sugg, gd, rank=i, dry_run=dry_run):
            written += 1

    print(f'\n  {"[DRY] " if dry_run else ""}wrote {written} suggestions')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='YYYY-MM-DD (default: today ET)')
    p.add_argument('--sport', help='Restrict to one sport (default: all)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    sports = [args.sport] if args.sport else None
    run(game_date=args.date, sports=sports, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
