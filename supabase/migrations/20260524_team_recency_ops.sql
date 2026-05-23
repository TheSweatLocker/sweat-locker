-- Team recency stats — L7/L14 OPS + wRC+ proxy.
--
-- Added 2026-05-23 after Savant date-filter approach hit blockers
-- (project_v11_recency_wrc memory). Self-aggregated from MLB Stats API
-- team gameLog by enrich_team_recency.py (nightly cron).
--
-- Used by:
--   - generate_props.score_batter_hits / _under for fade gating
--   - game_context.compute_confluence as offense_heat vote (richer than
--     existing offense_drift which uses runs/game with cluster-luck noise)

alter table mlb_team_offense
    add column if not exists ops_last7      numeric(5,3),
    add column if not exists ops_last14     numeric(5,3),
    add column if not exists wrc_proxy_l14  numeric(5,1);

comment on column mlb_team_offense.ops_last7
    is 'Self-aggregated OPS over last 7 calendar days. Updated nightly by enrich_team_recency.py.';
comment on column mlb_team_offense.ops_last14
    is 'Self-aggregated OPS over last 14 calendar days. Updated nightly.';
comment on column mlb_team_offense.wrc_proxy_l14
    is 'wRC+ proxy derived from ops_last14 vs league avg. Crude (no park adjustment). 100 = avg.';
