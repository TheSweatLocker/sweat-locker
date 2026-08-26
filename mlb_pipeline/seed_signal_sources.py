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
    # 2026-08-22 GAP FILL — projected_total (v3) had NO signal_source
    # even though it's the ensemble's primary heuristic runs projection
    # (computed in game_context.py from xERA + wRC + park + weather etc,
    # calibrated on backtest). Padres @ Twins tonight:
    #   projected_total 9.6, close_total 8.5 → +1.1 OVER edge, silently
    #   dropped. Total market ended up picking UNDER despite the model
    #   projecting OVER by 1.1 runs.
    # Now fires alongside jerry/v4/panel projections; ensemble's own
    # runs projection becomes a first-class citizen in the vote pool.
    {
        'signal_key': 'projected_total',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.projected_total is not None and ctx.close_total is not None and abs(float(ctx.projected_total) - float(ctx.close_total)) >= 0.5',
        'side_expr': '"OVER" if float(ctx.projected_total) > float(ctx.close_total) else "UNDER"',
        'strength_expr': 'min(abs(float(ctx.projected_total) - float(ctx.close_total)) / 2.0, 1.0)',
        'weight_registry_key': 'projected_total',
        'display_prose_template': 'primary runs projection {projected_total} vs market {close_total}',
    },

    # ── MODEL-COMBO CLASS (2026-08-26) ───────────────────────────────
    # Individual model signals hit ~50% (MLB dead-ball 2026 — books price
    # them in). Empirical mining shows AGREEMENT PATTERNS have real edge:
    #   UNDER-consensus and AWAY-consensus BACK
    #   OVER-consensus and HOME-consensus FADE
    # This is systemic — every model was trained on richer scoring / more
    # HFA years than 2026 actually is. Books capture the OVER/HOME thesis;
    # the UNDER/AWAY thesis is what beats the closing line.
    #
    # 60-day empirical rates baked into hit_rate_pct/sample_n so these
    # earn weight from day 1. refit_signal_registry updates over time.
    # All rows are class='model' so class-balance cap keeps them from
    # dominating any single side.

    # -- TOTAL: back consensus UNDERs --
    {
        'signal_key': 'mc_panel_agree_under',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': ('ctx.close_total is not None and '
                           'ctx.panel_implied_total is not None and '
                           'ctx.mc_probabilities is not None and '
                           '(ctx.mc_probabilities or {}).get("mc_mean_total") is not None and '
                           'float(ctx.panel_implied_total) < float(ctx.close_total) - 0.5 and '
                           'float((ctx.mc_probabilities or {}).get("mc_mean_total")) < float(ctx.close_total) - 0.5'),
        'side_expr': '"UNDER"',
        'strength_expr': '0.6',
        'hit_rate_pct': 65.1, 'sample_n': 43,
        'display_prose_template': 'MC ({mc_probabilities.mc_mean_total}) and panel ({panel_implied_total}) both under line {close_total} — historically 65% under',
        'description': 'MC+Panel joint UNDER lean. 65.1% n=43 60d MLB.',
    },
    {
        'signal_key': 'jerry_panel_agree_under',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': ('ctx.close_total is not None and '
                           'ctx.jerry_pred_total is not None and '
                           'ctx.panel_implied_total is not None and '
                           'float(ctx.jerry_pred_total) < float(ctx.close_total) - 0.5 and '
                           'float(ctx.panel_implied_total) < float(ctx.close_total) - 0.5'),
        'side_expr': '"UNDER"',
        'strength_expr': '0.7',
        'hit_rate_pct': 77.8, 'sample_n': 9,
        'display_prose_template': 'Jerry ({jerry_pred_total}) and panel ({panel_implied_total}) both under line {close_total} — historically 78% under',
        'description': 'Jerry+Panel joint UNDER lean. 77.8% n=9 60d MLB — small sample, DISCOVERY tier.',
    },

    # -- TOTAL: FADE consensus OVERs (contrarian side=UNDER) --
    {
        'signal_key': 'jerry_panel_mc_agree_over_fade',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': ('ctx.close_total is not None and '
                           'ctx.jerry_pred_total is not None and '
                           'ctx.panel_implied_total is not None and '
                           'ctx.mc_probabilities is not None and '
                           '(ctx.mc_probabilities or {}).get("mc_mean_total") is not None and '
                           'float(ctx.jerry_pred_total) > float(ctx.close_total) + 0.5 and '
                           'float(ctx.panel_implied_total) > float(ctx.close_total) + 0.5 and '
                           'float((ctx.mc_probabilities or {}).get("mc_mean_total")) > float(ctx.close_total) + 0.5'),
        'side_expr': '"UNDER"',   # CONTRARIAN — all 3 model OVER historically loses 70% of time
        'strength_expr': '0.8',
        'hit_rate_pct': 70.0, 'sample_n': 10,
        'display_prose_template': 'triple-model overs (Jerry {jerry_pred_total}, panel {panel_implied_total}, MC {mc_probabilities.mc_mean_total}) get faded 70% — books price this in',
        'description': 'All-3 model OVER consensus. Historical 30% hit → 70% fade edge, n=10 60d MLB. side=UNDER contrarian.',
    },
    {
        'signal_key': 'jerry_panel_agree_over_fade',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': ('ctx.close_total is not None and '
                           'ctx.jerry_pred_total is not None and '
                           'ctx.panel_implied_total is not None and '
                           'float(ctx.jerry_pred_total) > float(ctx.close_total) + 0.5 and '
                           'float(ctx.panel_implied_total) > float(ctx.close_total) + 0.5'),
        'side_expr': '"UNDER"',   # CONTRARIAN
        'strength_expr': '0.6',
        'hit_rate_pct': 61.9, 'sample_n': 21,
        'display_prose_template': 'both projection models over line ({jerry_pred_total} / {panel_implied_total} vs {close_total}) — historically fades 62%',
        'description': 'Jerry+Panel OVER consensus. Historical 38.1% hit → 61.9% fade edge, n=21 60d MLB. side=UNDER.',
    },

    # -- ML: back MC+model consensus on AWAY, fade jerry+proj on HOME --
    {
        'signal_key': 'mc_model_agree_away_ml',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': ('ctx.mc_probabilities is not None and '
                           '(ctx.mc_probabilities or {}).get("mc_p_away_win") is not None and '
                           'ctx.model_pred_spread is not None and ctx.close_spread is not None and '
                           'float((ctx.mc_probabilities or {}).get("mc_p_away_win")) >= 0.55 and '
                           '(float(ctx.model_pred_spread) + float(ctx.close_spread)) < -0.5'),
        'side_expr': '"AWAY_ML"',
        'strength_expr': '0.6',
        'hit_rate_pct': 63.2, 'sample_n': 19,
        'display_prose_template': 'MC ({mc_probabilities.mc_p_away_win} away win) and spread model both like the road team',
        'description': 'MC+model spread joint AWAY lean. 63.2% n=19 60d MLB.',
    },
    {
        'signal_key': 'jerry_proj_agree_home_ml_fade',
        'class': 'model', 'market_scope': 'ml',
        'condition_expr': ('ctx.jerry_pred_spread is not None and ctx.projected_spread is not None and '
                           'ctx.close_spread is not None and '
                           '(float(ctx.jerry_pred_spread) + float(ctx.close_spread)) > 0.5 and '
                           '(float(ctx.projected_spread) + float(ctx.close_spread)) > 0.5'),
        'side_expr': '"AWAY_ML"',   # CONTRARIAN — jerry+proj HOME historically loses 71.4%
        'strength_expr': '0.6',
        'hit_rate_pct': 71.4, 'sample_n': 35,
        'display_prose_template': 'jerry + projected models both favor home team — historically fades 71%, take road ML',
        'description': 'Jerry+Proj HOME consensus. Historical 28.6% hit → 71.4% fade, n=35 60d MLB. side=AWAY_ML contrarian.',
    },

    # -- RL cover: back jerry+proj AWAY, fade jerry+proj HOME --
    {
        'signal_key': 'jerry_proj_agree_away_rl',
        'class': 'model', 'market_scope': 'rl',
        'condition_expr': ('ctx.jerry_pred_spread is not None and ctx.projected_spread is not None and '
                           'ctx.close_spread is not None and '
                           '(float(ctx.jerry_pred_spread) + float(ctx.close_spread)) < -0.5 and '
                           '(float(ctx.projected_spread) + float(ctx.close_spread)) < -0.5'),
        'side_expr': '"AWAY_RL"',
        'strength_expr': '0.7',
        'hit_rate_pct': 68.8, 'sample_n': 48,
        'display_prose_template': 'jerry + projected models both like road team by 0.5+ runs — RL cover 69%',
        'description': 'Jerry+Proj AWAY spread consensus. 68.8% n=48 60d MLB — biggest sample compound signal.',
    },
    {
        'signal_key': 'jerry_proj_agree_home_rl_fade',
        'class': 'model', 'market_scope': 'rl',
        'condition_expr': ('ctx.jerry_pred_spread is not None and ctx.projected_spread is not None and '
                           'ctx.close_spread is not None and '
                           '(float(ctx.jerry_pred_spread) + float(ctx.close_spread)) > 0.5 and '
                           '(float(ctx.projected_spread) + float(ctx.close_spread)) > 0.5'),
        'side_expr': '"AWAY_RL"',   # CONTRARIAN — home spread consensus historically covers 34%
        'strength_expr': '0.6',
        'hit_rate_pct': 65.7, 'sample_n': 35,
        'display_prose_template': 'jerry + projected both project home by 0.5+ — historically road covers 66%',
        'description': 'Jerry+Proj HOME spread consensus. Historical 34.3% cover → 65.7% fade, n=35 60d MLB. side=AWAY_RL contrarian.',
    },

    # ── TEAM-FORM + PUBLIC-DIVERGENCE PATTERNS (2026-08-26 v3 mining) ──
    # These aren't model-vs-model combos — they're TREND-vs-model, TREND-vs-
    # TREND, or MODEL-vs-PUBLIC patterns that show real edge in 60d data.
    {
        'signal_key': 'home_under_trend_at_home_solo',
        'class': 'team_form', 'market_scope': 'total',
        'condition_expr': ('ctx.home_ou_l10_at_home_unders is not None and '
                           'ctx.home_ou_l10_at_home_overs is not None and '
                           '(int(ctx.home_ou_l10_at_home_unders) + int(ctx.home_ou_l10_at_home_overs)) >= 5 and '
                           'int(ctx.home_ou_l10_at_home_unders) / '
                           '(int(ctx.home_ou_l10_at_home_unders) + int(ctx.home_ou_l10_at_home_overs)) >= 0.7'),
        'side_expr': '"UNDER"',
        'strength_expr': '0.5',
        'hit_rate_pct': 65.7, 'sample_n': 35,
        'display_prose_template': 'home team gone UNDER 70%+ in last 10 at home — historically 66%',
        'description': 'Naked home UNDER trend at home. 23-12 (65.7%) 60d MLB.',
    },
    {
        'signal_key': 'both_teams_under_trend_split',
        'class': 'team_form', 'market_scope': 'total',
        'condition_expr': ('ctx.home_ou_l10_at_home_unders is not None and '
                           'ctx.home_ou_l10_at_home_overs is not None and '
                           'ctx.away_ou_l10_on_road_unders is not None and '
                           'ctx.away_ou_l10_on_road_overs is not None and '
                           '(int(ctx.home_ou_l10_at_home_unders) + int(ctx.home_ou_l10_at_home_overs)) >= 5 and '
                           '(int(ctx.away_ou_l10_on_road_unders) + int(ctx.away_ou_l10_on_road_overs)) >= 5 and '
                           'int(ctx.home_ou_l10_at_home_unders) / '
                           '(int(ctx.home_ou_l10_at_home_unders) + int(ctx.home_ou_l10_at_home_overs)) >= 0.6 and '
                           'int(ctx.away_ou_l10_on_road_unders) / '
                           '(int(ctx.away_ou_l10_on_road_unders) + int(ctx.away_ou_l10_on_road_overs)) >= 0.6'),
        'side_expr': '"UNDER"',
        'strength_expr': '0.6',
        'hit_rate_pct': 69.7, 'sample_n': 33,
        'display_prose_template': 'both teams L10 UNDER 60%+ in their split contexts — historically 70%',
        'description': 'Both-teams UNDER trend confluence (split-adjusted). 23-10 (69.7%) 60d MLB.',
    },
    {
        'signal_key': 'away_rl_home_cover_trap',
        'class': 'team_form', 'market_scope': 'rl',
        'condition_expr': ('ctx.jerry_pred_spread is not None and ctx.close_spread is not None and '
                           '(float(ctx.jerry_pred_spread) + float(ctx.close_spread)) < -0.5 and '
                           'ctx.home_ats_l10_at_home is not None and '
                           'ctx.home_ats_l10_at_home_losses is not None and '
                           '(int(ctx.home_ats_l10_at_home) + int(ctx.home_ats_l10_at_home_losses)) >= 5 and '
                           'int(ctx.home_ats_l10_at_home) / '
                           '(int(ctx.home_ats_l10_at_home) + int(ctx.home_ats_l10_at_home_losses)) <= 0.3'),
        'side_expr': '"AWAY_RL"',
        'strength_expr': '0.7',
        'hit_rate_pct': 73.3, 'sample_n': 15,
        'display_prose_template': 'jerry likes road RL and home team cover rate at home ≤30% — 73%',
        'description': 'Jerry AWAY_RL + home fails to cover at home. 11-4 (73.3%) 60d MLB — small sample, DISCOVERY tier.',
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

    # ── RUN-LINE CLASS (derives from model spread + juice traps) ───
    # Rationale: RL isn't a native output of most models — it's derived
    # from ML strength + spread magnitude. Heavy favorites tend to cover
    # -1.5 poorly (documented juice_fav_rl_trap: -200+ favs cover 29%);
    # underdog +1.5 in tight lines is real value.
    {
        'signal_key': 'home_rl_fav_covers',
        'class': 'model', 'market_scope': 'rl',
        'condition_expr': 'ctx.model_pred_spread is not None and ctx.home_ml_close is not None and float(ctx.model_pred_spread) >= 2.0 and int(ctx.home_ml_close) > -180',
        'side_expr': '"HOME_RL"',
        'strength_expr': 'min((float(ctx.model_pred_spread) - 1.5) / 2.5, 1.0)',
        'weight_registry_key': 'v4_ml',
        'display_prose_template': 'model projects home wins by {model_pred_spread} runs and price is affordable',
        'description': 'Home favored by >= 2 runs at reasonable juice — RL -1.5 has value.',
    },
    {
        'signal_key': 'away_rl_dog_+1.5',
        'class': 'situational', 'market_scope': 'rl',
        'condition_expr': 'ctx.close_spread is not None and float(ctx.close_spread) >= -1.5 and float(ctx.close_spread) <= 1.5 and ctx.away_ml_close is not None and int(ctx.away_ml_close) > 100',
        'side_expr': '"AWAY_RL"',
        'strength_expr': '0.35',
        'display_prose_template': '{away_team} +1.5 is live — tight line means a one-run loss still pays',
        'description': 'Away dog in a tight line — +1.5 covers frequently.',
    },
    {
        'signal_key': 'juice_fav_ml_fade_rl',
        'class': 'situational', 'market_scope': 'rl',
        'condition_expr': 'ctx.home_ml_close is not None and int(ctx.home_ml_close) <= -200',
        'side_expr': '"AWAY_RL"',
        'strength_expr': '0.5',
        'display_prose_template': 'home is priced at heavy juice ({home_ml_close}) — historically -200+ favs cover -1.5 only 29% of the time',
        'description': 'Documented juice_fav_rl_trap. Take the +1.5 dog when home is a heavy fav.',
    },
    {
        'signal_key': 'juice_fav_ml_fade_rl_away',
        'class': 'situational', 'market_scope': 'rl',
        'condition_expr': 'ctx.away_ml_close is not None and int(ctx.away_ml_close) <= -200',
        'side_expr': '"HOME_RL"',
        'strength_expr': '0.5',
        'display_prose_template': 'away is priced at heavy juice ({away_ml_close}) — historically -200+ favs cover -1.5 only 29% of the time',
    },
    {
        'signal_key': 'confluence_strong_home_rl',
        'class': 'cohort', 'market_scope': 'rl',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) >= 4 and ctx.close_spread is not None and float(ctx.close_spread) <= -1.5',
        'side_expr': '"HOME_RL"',
        'strength_expr': 'min(int(ctx.signal_confluence_net) / 6.0, 1.0)',
        'display_prose_template': 'cohort confluence strong home ({signal_confluence_net}) with home laying {close_spread}',
    },
    {
        'signal_key': 'confluence_strong_away_rl',
        'class': 'cohort', 'market_scope': 'rl',
        'condition_expr': 'ctx.signal_confluence_net is not None and int(ctx.signal_confluence_net) <= -4 and ctx.close_spread is not None and float(ctx.close_spread) >= 1.5',
        'side_expr': '"AWAY_RL"',
        'strength_expr': 'min(abs(int(ctx.signal_confluence_net)) / 6.0, 1.0)',
        'display_prose_template': 'cohort confluence strong away ({signal_confluence_net}) with away laying {close_spread}',
    },

    # ── INDIVIDUAL COHORT SIGNALS (2026-08-16 pm) ──────────────────
    # From memory files: each is a documented pattern with historical
    # hit rate. Seeded UNVALIDATED — backfill_signal_tiers will replay
    # and set the real tier.
    {
        'signal_key': 'home_bp_xera_high_over',
        'class': 'cohort', 'market_scope': 'total',
        'condition_expr': 'ctx.home_bullpen_effective_era is not None and float(ctx.home_bullpen_effective_era) >= 4.5',
        'side_expr': '"OVER"',
        'strength_expr': '0.5',
        'display_prose_template': '{home_team} pen has been leaky ({home_bullpen_effective_era} effective ERA) — late innings favor OVER',
        'description': 'project_total_factor_cohorts: BP xERA>=4.5 = 64% OVER historically.',
    },
    {
        'signal_key': 'away_bp_xera_high_over',
        'class': 'cohort', 'market_scope': 'total',
        'condition_expr': 'ctx.away_bullpen_effective_era is not None and float(ctx.away_bullpen_effective_era) >= 4.5',
        'side_expr': '"OVER"',
        'strength_expr': '0.5',
        'display_prose_template': '{away_team} pen has been leaky ({away_bullpen_effective_era} effective ERA) — late innings favor OVER',
    },
    {
        'signal_key': 'heavy_home_fav_rl_fade',
        'class': 'cohort', 'market_scope': 'rl',
        'condition_expr': 'ctx.home_ml_close is not None and int(ctx.home_ml_close) <= -200',
        'side_expr': '"AWAY_RL"',
        'strength_expr': '0.6',
        'display_prose_template': 'home favored at heavy juice ({home_ml_close}) — -200+ favs cover -1.5 only 29% historically',
        'description': 'project_juice_fav_rl_trap_724: -200+ home favs cover -1.5 only 29% of the time.',
    },
    {
        'signal_key': 'heavy_away_fav_rl_fade',
        'class': 'cohort', 'market_scope': 'rl',
        'condition_expr': 'ctx.away_ml_close is not None and int(ctx.away_ml_close) <= -200',
        'side_expr': '"HOME_RL"',
        'strength_expr': '0.6',
        'display_prose_template': 'away favored at heavy juice ({away_ml_close}) — -200+ favs cover -1.5 only 29% historically',
    },
    {
        'signal_key': 'spread_delta_trap_zone',
        'class': 'cohort', 'market_scope': 'rl',
        'condition_expr': 'ctx.spread_delta is not None and 1.5 <= abs(float(ctx.spread_delta)) <= 2.0',
        'side_expr': '"AWAY_RL" if float(ctx.spread_delta) > 0 else "HOME_RL"',
        'strength_expr': '0.4',
        'display_prose_template': 'model-vs-market gap of {spread_delta} runs is a documented cover trap zone (40-43% historically)',
        'description': 'project_spread_delta_trap_zone: 1.5-2.0 delta zone covers 40-43% — fade the favored side.',
    },
    {
        'signal_key': 'ump_over_friendly',
        'class': 'umpire', 'market_scope': 'total',
        'condition_expr': 'ctx.umpire_note is not None and "over" in str(ctx.umpire_note).lower()',
        'side_expr': '"OVER"',
        'strength_expr': '0.4',
        'display_prose_template': '{umpire} runs an OVER-friendly zone — {umpire_note}',
        'description': 'Umpire-specific OVER tendency; text-matched on umpire_note.',
    },
    {
        'signal_key': 'ump_under_friendly',
        'class': 'umpire', 'market_scope': 'total',
        'condition_expr': 'ctx.umpire_note is not None and "under" in str(ctx.umpire_note).lower()',
        'side_expr': '"UNDER"',
        'strength_expr': '0.4',
        'display_prose_template': '{umpire} squeezes the zone — {umpire_note}',
    },
    {
        'signal_key': 'mc_over_high_conf',
        'class': 'model', 'market_scope': 'total',
        'condition_expr': 'ctx.mc_probabilities is not None',
        'side_expr': '"OVER"',
        'strength_expr': '0.5',
        'display_prose_template': 'simulator sees the total going OVER at high confidence',
        'description': 'Placeholder — mc_probabilities is JSON, needs custom eval. Fires cautiously.',
        'enabled': False,  # gate off until we write a proper mc_probabilities parser
    },
    {
        'signal_key': 'nrfi_prime',
        'class': 'cohort', 'market_scope': 'total',
        'condition_expr': 'ctx.nrfi_ensemble_tier == "PRIME" and ctx.nrfi_ensemble_pick == "NRFI"',
        'side_expr': '"UNDER"',
        'strength_expr': '0.35',
        'display_prose_template': 'NRFI ensemble is PRIME confidence — top 10% NRFI = 77.8% historically',
        'description': 'project_nrfi_v2_model_606: PRIME NRFI hits 77.8% top-decile.',
    },
    {
        'signal_key': 'long_rest_home_ats',
        'class': 'situational', 'market_scope': 'rl',
        'condition_expr': 'ctx.home_days_rest is not None and int(ctx.home_days_rest) >= 3',
        'side_expr': '"HOME_RL"',
        'strength_expr': '0.35',
        'display_prose_template': '{home_team} coming off {home_days_rest} days rest — long-rest home 57% ATS historically',
        'description': 'project_rest_and_lineup_signals: long-rest home teams cover 57% ATS.',
    },
    {
        'signal_key': 'home_hot_short_home_streak',
        'class': 'situational', 'market_scope': 'ml',
        'condition_expr': 'ctx.days_since_last_home_game is not None and int(ctx.days_since_last_home_game) <= 1',
        'side_expr': '"HOME_ML"',
        'strength_expr': '0.25',
        'display_prose_template': '{home_team} in the middle of a home stand — no travel today',
    },
    {
        'signal_key': 'sharp_confluence_alignment',
        'class': 'cohort', 'market_scope': 'multi',
        'condition_expr': 'ctx.signal_confluence_net is not None and abs(int(ctx.signal_confluence_net)) >= 4',
        'side_expr': '"HOME_ML" if int(ctx.signal_confluence_net) > 0 else "AWAY_ML"',
        'strength_expr': 'min(abs(int(ctx.signal_confluence_net)) / 5.0, 1.0)',
        'display_prose_template': 'cohort engine strongly leans {signal_confluence_net}',
        'description': 'Strong confluence >= 4 in either direction — was documented VALIDATED at 62.8% n=94 for home lean.',
    },
    {
        'signal_key': 'batter_babip_extreme_home',
        'class': 'offense', 'market_scope': 'total',
        'condition_expr': 'ctx.home_team_babip_l14 is not None and float(ctx.home_team_babip_l14) > 0.320',
        'side_expr': '"UNDER"',
        'strength_expr': '0.3',
        'display_prose_template': '{home_team} due to cool off — L14 BABIP {home_team_babip_l14} above sustainable',
        'description': 'Regression signal: high BABIP is unsustainable.',
    },
    {
        'signal_key': 'batter_babip_extreme_away',
        'class': 'offense', 'market_scope': 'total',
        'condition_expr': 'ctx.away_team_babip_l14 is not None and float(ctx.away_team_babip_l14) > 0.320',
        'side_expr': '"UNDER"',
        'strength_expr': '0.3',
        'display_prose_template': '{away_team} due to cool off — L14 BABIP {away_team_babip_l14} above sustainable',
    },
    {
        'signal_key': 'pitcher_l3_matches_xera',
        'class': 'pitcher', 'market_scope': 'total',
        'condition_expr': 'ctx.home_sp_xera is not None and ctx.home_pitcher_last_3_era is not None and abs(float(ctx.home_sp_xera) - float(ctx.home_pitcher_last_3_era)) < 0.75',
        'side_expr': '"UNDER" if float(ctx.home_sp_xera) < 3.75 else "OVER"',
        'strength_expr': '0.3',
        'display_prose_template': '{home_pitcher} L3 ERA matches his stuff (xERA {home_sp_xera}) — no regression bias',
    },

    # ── TEAM ATS / O/U TENDENCY (2026-08-16 pm) ─────────────────────
    # User's manual NCAAB system: mine per-team ATS + O/U patterns from
    # L10-L20 history + this-season splits. These signals replicate that
    # process across sports. Field-dependent — will fire when
    # mlb_game_context has these columns populated (some may need
    # backfill_team_tendencies.py, TBD).
    {
        'signal_key': 'home_team_ats_hot',
        'class': 'team_form', 'market_scope': 'rl',
        'condition_expr': 'ctx.home_ats_last10 is not None and int(ctx.home_ats_last10) >= 7',
        'side_expr': '"HOME_RL"',
        'strength_expr': 'min((int(ctx.home_ats_last10) - 5) / 4.0, 1.0)',
        'display_prose_template': '{home_team} covering ATS {home_ats_last10}-{home_ats_last10_losses} L10 — hot cover trend',
        'description': "User's manual system: teams on ATS heaters keep covering.",
    },
    {
        'signal_key': 'home_team_ats_cold',
        'class': 'team_form', 'market_scope': 'rl',
        'condition_expr': 'ctx.home_ats_last10 is not None and int(ctx.home_ats_last10) <= 3',
        'side_expr': '"AWAY_RL"',
        'strength_expr': 'min((5 - int(ctx.home_ats_last10)) / 4.0, 1.0)',
        'display_prose_template': '{home_team} only covering ATS {home_ats_last10}-{home_ats_last10_losses} L10 — cold ATS',
    },
    {
        'signal_key': 'away_team_ats_hot',
        'class': 'team_form', 'market_scope': 'rl',
        'condition_expr': 'ctx.away_ats_last10 is not None and int(ctx.away_ats_last10) >= 7',
        'side_expr': '"AWAY_RL"',
        'strength_expr': 'min((int(ctx.away_ats_last10) - 5) / 4.0, 1.0)',
        'display_prose_template': '{away_team} covering ATS {away_ats_last10}-{away_ats_last10_losses} L10 — hot cover trend',
    },
    {
        'signal_key': 'home_team_over_trend',
        'class': 'team_form', 'market_scope': 'total',
        'condition_expr': 'ctx.home_ou_last10_overs is not None and int(ctx.home_ou_last10_overs) >= 7',
        'side_expr': '"OVER"',
        'strength_expr': 'min((int(ctx.home_ou_last10_overs) - 5) / 4.0, 1.0)',
        'display_prose_template': '{home_team} games going OVER {home_ou_last10_overs}/10 recently',
    },
    {
        'signal_key': 'home_team_under_trend',
        'class': 'team_form', 'market_scope': 'total',
        'condition_expr': 'ctx.home_ou_last10_overs is not None and int(ctx.home_ou_last10_overs) <= 3',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((5 - int(ctx.home_ou_last10_overs)) / 4.0, 1.0)',
        'display_prose_template': '{home_team} games staying UNDER {home_ou_last10_overs}/10 overs recently',
    },
    {
        'signal_key': 'away_team_over_trend',
        'class': 'team_form', 'market_scope': 'total',
        'condition_expr': 'ctx.away_ou_last10_overs is not None and int(ctx.away_ou_last10_overs) >= 7',
        'side_expr': '"OVER"',
        'strength_expr': 'min((int(ctx.away_ou_last10_overs) - 5) / 4.0, 1.0)',
        'display_prose_template': '{away_team} games going OVER {away_ou_last10_overs}/10 recently',
    },
    {
        'signal_key': 'away_team_under_trend',
        'class': 'team_form', 'market_scope': 'total',
        'condition_expr': 'ctx.away_ou_last10_overs is not None and int(ctx.away_ou_last10_overs) <= 3',
        'side_expr': '"UNDER"',
        'strength_expr': 'min((5 - int(ctx.away_ou_last10_overs)) / 4.0, 1.0)',
        'display_prose_template': '{away_team} games staying UNDER {away_ou_last10_overs}/10 overs recently',
    },
    {
        'signal_key': 'home_covers_as_fav',
        'class': 'team_form', 'market_scope': 'rl',
        'condition_expr': 'ctx.home_covers_as_fav_pct is not None and float(ctx.home_covers_as_fav_pct) >= 60 and ctx.close_spread is not None and float(ctx.close_spread) < 0',
        'side_expr': '"HOME_RL"',
        'strength_expr': '0.4',
        'display_prose_template': '{home_team} covers as favorite {home_covers_as_fav_pct}% this season',
    },
    {
        'signal_key': 'away_covers_as_dog',
        'class': 'team_form', 'market_scope': 'rl',
        'condition_expr': 'ctx.away_covers_as_dog_pct is not None and float(ctx.away_covers_as_dog_pct) >= 60 and ctx.close_spread is not None and float(ctx.close_spread) < 0',
        'side_expr': '"AWAY_RL"',
        'strength_expr': '0.4',
        'display_prose_template': '{away_team} covers as underdog {away_covers_as_dog_pct}% this season',
    },
    {
        'signal_key': 'home_fades_own_ml_hot',
        'class': 'team_form', 'market_scope': 'ml',
        'condition_expr': 'ctx.home_ml_last10 is not None and int(ctx.home_ml_last10) >= 7',
        'side_expr': '"HOME_ML"',
        'strength_expr': 'min((int(ctx.home_ml_last10) - 5) / 4.0, 1.0)',
        'display_prose_template': '{home_team} {home_ml_last10}-{home_ml_last10_losses} straight up L10 — hot form',
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
