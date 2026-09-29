---
name: project-v1-0-1-client-priorities
description: v1.0.1 client-side punchlist compiled from 9/7-9/8 session; each item has server-side status + reason for client-side companion work
metadata: 
  node_type: memory
  type: project
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-17T17:30:44.753Z
---

**v1.0.1 client-side punchlist** (Apple review of v1.0 pending 9/8). Server-side hot-fixes deploy immediately, client work batches here.

**Why:** Server-side fixes deploy in minutes; client-side edits mean an App Store rebuild + submission + review wait. Bundle these so the second review round covers a coherent set.

**How to apply:** When user asks "what's on v1.0.1?", answer from this list. When shipping a server-side fix, note here if a client counterpart is needed.

---

**1. Sharp Card composer → prop_jerry_reads direction** — ✅ SHIPPED babbdf71 (9/12 evening)
- Fix: direction-flip guard added to `_compose_mlb_props` in generate_sharp_card.py. Fetches canonical direction per (player, family) from prop_jerry_reads at composer entry; skips any mp row whose direction doesn't match. New drop-counter `direction_flip=N` in the daily gate log.
- Verified: 102 mismatches over past 14d would have been caught. Today's (9/12) slate had zero mismatches — recent upstream fixes already drove the count down, guard is defensive.
- Watchdog `prop_direction_flip_drift` continues to flag daily as of 34c94f4a for early-warning even with the composer guard in place.

**2. Receipts surface cross-sport rendering** — ✅ SHIPPED 5c1b7f98 (9/12 evening, pre-launch date labels for NHL/NBA/NCAAB)
- Current state (verified 9/8):
  - MLB: mlb_sides 81-51-3, prop 300-107, sharp_card 280-165, potd 17-7, ledger 47-55, dawg 5-15, ladder 8-12
  - NCAAF: ncaaf_sides 34-15-2, sharp_card 3-1
  - NFL: 0 rows (season starts 9/10 — expected)
  - NBA/NHL/NCAAB: 0 rows (offseason, expected)
  - UFC: 0 rows → PICKER SHIPPED 38b61fd7 (next daily run will populate ~28-30)
- Client should render "coming Nov 3" / "coming Oct 8" sentinels for offseason sports instead of blank sections.

**3. Prop deep-dive tab** [[project_prop_deep_dive_tab_905]]
- Queued post-launch. Per-player last-10 rolling + book distribution. See [[reference_lineterminal_prop_ui]] for reference.

**4. Home-screen retention** [[project_home_screen_hot_streak_907]]
- Adaptive record header + dynamic hot-streak banner + gold admin note styling.

**5. UX punchlist** [[project_ux_punchlist_907]] — ✅ PARTIAL SHIP (3 of 5, 9/12 verified)
- ✅ #1 Weekly-sport day+time labels — line 14168 renders weekday prefix for NFL/NCAAF/UFC (prior session)
- ⏸ #2 Week-switch timing subtitle — needs UX decision (skipped; see [[project_v1_0_1_client_priorities]] #16)
- ✅ #3 Paywall Receipts bullet — added to both Jerry (line 14806) + Steam Room (line 16342) paywalls
- ✅ #4 NCAAF/NFL card sparseness — sub-chips (AP rank / SP+ / season ATS / L10 venue ATS / Madden OVR / Top100) implemented at lines 14197-14262. Data path via nflGameContextMap / ncaafGameContextMap. Andy device-verify recommended but code is in place.
- ✅ #5 Sport-aware "when to view" hint — sport_registry.today_note wired at line 13350

**6. Toggle infrastructure rollout** [[project_ui_toggle_infrastructure_908]] — ✅ SHIPPED 8cccf562 (9/14 evening)
- config_ui_sections wired 9/8 e1acd09e. NFL/NCAAF Team Matchup POC.
- 9/14 extension (8cccf562): SportWeatherCard + TeamTendenciesCard wrapped. NFL slot now passes sport="NFL" (was defaulting to NCAAF key). LensGrid (10 keys), NCAAF (3), NFL (4), NBA (4), NCAAB (4), shared (weather × 2, trends_tendencies × 3) all reactive to SQL toggle now.

**7. NFL Prop Jerry visual QA** [[project_nfl_prop_jerry_overnight_status_907]]
- Backend parity shipped overnight 9/7. Screenshots + polish before v1.0.1 ship.

**8. NFL prop signal gap** — ✅ SHIPPED 2ddaeb3a (9/12 evening, coverage pill hidden when pct=0). Server-side signal-computation fix tracked separately in [[project_nfl_prop_signal_gap_908]].

**9. Sportsbook default UX** — ✅ RESOLVED 9/14 (Andy: "leave HR as sportsbook default")
- Hard Rock Bet stays as default. Rationale: existing partnership and consistent brand across the app. Book selector remains available for users who prefer DK/FD. No app change needed. See [[project_sportsbook_default_ux_907]] for the original UX discussion.

