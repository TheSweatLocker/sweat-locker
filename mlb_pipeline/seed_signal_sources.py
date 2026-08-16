"""Seed signal_sources with the initial MLB signal catalog (2026-08-16).

Populates ~50 signals covering every column class on mlb_game_context:
  * model      — MC, Panel, V4, V3, JerryPred (ml + total)
  * pitcher    — SP xERA/SIERA, heater flags, vs-team, first-inning
  * offense    — wRC+, OPS L7/L14, xwOBA, BABIP, barrel%
  * bullpen    — effective ERA, 3d usage, availability
  * defense    — team OAA, catcher framing
  * situational— rest days, travel, consecutive road, platoon
  * weather    — temperature, wind, rain, dome
  * park       — run factor
  * umpire     — OVER-tendency
  * cohort     — signal confluence net + v2
  * scenario   — sharp scenarios (via handler)
  * split      — line-movement classification (via handler)
  * external   — handicapper picks (via handler)

Every row is a plug-in — no code change to the scorer to add a new one.

CLI:
  python seed_signal_sources.py            # upsert all
  python seed_signal_sources.py --dry-run
  python seed_signal_sources.py --clear    # delete all + reseed (dev)
"""
from __future__ import annotations
import argparse, os, sys
from datetime import datetime, timezone
from pathlib import Path

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
H_WRITE = {**H_READ, 'Content-Type': 'application/json',
           'Prefer': 'resolution=merge-duplicates,return=minimal'}

# ═══════════════════════════════════════════════════════════════════════
# INITIAL SIGNAL CATALOG — every row is a plug-in.
# ═══════════════════════════════════════════════════════════════════════
#
# Conventions for readability:
#   condition_expr: fires when signal has meaningful direction on this game
#   side_expr:     which candidate the signal favors
#   strength_expr: how strong the opinion is, [0, 1]
#   display_prose: reader-friendly quote for Jerry, plain English only
#
# Sport = 'MLB' throughout. market_scope narrows to 'ml' | 'total' | 'multi'.
# ═══════════════════════════════════════════════════════════════════════

