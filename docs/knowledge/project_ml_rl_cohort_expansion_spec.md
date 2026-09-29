---
name: ml-rl-cohort-expansion-spec
description: Spec for adding ML/RL-specific cohort features to close the 0-3 vs 10-23 cohort coverage gap on side plays. Designed 2026-06-09 night.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Problem:** On the 2026-06-09 slate, the cohort engine fired 10-23 STRONG_EDGE cohorts on TOTALS per game, but only 0-3 on ML/RL. Root cause: the existing feature set in `backtest_model_attribution_v6_middle_zone.py:cohort_memberships()` is **run-scoring-predictive** (xERA, wRC+ gap, park, NRFI). These features predict whether runs happen — they don't directly predict whether the favorite wins.

**Solution:** Add ML/RL-SPECIFIC features that predict win/loss margin specifically. Features already in `mlb_game_context` that the cohort backtest doesn't currently use:

## New feature additions

### Starter fatigue / rest cohorts (already in DB)
```python
# Fields: home_days_rest, away_days_rest, home_last_pitch_count, home_last_ip
for side in ("home", "away"):
    rest = _i(g.get(f"{side}_days_rest"))
    if rest is not None:
        if rest >= 6: out[f"{side}_sp_long_rest"] = True
        elif rest <= 3: out[f"{side}_sp_short_rest"] = True
    last_pitches = _i(g.get(f"{side}_last_pitch_count"))
    last_ip = _f(g.get(f"{side}_last_ip"))
    if last_pitches is not None and last_pitches >= 100 and rest and rest <= 4:
        out[f"{side}_sp_short_rest_after_grind"] = True  # 100+ pitches + ≤4 days rest
    if last_ip is not None and last_ip <= 2.0:
        out[f"{side}_sp_opener_or_short"] = True
```

### Bullpen depth cohorts (already in DB)
```python
# Already partially used. Add gap-style:
h_bp = _i(g.get("home_bp_relievers_3d")); a_bp = _i(g.get("away_bp_relievers_3d"))
if h_bp is not None and a_bp is not None:
    gap = h_bp - a_bp
    if gap >= 4: out["bp_gap_home_taxed"] = True  # 4+ more reliever uses → fade home
    elif gap <= -4: out["bp_gap_away_taxed"] = True
```

### Lineup-vs-hand cohorts (already in DB but unused in cohorts)
```python
# Fields: home_wrc_vs_opp_hand, away_wrc_vs_opp_hand
# Already used in scorer but NOT in cohort_memberships — adds platoon edge as ML signal
for side in ("home", "away"):
    wrc_hand = _f(g.get(f"{side}_wrc_vs_opp_hand"))
    wrc_season = _f(g.get(f"{side}_wrc_plus"))
    if wrc_hand is not None and wrc_season is not None:
        platoon_edge = wrc_hand - wrc_season
        if platoon_edge >= 10: out[f"{side}_platoon_strong"] = True
        elif platoon_edge <= -10: out[f"{side}_platoon_weak"] = True
```

### Sharp-money / line-movement cohorts (already in DB)
```python
# Fields: line_movements_count, open_spread vs close_spread gap
op = _f(g.get("open_spread")); cp = _f(g.get("close_spread"))
if op is not None and cp is not None:
    move = abs(cp - op)
    if move >= 1.0: out["line_moved_loud"] = True  # 1+ run line movement
    elif move >= 0.5: out["line_moved_mid"] = True
    # Direction: did line move toward home (home_now_favored) or away?
    if cp < op: out["line_moved_to_home"] = True  # spread became more negative = home more favored
    elif cp > op: out["line_moved_to_away"] = True
```

### Travel / consecutive-road cohorts (already in DB)
```python
# Fields: away_consecutive_road_games, home_travel_distance_last_game,
#         days_since_last_home_game
crg = _i(g.get("away_consecutive_road_games"))
if crg is not None:
    if crg >= 7: out["away_long_road"] = True  # 7+ games in a row away
    elif crg <= 1: out["away_fresh_off_home"] = True
dh = _i(g.get("days_since_last_home_game"))
if dh is not None and dh >= 7: out["home_returning_from_road"] = True
```

### OAA / defense cohorts (already in DB)
```python
# Fields: home_team_oaa, away_team_oaa
h_oaa = _i(g.get("home_team_oaa")); a_oaa = _i(g.get("away_team_oaa"))
if h_oaa is not None and a_oaa is not None:
    gap = h_oaa - a_oaa
    if abs(gap) >= 15: out["oaa_gap_loud"] = True
    if gap >= 15: out["oaa_loud_home"] = True
    elif gap <= -15: out["oaa_loud_away"] = True
```

### Compound ML/RL cohorts
```python
# Long-rest ace as dog (already a single rule in play_of_day; promote to cohort)
# Combined: pitcher rest + xERA quality + spread side
for side in ("home", "away"):
    rest = _i(g.get(f"{side}_days_rest"))
    xera = _f(g.get(f"{side}_sp_xera"))
    if rest and rest >= 5 and xera and xera <= 3.7:
        # Check if this side is the dog
        cs = _f(g.get("close_spread"))
        if cs is not None:
            home_is_fav = cs < 0
            side_is_dog = (side == "home") != home_is_fav
            if side_is_dog:
                out[f"{side}_rested_ace_dog"] = True
```

## Min sample threshold

Current `min_raw_n=10`. For ML/RL, drop to `min_raw_n=7` because:
- 3-outcome (RL has push tier) splits samples thinner
- Some travel/fatigue conditions are rare; raising threshold suppresses real edges
- Phase 1 ships with min_n=7 but tier-gates STRONG_EDGE at min_n=10 (only LOCK tier surfaces sub-10 cohorts)

## Implementation path

1. **Phase A (1-2 days):** Add new features to `cohort_memberships()` in backtest_model_attribution_v6_middle_zone.py. Run backtest. Inspect new cohort hit rates.
2. **Phase B (1-2 days):** Filter to cohorts with shrunken_pct ≥ 55% (real edge) and raw_n ≥ 7. Identify top 20-30 new rules.
3. **Phase C (2-3 days):** Wire into cohort_signals.py production pipeline. Add to refresh_cohort_signals cron step.
4. **Phase D (1-2 days):** Backtest validation on rolling 14-day live data. Confirm no rules degrade.

Total: ~1 week focused work. Cost: minimal — uses existing data fields.

## Expected outcome

Per-game ML/RL cohort firings should go from 0-3 → 5-10 with a mix of LOCK / STRONG_EDGE / LEAN / FADE tiers. This:
- Gives the cohort engine actual leverage on side plays (currently dominated by totals)
- Lets ladder/POTD/DAWG selectors make ML/RL decisions on engine signal, not just confluence_net
- Reduces over-reliance on confluence_net for side conviction (which has its own 50% baseline)

## Related
- [[project_cohort_engine_v2_workstream]] — original 3-item workstream
- [[project_attribution_backtest_608]] — current attribution backtest framework
- [[project_dynamic_cohort_framework_607]] — cohort labeling infrastructure these would plug into
