-- tier_integrity_findings
--
-- Stores results of nightly audit_tier_integrity.py runs. Each row is a
-- (prop_type, direction, higher_tier, lower_tier) pair where the lower
-- tier's 30d hit rate exceeded the higher tier's by >= 2pt — i.e. the
-- conviction labels are inverted.
--
-- Backside (app receipts dashboard) can read this to surface a
-- "tier integrity warning" badge when drift is active.

create table if not exists tier_integrity_findings (
    id              bigserial primary key,
    computed_date   date        not null,
    prop_type       text        not null,
    direction       text        not null,
    higher_tier     text        not null,
    lower_tier      text        not null,
    higher_rate     numeric(6,4) not null,
    higher_n        integer     not null,
    lower_rate      numeric(6,4) not null,
    lower_n         integer     not null,
    delta_pct       numeric(6,2) not null,
    severity        text        not null check (severity in ('warn', 'critical')),
    created_at      timestamptz default now(),
    unique (computed_date, prop_type, direction, higher_tier, lower_tier)
);

create index if not exists tier_integrity_findings_date_idx
  on tier_integrity_findings (computed_date desc);

create index if not exists tier_integrity_findings_severity_idx
  on tier_integrity_findings (severity, computed_date desc);
