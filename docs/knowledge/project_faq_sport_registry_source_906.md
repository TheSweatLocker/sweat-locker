---
name: project-faq-sport-registry-source-906
description: "🎯 9/6 queued — move FAQ + coverage copy from hardcoded to sport_registry-sourced text so per-sport cadences track the DB automatically (drift scope shrunk 9/6: no season live/coming-soon text)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-06T18:54:03.881Z
---

**Queued 9/6:** user flagged FAQ + coverage copy had timing/live text hardcoded. Every cron cadence change or sport-going-live event required a hotfix.

**Scope simplified same day per user directive:** "In reality all sports are live or will be live come season start time so just mention all sports that are covered no live or not live." All UI coverage copy now names every covered sport flatly; per-sport records show empty when off-season and populate automatically the moment games land.

**Immediate cleanup landed 9/6 (`3dae8530` + follow-up):**
- FAQ "Which sports do you cover?" — flat sport list, no LIVE/coming markers
- FAQ "When do picks come out?" — per-sport cadences w/o date markers
- FAQ "What about props?" — MLB/NFL/NBA/NHL/UFC named flatly; college props flagged as not published
- Onboarding step 2 coverage line — flat sport list
- Paywall feature row — "All sports covered" instead of "NCAAB joins in November"
- Sharp method bullet — "across every sport we cover" instead of "MLB live; other sports as their playbooks ship"
- Receipts filter chips — no more disabled/offseason gating; all sports tappable
- Prop Jerry per-sport banners — "playbook reads X" rather than "coming this season"
- NHL Team Stats empty state — "populates once games are live" (dropped "Oct 7 puck drop")
- CohortDashboard + RecapCard offseason placeholders — "populate as graded picks accumulate" (dropped "activates when season starts" / "launching soon")
- NFL efficiency empty state — dropped "Preseason Aug 7 · Week 1 Sept 4"
- Track Record "tier tables coming soon" — reworded to "build with sample"

**Remaining drift risk (small, post-launch worthwhile):**

1. Per-sport cadence text in FAQ "When do picks come out?" is still hardcoded — if we change MLB refresh from 7am to 8am, requires an app update.
2. Onboarding coverage line is a single flat sentence — new sport added = app update.
3. `sport_registry.state_message` (used by TrackRecord + Games tab off-season card) already sources from DB — this is the pattern to extend.

**Fix scope (post-launch):**

Add ONE column to `sport_registry`:
- `cadence_description` (text) — e.g., "Morning ~10am ET; refresh ~4pm ET; daily"

Rewrite FAQ "When do picks come out?" to `SELECT sport, cadence_description FROM sport_registry` and render sport-by-sport. Ops updates a DB row when cadence changes; text refreshes across every install without an app resubmit.

**No longer needed:** `season_status_display` column — we intentionally don't distinguish live/coming-soon in copy anymore.

**Alternative (larger cut):** move ALL FAQ content to a `faq_entries` table (section, question, answer, sort_order, active). Overkill for launch — 25-question FAQ hardcoded works — but sport_registry-sourcing the cadence question is a small, high-value cut.

**Sequence:** post-launch, low priority. Hotfix cadence is minutes if a cron cadence changes; users still see empty-then-populate correctly when new sports come in-season.
