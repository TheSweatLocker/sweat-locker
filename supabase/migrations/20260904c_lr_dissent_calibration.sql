-- 2026-09-04 lr_dissent_calibration
-- ================================================================
-- One row per game where the LR (logistic-regression) predictor
-- disagreed with the ensemble top pick. Powers the decision on
-- whether to widen or tighten the consensus-dissent gate.
--
-- Two disagreement modes we track:
--   1. LR OVERRODE the ensemble  → engine=lr_v1, _pre_lr present
--   2. LR was BLOCKED by the consensus-dissent gate → shadow present,
--      audit_note contains "LR dissented"
--
-- For each we log:
--   lr_side           — the side LR wanted (HOME/AWAY)
--   shipped_side      — the side that actually shipped in primary_play
--   winning_side      — HOME/AWAY per mlb_game_results
--   lr_won            — did LR's side win?
--   shipped_won       — did the shipped side win?
--   consensus_count   — # of ensemble sources on the shipped side (context)
--   consensus_money_pct — sharp money% on the shipped side (context)
--   lr_prob           — LR's home-win probability at pick time
--
-- Analysis view: v_lr_dissent_hitrate rolls these up so you can see
-- the win rate on LR's picks vs the shipped picks in dissent spots.
-- ================================================================

CREATE TABLE IF NOT EXISTS public.lr_dissent_calibration (
    id           bigserial PRIMARY KEY,
    game_id      text NOT NULL,
    game_date    date NOT NULL,
    sport        text NOT NULL DEFAULT 'MLB',

    -- market this dissent applies to: 'ml' | 'total'
    market       text NOT NULL DEFAULT 'ml',

    -- disagreement mode: 'override' | 'blocked'
    mode         text NOT NULL,

    -- picks — HOME/AWAY for ml, OVER/UNDER for total
    lr_side              text,
    shipped_side         text,
    winning_side         text,          -- null when ungraded; 'PUSH' possible on totals
    lr_won               boolean,
    shipped_won          boolean,

    -- context (for stratifying "when is LR right / wrong?")
    lr_prob              numeric,       -- LR probability at pick time (interpretation is market-specific)
    consensus_count      int,           -- # of ensemble sources on shipped side
    consensus_money_pct  numeric,       -- sharp money% on shipped side (nullable, ml-only)

    -- audit
    home_team    text,
    away_team    text,
    logged_at    timestamptz NOT NULL DEFAULT now(),
    graded_at    timestamptz,           -- filled when we know the result

    UNIQUE (game_id, market)
);

CREATE INDEX IF NOT EXISTS idx_lr_dissent_calibration_date
    ON public.lr_dissent_calibration(game_date);
CREATE INDEX IF NOT EXISTS idx_lr_dissent_calibration_mode
    ON public.lr_dissent_calibration(mode);
CREATE INDEX IF NOT EXISTS idx_lr_dissent_calibration_graded
    ON public.lr_dissent_calibration(sport) WHERE graded_at IS NOT NULL;

COMMENT ON TABLE public.lr_dissent_calibration IS
    'Per-game log of LR-vs-ensemble disagreements. Populated nightly by '
    'mlb_lr_dissent_audit.py. Answer: does LR win when it dissents? '
    'Decides whether to widen/tighten the consensus-dissent gate.';

-- Rollup view for direct querying
CREATE OR REPLACE VIEW public.v_lr_dissent_hitrate AS
WITH graded AS (
    SELECT
        market,
        mode,
        CASE WHEN lr_won      THEN 1 ELSE 0 END AS lr_hit,
        CASE WHEN shipped_won THEN 1 ELSE 0 END AS shipped_hit,
        consensus_count,
        CASE
            WHEN game_date >= CURRENT_DATE - INTERVAL '7 days'  THEN 'd7'
            WHEN game_date >= CURRENT_DATE - INTERVAL '30 days' THEN 'd30'
            ELSE 'lifetime'
        END AS window_key
    FROM lr_dissent_calibration
    WHERE graded_at IS NOT NULL
      AND winning_side NOT IN ('PUSH')
      AND winning_side IS NOT NULL
)
SELECT
    market,
    mode,
    window_key,
    COUNT(*) AS n,
    SUM(lr_hit)::int      AS lr_wins,
    SUM(shipped_hit)::int AS shipped_wins,
    ROUND(100.0 * SUM(lr_hit)      / NULLIF(COUNT(*), 0), 1) AS lr_hit_pct,
    ROUND(100.0 * SUM(shipped_hit) / NULLIF(COUNT(*), 0), 1) AS shipped_hit_pct,
    ROUND(AVG(consensus_count)::numeric, 1) AS avg_consensus_count
FROM graded
GROUP BY market, mode, window_key
ORDER BY market, mode, CASE window_key WHEN 'd7' THEN 1 WHEN 'd30' THEN 2 ELSE 3 END;

COMMENT ON VIEW public.v_lr_dissent_hitrate IS
    'Rollup of LR vs shipped hit rate on dissent spots. When lr_hit_pct '
    'sustains >= 60% at n>=30 on mode=blocked, consensus-dissent gate is '
    'overprotective; widen it. When lr_hit_pct <= 45%, gate is correct.';

NOTIFY pgrst, 'reload schema';
