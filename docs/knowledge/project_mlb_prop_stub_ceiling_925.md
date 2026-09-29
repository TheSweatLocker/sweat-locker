---
name: project_mlb_prop_stub_ceiling_925
description: "99% of MLB props are coverage_stub because the last prop run is 14:00 ET, before lineups post. Only 14-34/day are confirmed, yielding 0-8 PRIME. Not a regression — the old 300-PRIME days were phantom stub props the 09-19 source gate stopped counting."
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-25T17:49:32.791Z
---

# MLB props: the ceiling is lineups, not the model

Andy 2026-09-25: "barely any MLB primes today, a bunch of leans, something is
wrong with prop analysis … two weeks ago the MLB prop pipeline wasn't like
this."

## What is actually happening

**99% of every day's MLB props are `lineup_state='coverage_stub'`** — rows for
players whose lineup is unconfirmed. They carry conviction 0, tier SKIP and
`_coverage_kill_gate: COVERAGE_TIER_UNPUBLISHABLE`, and can never publish.

Confirmed props per day, all-time: **3 to 34.** It has never been higher.

| date | total props | confirmed | confirmed PRIME |
|---|---|---|---|
| 09-11 | 3123 | 34 | 8 |
| 09-15 | 1641 | 30 | 6 |
| 09-20 | 1597 | 16 | 1 |
| 09-23 | 1443 | 28 | **0** |
| 09-24 | 1127 | 14 | **0** |
| 09-25 | 1607 | 19 | 4 |

So today (4 PRIME) is NORMAL. 09-23/24 (zero) were the bad days.

## Root cause — run timing vs lineup posting

`mlb_pipeline.yml` crons: 10:00, 11:15, 12:30, 18:00 UTC = **06:00, 07:15,
08:30, 14:00 ET.** The last prop generation is 2pm ET. MLB lineups post roughly
2-3 hours before first pitch, i.e. ~16:00-19:00 ET. **Every run happens before
the lineups that would confirm the props.**

Evidence it is timing, not logic: confirmed props accrue across runs
(09-25: 12 confirmed at 11 UTC, 7 more at 15 UTC), and the one late run on
09-24 at 21 UTC added 2 more. A run after lineups post would convert stubs at a
much higher rate.

**Recommended fix:** add a late MLB prop refresh (~22:00-23:00 UTC / 18:00-19:00
ET) to re-attach lineups and upgrade stubs to confirmed. Weigh against
publishing close to first pitch and against publish-lock immutability.

## Why it LOOKED like a recent regression

It is not one. `generate_props.tier_for()` gained a **source gate on
2026-09-19** (see its docstring): banned families now return SKIP instead of a
real tier. Before that, generation stamped PRIME on families every downstream
surface filtered out — its own audit measured **1,524 PRIME over 7 days with
only 216 reachable by a user, 85.8% phantom**, dominated by rbis_under /
runs_under / total_bases_under.

So the internal PRIME count fell ~300 → ~30 on 09-19 because the fake ones
stopped being counted. The publishable pipeline did not change. Cite confirmed
PRIME, never raw tier counts.

## The O/U skew is a stub artifact — do not chase it

Raw direction splits swing wildly (09-23: 353 over / 1090 under; 09-25: 1471
over / 136 under). That is entirely inside the stub population. **Confirmed
props on 09-25 split 8 over / 11 under** — balanced. Any O/U analysis must
filter `lineup_state='confirmed'` first or it is measuring noise.

**How to apply:** every MLB prop query for volume, tier mix or direction MUST
filter `lineup_state = 'confirmed'`. The raw table is ~98% unpublishable
placeholder and any percentage taken over it is meaningless. Related:
[[feedback_surface_records_trust_levels]], [[feedback_sample_size_with_pct]].

## THE ACTUAL REGRESSION (found after Andy pushed back)

Andy 2026-09-25: "I am fine with lineups not being confirmed, the majority of
props end up being players in the lineup; if we wait until lineups confirmed
it is 2-3 hours before game, users want to make bets in the morning."

He is right, and the data backs him: **92-98% of coverage_stub props actually
settled** (the player played). Only 2-8% voided, and a void already returns the
stake as NO_ACTION. Gating out ~98% of the prop universe to avoid a 2-8% void
rate is a bad trade, and waiting for confirmation is useless to a morning
bettor.

**The morning path already exists and silently died.**
`generate_props.fetch_projected_lineup()` pulls the team's most recent batting
order as a fallback "so props can still surface in the morning with a PROJECTED
tag, refreshed to CONFIRMED once today lineup lands."

`lineup_state='projected'` was written 08-22..08-26 (4-11/day) and has been
**ZERO every day since 2026-08-27.** Total ever: 11 rows.

Timeline from mlb_pipeline_props:
  08-20/21  confirmed 30-45, stubs 0        healthy
  08-22..26 projected 4-11, confirmed 25-48
  08-27+    projected 0 FOREVER
  09-25     confirmed 19, stubs 1588

Suspect commit window: 6cd58526 (08-25, "kill duplicate rows via UNIQUE index +
on_conflict on POST") sits immediately before the break. NOT yet confirmed as
the cause — sweep_prop_coverage gained an overwrite watchdog on 09-13 that
protects PRIME/STRONG/LEAN rows with ignore-duplicates, but a `projected` row
at a lower tier would not be protected by that guard.

**The fix is to revive the PROJECTED path, not to run the pipeline later.**
Running later (my first recommendation) was wrong for the product — it trades
away the morning window users actually bet in.
