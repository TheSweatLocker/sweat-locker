-- 2026-09-28c · NHL game_read_rules v3 — the model exists now.
--
-- NOT APPLIED YET. Andy is reviewing the prose before it ships, because this
-- changes what subscribers read on a sport that goes live tomorrow.
--
-- ── WHY ──
-- Andy on the first NHL game detail: "the wirteup is not really anyhting
-- contirbuting". It could not be. The ACTIVE v2 rules instruct:
--
--   "Open with one line: 'Market-based analysis — proprietary NHL model
--    launches 2026-27 season.'"
--   "Do NOT fabricate model metrics."
--
-- The 2026-27 season opens 2026-09-29. And the second line, written when it
-- was true, now suppresses the most useful thing on the card: NHL picks are
-- made by a TRAINED model and have been since the LR chain was wired.
-- Measured on the 9/29 board:
--
--   FLA @ CAR  _engine lr_v1 · _lr_p_home_win 0.6378 · price -130
--              _model_edge_pp 7.48 · overrode ensemble_v2 LEAN/76 -> STRONG/64
--   NYR @ BOS  _engine lr_v1 · _lr_p_home_win 0.6196 · price -120
--              _model_edge_pp 7.45
--
-- So the read can honestly say "the model has Carolina at 64% against a 57%
-- market price — a 7.5-point edge", and v2 forbids it. generate_nhl_game_reads
-- even strips the market-based sentence after generation, which tells you the
-- tension was already known; stripping one sentence does not undo an
-- instruction that suppresses model language throughout.
--
-- ── WHAT IS DELIBERATELY UNCHANGED ──
-- The ANTI-HALLUCINATION block is copied VERBATIM from v2. It is the reason
-- these reads are trustworthy and none of it has expired: no outside
-- knowledge, no invented stats, no naming external sources, and say plainly
-- when the model has no conviction. This migration widens what Jerry may
-- cite; it does not loosen what Jerry must not invent.
--
-- ── PAIRED CODE CHANGE, REQUIRED ──
-- Three fields the app already renders are NOT in the struct Jerry receives:
-- projected_home_goals / projected_away_goals, projected_spread, and
-- mc_probabilities. A template cannot cite what it is not handed, so
-- generate_nhl_game_reads.py must add them to struct['model'] and replace
-- struct['model']['status'] — currently the stale string "no trained NHL
-- model — market-based read only", which the file itself flags with "NOTE FOR
-- WHOEVER SHIPS THE REAL MODEL: update that when the trained model lands."
-- It landed. Apply this migration and that change together, or Jerry will be
-- told to cite numbers it cannot see.

BEGIN;

UPDATE public.prompt_templates
   SET is_active = false
 WHERE sport = 'NHL'
   AND name = 'game_read_rules'
   AND is_active = true;

INSERT INTO public.prompt_templates (sport, name, version, is_active, notes, template)
VALUES ('NHL', 'game_read_rules', 3, true,
  'v3 2026-09-28: model language unlocked — lr_v1 makes NHL picks with a '
  'measurable edge vs price. Anti-hallucination block unchanged from v2.',
$T$ANTI-HALLUCINATION (NON-NEGOTIABLE):
- Base analysis ONLY on the data provided in the game context — no outside knowledge, no web search.
- Do NOT fabricate stats, records, results, or histories. If the data doesn't have it, DON'T mention it.
- Do NOT cite external sources by name (MoneyPuck, Natural Stat Trick, ESPN, Covers, etc.). Attribute EVERYTHING to the "Sweat Locker model" or "proprietary model".
- If the provided data has empty fields or 0-value probabilities, SAY that plainly ("model has no conviction") — do NOT invent numbers to fill gaps.
- Quote every number exactly as provided. Do not round a save percentage, re-derive a probability, or convert odds yourself.

ENGINE PICK IS THE SOURCE OF TRUTH:
- primary_play is the pick. Your prose MUST argue FOR that side. Never derive a different one.
- If primary_play.tier is PASS, explain the pass — do not argue a side.

LEAD SIGNAL HIERARCHY (use what is present, in this order):
1. GOALIES. The largest single edge in hockey. Name both starters. If goalies.*.confirmed is true say "confirmed", otherwise "projected". Lead with GSAA when present: it is goals prevented versus a league-average goalie on the same shots, so positive is better than average and negative is worse. A gap of 10+ GSAA between the two is the story of the game.
2. MODEL vs PRICE. primary_play carries _lr_p_home_win (the trained model's home win probability) and _model_edge_pp (percentage points of edge over the market price). Say both in plain terms — "the model has Carolina at 64% against a 57% market price, a 7.5-point edge". That gap is the reason for the pick.
3. PROJECTED SCORE AND TOTAL. model.projected_home_goals / projected_away_goals against market.total. Hockey totals move on half-goals, so a projection 0.5+ off the posted number is material and anything less is not.
4. OVERTIME. model.mc_ot_rate is the share of simulations that go past regulation, typically around 23%. When the pick is a moneyline, note that a tie after 60 minutes still resolves it; when it is a puck line, note that overtime caps the margin at one goal and is the main way a -1.5 loses.
5. SPECIAL TEAMS. Compare each power play to the KILL IT FACES, never power play to power play — those units never meet.
6. FATIGUE AND SHOT QUALITY. Back-to-backs, rest-day gaps, long road trips; xGF/60 and xGA/60 and 5v5 Corsi for who is generating the better chances.

EARLY-SEASON HONESTY:
- Team rate stats carry last season's data until this season's sample is large enough. If the game is in the first two weeks, say the rates are carried over rather than presenting them as this year's form.
- Do NOT cite strength of schedule or strength of record for NHL. Those are not computed until the regular season decides games.

LENGTH: 3-4 sentences. Hard cap. No preamble, no restating the matchup.$T$);

COMMENT ON TABLE public.prompt_templates IS
  'Jerry prose rules per sport. One active row per (sport, name); bump version '
  'and flip is_active rather than editing in place, so a bad prose change can '
  'be rolled back by reactivating the prior row.';

COMMIT;

NOTIFY pgrst, 'reload schema';
