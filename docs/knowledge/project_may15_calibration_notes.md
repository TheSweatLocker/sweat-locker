---
name: may-15-calibration-notes-for-5-17-audit
description: "5/15 went 7-5 public / 8-5 with app. Three items to fold into the 5/17 audit: Coors flag-was-wrong, NRFI 95+ stratification, Ryan U2.5 ER not surfaced."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**5/15 final: 7-5 public card · 8-5 with app YRFI. Net positive.**

Three items queued for the 5/17 audit checkpoint (not standalone gates):

## 1. Coors card — framing saved a missed total

Ahead of post, I waved a yellow on the ARI/COL OVER 11.5 because `model_pred_total = 9.88` disagreed with `projected_total = 11.7` (xERA-based). Said the runs model has been more accurate when they conflict.

**Final: 9-1 = 10 runs, UNDER 11.5 by 1.5.** Runs model 9.88 was nearly dead-on; `projected_total` 11.7 was wrong. **My direction was correct.**

**The card hit ✅ anyway — because the framing was "ARI hitter stack + fade Freeland", not "OVER 11.5".** ARI bats fired (8 runs), Freeland got torched. Both angles delivered; the total missed quietly.

**The lesson is about framing, not model selection:**
- When runs model and `projected_total` disagree at high-environment parks, **the conflict is real and the runs model has been right** — don't post the total play.
- A Coors "stack + fade starter" card can hit even when the total misses. That's the right framing for a high-variance environment where the *winning team's* run total is the real edge, not the combined total.
- **Going forward:** at park_run_factor ≥115 with a model/total split, default to side-stack/fade-starter framing instead of total OVER framing.

**Audit question for 5/17:** Pull the cohort where `park_run_factor ≥115` AND `model_pred_total < projected_total - 1.0` — does runs-model direction win OVER/UNDER at material rate vs the xERA projected_total? Hypothesis: yes, by ≥10pt.

## 2. NRFI 95+ "L3 ERA stratification" hypothesis — validated 3-0

`nrfi_volatile_95plus` audited at 44.6% (trap zone). Tonight three at 79/99/100 all hit. The 100 (NYY/NYM) had both pitchers L3 ERA sub-1.50 (Schlittler 0.51 / Holmes 1.47).

**Hypothesis:** Stratify the 95+ cohort by *both* starters' L3 ERA. Subset `both_L3_era_le_2.50` should outperform the 44.6% base meaningfully.

**Audit query for 5/17:** Pull all resolved 95+ NRFI scores in backfill, split by `min(home_L3_era, away_L3_era) ≤ 2.50` and `max ≤ 2.50`. n likely small but if the split is clean (e.g. 65%+ vs 35-40%) it's a publishable gate.

If validated, **5/17 ship**: gate "NRFI play call" in cards at 95+ AND both L3 ERA ≤2.50. Below that, surface as info only.

See [[project_nrfi_patterns_april.md]] for prior NRFI cohort work.

## 3. Joe Ryan U 2.5 ER not surfaced — scorer gap

Resolved at **1 ER (hit)**. My morning read had it as the loudest non-card prop:
- xERA 3.27
- home_pitcher_home_era 2.60
- home_pitcher_vs_team_era 2.38 (mastery vs MIL)
- L3 ERA 2.19
- L3 K% 24.5

That's 4 aligned PRIME-grade indicators. Our `score_pitcher_er` didn't surface it.

**Audit question:** Why? Likely candidates:
- ER UNDER scorer may be requiring a separate "opp lineup wRC+ low" or recency-fatigue gate that Ryan's matchup didn't trip
- `vs_team_era ≤2.50` mastery bonus may not be wired into ER UNDER (we have it for outs/K cohorts)
- Could be a tier-floor issue (scored 60-64, missed PRIME cutoff)

**5/17 deliverable:** Pull the raw `score_pitcher_er` breakdown for Ryan and identify the missing signal. If the mastery+xERA+L3 stack doesn't hit STRONG floor, add a multi-signal bonus the way [[project_pitcher_class_projections.md]] handles outs.

## 4. POTD 0-2 — track but not act

5/14 (HOU OVER) ✓ — wait, that was the **5/14 win** (POTD hit). 5/15 BAL/WSH OVER ❌. That makes 0-1 on POTD this stretch, not 0-2. Confirm before flagging in audit. **Verify 5/13 SF/LAD UNDER POTD result during 5/17 audit.**

## Side reads to note (not audit items — just variance):
- KC mastery side and SEA confluence side both lost. Both had legitimate edge — Wacha vs STL 1.80 was real, Hancock vs SD mastery was real (got the shutout innings, NRFI hit, but bats didn't show). n=1 each.
- PIT side lost in 9th-inning bullpen blow-up. Pitching/totals model was correct through 8 (NRFI hit same game). Relief variance, not model failure.

## What worked, for the launch story:
- NRFI sweep 3-0 (PHI/PIT 99, NYY/NYM 100, SD/SEA 79)
- K props 2-0 (Schlittler PRIME, Yesavage STRONG 79 ER under also confirmed clean tier)
- Confluence +6 home delivered (BOS/ATL Braves)
- App-only YRFI at Coors hit

Week stretch: Mon 2-7 → Tue 27-10 props → Wed 5-1 → Thu 5-1 → Fri 7-5. Wed-Fri is the launch-receipts window.

**These are audit prep notes, not standalone calibration rules.** Don't ship anything off these until the 5/17 audit confirms each in cohort data.
