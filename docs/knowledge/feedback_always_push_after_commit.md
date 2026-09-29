---
name: feedback-always-push-after-commit
description: "When committing a fix the user is waiting on, push origin in the SAME turn — never leave commits local. GitHub Actions crons pull from origin/main; unpushed commits = silent revert next cron."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

**Always push to origin in the same turn as the commit, unless the user explicitly says to hold.**

This has bitten the user twice. 5/31 evening: committed two fixes (sweat dim rescore + props pipeline live-game preserve), did NOT push. 6/1 morning cron pulled from origin/main, ran the OLD code, slate scored 7-of-9 PASS again — same bug we fixed the day before. User opened the morning with "Something is going on with Sweat score again for games most if not all saying pass" and rightfully called the pattern "unsat."

**Why:** the user's GitHub Actions workflow pulls from `origin/main`. A commit that sits in the local working tree never reaches the cron. Every fix that requires a cron to exercise it must be on origin before the next cron fires (which can be as soon as the top of the next hour). Committing without pushing creates a phantom-fix state — the diff exists, the verification ran, but production is still running the bug.

**How to apply — three-step verification protocol (added 6/1 after second recurrence):**

1. **Commit → push, same turn.** After EVERY `git commit` that ships a bug fix or workflow-affecting change, immediately follow with `git push origin <branch>` in the same tool batch. Do NOT batch up commits "to push later." If the push is rejected (remote ahead from auto-card cron etc.), rebase and push immediately — don't defer to the user. Only hold if the user explicitly says "don't push yet" or "let me review the diff first."

2. **Verify the SHA reached origin.** After push, run `git log origin/main..HEAD --oneline` — if it returns anything, the push didn't take and the commit is still local. Don't sign off the task until this is empty.

3. **Verify the next cron run uses the new SHA.** For fixes that affect a scheduled workflow, check `gh run list --limit 3 --workflow <workflow-name> --json headSha,status,conclusion,createdAt` before declaring "shipped to production." The latest run's headSha should match the fix commit (or be ahead of it). If the latest successful run predates the fix, the deploy hasn't actually happened yet — flag it and either trigger workflow_dispatch or warn the user the next scheduled run is when it'll take effect.

**End-of-session checklist (do not skip):**
- Any local commits ahead of origin? → push them
- Any fixes shipped today that need a cron to exercise them? → confirm the cron has actually run with the new SHA, OR explicitly tell the user "fix is on origin/main; next cron at <time> will be the first run with it live"
- If both are clean, then OK to call it a night

**Why the verification matters:** A green `git commit` + `git push` output is necessary but NOT sufficient. The actual production state is what the workflow runner sees. Skipping the SHA verification step is what created the 5/31 → 6/1 recurrence — push was forgotten entirely, and there was no end-of-session check to catch it.

**Related:** [[project_pm_cron_live_game_prop_overwrite]] (the fix that didn't propagate), [[project_sweat_dim_jerry_drift_531]] (same)
