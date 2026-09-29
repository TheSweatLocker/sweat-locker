---
name: feedback-prop-graph-rendering-912
description: "🚨 Prop Jerry L5/L10 graphs going missing every morning. Runbook: check pagination in synth first, verify prop_jerry_reads.input_snapshot.render_sections.recent_form is populated, never touch app render. Root class: PostgREST 1000-row silent truncation."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-12T13:44:13.753Z
---

# Prop Jerry L5/L10 graph rendering — daily audit runbook

Andy 2026-09-12 (multiple rounds): "graphs were working yesterday
via backend fix without an app update... it worked on the same
build... I need this to be fixed and stop happening every day...
we are live in the app store what in the absolute duck."

Committed so I stop losing an hour every morning on this same class
of bug.

## The data path — memorize this

```
mlb_pipeline_props.signals._stat_last10
  ↓ (backfill_prop_lookback.py populates from MLB Stats API gameLog)
generate_prop_jerry_synthesis.py → render_prop_template.py
  ↓ (writes sections dict with recent_form.rows)
prop_jerry_reads.input_snapshot.render_sections.recent_form
  ↓ (fetched by app in fetchPipelineProps merge, keyed by
     game_id|player_name|prop_type|direction)
app/index.tsx:15287 → const rf = sections.recent_form
  ↓ (renders bar chart at line 15313-15369 when rf.rows.length > 0)
Bar chart on the prop card
```

**When graphs go missing, check the chain in reverse — never touch
the app render.** Andy has confirmed multiple times: same build
worked yesterday, doesn't today, therefore backend regression.

## First-check runbook (60 seconds)

Query to find where the chain breaks for a specific pitcher prop
Andy names:

```python
# 1. Does mlb_pipeline_props have the prop with _stat_last10?
GET /rest/v1/mlb_pipeline_props?game_date=eq.<today>
    &player_name=eq.<Name>&prop_type=eq.<type>&select=tier,signals

# 2. Does prop_jerry_reads have a row for it AT ALL?
GET /rest/v1/prop_jerry_reads?game_date=eq.<today>&sport=eq.MLB
    &player_name=eq.<Name>&prop_type=eq.<type>&direction=eq.<dir>
    &select=input_snapshot

# 3. If row exists, does input_snapshot.render_sections.recent_form.rows
#    have entries?
```

**Break-point verdict:**
- `_stat_last10` missing → backfill_prop_lookback couldn't find player
  (MLB Stats API lookup failed) OR STAT_MAP doesn't cover that prop
  family. Do NOT expand STAT_MAP to new families unless the app is
  wired for them — that's what surfaced rbis/tb/hr props on Sweat
  Card 9/12 and broke user trust.
- `_stat_last10` populated but no prop_jerry_reads row → synth cron
  fetch didn't reach that prop. Almost always the PostgREST 1000-row
  cap (see below). Also check for DUPLICATE prop rows.
- Row exists but no recent_form → render_prop_template returned
  sections without recent_form (means `stat_rows` was falsy when it
  ran; check timing vs backfill).

## The PostgREST 1000-row landmine — patched 4 sites 2026-09-12

PostgREST caps `.select()` responses at 1000 rows by default,
regardless of the `limit` query param — only server-side
`PGRST_DB_MAX_ROWS` raises the ceiling. Daily MLB prop count is
~3000. Any fetch without Range-header pagination silently truncates
past row 1000, dropping PRIME/STRONG pitcher props that sit at
higher IDs in the tier-then-conviction order.

**Fixed today** (all use `Range: 0-999`, `1000-1999`, etc. until
chunks < 1000, ordered by id.asc):
- `grade_props.py` fetch_ungraded → commit 77f138eb
- `aggregate_daily_records.py::_load_props_by_sport` → commit 4808ca02
- `generate_prop_jerry_synthesis.py::run_for_sport` → commit 6f939563

**If a NEW graph-render regression appears, check first**: any
fetch against `mlb_pipeline_props` or `prop_jerry_reads` without
Range pagination? Fix the site, don't touch anything else.

