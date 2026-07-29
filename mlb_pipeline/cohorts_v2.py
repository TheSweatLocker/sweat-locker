"""MLB cohort family v2 — 7 signals, backtestable from mlb_game_results.

Each cohort function takes a game context dict (fields from mlb_game_results
or mlb_game_context) + optional prior_day_map (game_date → team → prior game result).
Returns {'side': 'home'|'away'|None, 'triggered': bool, 'note': str}.

v2 is a SHADOW family — runs parallel to v1 (signal_confluence_breakdown) so
we can compare over 4 weeks before promoting to primary_play tier logic.

Cohorts:
  west_east_early       West team early ET after prior night game
  cold_after_blowout    9+ runs prior day + weak xwOBA baseline → under bounceback
  series_letdown        Game 3 of series after winning first 2 → drop
  fresh_vs_grind        Off-day vs 3-in-3 fatigue mismatch
  ace_hot_offense       Top-quartile pitcher + hot lineup → win
  starter_rest_extreme  SP on 2-3d fatigue or 7+d rust → fade
  bp_fatigue_combo      3+ IP pen last 3d vs high-ERA opposing SP → fade taxed side
"""
from typing import Optional


# ── Team timezone map (offset from ET at typical local time, static) ──
# Used by west_east_early. West = PDT (-3 from ET), Mountain = -2 (Denver only)
WEST_COAST_TEAMS = {
    'Seattle Mariners', 'Oakland Athletics', 'Athletics',
    'Los Angeles Angels', 'Los Angeles Dodgers',
    'San Francisco Giants', 'San Diego Padres',
    'Colorado Rockies',  # mountain, but same fatigue pattern
    'Arizona Diamondbacks',  # PST no DST
}


def _f(v) -> Optional[float]:
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _empty(reason: str = '') -> dict:
    return {'side': None, 'triggered': False, 'note': reason or 'inputs missing'}


# ────────────────────────────────────────────────────────────────
# 1. WEST-TO-EAST EARLY GAME
#   West Coast team playing 1-3pm ET after prior night game.
#   Historical MLB betting-lit: measurable under + slight fade of the west-coast side.
def cohort_west_east_early(ctx: dict, prior_day_map: dict = None) -> dict:
    tz_change = _f(ctx.get('timezone_change'))
    game_time = ctx.get('game_time') or ''
    away = ctx.get('away_team') or ''
    home = ctx.get('home_team') or ''
    # Determine which side has traveled from west to east
    west_side = None
    if away in WEST_COAST_TEAMS and home not in WEST_COAST_TEAMS:
        west_side = 'away'
    elif home in WEST_COAST_TEAMS and away not in WEST_COAST_TEAMS:
        # Home team returning to west from east — not the target pattern; skip
        return _empty('west team is home, not applicable')
    else:
        return _empty('no west-vs-east matchup')

    # Early ET start check (before 3pm ET) — parse game_time if available
    # Fallback to timezone_change ≥ 2 as proxy for west→east travel
    if tz_change is not None and tz_change >= 2:
        # Fade the traveling side
        return {'side': 'home' if west_side == 'away' else 'away',
                'triggered': True,
                'note': f'west→east travel (tz_change {tz_change:.0f}) → fade {west_side}'}
    return _empty('travel not severe enough')


# ────────────────────────────────────────────────────────────────
# 2. COLD OFFENSE AFTER BLOWOUT (refined 7/29)
#   Team scored 12+ (was 9+) prior day AND below-avg xwOBA rolling <.310 (was .320)
#   → tighter regression bet. Backtest at 9+/.320 was 49% (coinflip); try
#   rarer/stronger threshold.
def cohort_cold_after_blowout(ctx: dict, prior_day_map: dict = None) -> dict:
    if not prior_day_map:
        return _empty('no prior-day data')
    away = ctx.get('away_team'); home = ctx.get('home_team')
    game_date = ctx.get('game_date')
    away_prior = prior_day_map.get((game_date, away))
    home_prior = prior_day_map.get((game_date, home))

    away_wob = _f(ctx.get('away_team_xwoba'))
    home_wob = _f(ctx.get('home_team_xwoba'))

    BLOWOUT_RUNS = 12
    COLD_XWOBA = 0.310

    fade_sides = []
    if (away_prior and (away_prior.get('runs_scored') or 0) >= BLOWOUT_RUNS
            and away_wob is not None and away_wob < COLD_XWOBA):
        fade_sides.append('away')
    if (home_prior and (home_prior.get('runs_scored') or 0) >= BLOWOUT_RUNS
            and home_wob is not None and home_wob < COLD_XWOBA):
        fade_sides.append('home')

    if not fade_sides:
        return _empty('no blowout+cold combo')
    fade = fade_sides[0]
    side = 'home' if fade == 'away' else 'away'
    return {'side': side, 'triggered': True,
            'note': f'{fade} scored 12+ prior day w/ xwOBA <.310 → fade'}


