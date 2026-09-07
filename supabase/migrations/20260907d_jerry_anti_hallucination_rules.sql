-- 2026-09-07: Jerry anti-hallucination rules — user audit found 4 recurring
-- bugs on NFL game reads (BAL@IND spot check reproduced 3):
--
--   1. Team direction FLIPPED — "model's pulling Baltimore ahead by 2.38"
--      when projected_spread=2.38 (H+/A- convention) actually meant IND ahead.
--      LLM had to infer sign convention on its own → wrong ~15% of the time.
--
--   2. ML odds fabricated — "money line (-180 Ravens, +150 Colts)" when
--      actual close was -175/+145. Blast radius: 6 of 16 NFL reads on/after
--      9/1 (37.5%) cite ML odds that don't match ctx.close_home_ml/close_away_ml.
--      Pattern: rounding to nearest 5 or 10.
--
--   3. Dual-total citation — cites both projected_total AND panel_pred_total
--      as if they were the same number (WAS@PHI reproduced: 49.17 AND 43.55
--      in same read).
--
--   4. Sweat vs pick tier conflation — game sweat_score PRIME confused with
--      primary_play tier (which can be LOW/COVERAGE after juice caps). Jerry
--      says "PRIME territory" then cites conv=54 (which is LOW).
--
-- Root cause: raw signed numbers + no cite discipline. Fix: the generator now
-- passes a `pre_parsed_facts` block with English-language directional facts
-- (see generate_nfl_game_reads.py build_struct). This migration updates the
-- NFL rules template to REQUIRE Jerry consume pre_parsed_facts verbatim
-- instead of re-deriving from raw signed fields.
--
-- Applies to NFL only (Phase 1). MLB + NCAAF templates get the same
-- treatment in follow-up migrations after the generator functions are ported.
--
-- Idempotent — safe to re-run.

UPDATE public.prompt_templates
   SET template = template || E'\n\n' ||
'ANTI-HALLUCINATION RULES (2026-09-07 · consumes struct.pre_parsed_facts):

When struct.pre_parsed_facts is present, IT is the source of truth for
directional numbers. Every citation of a spread, moneyline, projected
margin, or projected total in your prose MUST come from pre_parsed_facts.

- moneyline_verbatim → quote this EXACT string. Do NOT round -175 to -180,
  do NOT round +145 to +150. If pre_parsed_facts says "BAL -175 / IND +145",
  your prose says "-175/+145" and nothing else.

- moneyline_favorite / moneyline_dog → use these to identify which team is
  favored / dog. Do NOT infer from spread sign — the fields are already
  parsed.

- market_favors → this string tells you who the MARKET has as favorite +
  by how much. Cite it directly. DO NOT compute "market has X at -3.5"
  from raw spread numbers.

- model_favors → this string tells you which team the MODEL projects to
  win + by how much. If your prose starts with "the model has X ahead", X
  MUST be the team named in model_favors. Getting this wrong means saying
  the opposite of what the model actually says.

- edge_side → this is the disagreement between model and market, pre-computed.
  Cite it as-is. If edge_side says "IND — model favors them by 5.88 more
  points than market", write "the model likes IND by nearly 6 points more
  than the market has them", not the reverse.

- total_canonical → cite THIS as the model total. If total_secondary_lens
  is also present, EITHER stick with canonical only, OR quote both with
  their labels (EPA-matchup X · Panel Y). NEVER cite both numbers as if
  they were one projection.

- tier_note → when present, this warns that GAME sweat and PICK tier
  disagree. Your prose MUST acknowledge both. Do not say "PRIME game →
  strong play" when the pick tier is LOW/COVERAGE.

If a required field is missing from pre_parsed_facts (edge_side absent,
model_favors null), acknowledge the gap in prose — do NOT fabricate a
substitute. Say "the game model is thin here, working from market only".'
 WHERE name = 'game_read_rules'
   AND sport = 'NFL'
   AND is_active = true
   AND template NOT LIKE '%ANTI-HALLUCINATION RULES (2026-09-07%';

NOTIFY pgrst, 'reload schema';