## Duplicate prop rows — separate landmine

`mlb_pipeline_props` sometimes has TWO rows for the same
(player, prop_type, direction) at different conviction values
(e.g., Peter Lambert ks_over conv 69 AND conv 66 on 9/12). Synth
writes prop_jerry_reads for one, app displays the other → missing
graph even when synth "succeeded."

`dedup_prop_dupes.py` only handles opposite-direction dupes
(ks_over + ks_under), NOT same-direction dupes. Queued fix:
extend that script to collapse same-direction dupes to the
higher-conviction row.

## What NOT to do

- **Never touch `app/index.tsx` render code** for a graph regression.
  Andy has confirmed the same app build renders graphs correctly when
  the backend data is complete. App-side edits waste time.
- **Never expand `MLB_STAT_MAP` in `backfill_prop_lookback.py`** to
  new prop families (rbis/tb/hr/runs/batter_ks) without user OK. That
  populates L5/L10 for those families → tier composer promotes them
  → they surface on Sweat Card for the first time ever. Andy 9/12:
  "we dont even list rbi in prop jerry i have actually never seen
  rbis plays until today on our first fucking full launch day."
- **Never edit `generate_prop_jerry_synthesis.py` internal counters**
  without checking Python variable scope. My 9/12 fix introduced
  UnboundLocalError that crashed the entire synth run and wrote 0
  rows. Test with `python -c "import ast; ast.parse(...)"` OR run
  the actual script in dry-mode before assuming a code path works.

## Daily verification query (run FIRST every morning)

```python
# Count PRIME/STRONG pitcher props today with populated recent_form
props = GET mlb_pipeline_props game_date=today
        tier=in.(PRIME,STRONG)
        prop_type=in.(ks_over,ks_under,outs_over,outs_under,
                      er_over,er_under,ha_over,ha_under,
                      bb_over,bb_under)
# For each, check prop_jerry_reads for matching row with
# input_snapshot.render_sections.recent_form.rows
# Coverage should be 100%. If under 90%, synth cron incomplete —
# rerun `generate_prop_jerry_synthesis.py --sport MLB --force --tier-gate NONE`
```

If coverage < 100% and pagination is confirmed working, dig for
duplicates OR MLB Stats API name-mismatch cases.

## Related memories

- [[feedback_grading_zero_fail_912]] — sibling grading pipeline
  zero-fail principle; same PostgREST 1000-row landmine class
- [[feedback_morning_audit_format_912]] — canonical morning audit
  order; graphs-populated check is part of Prop Pipeline section
- [[project_prop_jerry_coverage_gap_911]] — earlier note on
  jerry_reads population; superseded by this runbook
- [[project_mlb_prop_l5_l10_gap_912]] — earlier attempt at
  documenting this issue; this file replaces it as the canonical
  runbook

## Commits that landed the fix stack 2026-09-12

- 77f138eb — grade_props pagination + STAT_MAP grading expansion
- 4808ca02 — sharp_card aggregator pagination
- 6f939563 — prop synth pagination (THIS is the graph fix)
- f4947156 — UnboundLocalError fix after 6f939563 broke the counter

## THE PROCEDURE THAT WORKED — follow this order next time

Andy 2026-09-12: "why was that so fucked difficult... commit to
memory on this topic so we dont have to go through that again."

When Andy reports missing L5/L10 graphs on Prop Jerry — even ONE
card — run these steps IN ORDER. Do NOT skip to code edits.

### Step 1 — 60-second diagnosis (read-only)

```python
# Coverage check on PRIME/STRONG pitcher props (the ones users see)
props = paginated GET mlb_pipeline_props
        game_date=eq.<today> tier=in.(PRIME,STRONG)
        prop_type=in.(ks_over,ks_under,outs_over,outs_under,
                      er_over,er_under,ha_over,ha_under,
                      bb_over,bb_under)
for each prop:
  GET prop_jerry_reads for (sport,game_date,player,type,direction)
  check input_snapshot.render_sections.recent_form.rows
Report: N/total = X% coverage
```

