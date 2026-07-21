"""
Shared cohort feature extraction.

Pulled out of the backtest_model_attribution_*.py family so both the
NIGHTLY recompute (refresh_cohort_signals.py) and the RUNTIME lookup
(cohort_signals.py) extract the same set of features for any game.

USAGE:
    from cohort_features import get_features
    features = get_features(g)  # returns set of cohort feature keys
    if 'v3_tot_loud' in features: ...

The features dict is a flat set of string keys — each one represents
a cohort membership. Rules in cohort_signals.json reference these keys
in their `matches_if` list; a rule fires when every key it requires is
in this set.
"""
import os


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _i(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def _ml_call(spread):
    if spread is None or abs(spread) < 0.3: return None
    return "home" if spread > 0 else "away"


def _conf_side(net):
    n = _i(net)
    if n is None: return None
    if n > 1: return "home"
    if n < -1: return "away"
    return None


def _home_is_dog(g):
    cs = _f(g.get("close_spread")) or _f(g.get("open_spread"))
    if cs is None: return None
    return cs > 0


def get_features(g):
    """Return a set of all cohort feature keys this game qualifies for.

    Mirrors the cohort_memberships() helpers used across the
    backtest_model_attribution_* family. Update both when adding new
    features — refresh_cohort_signals.py uses this same function so a
    rule discovered in the backtest is automatically evaluable at runtime.
    """
    out = set()
    if not isinstance(g, dict):
        return out

    # ── Confluence ──
    cn = _i(g.get("signal_confluence_net"))
    if cn is not None:
        mag = abs(cn)
        out.add(f"conf_mag={mag}")
        if mag >= 4: out.add("conf_mag>=4")
        if mag >= 5: out.add("conf_mag>=5")
        if mag == 3: out.add("conf_mag=3")
        if 2 <= mag <= 3: out.add("conf_mag_mid")
        if mag <= 3: out.add("conf_mag<=3")  # middle-zone marker

    cs_dir = _conf_side(cn) if cn is not None else None
    hd = _home_is_dog(g)
    if cs_dir and hd is not None:
        if (cs_dir == "home" and hd) or (cs_dir == "away" and not hd):
            out.add("conf_points_to_dog")
        else:
            out.add("conf_points_to_fav")
        if "conf_mag=4" in out and "conf_points_to_fav" in out: out.add("conf4+fav")
        if "conf_mag=4" in out and "conf_points_to_dog" in out: out.add("conf4+dog")
        if "conf_mag=5" in out and "conf_points_to_fav" in out: out.add("conf5+fav")
        if "conf_mag=5" in out and "conf_points_to_dog" in out: out.add("conf5+dog")

    # ── Model agreement (ML direction) ──
    v3s = _f(g.get("projected_spread"))
    v4s = _f(g.get("model_pred_spread"))
    js = _f(g.get("jerry_pred_spread"))
    v3d, v4d, jd = _ml_call(v3s), _ml_call(v4s), _ml_call(js)
    if v3d and v4d:
        out.add("v3_v4_agree" if v3d == v4d else "v3_v4_disagree")
    if v4d and jd:
        out.add("v4_jerry_agree" if v4d == jd else "v4_jerry_disagree")
    if v3d and jd:
        out.add("v3_jerry_agree" if v3d == jd else "v3_jerry_disagree")

    # ── Spread magnitude ──
    for name, spread in (("v3", v3s), ("v4", v4s), ("jerry", js)):
        if spread is None: continue
        mag = abs(spread)
        if mag >= 3.0: out.add(f"{name}_spread_extreme")
        elif mag >= 2.0: out.add(f"{name}_spread_loud")
        elif mag >= 1.0: out.add(f"{name}_spread_mid")
        else: out.add(f"{name}_spread_quiet")

    # ── Total magnitude ──
    line = _f(g.get("close_total")) or _f(g.get("open_total"))
    for name, mt in (("v3", _f(g.get("projected_total"))),
                     ("v4", _f(g.get("model_pred_total"))),
                     ("jerry", _f(g.get("jerry_pred_total")))):
        if mt is None or line is None: continue
        d = mt - line
        if abs(d) >= 2.0: out.add(f"{name}_tot_loud")
        elif abs(d) >= 1.0: out.add(f"{name}_tot_mid")
        if d >= 1.0: out.add(f"{name}_tot_over_lean")
        elif d <= -1.0: out.add(f"{name}_tot_under_lean")

    # ── v3+v4 consensus on totals ──
    v3_t = _f(g.get("projected_total")); v4_t = _f(g.get("model_pred_total"))
    if line and v3_t is not None and v4_t is not None:
        d3 = v3_t - line; d4 = v4_t - line
        same_dir = (d3 > 0 and d4 > 0) or (d3 < 0 and d4 < 0)
        if same_dir and abs(d3) >= 1.0 and abs(d4) >= 1.0:
            out.add("v3_v4_tot_consensus")
            if abs(d3) >= 2.0 and abs(d4) >= 2.0:
                out.add("v3_v4_tot_loud_consensus")

    # ── Park ──
    park = _f(g.get("park_run_factor"))
    if park is not None:
        if park >= 108: out.add("park_extreme_hitter")
        elif park >= 104: out.add("park_hitter_friendly")
        elif park <= 92: out.add("park_extreme_pitcher")
        elif park <= 96: out.add("park_pitcher_friendly")

    # ── Temperature ──
    temp = _f(g.get("temperature"))
    if temp is not None:
        if temp <= 45: out.add("temp_freezing")
        elif temp <= 50: out.add("temp_cold")
        elif temp <= 60: out.add("temp_cool")
        elif temp >= 85: out.add("temp_extreme_hot")
        elif temp >= 80: out.add("temp_hot")

    # ── Pitcher mastery / blowup vs current opp ──
    for side in ("home", "away"):
        vt_era = _f(g.get(f"{side}_pitcher_vs_team_era"))
        vt_ip = _f(g.get(f"{side}_pitcher_vs_team_ip"))
        if vt_era is not None and vt_ip and vt_ip >= 15:
            if vt_era <= 2.5: out.add(f"{side}_sp_mastery")
            elif vt_era >= 5.5: out.add(f"{side}_sp_blowup_history")

    # ── Pitcher form drift / hot ──
    for side in ("home", "away"):
        l3 = _f(g.get(f"{side}_pitcher_last_3_era"))
        xera = _f(g.get(f"{side}_sp_xera"))
        if l3 is not None and xera is not None:
            d = l3 - xera
            if d >= 2.0: out.add(f"{side}_sp_form_drift_bad")
            elif d <= -1.5: out.add(f"{side}_sp_form_hot")

    # ── xERA tier ──
    h_xera = _f(g.get("home_sp_xera")); a_xera = _f(g.get("away_sp_xera"))
    for side, xera in (("home", h_xera), ("away", a_xera)):
        if xera is None: continue
        if xera <= 3.0: out.add(f"{side}_xera_elite")
        elif xera <= 4.0: out.add(f"{side}_xera_solid")
        elif xera >= 4.75: out.add(f"{side}_xera_shaky")

    # ── xERA gap ──
    if h_xera is not None and a_xera is not None:
        gap = abs(h_xera - a_xera)
        if gap >= 1.5: out.add("xera_gap_loud")
        elif gap <= 0.3: out.add("xera_gap_tight")
        if h_xera <= 3.0 and a_xera >= 4.75: out.add("mismatch_home_elite")
        elif a_xera <= 3.0 and h_xera >= 4.75: out.add("mismatch_away_elite")

    # ── Both starters classes ──
    if "home_xera_elite" in out and "away_xera_elite" in out: out.add("both_aces")
    if "home_xera_shaky" in out and "away_xera_shaky" in out: out.add("both_shaky")

    # ── K-gap loud ──
    for side in ("home", "away"):
        kg = _f(g.get(f"{side}_k_gap"))
        if kg is None: continue
        if kg >= 3.0: out.add(f"{side}_k_gap_loud_plus")
        elif kg <= -3.0: out.add(f"{side}_k_gap_loud_minus")

    # ── K-class joint (high-K SP vs K-prone opp lineup) ──
    h_k = _f(g.get("home_sp_k_pct")); a_k = _f(g.get("away_sp_k_pct"))
    h_team_k = _f(g.get("home_team_k_pct")); a_team_k = _f(g.get("away_team_k_pct"))
    if h_k is not None and a_team_k is not None:
        if h_k >= 28 and a_team_k >= 25: out.add("home_sp_high_k+away_team_high_k")
        elif h_k <= 20 and a_team_k <= 20: out.add("home_sp_low_k+away_team_low_k")
    if a_k is not None and h_team_k is not None:
        if a_k >= 28 and h_team_k >= 25: out.add("away_sp_high_k+home_team_high_k")
        elif a_k <= 20 and h_team_k <= 20: out.add("away_sp_low_k+home_team_low_k")

    # ── K% tier ──
    for side in ("home", "away"):
        kp = _f(g.get(f"{side}_sp_k_pct"))
        if kp is None: continue
        if kp >= 26: out.add(f"{side}_kpct_high_k")
        elif kp >= 22: out.add(f"{side}_kpct_mid_k")
        else: out.add(f"{side}_kpct_low_k")

    # ── 1st-inning ──
    for side in ("home", "away"):
        fi = _f(g.get(f"{side}_first_inning_era"))
        if fi is None: continue
        if fi >= 6.0: out.add(f"{side}_sp_1st_inn_shaky")
        elif fi <= 1.5: out.add(f"{side}_sp_1st_inn_clean")

    if "home_sp_1st_inn_shaky" in out and "away_sp_1st_inn_shaky" in out:
        out.add("both_1st_inn_shaky")
    if "home_sp_1st_inn_clean" in out and "away_sp_1st_inn_clean" in out:
        out.add("both_1st_inn_clean")

    # ── Days rest ──
    for side in ("home", "away"):
        dr = _i(g.get(f"{side}_sp_days_rest"))
        if dr is None: continue
        if dr <= 4: out.add(f"{side}_sp_short_rest")
        elif dr >= 7: out.add(f"{side}_sp_long_rest")
        else: out.add(f"{side}_sp_reg_rest")

    # ── Bullpen taxed/rested ──
    for side in ("home", "away"):
        bp = _i(g.get(f"{side}_bp_relievers_3d"))
        if bp is None: continue
        if bp >= 8: out.add(f"{side}_bp_taxed")
        elif bp <= 3: out.add(f"{side}_bp_rested")
    if "home_bp_taxed" in out or "away_bp_taxed" in out:
        out.add("bullpen_taxed_either")
    if "home_bp_taxed" in out and "away_bp_taxed" in out:
        out.add("both_bullpens_taxed")

    # ── wRC+ differential ──
    h_wrc = _f(g.get("home_wrc_plus")); a_wrc = _f(g.get("away_wrc_plus"))
    if h_wrc is not None and a_wrc is not None:
        diff = h_wrc - a_wrc
        if abs(diff) >= 20: out.add("wrc_gap_huge")
        if abs(diff) >= 15:
            out.add("wrc_gap_loud")
            if diff > 0: out.add("wrc_loud_home_better")
            else: out.add("wrc_loud_away_better")
        elif abs(diff) >= 10: out.add("wrc_gap_modest")

    # ── L10 R/G offense drift ──
    for side in ("home", "away"):
        l10 = _f(g.get(f"{side}_last10_runs_per_game"))
        rpg = _f(g.get(f"{side}_runs_per_game"))
        if l10 is not None and rpg is not None:
            drift = l10 - rpg
            if drift >= 1.5: out.add(f"{side}_offense_hot")
            elif drift <= -1.5: out.add(f"{side}_offense_cold")

    # ── NRFI / YRFI ──
    nrfi = _f(g.get("nrfi_score"))
    if nrfi is not None:
        if nrfi >= 90: out.add("nrfi_high")
        elif nrfi >= 70: out.add("nrfi_lean")
        elif nrfi <= 25: out.add("yrfi_lean")

    # ── Umpire ──
    un = (g.get("umpire_note") or "").lower()
    if "over-friendly" in un or "hitter-friendly" in un: out.add("ump_over")
    elif "under-friendly" in un or "pitcher-friendly" in un: out.add("ump_under")

    # ── Lineup confirmed ──
    if g.get("lineup_confirmed") is True: out.add("lineup_confirmed")
    elif g.get("lineup_confirmed") is False: out.add("lineup_unconfirmed")

    # ── 2D interactions used in rules ──
    # Form drift × opp lineup hot
    if "home_sp_form_drift_bad" in out:
        opp_wrc = _f(g.get("away_wrc_plus"))
        if opp_wrc is not None and opp_wrc >= 105:
            out.add("home_drift+opp_hot")
    if "away_sp_form_drift_bad" in out:
        opp_wrc = _f(g.get("home_wrc_plus"))
        if opp_wrc is not None and opp_wrc >= 105:
            out.add("away_drift+opp_hot")

    # v3 tot loud × form drift
    if "v3_tot_loud" in out:
        if "away_sp_form_drift_bad" in out:
            out.add("v3_tot_loud+away_sp_form_drift_bad")
        if "home_sp_form_drift_bad" in out:
            out.add("v3_tot_loud+home_sp_form_drift_bad")

    # v3 tot lean over × form drift
    if "v3_tot_over_lean" in out:
        if "away_sp_form_drift_bad" in out:
            out.add("v3_tot_over_lean+away_sp_form_drift_bad")

    # ═══════════════════════════════════════════════════════════════════
    # ML/RL-SPECIFIC features (added 2026-06-09, spec project_ml_rl_cohort_expansion_spec).
    # Closes the 10x cohort coverage gap on side plays (totals fire 10-23
    # STRONG_EDGE rules/game vs ML/RL 0-3). These features predict win/loss
    # margin specifically, not run scoring — that's the gap they fill.
    # ═══════════════════════════════════════════════════════════════════

    # ── Bullpen GAP (differential, predicts late-game ML leverage) ──
    h_bp = _i(g.get("home_bp_relievers_3d"))
    a_bp = _i(g.get("away_bp_relievers_3d"))
    if h_bp is not None and a_bp is not None:
        bp_gap = h_bp - a_bp
        if bp_gap >= 4: out.add("bp_gap_home_taxed")  # fade home ML
        elif bp_gap <= -4: out.add("bp_gap_away_taxed")  # fade away ML

    # ── Lineup vs opp-hand platoon edge (already used in scorer but not in cohorts) ──
    for side in ("home", "away"):
        wrc_hand = _f(g.get(f"{side}_wrc_vs_opp_hand"))
        wrc_season = _f(g.get(f"{side}_wrc_plus"))
        if wrc_hand is not None and wrc_season is not None:
            platoon_edge = wrc_hand - wrc_season
            if platoon_edge >= 10: out.add(f"{side}_platoon_strong")
            elif platoon_edge <= -10: out.add(f"{side}_platoon_weak")

    # ── Line movement (sharp money signal) ──
    op = _f(g.get("open_spread"))
    cp = _f(g.get("close_spread"))
    if op is not None and cp is not None:
        move = abs(cp - op)
        if move >= 1.0: out.add("line_moved_loud")
        elif move >= 0.5: out.add("line_moved_mid")
        # Direction: cp < op = spread became more negative = home more favored
        if cp < op - 0.25: out.add("line_moved_to_home")
        elif cp > op + 0.25: out.add("line_moved_to_away")

    # ── Reverse Line Move (RLM) — sharp $ disagreement (2026-07-21) ──
    # Per cohort_discovery agent: industry's #1 signal, and we detect line
    # movement but never join it to model direction. Fires when line moved
    # loudly AGAINST the model's implied side, meaning sharps are betting
    # the opposite of what our models are seeing. That's real information —
    # the market is telling us we're wrong.
    #
    # Model direction proxy: v3 spread (projected_spread) or v4 spread
    # (model_pred_spread). Negative = HOME favored by our models.
    model_spread = _f(g.get("model_pred_spread")) or _f(g.get("projected_spread"))
    if op is not None and cp is not None and model_spread is not None and abs(cp - op) >= 0.5:
        # Line moving TO home = market getting sharper on home
        # Line moving TO away = market getting sharper on away
        market_direction = "home" if cp < op else "away"
        model_direction = "home" if model_spread < 0 else "away"
        if market_direction != model_direction and abs(cp - op) >= 0.5:
            out.add("line_move_sharp_disagreement")
            # Which way sharps moved
            out.add(f"line_moved_sharp_to_{market_direction}")
            # Bigger move = louder RLM
            if abs(cp - op) >= 1.0:
                out.add("line_move_sharp_disagreement_loud")
        elif market_direction == model_direction:
            # Line moved WITH model — market confirming our read
            out.add("line_move_confirms_model")

    # Total line RLM (2026-07-21) — same pattern for totals
    op_total = _f(g.get("open_total"))
    cp_total = _f(g.get("close_total"))
    model_total = _f(g.get("model_pred_total")) or _f(g.get("projected_total"))
    if op_total is not None and cp_total is not None and model_total is not None and abs(cp_total - op_total) >= 0.5:
        market_dir_tot = "over" if cp_total > op_total else "under"
        model_dir_tot = "over" if model_total > (cp_total or op_total) else "under"
        if market_dir_tot != model_dir_tot and abs(cp_total - op_total) >= 0.5:
            out.add("total_line_move_sharp_disagreement")
            out.add(f"total_line_moved_sharp_to_{market_dir_tot}")

    # ── Travel / consecutive-road exhaustion ──
    crg = _i(g.get("away_consecutive_road_games"))
    if crg is not None:
        if crg >= 7: out.add("away_long_road")  # tired away team
        elif crg <= 1: out.add("away_fresh_off_home")
    dh = _i(g.get("days_since_last_home_game"))
    if dh is not None and dh >= 7: out.add("home_returning_from_road")

    # ── Team defense (OAA gap predicts close games) ──
    h_oaa = _i(g.get("home_team_oaa"))
    a_oaa = _i(g.get("away_team_oaa"))
    if h_oaa is not None and a_oaa is not None:
        oaa_gap = h_oaa - a_oaa
        if abs(oaa_gap) >= 15:
            out.add("oaa_gap_loud")
            if oaa_gap >= 15: out.add("oaa_loud_home")
            else: out.add("oaa_loud_away")

    # ── Compound: rested ace as DOG (promote single rule to cohort) ──
    # Long-rest + sub-3.7 xERA + DOG side. play_of_day already has this as a
    # +6/+12 driver (62% / 85% lifetime per backtest). Promoting to cohort
    # so the engine can vote on it via cohort_signals instead of a hardcoded
    # rule. Verified the existing rule is signal, not noise — making it a
    # first-class cohort lets future backtests re-validate the edge.
    cs_val = _f(g.get("close_spread")) or _f(g.get("open_spread"))
    if cs_val is not None:
        home_is_fav = cs_val < 0
        for side in ("home", "away"):
            rest = _i(g.get(f"{side}_sp_days_rest"))
            sp_xera = _f(g.get(f"{side}_sp_xera"))
            if rest is None or sp_xera is None: continue
            if rest >= 5 and sp_xera <= 3.70:
                side_is_dog = (side == "home") != home_is_fav
                if side_is_dog:
                    out.add(f"{side}_rested_ace_dog")

    return out