# ────────────────────────────────────────────────────────────────
# 3. SERIES LETDOWN (refined 7/29)
#   Game 3+ of series where one side is on a 2-game win streak in this same series
#   → fade that side (letdown risk).
#   Prior fix: verify prior game was between the SAME two teams via opponent tag.
def cohort_series_letdown(ctx: dict, prior_day_map: dict = None) -> dict:
    sgn = _f(ctx.get('series_game_number'))
    if sgn is None or sgn < 3:
        return _empty(f'game {sgn} in series, need 3')
    if not prior_day_map:
        return _empty('no prior-day data')
    away = ctx.get('away_team'); home = ctx.get('home_team')
    game_date = ctx.get('game_date')
    away_prior = prior_day_map.get((game_date, away))
    home_prior = prior_day_map.get((game_date, home))
    if not away_prior or not home_prior:
        return _empty('missing prior')
    # Same-series check: both teams' prior game opponent must be each other.
    if away_prior.get('opponent') != home or home_prior.get('opponent') != away:
        return _empty('prior game not vs same opp')
    if away_prior.get('won') and (home_prior.get('won') is False):
        # Away won prior game → attempting to sweep game 3 → fade AWAY
        return {'side': 'home', 'triggered': True,
                'note': 'away won last vs same opp, letdown → fade sweep attempt'}
    if home_prior.get('won') and (away_prior.get('won') is False):
        return {'side': 'away', 'triggered': True,
                'note': 'home won last vs same opp, letdown → fade sweep attempt'}
    return _empty('no clear letdown side')


# ────────────────────────────────────────────────────────────────
# 4. FRESH vs GRIND (fixed 7/29)
#   Team with off-day yesterday vs team on 2+ games in the last 3 days.
#   Prior fix: prior_day_map only ever has played=True entries. Off-day detection
#   must check WAS-THERE-AN-ENTRY-YESTERDAY, not the 'played' key.
#   games_last_3 is counted BEFORE the current game so max = 2 (yesterday +
#   day-before). Threshold >=2 means "team played both of last 2 days" ≈ grind.
def cohort_fresh_vs_grind(ctx: dict, prior_day_map: dict = None) -> dict:
    if not prior_day_map:
        return _empty('no prior-day data')
    away = ctx.get('away_team'); home = ctx.get('home_team')
    game_date = ctx.get('game_date')
    away_prior = prior_day_map.get((game_date, away))
    home_prior = prior_day_map.get((game_date, home))

    # Off-day yesterday = no prior_day entry for that team (means it was skipped
    # because they didn't play). But prior entry ALSO doesn't mean yesterday
    # specifically — it means their MOST RECENT prior game. So we need to check
    # the date on the prior entry.
    from datetime import datetime, timedelta
    try:
        gd = datetime.strptime(game_date, '%Y-%m-%d')
        yesterday = (gd - timedelta(days=1)).strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return _empty('bad game_date')

    away_played_yest = away_prior is not None and away_prior.get('date') == yesterday
    home_played_yest = home_prior is not None and home_prior.get('date') == yesterday
    away_off = not away_played_yest
    home_off = not home_played_yest

    away_streak = _f((away_prior or {}).get('games_last_3')) or 0
    home_streak = _f((home_prior or {}).get('games_last_3')) or 0

    if away_off and home_streak >= 2:
        return {'side': 'away', 'triggered': True,
                'note': f'away fresh (off yesterday), home on {int(home_streak)+1}-in-3'}
    if home_off and away_streak >= 2:
        return {'side': 'home', 'triggered': True,
                'note': f'home fresh (off yesterday), away on {int(away_streak)+1}-in-3'}
    return _empty('no mismatch')


# ────────────────────────────────────────────────────────────────
# 5. ACE + HOT OFFENSE
#   Top-quartile pitcher (xERA <= 3.30) + hot lineup (wrc_proxy_l14 > 105).
#   Signal: back the ace's team.
def cohort_ace_hot_offense(ctx: dict, prior_day_map: dict = None) -> dict:
    away_xera = _f(ctx.get('away_sp_xera'))
    home_xera = _f(ctx.get('home_sp_xera'))
    away_wrc = _f(ctx.get('away_wrc_proxy_l14'))
    home_wrc = _f(ctx.get('home_wrc_proxy_l14'))

    # Ace = top quartile (xERA <= 3.30 as proxy)
    ACE = 3.30
    HOT = 105  # wRC+ > 105 = above avg
    away_ace_hot = (away_xera is not None and away_xera <= ACE
                    and away_wrc is not None and away_wrc > HOT)
    home_ace_hot = (home_xera is not None and home_xera <= ACE
                    and home_wrc is not None and home_wrc > HOT)

    if away_ace_hot and not home_ace_hot:
        return {'side': 'away', 'triggered': True,
                'note': f'AWAY ace {away_xera:.2f} xERA + hot bat wRC+ {away_wrc:.0f}'}
    if home_ace_hot and not away_ace_hot:
        return {'side': 'home', 'triggered': True,
                'note': f'HOME ace {home_xera:.2f} xERA + hot bat wRC+ {home_wrc:.0f}'}
    return _empty('no ace+hot combo')


