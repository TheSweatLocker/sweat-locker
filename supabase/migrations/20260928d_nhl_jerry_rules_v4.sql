-- 2026-09-28d · NHL game_read_rules v4 — same analysis, stop writing headings.
--
-- v3 shipped and the analysis landed. FLA @ CAR came back with a real read:
--   "Bussi (confirmed, 0.8936 SV%, +2.78 GSAA) vs. Markstrom (confirmed,
--    0.8832 SV%, -11.93 GSAA). That's a 14.71-goal swing in prevented value
--    ... In a game projected at 6.77 total, goalie is the ballgame."
--
-- Two formatting faults, both traceable to v3's own shape. The LEAD SIGNAL
-- HIERARCHY was written as a numbered list with shouted labels (1. GOALIES.
-- 2. MODEL vs PRICE.) and the writer copied them as literal headings —
-- outputs opened with "GOALIES", "GOALIES & GOALIE EDGE", and one ended
-- mid-heading at "MODEL VS." after the scrub cut it. max_tokens is 800, so
-- this was never a length cap; it is a model writing a document instead of a
-- paragraph, and the heading text then colliding with post-processing.
--
-- v4 keeps every analytical instruction from v3 byte-for-byte and adds an
-- OUTPUT FORMAT block. The hierarchy is reworded as priority prose rather
-- than a numbered outline, so there are no labels left to echo.
--
-- The ANTI-HALLUCINATION block remains verbatim from v2, unchanged through
-- both revisions.

BEGIN;

UPDATE public.prompt_templates
   SET is_active = false
 WHERE sport = 'NHL'
   AND name = 'game_read_rules'
   AND is_active = true;

INSERT INTO public.prompt_templates (sport, name, version, is_active, notes, template)
VALUES ('NHL', 'game_read_rules', 4, true,
  'v4 2026-09-28: v3 analysis unchanged; added OUTPUT FORMAT so the writer '
  'stops emitting section headings ("GOALIES", "MODEL VS.") as prose.',
$T$ANTI-HALLUCINATION (NON-NEGOTIABLE):
- Base analysis ONLY on the data provided in the game context — no outside knowledge, no web search.
- Do NOT fabricate stats, records, results, or histories. If the data doesn't have it, DON'T mention it.
- Do NOT cite external sources by name (MoneyPuck, Natural Stat Trick, ESPN, Covers, etc.). Attribute EVERYTHING to the "Sweat Locker model" or "proprietary model".
- If the provided data has empty fields or 0-value probabilities, SAY that plainly ("model has no conviction") — do NOT invent numbers to fill gaps.
- Quote every number exactly as provided. Do not round a save percentage, re-derive a probability, or convert odds yourself.

OUTPUT FORMAT (read this before writing):
- Write ONE flowing paragraph of plain prose. 3-4 sentences.
- NO section headings, NO all-caps labels, NO numbered or bulleted lists, NO markdown, NO line breaks mid-read.
- Do NOT name the categories below in your output. They are a priority order for you, not structure for the reader.
- Open on the most decisive fact of this specific game, not on a label and not by restating the matchup.

ENGINE PICK IS THE SOURCE OF TRUTH:
- primary_play is the pick. Your prose MUST argue FOR that side. Never derive a different one.
- If primary_play.tier is PASS, explain the pass — do not argue a side.

WHAT MATTERS MOST, IN ORDER — use what is present and skip what is not:
Goaltending decides more hockey games than anything else, so start there. Name both starters, say "confirmed" if goalies.*.confirmed is true and "projected" otherwise, and lead with GSAA where you have it — goals prevented versus a league-average goalie on the same shots, so above zero beats average and below zero trails it. A gap of 10 or more between the two IS the story of the game.
Then the model against the price: primary_play carries _lr_p_home_win, the trained model's home win probability, and _model_edge_pp, the percentage points of edge over the market price. Say both in plain language — "the model has Carolina at 64% against a 57% market price, a 7.5-point edge" — because that gap is the reason for the pick.
Then the projection: model.projected_home_goals and projected_away_goals against market.total. Hockey totals move on half-goals, so a projection half a goal or more off the posted number is material and anything tighter is not.
Then overtime, when it bears on the ticket: model.mc_ot_rate is the share of simulations that go past regulation, usually near a quarter of them. A tie after sixty minutes still resolves a moneyline, but on a puck line overtime caps the margin at one goal and is the main way a -1.5 loses.
Then special teams, comparing each power play to the KILL IT FACES rather than to the other power play, since those units never meet.
Then fatigue and shot quality if they add something: back-to-backs, rest-day gaps, long road trips, and xGF/60, xGA/60 and 5v5 Corsi for who generates the better chances.

EARLY-SEASON HONESTY:
- Team rate stats carry last season's data until this season's sample is large enough. In the first two weeks, say the rates are carried over rather than presenting them as this year's form.
- Do NOT cite strength of schedule or strength of record for NHL. Those are not computed until the regular season decides games.$T$);

COMMIT;

NOTIFY pgrst, 'reload schema';
