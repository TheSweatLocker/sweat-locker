# Continuity Plan — running The Sweat Locker without Claude

Written 2026-09-29 at Andy's request: *"want to be prepared for an account
banning on claude so what should I copy and paste to another AI to keep this
going, I need a plan written out just in case I lose access to you."*

Read the first section before panicking. Almost none of this product depends on
Anthropic.

---

## 1. What is NOT at risk

The product is a git repository of Python, TypeScript and SQL running on
infrastructure **you own**:

| Layer | Where it runs | Owned by |
|---|---|---|
| Pipeline (~250 Python scripts) | GitHub Actions | You |
| Database + edge functions | Supabase | You |
| Mobile app | Expo / EAS → App Store | You |
| Subscriptions | RevenueCat | You |
| Source of truth | GitHub `TheSweatLocker/sweat-locker` | You |

Losing a Claude account costs you a **development assistant**. It does not
touch the running product. Picks keep generating, cards keep composing, the app
keeps serving. Nothing in the nightly crons calls Anthropic except the read
generators below.

---

## 2. The ONE runtime dependency, and how to replace it

Jerry's write-ups (game reads, prop reads, POTD narrative) call the Anthropic
API with `claude-haiku-4-5`. Everything else is your own models.

**As of 2026-09-29 this is a single choke point**, which makes swapping
providers a one-file job:

```
mlb_pipeline/anthropic_guard.py   →   call(prompt, model, max_tokens, timeout, api_key)
```

Four generators route through it and nothing else talks to the API directly:

- `generate_nhl_game_reads.py`
- `generate_nfl_game_reads.py`
- `generate_ncaaf_game_reads.py`
- `generate_jerry_synthesis.py`

**To move to OpenAI / Gemini / anything else:** rewrite the body of
`anthropic_guard.call()` to hit the new endpoint, keep the same signature and
the same return contract (text on success, `None` on transient failure,
`FatalLLMError` on a dead key). Do not touch the generators.

The second path is the edge function `supabase/functions/claude-proxy`, used by
the app. Same idea — one function to repoint.

**Cost reality check:** these are short prompts on a cheap model. Normal burn
is about **$1.50/day**. Any provider's cheapest tier covers it.

---

## 3. Credentials — where they live (never the values)

| Secret | Lives in | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | GitHub Actions secrets, Supabase edge secrets, `mlb_pipeline/.env` | **Never** in root `.env` or EAS |
| `SUPABASE_SERVICE_ROLE_KEY` | GitHub Actions secrets, `mlb_pipeline/.env` | Bypasses RLS — server only |
| `ODDS_API_KEY` | GitHub Actions secrets, Supabase edge secrets | via `odds-proxy` |
| `KENPOM_KEY` | GitHub Actions secrets | server-side since `kenpom_pull.py` |
| `EXPO_PUBLIC_SUPABASE_URL` / `_ANON_KEY` | EAS production env | public by design, RLS is the control |
| `EXPO_PUBLIC_REVENUECAT_KEY_IOS` | EAS production env | public SDK key by design |

`app.config.js` enforces this — it **fails the build** if a provider secret is
present in the environment. That control exists because on 2026-09-28 a leaked
`EXPO_PUBLIC_ANTHROPIC_API_KEY` bundled into build 1.0.1 cost **$548.41 in one
day**. Do not remove the guard; add to its `BLOCKED` list when a new paid
provider appears.

---

## 4. Getting a new AI assistant productive — what to hand it

In priority order. Hand it these paths, not pasted text:

1. **`CLAUDE.md`** — the working rules. Hard rules about never presenting an
   unverified pick, trusting the DB over training recall, terminology. Read
   this first; most costly mistakes are mistakes it warns about.
2. **`docs/knowledge/MEMORY.md`** — index of 380+ findings, one line each.
   Then the individual files it points to. **This is the expensive part of the
   project.** Every 🚨 entry is a bug that reached users or a model claim that
   turned out false. It was exported into the repo on 2026-09-29 precisely so
   it survives losing the tool that wrote it.
