---
name: Weekly audit items (week of 2026-04-19)
description: Docket of model/data audits to complete this week
type: project
originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
---
Items to audit/investigate this week:

1. **UFC stat check** — Verify UFC fighter stats are being used correctly in prop grading and Jerry's UFC game takes. Check that `ufc_fighter_stats` table data flows properly into the UFC prompt context. Audit whether SLpM, TD defense, finishing rate, etc are actually surfacing in analysis or being ignored.

2. **Umpire data usage and availability** — Check umpire assignment coverage in recent pipeline runs (many games showing "Umpire assignments loaded for 0 games"). Investigate why umpire data is intermittent. Verify the stored umpire NRFI rates and over_rate are actually feeding into NRFI score and total projection when available.

3. **Remote model config via Supabase** — Build a `model_config` table with JSONB values for tier thresholds (NRFI tiers, spread delta tiers, total delta tiers, HR Watch cutoffs, Prop Jerry grade thresholds). App fetches on launch, caches 12-24h locally with hardcoded fallback defaults. Pattern enables threshold tuning after audits without TestFlight builds. Start with NRFI tiers only, expand once validated. ~2 hour refactor, highest value post-launch.

4. **HR Watch text cleanup** — Visual polish issue: certain player names overlap or look cramped in the HR Watch list. Also audit whether the proposed names on est lineup matches what user actually expects (sanity check the scoring logic surfaces the right guys). Layout fix in app, no pipeline change needed.

