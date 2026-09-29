---
name: feedback-daily-trouble-log
description: "🚨 Start every session by opening docs/daily/YYYY-MM-DD.md — one file per day, append-only. Every bug reported, root cause, commit SHA, and assessment lands there so future-me isn't relearning today's fixes tomorrow."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-22T20:46:15.577Z
---

# Daily trouble log — persistent journal

Andy 2026-09-12: "I think we should we keep a trouble log document
so we know what we fixing every day and you can know every day
instead of forgetting all the time, a daily journal of trouble
logs, things wrong and what was done to fix, daily entries of
everything we assess and tackle."

## Location

Repo-committed at `docs/daily/YYYY-MM-DD.md` — one file per day.
README with template at `docs/daily/README.md`.

## First thing every session

**Open today's file.** If it doesn't exist yet, create it from the
template in `docs/daily/README.md`. Read the last few days' files
if user is describing symptoms — the answer or a recent fix may
already be there.

## What goes in

- Every bug reported (user or discovered)
- Root cause identified
- Fix (with commit SHA)
- What was assessed but not changed (with the decision)
- Handoffs / queued items (link to memory file, punchlist, PR)
- Data audits and their conclusions

## What does NOT go in

- Feature planning (that's `project_v1_0_1_client_priorities` or a
  dedicated project memory file)
- Long design docs (link to a proper doc instead)
- Duplicated content from memory files — the log links to them,
  doesn't restate them

## Rhythm

- Session start: open today's file
- After every fix: add a bullet with the commit SHA
- After every audit: add findings + link to the memory file it
  updated (if any)
- End of day: no formal wrap — the file stands on its own

## Why this exists

Multiple times in the past two weeks, Andy has raised the SAME
issue on consecutive days ("graphs missing", "sharp record wrong",
"grader not catching up") and I've re-diagnosed from scratch each
time. The daily log means:

- Tomorrow, when Andy says "graphs are missing again", I open
  today's file first — see the pagination-fix pattern — try that
  before touching anything else
- The v1.0.1 punchlist stays clean (it's decisions + plans);
  operational churn lives in the daily log
- Andy can scroll a day and see what happened without asking me
  to recap

## Companion: docs/BACKLOG.md  (created 2026-09-22)

The daily log is **what happened that day**. `docs/BACKLOG.md` is
**what is still open** — the single living list, first one in the
repo. Read BOTH at session start: the log for recent context, the
backlog for what is outstanding.

Rules it was built with, because `docs/hardcoded_percent_audit.md`
(06-18) had every line number stale by the time it was reopened:
no line numbers, a VERIFY command on every item so it can be
re-checked rather than trusted, nothing closes without a commit
SHA, re-verify before quoting it.

## 2026-09-22 — I broke this rule for a whole day

Last entry was 09-20; 09-21 never written; 09-22 not opened once
until Andy asked when it had last been touched — after a day that
found the prop leak, the 86 dark columns, and a bug I introduced
myself. He was right that this is exactly why he re-explains
things to me.

Knowing the rule is not the problem. **Open the file before doing
any work, not after being asked.**

## Related

- [[feedback_morning_audit_format_912]] — the audit format is what
  the morning session STARTS with; the daily log is what it
  PRODUCES throughout the day
- [[feedback_prop_graph_rendering_912]] — an example of the exact
  class of runbook the daily log points people to when a pattern
  recurs
