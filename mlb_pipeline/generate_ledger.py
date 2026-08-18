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

def build_chalk_parlay(picks: list[dict], target_odds_range: tuple = (100, 200)) -> Optional[dict]:
    """Combine 2-3 ML favorites into a parlay landing in target_odds_range.

    Prefers highest-tier picks first. Skips longshots (+odds) and heavy
    chalk (-300+) since combining chalk gives bad math."""
    # Filter to ML picks only with reasonable odds (-250 to -110)
    candidates = [p for p in picks
                  if p['market'] == 'ml'
                  and p['original_odds'] is not None
                  and -250 <= float(p['original_odds']) <= -110
                  and p['tier'] in ('PRIME', 'STRONG')]
    # Sort by tier + conviction
    tier_rank = {'PRIME': 0, 'STRONG': 1, 'LEAN': 2}
    candidates.sort(key=lambda p: (tier_rank.get(p['tier'], 9), -p.get('conviction', 0)))

    # Try 3-leg first, then 2-leg
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
                'reasoning': f'{n_legs}-leg chalk: {" · ".join(l["pick"] for l in legs)}. '
                             f'Combined {"+" if combined > 0 else ""}{combined} — even-money adjacent.',
            }
    return None


def build_dog_parlay(picks: list[dict], target_odds_range: tuple = (250, 700)) -> Optional[dict]:
    """Combine 2-3 plus-money ML dogs into a parlay landing in target_odds_range.
    Complements build_chalk_parlay (which requires fav ML only). On dog-heavy
    slates (like 2026-08-18 where Twins+116, Reds+106, Rockies+172 all shipped
    STRONG), chalk parlays don't compose and Ledger was empty. Dog parlays fill
    the gap — smaller strike zone but higher payoff, tier-gated so we only
    combine ensemble-endorsed dogs."""
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
    print(f'  fetched {len(picks)} PRIME/STRONG/LEAN picks across sports')
    if not picks:
        print('  no picks to build combos from — skipping')
        return

    if not dry_run:
        clear_todays(gd)

    suggestions = []

    # 1. Chalk parlay (any sports, prefer mid-tier chalk) — only fires when
    # slate has 2-3 favorites at -110 to -250. On dog-heavy nights returns None.
    chalk = build_chalk_parlay(picks)
    if chalk:
        suggestions.append(chalk)
        print(f'  ✓ CHALK PARLAY: {chalk["combined_odds"]:+d} · {len(chalk["legs"])} legs')

    # 2. Dog parlay — 2-3 plus-money ML dogs from ensemble-endorsed picks.
    # Complement to chalk: when the slate skews to home dogs (like 2026-08-18
    # w/ Twins+116, Reds+106, Rockies+172 all STRONG), chalk parlays fail
    # to compose. Dog parlays fill the gap with smaller hit rate + higher payoff.
    dogs = build_dog_parlay(picks)
    if dogs:
        suggestions.append(dogs)
        print(f'  ✓ DOG PARLAY: +{dogs["combined_odds"]} · {len(dogs["legs"])} legs')

    # 3. Mixed parlay — filler for slates where neither chalk nor dog specific
    # parlays compose. Cross-market, cross-game (no 2 legs from same game).
    mixed = build_mixed_parlay(picks)
    if mixed:
        suggestions.append(mixed)
        print(f'  ✓ MIXED PARLAY: {mixed["combined_odds"]:+d} · {len(mixed["legs"])} legs')

    # 4. MLB teaser
    mlb_teaser = build_teaser(picks, sport_filter='MLB')
    if mlb_teaser:
        suggestions.append(mlb_teaser)
        print(f'  ✓ MLB TEASER: {mlb_teaser["combined_odds"]:+d} · '
              f'{mlb_teaser["legs"][0]["pick"]} → {mlb_teaser["legs"][0]["teased_line"]}')

    # 5. NFL teaser (when NFL running)
    nfl_teaser = build_teaser(picks, sport_filter='NFL')
    if nfl_teaser:
        suggestions.append(nfl_teaser)
        print(f'  ✓ NFL TEASER: {nfl_teaser["combined_odds"]:+d} · '
              f'{nfl_teaser["legs"][0]["pick"]} → {nfl_teaser["legs"][0]["teased_line"]}')

    # 6. NCAAF teaser
    ncaaf_teaser = build_teaser(picks, sport_filter='NCAAF')
    if ncaaf_teaser:
        suggestions.append(ncaaf_teaser)
        print(f'  ✓ NCAAF TEASER: {ncaaf_teaser["combined_odds"]:+d}')

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
