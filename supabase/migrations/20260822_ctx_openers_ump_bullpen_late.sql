-- 2026-08-22 Persist fetched-but-lost signal fields on mlb_game_context.
-- Audit finding: get_pitcher_last_outing, detect_opener, get_umpire_stats
-- (numeric fields), and bullpen pitching_7_9_* were all being fetched by
-- game_context.py but ONLY printed / used inline in NRFI calc — the
-- returned data was thrown away between game_context and every downstream
-- reader (ensemble scorer, prop template, Jerry synth, coverage chip).
--
-- Adding all columns as nullable so historical rows continue reading
-- cleanly. Upload has an auto-strip retry loop so this migration + the
-- game_context.py add-to-context-dict change are safe to ship in either
-- order.

ALTER TABLE mlb_game_context
  -- Fatigue (added earlier today via commit a3607aec, migrating column now)
  ADD COLUMN IF NOT EXISTS home_pitcher_last_outing_pitches  smallint,
  ADD COLUMN IF NOT EXISTS home_pitcher_last_outing_ip       numeric(4, 1),
  ADD COLUMN IF NOT EXISTS away_pitcher_last_outing_pitches  smallint,
  ADD COLUMN IF NOT EXISTS away_pitcher_last_outing_ip       numeric(4, 1),

  -- Openers — detect_opener() bool. Prior: consumed inline by NRFI calc
  -- and printed. Now: prop scorer can demote outs_over / ks_over lines
  -- for bullpen-game starters (user-flagged "Cameron 5-day rest" vector).
  ADD COLUMN IF NOT EXISTS home_is_opener  boolean,
  ADD COLUMN IF NOT EXISTS away_is_opener  boolean,

  -- Late-inning bullpen — pitching_7_9_* is the key signal for late-game
  -- props + total late-game modeling. Was in the get_bullpen_stats
  -- select=* return but only bullpen_era + save_pct were persisted.
  ADD COLUMN IF NOT EXISTS home_bullpen_late_era   numeric(4, 2),
  ADD COLUMN IF NOT EXISTS away_bullpen_late_era   numeric(4, 2),
  ADD COLUMN IF NOT EXISTS home_bullpen_late_k_pct numeric(4, 1),
  ADD COLUMN IF NOT EXISTS away_bullpen_late_k_pct numeric(4, 1),

  -- Umpire numeric fields — mlb_umpires row has over_rate,
  -- k_rate_above_avg, nrfi_rate, run_factor, games_sampled but only the
  -- ump NAME + formatted umpire_note string were persisted. Prop coverage
  -- check now surfaces "umpire_k signal missing" whenever the numeric
  -- fields aren't present; this fills that gap.
  ADD COLUMN IF NOT EXISTS umpire_over_rate         numeric(4, 3),
  ADD COLUMN IF NOT EXISTS umpire_k_rate_above_avg  numeric(4, 3),
  ADD COLUMN IF NOT EXISTS umpire_nrfi_rate         numeric(4, 3),
  ADD COLUMN IF NOT EXISTS umpire_run_factor        smallint,
  ADD COLUMN IF NOT EXISTS umpire_games_sampled     smallint;

-- Standard schema-cache reload so PostgREST sees new columns immediately.
NOTIFY pgrst, 'reload schema';
