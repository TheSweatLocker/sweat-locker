-- Extend model_health with totals direction tracking (OVER/UNDER split).
-- Required for auto-throttle of v4 OVER suppression — without separate
-- OVER vs UNDER columns, we can't make the asymmetric decision the
-- audit_v4_totals data showed was needed (43% OVER hit, 55% UNDER hit).

alter table model_health
    add column if not exists window_7d_total_over_w   integer,
    add column if not exists window_7d_total_over_l   integer,
    add column if not exists window_7d_total_over_pct numeric(5,2),
    add column if not exists window_7d_total_under_w  integer,
    add column if not exists window_7d_total_under_l  integer,
    add column if not exists window_7d_total_under_pct numeric(5,2),
    add column if not exists window_30d_total_over_w  integer,
    add column if not exists window_30d_total_over_l  integer,
    add column if not exists window_30d_total_over_pct numeric(5,2),
    -- Asymmetric suppression flags — populated by audit_v4_health.py
    -- Read by game_context.compute_primary_play + generate_sweat_card.find_total_edges
    add column if not exists over_suppressed          boolean default true,
    add column if not exists under_suppressed         boolean default false;

comment on column model_health.over_suppressed
  is 'True when v4 OVER picks should be filtered from card surfacing. Auto-flipped by audit_v4_health.py based on rolling 7d OVER hit rate vs 50% threshold.';

NOTIFY pgrst, 'reload schema';
