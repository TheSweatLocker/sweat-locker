-- display_labels: pre-translated, user-facing text for UFC picks (2026-08-08)
--
-- Root cause: app was showing raw analyst shorthand ("OVER DIST",
-- "@ 1.33", "PRIME 90%") that even the founder had to ask what they
-- meant. Per feedback_backside_dictates_app_renders — translation
-- lives server-side, app renders verbatim.
--
-- Shape (populated by ufc_display_labels.py):
--   {
--     "odds_a": "1.65 (-154)",              — decimal + American
--     "odds_b": "2.35 (+135)",
--     "method_breakdown": "KO 41% · SUB 25% · Decision 34%",
--     "distance": "Goes to Decision (58%)",  — plain English
--     "rounds": "R1 48% · R2 29% · R3 22%",
--     "conviction_badge": "PRIME · 85% model win",
--     "action_badge": "BACK Nurgozhay (+26pp edge)",  — actionable
--     "recommended_odds": "1.65 (-154)",
--     "recommended_fighter": "Diyar Nurgozhay",
--   }

ALTER TABLE public.ufc_picks
  ADD COLUMN IF NOT EXISTS display_labels JSONB;

CREATE INDEX IF NOT EXISTS ufc_picks_display_labels_idx
  ON public.ufc_picks USING gin (display_labels);

NOTIFY pgrst, 'reload schema';