Under 90% coverage → proceed to Step 2. At 100% → app cache stale,
tell Andy to pull-to-refresh.

### Step 2 — nuke banned families FIRST if present

If today's `mlb_pipeline_props` contains ANY of these prop_types at
PRIME/STRONG, DELETE them from BOTH tables:

```
rbis_over, rbis_under, total_bases_over, total_bases_under,
hr_over, hr_under, batter_ks_over, batter_ks_under,
runs_over, runs_under, hits_under
```

```python
DELETE /rest/v1/prop_jerry_reads?sport=eq.MLB
       &game_date=eq.<today>&prop_type=in.(<banned list>)
DELETE /rest/v1/mlb_pipeline_props?game_date=eq.<today>
       &prop_type=in.(<banned list>)
```

Reason: these families were never historically on the app, don't
have app render mappings, and clutter Prop Jerry with prop types
users don't recognize. Andy: "we dont even list rbi in prop jerry."

### Step 3 — rerun synth

```
python generate_prop_jerry_synthesis.py --sport MLB --force --tier-gate NONE
```

Takes 2-5 min. Uses paginated fetch (post 6f939563) so it processes
ALL props, not just the first 1000. Writes `render_sections.recent_form`
to prop_jerry_reads for every prop with `_stat_last10` in signals.

### Step 4 — re-verify coverage

Repeat Step 1 query. Should hit 90%+ (a couple stragglers are
normal — usually duplicate-row cases or players MLB API can't find).

### Step 5 — surgical fill for stragglers if needed

For 1-2 remaining pitcher props (Peter Lambert / Michael King class),
manually render + upsert. But the `on_conflict` clause on
`prop_jerry_reads` needs the actual unique constraint name — if
the schema constraint doesn't match `sport,player_name,prop_type,
direction,game_date`, the upsert 42P10s. Usually not worth the
manual fix — 92%+ coverage on PRIME/STRONG pitcher = graphs on
the props users care about.

### Step 6 — tell Andy to refresh the app

The DB is now correct. App may be holding a stale cache from
before the synth run. Pull-to-refresh or app restart.

## Anti-patterns to AVOID next time

- **Don't touch `app/index.tsx` render code.** Same build renders
  graphs correctly when backend is complete. Confirmed multiple
  times.
- **Don't expand `MLB_STAT_MAP` in backfill_prop_lookback.** Adding
  batter families opens PRIME/STRONG promotion for prop_types the
  app has never rendered → surfaces alien prop types on the card.
  My 9/12 attempt did this and Andy noticed within minutes.
- **Don't add / reference `done` counters mid-loop without checking
  Python scope.** My 9/12 attempt crashed synth entirely with
  UnboundLocalError, wrote 0 rows on the "catch-up" rerun. Always
  `python -c "import ast; ast.parse(open('...').read())"` before
  running, and verify by scanning for `done = 0` initialization sites.
- **Don't propose "belt-and-suspenders" caps** like the
  `_NEVER_PUBLISH_TYPES` set in prop_ensemble_scorer if you've just
  reverted the STAT_MAP that promoted them. The scorer runs on
  refresh-existing and may re-promote, but that pathway is not the
  primary source; addressing at STAT_MAP alone is sufficient.
- **Don't chase "why was it different yesterday"** — the answer is
  usually cron-timing / row-truncation-luck. Skip that rabbit hole
  and just run Steps 1-6.

## Escalation trigger

If Steps 1-6 don't hit 90%+ coverage, THEN consider deeper
investigation — could be:
- Duplicate prop rows (`dedup_prop_dupes.py` doesn't handle
  same-direction dupes yet — file needs a fix)
- MLB Stats API name-mismatch (backfill_prop_lookback can't find
  the player)
- Cron sequencing issue (backfill ran AFTER synth)

Everything else is Steps 1-6. Don't invent a new fix.
