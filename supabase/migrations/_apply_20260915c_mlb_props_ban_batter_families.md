# 🚨 2026-09-15 c — EMERGENCY: ban batter families from Prop Jerry view

**Apply IMMEDIATELY in Supabase SQL editor.** Paste the block below and click
Run. Idempotent (uses CREATE OR REPLACE VIEW) — safe to re-run.

**Why:** the 4 batter families (runs / rbis / total_bases / hr) leaked into
`v_mlb_props_publishable` in LEAN-tier volume (~180 rows on the 9/15 slate).
App has no PROP_TYPE_LABELS entries or dedicated label formatters for them
→ render loop swamped → visible lag + Prop Jerry crash.

This is the SERVER-SIDE kill. Client-side filter + Python composer ban are
shipped alongside as belt-and-suspenders.

**After paste:** the view rebuilds instantly. Pull Prop Jerry in the app —
those 8 prop_type variants disappear immediately.

```sql
CREATE OR REPLACE VIEW public.v_mlb_props_publishable AS
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
    COALESCE(p.refit_conviction, p.conviction) AS display_conviction,
    (p.tier = 'SKIP' AND UPPER(COALESCE(pj.call_verdict, '')) = 'BACK') AS is_skip_back,
    pj.call_verdict AS jerry_verdict,
    pj.short_read  AS jerry_short_read,
    pj.conviction  AS jerry_conviction
FROM mlb_pipeline_props p
LEFT JOIN prop_jerry_reads pj
    ON pj.game_id     = p.game_id
   AND pj.player_name = p.player_name
   AND pj.prop_type   = p.prop_type
   AND pj.direction   = p.direction
   AND pj.sport       = 'MLB'
   AND pj.game_date   = p.game_date
WHERE
    (
        p.signals->>'_coverage_kill_gate' IS NULL
        OR LOWER(p.signals->>'_coverage_kill_gate') IN ('false','0','no','')
    )
    AND (
        p.tier != 'SKIP'
        OR UPPER(COALESCE(pj.call_verdict, '')) = 'BACK'
    )
    AND (
        p.tier IN ('PRIME','STRONG')
        OR (
            jsonb_typeof(p.signals->'_stat_last10') = 'array'
            AND jsonb_array_length(p.signals->'_stat_last10') > 0
        )
    )
    AND p.prop_type NOT IN (
        'runs_over',        'runs_under',
        'rbis_over',        'rbis_under',
        'total_bases_over', 'total_bases_under',
        'hr_over',          'hr_under'
    );

NOTIFY pgrst, 'reload schema';
```