# ────────────────────────────────────────────────────────────────
# 6. STARTER REST EXTREME — INVERTED 7/29
#   Backtest at n=343 showed 45.2% on fade-the-extreme-rest logic. Inverting
#   converts to 54.8%. So the pattern is actually: extreme rest ≠ liability
#   (probably because it correlates with premium/veteran arms rested to full).
#   New logic: BACK the extreme-rest starter's side (don't fade it).
def cohort_starter_rest_extreme(ctx: dict, prior_day_map: dict = None) -> dict:
    away_rest = _f(ctx.get('away_sp_days_rest'))
    home_rest = _f(ctx.get('home_sp_days_rest'))

    def _extreme(rest):
        return rest is not None and (rest <= 3 or rest >= 7)

    away_ext = _extreme(away_rest)
    home_ext = _extreme(home_rest)

    if away_ext and not home_ext:
        return {'side': 'away', 'triggered': True,
                'note': f'away SP on extreme rest {away_rest:.0f}d → back away (inverted 7/29 from 45%)'}
    if home_ext and not away_ext:
        return {'side': 'home', 'triggered': True,
                'note': f'home SP on extreme rest {home_rest:.0f}d → back home (inverted 7/29 from 45%)'}
    return _empty('no extreme rest')


# ────────────────────────────────────────────────────────────────
# 7. BULLPEN FATIGUE COMBO (loosened 7/29)
#   Original combo (taxed pen + opposing high-ERA SP) never fired in backtest.
#   Loosened: fire when ONE side has taxed pen and OTHER side is fresh (≥2 gap).
#   Signal: fade the taxed side (back the fresh-pen side).
def cohort_bp_fatigue_combo(ctx: dict, prior_day_map: dict = None) -> dict:
    away_bp3d = _f(ctx.get('away_bp_relievers_3d'))
    home_bp3d = _f(ctx.get('home_bp_relievers_3d'))
    if away_bp3d is None or home_bp3d is None:
        return _empty('bp counts missing')

    # Taxed = ≥3 relievers used last 3d, AND gap of 2+ vs opposing pen
    away_taxed_fresh_opp = away_bp3d >= 3 and (away_bp3d - home_bp3d) >= 2
    home_taxed_fresh_opp = home_bp3d >= 3 and (home_bp3d - away_bp3d) >= 2

    if away_taxed_fresh_opp:
        # Away pen taxed & home pen fresh → fade away
        return {'side': 'home', 'triggered': True,
                'note': f'AWAY pen taxed {away_bp3d:.0f} vs HOME pen fresh {home_bp3d:.0f}'}
    if home_taxed_fresh_opp:
        return {'side': 'away', 'triggered': True,
                'note': f'HOME pen taxed {home_bp3d:.0f} vs AWAY pen fresh {away_bp3d:.0f}'}
    return _empty('no clear pen mismatch')


# Backtest 2026-07-29:
#   cold_after_blowout DISABLED (n=14, 42.9% — too rare & weak signal)
#   series_letdown kept in fn registry but silent unless data is proper series-aware
COHORT_FUNCTIONS = {
    'west_east_early': cohort_west_east_early,          # 57.0% n=235 ⭐
    'ace_hot_offense': cohort_ace_hot_offense,          # 55.9% n=143 ✓
    'starter_rest_extreme': cohort_starter_rest_extreme,# 54.8% n=343 ✓ (inverted from 45.2%)
    'fresh_vs_grind': cohort_fresh_vs_grind,            # 53.3% n=152 ✓
    'bp_fatigue_combo': cohort_bp_fatigue_combo,        # 50.3% n=835 (marginal, stacking value)
    'series_letdown': cohort_series_letdown,            # 0 fires — defer debug
    # 'cold_after_blowout': DISABLED — 42.9% n=14
}


def compute_v2_breakdown(ctx: dict, prior_day_map: dict = None) -> tuple[dict, int]:
    """Run all v2 cohorts against a game context. Returns (breakdown_dict, net_int).

    breakdown_dict shape:
        {cohort_name: {"side": ..., "triggered": bool, "note": str}, ...,
         "not_fired": [names]}
    net_int = sum of +1(home) / -1(away) across fired cohorts
    """
    breakdown = {}
    not_fired = []
    net = 0
    for name, fn in COHORT_FUNCTIONS.items():
        try:
            r = fn(ctx, prior_day_map)
        except Exception as e:
            r = _empty(f'error: {type(e).__name__}')
        if r.get('triggered'):
            breakdown[name] = {'side': r['side'], 'note': r.get('note', '')}
            net += 1 if r['side'] == 'home' else -1
        else:
            not_fired.append(name)
    breakdown['not_fired'] = not_fired
    return breakdown, net
