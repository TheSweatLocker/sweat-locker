-- ══ 2026-10-19 · SHADOW THE ONE SIGNAL THAT SURVIVED ══
-- Four independent searches found nothing that predicts the spread residual:
-- 151/151/80 context features (zero clear Bonferroni, nominal hits equal to
-- chance expectation), 15 line-movement features (none reach nominal p<.05),
-- a multivariate ridge (TEST R2 NEGATIVE in all three sports), and our own
-- closing line value (mean +0.04 pts, sign-test p=0.64).
--
-- One thing survived. Grading the SAME picks at our number and again at the
-- close separates the mechanical half from the information half:
--
--                  at our line        at the close
--     CLV > 0      22-11  66.7%       20-13  60.6%
--     CLV < 0      11-18  37.9%       13-16  44.8%
--     gap          28.8 pts           15.8 pts
--
-- Half is just holding a better number; the other ~16 points is information.
-- n=62 with overlapping CIs: the strongest lead we have, NOT a proven edge,
-- and nowhere near enough to gate a live pick on.
--
-- So it gets shadowed, which is the pattern this repo already uses for a
-- suppression rule (shadow first, exit on evidence, never flip a live gate on
-- one sample). check_live_clv.py writes a row per published pick per day with
-- our number, the latest market number and the signed CLV. In a few weeks the
-- rule can be graded on its own data instead of argued about: line_history now
-- covers ~100% of games going forward, so this accrues roughly 124 football
-- and 50 NHL picks a week.
--
-- WHY A NEW TABLE AND NOT A COLUMN ON public_receipts. That table is
-- effectively immutable: a PATCH to pick_side or pick_line returns HTTP 200
-- with a body showing the value UNCHANGED, then re-reads as NULL. Only
-- `result` is writable. Verified on row 810352 on 2026-10-19. CLV also has to
-- be sampled repeatedly as the line moves, which is a time series, not a
-- column.

CREATE TABLE IF NOT EXISTS clv_shadow (
    id               bigserial PRIMARY KEY,
    game_date        date        NOT NULL,
    sport            text        NOT NULL,
    receipt_id       bigint      NOT NULL,
    surface          text,
    market           text,
    pick_label       text,
    pick_side        text,
    our_line         numeric,
    live_line        numeric,
    clv              numeric,
    live_captured_at timestamptz,
    checked_at       timestamptz NOT NULL DEFAULT now(),
    -- one row per pick per DAY, so repeated runs update rather than pile up,
    -- while still leaving a day-by-day trail of how the line moved.
    --
    -- 2026-10-20 · WAS A GENERATED COLUMN AND POSTGRES REFUSED IT:
    --   ERROR 42P17: generation expression is not immutable
    -- `checked_at::date` casts timestamptz to date, which depends on the
    -- session TimeZone, so it is STABLE rather than IMMUTABLE and a stored
    -- generated column may only use immutable expressions. Naming a zone does
    -- not help: `AT TIME ZONE 'America/New_York'` is also only STABLE, because
    -- DST rules can change under it.
    --
    -- A DEFAULT has no such restriction - it is evaluated once at INSERT - so
    -- the same thing is expressed as a defaulted column. UTC is used
    -- deliberately rather than ET: this is a dedupe key for "did this run
    -- already write this pick today", not a slate date a user ever sees, so it
    -- needs to be deterministic, not localised.
    checked_date     date        NOT NULL
                                 DEFAULT ((now() AT TIME ZONE 'UTC')::date),
    CONSTRAINT clv_shadow_once_per_day UNIQUE (receipt_id, checked_date)
);

CREATE INDEX IF NOT EXISTS clv_shadow_date_idx  ON clv_shadow (game_date);
CREATE INDEX IF NOT EXISTS clv_shadow_sport_idx ON clv_shadow (sport, game_date);

COMMENT ON TABLE clv_shadow IS
  'Shadow record of live closing-line value per published pick. Written by '
  'check_live_clv.py. NOT used to gate any pick - it exists so the CLV rule '
  'can be judged on accumulated evidence rather than on n=62.';
COMMENT ON COLUMN clv_shadow.clv IS
  'our_line - live_line, in the taken side''s own handicap terms. POSITIVE '
  'means we hold a better number than the market now offers.';

NOTIFY pgrst, 'reload schema';
