---
name: project-external-scraper-degradation-902
description: 9/2 finding — 4 external handicapper scrapers stalled 2-3 days ago (oddscrowd/sbr/covers/betfirm). Diagnostic needed with live GHA logs.
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-02T22:35:25.120Z
---

🚨 **9/2 finding: 4 external handicapper scrapers stalled around 9/1.**

**Impact:** External Handicappers panel shows fewer picks per game.
Per-source calibration data thinning. Not catastrophic (scoresandodds
still active) but affects the "external transparency" moat.

**Latest write per source (external_picks table):**
- `oddscrowd` — 381 rows / latest 2026-09-01 → dead 2 days
- `sbr` — 82 rows / latest 2026-09-01 → dead 2 days
- `covers` — 38 rows / latest 2026-09-01 → dead 2 days
- `betfirm` — 77 rows / latest 2026-08-31 → dead 3 days
- `scoresandodds` — 311 rows / latest 2026-09-05 → still active ✓

**How to diagnose:**
1. Trigger manual `pull_externals_mlb.py --source covers --dry-run` (etc)
   locally + capture stderr
2. Check GHA logs for last successful pull_externals_mlb workflow run
3. Common causes: HTML changes on scraped sites, rate limit / IP ban,
   auth cookie expired, Cloudflare block

**Common breakage window:** all four dropped ~same timeframe (8/31-9/1).
Could indicate:
- (a) Shared user-agent got blocked
- (b) `pull_externals_mlb.py` script had a shared code path that broke
- (c) GHA workflow itself failed on those days (check run history)

**Priority:** Post-launch investigation. Scrapers are fragile by nature;
scoresandodds keeps user experience whole. But 4/5 external sources
missing means Split source diversity + External Handicappers panel
both feel thin.

**Related:**
- [[project-per-source-tracker-moat-818]] — source calibration table depends on this data
- [[project-tos-scrub-source-names]] — brand hygiene (sources anonymized as Split N)
