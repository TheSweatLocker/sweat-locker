---
name: K props display switched from "Over X.X" to expected Ks (2026-05-08)
description: K prop labels now show model's expected-Ks point estimate instead of audit threshold; book line/juice varies so users shop their book
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
K prop user-facing labels switched from `Over 5.1 Strikeouts` to `8.1 expected Ks (over)` across all surfaces.

**Why:** Pipeline's `prop_line` (e.g., 5.1) is an internal audit threshold — no book actually offers that line. For high-K aces the standard book line at 5.1 can be juiced -700+ (essentially unplayable), while the playable alt-line is 7.5 at -150. Showing users "PRIME 100 Over 5.1" risks them either betting unplayable juice or not realizing the alt is the actual edge.

**How to apply:** Pipeline writes `signals._projected_ks` (uncapped point estimate, ~K%/100 × 22 BF for over, × 18 BF for under). App reads this, displays `X.X expected Ks`. Audit math still uses `prop_line` threshold — change is display-only.

**Surfaces updated:**
- Props view: `app/index.tsx:9551` (main pipeline props list)
- Sweat Card top props row: `app/index.tsx:8498`
- Daily Degen leg labels: `mlb_pipeline/generate_daily_degen.py:172`
- Pipeline writes `_projected_ks`: `mlb_pipeline/generate_props.py` in `score_pitcher_ks` and `score_pitcher_ks_under`

**Convention:** underscore-prefixed signal keys are metadata, not display bullets. App filters `signalEntries` to drop `_*` keys from the bullet list so projected_ks doesn't render as a stray bullet.

**Hits props unchanged** — `Over 0.5 Hits` IS the actual book line, so display matches reality.

**Tomorrow's audit checks (5/9 morning):**
1. Did Hits UNDER PRIME (Barrosa + Fernandez vs McLean) hit? Cohort was 0-3 from 5/3 — moves to 0-5 or 2-3.
2. A's/Orioles concentration (POTD + Dawg + 6 PRIME hits) — pay or punish?
3. Mets/DBacks PRIME +6 confluence cash?
4. Brewers/Yankees chalk-trap save was validated — shutout would have been confluence-PRIME loss without the edge gate. n=1 but real evidence the fix works.