**10. August-referencing record note** — ✅ SHIPPED 11057816 (9/12 evening)
- Located in Ladder tab gate caption at app/index.tsx:16597. Rewritten from "3 of 5 gates must hit. Prior 5-of-5 rule left the ladder empty most days — loosened Aug 20 to keep it firing." → "Ladder publishes when 3 of 5 quality gates fire on a pick." Ships with next App Store rebuild.

**11. Website content parity** [[project_website_update_queue_908]] — ✅ DONE (Andy 9/13)
- Marketing site content updated to match v1.0 launch messaging.

**12a. Generic `<InfoChip>` component + `align.chips_extra` rendering** — ✅ SHIPPED 0ac694b4 (9/12 evening, tap-Alert tooltip). Unlocks GOAT / extreme_public / sweat_pick badge rendering without further client work.
- Backend now writes `nfl_game_context.align_status.chips_extra[]` — array of `{key, label, value, tooltip, kind, priority}` objects.
- **Why:** enables backend-driven chips (add new models/signals without app rebuild). GOAT is the first chip; future models drop into this array server-side and the app surfaces them automatically.
- **App work needed:** ~40 lines in [app/components/GameDetailV2.tsx](app/components/GameDetailV2.tsx):
  - New `<InfoChip>` component that reads `{label, value, tooltip, kind}` and renders like existing align chips
  - Tap opens a small modal showing the tooltip
  - Iterate `align.chips_extra || []` and render each, sorted by `priority` desc, alongside current hardcoded chips
- **Kind mapping:** `ok` → green, `warn` → yellow/gold, `neutral` → grey (match existing chipStyleFor).
- Ships with GOAT chip live day 1. TOS-safe copy already baked into tooltip.

**15. Upgrade Expo SDK 54 → 57** — added 9/10 (user pain: dev-client flow is friction city)
- **User pain:** dev-client setup was miserable 9/10. Multiple hours lost to install/trust/network debugging just to see the app on phone. Would rather use Expo Go for daily QA since it's zero-setup.
- **Blocker to Expo Go:** SDK 54 project vs Expo Go current SDK 57 mismatch. iOS can't install older Expo Go versions. Only ways around: (a) build dev-client (current painful path), (b) upgrade project SDK to match Expo Go.
- **Upgrade path:** `npx expo install expo@latest --fix` + `npx expo-doctor` + full regression QA. Requires bumping ~20 peer packages; some may have breaking API changes.
- **Risk:** SDK 54 → 57 is 3 major versions. Real risk of screens/components regressing silently. Would need every tab tested manually after upgrade.
- **Do NOT ship in v1.0.1 launch cycle** — SDK upgrade + all v1.0.1 items in same review round = too many variables. Sequence: land v1.0.1 first (fixes + polish), then v1.0.2 = SDK upgrade only.
- **Payoff:** post-upgrade, QA becomes `npm start` → scan QR in Expo Go → done. No EAS build, no cert trust, no network debugging.

**14. Alignment chip clarity — "Market aligned" vs "Model aligned"** — ✅ SHIPPED f98df5f7 (9/12 evening, first pass — labels renamed to "Market …" prefix)
- **User pain:** HOU @ PHI 9/10 showed green "Strongly aligned" chip (3 dots) right above card that read "LOW CONVICTION — Downgraded to LEAN." Both true (market: 6/6 books + 87% bets on HOME; model: LR sees coin flip). Chip label doesn't distinguish market vs model alignment → users read as internal contradiction.
- **Fix path** (client, `app/components/StatusChip.tsx` line 57):
  - Change `aligned_strong` label from `'Strongly aligned'` → `'Market strong'`
  - Add a second chip variant for `model_aligned` that shows model→pipeline agreement
  - Update `align_status` reader in `GameDetailV2.tsx` to render both when they disagree
- **Server-side already ready:** `align_status.overall.verdict` is available; add matching model-alignment computation as follow-up if desired.
- **Non-blocking** — chip is honest (market is aligned), just misleading label. Higher-priority for v1.0.1 given user visibility.

**13. Scaling mitigations for 500-user launch** — added 9/9 🚨
- **Context:** Pro tier Supabase = 200 concurrent DB connections + 250GB/mo egress. 29 `SELECT *` calls in app/index.tsx. Biggest offender: `fetchMLBContext` runs up to 5 sequential SELECT * on mlb_game_context (17KB/row × 5 = 85KB per user click). At 500 users this is ~42MB per game click across user base.
- **Server-side landed 9/9** (migration 20260909f, applies now, benefits ALL clients):
  - Hot-path indexes on mlb_game_context (game_date, teams), same for NFL/NCAAF, jerry_cache (cache_key, fetched_at)
  - Slim VIEWs `mlb_game_context_slim` / `nfl_game_context_slim` / `ncaaf_game_context_slim` with only display columns (~2KB/row, 88% egress reduction)
