---
name: may-17-er-under-scorer-gap-three-bugs-found
description: "score_pitcher_er_under missed Joe Ryan 5/15 (would have been clean STRONG 60+). Three bugs: vs_team_era mastery not scored, threshold cliffs (L3 2.0, xERA 3.0, split -1.0), no opp recency factor."
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

## Test case: Joe Ryan 5/15 MIL @ MIN

Ryan's profile that day:
- xERA 3.27
- L3 ERA 2.19
- home_pitcher_vs_team_era **2.38 in 12.3 IP** (mastery — Big sample)
- home_pitcher_home_era 2.60 (vs season ~3.27, split -0.67)
- Opp MIL wRC+ 100 (neutral)
- Park 98 (neutral)
- Hot day, wind W (no env suppress)

**He had 4 PRIME-grade indicators (xERA + L3 + vs-team mastery + home split). Scorer landed him at conviction 40 — below threshold, not surfaced.** I called this out on 5/15 as "loudest non-card prop." Resolved as **WIN (Ryan went 1 ER).**

## Three concrete bugs

### Bug 1: vs_team_era mastery isn't read in score_pitcher_er_under
- `home_pitcher_vs_team_era` / `away_pitcher_vs_team_era` fields exist + are populated
- Used in `score_pitcher_outs`, `score_pitcher_er` (OVER), and others
- **NOT used in `score_pitcher_er_under`** — pure oversight
- Cost in Ryan case: +0 instead of +10

### Bug 2: Threshold cliffs leave clean signals at zero
- `L3 ERA ≤2.0 = +10` cliff: Ryan's 2.19 (sharp recent form) gets +0
- `xERA ≤3.0 = +18, ≤3.5 = +10` cliff: Ryan's 3.27 gets +10 instead of ~+15
- `split ≤-1.0 = +5` cliff: Ryan's -0.67 favorable split gets +0
- Each cliff cost Ryan 3-7 points individually

### Bug 3: No opp recency / NRFI band signal
- Scorer treats opp lineup as static (current wRC+)
- Ignores L10 drift, NRFI band signal (Ryan's game had NRFI 74 = "won't score early")
- Cold opp lineup = lower ER risk; not factored

## Recommended scorer patch (post-launch)

```python
# In score_pitcher_er_under():

# 1. Loosen L3 ERA bands
if l3_era is not None:
    if l3_era <= 2.0:  conviction += 12
    elif l3_era <= 2.5: conviction += 7
    elif l3_era <= 3.0: conviction += 3
    elif l3_era >= 5.5: conviction -= 10

# 2. Smoother xERA bands
if xera <= 2.5: conviction += 25
elif xera <= 3.0: conviction += 18
elif xera <= 3.3: conviction += 13
elif xera <= 3.5: conviction += 10
elif xera >= 4.5: conviction -= 15

# 3. Add vs_team_era mastery bonus
opp_side = 'away' if side == 'home' else 'home'
vt_era = _f(g.get(f'{side}_pitcher_vs_team_era'))
if vt_era is not None and xera is not None:
    if vt_era <= 2.5 and (vt_era < xera - 1.0):
        conviction += 10
        signals['vs_team'] = f'Career vs opp: {vt_era:.2f} ERA — mastery'

# 4. Add NRFI band assist (small)
nrfi = _f(g.get('nrfi_score'))
if nrfi is not None:
    if nrfi >= 75: conviction += 4
    elif nrfi <= 40: conviction -= 4

# 5. Smoother split bands
if split is not None:
    if split <= -1.0: conviction += 7
    elif split <= -0.5: conviction += 4
    elif split >= 1.0: conviction -= 5
```

**Expected Ryan recompute under new logic:**
- Base 30
- xERA 3.27 → +13 (new middle band)
- L3 2.19 → +7 (new ≤2.5 band)
- vs MIL 2.38 mastery → +10
- NRFI 74 → +4
- Split -0.67 → +4
- Opp wRC+ 100 → +0
- Total: ~**68 — clean STRONG tier**, surfaces as a play

## Audit note

The ER scorer gap is on the same family of bugs as:
- [[project_may17_hits_under_audit]] — PRIME over-promotes via noise signals
- [[project_may17_pitcher_prop_cohorts]] — ER OVER PRIME at 25% suggests OVER scoring also needs review

ER scorers (both OVER and UNDER) should get a comprehensive pass post-launch.

## Related
- [[project_may15_calibration_notes]] — original 5/17 audit docket
- [[project_pitcher_class_projections]] — alternative ER projection path (Phase B)
