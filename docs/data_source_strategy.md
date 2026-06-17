# Data Source Strategy

**Status:** v1.0 / 2026-06-16
**Author:** ag + claude
**Related:** [season_calendar.py](../mlb_pipeline/season_calendar.py), [project_nba_offseason_rebuild](memory link)

---

## 1. Strategic Principle

**Goal: a 5-sport proprietary advanced model on the cheapest defensible data stack.**

Most competitor apps fall into two failure modes:
1. **Pay for everything** — burns COGS, can't price competitively at consumer tier
2. **Free-everything-shallow** — surface-level stats, no edge over what users get elsewhere

Our position: **use the same raw feeds the actual quant shops use**, then differentiate on the engine layer (cohort framework, tier discipline, attribution validation, sweat scoring). Raw data is a commodity; the analytical layer is the moat.

**Target steady-state data COGS:** ~$30-300/month (just Odds API + optional NBA pro-tier).

---

## 2. Per-Sport Source Matrix

| Sport  | Source(s)                          | Cost      | Notes                                              | Build Status |
|--------|------------------------------------|-----------|----------------------------------------------------|--------------|
| MLB    | MLB Stats API + Savant + pybaseball | $0       | Best-in-class free data in all of sports          | ✅ Live      |
| NBA    | `nba_api` (offseason migration)    | $0       | Currently BDL ($30-60/mo) — porting offseason     | 🔧 Migrating |
| NFL    | nfl_data_py (nflverse)             | $0       | Equivalent depth to MLB's Savant                  | 🔧 Phase 1 done |
| NCAAB  | **KenPom** (kept) + BartTorvik backup | $25/yr  | Internal use only — never name in app             | ⏸ Nov 2026  |
| NCAAF  | cfbd-api (CollegeFootballData)     | $0       | Free tier sufficient; equivalent to nfl_data_py   | ❌ Queued Aug |
| NHL    | NHL Stats API + MoneyPuck          | $0       | Official free endpoint — no need to pay anyone     | ❌ Queued    |
| UFC    | UFC Stats scrape                   | $0       | Brittle scraper — gold-standard data is free       | 🔧 Live (broke 6/6) |
| Odds API | The Odds API                     | ~$30-300/mo | Universal paid dependency — no free alternative | ✅ Live      |

---

## 3. Sport-by-Sport Detail

### 3.1 MLB ✅ (current stack is best-in-class)

**Live sources:**
- **MLB Stats API** (statsapi.mlb.com): schedules, scores, boxscores, lineups, plays. Official, free, no rate-limit pain.
- **Baseball Savant** (baseballsavant.mlb.com): pitch-level Statcast — exit velo, barrel%, xBA, xwOBA, spin rate, sprint speed. **The gold standard.** Official MLB data, downloadable CSVs, free.
- **pybaseball**: Python wrapper for Savant + FanGraphs leaderboards + Baseball Reference scrapes.
- **FanGraphs**: leaderboard pages scrapeable; their paid API exists but pyfangraphs covers what we need free.

**Gaps:** none material for our use case.

**Verdict:** MLB has the deepest free data of any sport. No upgrade path needed.

---

### 3.2 NBA 🔧 (offseason BDL → nba_api migration)

