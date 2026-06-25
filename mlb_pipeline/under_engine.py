"""Under Engine — explicit suppression-signal scoring (2026-06-25).

Composite (v3 + v4 + jerry avg) has documented OVER bias:
  - Calls OVER 72% of games vs actual 49% OVER rate
  - Composite UNDER picks hit 61% when it bothers to call them
  - Math UNDER picks hit 75% on small recent sample

The Under Engine captures conditions that suppress run scoring but get
under-weighted in the composite. Operates as a PARALLEL scorer, not a
filter on composite.

Scoring rubric (each signal adds points, total 0-110):
  Pitching quality:
    Both SPs xERA <= 3.5         +15
    Both SPs L3 ERA <= 3.5       +12
    SP K% sum >= 50              +10
    1st-inn ERA avg <= 3.0       +8
  Bullpens:
    Both BPs ERA <= 4.0          +8
    BPs rested (<=6 relievers L3d each)  +6
  Offense suppression:
    Both teams L7 OPS <= .680    +12
    At least one team L7 OPS <= .600  +8
    Both teams wRC+ <= 100       +5
  Park / environment:
    Pitcher-friendly park (PRF <= 96)  +8
    Cool weather (<65F)          +5
    Wind blowing IN              +6
  Defense:
    OAA sum >= 10                +4
    Both catcher framing > 0     +3

Tier mapping:
  ELITE UNDER: score >= 75
  STRONG UNDER: 60-74
  LEAN UNDER: 45-59
  NO SIGNAL: <45

Returns dataclass with score + tier + reasons for transparency.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class UnderVerdict:
    score: int
    tier: str  # 'ELITE' | 'STRONG' | 'LEAN' | 'NONE'
    reasons: List[str] = field(default_factory=list)


def _fnum(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def score_under(ctx: dict) -> UnderVerdict:
    """Score a game context for UNDER suppression conditions.

    ctx is a mlb_game_context row dict. Missing fields are handled
    gracefully — signal just doesn't fire.
    """
    score = 0
    reasons: List[str] = []

    # ── Pitching quality ──
    away_xera = _fnum(ctx.get('away_sp_xera'))
    home_xera = _fnum(ctx.get('home_sp_xera'))
    if away_xera is not None and home_xera is not None:
        if away_xera <= 3.5 and home_xera <= 3.5:
            score += 15
            reasons.append(f'Both SPs xERA<=3.5 (away {away_xera:.2f}, home {home_xera:.2f}) +15')

    away_l3 = _fnum(ctx.get('away_pitcher_last_3_era'))
    home_l3 = _fnum(ctx.get('home_pitcher_last_3_era'))
    if away_l3 is not None and home_l3 is not None:
        if away_l3 <= 3.5 and home_l3 <= 3.5:
            score += 12
            reasons.append(f'Both SPs L3 ERA<=3.5 (away {away_l3:.2f}, home {home_l3:.2f}) +12')

    away_k = _fnum(ctx.get('away_pitcher_last_3_k_pct')) or _fnum(ctx.get('away_sp_k_pct'))
    home_k = _fnum(ctx.get('home_pitcher_last_3_k_pct')) or _fnum(ctx.get('home_sp_k_pct'))
    if away_k is not None and home_k is not None:
        ksum = away_k + home_k
        if ksum >= 50:
            score += 10
            reasons.append(f'SP K% sum {ksum:.1f}>=50 +10')

    away_fi = _fnum(ctx.get('away_first_inning_era'))
    home_fi = _fnum(ctx.get('home_first_inning_era'))
    if away_fi is not None and home_fi is not None:
        if (away_fi + home_fi) / 2 <= 3.0:
            score += 8
            reasons.append(f'Avg 1st-inn ERA<=3.0 (a {away_fi:.1f}, h {home_fi:.1f}) +8')

    # ── Bullpens ──
    away_bp = _fnum(ctx.get('away_bullpen_era'))
    home_bp = _fnum(ctx.get('home_bullpen_era'))
    if away_bp is not None and home_bp is not None:
        if away_bp <= 4.0 and home_bp <= 4.0:
            score += 8
            reasons.append(f'Both BPs ERA<=4.0 (a {away_bp:.2f}, h {home_bp:.2f}) +8')

    away_rel = _fnum(ctx.get('away_bp_relievers_3d'))
    home_rel = _fnum(ctx.get('home_bp_relievers_3d'))
    if away_rel is not None and home_rel is not None:
        if away_rel <= 6 and home_rel <= 6:
            score += 6
            reasons.append(f'BPs rested (a {int(away_rel)}, h {int(home_rel)} relievers L3d) +6')

    # ── Offense suppression ──
    away_l7 = _fnum(ctx.get('away_ops_last7'))
    home_l7 = _fnum(ctx.get('home_ops_last7'))
    if away_l7 is not None and home_l7 is not None:
        if away_l7 <= 0.680 and home_l7 <= 0.680:
            score += 12
            reasons.append(f'Both teams L7 OPS<=.680 (a {away_l7:.3f}, h {home_l7:.3f}) +12')
        elif away_l7 <= 0.600 or home_l7 <= 0.600:
            score += 8
            reasons.append(f'One team L7 OPS<=.600 (a {away_l7:.3f}, h {home_l7:.3f}) +8')

    away_wrc = _fnum(ctx.get('away_wrc_plus'))
    home_wrc = _fnum(ctx.get('home_wrc_plus'))
    if away_wrc is not None and home_wrc is not None:
        if away_wrc <= 100 and home_wrc <= 100:
            score += 5
            reasons.append(f'Both teams wRC+<=100 (a {int(away_wrc)}, h {int(home_wrc)}) +5')

    # ── Park / environment ──
    park = _fnum(ctx.get('park_run_factor'))
    if park is not None and park <= 96:
        score += 8
        reasons.append(f'Pitcher-friendly park (PRF {int(park)}) +8')

    temp = _fnum(ctx.get('temperature'))
    if temp is not None and temp < 65:
        score += 5
        reasons.append(f'Cool weather ({temp:.0f}F) +5')

    wind_in = ctx.get('wind_blowing_in')
    if wind_in is True or wind_in == 't' or wind_in == 1:
        score += 6
        reasons.append('Wind blowing IN +6')

    # ── Defense ──
    away_oaa = _fnum(ctx.get('away_team_oaa'))
    home_oaa = _fnum(ctx.get('home_team_oaa'))
    if away_oaa is not None and home_oaa is not None:
        if away_oaa + home_oaa >= 10:
            score += 4
            reasons.append(f'OAA sum>=10 (a {int(away_oaa)}, h {int(home_oaa)}) +4')

    away_cf = _fnum(ctx.get('away_catcher_framing'))
    home_cf = _fnum(ctx.get('home_catcher_framing'))
    if away_cf is not None and home_cf is not None:
        if away_cf > 0 and home_cf > 0:
            score += 3
            reasons.append(f'Both C framing positive (a {away_cf:.1f}, h {home_cf:.1f}) +3')

    # Tier mapping (recalibrated 2026-06-25 after 948-game backtest)
    # Original thresholds were too high — only 3 games in 83 days at STRONG+.
    # LEAN tier (45-59) hit 61.4% UNDER on n=44 — that's the real signal band.
    # Re-mapping moves the proven signal to STRONG and adds a LEAN bucket
    # at 30-44 for "suppression conditions present but not loud".
    if score >= 60:
        tier = 'ELITE'
    elif score >= 45:
        tier = 'STRONG'
    elif score >= 30:
        tier = 'LEAN'
    else:
        tier = 'NONE'

    return UnderVerdict(score=score, tier=tier, reasons=reasons)


if __name__ == '__main__':
    # Self-test: simulate a known UNDER setup (NYY @ DET 6/24 in retrospect)
    test_ctx = {
        'away_sp_xera': 4.19, 'home_sp_xera': 3.62,
        'away_pitcher_last_3_era': 6.48, 'home_pitcher_last_3_era': 3.63,
        'away_pitcher_last_3_k_pct': 20.6, 'home_pitcher_last_3_k_pct': 27.1,
        'away_first_inning_era': 7.07, 'home_first_inning_era': 4.0,
        'away_bullpen_era': 3.37, 'home_bullpen_era': 3.83,
        'away_bp_relievers_3d': 10, 'home_bp_relievers_3d': 9,
        'away_ops_last7': 0.733, 'home_ops_last7': 0.705,
        'away_wrc_plus': 118, 'home_wrc_plus': 103,
        'park_run_factor': 93, 'temperature': 76,
        'away_team_oaa': -2, 'home_team_oaa': -17,
    }
    v = score_under(test_ctx)
    print(f'NYY @ DET 6/24 — Under score: {v.score} ({v.tier})')
    for r in v.reasons:
        print(f'  {r}')
