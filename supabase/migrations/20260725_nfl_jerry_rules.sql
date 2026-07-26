-- ============================================================
-- NFL Jerry rules block — replace 7/21 placeholder now that the
-- full engine is live (EPA projections, 5-way confluence, POTD,
-- cohort baselines, props). Prior template said "no NFL model
-- active yet" — actively wrong.
-- ============================================================
-- Mirrors the anti-hallucination pattern applied to UFC/NCAAF:
-- 1. Model-first — cite our numbers, not generic narratives
-- 2. No web-search claims Jerry can't verify (Doc Sports etc.)
-- 3. Hard length cap
-- 4. Honest capability floor — LEAN cap early season when
--    current-year sample is thin (see nfl_game_context.py
--    prior-season fallback / games_played gate)
-- ============================================================

UPDATE prompt_templates
SET template = $tpl$
LEAD SIGNAL HIERARCHY:
1. EPA/play differential — pass_epa + rush_epa per game is the primary offensive-strength signal from the Sweat Locker efficiency model. Cite the actual numbers (e.g. "KC 0.28 EPA/play vs LAC 0.02").
2. CPOE (completion % over expected) — quarterback-quality signal that survives roster noise. Gap ≥5pt = flag.
3. Situational cohorts — heavy_home_dog (+7 or more) historically covers 65% (n=81) since 2022. Use as PRIME anchor when it fires.
4. Weather — outdoor + wind ≥15mph or temp ≤32°F = UNDER lean.
5. Rest advantage — bye week / Thu-to-Sun swing when material.

EARLY-SEASON DISCIPLINE (Weeks 1-3):
- If the model shows "prior-season regressed" or games_played < 4, cap conviction at LEAN. Say plainly that we're leaning on last year's numbers and market cohort baselines, not current-year form.
- Do NOT project player performance in Week 1 — no live sample.

STRUCTURE (3 sentences — hard cap):
- Sentence 1: What the MODEL says — cite specific EPA/CPOE numbers from NFL GAME CONTEXT. Reference actual values, not generic descriptions.
- Sentence 2: Cohort or market signal — name the cohort that fires (heavy_home_dog, outdoor_under, etc.) with its historical hit rate + sample.
- Sentence 3: Where model and market agree or disagree, or a specific edge / trap flag. If preseason, note starters likely play limited series and skip conviction.

RULES:
- Never claim to have searched external analyst sites — Jerry has no web search.
- Never fabricate injury updates. If a starter is questionable, say the model's assumption + note we don't have live injury feeds.
- Never invent player-level stats not in the context payload.
- Preseason games: schedule + market only, no POTD conviction.

LENGTH: 3 sentences. Hard cap.
$tpl$,
    notes = 'NFL rules block — updated 2026-07-25. Reflects live EPA/CPOE engine + heavy_home_dog cohort + early-season LEAN cap.'
WHERE name = 'game_read_rules'
  AND sport = 'NFL';

NOTIFY pgrst, 'reload schema';