SIGNALS = [

    # ── MODEL CLASS ──────────────────────────────────────────────────
    {
        'signal_key': 'panel_implied_margin',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.panel_implied_margin is not None and abs(float(ctx.panel_implied_margin)) >= 0.3',
        'side_expr': '"HOME_ML" if float(ctx.panel_implied_margin) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.panel_implied_margin)) / 3.0, 1.0)',
        'weight_registry_key': 'panel_ml',
        'display_prose_template': 'the market-consensus panel projects {panel_implied_margin} runs',
        'description': 'Panel of external projection sources agrees on ML direction.',
    },
    {
        'signal_key': 'panel_implied_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.panel_implied_total is not None and ctx.close_total is not None and abs(float(ctx.panel_implied_total) - float(ctx.close_total)) >= 0.3',
        'side_expr': '"OVER" if float(ctx.panel_implied_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.panel_implied_total) - float(ctx.close_total)) / 2.0, 1.0)',
        'weight_registry_key': 'panel_implied_total',
        'display_prose_template': 'external panel projects {panel_implied_total} runs vs market {close_total}',
    },
    {
        'signal_key': 'v4_model_spread',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.model_pred_spread is not None and abs(float(ctx.model_pred_spread)) >= 0.5',
        'side_expr': '"HOME_ML" if float(ctx.model_pred_spread) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.model_pred_spread)) / 3.0, 1.0)',
        'weight_registry_key': 'v4_ml',
        'display_prose_template': 'V4 model sees a {model_pred_spread}-run edge',
    },
    {
        'signal_key': 'v4_model_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.model_pred_total is not None and ctx.close_total is not None and abs(float(ctx.model_pred_total) - float(ctx.close_total)) >= 0.5',
        'side_expr': '"OVER" if float(ctx.model_pred_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.model_pred_total) - float(ctx.close_total)) / 2.0, 1.0)',
        'weight_registry_key': 'v4_projected_total',
        'display_prose_template': 'V4 sees {model_pred_total} runs vs market {close_total}',
    },
    {
        'signal_key': 'jerry_pred_spread',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.jerry_pred_spread is not None and abs(float(ctx.jerry_pred_spread)) >= 0.5',
        'side_expr': '"HOME_ML" if float(ctx.jerry_pred_spread) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(float(ctx.jerry_pred_spread)) / 3.0, 1.0)',
        'weight_registry_key': 'jerry_pred_ml',
        'display_prose_template': 'the ensemble runs model projects a {jerry_pred_spread}-run edge',
    },
    {
        'signal_key': 'jerry_pred_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.jerry_pred_total is not None and ctx.close_total is not None and abs(float(ctx.jerry_pred_total) - float(ctx.close_total)) >= 0.5',
        'side_expr': '"OVER" if float(ctx.jerry_pred_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.jerry_pred_total) - float(ctx.close_total)) / 2.0, 1.0)',
        'weight_registry_key': 'jerry_pred_total',
        'display_prose_template': 'runs model projects {jerry_pred_total} vs market {close_total}',
    },
    {
        'signal_key': 'mc_high_confidence',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': 'ctx.mc_high_conf_flag == True and ctx.mc_high_conf_side is not None',
        'side_expr': '"HOME_ML" if ctx.mc_high_conf_side == "H" else "AWAY_ML"',
        'strength_expr': 'min((float(ctx.mc_high_conf_pct) - 50) / 30.0, 1.0) if ctx.mc_high_conf_pct is not None else 0.7',
        'weight_registry_key': 'mc_ml_high_conf',
        'display_prose_template': 'simulator sees this at {mc_high_conf_pct}% for one side',
    },

    # ── PITCHER CLASS ────────────────────────────────────────────────
    {
        'signal_key': 'home_pitcher_on_heater',
        'class': 'pitcher', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_pitcher_last_3_era is not None and float(ctx.home_pitcher_last_3_era) <= 2.50',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((3.5 - float(ctx.home_pitcher_last_3_era)) / 3.0, 1.0)',
        'display_prose_template': '{home_pitcher} on a heater — {home_pitcher_last_3_era} ERA last three starts',
        'description': 'Home starter dominant recently. Suppresses runs.',
    },
    {
        'signal_key': 'home_pitcher_ice_cold',
        'class': 'pitcher', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_pitcher_last_3_era is not None and float(ctx.home_pitcher_last_3_era) >= 5.50',
        'side_expr': '"OVER"',
        'strength_expr': 'min((float(ctx.home_pitcher_last_3_era) - 4.0) / 4.0, 1.0)',
        'display_prose_template': '{home_pitcher} getting rocked — {home_pitcher_last_3_era} ERA last three starts',
    },
    {
        'signal_key': 'away_pitcher_on_heater',
        'class': 'pitcher', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_pitcher_last_3_era is not None and float(ctx.away_pitcher_last_3_era) <= 2.50',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((3.5 - float(ctx.away_pitcher_last_3_era)) / 3.0, 1.0)',
        'display_prose_template': '{away_pitcher} on a heater — {away_pitcher_last_3_era} ERA last three starts',
    },
    {
        'signal_key': 'away_pitcher_ice_cold',
        'class': 'pitcher', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_pitcher_last_3_era is not None and float(ctx.away_pitcher_last_3_era) >= 5.50',
        'side_expr': '"OVER"',
        'strength_expr': 'min((float(ctx.away_pitcher_last_3_era) - 4.0) / 4.0, 1.0)',
        'display_prose_template': '{away_pitcher} getting rocked — {away_pitcher_last_3_era} ERA last three starts',
    },
    {
        'signal_key': 'home_ace_by_xera',
        'class': 'pitcher', 'market_scope': 'total',
        'condition_expr': 'ctx.home_sp_xera is not None and float(ctx.home_sp_xera) <= 3.20',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((4.0 - float(ctx.home_sp_xera)) / 2.0, 1.0)',
        'display_prose_template': '{home_pitcher} is an ace by underlying stuff — {home_sp_xera} xERA',
    },
    {
        'signal_key': 'away_ace_by_xera',
        'class': 'pitcher', 'market_scope': 'total',
        'condition_expr': 'ctx.away_sp_xera is not None and float(ctx.away_sp_xera) <= 3.20',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((4.0 - float(ctx.away_sp_xera)) / 2.0, 1.0)',
        'display_prose_template': '{away_pitcher} is an ace by underlying stuff — {away_sp_xera} xERA',
    },
    {
        'signal_key': 'home_pitcher_owns_opp',
        'class': 'pitcher', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_pitcher_vs_team_era is not None and ctx.home_pitcher_vs_team_ip is not None and float(ctx.home_pitcher_vs_team_ip) >= 15 and float(ctx.home_pitcher_vs_team_era) <= 2.75',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((3.5 - float(ctx.home_pitcher_vs_team_era)) / 3.0, 1.0)',
        'display_prose_template': '{home_pitcher} owns this lineup — {home_pitcher_vs_team_era} career ERA over {home_pitcher_vs_team_ip} IP vs them',
    },
    {
        'signal_key': 'away_pitcher_owns_opp',
        'class': 'pitcher', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_pitcher_vs_team_era is not None and ctx.away_pitcher_vs_team_ip is not None and float(ctx.away_pitcher_vs_team_ip) >= 15 and float(ctx.away_pitcher_vs_team_era) <= 2.75',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((3.5 - float(ctx.away_pitcher_vs_team_era)) / 3.0, 1.0)',
        'display_prose_template': '{away_pitcher} owns this lineup — {away_pitcher_vs_team_era} career ERA over {away_pitcher_vs_team_ip} IP vs them',
    },
    {
        'signal_key': 'home_pitcher_short_last_start',
        'class': 'pitcher', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_last_ip is not None and float(ctx.home_last_ip) <= 4.0',
        'side_expr': '"OVER"',
        'strength_expr': '0.35',
        'display_prose_template': '{home_pitcher} was pulled early last time — {home_last_ip} IP',
        'description': 'Short last outing hints at injury or command issues, elevates OVER risk.',
    },
    {
        'signal_key': 'home_first_inning_disaster',
        'class': 'pitcher', 'market_scope': 'total',
        'condition_expr': 'ctx.home_first_inning_era is not None and float(ctx.home_first_inning_era) >= 7.0',
        'side_expr': '"OVER"',
        'strength_expr': '0.5',
        'display_prose_template': '{home_pitcher} bleeds in the first — {home_first_inning_era} first-inning ERA',
    },
    {
        'signal_key': 'away_first_inning_disaster',
        'class': 'pitcher', 'market_scope': 'total',
        'condition_expr': 'ctx.away_first_inning_era is not None and float(ctx.away_first_inning_era) >= 7.0',
        'side_expr': '"OVER"',
        'strength_expr': '0.5',
        'display_prose_template': '{away_pitcher} bleeds in the first — {away_first_inning_era} first-inning ERA',
    },

    # ── OFFENSE CLASS ────────────────────────────────────────────────
    {
        'signal_key': 'home_offense_hot_l7',
        'class': 'offense', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_ops_last7 is not None and ctx.home_ops is not None and (float(ctx.home_ops_last7) - float(ctx.home_ops)) >= 0.070',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.5',
        'display_prose_template': '{home_team} offense heating up — L7 OPS {home_ops_last7} vs season {home_ops}',
    },
    {
        'signal_key': 'away_offense_hot_l7',
        'class': 'offense', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_ops_last7 is not None and ctx.away_ops is not None and (float(ctx.away_ops_last7) - float(ctx.away_ops)) >= 0.070',
        'side_expr': '"AWAY_ML"',
        'strength_expr': '0.5',
        'display_prose_template': '{away_team} offense heating up — L7 OPS {away_ops_last7} vs season {away_ops}',
    },
    {
        'signal_key': 'home_offense_cold_l7',
        'class': 'offense', 'market_scope': 'multi',
        'condition_expr': 'ctx.home_ops_last7 is not None and ctx.home_ops is not None and (float(ctx.home_ops) - float(ctx.home_ops_last7)) >= 0.070',
        'side_expr': '"AWAY_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} offense in a rut — L7 OPS {home_ops_last7} vs season {home_ops}',
    },
    {
        'signal_key': 'away_offense_cold_l7',
        'class': 'offense', 'market_scope': 'multi',
        'condition_expr': 'ctx.away_ops_last7 is not None and ctx.away_ops is not None and (float(ctx.away_ops) - float(ctx.away_ops_last7)) >= 0.070',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{away_team} offense in a rut — L7 OPS {away_ops_last7} vs season {away_ops}',
    },
    {
        'signal_key': 'home_babip_regression_over',
        'class': 'offense', 'market_scope': 'total',
        'condition_expr': 'ctx.home_team_babip_l14 is not None and float(ctx.home_team_babip_l14) < 0.270',
        'side_expr': '"OVER"',
        'strength_expr': '0.35',
        'display_prose_template': '{home_team} unlucky lately — L14 BABIP {home_team_babip_l14} regresses toward .300',
    },
    {
        'signal_key': 'away_babip_regression_over',
        'class': 'offense', 'market_scope': 'total',
        'condition_expr': 'ctx.away_team_babip_l14 is not None and float(ctx.away_team_babip_l14) < 0.270',
        'side_expr': '"OVER"',
        'strength_expr': '0.35',
        'display_prose_template': '{away_team} unlucky lately — L14 BABIP {away_team_babip_l14} regresses toward .300',
    },
    {
        'signal_key': 'home_platoon_edge',
        'class': 'offense', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_wrc_vs_opp_hand is not None and ctx.home_wrc_plus is not None and (float(ctx.home_wrc_vs_opp_hand) - float(ctx.home_wrc_plus)) >= 15',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} loves this hand — wRC+ {home_wrc_vs_opp_hand} vs {home_wrc_plus} season',
    },

    # ── BULLPEN CLASS ────────────────────────────────────────────────
    {
        'signal_key': 'home_bullpen_gassed',
        'class': 'bullpen', 'market_scope': 'total',
        'condition_expr': 'ctx.home_bp_relievers_3d is not None and int(ctx.home_bp_relievers_3d) >= 5',
        'side_expr': '"OVER"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} pen gassed — {home_bp_relievers_3d} arms used in last 3 games',
    },
    {
        'signal_key': 'away_bullpen_gassed',
        'class': 'bullpen', 'market_scope': 'total',
        'condition_expr': 'ctx.away_bp_relievers_3d is not None and int(ctx.away_bp_relievers_3d) >= 5',
        'side_expr': '"OVER"',
        'strength_expr': '0.4',
        'display_prose_template': '{away_team} pen gassed — {away_bp_relievers_3d} arms used in last 3 games',
    },
    {
        'signal_key': 'home_bullpen_elite',
        'class': 'bullpen', 'market_scope': 'total',
        'condition_expr': 'ctx.home_bullpen_effective_era is not None and float(ctx.home_bullpen_effective_era) <= 3.20',
        'side_expr': '"UNDER"',
        'strength_expr': '0.35',
        'display_prose_template': '{home_team} pen locks it down — {home_bullpen_effective_era} effective ERA',
    },
    {
        'signal_key': 'away_bullpen_elite',
        'class': 'bullpen', 'market_scope': 'total',
        'condition_expr': 'ctx.away_bullpen_effective_era is not None and float(ctx.away_bullpen_effective_era) <= 3.20',
        'side_expr': '"UNDER"',
        'strength_expr': '0.35',
        'display_prose_template': '{away_team} pen locks it down — {away_bullpen_effective_era} effective ERA',
    },

    # ── DEFENSE CLASS ────────────────────────────────────────────────
    {
        'signal_key': 'home_elite_defense',
        'class': 'defense', 'market_scope': 'total',
        'condition_expr': 'ctx.home_team_oaa is not None and float(ctx.home_team_oaa) >= 15',
        'side_expr': '"UNDER"',
        'strength_expr': '0.3',
        'display_prose_template': '{home_team} defense is elite — {home_team_oaa} Outs Above Average',
    },

    # ── SITUATIONAL CLASS ───────────────────────────────────────────
    {
        'signal_key': 'home_long_rest',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_days_rest is not None and int(ctx.home_days_rest) >= 2',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.25',
        'display_prose_template': '{home_team} coming off {home_days_rest} days rest',
    },
    {
        'signal_key': 'away_road_grind',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.away_consecutive_road_games is not None and int(ctx.away_consecutive_road_games) >= 8',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.3',
        'display_prose_template': '{away_team} on {away_consecutive_road_games} straight road games — travel fatigue',
    },

    # ── WEATHER CLASS ────────────────────────────────────────────────
    {
        'signal_key': 'cold_game_under',
        'class': 'weather', 'market_scope': 'total',
        'condition_expr': 'ctx.temperature is not None and int(ctx.temperature) <= 55 and (ctx.is_dome is None or ctx.is_dome == False)',
        'side_expr': '"UNDER"',
        'strength_expr': '0.4',
        'display_prose_template': 'game-time temp {temperature}F suppresses offense',
    },
    {
        'signal_key': 'hot_game_over',
        'class': 'weather', 'market_scope': 'total',
        'condition_expr': 'ctx.temperature is not None and int(ctx.temperature) >= 88 and (ctx.is_dome is None or ctx.is_dome == False)',
        'side_expr': '"OVER"',
        'strength_expr': '0.3',
        'display_prose_template': 'game-time temp {temperature}F helps the ball carry',
    },
    {
        'signal_key': 'wind_blowing_out',
        'class': 'weather', 'market_scope': 'total',
        'condition_expr': 'ctx.wind_blowing_in is not None and ctx.wind_blowing_in == False and ctx.wind_speed is not None and int(ctx.wind_speed) >= 10',
        'side_expr': '"OVER"',
        'strength_expr': '0.35',
        'display_prose_template': 'wind {wind_speed} mph blowing out',
    },
    {
        'signal_key': 'wind_blowing_in',
        'class': 'weather', 'market_scope': 'total',
        'condition_expr': 'ctx.wind_blowing_in is not None and ctx.wind_blowing_in == True and ctx.wind_speed is not None and int(ctx.wind_speed) >= 10',
        'side_expr': '"UNDER"',
        'strength_expr': '0.35',
        'display_prose_template': 'wind {wind_speed} mph blowing in — knocks HRs down',
    },

    # ── PARK CLASS ───────────────────────────────────────────────────
    {
        'signal_key': 'hitters_park_over',
        'class': 'park', 'market_scope': 'total',
        'condition_expr': 'ctx.park_run_factor is not None and float(ctx.park_run_factor) >= 108',
        'side_expr': '"OVER"',
        'strength_expr': 'min((float(ctx.park_run_factor) - 100) / 15.0, 1.0)',
        'display_prose_template': 'hitter-friendly park (run factor {park_run_factor})',
    },
    {
        'signal_key': 'pitchers_park_under',
        'class': 'park', 'market_scope': 'total',
        'condition_expr': 'ctx.park_run_factor is not None and float(ctx.park_run_factor) <= 92',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((100 - float(ctx.park_run_factor)) / 15.0, 1.0)',
        'display_prose_template': 'pitcher-friendly park (run factor {park_run_factor})',
    },

    # ── COHORT CLASS ─────────────────────────────────────────────────
    {
        'signal_key': 'confluence_home_lean',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) >= 2',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min(int(ctx.signal_confluence_net) / 6.0, 1.0)',
        'display_prose_template': 'cohort net favors home {signal_confluence_net}',
    },
    {
        'signal_key': 'confluence_away_lean',
        'class': 'cohort', 'market_scope': 'ml',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) <= -2',
        'side_expr': '"AWAY_ML"',
        'strength_expr': 'min(abs(int(ctx.signal_confluence_net)) / 6.0, 1.0)',
        'display_prose_template': 'cohort net favors away {signal_confluence_net}',
    },

    # ── SPLIT / LINE-MOVE CLASS ─────────────────────────────────────
    # Note: these rely on the scorer's handler wire-in (fetching
    # line_movement_flags at score time). condition_expr is intentionally
    # placeholder — the scorer detects _class=='split' and does the fetch.
    {
        'signal_key': 'sharp_split_triple_confirmed',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG',  # scorer intercepts
        'side_expr': '_HANDLER_LINE_FLAG',
        'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'all three public-split sources agree sharp money is on this side',
    },
    {
        'signal_key': 'sharp_split_confirmed',
        'class': 'split', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_LINE_FLAG',
        'side_expr': '_HANDLER_LINE_FLAG',
        'strength_expr': '_HANDLER_LINE_FLAG',
        'weight_registry_key': 'cross_source_sharp_confirmed',
        'display_prose_template': 'two split sources agree sharp money is here',
    },

    # ── SCENARIO CLASS (per-game matches, handler-based) ────────────
    {
        'signal_key': 'sharp_scenario_match',
        'class': 'scenario', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_SCENARIO',
        'side_expr': '_HANDLER_SCENARIO',
        'strength_expr': '_HANDLER_SCENARIO',
        'display_prose_template': 'historical pattern hit {hit_rate}% in {sample_n} similar spots',
    },

    # ── EXTERNAL HANDICAPPER CLASS (handler-based) ──────────────────
    {
        'signal_key': 'external_handicapper_pick',
        'class': 'external_pick', 'market_scope': 'multi',
        'condition_expr': '_HANDLER_EXTERNAL',
        'side_expr': '_HANDLER_EXTERNAL',
        'strength_expr': '_HANDLER_EXTERNAL',
        'display_prose_template': 'external analysts are on this side',
    },
]


