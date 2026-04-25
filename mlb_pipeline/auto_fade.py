"""
Auto-fade calibration — handles cohorts where the model is systematically wrong.

Three actions per cohort based on hit rate + sample size:
  - SURFACE  : pick is shown to users as model says (default)
  - SUPPRESS : pick is silently dropped (don't push losing picks to users)
  - FADE     : silently flip the displayed pick to the OPPOSITE side
              (we lean on the model's inverse correlation when sample warrants)

Calibration thresholds (conservative — avoid acting on noisy small-sample bias):
  FADE     when n >= 30 AND hit_rate <= 0.30
  SUPPRESS when n >= 15 AND hit_rate <= 0.30
  SURFACE  otherwise

Refresh monthly. As resolved sample grows, cohorts may move between buckets.

Cohort buckets currently tracked:
  ml_dog            — model picks ML underdog (corrected_delta sign disagrees w/ market)
  ml_dog_high_conv  — model picks ML dog with confluence_net >= 2 (extra unreliable)
  ml_chalk          — model agrees with market on ML direction
  ml_chalk_high_mag — model agrees with market AND corrected |delta| >= 1.5 (the gold)
"""

# Calibration table. 6 cohorts based on (ML direction agreement, RL direction agreement).
# Updated 2026-04-25 from April 1-23 backtest, n=56 games.
# Data is RL-based (we didn't have ML odds historically). The mixed cohorts
# (ml_fav_rl_dog, ml_dog_rl_fav) are NEW concepts with no calibration data —
# default SUPPRESS until we accumulate >=15 resolved picks per cohort.
CALIBRATION = {
    # CLEAN COHORTS (model agrees with both ML and RL, or disagrees with both)
    'ml_chalk_high_mag': {
        'n': 7, 'hit_rate': 0.857,
        'note': 'Model + ML + RL all agree, corrected |delta| >= 1.5 (Marlins-style). 85.7% Apr.',
    },
    'ml_chalk': {
        'n': 32, 'hit_rate': 0.594,
        'note': 'Model + market agree on direction (RL-based audit). 59.4% over April.',
    },
    'ml_dog': {
        'n': 24, 'hit_rate': 0.25,
        'note': 'Model picks against market (RL-based). 25% in April. Below break-even.',
    },
    'ml_dog_high_conv': {
        'n': 6, 'hit_rate': 0.0,
        'note': 'Model picks against market w/ confluence>=2. 0/6 — tiny sample but consistent.',
    },
    # MIXED COHORTS (ML and RL favorites disagree — bookmakers split signal)
    'ml_fav_rl_dog': {
        'n': 0, 'hit_rate': None,
        'note': 'Model picks ML-fav-but-RL-dog team (Orioles today). NO calibration data. SUPPRESS until n>=15.',
    },
    'ml_dog_rl_fav': {
        'n': 0, 'hit_rate': None,
        'note': 'Model picks RL-fav-but-ML-dog team. NO calibration data. SUPPRESS until n>=15.',
    },
}

FADE_MIN_N = 30        # Need solid sample before betting AGAINST our model
FADE_MAX_HIT = 0.30
SUPPRESS_MIN_N = 5     # SUPPRESS is low-risk (we just don't make a pick) — be permissive
SUPPRESS_MAX_HIT = 0.30


def cohort_for_pick(projected_spread, close_spread, confluence_net, home_ml=None, away_ml=None):
    """Categorize a pick into one of 6 cohorts based on:
      - whether model's pick aligns with the ML favorite
      - whether model's pick aligns with the RL favorite
      - whether ML and RL favorites are the same team (clean) or different (mixed)

    Mixed cohorts (ML and RL disagree on favorite — e.g. Orioles today where home is
    -115 ML fav but +1.5 RL dog) are STRUCTURALLY DIFFERENT from clean cohorts and
    have no historical calibration. Default SUPPRESS until data accumulates.
    """
    if projected_spread is None:
        return None
    ps = float(projected_spread)
    cn = int(confluence_net) if confluence_net is not None else 0
    model_home = ps > 0

    # Market direction signals
    ml_market_home = None
    rl_market_home = None
    if home_ml is not None and away_ml is not None:
        ml_market_home = float(home_ml) < float(away_ml)
    if close_spread is not None:
        rl_market_home = float(close_spread) < 0

    # Without either market signal we can't bucket
    if ml_market_home is None and rl_market_home is None:
        return None

    # MIXED cohort detection — both signals present AND they disagree
    if ml_market_home is not None and rl_market_home is not None and ml_market_home != rl_market_home:
        # Bookmakers split signal — model's pick is structurally different from clean chalk/dog
        if model_home == ml_market_home:
            return 'ml_fav_rl_dog'  # model picks ML-fav-but-RL-dog team (Orioles today)
        return 'ml_dog_rl_fav'      # model picks RL-fav-but-ML-dog team

    # CLEAN cohort — ML and RL agree, OR only one signal available
    if ml_market_home is not None:
        market_home = ml_market_home
    else:
        market_home = rl_market_home
    agrees = model_home == market_home
    cs = float(close_spread) if close_spread is not None else 0
    corrected_delta_abs = abs(ps + cs)

    if not agrees:
        if cn >= 2:
            return 'ml_dog_high_conv'
        return 'ml_dog'
    if corrected_delta_abs >= 1.5:
        return 'ml_chalk_high_mag'
    return 'ml_chalk'


