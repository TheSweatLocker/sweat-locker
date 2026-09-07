-- 2026-09-07: NCAAF Jerry anti-hallucination rules — mirror of NFL
-- (20260907d) and MLB (20260907f). Ports the "consume pre_parsed_facts
-- verbatim, don't re-derive from raw signed fields" contract to NCAAF.
--
-- Companion generator update (generate_ncaaf_game_reads.py) adds a
-- pre_parsed_facts block with English strings for moneyline_verbatim,
-- model_favors, edge_side, total_canonical. Render hoists them to a
-- CONFIRMED FACTS block at top of context + redacts raw ML/spread from
-- the JSON dump so Jerry can only cite parsed facts.
--
-- Same fixes as NFL: direction flips, ML odds paraphrasing, dual-total
-- citation, sign-convention guessing. Ships pre-emptively for NCAAF
-- since Week 2+ is when tier calibration matures + Jerry synthesis
-- volume goes up, so getting the anti-hallucination layer in place
-- before the volume ramp saves an audit cycle.
--
-- Idempotent — safe to re-run.

UPDATE public.prompt_templates
   SET template = template || E'\n\n' ||
'ANTI-HALLUCINATION RULES (2026-09-07 · consumes struct.pre_parsed_facts):

When struct.pre_parsed_facts is present, IT is the source of truth for
directional numbers. Every citation of a spread, moneyline, projected
margin, or projected total in your prose MUST come from pre_parsed_facts.

- moneyline_verbatim → quote this EXACT string. Do NOT round.
- moneyline_favorite / moneyline_dog → use these to identify fav/dog.
- market_spread_verbatim / market_favors → cite these; do NOT re-derive
  from raw close_spread.
- model_favors → tells you which team the model projects to win. If
  your prose says "the model has X ahead", X MUST match model_favors.
- model_favors_ambiguous → the projected_spread is present but there
  are no per-team scores to disambiguate. Quote raw + say "thin model
  coverage" rather than picking a direction.
- edge_side → the pre-computed disagreement between model and market.
  Cite as-is.
- total_canonical → cite THIS as the model total; do NOT double-cite
  projected + panel as if they were one number.
- total_market_delta → the pre-computed OVER/UNDER lean vs market.

If a required field is missing from pre_parsed_facts, acknowledge the
gap explicitly ("model coverage thin here, working from market only")
rather than fabricating a substitute.'
 WHERE name = 'game_read_rules'
   AND sport = 'NCAAF'
   AND is_active = true
   AND template NOT LIKE '%ANTI-HALLUCINATION RULES (2026-09-07%';

NOTIFY pgrst, 'reload schema';
