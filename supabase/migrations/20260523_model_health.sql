-- model_health: rolling direction-accuracy + status for the v4 (and future)
-- prediction models. Read by generate_sweat_card and play_of_day to throttle
-- v4-derived signals during cold stretches.

create table if not exists model_health (
    id                  bigserial primary key,
    computed_date       date        not null,
    model_version       text        not null,
    window_5d_ml_w      integer,
    window_5d_ml_l      integer,
    window_5d_ml_pct    numeric(5,2),
    window_7d_ml_w      integer,
    window_7d_ml_l      integer,
    window_7d_ml_pct    numeric(5,2),
    window_14d_ml_w     integer,
    window_14d_ml_l     integer,
    window_14d_ml_pct   numeric(5,2),
    window_30d_ml_w     integer,
    window_30d_ml_l     integer,
    window_30d_ml_pct   numeric(5,2),
    -- 'cold' (5d <48%), 'neutral', 'hot' (5d >56%), 'insufficient_sample'
    status              text not null check (status in ('cold','neutral','hot','insufficient_sample')),
    created_at          timestamptz default now(),
    unique (computed_date, model_version)
);

create index if not exists model_health_status_idx
  on model_health (status, computed_date desc);

create index if not exists model_health_recent_idx
  on model_health (computed_date desc);

-- Force PostgREST schema cache reload — without this, PGRST204 errors
-- on writes for up to 10 minutes while cache catches up.
NOTIFY pgrst, 'reload schema';
