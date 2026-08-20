"""Primary-play conflict filter (2026-08-19).

Shared utility for surfaces that generate their own leg/pick recommendations
(Dawg of the Day, Daily Degen, Ladder-adjacent). These surfaces historically
used LEGACY signals (spread_delta, confluence_net, model_pred_total) and
picked without consulting the ensemble-v2 primary_play decision.

Problem: Sharp Card / primary_play is the AUTHORITATIVE per-game pick — it
composes sharp-fade, source calibration, refit, juice-trap, everything. When
a downstream surface picks the OPPOSITE side of a STRONG/PRIME primary_play,
users see contradictions ("Sharp Card says Red Sox STRONG, but the Ledger
parlay includes Diamondbacks +1.5" — same game, opposite side).

This module provides a single check: given a candidate pick side + the game
context row, return True if the candidate CONFLICTS with a STRONG+ primary
play. Callers should drop conflicting candidates from their pool.

Design:
  * Only blocks when primary_play.tier ∈ {'PRIME', 'STRONG'} — LEAN doesn't
    have enough authority to veto a downstream surface's independent read.
  * Blocks when the candidate market is the SAME (ml vs ml, total vs total)
    but the SIDE differs.
  * Does NOT block cross-market picks (a total leg in a game where the
    primary is ML is fine — they're independent decisions).
  * Fails OPEN: if primary_play is missing/unparseable, no block.
"""
from typing import Optional


def _team_in(label: str, team: str) -> bool:
    if not label or not team: return False
    return team.lower() in label.lower()


def primary_play_conflicts(candidate_market: str,
                            candidate_side_label: str,
                            game_ctx_row: dict) -> Optional[str]:
    """Return a reason string if this candidate contradicts the game's
    STRONG+ primary_play, else None.

    Args:
        candidate_market: 'ml' | 'spread' | 'rl' | 'total' | 'over' | 'under'
        candidate_side_label: e.g. "Boston Red Sox ML" | "Over 8.5" | "Marlins +1.5"
        game_ctx_row: full mlb_game_context / *_game_context row
    """
    pp = (game_ctx_row or {}).get('primary_play') or {}
    tier = pp.get('tier')
    if tier not in ('PRIME', 'STRONG'):
        return None  # LEAN or missing — no veto authority

    pp_market = (pp.get('type') or '').lower()
    pp_label = (pp.get('label') or '')
    if not pp_market or not pp_label:
        return None  # unparseable — fail open

    cm = (candidate_market or '').lower()
    cl = (candidate_side_label or '')

    # Side markets — ml/spread/rl are all "team X wins/covers" flavors
    side_markets = {'ml', 'spread', 'rl'}
    if cm in side_markets and pp_market in side_markets:
        home_team = game_ctx_row.get('home_team') or ''
        away_team = game_ctx_row.get('away_team') or ''
        cand_home = _team_in(cl, home_team)
        cand_away = _team_in(cl, away_team)
        pp_home = _team_in(pp_label, home_team)
        pp_away = _team_in(pp_label, away_team)
        if cand_home and pp_away:
            return f'primary_play={tier} {pp_label} contradicts side pick'
        if cand_away and pp_home:
            return f'primary_play={tier} {pp_label} contradicts side pick'
        return None

    # Total markets — over vs under conflict, same-direction fine
    total_markets = {'total', 'over', 'under'}
    if cm in total_markets and pp_market in total_markets:
        cand_over = 'over' in cl.lower() or cm == 'over'
        cand_under = 'under' in cl.lower() or cm == 'under'
        pp_over = 'over' in pp_label.lower() or pp_market == 'over'
        pp_under = 'under' in pp_label.lower() or pp_market == 'under'
        if cand_over and pp_under:
            return f'primary_play={tier} {pp_label} contradicts total pick'
        if cand_under and pp_over:
            return f'primary_play={tier} {pp_label} contradicts total pick'
        return None

    # Cross-market — no conflict (ML pick + total pick are independent)
    return None


def primary_play_supports(candidate_market: str,
                           candidate_side_label: str,
                           game_ctx_row: dict) -> Optional[dict]:
    """Return the primary_play dict if it matches/supports this candidate
    (same side, tier ≥ STRONG), else None. Used to boost conviction when
    the ensemble agrees.
    """
    pp = (game_ctx_row or {}).get('primary_play') or {}
    tier = pp.get('tier')
    if tier not in ('PRIME', 'STRONG'):
        return None
    if primary_play_conflicts(candidate_market, candidate_side_label, game_ctx_row):
        return None  # explicit conflict trumps

    pp_market = (pp.get('type') or '').lower()
    pp_label = (pp.get('label') or '')
    cm = (candidate_market or '').lower()
    cl = (candidate_side_label or '')

    side_markets = {'ml', 'spread', 'rl'}
    total_markets = {'total', 'over', 'under'}
    if not ((cm in side_markets and pp_market in side_markets) or
            (cm in total_markets and pp_market in total_markets)):
        return None

    # Verify same-side
    home_team = game_ctx_row.get('home_team') or ''
    away_team = game_ctx_row.get('away_team') or ''
    if cm in side_markets:
        cand_home = _team_in(cl, home_team)
        pp_home = _team_in(pp_label, home_team)
        if cand_home != pp_home:
            return None
    elif cm in total_markets:
        cand_over = 'over' in cl.lower() or cm == 'over'
        pp_over = 'over' in pp_label.lower() or pp_market == 'over'
        if cand_over != pp_over:
            return None

    return pp