**Current: BDL (Ball Don't Lie API) — $30-60/month**
- ✅ Team season averages (advanced, defense, tracking)
- ✅ Player stats by date
- ✅ Playoff series
- ✅ Clean REST API, good docs
- ❌ Less granular than the official NBA Stats API
- ❌ Injuries weak (we already supplement)

**Target: `nba_api` Python package — FREE**
- Wraps the unofficial NBA Stats API (stats.nba.com) — same data the league publishes
- Has: lineup combos, shot zones by player, defense matchup data, hustle stats, player tracking, on/off splits, possessions, dribble counts, touch counts — substantially richer than BDL
- ❌ Unofficial endpoint — rate limit handling required, schema can break
- ❌ Same injury gap as BDL (need separate scrape)

**Alternative tiers if model demands more:**
- **PBP Stats** (pbpstats.com) — free play-by-play derived stats, advanced lineup data
- **Basketball Reference scrape** — free, TOS gray area
- **Cleaning the Glass** ($40/mo) — garbage-time-stripped advanced stats used by NBA front offices. Worth considering for v2.0 if revenue supports it.
- **Synergy** — pro-tier, expensive

**Injuries (both BDL and nba_api need supplement):**
- ESPN injury page scrape
- Rotowire feed
- NBA.com injury report PDF (daily)

**Migration plan:** BDL paused 2026-06-16 (sub cancelled, season ended). Rebuild on `nba_api` during NBA offseason workstream (Jul-Sep 2026). Re-enable BDL only if `nba_api` proves unreliable in build.

---

### 3.3 NFL 🔧 (Phase 1 done, Phase 2 August)

**Live:**
- **nfl_data_py** (nflverse ecosystem): play-by-play, EPA, CPOE (completion % over expected), expected metrics, snap counts, weekly stats, schedules, rosters, win probability. **Equivalent to MLB Savant in depth.**
- **Pro Football Reference**: scrapeable for historical splits

**Free supplemental:**
- **Next Gen Stats**: NFL.com publishes select metrics weekly free (separation, time to throw, etc.)
- **nflverse data pipelines**: include some PFF-light grades

**Premium (NOT planning to pay):**
- **PFF (Pro Football Focus)** — alignment data, snap grades. $800+/yr for premium API. Nice but not necessary for our cohort framework.

**Verdict:** nfl_data_py covers ~95% of what advanced models need. No paid layer required for v1.0/v2.0.

---

### 3.4 NCAAB ⏸ (KEEP KenPom, internal use only)

**Current intent: KenPom subscription ($25/yr) — KEPT**

**Why keep it:**
- KenPom is the gold standard for college basketball: adjusted efficiency, four factors, tempo, luck, schedule strength, returning experience
- Best-known and most-trusted ratings in the sport
- Deep historical data + daily refresh

**How we use it (internal only — see [feedback_no_kenpom_attribution](memory link)):**
- ✅ Pull KenPom raw efficiency + four factors as model input features
- ✅ Build our own cohort engine on top (matchup-vs-efficiency-gap rules, tempo cohorts, etc.)
- ✅ Branding: "proprietary efficiency model" — never mention KenPom by name in app, marketing, social
- ❌ Never display KenPom rankings as-is; transform them through our model layer first

**Free backup if KenPom access ever breaks:**
- **BartTorvik** (barttorvik.com) — comparable model quality, free, has an API
- **T-Rank** (similar concept, free)
- Quality is comparable to KenPom; we just pay $25 for the brand recognition + convenience

**Build status:** Pull script failing on missing env var (see [project_kenpom_pull_no_api_key](memory link)). Low priority until Nov 2026 NCAAB season.

---

### 3.5 NCAAF ❌ (queued Aug 2026)

**Target: cfbd-api (CollegeFootballData.com) — $0**
- Free tier covers: plays, EPA, win probability, returning production, talent composite, recruiting, advanced box scores
- **The cfbd-api is to NCAAF what nfl_data_py is to NFL.** Same data philosophy, built by the same kind of stats community.
- Generous free tier; paid tier only needed for very high request volumes

**Free supplemental:**
- **cfbfastR** (R ecosystem) — equivalent to nflfastR for college
- **SP+ ratings** (Bill Connelly on ESPN) — free, comparable to KenPom-for-CFB
- **Football Outsiders** — some free CFB data

**Scope decision:** v1.0 ships **Spread / ML / Total only** — no player props. (Same rationale as NCAAB — see [project_ncaaf_scope](memory link).)

---

### 3.6 NHL ❌ (queued — add to docket)

**Target: NHL Stats API (statsapi.web.nhl.com) — $0**
- Official NHL endpoint, free, no auth required
- Provides: schedules, scores, boxscores, lineups, play-by-play, shot data, goalie stats, advanced situational stats
- Same shape as MLB Stats API

**Free supplemental:**
- **MoneyPuck.com** — free expected goals (xG), high-danger chances, lineup data. Used by serious hockey analytics shops.
- **Natural Stat Trick** — free advanced stats site, scrapeable
- **Hockey Reference** — free historical scrape

**Verdict:** No paid layer needed. NHL has surprisingly good free data infrastructure.

**Scope (initial):** Spread (puck line), ML, Total, plus goalie save% / shutout props (Vegas books carry these). Player props (shots on goal, points) viable Phase 2 if data quality holds.

---

### 3.7 UFC 🔧 (live, scraper brittle)

**Source: UFC Stats scrape (ufcstats.com) — $0**
- Official UFC statistics, scrapeable
- Provides: fighter records, strike accuracy, takedown defense, fight outcomes by method

**Status:** Scraper broke 2026-06-06 (see [project_v4_blackout_606](memory link)). Needs repair when UFC content resumes priority.

**No paid alternative considered.** UFC data is niche enough that free is the right move.

---

### 3.8 The Odds API (universal paid dependency)

**Cost:** ~$30-300/month depending on tier (call volume)

**No free alternative exists.** Odds API is the universal price for sports betting market data. Every competitor pays this same line item.

**Mitigation:** Don't burn calls. Cache aggressively. Don't poll markets that aren't moving.

---

## 4. Migration Tracker

| Migration                         | Trigger   | Target Date | Status |
|-----------------------------------|-----------|-------------|--------|
| NBA: BDL → `nba_api`              | Offseason | Jul-Sep 2026 | 📋 Queued |
| NCAAB: KenPom env var fix         | Pre-season | Oct 2026   | 📋 Queued |
| NCAAF: cfbd-api integration       | Pre-season | Aug 2026   | 📋 Queued |
| NHL: NHL Stats API integration    | Pre-season | Sep 2026   | 📋 Queued |
| UFC: scraper repair               | When prioritized | TBD  | 📋 Queued |

---

## 5. Decision Heuristics for Future Sources

When evaluating any new data source ask:

1. **Is there a credible free equivalent?** Default to free unless it gives a material model edge worth the spend.
2. **Does the source have an official endpoint?** Official > unofficial > scrape. Unofficial endpoints + scrapes are tech debt.
3. **Is the data already in nflverse/Savant/cfbd/NHL Stats?** If yes, don't pay twice.
4. **Is the paid data going INTO the model, or just displayed?** If just displayed, you're paying for branding — usually not worth it (see KenPom exception for internal-only use).
5. **What's the break risk?** Brittle scrapes have hidden ongoing cost (engineering hours fixing them).

---

## 6. What we will NOT pay for (currently)

- PFF NFL grades — nflverse covers our needs
- Synergy — for either NBA or NCAAB; over-spec'd for v1.0
- Cleaning the Glass — strong consideration for v2.0 if revenue supports
- Multiple Odds API tiers — one tier, cache hard
- Anything that just gives us "rankings" without raw data (we build rankings)

---

## 7. Appendix: Cost Snapshot (steady state)

| Line Item                | Monthly | Annual    |
|--------------------------|---------|-----------|
| Odds API                 | ~$50    | $600      |
| KenPom (NCAAB)           | ~$2     | $25       |
| BDL (NBA, paused)        | $0      | $0 (was ~$540/yr) |
| **TOTAL data COGS**      | **~$52**| **~$625** |

Compare to a "pay-for-everything" build: $200-400/month easily ($2.5-5k/yr) once PFF, Synergy, Cleaning the Glass, and other premium tiers stack up. Our path saves $2-4k/yr without sacrificing model quality.
