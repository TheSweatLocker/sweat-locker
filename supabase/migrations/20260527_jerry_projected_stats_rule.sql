-- Jerry MLB prompt rule: cite all five pitcher projections + WHIP color.
--
-- Companion to 20260527_jerry_projected_ks_rule.sql. The K-Override rule was
-- step 1; this generalizes to every pitcher prop so the same "Over the
-- projected X" social-copy gap can't open on BB / Hits / Outs / ER.
--
-- The struct now ships pitchers.{side}.projected_ks/bb/hits/outs/er plus
-- whip + whip_flag (elite/shaky). All values come from the same JSON cache
-- the prop scorers use.

UPDATE prompt_templates
SET template = template || E'\n\nPROJECTED-STAT RULES (struct fields pitchers.{side}.projected_*, extended 2026-05-27):\n'
    || E'- The struct ships projected_ks, projected_bb, projected_hits, projected_outs, projected_er per starter. ALL of these are the same numbers the prop scorers use.\n'
    || E'- When citing a pitcher prop in The Play section, ALWAYS pair the recommendation with the concrete projected number from the struct ("DeGrom Under 1.5 BB — projects 1.1 BB"). Never say "Over/Under the projected X" without quoting X.\n'
    || E'- In The Pitcher Matchup section, you may cite projection numbers as descriptive context even when no prop of that type is published — e.g., "projects ~5 Ks, 1 BB, 6 hits over 5+ innings" — but do NOT pitch an Over/Under angle for a prop type that is not in best_plays.\n'
    || E'- WHIP rules (struct field pitchers.{side}.whip + whip_flag):\n'
    || E'  * whip_flag == "elite" (WHIP ≤ 0.95): cite as "X.XX WHIP — elite command/suppression" in The Pitcher Matchup.\n'
    || E'  * whip_flag == "shaky" (WHIP ≥ 1.50): cite as "X.XX WHIP — traffic concern" and tie to whichever prop type best matches (BB Over, Hits Over, ER Over).\n'
    || E'  * Mid-range WHIP (0.96-1.49): do not cite WHIP unless it serves a specific narrative beat.\n'
    || E'- If the starter is flagged with "last outing only X.X IP" or "opener/short," append a one-clause caveat to projection citations ("if he goes 5+ innings, projects ~5 Ks").'
WHERE name = 'game_read_rules'
  AND sport = 'MLB'
  AND template NOT LIKE '%PROJECTED-STAT RULES (struct fields pitchers%';

NOTIFY pgrst, 'reload schema';
