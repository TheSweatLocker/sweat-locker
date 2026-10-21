-- == 2026-10-21 - NBA + NHL PROPS: SERVER SIDE, SO NO SECOND BUILD ==
--
-- ANDY: "For NBA props, and nhl willa nother build be necessary for tuning
-- it on and live"
--
-- It was. PROP_SPORT_REGISTRY is a CLIENT constant: NBA sat commented out
-- and NHL was absent, so enabling either needed an EAS build and an App
-- Store review, with no OTA path (B5). The client now registers both and
-- reads feature_flags(sport, 'pipeline_props') FAIL-CLOSED, so the switch
-- lives here instead of in a binary.
--
-- This migration supplies the two things the client expects to find: a
-- publishable view per sport and an RPC per sport, plus the flag rows that
-- keep them OFF until the evidence says otherwise.
--
-- ── THEY ARE SEEDED DISABLED, ON PURPOSE ────────────────────────────────
-- Today's calibration does not support publishing either:
--
--   NHL  every measurable prop signal LOSES to its own price. Inside the
--        publishable -300..+150 band, on 25,478 graded rows: best -0.5pp,
--        worst -7.1pp, ZERO VALIDATED. After the structural tier floor the
--        board is 17 LEAN and no PRIME or STRONG at all, so this view will
--        legitimately return 0 rows.
--   NBA  ZERO graded prop rows exist, so its tiers are uncalibrated by
--        definition. It shows 25 STRONG today on a board whose signals have
--        never been measured against a result.
--
-- So the flags ship false. Flip one to true when its calibration earns it
-- and the board appears with no build. That is the whole point.
--
-- ── GATES, MIRRORING v_nfl_props_publishable ────────────────────────────
-- Same structure as NFL's view (20260925b) rather than a new invention:
-- tier whitelist, coverage-kill gate, per-slate volume cap, ordered by
-- display_conviction. Two deliberate differences:
--
--   * player_team is NOT selected. It exists on nfl_pipeline_props and does
--     NOT exist on either nhl_pipeline_props or nba_pipeline_props (probed
--     column by column). Naming it would make the whole view fail, and the
--     client reads a failed view as an empty board.
--   * the ODDS BAND is enforced in the view. -300..+150 is house policy and
--     a prop outside it is a SKIP, but more than that: 75% of NHL signal
--     firings measured today sat outside that band, which is what made the
--     old hit rates look like 74% instead of 59.9%. A board that shows what
--     the calibration excluded would be measuring one thing and selling
--     another.
--
-- LEAN is excluded from both, as NFL's view excludes it. For NHL that means
-- zero rows until the board produces a tier above LEAN, which is the honest
-- outcome and not a bug to work around.

-- ── NHL ──────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.v_nhl_props_publishable AS
SELECT
    game_id, game_date, player_name, matchup,
    prop_type, prop_line, direction, tier, conviction, signals,
    book_line, book_over_odds, book_under_odds, result,
    refit_conviction, display_conviction,
    jerry_verdict, jerry_short_read, jerry_conviction