3. **`docs/PIPELINE_MAP.md`** and **`docs/TECHNICAL_REFERENCE.md`** — architecture.
4. **`docs/daily/`** — most recent 5 files. What broke lately and why.
5. **The code's own comments.** This repo documents *why* inline, at length,
   including measurements and retracted conclusions. A new assistant that reads
   the comment above a function usually does not need to be briefed on it.

**A short opening prompt that works:**

> This is a sports betting analytics product with a Python pipeline on GitHub
> Actions writing to Supabase, and an Expo/React Native app reading from it.
> Read `CLAUDE.md` for working rules, then `docs/knowledge/MEMORY.md` for
> accumulated findings. Hard rule: never present a specific pick to me without
> verifying the exact row in the database first — subagents have fabricated
> plausible-looking plays that did not exist. Trust the database over your
> training data for rosters and player-team assignments.

---

## 5. Daily operations — is it healthy?

Workflows in `.github/workflows/`: one per sport plus `daily_card.yml`
(cross-sport surfaces) and grading workflows.

**Health check, in order:**

```sql
-- Did every workflow start and end today?
select workflow, event, fired_at from workflow_heartbeat
where fired_at >= current_date order by fired_at;

-- Any step failures? (event='step_failed', meta names the script)
select meta, fired_at from workflow_heartbeat
where event = 'step_failed' and fired_at >= current_date;
```

**Signatures worth knowing on sight:**

- **`rc=1, duration_s=0`** → the script died at import. Almost always a missing
  dependency in the workflow's `pip install`, or a file that exists locally and
  not in CI. Cost a full day to diagnose twice.
- **A step that "succeeds" with 0 rows written** → the failure mode this repo
  keeps rediscovering. `.github/scripts/run_step.sh` exists to make failures
  visible instead of swallowed by `|| echo`.
- **A surface silently blank** → an explicit PostgREST `select` that omitted the
  column. It returns rows without the field and never errors. This has happened
  at least five times; see `feedback_explicit_select_silent_blanks`.

---

## 6. Rules that cost real money to learn

Do not let a new assistant relearn these:

1. **Never present a pick without querying the row first.** Documented case of a
   fabricated play: real player, plausible prop, plausible odds, zero rows in
   the database.
2. **Never restate a published record.** Excluding a bad window from a record
   looks like moving the goalposts. Fix the source and the future; leave what
   subscribers already saw alone. Records come from immutable
   `public_receipts`, not raw pipeline tables.
3. **Do not adjust models without a measured sample.** Two recalibrations have
   been refused on this basis (SP+ K=0.85, leaky backtests) and one "edge" was
   retracted at 50.9% after being claimed at 58.3%. A confidence cap is an
   adjustment and is held to the same bar.
4. **An explicit `select` that omits a column returns rows without it, silently.**
   Audit every read when adding a field.
5. **Trust the database over training recall** for rosters and player-team
   assignments. Corrected repeatedly.
6. **tsc/lint error *counts* are not verification.** A new error can hide behind
   a simultaneously removed one. Compare error *signatures*.

---

## 7. If the account is lost tomorrow — do this

1. Nothing urgent. The product runs. Confirm with the health check in §5.
2. Pick a replacement dev tool (Cursor, Copilot, Codex, Gemini CLI, Aider,
   Cline — all read this repo fine).
3. Brief it with §4.
4. Only if the Anthropic *API* key is also dead: repoint
   `anthropic_guard.call()` per §2, and the `claude-proxy` edge function. Until
   then reads keep generating; a dead key raises `FatalLLMError` and fails the
   step loudly rather than writing empty reads.
5. Keep `docs/knowledge/` in sync if you keep using a tool that maintains its
   own memory store outside the repo. That drift is how this knowledge nearly
   got stranded in the first place.
