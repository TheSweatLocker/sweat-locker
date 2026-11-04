-- 20261104a — correct 34 POTD receipt prices the freeze trigger reverts.
-- ==================================================================
-- WHY. public_receipts.pick_odds is SET-ONCE: freeze_receipt_identity()
-- (migration 20260918b) does
--
--     IF OLD.pick_odds IS NOT NULL THEN NEW.pick_odds := OLD.pick_odds;
--
-- which SILENTLY reverts the value and returns 204, so a REST PATCH
-- reports success and changes nothing. Verified on all 34 rows below.
--
-- Replaying price recovery over all 175 POTD receipts found 36 whose
-- stored price disagrees with the price recoverable from the
-- authoritative source, 17 of them a flat -110:
--
--     2026-08-02  stored -110  real -232   Milwaukee Brewers ML
--     2026-10-03  stored -110  real +124   Chicago White Sox ML
--     2026-09-29  stored +102  real -145   Michael King Over 14.5 Outs
--
-- On the 34 corrected here (graded 21-13):
--     as stored    61.76% vs BE 55.34%   ROI +15.09%
--     real prices  61.76% vs BE 57.95%   ROI +10.22%
-- A flat -110 on a game that closed -232 overstates every ROI figure
-- computed from it.
--
-- TWO OF THE 36 ARE DELIBERATELY NOT HERE:
--   'San Diego Padres ML Away 1.5' (08-05) — a RUN LINE whose recovered
--     value came from away_ml_close, i.e. the moneyline price for a
--     spread bet.
--   'Texas Rangers RL +1.5' (09-11) — priced from spread_home_odds with
--     nothing establishing that Texas was the home side.
-- A real price from the wrong market is worse than a stale one.
--
-- SAFETY.
--  * Each row updates ONLY if pick_odds still equals the expected stale
--    value, so re-running is a no-op and a row that drifted is skipped
--    rather than clobbered.
--  * The trigger is disabled and re-enabled inside ONE transaction, so it
--    cannot be left off. This is the one case where BEGIN/COMMIT is right
--    despite feedback_notify_pgrst_breaks_andys_sql_client's advice to omit
--    it — that warning is about multi-statement SCHEMA migrations where a
--    trailing failure rolls back good work; here atomicity is the point.
--  * NO `NOTIFY pgrst` — nothing about the schema changes, and that NOTIFY
--    makes this client report 42P01 whether it succeeded or failed.
--
-- VERIFY AFTERWARDS (should return 0 rows):
--   select id, pick_odds from public_receipts
--    where id in (select id from (values 34 ...) ) ...
--   or simply re-run:
--     python mlb_pipeline/_overwrite_stale_potd_prices_1103.py
--   which should then report "overwriting: 0".
-- ==================================================================

BEGIN;

ALTER TABLE public.public_receipts DISABLE TRIGGER USER;

WITH corrections(id, expect_stale, real_odds) AS (
  VALUES
    (24283, -110, -232),  -- 2026-08-02 ML: Milwaukee Brewers ML (Jerry 76/100)
    (24289, -110, -119),  -- 2026-08-06 TOTAL: Under 9.0 (Jerry 73/100)
    (24290, -110, -129),  -- 2026-08-07 ML: Detroit Tigers ML (Jerry 72/100)
    (24293, -110, -140),  -- 2026-08-08 ML: Milwaukee Brewers ML (resolver STRONG)
    (24296, -110, -108),  -- 2026-08-10 ML: Baltimore Orioles ML (Jerry 72/100)
    (24300, -110, -170),  -- 2026-08-11 ML: Arizona Diamondbacks ML (resolver STRONG
    (24303, -110, -174),  -- 2026-08-12 ML: Atlanta Braves ML (resolver STRONG)
    (24304, -110, -140),  -- 2026-08-13 ML: Boston Red Sox ML (Jerry 72/100)
    (24307, -110, -115),  -- 2026-08-16 TOTAL: Under 10.5 (Jerry 72/100)
    (24301, -110, 100),  -- 2026-08-26 TOTAL: Under 8.5 (Jerry 86/100)
    (24305, 116, -118),  -- 2026-08-27 TOTAL: Under 8.5 (Jerry 94/100)
    (24319, -181, -199),  -- 2026-08-28 ML: Milwaukee Brewers ML (Jerry 97/100)
    (24316, -106, -101),  -- 2026-08-29 ML: San Francisco Giants ML (resolver STRONG
    (24315, -110, 100),  -- 2026-08-30 TOTAL: Under 9.0 (Jerry 97/100)
    (24313, -112, -149),  -- 2026-09-02 ML: Boston Red Sox ML (Jerry 88/100)
    (24298, -118, -157),  -- 2026-09-03 ML: Houston Astros ML (Jerry 97/100)
    (24288, -110, -115),  -- 2026-09-06 TOTAL: Over 8.0 (Jerry 85/100)
    (24312, 109, -110),  -- 2026-09-07 TOTAL: Under 8.0 (Jerry 74/100)
    (24318, -223, -115),  -- 2026-09-09 TOTAL: Under 9.5 (Jerry 93/100)
    (24309, -110, -145),  -- 2026-09-10 PROP: Jared Jones Under 15.5 Outs (Jerry 81/10
    (24320, -202, -168),  -- 2026-09-12 ML: Los Angeles Dodgers ML (Jerry 87/100)
    (24325, -110, -175),  -- 2026-09-16 ML: San Diego Padres ML (Jerry 91/100)
    (24310, -110, -203),  -- 2026-09-17 ML: San Diego Padres ML (Jerry 95/100)
    (24326, -151, -261),  -- 2026-09-20 ML: Cleveland Guardians ML (Jerry 91/100)
    (176093, -160, -105),  -- 2026-09-23 TOTAL: Under 11.0 (resolver STRONG)
    (233445, 119, -113),  -- 2026-09-24 TOTAL: Over 7.5 (Jerry 90/100)
    (417339, -168, -167),  -- 2026-09-26 ML: Toronto Blue Jays ML (resolver STRONG)
    (453969, -136, -109),  -- 2026-09-27 ML: Cleveland Guardians ML (Jerry 97/100)
    (606774, 102, -145),  -- 2026-09-29 PROP: Michael King Over 14.5 Outs (Jerry 85/10
    (684262, -141, -175),  -- 2026-09-30 PROP: Hunter Brown Over 14.5 Outs (Jerry 85/10
    (824383, -110, 124),  -- 2026-10-03 ML: Chicago White Sox ML
    (1301859, -115, -103),  -- 2026-10-08 ML: Chicago White Sox ML (resolver STRONG)
    (1861991, -171, -155),  -- 2026-10-12 PROP: Yoshinobu Yamamoto Over 17.5 Outs (Jerry
    (2276071, -133, -130)  -- 2026-10-15 ML: Cleveland Guardians ML
)
UPDATE public.public_receipts r
   SET pick_odds = c.real_odds
  FROM corrections c
 WHERE r.id = c.id
   AND r.pick_odds = c.expect_stale;

ALTER TABLE public.public_receipts ENABLE TRIGGER USER;

COMMIT;
