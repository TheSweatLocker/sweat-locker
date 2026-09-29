---
name: ui-and-pipeline-in-parallel
description: "Design/plot UI and build backing pipeline in parallel; the gate is at prod-ship, not at design-start."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-01T14:45:06.148Z
---

Never gate the START of UI design on the pipeline being clean. Plot the UI first (mock data, artifact, or code with placeholders) so the layout is nailed down, then build/refine the pipeline against the exact fields the layout needs. Serial "pipeline first, UI second" is inefficient — layout reveals what data is actually required and drives pipeline scope.

**Why:** User corrected me 2026-09-01 after I wrote a directive saying "prove pipeline runs green for 3-7 days, THEN wire the UI." That's too strict. Plotting the UI is cheap and fast; it doesn't touch production. What IS gated is shipping the UI to production — that still requires clean, cadenced, complete data ([[project_game_detail_action_network_vision_901]]).

**How to apply:**
- When a design vision drops, offer to plot immediately (HTML artifact or component sketch) alongside a data audit
- The plot uses realistic mock values but is structured so real fields map cleanly when the pipeline lands
- Only the final production merge is gated: pipeline complete + on cadence + data quality checker green
- Never say "let's wait N days" for something that's parallelizable
