---
name: project-apple-approval-910
description: v1.0 approved by Apple 2026-09-10 (subscription + overall). Launch cleared. v1.0.1 punchlist queued for next build round.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-10T18:46:05.971Z
---

**v1.0 Sweat Locker: APPROVED by Apple 2026-09-10.** Subscription pieces + overall app both cleared review.

**Why:** Weeks of pipeline + Jerry + UX work funneled to this milestone. First public build shipping.

**How to apply:**
- When user talks about "launch" / "shipping" / "app is out", they mean v1.0 which is now live-eligible.
- All work from 9/10 forward goes into [[project_v1_0_1_client_priorities]] — bundle for next TestFlight → App Store round rather than shipping standalone.
- Server-side fixes still deploy immediately (no App Store gate).
- SDK 54 → 57 upgrade stays queued as v1.0.2 (per that memory), not v1.0.1.

**Post-approval to-do (do NOT lose these):**
- Confirm subscription products live (weekly $14.99 / annual $119.99 / 7d trial per [[project_pricing_launch_decision]]).
- Website content parity ([[project_website_update_queue_908]]).
- Marketing/social launch material.
- Redeploy odds-proxy edge fn if not done.
- Apply pending migrations: 20260910b (cohort_display_config seed) ✓ user confirmed, 20260910c (cohort_tag_records) ✓ user confirmed, 20260910d (NFL alias cleatz forms) — pending user apply.
