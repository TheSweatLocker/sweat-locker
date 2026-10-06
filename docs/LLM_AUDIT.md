# LLM_AUDIT — All LLM call sites, current cost, kill list

**Purpose**: Every Anthropic API call in the pipeline. What runs it, how often, why, and whether we should stop.

**Written**: 2026-08-28. Rerun quarterly.

**Bottom line**: We spend **~$2/day blended, ~$60/month** on LLM calls today.
Currently all Haiku 4.5 (already the cheap tier). Implementing the top-5
kill list drops that to **~$0.80/day, ~$25/month** (−60%).

---

## Global facts

- Every call goes to `api.anthropic.com/v1/messages` via raw `requests.post`
- Every call uses **`claude-haiku-4-5-20251001`** — no Sonnet, no Opus, no OpenAI
- No `anthropic` SDK anywhere — all raw HTTP
- One edge function at `supabase/functions/claude-proxy/index.ts` (client-side, no longer used)

## Pricing reference (late 2025)

| Model | Input $/MTok | Output $/MTok |
|---|---|---|
| Haiku 4.5 | $1 | $5 |
| Sonnet 4.5 | $3 | $15 |

---

## Every call site

| # | Script | Model · max_tok | Cron trigger | Calls/day | User-facing? | Kill? |
|---|---|---|---|---|---|---|
| 1 | `generate_mlb_game_reads.py::call_claude` (line 1164 + 3 retry paths) | Haiku · 1800 | mlb 6am + 8:30am backup + 2pm | ~60-80 | Yes — Numbers Panel narrative | **KILL** — duplicates #2 |
| 2 | `generate_jerry_synthesis.py::call_claude` (line 401 + hallucination retry) | Haiku · 1500 | mlb 3 crons | ~55-70 | Yes — headline `short_read`/`long_read` | Keep — but gate backup cron |
| 3 | `generate_prop_jerry_synthesis.py::call_claude` (line 205) | Haiku · 500 | mlb 3 crons (`--tier-gate NONE` disables it) | **0** | Yes when enabled | Already disabled — keep off |
| 4 | `generate_potd_narrative.py::call_claude` (line 68) | Haiku · 300 | mlb 3 crons | 2-3 | Yes — POTD hero | Keep — cheapest thing we do |
| 5 | `generate_daily_degen.py::build_narrative` (line 715) | Haiku · 240 | mlb 3 crons | 2-3 | Yes — Daily Degen tile | Keep |
| 6 | `generate_dawg_of_day.py::build_narrative` (line 789) | Haiku · 260 | mlb 3 crons | 2-3 | Yes — Dawg tile | Keep |
| 7 | `generate_ufc_game_reads.py::call_claude` (line 230) | Haiku · 900 | ufc 4 crons/wk | ~7/day fight-week avg | Yes — UFC card | **KILL** — duplicates #8 |
| 8 | `generate_ufc_fight_synthesis.py::call_claude` (line 247) | Haiku · 1500 | ufc 4 crons/wk | ~7/day fight-week avg | Yes — UFC verdict | Keep |
| 9 | `generate_nfl_game_reads.py::call_claude` (line 375 + 1 retry) | Haiku · 800 | nfl 7 crons/wk | ~16/day in-season | Yes — NFL card | Keep — bounded volume |
| 10 | `generate_ncaaf_game_reads.py::call_claude` (line 236) | Haiku · 800 | ncaaf 5 crons/wk | ~50-70/day CFB season | Yes — NCAAF card | **TRIM** — horizon 10d → 3d |
| 11 | `generate_ncaab_game_reads.py::call_claude` (line 263) | Haiku · 800 | ncaab 3 crons/day | **~300/day CBB season** | Yes — NCAAB card | **CUT to 1 cron** — biggest cost driver |

---

## Daily / monthly cost

| Scenario | Calls | Daily | Monthly |
|---|---:|---:|---:|
| MLB-only (Jun-Jul) | ~115 | $1.38 | ~$42 |
| MLB + UFC + NFL preseason (Aug) | ~140 | ~$1.80 | ~$54 |
| Peak all-sport overlap (Sep-Oct MLB+NFL+CFB+CBB early) | ~500 | ~$3.38 | ~$100 |
| Blended annual | | ~$2/day | **~$60/month** |

Your recent "$15 per 5-7 days" observation → ~$2-3/day → **matches the peak-day estimate**. Not a bug spike; just the peak-season rate.

---

## Kill list (in priority order)

### #1 — Kill `generate_mlb_game_reads.py` LLM call | Save ~$0.65/day (~$20/mo)

**Why:** Direct duplicate of `generate_jerry_synthesis.py`. The synthesis
docstring literally says it "reuses build_struct + fetch_games + fetch_props
+ fetch_potd" from game_reads, then makes its own Claude call with the
same context. Both run in the same cron. Since 2026-07-31 the synthesis
output is the headline product; the game_reads output only feeds the
"Numbers Panel" which is arguably a deterministic render.