5. **Jerry tab audit — Fades / Prop Jerry / Daily Degen** — Three-part audit of the Jerry tab:
   - **Fades tab**: user feels it's fairly useless. Consider killing it OR reworking content (is it duplicating data already shown elsewhere? Does it surface plays not visible from the main game list?).
   - **Prop Jerry**: still producing bad writes. Specific bug — said "Heineman can get a hit" but referenced Laureano (Heineman's teammate) as the pitcher, when Laureano was actually the starter FOR that team. Entity confusion between batter/pitcher/team. Matchup Prop and EV Prop both feel ineffective. Need prompt audit + probably filter out props where the referenced pitcher is on the same team as the batter.
   - **Daily Degen**: audit what it's selecting. Also architectural question — currently might be regenerating per-user on load. Should be **generated once server-side** (pipeline run after 2pm) and cached in Supabase, served identically to all users. Would reduce API costs + ensure consistency + speed up load.

6. **Jerry Track Record discrepancy** — ✅ FIXED 2026-04-22. Was excluding 80-89 dead zone + including 95+ volatile. Now tracks only lean tiers (70-79 + 90-94) with matching label.

7. **In-app onboarding update** — ✅ DONE 2026-04-22. Steps 1, 3, 4 rewritten for pipeline/Dawg/Savant. Settings How It Works also refreshed.

8. **LAUNCH BLOCKER — "Why This Score" card on game detail** — Pre-launch must-have per Andy 2026-04-22. Users currently see the final Sweat Score but no explicit breakdown of contributing signals. Build a card on game detail that lists top 4-6 signals with their score contribution, e.g.:
     ⚡ ML spread delta +4.2 (HIGH conviction)    +12
     🔒 NRFI PRIME tier (score 92)                +8
     📊 Platoon-adj wRC+ gap (+17 vs LHP)         +6
     🧤 Catcher framing edge (+3 runs)            +4
     🏟️ Park factor 108 (hitter friendly)         +5
   All contributing factors are already computed inside calcGameSweatScore (marketEfficiency, modelMismatch, lineTrajectory, sharpSignal, situationalEdge) — need to either (a) surface those 5 high-level buckets OR (b) refactor calcGameSweatScore to return a list of labeled contributions with their bump amounts. Option B is more marketing-friendly ('here's exactly why this game scored 86') but requires ~2 hours of refactor. Must complete before App Store launch.

9. **Weekly audit dashboard (Week 1 post-launch)** — Build internal validation tooling (not user-facing). Runs locally or via scheduled job. Outputs:
   - Daily digest: yesterday's pipeline props + resolved hit rate by tier (PRIME/STRONG/LEAN) and by type (hits_over/ks_over)
   - Rolling 14-day tier performance chart
   - Divergence alerts: if PRIME hit rate drops below 55% over 20+ resolved picks → flag conviction formula overweighting noise
   - Sample size tracker per tier (green ≥30, yellow 15-29, red <15)
   - NRFI retro calibration comparison (90-94 vs 70-79 vs 80-89 buckets, flag if boundaries shift)
   - Model MAE tracker (Ridge total v5 baseline = 3.63, track toward v6 target ≤3.30)
   ~3 hours to build. Gates the WC greenlight — Andy won't commit to WC build until Week 3 audit confirms Gate 1 (PRIME ≥58% hit rate) is cleared.

10. **MLB model validation gates before WC greenlight** — Three non-negotiable gates per Andy 2026-04-22:
   - Gate 1 (Week 3, ~May 24): Prop Jerry PRIME tier ≥58% hit rate over 30+ picks
   - Gate 2 (Week 4, ~May 31): NRFI tier audit with 550+ games confirms 90-94 PRIME stays >68%; tune boundaries if divergent
   - Gate 3 (Week 4-5): Retrain Ridge total model v6 + spread model with new enrichment features (platoon-adj wRC+, L3 ERA/K%, OAA, framing, xwOBA, hard hit%). Target v6 MAE ≤ 3.30 (from 3.63 baseline). Must beat v5 on held-out games.
   If all 3 clear by June 1 → 2-week WC sprint. If any slip → delay or skip WC, keep NFL prep on schedule regardless.

11. **World Cup opportunistic product (2-week sprint, June 1-8 target if gates cleared)** — Light-touch model, NOT full soccer pipeline. Scope:
   - Data: clubelo.com Elo scrape + fbref xG (last 10 internationals per team) + FIFA rankings
   - Model: Elo delta → implied win prob vs market, summed xG for O/U 2.5 goals, BTTS flag
   - Picks: Match Lean, Goal Total, BTTS, Dawg of the Tournament (biggest Elo-vs-market disagreement)
   - UI: Jerry sectioned reads mirroring MLB/NBA pattern
   - Post-tournament (July 19+): archive unless engagement justifies EPL/Champions League extension

12. **NFL pipeline build (August 1-September 4) — THE SUMMER MOUNTAIN** — Highest-value US sport, 5 months active engagement, winter subscription anchor. Depth equivalent to MLB pipeline:
   - Team DVOA (offensive + defensive)
   - QB efficiency (Pass+, EPA/play, deep ball rate)
   - Pace of play, red zone efficiency
   - Weather (wind, temp, precip — huge in NFL)
   - Injury reports + practice participation
   - Situational ATS trends (divisional, primetime, rest advantage, travel)
   - Line movement + sharp money indicators
   Start August 1 latest. September 4 launch = Week 1 hype retention.

13. **NCAAF soft launch (August 23)** — If NFL pipeline ready early, layer NCAAF on same architecture. Signals: SP+/FEI ratings, recruiting class quality, returning production, turnover margin, pace. NCAAF is messier (garbage time, huge variance) so model conviction thresholds need softening vs NFL.

14. **NCAAB retrospective data pull (August, ~1 week)** — Current NCAAB model (KenPom fanmatch + four factors) got a decent sample. Retro-pull 2023-24 and 2024-25 seasons triples training corpus. Low risk, free layup. Aim to push spread hit rate from ~55% → 57-58%. Execute during slow August weeks before NFL launch ramps.

15a. **NBA pipeline build for v2 (offseason July-Sep)** — Mirror MLB-style depth.
    - **Cancel BDL subscription mid-June** when playoffs end (save $); restart Oct
    - **August audit:** can NBA Stats API (free) replace BDL for what we need? Likely yes
    - **Add features missing from current NBA:** back-to-back game flag, rest mismatch, travel/altitude, pace mismatch, lineup confirmations, L10 net rating, 4-factor metrics, opponent-adjusted ratings vs top/bottom defenses
    - **NBA pipeline props (~5-7 day build, August)** — same multi-signal architecture as MLB props. Player props (PRA, points, rebounds, assists, threes) are higher volume than ML/spread/totals — biggest retail differentiator. Signals: player form (L5 averages by stat), opp defense vs position, minutes projection, pace mismatch, B2B fatigue, injury impact (next-man-up minutes)
    - **Build nba_game_results table NOW (4/25)** so we have validation data by August (started — see nba_pick_logger.py + SQL migration)
    - **Ship NBA v2 for season open Oct ~21**

15b. **NBA validation data collection (started 4/25)** — Built nba_pick_logger.py to capture per-game features + market lines daily. Resolver fetches BDL boxscores. Goal: 2-3 weeks of resolved playoff data + early offseason simulations to audit which NBA features actually predict outcomes before committing to PRIME-tier elevations or NBA-specific score floors.

16. **Prop signal retraining (Month 2-3 post-launch)** — Data-drive the conviction formula in generate_props.py. Phased approach:
    - **Week 3 (~Mid-May):** SQL aggregation audit — hit rate per signal key from jsonb_each_text(signals). Surfaces which signals are predictive (elite xera, k_gap) vs noise (streak, ump). No model, just reports. Shows which hardcoded bumps are over/under-weighted.
    - **Week 6 (~Early June):** First retrain with ~1,000 resolved picks. Logistic regression on signal features → Win/Loss outcome. Output coefficient weights to model_config table (ties to docket #3 remote config). generate_props.py reads weights from DB instead of hardcoded bumps.
    - **Monthly cadence thereafter:** Re-run training on trailing 60-90 days. Conviction formula self-improves every 30 days without app builds.
    - **Post-launch differentiator:** 'Our model retrains monthly on verified results' is premium brand claim no EV scanner can match. Same architecture extends to NBA/NFL/NCAAF props when those launch.
    Budget: ~2hr SQL audit Week 3, ~6-8hr model build Month 2, ~2hr/month ongoing.

**Why:** UFC and umpire signals exist in the pipeline but haven't been verified as actively improving outputs. Remote config is infrastructure that pays off every time an audit reveals a threshold needs tuning. HR Watch + Jerry tab are user-facing marquee features that need to look and feel premium before launch. Prop Jerry entity confusion specifically erodes trust fast — a sharp bettor spots that bug immediately.

**How to apply:** When user brings up UFC, prop grading, umpire, HR Watch, Jerry tab, Fades, Daily Degen, Jerry record, or threshold tuning, revisit the relevant item.
