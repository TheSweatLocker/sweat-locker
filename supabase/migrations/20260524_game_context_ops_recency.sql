-- Pass-through fields for L7/L14 team OPS recency on mlb_game_context.
-- Sourced from enrich_team_recency.py → mlb_team_offense, then game_context
-- copies the relevant per-team values into the game context payload so
-- generate_props + downstream consumers can read them via the same ctx dict.

alter table mlb_game_context
    add column if not exists home_ops_last7        numeric(5,3),
    add column if not exists home_ops_last14       numeric(5,3),
    add column if not exists home_wrc_proxy_l14    numeric(5,1),
    add column if not exists away_ops_last7        numeric(5,3),
    add column if not exists away_ops_last14       numeric(5,3),
    add column if not exists away_wrc_proxy_l14    numeric(5,1);

comment on column mlb_game_context.home_ops_last7
    is 'Home team L7 OPS (self-aggregated by enrich_team_recency.py)';
comment on column mlb_game_context.home_wrc_proxy_l14
    is 'Home team wRC+ proxy from L14 OPS — 100=avg, no park adjustment';
