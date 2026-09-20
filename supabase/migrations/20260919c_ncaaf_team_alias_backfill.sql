-- 2026-09-19  NCAAF team-name aliases: the cases no algorithm should guess.
--
-- WHY: Andy reported "FIU FAU game completely blank". Cause was that
-- ncaaf_game_context stores odds-pipe team names while ncaaf_team_stats
-- stores CFBD canonical names. 'FIU' never matched 'Florida International',
-- so every away_* stat came back NULL, which meant no SP+, which meant the
-- Monte Carlo sim had nothing to run on -- mc_probabilities NULL -- which
-- rendered the whole game card empty.
--
-- An audit of all 255 ctx team names against CFBD found 32 unmatched.
-- Those split cleanly into two groups, handled in two different places:
--
--   MECHANICAL (19) -- mascot suffixes, St/State, diacritics:
--     'Sacramento State Hornets', 'Youngstown St Penguins', 'VMI Keydets'.
--     Handled in code by _resolve_team_key (ncaaf_game_context.py), which
--     strips trailing words and REFUSES any truncation another CFBD name
--     extends. That guard matters: without it the strip resolved
--     'Houston Baptist Huskies' -> 'Houston' (+11.6 SP+, wrong school) and
--     'Louisiana Monroe' -> 'Louisiana' (-6.6 vs ULM's real -29.3).
--     Both were caught in testing and now refuse, falling through to here.
--
--   GENUINE SYNONYMS (below) -- no rule derives 'FIU' from
--     'Florida International'. These need a human-curated mapping and
--     that is exactly what this table is for.
--
-- Only 6 of these have an SP+ rating; the rest are FCS/D2 opponents with
-- no CFBD rating at all. They are listed anyway so the coverage audit
-- reads clean and a future SP+ backfill picks them up automatically --
-- an alias row is not a claim that a rating exists.

INSERT INTO public.ncaaf_team_name_aliases (ctx_name, cfbd_canonical, notes)
VALUES
  -- FBS, has SP+ -- these were costing real model coverage
  ('FIU',                               'Florida International', 'Odds pipe uses initialism. Blanked FIU@FAU 9/19.'),
  ('Connecticut',                       'UConn',                 'CFBD uses the short brand name'),
  ('UMass',                             'Massachusetts',         'Inverse case: CFBD uses the long form'),
  ('Louisiana Monroe',                  'UL Monroe',             'CFBD uses UL prefix. Ambiguity guard blocks the strip to Louisiana (-29.3 vs -6.6).'),
  ('Louisiana Ragin Cajuns',            'Louisiana',             'Mascot suffix, but Louisiana Tech extends the prefix so the strip refuses'),
  ('Southern Mississippi Golden Eagles','Southern Miss',         'Mascot suffix AND long/short mismatch'),
  -- FCS / D2 -- no SP+ in CFBD, aliased for audit cleanliness
  ('Houston Baptist Huskies',           'Houston Christian',     'School renamed 2022. Strip to Houston would be the wrong school entirely.'),
  ('Citadel Bulldogs',                  'The Citadel',           'CFBD keeps the leading article'),
  ('Albany',                            'UAlbany',               'CFBD uses the U-prefix brand'),
  ('Southeastern Louisiana',            'SE Louisiana',          'CFBD abbreviates the direction'),
  ('LIU Sharks',                        'Long Island University','Initialism + mascot')
ON CONFLICT (ctx_name) DO UPDATE
  SET cfbd_canonical = EXCLUDED.cfbd_canonical,
      notes          = EXCLUDED.notes,
      updated_at     = now();

-- The 2026-09-10 seed mapped this to itself with the note "Verify: may
-- need ULM". Verified 2026-09-19: it does. The row above corrects it via
-- the DO UPDATE; this comment records that the original was a placeholder,
-- not a deliberate identity mapping.

-- Not aliased: 'Cheyney' and 'North Greenville' are D2 programs with no
-- CFBD row at all. Nothing to point at, so they stay unresolved and get
-- reported by _TeamStats.unresolved rather than mapped to a guess.

NOTIFY pgrst, 'reload schema';