def upsert(dry_run: bool = False):
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads = []
    for s in SIGNALS:
        row = {
            'signal_key':      s['signal_key'],
            'sport':           s.get('sport', 'MLB'),
            'market_scope':    s.get('market_scope', 'multi'),
            'class':           s['class'],
            'condition_expr':  s['condition_expr'],
            'side_expr':       s['side_expr'],
            'strength_expr':   s.get('strength_expr', '0.5'),
            'weight_registry_key':    s.get('weight_registry_key'),
            'hit_rate_pct':           s.get('hit_rate_pct'),
            'sample_n':               s.get('sample_n'),
            'display_prose_template': s.get('display_prose_template'),
            'description':            s.get('description'),
            'enabled':                s.get('enabled', True),
            'origin':                 'SEEDED',
            'updated_at':             now_iso,
        }
        payloads.append(row)

    # Union all keys per feedback_postgrest_batch_normalize_keys
    all_keys = set()
    for p in payloads: all_keys.update(p.keys())
    normalized = [{k: p.get(k) for k in all_keys} for p in payloads]

    print(f'=== seeding signal_sources · {len(normalized)} rows ===')
    for row in normalized:
        print(f'  {row["signal_key"]:<35} [{row["class"]:<12}] {row["market_scope"]:<5}')

    if dry_run:
        print('\n[DRY-RUN] no writes')
        return

    written = 0
    for i in range(0, len(normalized), 100):
        chunk = normalized[i:i+100]
        pr = requests.post(
            f'{SB}/rest/v1/signal_sources?on_conflict=signal_key,sport,market_scope',
            headers=H_WRITE, json=chunk, timeout=15)
        if pr.status_code in (200, 201, 204):
            written += len(chunk)
        else:
            print(f'  ✗ chunk {i}: {pr.status_code} {pr.text[:200]}')
    print(f'  ✓ wrote {written} signals')


def clear_all(dry_run: bool = False):
    if dry_run:
        print('[DRY-RUN] would DELETE all signal_sources rows')
        return
    r = requests.delete(f'{SB}/rest/v1/signal_sources?id=gt.0', headers=H_WRITE, timeout=15)
    print(f'  cleared: {r.status_code}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--clear', action='store_true')
    args = p.parse_args()
    if args.clear:
        clear_all(dry_run=args.dry_run)
    upsert(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