- **Client work needed in v1.0.1:**
  - **13a.** Swap all 29 `SELECT *` to explicit column lists. Biggest wins: `fetchMLBContext` → use `mlb_game_context_slim` view (85KB → 10KB per game click). Same for NFL/NCAAF ctx fetches.
  - **13b.** Wrap all `supabase.from().select()` calls in a shared helper with **8s timeout + 1 retry with 500ms backoff**. Currently no timeout → hung queries pile up connections. Helper: `dbFetch(table, opts)` in `app/lib/db.ts`.
  - **13c.** Add 5-min TTL client cache on stable reads: games list, sharp_card, potd, receipts. Use React Query or roll-your-own with AsyncStorage.
  - **13d.** Capacity fallback UI: when a fetch fails 2× or times out, show "We're at capacity — try again in a moment" chip instead of blank tab. Never white-screen.
- **Rollout order (v1.0.1):** 13a highest impact (single biggest egress cut), 13b safety net, 13c retention win, 13d polish.
- **Verification:** measure egress/hr in Supabase Dashboard before + after ship. Target: 60%+ reduction on peak-hour reads.

**16. NFL + NCAAF "This Week" tab bleeding into next week** — added 9/12 🚨 (NCAAF confirmed 9/12 evening)
- **Symptom (NFL):** After Denver @ KC MNF (9/15 = Week 1 finale), "This Week" tab still shows next week's games. Detroit @ Buffalo TNF (Thu 9/18 = Week 2 opener) appears in Week 1 window.
- **Symptom (NCAAF):** Miami @ Wake Fri 9/19 (Week 3) appears in Week 2 tab when viewing on Fri 9/12. Same class as NFL. Andy screenshot confirms.
- **Root cause hypothesis (needs verify):** Week-boundary calc in the games list uses a naive "next 7 days from today" window instead of anchoring to the NFL week boundary (Thursday morning). NFL Week N runs Thu → Mon following. After MNF completes, tab should be empty until Thu.
- **Where to look:** `app/index.tsx` NFL games filter — likely a `game_time > now && game_time < now + 7d` predicate that should be `game_time in nfl_week_bounds(current_week)`.
- **Fix path:** Add `nfl_week_bounds()` helper reading from `nfl_schedule.week` column (already in DB), filter this-week tab strictly by that column. Next-week tab handles Thu-forward.
- **Not blocking today** but visible on Sun 9/14 slate view — user landed on it 9/12.

**17. AdminNoticeBanner positioning — pushes content above app title** — ✅ SHIPPED f407f323 (9/12 evening, top inset via expo-constants)
- **Symptom:** Live admin_notice row renders at top of screen but pushes down (or shifts above) the app title/SafeArea header. Andy 9/12: "on top running off above app title" when the first two notices were posted (id=1 NFL refresh, id=2 NCAAF flips). Notices expired immediately to unblock, but the layout bug will re-bite any future use.
- **Where to look:** [app/components/AdminNoticeBanner.tsx](app/components/AdminNoticeBanner.tsx) — likely wrapped in absolute/fixed positioning without proper SafeAreaView + top-inset padding, so it renders behind or above the header instead of below it.
- **Fix path:** Wrap in `<SafeAreaView edges={['top']}>` OR pass explicit `paddingTop: insets.top + HEADER_HEIGHT`. Ensure banner sits BELOW the app title, not above/behind it. Add a min-height reserve on the page when a notice is active so layout doesn't jump.
- **Blocks:** any future use of admin notices for live-ops messaging (game refresh, capacity, grader delay). Currently we cannot ship an admin note without triggering this bug.

**18. NFL prop conviction tier-snap fix** — ✅ SHIPPED 5aa4ed76 (9/13) · added 9/12 🚨 (Andy: "1.0.1 has to have this fixed")
- **Symptom:** NFL prop conviction values snap to tier caps — 100% of PRIMEs at exactly 95, 49% of STRONGs at exactly 85, 50% of LEANs at exactly 65. Conviction stops meaning "how confident" and becomes "which tier bucket."
- **Root cause:** `mlb_pipeline/nfl_prop_signal_discipline.py` lines 168-197 all use `min(CAP, FLOOR + int(edge_pct))`. Since edge_pct qualifiers cluster right above tier thresholds (PRIME needs 18, most PRIMEs land 18-25), and CAP is only 15 pts above FLOOR, virtually every qualifier hits the cap.
- **Concrete data (Sun 9/13):** 51 PRIMEs at conv=95 uniform, 46/93 STRONGs at 85, 85/169 LEANs at 65.
- **Fix path:** rewrite the 4 return statements in `_score_prop` to blend edge_pct + score (0-5.75) + hit_pct (0-1.0) into conviction so it actually varies. Starting coefficients (need retuning against backtest):
  ```
  PRIME:  min(97, 40 + 1.5*edge + 3.5*score + 10*hit_pct)
  STRONG: min(84, 30 + 1.5*edge + 2.5*score + 8*hit_pct)
  LEAN:   min(68, 25 + 1.2*edge + 2.0*score + 5*hit_pct)
  LIGHT:  min(58, 20 + 1.0*edge + 1.0*score + 4*hit_pct)
  ```
