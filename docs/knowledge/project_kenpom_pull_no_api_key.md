---
name: project-kenpom-pull-no-api-key
description: "NCAAB KenPom puller logs \"Missing KENPOM_KEY env var\" despite secret being set in GH; investigate when NCAAB Phase 1 work resumes"
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---

User reported 6/1: NCAAB KenPom pull step in the MLB workflow says "no api key available" even though the GH secret IS set. Likely the literal log line: `❌ Missing KENPOM_KEY env var` from [ncaab_pipeline.py:47](mlb_pipeline/ncaab_pipeline.py#L47).

**Code path verified clean:**
- `.github/workflows/mlb_pipeline.yml:233` — step's env block sets `KENPOM_KEY: ${{ secrets.KENPOM_KEY }}` correctly
- `ncaab_pipeline.py:29` — reads `os.environ.get("KENPOM_KEY") or os.environ.get("EXPO_PUBLIC_KENPOM_KEY")` correctly
- Step is gated to Mondays only (`if [ "$DAY" = "1" ]`) so the failure was on a Monday cron, not today (6/1 is Sunday)

**Most likely causes (in order):**
1. **GH secret `KENPOM_KEY` is unset or has an empty string value.** GitHub silently injects empty for missing secrets — the code reads "" → falsy → "missing env var" message. Fix: verify the secret exists and has a real value in repo settings → Secrets and variables → Actions.
2. **Secret named differently** — e.g., `KEN_POM_KEY` or `KENPOM_API_KEY`. Step env block won't match. Fix: rename secret OR change workflow to use whatever the actual name is.
3. **Org-level secret not propagated to repo** — if KenPom is an org secret, ensure the workflow is in a repo that has access.
4. **Stale workflow run** — if the workflow was triggered before the secret was added, env block was empty. Trigger a fresh run after confirming the secret value.

**How to debug fast:**
- Add a temporary `echo "KENPOM_KEY length: ${#KENPOM_KEY}"` to the workflow step (prints length without exposing value). Run it. If it prints 0, secret is empty.
- Or: temporarily print `os.environ.get("KENPOM_KEY") is None` + `len(os.environ.get("KENPOM_KEY") or "")` in `ncaab_pipeline.py` before the early-return.

**Priority:** Low until NCAAB season (Nov 2026). KenPom is mostly static offseason; weekly pulls are nice-to-have, not urgent. Promote to medium once NCAAB Phase 1 v1.0 build queue starts.

**Related:** [[project_ncaab_scope]] (NCAAB v1.0/v1.1 scope decisions), [[feedback_no_kenpom_attribution]] (never name KenPom in user-facing copy — internal data source only)
