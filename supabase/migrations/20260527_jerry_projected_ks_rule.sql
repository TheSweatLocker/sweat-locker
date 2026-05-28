-- Jerry MLB prompt rule: cite projected_ks as a concrete number when present.
--
-- Trigger: 2026-05-27 DeGrom incident. Jerry's narrative said "the K gap
-- favors deGrom by 6.0 strikeouts" (k_gap_vs_opp) as context for a BB Under
-- play, which read like a third recommendation. Social copy then paraphrased
-- as "Over projected Ks" with no number — unauditable. Adding a rule that
-- when pitchers.{side}.projected_ks is present, Jerry cites the concrete
-- number ("~5 Ks projected") rather than vague "K-gap favors X" framing.
--
-- Also: when no K-Over prop is being published for that starter, narrative
-- should NOT pitch a K Over angle. K context only when the prop is actually
-- in best_plays, OR cite as neutral context tied to the BB/Outs/ER play.

UPDATE prompt_templates
SET template = template || E'\n\nK-PROJECTION RULE (struct field pitchers.{side}.projected_ks, added 2026-05-27):\n'
    || E'- When ``pitchers.<side>.projected_ks`` is present, cite the concrete number in The Pitcher Matchup section: "deGrom projects ~5 Ks vs Houston" rather than vague "K gap favors deGrom by 6.0 strikeouts."\n'
    || E'- This number is the SAME projection the K-Over prop scorer uses. Do not invent your own K estimate from ``k_pct`` × IP; trust ``projected_ks``.\n'
    || E'- Do not pitch a K-Over angle in The Play section unless a K-Over prop is in best_plays. K context for a starter whose only published prop is BB/Outs/ER should appear in The Pitcher Matchup as descriptive context only.\n'
    || E'- The projection is for a typical start. If the starter is flagged with "last outing only X.X IP" or "opener/short," append a one-clause caveat ("if he goes 5+ innings").'
WHERE name = 'game_read_rules'
  AND sport = 'MLB'
  AND template NOT LIKE '%K-PROJECTION RULE (struct field pitchers%';

NOTIFY pgrst, 'reload schema';
