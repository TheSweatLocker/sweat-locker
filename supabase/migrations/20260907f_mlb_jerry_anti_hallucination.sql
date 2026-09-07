-- 2026-09-07: MLB Jerry anti-hallucination rules — mirror of the NFL
-- rules shipped in 20260907d. Ports the same "consume pre_parsed_facts
-- verbatim, don't re-derive from raw signed fields" contract to MLB.
--
-- Motivation: user complaint 9/7 evening — MLB game write-ups reading
-- short + thin, primary_play.sub often collapses to "Supervised total
-- model backs Under · 60% confidence" with no supporting data. Root:
-- Jerry saw raw ML/spread fields + primary_play.sub and just paraphrased
-- the sub instead of pulling data from struct.pitchers / struct.confluence
-- / struct.situational.
--
-- Companion generator update (generate_mlb_game_reads.py) adds a
-- pre_parsed_facts block with English strings for moneyline_verbatim,
-- model_favors, edge_side, total_canonical + a primary_play_sub_is_thin
-- flag that fires when sub is a supervised-model stub. This migration
-- appends rules to the MLB game_read_rules template so Jerry knows to
-- consume the parsed block + pull specific signals when the sub is thin.
--
-- Idempotent — safe to re-run.

UPDATE public.prompt_templates
   SET template = template || E'\n\n' ||
'ANTI-HALLUCINATION RULES (2026-09-07 · consumes struct.pre_parsed_facts):

When struct.pre_parsed_facts is present, IT is the source of truth for
directional numbers. Every citation of a spread, moneyline, projected
margin, or projected total in your prose MUST come from pre_parsed_facts.

- moneyline_verbatim → quote this EXACT string. Do NOT round -175 to
  -180, do NOT round +145 to +150.
- moneyline_favorite / moneyline_dog → use these to identify the fav/dog.
  Do NOT infer from spread sign — the fields are already parsed.
- model_favors → this string tells you which team the model projects to
  win + by how much. If your prose starts with "the model has X ahead",
  X MUST be the team named in model_favors.
- edge_side → this is the disagreement between model and market, already
  computed. Cite it as-is.
- total_canonical → cite THIS as the model total. Do NOT double-cite
  V3, V4, and Jerry totals as if they were the same number.
- total_market_delta → the pre-computed OVER/UNDER lean vs market.

CRITICAL — primary_play_sub_is_thin handling:

When struct.pre_parsed_facts.primary_play_sub_is_thin is present, it
means the pipeline built primary_play.sub as a supervised-model summary
(e.g. "Supervised total model backs Under · 100% confidence"). This
alone is NOT enough context for a write-up.

DO NOT: paraphrase the supervised-model output as the entire read
  (BAD: "The supervised model backs the under at 100% confidence.
  That is the take.")

INSTEAD: pull specific data from struct.pitchers, struct.situational,
struct.confluence.breakdown, struct.buy_down_play, and struct.recent_form.
Every read on a thin-sub game MUST include AT LEAST:
  - Named pitcher(s) with a specific stat (xERA, L3 ERA, K rate, etc.)
  - Opposing lineup context (wRC+ vs pitcher hand, K rate, etc.)
  - Environmental factor (park, weather, umpire) IF present in struct
  - One matchup or trend detail from struct.buy_down_play or
    struct.confluence.breakdown

If a required data source is missing from the struct, acknowledge the
gap explicitly ("model data is thin on this game — leaning on market
priors") rather than padding with generic content.'
 WHERE name = 'game_read_rules'
   AND sport = 'MLB'
   AND is_active = true
   AND template NOT LIKE '%ANTI-HALLUCINATION RULES (2026-09-07%';

NOTIFY pgrst, 'reload schema';
