-- 2026-09-25 · Ban the NFL prop families that are provably bleeding.
--
-- Andy: "lets drop the 38% nfl prop, no longer surface, or just fade them."
--
-- Graded NFL prop receipts, all-time, real book prices (breakeven 54.2%):
--
--   rush_yds_over          19-30  n=49  38.8%   -13.11u   <- biggest single bleed
--   pass_completions_over   5-8   n=13  38.5%    -3.56u
--   pass_attempts_over      4-9   n=13  30.8%    -5.29u
--
-- Banned rather than faded. A fade is a new bet type with no record of its
-- own, and the opposite side already exists and already performs:
-- rush_yds_under 17-13 (56.7%, +2.12u), pass_attempts_under 13-4 (76.5%,
-- +7.58u), pass_completions_under 10-8 (55.6%, +0.85u). Backing the under
-- family we already publish is the same bet with a track record, so there is
-- nothing to gain from inventing a fade.
--
-- SAMPLE-SIZE HONESTY: only rush_yds_over clears the n>=30 gate. The two
-- pass_*_over families sit at n=13 and are banned on Andy's explicit
-- instruction plus a consistent sign, NOT on proof. If either is wanted back,
-- it should return as a measured pilot rather than a silent un-ban.
--
-- NOT banned, but worth naming — the highest-volume family is also losing:
--   reception_yds_over  95-93  n=188  50.5%  -8.78u
--   receptions_under    67-58  n=125  53.6%  -8.09u
-- Those two bleed 16.9u between them, more than the three bans combined.
-- 53.6% loses money at our prices, which is the whole lesson: a family needs
-- to clear 54.2%, not 50%. Left alone here because cutting the top two
-- families by volume is a product decision, not a cleanup.
--
-- CREATE OR REPLACE VIEW REPLACES the whole definition, so every prior rule
-- is carried forward verbatim per feedback_publishable_view_drift:
--   Rule 1  coverage-kill gate            (20260918c)
--   Rule 2  NFL SKIP needs BACK@conv>=60  (20260907e)
--   Rule 3  LEAN dropped from whitelist   (20260919a)
--   Rule 4  per-slate volume cap <= 40    (20260919a)
--   Rule 5  family ban                    (NEW, this migration)
--
-- The ban is applied INSIDE the subquery so banned rows are removed BEFORE
-- ROW_NUMBER() assigns the cap. Filtering after the window would let a banned
-- prop consume one of the 40 slots and shrink the real board.

CREATE OR REPLACE VIEW public.v_nfl_props_publishable AS
SELECT
    game_id, game_date, player_name, player_team, matchup,
    prop_type, prop_line, direction, tier, conviction, signals,
    book_line, book_over_odds, book_under_odds, result,
    refit_conviction, display_conviction, is_skip_back,
    jerry_verdict, jerry_short_read, jerry_conviction
FROM (
    SELECT
        p.game_id,
        p.game_date,
        p.player_name,
        p.player_team,
        p.matchup,
        p.prop_type,
        p.prop_line,
        p.direction,
        p.tier,
        p.conviction,
        p.signals,
        p.book_line,
        p.book_over_odds,
        p.book_under_odds,
        p.result,
        p.refit_conviction,
        CASE
            WHEN p.tier = 'PRIME' THEN p.conviction
            ELSE COALESCE(p.refit_conviction, p.conviction)
        END AS display_conviction,
        (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK') AS is_skip_back,
        pj.call_verdict AS jerry_verdict,
        pj.short_read   AS jerry_short_read,
        pj.conviction   AS jerry_conviction,
        ROW_NUMBER() OVER (
            PARTITION BY p.game_date
            ORDER BY
                CASE
                    WHEN p.tier = 'PRIME' THEN p.conviction
                    ELSE COALESCE(p.refit_conviction, p.conviction)
                END DESC NULLS LAST,
                p.conviction DESC NULLS LAST,
                p.player_name ASC
        ) AS _rn
    FROM nfl_pipeline_props p
    LEFT JOIN prop_jerry_reads pj
        ON pj.game_id     = p.game_id
       AND pj.player_name = p.player_name
       AND pj.prop_type   = p.prop_type
       AND pj.direction   = p.direction
       AND pj.sport       = 'NFL'
       AND pj.game_date   = p.game_date
    WHERE
        -- Rule 1 (carried from 20260918c): coverage-kill gate.
        (
            p.signals->>'_coverage_kill_gate' IS NULL
            OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
        )
        -- Rule 2 (carried from 20260907e): NFL SKIP needs BACK@conv>=60.
        AND (
            p.tier != 'SKIP'
            OR (
                UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
                AND COALESCE(pj.conviction, 0) >= 60
            )
        )
        -- Rule 3 (carried from 20260919a): LEAN dropped from the whitelist.
        AND (
            p.tier IN ('PRIME','STRONG')
            OR (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK')
        )
        -- Rule 5 (NEW 20260925b): banned families, applied before the cap.
        AND p.prop_type NOT IN (
            'rush_yds_over',
            'pass_attempts_over',
            'pass_completions_over'
        )
) ranked
-- Rule 4 (carried from 20260919a): per-slate volume cap.
WHERE ranked._rn <= 40;

NOTIFY pgrst, 'reload schema';
