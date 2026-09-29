---
name: nrfi-yrfi-high-bar-not-hard-no
description: N/YRFI publishable on cards only when model signal is extreme AND sweat card conviction agrees. Downgraded 2026-07-18 from hard "no NRFI/YRFI on cards" after data review showed 60% POTD win rate.
metadata:
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  updated: 2026-07-18
---

**Updated 2026-07-18 after data review.** Original rule (below) was written after a bad late-May stretch. 30d data + full POTD history contradict the "hard no."

## The receipts

**NRFI POTDs actually played: 18-12 (60%)** — real winning surface. Not a losing category.

**YRFI at nrfi_score <40 hits 57.0% (n=100)** — real edge, especially at plus-money odds.

**NRFI at nrfi_score 80-89 hits 54.9% (n=102)** — best NRFI band.

**Full N/YRFI performance by nrfi_score bucket (n=981):**
- 90+: NRFI 52.4%
- 80-89: NRFI 54.9% ⭐
- 70-79: NRFI 46.2%
- 60-69: NRFI 48.9%
- 30-39: YRFI 57.1% ⭐
- <20: YRFI 66.7% (tiny sample n=9)

## Updated rule

**Publishable on card when BOTH:**

**NRFI:**
- nrfi_score ≥ 85
- Sweat card assigns STRONG+ conviction
- Optional companion signal: temp ≤ 45°F, ace duel, both starters low 1st-inn ERA

**YRFI:**
- nrfi_score ≤ 40
- Both starters have elevated 1st-inning ERA (≥ 5.0)
- Sweat card assigns STRONG+ conviction
- Bonus: hitter's park + weak bullpens

**Not publishable when:**
- Middle band (nrfi_score 40-80) — coin flip territory
- Sweat card assigns only LEAN
- Single-signal (model alone) without stat spine confirmation

## Framing for public card writeups

- Frame as "1st inning signal" not "NRFI/YRFI" (avoids gambling verb feel)
- Always cite both starters' 1st-inning ERA as evidence
- Reference "60% POTD record" if pushback in replies
- Cite n along with % (per [[feedback_sample_size_with_pct]])

## Original rule (for archive)

*"NRFI plays do NOT belong on the public 5-pick card right now. Even high NRFI-score games (90+) and supplementary STRONG tags should stay off cards. Week of 5/26-5/31 was burned multiple times on NRFI legs that the model rated strong."*

**Why this was over-generalized:** The 5/26-5/31 stretch was a variance blip. Rolling 30d + POTD-played record show N/YRFI is a legitimate surface when the model signal is extreme AND the sweat card conviction agrees. Small-sample bad weeks aren't grounds for a category-wide ban.

## Related

- [[project_nrfi_v2_model_606]] — 6/6 sklearn LogisticRegression NRFI/YRFI model
- [[project_nrfi_patterns_april]] — early NRFI patterns; temp ≤45°F = 79.2%
- [[project_nrfi_demotion]] — NRFI demoted from POTD 5/30; supplementary + companion-signal gate
- [[feedback_sample_size_with_pct]] — cite n alongside %
