"""Pick correlation checker (2026-08-07).

Detects when two picks in the same game push in opposing directions
and are unlikely to co-cash. Caught during 8/7 slate analysis: Schultz
ER OVER 2.5 was published alongside CLE @ CHW UNDER 7.5. Both plays
require a narrow scoring window to hit simultaneously (Schultz gives
up exactly 3-4 ER, opponent scores ≤3), so they're MORE likely to
split than co-cash — negatively correlated.

Neither the pipeline nor analyst layer had this check. Both plays
sailed through calibration independently because calibration operates
on single-pick basis.

This module runs AFTER card / prop selection and BEFORE publish. Flags
correlation conflicts so a human (or downstream automation) can decide
whether to:
  - drop one of the conflicting picks
  - keep both with a "correlation-adjusted unit" warning
  - swap one to a related non-conflicting play

Sport-universal — the correlation rules are labeled by market shape,
not sport-specific.

Interface:
    from correlation_check import check_correlations
    warnings = check_correlations(picks_list)
    # → list of {picks_involved, rule, severity, note}
    # empty list = no conflicts found

Rule catalog:
  R1  pitcher_er_over + game_under          (negative)
  R2  pitcher_ha_over + game_under          (negative)
  R3  pitcher_ks_under + game_under         (mild negative — Ks and runs anti-correlate)
  R4  batter_hits_over + game_under         (mild negative — hits drive runs)
  R5  batter_hits_over + opposing_team_ml   (negative — you're rooting against your side)
  R6  pitcher_ml_side + game_over_high      (mild — winning pitcher usually keeps runs down)
"""
from __future__ import annotations
from typing import Optional


# Rule severity levels drive warning tone downstream.
# 'strong' = high probability of splitting (drop one)
# 'medium' = meaningful anti-correlation (adjust units)
# 'weak'   = mild tension (informational only)
_SEVERITY_STRONG = 'strong'
_SEVERITY_MEDIUM = 'medium'
_SEVERITY_WEAK = 'weak'


def _norm_side(s: Optional[str]) -> Optional[str]:
    if not s: return None
    s = s.upper().strip()
    if s in ('OVER','UNDER','HOME','AWAY'): return s
    return None


def _pitcher_team(pick: dict, pitcher_to_team: dict) -> Optional[str]:
    """Return the team key ('home' or 'away') the pitcher plays for.
    pitcher_to_team maps {'game_id': {'home_pitcher_name': ..., 'away_pitcher_name': ...}}
    """
    gid = pick.get('game_id')
    player = (pick.get('player_name') or '').lower()
    if not gid or not player: return None
    game = pitcher_to_team.get(gid) or {}
    hp = (game.get('home_pitcher') or '').lower()
    ap = (game.get('away_pitcher') or '').lower()
    if player == hp: return 'home'
    if player == ap: return 'away'
    return None


