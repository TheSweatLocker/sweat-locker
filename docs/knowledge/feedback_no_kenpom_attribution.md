---
name: feedback-no-kenpom-attribution
description: "Never reference \"KenPom\" by name in user-facing copy (app, social cards, Jerry reads, marketing). Use generic \"proprietary efficiency model\" framing instead."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

Hard rule: **never use the "KenPom" name anywhere a user can see it.**

**Why:** KenPom is paid third-party data we license. Attribution exposes a vendor dependency, signals dependence on an outside source (undermines the "deep proprietary model" positioning), and creates brand confusion ("Sweat Locker = KenPom skin" is not the positioning). We pay for and integrate the data — but the model on top of it is ours.

**How to apply:**

Approved framings (in order of preference):
- "Proprietary efficiency model"
- "Adjusted efficiency model"
- "Adjusted tempo + efficiency model"
- "D1 efficiency engine"
- "The Sweat Locker NCAAB model"
- Generic stat names ("adjusted offensive efficiency", "tempo-adjusted ratings")

Forbidden in user copy:
- "KenPom"
- "KenPom-powered"
- "KenPom data"
- Any name attribution to outside providers

This applies to:
- App screens (especially the NCAAB placeholder + future game cards)
- Social media cards
- Jerry game reads / narratives
- Marketing site / App Store description
- Push notifications
- Prompt templates that ship text to users

OK to reference internally (backend code, file names, internal comments) — it's accurate technical naming there and doesn't reach users. `ncaab_pipeline.py` calling KenPom API is fine; user-facing strings are not.

Triggered by 2026-05-21 NCAAB placeholder I added that said "KenPom-powered model" and "KenPom adj efficiency model" — patched immediately, rule saved to prevent recurrence.

**Recurrence (2026-05-22):** Even when describing future product visualizations in chat, I wrote "KenPom adjEM gap ≥10" as a cohort label. Even narrative mockups need to use generic framing — the brain trains on what gets written. Going forward, the only acceptable phrasing in chat AND code is "Adjusted efficiency gap" / "adjEM gap" without the vendor name. Andy reminded the rule explicitly.

Related: same principle applies to any other paid third-party data we use (Bart Torvik for NCAAB, fanmatch, KenPom, etc.). The model output is ours; the source feeds are infrastructure, not branding.
