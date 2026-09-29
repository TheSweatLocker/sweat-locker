---
name: project-free-launch-3wk-discussion-906
description: "🎯 9/6 discussion queued — launch free for 3 weeks (Sept-early Oct) instead of straight to paid, pros/cons + cost model"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-06T16:58:51.807Z
---

**Queued 9/6:** user asked about launching free for the first 3 weeks (roughly Sept 10 - Oct 1), then flipping paywall on. Wants to understand pros/cons and whether operating costs change with 100 free users.

**Cost model (100 free users):** infrastructure is per-request not per-user. Fixed costs stay identical whether we have 0 or 100 users:
- Supabase Pro $25/mo (fixed)
- Odds API subscription (fixed tier — request-limited not user-limited)
- Anthropic API for Jerry reads: called by *pipeline crons* not per user; cost tied to number of games not number of readers
- Expo EAS builds: per build not per install
- Cron GitHub Actions: free tier covers current volume

Only two variable-cost paths at 100 users:
- Prop Jerry client-side rebuild flow (per-user Anthropic call) — but that's currently in "cache read only" mode after fetch; cost is minimal
- Push notification volume via APNs — free from Apple

**Verdict**: 100 free users ≈ same cost as 0. Meaningful cost increase starts around 5-10k users when Supabase egress + Anthropic-per-user paths scale.

**Pros of 3-week free launch:**
- Higher install volume (no paywall friction on install)
- Better organic ASO signal (installs, ratings, keyword ranking)
- Community/word-of-mouth uplift before Week 4 (paywall goes live during a KEY NFL slate)
- Public track record proof-points accumulate before conversion ask ("we went 62% Week 1-3, now start your trial")
- Cheaper A/B testing on free-tier UX before conversion economics matter

**Cons of 3-week free launch:**
- Delays revenue by ~21 days
- Trains users to expect free forever — churn risk when paywall drops
- Some early adopters may drop when paywall hits ("bait and switch" perception)
- Grandfathering pressure — "I've been here from Day 1, do I get free forever?" community expectations
- Delays MRR data — hard to project 6-month revenue without early trial metrics

**Middle option**: launch immediately with trial (7d free) but publicly *extend* the trial for early users to 21d ("Founding Sweat" promo — auto-expires Oct 1). Best of both — no bait-and-switch feel, still gets word-of-mouth uplift, revenue starts trickling.

**Decision needed before RC/App Store wire**: which model? Affects RC product config (trial length + intro pricing offer) and app copy.
