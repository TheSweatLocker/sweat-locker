---
name: Barrel% slump detector + Pitcher BB/H rolling stats (2026-05-11)
description: Two data features shipped — Statcast barrel% as luck-vs-skill signal in hits Over/Under scoring, plus L7 rolling BB and H per start in pitcher class projections.
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
Shipped two data features on 2026-05-11 in response to the hits-UNDER cohort calibration work + user request.

**Feature 1 — Barrel% slump detector (generate_props.py):**

Added `fetch_batter_quality()` that pulls Savant's per-batter barrel% / hard-hit% leaderboard (one-time CSV fetch, cached). Same source build_hr_watch.py already uses.

**Logic in hits-UNDER scoring (`score_batter_hits_under`):**
- If barrel_pct ≥ 9.0 AND L7 got_hit_rate ≤ 0.40 → **conviction -6** with signal "Barrel% X.X% (above avg) despite L7 cold — quality contact, due for regression"
- If barrel_pct ≤ 4.0 AND L7 got_hit_rate ≤ 0.40 → **conviction +4** with signal "Barrel% X.X% confirms cold (no quality contact)"

**Logic in hits-OVER scoring (`score_batter_hits`):**
- If barrel_pct ≥ 10.0 AND L7 got_hit_rate ≤ 0.40 → **conviction +6** "Barrel% elite despite L7 cold — due for regression"
- If barrel_pct ≥ 8.0 AND L7 got_hit_rate ≤ 0.50 → **conviction +3** "underlying quality contact"

**Why it matters:** Directly addresses the Mitchell/Lowe/Chapman 5/9 disaster (all 3 PRIME hits-UNDER picks lost when batters broke their hitless streaks). If we'd flagged "unlucky cold" vs "genuinely cold," conviction would have been downgraded. Pairs with the PRIME multi-signal gate shipped earlier same day.

**Feature 2 — Pitcher BB/H per start tracking (compute_pitcher_class_projections.py):**

Extended gameLog capture to also include `hits` (was capturing ip/er/k/bb/hr already, hits was missing). Added `aggregate_l7_rolling()` function — most recent 7 starts → avg BB/H/K per start + BB/9, H/9, K/9, ERA.

**Output now includes:**
- Per-class buckets: avg_ip / avg_er / avg_k / avg_bb / **avg_hits** / avg_hr / era_in_class
- L7 rolling: avg_bb, avg_hits, avg_k, avg_ip, bb_per_9, hits_per_9, k_per_9, era

**Scout report surfaces both** in the pitcher class projection section.

**Why it matters:**
- Walks props (Pitcher Total BB O/U 1.5) and hits-allowed props are under-modeled markets vs K props
- Content angle: "Brandon Young has averaged 1.71 BB and 5.43 H per start over his L7 — supports K UNDER 4.0 with his 3.29 K/start"
- Sets foundation for Phase B (wire into pipeline for BB/H prop generation)

**5/11 verifications:**
- Brandon Young L7: 1.71 BB / 5.43 H / 3.29 K — directly supports K UNDER 4 play (3.29 K avg vs 4 line)
- Drew Rasmussen L7: 0.86 BB / 4.0 H / 5.29 K — elite recent, confirms Rays DAWG
- Roki Sasaki L7: 2.14 BB / 4.86 H — walks-heavy, supports SF/LAD over angle
- Kevin Gausman L7: 1.29 BB / 5.14 H / 4.57 K — solid recent (mastery fade vs Rays still primary signal)

**File locations:**
- `mlb_pipeline/generate_props.py` — `fetch_batter_quality()`, slump detector in both hits scorers (insertions around lines 173/1280/1454)
- `mlb_pipeline/compute_pitcher_class_projections.py` — `aggregate_l7_rolling()`, extended gameLog capture, per-class hits field
- `mlb_pipeline/scout_report.py` — updated `proj_line()` to show BB/H per class + L7 rolling block

**Followup queue:**
- ~~Add BB Over/Under prop generators~~ ✅ SHIPPED 2026-05-11 (see below)
- Add Hits-Allowed Over/Under prop generators in generate_props.py (still pending)
- Audit cohort: did barrel% slump detector materially improve hits-UNDER PRIME hit rate? Check at 5/17 audit.

