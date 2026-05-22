"""
Auto-fade calibration — handles cohorts where the model is systematically wrong.

Three actions per cohort based on hit rate + sample size:
  - SURFACE  : pick is shown to users as model says (default)
  - SUPPRESS : pick is silently dropped (don't push losing picks to users)
  - FADE     : silently flip the displayed pick to the OPPOSITE side
              (we lean on the model's inverse correlation when sample warrants)

Calibration thresholds (conservative — avoid acting on noisy small-sample bias):
  FADE     when 30d n >= 30 AND 30d hit_rate <= 0.30
  SUPPRESS when 30d n >= 15 AND 30d hit_rate <= 0.30
  REGIME   when 7d n >= 8 AND 7d hit_rate <= (30d hit_rate - 0.10)
           — current cohort is in a regime shift; downgrade to SUPPRESS
  SURFACE  otherwise

Cohorts now read live from mlb_tier_calibration (refreshed daily by
audit_tier_calibration.py). Hard-coded fallback below is used only when
the live table is unreachable (e.g. local dev without env vars).
"""

import os
import json
import urllib.parse
import urllib.request

# Hard-coded fallback (used only when mlb_tier_calibration is unreachable).
# Refreshed 2026-04-25 from April 1-23 backtest. Live table supersedes this.
FALLBACK_CALIBRATION = {
    'ml_chalk_high_mag': {'n': 7, 'hit_rate': 0.857, 'n_7d': 0, 'hit_rate_7d': None},
    'ml_chalk':          {'n': 32, 'hit_rate': 0.594, 'n_7d': 0, 'hit_rate_7d': None},
    'ml_dog':            {'n': 24, 'hit_rate': 0.25,  'n_7d': 0, 'hit_rate_7d': None},
    'ml_dog_high_conv':  {'n': 6,  'hit_rate': 0.0,   'n_7d': 0, 'hit_rate_7d': None},
    'ml_fav_rl_dog':     {'n': 0,  'hit_rate': None,  'n_7d': 0, 'hit_rate_7d': None},
    'ml_dog_rl_fav':     {'n': 0,  'hit_rate': None,  'n_7d': 0, 'hit_rate_7d': None},
}

# Map auto_fade cohort name → tier name in mlb_tier_calibration
_TIER_MAP = {
    'ml_chalk_high_mag': 'autofade_chalk_high_mag',
    'ml_chalk':          'autofade_chalk',
    'ml_dog':            'autofade_dog',
    'ml_dog_high_conv':  'autofade_dog_high_conv',
}

FADE_MIN_N = 30
FADE_MAX_HIT = 0.30
SUPPRESS_MIN_N = 5
SUPPRESS_MAX_HIT = 0.30
# Regime-shift guard: if recent 7d sample drops well below 30d rate, treat
# as a slump and SUPPRESS even if 30d looks healthy.
REGIME_MIN_N_7D = 8
REGIME_DROP = 0.10  # 7d must be at least 10pt below 30d to trigger


def _fetch_live_calibration():
    """Fetch fresh cohort rates from mlb_tier_calibration. Returns dict
    keyed by auto_fade cohort name with 30d + 7d rates. Falls back to
    FALLBACK_CALIBRATION on any failure (network, missing env, table
    not yet seeded for this cohort)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return dict(FALLBACK_CALIBRATION)
    tier_names = list(_TIER_MAP.values())
    # 2026-05-22 fix: filter to TODAY's computed_date so we don't hit
    # PostgREST's 1000-row default when historical calibration data
    # accumulates over a full season. Same bug as the sweat_card YRFI
    # truncation. One row per cohort per window per date = bounded.
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _today = (_dt.now(_tz.utc) - _td(hours=4)).strftime("%Y-%m-%d")
    try:
        # Single query for all autofade tiers, both windows.
        # Filter to sport='mlb' so a future NBA/NFL row in the same table
        # doesn't pollute MLB auto-fade calibration.
        params = urllib.parse.urlencode({
            "tier": f"in.({','.join(tier_names)})",
            "window_label": "in.(7d,30d)",
            "sport": "eq.mlb",
            "computed_date": f"eq.{_today}",
            "select": "tier,window_label,hits,total,hit_rate",
        }, safe=",.()")
        req = urllib.request.Request(
            f"{url}/rest/v1/mlb_tier_calibration?{params}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            rows = json.loads(r.read())
    except Exception:
        return dict(FALLBACK_CALIBRATION)

    # Build reverse map (tier_name → cohort_name)
    rev = {v: k for k, v in _TIER_MAP.items()}
    out = {k: dict(v) for k, v in FALLBACK_CALIBRATION.items()}
    for row in rows:
        cohort = rev.get(row.get("tier"))
        if not cohort:
            continue
        if cohort not in out:
            out[cohort] = {'n': 0, 'hit_rate': None, 'n_7d': 0, 'hit_rate_7d': None}
        if row.get("window_label") == "30d":
            out[cohort]['n'] = row.get("total") or 0
            out[cohort]['hit_rate'] = row.get("hit_rate")
        elif row.get("window_label") == "7d":
            out[cohort]['n_7d'] = row.get("total") or 0
            out[cohort]['hit_rate_7d'] = row.get("hit_rate")
    return out


# Live calibration loaded once per process. To force refresh, re-import.
CALIBRATION = _fetch_live_calibration()


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

    Order of checks:
      1. No data → SUPPRESS (conservative for new buckets)
      2. 30d FADE band (n>=30, rate<=0.30) → FADE
      3. 30d SUPPRESS band (n>=15, rate<=0.30) → SUPPRESS
      4. Regime guard: 7d sample dropped 10pt+ below 30d → SUPPRESS
         (cohort in a slump even if 30d still looks fine)
      5. Otherwise → SURFACE
    """
    if cohort is None or cohort not in CALIBRATION:
        return 'SURFACE'
    cal = CALIBRATION[cohort]
    n = cal['n']
    hit = cal['hit_rate']
    if hit is None:
        return 'SUPPRESS'
    if n >= FADE_MIN_N and hit <= FADE_MAX_HIT:
        return 'FADE'
    if n >= SUPPRESS_MIN_N and hit <= SUPPRESS_MAX_HIT:
        return 'SUPPRESS'
    # Regime-shift guard
    n_7d = cal.get('n_7d') or 0
    hit_7d = cal.get('hit_rate_7d')
    if hit_7d is not None and n_7d >= REGIME_MIN_N_7D and (hit - hit_7d) >= REGIME_DROP:
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