def action_for_cohort(cohort):
    """Returns one of SURFACE / SUPPRESS / FADE based on calibration thresholds.
    Cohorts with no data (hit_rate is None or n < SUPPRESS_MIN_N) default to SUPPRESS
    when they lack calibration entirely (hit_rate=None) — conservative for new buckets.
    """
    if cohort is None or cohort not in CALIBRATION:
        return 'SURFACE'
    cal = CALIBRATION[cohort]
    n = cal['n']
    hit = cal['hit_rate']
    # No data at all — conservative SUPPRESS until calibrated
    if hit is None:
        return 'SUPPRESS'
    if n >= FADE_MIN_N and hit <= FADE_MAX_HIT:
        return 'FADE'
    if n >= SUPPRESS_MIN_N and hit <= SUPPRESS_MAX_HIT:
        return 'SUPPRESS'
    return 'SURFACE'


def adjust_pick(projected_spread, close_spread, confluence_net, home_team, away_team,
                home_ml=None, away_ml=None):
    """Apply auto-fade calibration to a pick.

    Returns dict with:
      action       : 'SURFACE' | 'SUPPRESS' | 'FADE'
      cohort       : the bucket the pick falls in
      pick_team    : the team to display (after FADE flip if applicable),
                     None if action == 'SUPPRESS'
      original_team: what the model originally picked (for audit trail)
      explanation  : short string explaining the action (for logs, not user-facing)
    """
    cohort = cohort_for_pick(projected_spread, close_spread, confluence_net, home_ml, away_ml)
    action = action_for_cohort(cohort)

    if projected_spread is None:
        return {'action': 'SUPPRESS', 'cohort': cohort, 'pick_team': None,
                'original_team': None, 'explanation': 'no projection'}

    model_pick = home_team if float(projected_spread) > 0 else away_team
    other = away_team if model_pick == home_team else home_team

    if action == 'SUPPRESS':
        cal = CALIBRATION.get(cohort, {})
        hit = cal.get('hit_rate')
        n = cal.get('n', 0)
        hit_str = f"{hit*100:.0f}%" if hit is not None else "no-data"
        return {'action': 'SUPPRESS', 'cohort': cohort, 'pick_team': None,
                'original_team': model_pick,
                'explanation': f"cohort {cohort} hit {hit_str} (n={n}) — drop"}
    if action == 'FADE':
        cal = CALIBRATION.get(cohort, {})
        hit = cal.get('hit_rate', 0) or 0
        return {'action': 'FADE', 'cohort': cohort, 'pick_team': other,
                'original_team': model_pick,
                'explanation': f"cohort {cohort} hit {hit*100:.0f}% — silent flip to {other}"}
    return {'action': 'SURFACE', 'cohort': cohort, 'pick_team': model_pick,
            'original_team': model_pick,
            'explanation': f"cohort {cohort} surface as-is"}


if __name__ == '__main__':
    # Smoke test
    print("=== Auto-fade calibration table ===")
    for cohort, data in CALIBRATION.items():
        print(f"  {cohort:20s} n={data['n']:3d} hit={data['hit_rate']*100:5.1f}%  -> {action_for_cohort(cohort)}")

    print("\n=== Sample picks ===")
    # Format: (label, ps, cs, cn, home_team, away_team)
    # ps positive = home wins by X; cs negative = home is RL favorite
    samples = [
        # Marlins yesterday: model picks Marlins (away), market RL has Marlins fav (cs+1.5 = home Giants RL dog)
        ("Marlins-style chalk-loved", -2.58, 1.5, 4, "Giants", "Marlins"),
        # Cubs today: model picks Cubs (away), market RL fav Dodgers (cs-1.5 home fav)
        ("Cubs dog pick (disagree, low conv)", -1.42, -1.5, 1, "Dodgers", "Cubs"),
        # Hypothetical Twins: model picks Twins (away) with conf+3, market RL fav home
        ("Twins dog pick (disagree, high conv)", -2.5, -1.5, 3, "Rays", "Twins"),
        # Standard chalk: model picks home Braves, market also Braves (cs-1.5)
        ("Standard chalk (agree, low mag)", 1.0, -1.5, 2, "Braves", "Phillies"),
    ]
    for name, ps, cs, cn, home, away in samples:
        result = adjust_pick(ps, cs, cn, home, away)
        print(f"  {name:40s} cohort={result['cohort'] or '-':20s} action={result['action']:8s} pick={result['pick_team']}")
