---
name: project-august-note-record-907
description: "🚨 9/7 queued for tomorrow: user doesn't like the August-referencing record note somewhere in the app — needs scoped discussion + rewrite"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-07T04:27:03.000Z
---

**Queued 9/7 evening:** user flagged "that August note for record comes up i dont like it, lets talk about that tomorrow."

**Context unclear yet — need to scope tomorrow:**

Possible surfaces this could refer to:
1. **Receipts hero fresh-window disclaimer** — the "Since Aug 20 · post-recalibration fresh start" text that shows when `winKey === 'epoch'`. Even though we dropped the FRESH tab, this italic text may still render if someone lands on that state. Check `app/index.tsx` around the surfaceRecords hero.
2. **SHARP_RECORD_EPOCH constant** — `compute_surface_records.py:44` sets epoch as `2026-08-20` and all "epoch" surface_records rollups anchor there. The `epoch` window key still populates surface_records even if not shown in receipts UI.
3. **Onboarding / Home tour** — some tour step may reference the Aug 20 recalibration
4. **Sharp Card audit line** — could be a "since Aug 20" audit disclosure
5. **Track Record page** — may still reference an August starting date
6. **FAQ answer** — could mention Aug 20 as the model reset date

**Tomorrow discussion should cover:**
- Which specific note bothers user — locate the exact string
- Why it bothers them (looks tout-adjacent? feels stale? cherry-picking?)
- Replace with what — pure lifetime? "Since launch"? Different phrasing?
- If user wants to kill the epoch concept entirely: bigger refactor because `compute_surface_records.py` anchors on it

**Related work:**
- Already dropped FRESH tab from Receipts (see [[project_launch_day_907_priorities]])
- Already rewrote Paywall subhead that cherry-picked "20-4 over 5/20-5/21" stretch (commit 156b0d2f)
- User's directive throughout: no cherry-picking, no tout-adjacent framing

**Priority:** launch-day polish. Not launch-blocking unless the note appears in the App Store screenshot or reviewer-facing surface. Tomorrow morning first thing to close.
