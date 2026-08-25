-- Expand prop playbook signal set (2026-08-17) — 12 new signals.
--
-- Reads from the `signals` JSONB flag bag that generate_props.py already
-- writes on every prop row. Each legacy narrative flag becomes a
-- plug-in signal — no changes needed to generate_props.
--
-- Depends on: 20260817_prop_playbook_infra.sql
--
-- These plus the 3 POC signals give the playbook 15 total. Multi-signal
-- props can now clear STRONG (need score>=1.2, classes>=2, margin>=0.35)
-- and PRIME (score>=2.0, classes>=3, margin>=0.6) without any change
-- to the scorer's tier framework.

DELETE FROM public.signal_sources
 WHERE sport IN ('MLB', 'NFL')
   AND class IN ('prop_form', 'prop_matchup', 'prop_trend', 'prop_environment')
   AND origin = 'SEEDED_EXPAND_817';

INSERT INTO public.signal_sources
  (signal_key, sport, class, market_scope, subject_scope,
   condition_expr, side_expr, strength_expr,
   display_prose_template, description, enabled, origin)
VALUES
  -- ── BATTER form signals ──────────────────────────────────────────
  ('batter_l7_hot', 'MLB', 'prop_trend', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and ("l7_hot" in p["signals"] or "l7_avg_hot" in p["signals"])',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.6',
   'batter L7 heater — legacy scorer flagged {signals}',
   'Batter got a hit in 6+ of last 7 games (86%+)',
   true, 'SEEDED_EXPAND_817'),

  ('batter_l7_cold', 'MLB', 'prop_trend', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and ("l7_cold" in p["signals"] or "l7_slump" in p["signals"] or "hitless_streak" in p["signals"])',
   '"BACK" if p.get("direction") == "under" else "FADE"',
   '0.5',
   'batter L7 cold — legacy scorer flagged slump',
   'Batter in a hitless slump — L7 got-hit rate ≤ 40%',
   true, 'SEEDED_EXPAND_817'),

  ('batter_l14_heat', 'MLB', 'prop_trend', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and "l14_heat" in p["signals"]',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.7',
   'L14 wRC+ heater — quality contact confirmed',
   'L14 wRC+ 40+ points above season baseline — real quality bump',
   true, 'SEEDED_EXPAND_817'),

  ('batter_l14_cold', 'MLB', 'prop_trend', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and "l14_cold" in p["signals"]',
   '"BACK" if p.get("direction") == "under" else "FADE"',
   '0.6',
   'L14 wRC+ collapse — quality contact down',
   'L14 wRC+ 40+ points BELOW season baseline — real slump',
   true, 'SEEDED_EXPAND_817'),

  -- ── PITCHER form signals ────────────────────────────────────────
  ('pitcher_bb_elite', 'MLB', 'prop_trend', 'pitcher', 'prop',
   'isinstance(p.get("signals"), dict) and "bb_rate" in p["signals"] and p.get("prop_type", "").startswith("bb_")',
   '"BACK" if p.get("direction") == "under" else "FADE"',
   '0.7',
   'pitcher elite command — L7 BB/9 ≤ 1.5',
   'Legacy flagged elite command (L7 BB/9 ≤ 1.5) — backs BB unders',
   true, 'SEEDED_EXPAND_817'),

  ('pitcher_l5_confirm', 'MLB', 'prop_trend', 'pitcher', 'prop',
   'isinstance(p.get("signals"), dict) and "l5_confirm" in p["signals"]',
   '"BACK"',
   '0.5',
   'L5 confirms direction — matches prop-line side in most recent 5 starts',
   'Legacy L5 confirmation flag — direction matches recent form',
   true, 'SEEDED_EXPAND_817'),

  ('pitcher_clean_start', 'MLB', 'prop_trend', 'pitcher', 'prop',
   'isinstance(p.get("signals"), dict) and "clean_start" in p["signals"] and p.get("prop_type", "").startswith("bb_")',
   '"BACK" if p.get("direction") == "under" else "FADE"',
   '0.5',
   'clean-start pitcher — pounds the zone early',
   '1st-inn WHIP ≤ 1.10 — attacks zone, low BB probability',
   true, 'SEEDED_EXPAND_817'),

  ('pitcher_last7_control', 'MLB', 'prop_trend', 'pitcher', 'prop',
   'isinstance(p.get("signals"), dict) and "last7_control" in p["signals"] and p.get("prop_type", "").startswith("bb_")',
   '"BACK" if p.get("direction") == "under" else "FADE"',
   '0.5',
   'last-7 starts control lock — projection under book',
   'Blended L7 BB projection meaningfully under book line',
   true, 'SEEDED_EXPAND_817'),

  -- ── MATCHUP signals ─────────────────────────────────────────────
  ('bvp_batter_owns', 'MLB', 'prop_matchup', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and "bvp_mastery" in p["signals"]',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.7',
   'batter owns this pitcher — {signals}',
   'Career BvP OPS ≥ 1.000 on 8+ AB — real mastery',
   true, 'SEEDED_EXPAND_817'),

  ('opp_starter_weak', 'MLB', 'prop_matchup', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and "opp_starter" in p["signals"] and ("very soft" in str(p["signals"].get("opp_starter", "")) or "below avg" in str(p["signals"].get("opp_starter", "")))',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.6',
   'opposing starter is soft — {signals}',
   'Opp starter xERA above 4.5 = below-avg matchup for hitter',
   true, 'SEEDED_EXPAND_817'),

  ('opp_bullpen_weak', 'MLB', 'prop_matchup', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and "opp_bullpen" in p["signals"] and "soft pen" in str(p["signals"].get("opp_bullpen", ""))',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.4',
   'opposing bullpen soft — late-inning AB upside',
   'Opp BP ERA ≥ 4.30 — batter gets late-inning AB against soft arms',
   true, 'SEEDED_EXPAND_817'),

  ('opp_form_trending_wrong', 'MLB', 'prop_matchup', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and "opp_form" in p["signals"] and "trending wrong way" in str(p["signals"].get("opp_form", ""))',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.5',
   'opp pitcher L3 ERA blown out — form trending wrong way',
   'Opp starter L3 ERA above 6.0 — recent form terrible',
   true, 'SEEDED_EXPAND_817'),

  -- ── ENVIRONMENT signals ─────────────────────────────────────────
  ('park_hitter_friendly', 'MLB', 'prop_environment', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and "park" in p["signals"] and "hitter friendly" in str(p["signals"].get("park", ""))',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.4',
   'hitter-friendly park — {signals}',
   'Park factor ≥ 107 — boosts hits_over probability',
   true, 'SEEDED_EXPAND_817'),

  ('lineup_spot_heart', 'MLB', 'prop_environment', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and "lineup_spot" in p["signals"] and "heart of order" in str(p["signals"].get("lineup_spot", ""))',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.3',
   'heart of order — 4+ PA game',
   'Batting 3-5 gets guaranteed 4+ PAs, materially boosts hits over',
   true, 'SEEDED_EXPAND_817'),

  ('lineup_spot_top', 'MLB', 'prop_environment', 'hits', 'prop',
   'isinstance(p.get("signals"), dict) and "lineup_spot" in p["signals"] and "leadoff" in str(p["signals"].get("lineup_spot", ""))',
   '"BACK" if p.get("direction") == "over" else "FADE"',
   '0.3',
   'top of order — 4-5 PA game',
   'Batting 1-2 gets 4-5 PAs, boosts hits over',
   true, 'SEEDED_EXPAND_817');

NOTIFY pgrst, 'reload schema';
