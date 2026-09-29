---
name: project-website-update-queue-908
description: "🎯 9/7 queued: tomorrow discuss + ship website updates (thesweatlocker.com) — content parity with app v1.0 launch messaging, Terms/Privacy verified live, Support page enhanced"
metadata:
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-08T11:09:48.307Z
---

**Queued 9/7 late-night after app v1.0 submitted to Apple.** User asked to queue up website-update discussion for tomorrow. Waiting on Apple review approval (24-48 hrs).

## What the website has now (verified live tonight)
- Root: `https://thesweatlocker.com` — marketing landing
- `/support` — verified live (blocked Apple submit otherwise)
- `/privacy` — verified live for App Store
- `/terms` — updated recently with §12 Sportsbook Independence clause

## What to discuss tomorrow

**1. Content parity with app v1.0 launch messaging**
- Landing page copy should match the App Store description tone (More Data, Less Sweat, receipts posture)
- Feature callouts (Sweat Card, The Sharp, Vault Match, MoneyFlow, ROI Tracker, Jerry, POTD) should match app naming conventions ([[feedback_sweat_card_vs_sharp_card]], [[feedback_steam_room_tab_names]])
- No "Sharp Card" references anywhere — use "The Sharp" if referring to the Steam Room sub-tab
- No sportsbook logos or affiliate references ([[feedback_tos_scrub_source_names]])
- No handicapper names in prose — use abbrev codes only (SC, DK, etc.)

**2. Sport list / cadence source of truth**
- Site should reflect actual sport availability + schedule
- Cross-check against [[project_faq_sport_registry_source_906]] — FAQ answers should source from sport_registry not hardcoded drift
- Pattern applies to website too — any sport-list needs a canonical source, not a hand-maintained list

**3. Download CTAs**
- App Store link — will need to add once Apple approves + user hits Release
- TestFlight signup CTA if we want to build a beta waitlist while awaiting review

**4. Legal / compliance**
- Privacy policy alignment with App Privacy questionnaire answers submitted to Apple:
  - Data collected: Purchases, Device ID, Crash Data, Performance Data
  - Not for tracking, no ATT
  - No user contact info collected (no login)
- Terms of Service §12 (Sportsbook Independence) confirmed live
- 18+ age gate note prominently displayed (matches App Store rating)
- 1-800-GAMBLER resource visible

**5. Support page**
- Currently minimal. Consider adding:
  - Contact email
  - Subscription cancellation walkthrough (link to iOS Settings → Apple ID → Subscriptions)
  - FAQ mirror of in-app FAQ
  - Sport coverage matrix
  - "Report a bug" form or mailto:

**6. Clean URL slugs (aesthetic — not launch blocking)**
- Current state (9/8): support/terms/privacy pages exist but use hash-fragment URLs like `thesweatlocker.com/#4a867da5-8815-43cc-9c70-bf875edd04dc`. Functional (HTTP 200, real content) — Apple accepted them — but ugly in the App Store product page where users see the raw URL.
- Flip page slugs in the website builder to `/support`, `/terms`, `/privacy`.
- After: update ASC → App Information Support URL + Marketing URL + Privacy Policy URL to clean paths. Update the two links at the bottom of the Description to clean paths. Pure metadata edit — no rebuild needed.
- Keep hash URLs functional as backups (301 redirect or leave them alive) so any user with a bookmarked hash URL doesn't 404.

## Related
- [[project_asc_submission_page_907]] — App Store submission (SHIPPED 9/7)
- [[project_faq_sport_registry_source_906]] — canonical sport list source
- [[feedback_tos_scrub_source_names]] — handicapper naming rule extends to website
