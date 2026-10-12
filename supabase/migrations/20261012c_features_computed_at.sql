-- 2026-10-12 · features_computed_at: one column, one writer, one meaning.
--
-- WHY. "When were this row's features last computed" has been unanswerable,
-- and that is why the upcoming slate sat on LAST SEASON's team stats for
-- months without anyone noticing (B79: 364 of 388 team-slots, 94%).
--
-- Every existing candidate is overloaded or dead:
--
--   updated_at          written by nfl_game_context.py since 2026-10-07 to
--                       mean "features recomputed" AND by
--                       recompute_nfl_primary_play.py since 2026-09-30 to
--                       mean "pick recomputed", plus five other narrow
--                       PATCHers (mc_simulator, ml_logreg_predict,
--                       v3_regression, v4_inference, refresh_nfl_context_qb).
--                       It therefore means "last touched by anybody", which
--                       is how rows stamped 2026-10-07 and 2026-10-08 still
--                       held July values.
--   computed_at         INSERT-time only. Rows re-enriched on 2026-10-12
--                       with correct current-season penalty figures (8.2,
--                       9.8, 9.2) still read computed_at = 2026-07-28.
--   team_tendencies_updated_at   NULL on every row (one old backfill).
--   team_trends_updated_at       NULL on every row.
--   team_form_enriched_at        a DIFFERENT enrichment block, so it reads
--                                fresh on rows whose blended stats are
--                                months old.
--
-- Both overloads were added deliberately and both are defensible on their
-- own. The lesson is not that either was wrong — it is that a timestamp
-- shared by two writers with different meanings is not a timestamp.
--
-- CONTRACT: ONLY the full enrichment in nfl_game_context.py writes this,
-- and it writes it on every row it recomputes. A narrow PATCHer must never
-- touch it. If that rule is broken this column becomes updated_at again.
--
-- NOTE this does not replace the value-level check in
-- watchdogs.check_context_features_preseason, which compares the STORED
-- NUMBER against a recomputable ground truth (season total / games played).
-- That remains the authority, because it cannot be fooled by a timestamp
-- that was written without the content changing — exactly the failure being
-- fixed here. This column makes freshness a cheap lookup for the other ~140
-- consumers that cannot afford to recompute ground truth.

ALTER TABLE nfl_game_context
    ADD COLUMN IF NOT EXISTS features_computed_at TIMESTAMPTZ;

COMMENT ON COLUMN nfl_game_context.features_computed_at IS
  'When the blended team-stat features on this row were last recomputed by the FULL nfl_game_context enrichment. Written by that job only — never by a narrow PATCH. updated_at means "last touched by any writer" and cannot be used for this. Ground truth remains the value-level comparison in watchdogs.check_context_features_preseason.';

NOTIFY pgrst, 'reload schema';
