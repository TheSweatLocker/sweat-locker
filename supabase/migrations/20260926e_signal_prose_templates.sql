-- 2026-09-26e · Fix four user-facing signal prose templates.
--
-- These live in signal_sources.display_prose_template, i.e. in the
-- DATABASE, so the edits made while working Andy's QA lists are not
-- captured by git and would be lost on any environment rebuild or
-- restore. Recording them as a migration so the fix is reproducible.
-- Idempotent: each statement sets an exact final value.
--
-- ── 1-3 · a projection is not an edge ────────────────────────────────
-- Andy, on KC @ MIA: "'edge' is the wrong word — -8.46 against a -10
-- line is a margin projection that says KC doesn't cover, not an
-- 8.83-point edge."
--
-- Three spread templates called the model's own margin an "edge", while
-- the TOTAL templates sitting beside them already did it correctly by
-- naming both the projection and the market. Now they match: state both
-- numbers and let the reader see the difference, which also makes any
-- drift between the prose snapshot and the live field visible instead
-- of silent.

UPDATE public.signal_sources
   SET display_prose_template = 'V3 model projects {v3_spread} vs market {close_spread}'
 WHERE signal_key = 'v3_spread_nfl';

UPDATE public.signal_sources
   SET display_prose_template = 'V4 projects {v4_spread} vs market {close_spread}'
 WHERE signal_key = 'v4_spread_nfl';

UPDATE public.signal_sources
   SET display_prose_template = 'V4 model projects {model_pred_spread} runs vs market {close_spread}'
 WHERE signal_key = 'v4_model_spread';

-- ── 4 · two scores are not a range ───────────────────────────────────
-- Andy: "External panel sees 16.79-24.08 — a score range with no team
-- labels that matches neither side of the predicted score."
--
-- It was never a range. Those are two projected SCORES — panel_pred_home_pts
-- and panel_pred_away_pts — printed unlabelled and joined by a hyphen,
-- so "16.79-24.08" read as an interval. Worse, it led with the HOME
-- value while the card header and every other surface in the app order
-- away-then-home (the card is "KC @ MIA"), so even a reader who guessed
-- they were scores would attach them to the wrong teams.
--
-- home_team / away_team are already abbreviations on the NFL context
-- row (MIA / KC), so labelling costs almost no width:
--     before  external panel sees 16.79-24.08
--     after   external panel sees KC 24.08, MIA 16.79

UPDATE public.signal_sources
   SET display_prose_template = 'external panel sees {away_team} {panel_pred_away_pts}, {home_team} {panel_pred_home_pts}'
 WHERE signal_key = 'panel_pred_spread';

NOTIFY pgrst, 'reload schema';