FROM (
    SELECT
        p.game_id, p.game_date, p.player_name, p.matchup,
        p.prop_type, p.prop_line, p.direction, p.tier, p.conviction,
        p.signals, p.book_line, p.book_over_odds, p.book_under_odds,
        p.result, p.refit_conviction,
        CASE WHEN p.tier = 'PRIME' THEN p.conviction
             ELSE COALESCE(p.refit_conviction, p.conviction) END
            AS display_conviction,
        pj.call_verdict AS jerry_verdict,
        pj.short_read   AS jerry_short_read,
        pj.conviction   AS jerry_conviction,
        ROW_NUMBER() OVER (
            PARTITION BY p.game_date
            ORDER BY CASE WHEN p.tier = 'PRIME' THEN p.conviction
                          ELSE COALESCE(p.refit_conviction, p.conviction)
                     END DESC NULLS LAST,
                     p.conviction DESC NULLS LAST,
                     p.player_name ASC
        ) AS _rn
    FROM nhl_pipeline_props p
    LEFT JOIN prop_jerry_reads pj
        ON  pj.game_id     = p.game_id
        AND pj.player_name = p.player_name
        AND pj.prop_type   = p.prop_type
        AND pj.direction   = p.direction
        AND pj.sport       = 'NHL'
        AND pj.game_date   = p.game_date
    WHERE
        -- coverage-kill gate, as NFL
        (p.signals->>'_coverage_kill_gate' IS NULL
         OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no',''))
        -- publishable tiers only; LEAN excluded as in NFL's view
        AND p.tier IN ('PRIME','STRONG')
        -- odds band -300..+150 on the side being taken
        AND (
            (p.direction = 'over'  AND p.book_over_odds  BETWEEN -300 AND 150)
            OR
            (p.direction = 'under' AND p.book_under_odds BETWEEN -300 AND 150)
        )
) ranked
WHERE _rn <= 12;

COMMENT ON VIEW public.v_nhl_props_publishable IS
    'NHL props cleared to publish: PRIME/STRONG only, coverage-kill gate, '
    'odds inside -300..+150 on the taken side, 12 per slate. Expected to '
    'return ZERO rows as of 2026-10-21 - the board is all LEAN after the '
    'tier floor, and every measurable NHL prop signal loses to its own '
    'price. Gated off in feature_flags regardless.';

-- ── NBA ──────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.v_nba_props_publishable AS
SELECT
    game_id, game_date, player_name, matchup,
    prop_type, prop_line, direction, tier, conviction, signals,
    book_line, book_over_odds, book_under_odds, result,
    refit_conviction, display_conviction,
    jerry_verdict, jerry_short_read, jerry_conviction
FROM (
    SELECT
        p.game_id, p.game_date, p.player_name, p.matchup,
        p.prop_type, p.prop_line, p.direction, p.tier, p.conviction,
        p.signals, p.book_line, p.book_over_odds, p.book_under_odds,
        p.result, p.refit_conviction,
        CASE WHEN p.tier = 'PRIME' THEN p.conviction
             ELSE COALESCE(p.refit_conviction, p.conviction) END
            AS display_conviction,
        pj.call_verdict AS jerry_verdict,
        pj.short_read   AS jerry_short_read,
        pj.conviction   AS jerry_conviction,
        ROW_NUMBER() OVER (
            PARTITION BY p.game_date
            ORDER BY CASE WHEN p.tier = 'PRIME' THEN p.conviction
                          ELSE COALESCE(p.refit_conviction, p.conviction)
                     END DESC NULLS LAST,
                     p.conviction DESC NULLS LAST,
                     p.player_name ASC
        ) AS _rn
    FROM nba_pipeline_props p
    LEFT JOIN prop_jerry_reads pj
        ON  pj.game_id     = p.game_id
        AND pj.player_name = p.player_name
        AND pj.prop_type   = p.prop_type
        AND pj.direction   = p.direction
        AND pj.sport       = 'NBA'
        AND pj.game_date   = p.game_date
    WHERE
        (p.signals->>'_coverage_kill_gate' IS NULL
         OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no',''))
        AND p.tier IN ('PRIME','STRONG')
        AND (
            (p.direction = 'over'  AND p.book_over_odds  BETWEEN -300 AND 150)
            OR
            (p.direction = 'under' AND p.book_under_odds BETWEEN -300 AND 150)
        )
) ranked
WHERE _rn <= 12;

COMMENT ON VIEW public.v_nba_props_publishable IS
    'NBA props cleared to publish: PRIME/STRONG only, coverage-kill gate, '
    'odds inside -300..+150 on the taken side, 12 per slate. NBA has ZERO '
    'graded prop rows as of 2026-10-21, so its tiers are uncalibrated - '
    'gated off in feature_flags until a graded sample exists.';

-- ── RPCs the client registry names ──────────────────────────────────────
-- SECURITY INVOKER (no DEFINER clause), matching the other client RPCs, so
-- RLS applies as the caller. The anon read policies from 20261021c cover
-- the base tables.
CREATE OR REPLACE FUNCTION public.get_todays_pipeline_props_nhl()
RETURNS SETOF public.v_nhl_props_publishable
LANGUAGE sql STABLE
AS $$
  SELECT * FROM public.v_nhl_props_publishable
  WHERE game_date = slate_date_et();
$$;

CREATE OR REPLACE FUNCTION public.get_todays_pipeline_props_nba()
RETURNS SETOF public.v_nba_props_publishable
LANGUAGE sql STABLE
AS $$
  SELECT * FROM public.v_nba_props_publishable
  WHERE game_date = slate_date_et();
$$;

GRANT EXECUTE ON FUNCTION public.get_todays_pipeline_props_nhl()
  TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_todays_pipeline_props_nba()
  TO anon, authenticated;

-- ── THE SWITCH, SEEDED OFF ──────────────────────────────────────────────
-- The client reads these FAIL-CLOSED, unlike isFeatureOn() which fails open
-- so a missing flag never hides a sport tab. That default is right for a tab
-- and wrong for a prop board: the harm is publishing, so a deleted row must
-- mean OFF.
INSERT INTO public.feature_flags (sport, feature, enabled, note)
VALUES
  ('NHL', 'pipeline_props', FALSE,
   'OFF 2026-10-21. Every measurable NHL prop signal loses to its own price '
   'inside the -300..+150 band on 25,478 graded rows: best -0.5pp, worst '
   '-7.1pp, zero VALIDATED. Board is all LEAN after the tier floor. Flip to '
   'true when calibration earns it - no app build required.'),
  ('NBA', 'pipeline_props', FALSE,
   'OFF 2026-10-21. NBA has ZERO graded prop rows, so its tiers are '
   'uncalibrated by definition. L5/L10 also leans on last season until '
   'mid-November. Flip to true once a graded sample exists - no app build '
   'required.')
ON CONFLICT (sport, feature) DO NOTHING;

NOTIFY pgrst, 'reload schema';