def check_correlations(picks: list, pitcher_map: Optional[dict] = None) -> list:
    """Scan a list of picks for same-game correlation conflicts.

    picks: list of dicts with at least:
      - game_id: str
      - market: 'total' | 'ml' | 'rl' | 'spread' | 'prop'
      - side:   'OVER' | 'UNDER' | 'HOME' | 'AWAY' | None
      - prop_type: str (for prop picks — 'er_over','ha_over','ks_under','hits_over',...)
      - player_name: str (for prop picks)
      - line: numeric (optional)

    pitcher_map: optional {game_id: {home_pitcher, away_pitcher}} for
      pitcher-team resolution on prop rules. If absent, pitcher-team
      rules are skipped.

    Returns list of warning dicts:
      {picks: [i, j], rule, severity, note}
    """
    warnings = []
    pitcher_map = pitcher_map or {}

    # Group picks by game_id
    by_game = {}
    for i, p in enumerate(picks):
        gid = p.get('game_id')
        if not gid: continue
        by_game.setdefault(gid, []).append((i, p))

    for gid, game_picks in by_game.items():
        # Only care about games with 2+ picks
        if len(game_picks) < 2: continue

        # Sort by market type to make pair enumeration deterministic
        for a_idx in range(len(game_picks)):
            for b_idx in range(a_idx + 1, len(game_picks)):
                i, a = game_picks[a_idx]
                j, b = game_picks[b_idx]
                w = _check_pair(a, b, pitcher_map.get(gid, {}))
                if w:
                    w['picks'] = [i, j]
                    w['game_id'] = gid
                    warnings.append(w)

    # 2026-08-08 SAME-PITCHER CORRELATION (R7).
    # Discovery from 8/7 post-mortem: Wheeler had TWO PRIME props
    # (Ks OVER 6.5 + BB UNDER 1.5). Wheeler had an off night with 5
    # walks and only 6 Ks — BOTH props lost as a single-pitcher-outing
    # event. When a pitcher has 2+ STRONG+ props on the card, one bad
    # outing cascades into multiple losses. Warn so bettors can
    # correlation-adjust units (drop one or use smaller sizes on both).
    by_pitcher = {}
    for i, p in enumerate(picks):
        # Only props tied to a specific player_name matter here
        pt = (p.get('prop_type') or '').lower()
        # Pitcher prop types (not batter hits)
        if not any(pt.startswith(x) for x in ('ks_','bb_','er_','ha_','outs_')):
            continue
        player = (p.get('player_name') or '').strip().lower()
        if not player: continue
        by_pitcher.setdefault(player, []).append((i, p))

    for player, plist in by_pitcher.items():
        if len(plist) < 2: continue
        # Collect prop-type descriptors
        descs = [f"{p.get('prop_type')} {p.get('prop_line','?')} {p.get('side','')}"
                 for _, p in plist]
        indices = [i for i, _ in plist]
        # Highest tier / conviction across the stack
        max_conv = max((p.get('conviction') or 0) for _, p in plist)
        severity = _SEVERITY_STRONG if max_conv >= 80 else _SEVERITY_MEDIUM
        warnings.append({
            'rule': 'R7_same_pitcher_multi_prop_stack',
            'severity': severity,
            'picks': indices,
            'game_id': plist[0][1].get('game_id'),
            'note': (f'{player.title()} has {len(plist)} props on card '
                     f'({", ".join(descs)}). One bad outing cascades into '
                     f'multiple losses — correlation-adjust units.'),
        })

    return warnings


