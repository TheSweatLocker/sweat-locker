---
name: tiktok-public-streak-721
description: Another bad TikTok public post night 7/21. Multi-night streak building; audit tomorrow BEFORE any new content.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-22T02:23:47.111Z
---

**Set 2026-07-21 evening after user reported "another terrible night."**

Second consecutive rough night on TikTok public picks per user report.
Actual pick-by-pick results not verified in this session — tomorrow's
first move: pull `mlb_game_picks` + `mlb_props` for 7/20 + 7/21, cross
against the graded results, break down by tier + surface.

**Why:** The audit-battery + reweight from 7/21 morning shipped in
[[project_model_reweight_721]] + [[project_audit_battery_721]], but
those changes affect what the model produces GOING FORWARD, not
retrospectively grade what already posted. If today's public post was
built on morning-of picks that came BEFORE the reweight took effect
on the noon cron, that could explain the disconnect. Verify SHA
timestamps of the picks that got posted vs when the reweight
went live.

**How to apply tomorrow's first turn:**
1. Which specific picks lost and their tier/surface/source
2. Were they on the pipeline SHA that had the reweight or pre-reweight?
3. Any tier-band misfires (LIGHT that shouldn't have been on card, PRIME
   that was really LEAN)?
4. Only THEN discuss next-day card composition.

Do not launch into rebuilding models or panic-fixes. Diagnosis first,
per [[feedback_dont_fade_prime_on_pattern_alone]] — one bad night is
signal, two bad nights is a directive to audit, but neither is a
mandate to fade the pipeline.

Related: [[feedback_user_doubt_is_signal]], [[feedback_confidence_in_first_pass]],
[[project_card_process_discipline_718]]
