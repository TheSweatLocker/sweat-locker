-- Jerry byline — adds "— Jerry" attribution to all model-generated outputs.
--
-- Voice doc (docs/jerry_voice.md) defined the persona; this migration
-- repeats his name on every output so users build the Pavlovian association:
-- "this read is from Jerry." Cheap, additive, no new surfaces, no redundancy
-- with the existing record tracking.
--
-- Three templates updated:
--   - game_read_universal: byline added as a final BYLINE block. Placed
--     AFTER the narrative sign-off (e.g. "That's the read.") on a new line
--     because it's attribution, not the close.
--   - best_prop_blurb: append " — Jerry" after the play sentence.
--   - pick_recap: append " — Jerry" after the result emoji.
--
-- Already applied to live Supabase via PATCH. This migration is the
-- repo source-of-truth for fresh-DB reproduction.

-- 1) game_read_universal — BYLINE block appended
UPDATE prompt_templates
SET template = template || E'\n\nBYLINE (added 2026-05-25):\n'
    || E'- After the final section/sentence of the read, on a new line, add the byline: "— Jerry"\n'
    || E'- This appears AFTER the narrative sign-off (e.g. "That''s the read.") — it''s the attribution, not the close.\n'
    || E'- Always present, always exactly "— Jerry" (em dash, space, Jerry). No variants.'
WHERE name = 'game_read_universal' AND sport = 'ALL'
  AND template NOT LIKE '%BYLINE (added 2026-05-25)%';

-- 2) best_prop_blurb — append byline instruction to the play-ending rule
UPDATE prompt_templates
SET template = REPLACE(
    template,
    'End with the specific play.',
    'End with the specific play, then append '' — Jerry'' as the byline.'
)
WHERE name = 'best_prop_blurb' AND sport = 'ALL'
  AND template NOT LIKE '%append '' — Jerry'' as the byline%';

-- 3) pick_recap — append byline after emoji rule
UPDATE prompt_templates
SET template = REPLACE(
    template,
    'End with 🔒 if Win, no emoji if Loss.',
    'End with 🔒 if Win, no emoji if Loss. Then append '' — Jerry'' as the final attribution on the same line.'
)
WHERE name = 'pick_recap' AND sport = 'ALL'
  AND template NOT LIKE '%append '' — Jerry'' as the final attribution%';

NOTIFY pgrst, 'reload schema';
