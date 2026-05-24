-- cohort_display_config: backend-driven labels for the CohortDashboard.
--
-- Previously the (tier_name -> pretty label + group) mapping lived in
-- app/components/CohortDashboard.tsx as a hardcoded COHORT_LABELS dict.
-- That meant adding a new cohort on the backend required an app
-- rebuild + App Store resubmit to surface it. Now: backend writes
-- the row, app picks it up on next reload.
--
-- The dashboard reads this table and joins it against mlb_tier_calibration
-- to render. Tiers without a row here are intentionally hidden
-- (internal/experimental cohorts that aren't ready for user view).

create table if not exists cohort_display_config (
    id              bigserial primary key,
    tier            text        not null unique,
    label           text        not null,
    description     text,
    group_name      text        not null,
    group_order     integer     not null default 99,
    display_order   integer     not null default 99,
    -- Filter to sport. NULL = applies to all sports.
    sport           text,
    -- Active flag for sunset/promote workflow without deletion
    is_active       boolean     not null default true,
    created_at      timestamptz default now(),
    updated_at      timestamptz default now()
);

create index if not exists cohort_display_config_active_idx
  on cohort_display_config (is_active, group_order, display_order);

create index if not exists cohort_display_config_sport_idx
  on cohort_display_config (sport);

-- Seed initial labels matching the prior in-app COHORT_LABELS map.
-- group_order: NRFI=1, Signal Stack=2, Spread Edge=3, Lineup Edge=4,
--              Prop Tiers=5, Dawg=6.
insert into cohort_display_config (tier, label, description, group_name, group_order, display_order, sport)
values
    -- NRFI band cohorts
    ('nrfi_prime_90_94',     'NRFI 90-94 (sweet spot)',  'Score 90-94 — strongest NRFI band',          'NRFI', 1, 1, 'mlb'),
    ('nrfi_volatile_95plus', 'NRFI 95+ (trap zone)',     'High score but volatile',                     'NRFI', 1, 2, 'mlb'),
    ('yrfi_lean_le40',       'YRFI ≤40 (early-run lean)','Low NRFI score = early runs likely',          'NRFI', 1, 3, 'mlb'),
    -- Confluence
    ('confluence_extreme_ge6','Confluence ≥+6 (extreme)','6+ signals stack — highest model conviction','Signal Stack', 2, 1, 'mlb'),
    ('confluence_prime_ge4', 'Confluence ≥+4 (PRIME)',   'PRIME tier signal alignment',                 'Signal Stack', 2, 2, 'mlb'),
    ('confluence_strong_2_3','Confluence 2-3 (STRONG)',  'Moderate signal alignment',                   'Signal Stack', 2, 3, 'mlb'),
    -- Spread delta
    ('spread_delta_ge2',     'Model edge ≥2 runs',       'Model disagrees with market by 2+',           'Spread Edge', 3, 1, 'mlb'),
    ('spread_delta_1_5_2',   'Model edge 1.5-2 (trap)',  'Mid-magnitude — historical trap zone',        'Spread Edge', 3, 2, 'mlb'),
    ('spread_delta_lt1',     'Model edge <1 (agree)',    'Model and market agree closely',              'Spread Edge', 3, 3, 'mlb'),
    -- Prop tiers
    ('k_under_strong',       'K Under STRONG',           'Strikeouts under, strong conviction',         'Prop Tiers', 5, 1, 'mlb'),
    ('k_under_prime',        'K Under PRIME',             null,                                          'Prop Tiers', 5, 2, 'mlb'),
    -- Dawg
    ('autofade_dog_high_conv',  'Dog w/ high model conviction', 'Model picks the dog strongly',       'Dawg', 6, 1, 'mlb'),
    ('autofade_chalk_high_mag', 'Fade chalk (high magnitude)',  'Heavy chalk that model dislikes',     'Dawg', 6, 2, 'mlb'),
    -- wRC+ cohorts
    ('wrc_diff_away_adv_ml', 'Away wRC+ advantage',      'Road team has hitter edge',                   'Lineup Edge', 4, 1, 'mlb'),
    ('wrc_diff_home_adv_ml', 'Home wRC+ advantage',       null,                                          'Lineup Edge', 4, 2, 'mlb')
on conflict (tier) do nothing;

-- Force PostgREST schema cache reload — without this, PGRST204 errors
-- on writes for up to 10 minutes while cache catches up.
NOTIFY pgrst, 'reload schema';
