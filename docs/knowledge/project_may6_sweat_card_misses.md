---
name: 2026-05-06 Sweat Card miss diagnostics (review tomorrow)
description: 5/6 card had multiple miss categories worth reviewing in 5/7 audit cohort run
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
5/6 Sweat Card had a tough outcome that warrants 5/7 morning audit deep-dive before NFL prep starts.

**Why:** Multiple miss categories on the same day suggest cohort-level (not single-game) issues that need grading review before the audit run on 5/7 morning rolls them into the calibration table. Specifically:
- **Ks UNDER tier**: flagged "sus" by Andy. Need to pull all 5/6 K-under picks (PRIME and STRONG), check actual K count vs line, segment by pitcher type.
- **Hitter misses**: multiple hits OVER/UNDER picks missed. Need to audit by tier.
- **Bucket play (innings 4-6 OVER)**: complete miss. The bucket angle on the card was a single-game fire — but if the cohort is hitting <50% over recent sample, may need to pull or downgrade like we did with OVER-lean and NBA NR_gap.
- **Yankees confluence +8 PRIME**: lost 6-0 (Eovaldi vs NYY 0.36 ERA mastery). Already documented in earlier session as the trigger for tomorrow's confluence breakdown extension to add `pitcher_vs_team` as 9th vote.

**How to apply:** 5/7 morning, before kicking off NFL pipeline build:
1. Run `audit_tier_calibration.py` after 8am cron resolves yesterday's games — let cohort updates settle first.
2. Pull 5/6 results query: all picks (POTD, Dawg, Pipeline Props, NRFI tiers, bucket angle) with W/L outcomes.
3. Check if any cohort drops materially (>5pt hit rate drop on n>=10 sample). Flag candidates for downgrade or pull.
4. Specifically examine: K-under cohort by sample size, hitter UNDER cohort by pitcher type, bucket_innings_4_6_over recent sample.
5. Then proceed to NFL prep.

Don't fire same-day reactions — let the cohort numbers update first, then assess in audit context.
