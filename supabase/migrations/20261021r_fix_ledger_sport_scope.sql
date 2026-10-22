-- == 2026-10-21 - 23 LEDGER SLIPS LABELLED MULTI THAT ARE SINGLE-SPORT ==
--
-- ANDY: "findout why ledger says 3-3 in recupeiits for eveyr sport
-- somehting is off there".
--
-- The identical record under every sport is DELIBERATE and documented in
-- compute_surface_records.py on 2026-10-08: 'ledger' is in
-- CROSS_SPORT_SURFACES, so it reports its ALL aggregate under every sport
-- key, on the reasoning that "'The MLB ledger record' is a CATEGORY ERROR"
-- because a chalk parlay deliberately combines legs across sports.
--
-- THE REASONING IS SOUND. THE PREMISE IS 4% TRUE.
-- Measured over all 267 graded slips by reading the SPORT INSIDE EACH LEG,
-- rather than the sport_scope label:
--
--   truly multi-sport (legs span 2+ sports)    11   (4.1%)
--   single-sport                              256  (95.9%)
--      MLB 231 · NHL 22 · NBA 1 · NCAAF 1 · NFL 1
--
-- So a per-sport ledger record is well defined for 96% of slips. What made
-- October look cross-sport is that 23 of the 34 rows carrying
-- sport_scope='MULTI' are NOT multi-sport at all - 22 are NHL-only and 1 is
-- NBA-only. The October rows the 10-08 note observed were mislabelled, and
-- the conclusion drawn from them is narrower than it appeared.
--
-- The cost is visible: the TRUE last-7-day ledger record by actual leg sport
-- is NHL 4-2, while every sport tab shows the global 3-3.
--
-- ── WHAT THIS MIGRATION DOES, AND DOES NOT ──────────────────────────────
-- It corrects the LABEL only. sport_scope becomes the actual sport whenever
-- every leg of a slip is that sport. Nothing about display changes, because
-- 'ledger' is still in CROSS_SPORT_SURFACES and still aggregates to ALL.
--
-- It deliberately does NOT remove 'ledger' from CROSS_SPORT_SURFACES. That
-- is a product decision with a live consequence: the 10-08 note records that
-- the shipped build hardcodes sport='MLB' for this surface, so restoring a
-- per-sport split would show that build MLB's own ledger history - 231
-- slips, nearly all from a season that has ended - instead of the global
-- figure. Correct labels first; the split is Andy's call, and it is now a
-- call about 4% of rows rather than about all of them.
--
-- Idempotent: only rows whose label disagrees with their legs are touched.

BEGIN;

WITH leg_sports AS (
  SELECT s.id,
         array_agg(DISTINCT l->>'sport') AS sports
    FROM public.ledger_snapshots s,
         jsonb_array_elements(s.legs) AS l
   WHERE s.legs IS NOT NULL
     AND jsonb_typeof(s.legs) = 'array'
     AND l->>'sport' IS NOT NULL
   GROUP BY s.id
)
UPDATE public.ledger_snapshots s
   SET sport_scope = ls.sports[1]
  FROM leg_sports ls
 WHERE s.id = ls.id
   -- single-sport slips only. A genuinely multi-sport slip keeps MULTI.
   AND array_length(ls.sports, 1) = 1
   AND COALESCE(s.sport_scope, '') <> ls.sports[1];

-- And the reverse: a slip whose legs DO span sports must say MULTI, in case
-- one was ever written with a single sport's label.
WITH leg_sports AS (
  SELECT s.id,
         array_agg(DISTINCT l->>'sport') AS sports
    FROM public.ledger_snapshots s,
         jsonb_array_elements(s.legs) AS l
   WHERE s.legs IS NOT NULL
     AND jsonb_typeof(s.legs) = 'array'
     AND l->>'sport' IS NOT NULL
   GROUP BY s.id
)
UPDATE public.ledger_snapshots s
   SET sport_scope = 'MULTI'
  FROM leg_sports ls
 WHERE s.id = ls.id
   AND array_length(ls.sports, 1) > 1
   AND COALESCE(s.sport_scope, '') <> 'MULTI';

DO $$
DECLARE n_multi INT; n_single INT;
BEGIN
  SELECT COUNT(*) INTO n_multi
    FROM public.ledger_snapshots WHERE sport_scope = 'MULTI';
  SELECT COUNT(*) INTO n_single
    FROM public.ledger_snapshots WHERE sport_scope <> 'MULTI';
  RAISE NOTICE 'ledger_snapshots sport_scope corrected: % MULTI, % single-sport',
    n_multi, n_single;
  IF n_multi > 20 THEN
    RAISE WARNING 'expected ~11 genuinely multi-sport slips, found % - '
                  'check whether legs carry a sport key', n_multi;
  END IF;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
