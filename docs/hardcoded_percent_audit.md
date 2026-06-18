# Hardcoded Percent Audit — Findings

**Status:** Audit complete / 2026-06-18
**Author:** ag + claude
**Related:** [feedback_sample_size_with_pct](memory), [engine_clarity_refactor.md](engine_clarity_refactor.md) Phase 4, [project_dynamic_cohort_framework_607](memory)

---

## 1. Goal

Find every percentage citation in user-facing code paths that is:
- **Hardcoded** (not pulled from a live data source), OR
- **Ungated** (cited without sample size n alongside)

Both classes violate `feedback_sample_size_with_pct` (saved 2026-06-18). The rule: every % on a user surface must show n alongside, both numbers must be live, and below n ≥ 30 the % should be omitted entirely.

---

## 2. Findings — server-side

### 2.1 play_of_day.py — sweat card driver labels (HIGH exposure)

| Line | String | Issue |
|------|--------|-------|
| 576 | `'1st-inn fragility 6-8 audits ~63%'` | Hardcoded + no n |
| 618 | `'NRFI 90-94 audits 50% alone; companion-signal cohort untracked'` | Hardcoded + no n |
| 625 | `'NRFI 90-94 alone audits 50% (n=22, 30d) — coinflip, surface only'` | Has n but hardcoded |
| 776 | `f'Score {int(nrfi)}/100 — audit 50% (n=22, coinflip)'` | Has n but hardcoded |
| 782 | `f'Score {int(nrfi)}/100 — fade cohort 47.8%'` | Hardcoded + no n |
| 793 | `f'NRFI {int(nrfi)} + 1st-inn ERA {_max_fi:.1f} — audit ~63%'` | Hardcoded + no n |
| 992 | `f'Fav at {fmv} hits 50% lifetime — no confluence rescue'` | Hardcoded + no n |
| 1040 | `f'... + {xera_f:.2f} xERA (62.0% DOG RL lifetime)'` | Hardcoded + no n |
| 1047 | `'85% lifetime cohort — compound edge'` | Hardcoded + no n |
| 1241 | `'4-signal confluence games skew UNDER 58.1% lifetime'` | Hardcoded + no n |
| 1459 | `f'avg GB% {avg_gb*100:.0f}% at extreme park = 67% OVER lifetime'` | Hardcoded + no n |
| 1999 | `f'... ({over_rate:.2f} over rate, audit: 20% OVER hits) — suppressed'` | Hardcoded + no n |

**Exposure:** ALL of these feed driver labels that appear in the sweat card → app "Why This Score" UI. Users see these % strings on every game card every day.

### 2.2 generate_mlb_game_reads.py — NRFI tier strings (HIGH exposure)

| Line | String | Issue |
|------|--------|-------|
| 384 | `f"{score} — PRIME 90-94 band (~69% audited 30d)"` | Hardcoded |
| 386 | `f"{score} — mild NRFI lean 70-79 (~58% audited)"` | Hardcoded + no n |
| 388 | `f"{score} — YRFI lean ... 30d audit ~48%; sweet spot ... → 63%"` | Hardcoded + no n |
| 390 | `f"{score} — soft YRFI lean ... 30d audit ~48% — gate on 1st-inn ERA"` | Hardcoded + no n |

**Exposure:** Goes into NRFI tier annotations fed to Jerry → narrative text users read.

### 2.3 generate_dawg_of_day.py — DAWG signal labels (MEDIUM exposure)

| Line | String | Issue |
|------|--------|-------|
| 499 | `f"(+{team_wrc - opp_wrc:.0f} dog hitter edge — 58% cohort)"` | Hardcoded + no n |

**Exposure:** DAWG signals are surfaced on the in-app DAWG card.

---

## 3. What this means in practice

### Example user-facing leak from today
Tonight's Jerry struct for any game with NRFI score 70-79 contains:
```
"nrfi_tier": "78 — mild NRFI lean 70-79 (~58% audited)"
```

The "~58% audited" is **a frozen number from when the line was written**. It does NOT update when 30d cohort outcomes change. Users reading "this NRFI tier hits 58% historically" are reading a stale claim with no sample size.

### Why the dynamic-cohort-framework migration (607) didn't catch these
[project_dynamic_cohort_framework_607](memory) shipped Phase 1 dynamic cohort migration covering 3 of ~12 sites. **These ~9 sites are the rest of that work.** They're the same class of issue: hardcoded backtest %s that should pull from `mlb_tier_calibration` live row.

---

## 4. Fix plan — three tiers of effort

### Tier A — wire to existing live source (queued, ~1-2 hours each)
Sites where the underlying cohort EXISTS in `mlb_tier_calibration`:
- NRFI 90-94 band (line 776, 384, 625) → `nrfi_prime_90_94` calibration row
- NRFI 70-79 (line 386) → `nrfi_lean_70_79`
- NRFI volatile 95+ (line 782) → `nrfi_volatile_95plus`
- YRFI ≤40 (line 388, 390) → `yrfi_lean_le40`
- 4-confluence net (line 1241) → `confluence_prime_ge4`
- Long-rest DOG RL (line 1040) → `away_sp_rest_long_team_ml` or similar

Each gets the same treatment as buy-down calibration (Phase 6 from this morning): pull the live rate from the calibration row, gate at n ≥ 30, format as `"rate% (W-L over n games)"`.

### Tier B — compute on demand (medium effort, ~2-4 hours each)
Sites where no calibration row exists yet but data does:
- "Fav at -200 hits 50% lifetime" (line 992) — need a price-band calibration
- "85% stacked cohort" (line 1047) — need a stacked-confluence cohort row
- "67% OVER lifetime at extreme park + GB" (line 1459) — need a park×GB cohort row

### Tier C — strip and re-add when wired (quick win, ~30 min total)
Sites where the % is mostly informational and removing it doesn't hurt the user:
- "(58% cohort)" appended to DAWG hitter edge (line 499) — strip the % for now, surface only the wRC+ gap

---

## 5. Recommended sequencing

For **launch readiness this weekend:**

1. **Tier C strip — ship tonight** (30 min) — removes the worst-class violations from user-visible surfaces while we wait on live wiring
2. **Tier A NRFI sites — ship Saturday** (3-4 hours) — biggest exposure, calibration table already has the data
3. **Tier A spread/confluence sites — ship Sunday** (2-3 hours)
4. **Tier B compute-on-demand sites** — queue post-launch unless time allows

Total estimated effort: ~6-8 hours of focused work over the weekend.

---

## 6. Discovery method (for future audits)

Search command:
```bash
grep -nE "['\"][^'\"]*[0-9]{1,3}(\.[0-9]+)?\s*%[^'\"]*['\"]" *.py | grep -v "^#" | grep -v "test_"
```

Should be added to a pre-commit hook so new hardcoded %s don't sneak back in. **Queued: pre-commit hook to flag hardcoded %s in any committed line.**