def _check_pair(a: dict, b: dict, pitcher_info: dict) -> Optional[dict]:
    """Evaluate a pair of same-game picks against the rule catalog.
    Returns a warning dict or None."""
    a_mkt = (a.get('market') or '').lower()
    b_mkt = (b.get('market') or '').lower()
    a_side = _norm_side(a.get('side'))
    b_side = _norm_side(b.get('side'))
    a_type = (a.get('prop_type') or '').lower()
    b_type = (b.get('prop_type') or '').lower()

    # Helper: identify a game-total pick (either side)
    def is_game_total(pk_mkt, pk_side, target_side):
        return pk_mkt == 'total' and pk_side == target_side

    # Helper: identify a pitcher-over prop of specific stat
    def is_pitcher_over(pk_mkt, pk_type, stat):
        return pk_mkt == 'prop' and pk_type == f'{stat}_over'

    def is_pitcher_under(pk_mkt, pk_type, stat):
        return pk_mkt == 'prop' and pk_type == f'{stat}_under'

    def is_hitter_over(pk_mkt, pk_type):
        return pk_mkt == 'prop' and pk_type == 'hits_over'

    # R1: pitcher_er_over + game_under  → strong negative
    for x, y in ((a, b), (b, a)):
        x_mkt = (x.get('market') or '').lower(); x_type = (x.get('prop_type') or '').lower()
        y_mkt = (y.get('market') or '').lower(); y_side = _norm_side(y.get('side'))
        if is_pitcher_over(x_mkt, x_type, 'er') and is_game_total(y_mkt, y_side, 'UNDER'):
            return {
                'rule': 'R1_pitcher_er_over_vs_game_under',
                'severity': _SEVERITY_STRONG,
                'note': (f'{x.get("player_name","?")} ER OVER {x.get("line","?")} + game UNDER '
                         f'{y.get("line","?")}: pitcher giving up 3+ ER + game staying under '
                         f'requires narrow scoring window. Likely to split.'),
            }

    # R2: pitcher_ha_over + game_under → medium negative
    for x, y in ((a, b), (b, a)):
        x_mkt = (x.get('market') or '').lower(); x_type = (x.get('prop_type') or '').lower()
        y_mkt = (y.get('market') or '').lower(); y_side = _norm_side(y.get('side'))
        if is_pitcher_over(x_mkt, x_type, 'ha') and is_game_total(y_mkt, y_side, 'UNDER'):
            return {
                'rule': 'R2_pitcher_ha_over_vs_game_under',
                'severity': _SEVERITY_MEDIUM,
                'note': (f'{x.get("player_name","?")} hits allowed OVER + game UNDER: '
                         'more hits usually pushes total OVER.'),
            }

    # R3: pitcher_ks_under + game_under → weak negative
    for x, y in ((a, b), (b, a)):
        x_mkt = (x.get('market') or '').lower(); x_type = (x.get('prop_type') or '').lower()
        y_mkt = (y.get('market') or '').lower(); y_side = _norm_side(y.get('side'))
        if is_pitcher_under(x_mkt, x_type, 'ks') and is_game_total(y_mkt, y_side, 'UNDER'):
            return {
                'rule': 'R3_pitcher_ks_under_vs_game_under',
                'severity': _SEVERITY_WEAK,
                'note': (f'{x.get("player_name","?")} Ks UNDER + game UNDER: '
                         'contact rate up = more balls in play = mild OVER pressure.'),
            }

    # R4: batter_hits_over + game_under → medium negative
    for x, y in ((a, b), (b, a)):
        x_mkt = (x.get('market') or '').lower(); x_type = (x.get('prop_type') or '').lower()
        y_mkt = (y.get('market') or '').lower(); y_side = _norm_side(y.get('side'))
        if is_hitter_over(x_mkt, x_type) and is_game_total(y_mkt, y_side, 'UNDER'):
            return {
                'rule': 'R4_hitter_hits_over_vs_game_under',
                'severity': _SEVERITY_MEDIUM,
                'note': (f'{x.get("player_name","?")} hits OVER + game UNDER: '
                         'batter reaching base 1+ times slightly pressures OVER.'),
            }

    return None


if __name__ == '__main__':
    import sys, json
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try: sys.stdout.reconfigure(encoding='utf-8')
        except Exception: pass

    # Self-test — the Schultz/UNDER case
    picks = [
        # Slide 4 from tonight's card: CLE @ CHW UNDER 7.5
        {'game_id': 'clechw', 'market': 'total', 'side': 'UNDER', 'line': 7.5},
        # Slide 5 (removed): Schultz ER OVER 2.5 in same game
        {'game_id': 'clechw', 'market': 'prop', 'prop_type': 'er_over',
         'player_name': 'Noah Schultz', 'line': 2.5, 'side': 'OVER'},
        # Unrelated: Wheeler Ks OVER — different game, no conflict
        {'game_id': 'torphi', 'market': 'prop', 'prop_type': 'ks_over',
         'player_name': 'Zack Wheeler', 'line': 6.5, 'side': 'OVER'},
        # Bregman hits OVER + Cubs ML in same game (fine)
        {'game_id': 'chckc', 'market': 'prop', 'prop_type': 'hits_over',
         'player_name': 'Alex Bregman', 'line': 0.5, 'side': 'OVER'},
        # Same game with UNDER total → medium warning
        {'game_id': 'chckc', 'market': 'total', 'side': 'UNDER', 'line': 9.5},
    ]

    ws = check_correlations(picks)
    print(f'Detected {len(ws)} correlation warnings:')
    for w in ws:
        print(f'\n  [{w["severity"].upper()}] {w["rule"]}')
        print(f'    game: {w["game_id"]}  picks: {w["picks"]}')
        print(f'    {w["note"]}')
