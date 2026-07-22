-- 2026-07-22 — Absolute home-vs-away confluence lean (display-facing).
--
-- signal_confluence_net is MODEL-RELATIVE (support − against relative to
-- the model's projected_spread direction). It gates primary_play PRIME
-- ML thresholds correctly, but as a raw DISPLAY value it misreads.
--
-- Concrete: MIN@CLE 7/22 had breakdown {HOME 4, AWAY 2}. model_pick=away
-- (v3 projected_spread=-0.29 → 'away'). Stored confluence_net = 2 − 4 = −2.
-- Users see -2 in the app and read "2-signal away lean" when actually
-- 4/6 signals point HOME.
--
-- signal_confluence_home_lean = (home_signals − away_signals). Direction-
-- agnostic. Downstream app + jerry read should prefer this for display.
-- primary_play gate keeps using confluence_net (correct for fav-only
-- playable ML picks per _ml_playable design).

ALTER TABLE mlb_game_context
  ADD COLUMN IF NOT EXISTS signal_confluence_home_lean INT;

NOTIFY pgrst, 'reload schema';
