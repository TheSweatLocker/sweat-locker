---
name: grey-situational-records-early-season-is-sit-min-n-not-a-render-bug
description: Andy has asked three times why Situational Records show no red/green. At week 3-4 every split has N<=2 and SIT_MIN_N=3 nulls the colour by design. Verified 2026-09-28.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-28T18:50:58.578Z
---

`SIT_MIN_N = 3` in `app/components/GameDetailV2.tsx`. In `SitRow` the sample gate runs **last**, deliberately overriding every colouring branch, so any row where either side has 1–2 games renders grey with the note "too few games to call an edge".

Measured 2026-09-28 for PHI @ CHI: **all 36 `team_situational_records` rows (2 teams × 3 markets × 6 filters) had N ≤ 2**, so every box was correctly grey. Both teams had played 2 games.

**Why:** Andy has now reported "no red or green boxes" three separate times (most recently in the PHI @ CHI screenshot pass), and it has been a real bug before — the 2026-09-26 moneyline case was genuine, where compared-and-level rows rendered identically to no-data rows and were fixed with an explicit `'even'` state. So the report is not automatically wrong; it just is not wrong *this* way in weeks 1–4.

**How to apply:** Before touching render code, query `team_situational_records` for the two teams and compute `wins + losses` per row. If every N ≤ 2 the answer is the sample floor and the fix is explanatory, not visual — tell Andy it clears naturally around week 4–5 when N crosses 3. Only if some rows have N ≥ 3 and still render grey is there a rendering bug. A mostly-grey tab reads as broken no matter how correct the logic is, so consider whether the *explanation* is visible enough (the `_allThin` one-note-per-card treatment exists for this). Related: [[feedback_sample_size_with_pct]].