**Cut:** Delete the `call_claude()` invocation in `generate_mlb_game_reads.py`
(or the whole script). Have Numbers Panel render from `jerry_reads.long_read`
or the deterministic struct.

**Risk:** medium — Numbers Panel display might briefly regress until we
verify it can read from the surviving synthesis output.

---

### #2 — Gate MLB backup cron's LLM steps | Save ~$0.45/day (~$14/mo)

**Why:** `mlb_pipeline.yml` line 20-21 says the 8:30am UTC cron is a
"BACKUP" for when 6am dropped. But the 5 LLM steps (Jerry synth, MLB
reads, POTD, Daily Degen, Dawg) run **unconditionally** on every cron.
Result: every LLM narrative gets written 3× per day (6am, 8:30am, 2pm)
even when the first two succeeded.

**Cut:** Wrap the 5 LLM steps in a freshness gate:
```yaml
if: github.event.schedule != '30 12 * * *'
```
Or better: check if `jerry_reads.generated_at` for today > 6am ET and
skip if so.

**Risk:** low. If 6am cron dropped, the 2pm cron still regenerates. The
only case we lose is 6am drop + backup fires (currently gets 8:30am
Jerry) — we'd wait until 2pm instead. Acceptable.

---

### #3 — Kill `generate_ufc_game_reads.py` LLM call | Save ~$0.10/day fight-week (~$3/mo)

**Why:** Same duplication as #1 but for UFC. Both `game_reads.py` and
`fight_synthesis.py` run back-to-back on all 4 UFC crons, hitting Claude
per-fight. Cut the game_reads call and derive the `jerry_cache` prose
from `fight_synthesis.py`'s output.

**Risk:** low — same fix pattern as #1.

---

### #4 — Cap NCAAB LLM to 1 cron/day | Save ~$0.55/day CBB season (~$17/mo Nov-Mar)

**Why:** `ncaab_pipeline.yml` fires 3 daily crons (10/16/20 UTC) and each
hits every game in the next 5 days. On a normal CBB night (~100 games)
that's 300 Claude calls/day at $0.55/day just for CBB. **Single biggest
cost driver Nov-Mar.**

**Cut:** Keep only the 20 UTC (4pm ET) cron for LLM narrative — after
confirmed spreads + line movement locks in. Drop LLM from the 10 UTC +
16 UTC steps (they can still pull data + generate ensemble picks;
just no narrative).

**Risk:** medium — if a user opens a game detail card before 4pm ET, no
narrative. Mitigation: server sends a fallback template with the
mechanical pick info.

---

### #5 — NCAAF horizon 10d → 3d | Save ~$0.35/day CFB season (~$11/mo Sep-Jan)

**Why:** `generate_ncaaf_game_reads.py` comment says "next 10 days"
window. CFB lines don't stabilize enough to justify writing narratives
for games 8-10 days out — those get overwritten multiple times before
users see them.

**Cut:** Change window to 3 days. On Sat, that catches Sat + Sun's
games; on Mon, that catches Tue-Thu games. All the meaningful ones.

**Risk:** low.

---

## Honorable mentions

- **`generate_prop_jerry_synthesis.py`** — already gated to zero via
  `--tier-gate NONE`. Don't accidentally re-enable. If prop template
  renderer regresses, we're looking at ~$0.60/day new spend (was 332
  calls/night when previously on).
- **Jerry hallucination retry** — 15-25% of games trigger a retry, doubling
  their cost. If retry rate ever climbs above 30%, cap retries to 1 or
  auto-downgrade the pick to LEAN instead of regenerating.
- **`generate_potd_narrative`** — 2-3 calls/day at 300 max_tok. Cheapest
  thing we run. Don't touch.

---

## What we'd save

| Cut | Daily | Monthly |
|---|---:|---:|
| #1 MLB game_reads duplicate | −$0.65 | −$20 |
| #2 Backup cron LLM gate | −$0.45 | −$14 |
| #3 UFC game_reads duplicate | −$0.10 | −$3 |
| #4 NCAAB 3→1 cron | −$0.55 (Nov-Mar) | −$17 |
| #5 NCAAF horizon trim | −$0.35 (Sep-Jan) | −$11 |
| **All 5** | **~$1.20/day off blended** | **~$36/month saved** |

Post-cuts: ~$25/month LLM spend total. Effectively free.

---

## What we should NEVER do

- **Upgrade to Sonnet** for game reads. Would triple the cost ($60 → $180/mo)
  for prose quality that users don't measurably care about at Haiku.
- **Add LLM to the ensemble scorer or grading path.** Ensemble picks must
  be deterministic. LLM is narrator only.
- **Re-enable `generate_prop_jerry_synthesis.py`** without pricing the
  regression on the template renderer first.

---

## Change log

| Date | Change |
|---|---|
| 2026-08-28 | Doc created + kill list drafted |