---
**UPDATE 2026-05-11 — Pitcher Walks O/U props SHIPPED (Phase B item #1):**

`score_pitcher_bb_over()` / `score_pitcher_bb_under()` in generate_props.py. Projection-first design:
- `get_pitcher_projection(name)` loads `data/pitcher_class_projections.json` (built by compute_pitcher_class_projections.py) — must run BEFORE generate_props (cron reordered: projections step moved to right after team_hr_threats, before HR Watch + props)
- Primary signal: L7 `avg_bb` vs the 1.5 line. Over needs margin ≥0.2, big boost at ≥1.0. Under needs margin ≥0.2, big boost at ≥0.7.
- Adjustments: BB/9 corroboration, 1st-inning WHIP (control wobble), opp lineup patience proxy (team K%), days-rest rust
- `signals._projected_bb` = headline number the app surfaces ("X.X expected walks")
- Tier: PRIME ≥70, STRONG ≥55 (scoring scale tops ~60 for strong cases, so thresholds scaled down vs other props). BB_CUTOFF = BB_UNDER_CUTOFF = 55.
- Opener filter: skips if last_ip ≤1.5 or L7 avg_ip <3.0 (over) / <4.0 (under)

App display: `app/index.tsx` propLabel now handles bb_over/bb_under ("X.X expected walks (over/under N)") + filled in outs_over/outs_under/er_over/er_under labels (were falling through to raw prop_type string).

Resolver: `resolve_game_results.py` now grades bb_over/bb_under (baseOnBalls), outs_over/outs_under (inningsPitched → outs), er_over/er_under (earnedRuns) — these prop types were generated but NEVER resolved before this fix (pre-existing gap, `else: continue` swallowed them).

5/11 first output: Drew Rasmussen bb_under O/U1.5 at STRONG (conv 64), projected 0.9 BB/start. Thin slate (6 games) so only 1 BB prop surfaced — more expected on 15-game slates with extreme walk-rate pitchers.

**Phase B COMPLETE 2026-05-11** (everything below shipped same day as BB props):
- ✅ Hits-Allowed O/U prop generators: `score_pitcher_ha_over()` / `score_pitcher_ha_under()` in generate_props.py — projection-first from L7 `avg_hits`, line 5.5, adjusts opp wRC+ / park / L3 ERA. `signals._projected_hits`. Tier PRIME ≥70 / STRONG ≥55. HA_CUTOFF = HA_UNDER_CUTOFF = 55. Wired into run() loop after BB props.
- ✅ App display: propLabel handles ha_over/ha_under ("X.X expected hits allowed (over/under N)")
- ✅ Resolver: resolve_game_results.py grades ha_over/ha_under via pitching `hits` stat
- ✅ Pitcher Scouting panel: app/index.tsx matchupTab='stats' MLB section now shows a "📋 PITCHER SCOUTING — last 7 starts" block for both starters (avg K/BB/H/IP per start + K/9, BB/9, H/9, ERA). Reads from new `pitcherProjections` state.
- ✅ Supabase table: `pitcher_projections` (migration: supabase/migrations/20260511_pitcher_projections.sql). compute_pitcher_class_projections.py upserts to it after writing the JSON cache. App fetches it in fetchMLBGameContext (limit 200, keyed by lowercased pitcher_name).
- ✅ Cron reorder: compute_pitcher_class_projections.py moved to run after team_hr_threats, before HR Watch + generate_props (props depend on the fresh cache).

**⚠️ USER ACTION REQUIRED:** Apply `supabase/migrations/20260511_pitcher_projections.sql` in Supabase SQL editor. Until then the projections upsert 404s (non-fatal — JSON cache still works for the server-side props/scout) but the app's Pitcher Scouting panel won't populate.

**5/11 first outputs:** Drew Rasmussen — bb_under O/U1.5 STRONG (proj 0.9 BB/start), ha_under O/U5.5 PRIME (proj 4.0 H/start). Thin 6-game slate so limited; more on 15-game slates.

**Followup queue (post-launch):**
- Audit cohorts for bb_over/bb_under and ha_over/ha_under once n≥20 resolved each
- Audit cohort: barrel% slump detector impact on hits-UNDER PRIME hit rate (check 5/17)