- **Downstream gate audit (already run 9/12):** no NFL-specific code branches on conviction >= 95 or 85. Existing gates use `>= 60` (prop_ensemble_scorer.py:652,683) and `>= 65` (prop_ensemble_scorer.py:785) — all safely below the new formula's floor. Tier assignment logic unchanged; only conviction number changes.
- **Testing plan before merge:**
  1. Backtest simulation on completed Wk 1 games (once results grade Sun-Mon)
  2. Tune coefficients so PRIME distribution has real spread (not >70% at cap) and LEAN doesn't snap to a new cap
  3. Cross-check MLB props are unaffected (they use a different scorer, but confirm)
- **Files:** `mlb_pipeline/nfl_prop_signal_discipline.py` only. ~4 line-changes. Zero schema migration.
- **Blast radius:** all NFL prop conviction values shift. Tiers stay the same. Sharp Card / prop ranking within tier becomes meaningful.
- **Priority: HIGH — Andy explicit blocker for v1.0.1 batch.**

**19. NFL prop synth cadence gap — Sun 9/13 slate props never synth'd** — added 9/12 · ✅ SHIPPED b9c7aa98 (9/12 evening)
- **Symptom:** 15 pass_attempts + 29 rush_attempts + 17 pass_completions NFL prop rows for Sun 9/13 exist in `nfl_pipeline_props` (with full `_stat_last10` populated), but ZERO have rows in `prop_jerry_reads`. Users tapping them in the app get blank cards.
- **Root cause (verified 9/12):** _NOT_ a family whitelist — `_STAT_META` (render_prop_template.py) and `NFL_STAT_MAP` (backfill_prop_lookback.py) both include pass_attempts + rush_attempts + pass_completions. Also NOT a dedup collision (only one direction per player-family remains post-discipline). **Real cause:** `generate_prop_jerry_synthesis.py` was last invoked for game_date=2026-09-11 (Thursday), never for 9/13 or 9/14. Props promoted to PRIME after the Thursday synth run have no matching jerry_reads until the next synth invocation.
- **Fix path (two parts):**
  1. **Immediate:** run `generate_prop_jerry_synthesis.py --sport NFL --date 2026-09-13` and `--date 2026-09-14` — creates the missing jerry_reads for tomorrow. Fired 9/12 evening as tasks bonx9vwyb + bspuz8vvu.
  2. **Systemic:** the daily NFL workflow must invoke prop synth for the CURRENT slate day (game_date), not just Thursday's slate. Check `.github/workflows/nfl_pipeline.yml` — likely calls synth with game_date=today only when cron day == kickoff day, missing Sunday when Sat cron runs. Fix: pass `--days 3` window or invoke per game_date in the next 3 days.
- **Same class as:** Skenes "Under 5.5 ks under" label bug — quiet on the surface, obvious once tapped.
- **Priority: HIGH** — Andy 9/12: verified during social-post prep. Blocks half our star-name volume plays from rendering. Manual runs cover Sun 9/13; systemic fix needed before Sun 9/20.

**12. NHL + NBA historical odds backfill (LR unblock)** — added 9/8 · ✅ NHL SHIPPED 753fec97 (9/14), NBA queued
- **NHL 9/14 result**: 1,120 / 1,335 games (83.9%) populated in 10min via The Odds API historical endpoint. 215 gaps: ~200 afternoon matinees before 22:30 UTC snapshot window, 7 4-Nations Face-Off (no odds), 5 Rangers-vs-Islanders (both "New York" — unresolvable). Improvement path: add 17:00 UTC snapshot pass to recover matinees.
- **NBA 9/17 verified DONE**: 1,198/1,324 rows (90.5%) have close_home_ml + close_spread + close_total. Better coverage than NHL (83.9%). Ran at some point post-9/8 unblock. Ready for `nba_ml_logreg_train.py`.
- **Discovery**: 8/20 report was wrong about needing paid tier — our existing ODDS_API_KEY has historical endpoint access. 4.9M credits remaining budget.
- Post-backfill: `nhl_ml_logreg_train.py` + `nba_ml_logreg_train.py` now have market features. Add to `mlb_refit_weekly.yml`; add both sports to `defensive_gates.py` `_model_map` + whitelist.
