---
name: feedback-surface-records-trust-levels
description: Which surface_records numbers are honest vs inflated/censored. NEVER cite prop_prime as a credible record — it counts PRIMEs that never published.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-18T21:49:10.356Z
---

**Not every `surface_records` row is a number we can show or cite.
Check the trust level before quoting one to Andy or surfacing it in
the app.**

**Why:** On 2026-09-18 I called `prop_prime` (463-96, 82.8%, +263u)
the "flagship" record — hours after we had spent the morning
establishing that exact number is inflated. Andy: *"we literally spent
all morning talking about how its inflated wtf are you talking
about."* Quoting an untrusted record as a headline is how a bad number
ends up in a public post.

**How to apply:** Before citing any `surface_records` row, classify it:

🔴 **INFLATED — never cite as credible**
- `prop_prime` / `prop_strong` / `prop_lean` / `prop` — count every
  row that carried the tier at grading time, including PRIMEs that
  were **never published to a user** (banned prop families, mid-day
  demotes, view-filtered rows). See [[project-public-receipts-integrity-918]].
  The fix is the `public_receipts` immutable log; until that swap
  lands, these numbers overstate performance.

🟡 **CENSORED — real but not representative**
- `sharp_card` scoped to a sport with sparse composition. The composer
  picks the top 1-2 per day, so the record reflects the best few picks,
  not the sport's actual edge. 2026-09-18 example: NCAAF `sharp_card`
  was 8-2 (80%) on n=10 while 41 PRIME/STRONG NCAAF sides existed in
  the same window — a 24% cherry-picked slice.
- Anything with n < 30. Per [[feedback-sample-size-with-pct]] the gate
  is n≥30; the app's chip gate is only n≥10, which is too loose.

🟢 **HONEST — safe to cite and surface**
- `<sport>_sides` (`mlb_sides`, `ncaaf_sides`, `nfl_sides`) — full
  graded sides population, un-censored by composition. These are the
  numbers Andy wants up: *"Thats the number that should be up large
  sample accurate."*
- `sharp_card` at the ALL/combined level with large n — it is the
  actual published card.

**Andy's standard:** large sample + accurate + un-censored. If a number
fails any of those, either don't show it or say the caveat in the same
breath.

Related: [[feedback-sample-size-with-pct]],
[[feedback-verify-pick-before-socials]],
[[project-public-receipts-integrity-918]]
