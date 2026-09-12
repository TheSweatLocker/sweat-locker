# Daily Trouble Log

One file per day: `YYYY-MM-DD.md`.

## Purpose

Andy's directive 2026-09-12: "keep a trouble log document so we know
what we fixing every day and you can know every day instead of
forgetting all the time, a daily journal of trouble logs, things
wrong and what was done to fix, daily entries of everything we
assess and tackle."

## Rules of the log

- **One file per day.** Named `YYYY-MM-DD.md`.
- **Append-only within the day.** Don't rewrite earlier entries when
  new context emerges — add a new subsection below with the update.
- **What goes in:**
  - Every bug reported (user or discovered)
  - Root cause identified
  - Fix (with commit SHA)
  - What was assessed but not changed
  - Handoffs / queued items
  - Data audits and their conclusions
- **What does NOT go in:**
  - Feature planning (that's the v1.0.1 punchlist or dedicated
    project memory files)
  - Long design docs (link to a proper doc instead)

## Rhythm

- **First thing each session:** open today's file. If it doesn't
  exist yet, create it from the template below.
- **After every fix:** add a bullet with the commit SHA.
- **End of day:** no formal wrap — the file stands on its own.

## Template

```markdown
# Daily Log — YYYY-MM-DD

**Context:** <one line — what's the day about? App live? Testing?>

## Reported / discovered

- **[bug/issue name]** — <one line describing what user saw or what
  I found>

## Root causes

- **[bug name]** — <one line describing where the bug lives>

## Fixes shipped

- **[commit-sha]** — <one line: what it does, why>

## Assessed but not changed

- <thing that was investigated + decision>

## Queued / handoffs

- <item + where it's tracked (memory file, punchlist, PR)>

## Data audits / findings

- <query run + top-line result>
```
