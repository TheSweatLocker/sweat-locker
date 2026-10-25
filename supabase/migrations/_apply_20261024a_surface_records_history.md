# 2026-10-24a — surface_records_history (B172)

**Paste the block below into the Supabase SQL editor and Run once.**
Idempotent (`IF NOT EXISTS` throughout), safe to re-run.

## Why this is needed

On **2026-10-23 the all-time Sharp went +81.21u → +36.77u overnight.** The
W-L barely moved; only the pricing basis changed. Nothing alerted, nothing was
logged, and the only detector in the system was Andy reading the card the next
morning and asking why the units were half what they had been.

It was undetectable **after** the fact for a specific reason: `surface_records`
holds exactly one row per `(surface, sport, window_key)`, **updated in place**
by `compute_surface_records.py`. There is no history anywhere, so once the
number moved the previous value was simply gone — it could not be diffed, and
the size of the move could only be recovered from a code comment that happened
to record it.

## What is already shipped and waiting on this

| piece | state |
|---|---|
| `mlb_pipeline/snapshot_surface_records.py` | written, wired into `daily_card.yml` right after `compute_surface_records.py` |
| `watchdogs.check_record_discontinuity` | written and registered in `CHECKS` |

Both are **inert until this migration runs** — the snapshot writer gets a 404,
and the watchdog returns `None` on 404 by design so it cannot cry wolf about a
table that does not exist yet. Nothing breaks in the meantime; it simply does
not monitor anything.

## After running it

1. Take the first snapshot by hand so the clock starts today:
   ```
   python snapshot_surface_records.py --apply
   ```
2. The discontinuity check stays quiet until a **second** ET day lands — one
   snapshot is not a comparison. From Sunday's nightly onward it is live.
3. Verify it is actually wired, not just green:
   ```
   python watchdogs.py --check record_discontinuity --dry-run
   ```
   Before the migration this prints `clean` because the table 404s, which is
   indistinguishable from a real pass. After two snapshots exist it is a real
   result.

## What it alerts on, and what it deliberately ignores

The test is **not** "units changed" — units change every day games grade, and
an alert on that is noise nobody reads. The test is whether units moved
**further than the newly graded picks could account for**: budget is a
generous `2.0 × new_picks + 1.0`. A record that moves without new results is a
methodology change or a bug, and both need saying out loud before a subscriber
finds them.

Severity is `CRITICAL` when a `lifetime` or `epoch` window is involved (that
is the user-facing headline) and `WARNING` otherwise.

```sql
CREATE TABLE IF NOT EXISTS public.surface_records_history (
    id              bigserial PRIMARY KEY,
    snapshot_date   date        NOT NULL,
    captured_at     timestamptz NOT NULL DEFAULT now(),
    surface         text        NOT NULL,
    sport           text        NOT NULL,
    window_key      text        NOT NULL,
    wins            integer,
    losses          integer,
    pushes          integer,
    picks_count     integer,
    hit_rate        numeric,
    units_net       numeric,
    roi_pct         numeric,
    epoch_start     date,
    last_pick_date  date,
    source_computed_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS surface_records_history_key
    ON public.surface_records_history (snapshot_date, surface, sport,
                                       window_key);

CREATE INDEX IF NOT EXISTS surface_records_history_lookup
    ON public.surface_records_history (surface, sport, window_key,
                                       snapshot_date DESC);

COMMENT ON TABLE public.surface_records_history IS
    'Daily (ET) snapshot of surface_records so day-over-day movement in a '
    'published record is measurable. Added 2026-10-24 after the all-time '
    'Sharp moved +81.21u -> +36.77u overnight with no detector but a human '
    'reading the card. See B172.';

COMMENT ON COLUMN public.surface_records_history.snapshot_date IS
    'ET date, not UTC. A UTC boundary falls at 8pm ET and would split one '
    'slate across two snapshot dates.';

GRANT SELECT ON public.surface_records_history TO anon, authenticated;
GRANT ALL    ON public.surface_records_history TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.surface_records_history_id_seq
    TO service_role;

NOTIFY pgrst, 'reload schema';
```

## One note on the grain

Deliberately a **daily snapshot**, not an audit trigger on `surface_records`.
The writer runs a handful of times a day, a daily grain answers the question,
and a trigger on every UPDATE would store thousands of identical rows for the
same answer. The snapshot upserts with `merge-duplicates` on
`(snapshot_date, surface, sport, window_key)` — **not** `ignore-duplicates`,
because that would freeze the first run of the day and miss exactly the
intraday change this exists to catch. The 10-23 move happened between two
same-day runs.
