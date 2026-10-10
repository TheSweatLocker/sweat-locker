# NCAAB Historical Closing-Odds Source Hunt — 2026-08-20

Read-only investigation. No pages scraped beyond quick 200-response probes,
no DB writes, no backfill code. Recommendation-only, three sources probed
plus a survey of alternatives.

**TL;DR** — The Odds API is the clean winner for the ~57% of `ncaab_game_results`
rows that fall on or after **2020-11-16** (that's the vendor's earliest cursor,
confirmed by probe). Pre-2020 has no clean paid or free per-game source; either
accept a Kaggle-dataset merge with expected coverage gaps, or lean on
signal-registry self-heal.

---

## 1. Efficiency-data-source paid API — CONFIRMED no odds

Base URL is `https://kenpom.com/api.php?endpoint=X&y=YYYY` with
`Authorization: Bearer $KENPOM_KEY` (this session re-probed and matches the
earlier `_ncaab_odds_backfill_report_2026-08-20.md`).

| endpoint                                         | status  | contains odds?                    |
|--------------------------------------------------|---------|-----------------------------------|
| `ratings`                                        | **200** | no (rating cols only)             |
| `four-factors`                                   | **200** | no                                |
| `fanmatch` (`d=YYYY-MM-DD`)                      | **200** | **no** — dumped full schema: `Season, GameID, DateOfGame, Visitor, Home, HomeRank, VisitorRank, HomePred, VisitorPred, HomeWP, PredTempo, ThrillScore`. Zero Vegas / spread / total / ML fields. |
| `games`, `schedule`, `gamelog`, `teamgames`, `history`, `odds`, `vegas`, `lines`, `spreads`, `predictions`, `gameplan`, `summary`, `team`, `team-games`, `players`, `top-25`, `kpoy`, `coach` | **404** | endpoint doesn't exist            |

**Verdict:** paid API cannot supply closing lines under any current or
plausibly-hidden endpoint name. Per `feedback_no_kenpom_attribution.md`, keep
vendor name out of user-facing copy either way.

The previously-scaffolded `_ncaab_kenpom_odds_backfill_2026-08-20.py` route
(session-cookie scrape of `team.php`) still theoretically works but requires
adding site email/password to `.env` and hasn't been verified to actually
surface odds columns at the operator's subscription tier — leaving it as a
distant fallback behind The Odds API path recommended below.

---

## 2. teamrankings.com — CURRENT SEASON ONLY, historical archive is aggregate

**Structural verdict:** per-game closing spread / total / ML **do exist** on
`teamrankings.com/ncaa-basketball/team/{slug}` and the sibling `/ats-results`
+ `/over-under-results` sub-pages (columns: `Date | Opponent | ... | Spread |
Total | Money`). No login gate, robots.txt allows `/` with `Crawl-delay: 10`.

**Fatal for backfill:** every historical URL pattern I probed
(`?season_id=13`, `?season=2023-24`, `/2010-11`, `/history/`) **301-redirects
to the current-season page**. TR does not expose historical per-team schedules
to unauthenticated visitors. The team-page odds tables are for **the current
season only**.

Meanwhile the `odds-history/*` pages **do** span 2005+ but only as aggregates
(`Closing Spread bucket → cover% + record`, ~10-30k games per bucket) — great
for cohort calibration, not per-game backfill.

The existing `pull_ncaab_teamrankings_trends.py` (currently used for team
season ATS/OU) confirms this: it grabs `range=yearly_YYYY_YYYY` season totals,
never per-game lines. `/ncb/odds/?date=2024-02-15` returned "No matching games
for this category" — the odds view is today-only.

**Verdict:** teamrankings is not viable for the 5,911-game backfill. Keep it
as the source for team-season trend enrichment (already wired) and — new
option — for **live capture starting this season** by hitting `/ats-results` +
`/over-under-results` weekly during 2026-27.

---

## 3. Alternatives ranked

| Path | Cost | Coverage | Effort | Verdict |
|---|---|---|---|---|
| **The Odds API historical** — key already in `.env`, live-probed this session | $0 marginal (60,589 credits remaining, 30/call) | **~3,400 of 5,911 games** (Nov 2020 - Apr 2025) | **2-4 hours** | **PICK THIS** |
| Kaggle CBB lines dumps ("College Basketball Games" Sundberg 2013-22; "NCAAB Scores + Betting Lines 2003-23") | $0 | 60-90% of pre-2020 games with quality caveats | 1-2 days (team-name normalization is real work) | **Optional pre-2020 fill** |
| SBRodds / SportsBookReviewOnline archive | $0 | Would be great — but SBR closed to public in 2020 | dead | Skip |
| OddsShark historical | $0 | Only current season on public pages | Skip | Skip |
| SportsDataIO NCAA basketball historical odds add-on | ~$500-2,000/mo | Full | 1-2 days | Overpay; not worth vs Odds API + Kaggle |
| Efficiency-data-source `team.php` scrape (already scaffolded) | $0 | Unverified — may lack odds at current tier | 4 hours (creds + verify + run) | Fallback if Odds API path stalls |

**Odds API probe results (this session):**
- `/v4/historical/sports/basketball_ncaab/odds` returns 200 with real bookmaker
  spreads/totals/H2H at 2024-02-15, 2020-11-20, 2020-11-16 (first day with
  data). 2019-11-15 returns `next_timestamp=2020-11-16T09:15:00Z` (empty),
  confirming **2020-11-16 is the archive floor**.
- Cost: **30 credits per `/odds` call**, 1 credit per `/events` call. Current
  quota shows 60,589 remaining.
- Sample body includes FanDuel + LowVig + others with h2h/spreads/totals and
  precise timestamps at 5-min granularity.
- One `/odds` call at commence_time returns all games at that snapshot, so
  ~1-2 calls/day × ~150 game-days/season × 5 seasons ≈ **~1,500 calls × 30 =
  ~45k credits** (~75% of remaining quota, doable in one monthly window; use
  `previous_timestamp` chain to pin true closing snapshot per game).

---

## 4. Recommendation

**Do this:**
1. **Write** `mlb_pipeline/backfill_ncaab_closing_odds_oddsapi.py` — iterate
   `/v4/historical/sports/basketball_ncaab/events` per date 2020-11-16 →
   2025-04-08 (1 credit each), then for each date with events fetch
   `/v4/historical/sports/basketball_ncaab/odds` at a timestamp ~5 min before
   the last commence_time of the day (30 credits each). Land in the same
   `ncaab_historical_closing_odds` table the KenPom scaffold's migration
   creates, `source='the_odds_api'`.
2. Upsert into `ncaab_game_results.close_spread / close_total / close_home_ml /
   close_away_ml` by matching `(game_date, home_team, away_team)` via
   `ncaab_team_aliases`.
3. Accept the pre-2020 gap. Re-run `ncaab_cohort_backfill.py` and the 45-signal
   registry validator on the 57% now populated — plenty of sample to move
   spread/total signals off the `w=0.30` unvalidated floor.
4. **Layer a second pass later** (post-launch) with a Kaggle dump for
   2013-2020 if signal owners still want more sample; expect 1-2 days of
   team-name reconciliation.

**Coverage math:** ~3,400 of 5,911 games ≈ **57%** validated pre-launch, and
those are the modern-era games (post-Wayfair sportsbook explosion) that best
match live 2026-27 line behavior anyway. The pre-2020 tail has systematically
different market structure and its absence is arguably a feature.

**Effort:** ~2-4 hours vs the scaffolded KenPom scrape's ~5-6 hours (script +
verify + backfill runtime), no site credentials in `.env`, no scraping-etiquette
concerns, no unverified-tier risk.

**Fallback if this stalls:** the readiness report's "Scenario B" — ship Nov 3
with `w=0.30` on spread/total signals and let `signal_registry` self-heal from
live 2026-27 fires. By mid-January 2027 ~2,000 games are banked and calibration
converges. Nothing is broken by skipping the historical backfill entirely.
