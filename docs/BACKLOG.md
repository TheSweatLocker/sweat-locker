# BACKLOG — living

**Last verified: 2026-09-22 (night)**

Single source of open work. Rules that keep it from rotting like
`hardcoded_percent_audit.md` did (written 06-18, every line number
stale by the time it was reopened):

1. **No line numbers.** They move. Reference files, functions, table
   names — things that survive a refactor.
2. **Every item carries a VERIFY command.** The doc should be
   re-checkable in minutes rather than trusted. If an item can't be
   verified by running something, say so explicitly.
3. **Nothing is "done" without a commit SHA.**
4. **Re-verify the whole file before quoting it.** Items go stale
   silently; the verify commands are how you find out.

---

## P0 — user-facing or money

### B1 · Client API keys bundled in the app binary
`EXPO_PUBLIC_KENPOM_KEY` and `EXPO_PUBLIC_BDL_API_KEY` are compiled into
the IPA and extractable. `ODDS_API_KEY` and `ANTHROPIC_API_KEY` were
already moved behind Supabase Edge Function proxies for exactly this
reason; these two were left behind.

- KenPom: **not currently being called** — client checks local cache →
  `kenpom_cache` (20h gate) → only then the API. Cache is fresh and
  refreshed by `ncaab_pipeline.yml`. Risk is key exposure + a thundering
  herd if that workflow ever fails past 20h.
- balldontlie: **3 call sites with no cache at all** — `fetchPropHistory`
  (user taps a prop), NBA game logs (user opens a team), and
  `fetchPlayerStats` on load. These do hit the account per-user. Quiet
  now because NBA is preseason; live 10-21.

VERIFY: `grep -n "EXPO_PUBLIC_KENPOM_KEY\|EXPO_PUBLIC_BDL_API_KEY" app/index.tsx`
FIX: proxy both, or read from DB — KenPom data is already in
`ncaab_team_efficiency` (857 rows).

### B2 · `fetchPlayerStats` hardcoded to a dead season
Requests `season: 2025` and eight literal `player_ids`. Wrong season and
an arbitrary player set.
VERIFY: `grep -n "season:2025\|player_ids:\[" app/index.tsx`

### B3 · Prop tier contaminated 2026-09-03 → 2026-09-22
`mlb_pipeline_props.tier` in that window was set by the LR override,
whose features contained the outcome. Records built on it are
overstated. **Per Andy 09-22: do NOT change displayed records.** Carried
for awareness, not action.
VERIFY: split PRIME props at 09-03 — before 489-362 (57.5%), after
450-94 (82.7%).
NOTE: an uncommitted exclusion edit to `compute_surface_records.py` was
made and then abandoned on instruction. Do not apply without a decision.

### B4 · Prop LR model trained on leaked features
`models/mlb_prop_logreg.json` learned from L5/L10 that included the game
being predicted. Tier authority is revoked (`MLB_PROP_LR_TIER` defaults
off) but the model still needs retraining on leak-free history before it
can be trusted again.

### B5 · Client build required to ship 86 restored columns
All column fixes are pushed but **no `expo-updates` exists** — no OTA.
Users on 1.0.1 keep seeing blank projections / analysis until an EAS
build + App Store submission.
VERIFY: `grep -n "expo-updates" package.json` (returns nothing)

---

## P1 — correctness

### B6 · Doubleheader — code fixed, one stale row remains
Ordinal pairing in `game_context.match_probable_pitcher` took TWO passes.
First pass paired by ordinal but built the sibling list with no date
constraint, so on 09-22 evening — with game 1 finished and dropped from
the feed, and TOMORROW's same-matchup game still listed — it counted two
events against two MLB candidates, fired, and handed game 2's row game
1's starters. Siblings are now scoped to the same ET date (`5a079e2c`).

STILL OPEN: context row `394e1e2b` (Rays/Yankees game 1, Final) holds
game 2's starters. Its odds event no longer exists, so nothing will
correct it. Rewriting a completed game's row is the B12 mutation
pattern — needs a decision, not a silent patch.
ALSO UNKNOWN: whether the Games list collapses a DH into one card, which
would make leg 2 invisible to users.
VERIFY: `mlb_game_context` on a DH date — compare `away_pitcher`/
`home_pitcher` across the two rows; they must differ.

### B7 · Hardcoded percentages — CLOSED for user-facing strings
`play_of_day.py` 20/23 (`034adc4b`), `game_context.py` all 4 user-facing
`sub` claims (`0e199a36`). Resolver extracted to `cohort_evidence.py` so
`play_of_day` and `game_context` share one implementation rather than a
copy — that duplication is what made the `align_row` bug survive a fix.

Two of the four were below the project's own floor: "wins 90% (n=10)"
and "hits 43% n=7", printed on the card with the sample size visible.

REMAINING (low priority, not user-facing): 6 `audit_note` strings in
`game_context.py`, 2 `print()` debug lines, 3 non-claims in
`play_of_day.py` (a threshold description and the v3_tot constants).
`generate_mlb_game_reads.py` shows 4 hits but all are comments about an
already-fixed bug — nothing to do.
VERIFY: `grep -nE "['\"][^'\"]*[0-9]{1,3}(\.[0-9]+)?\s?%" mlb_pipeline/game_context.py | grep -v "^\s*#"`

### B8 · Admin note — placement and intermittency
Two overlapping systems: `sport_registry` (`state_message`,
`today_note`, `tomorrow_note` — per sport, renders in-tab, this is the
one Andy wants) and `admin_notice` (app-wide gold banner, truncates).
Open: notes don't appear every time — trigger or client fetch is
unreliable, cause unknown. Wanted placement is below the
Time/Conviction/Best-Edge/Prime/Strong filters, above the game list.
MLB currently has a `tomorrow_note` but **no `today_note`**.

### B9 · UFC tab shows non-UFC promotions — backend done
Odds API has one MMA key and stamps every promotion `sport_title: "MMA"`
(verified: 42 events, Yoel Romero vs Darren Till in the same feed as UFC
fights). No field to filter on.

DONE (`fd1a6d3d`): `ufc_card_scraper_v3` now stores the forward slate
instead of `events[0]` — 1 stale event -> 8 forward events, 456 named
fighters. ESPN's /mma/ufc/ endpoint is the only authoritative roster.

REMAINING: the client-side filter itself — match odds events against
fighters from `ufc_upcoming_event` where `event_date >= today`.
BUILD-GATED (client change).

### B10 · NFL props have no projection
`projection` populated on **2 of 1423** rows; `consensus` and
`consensus_delta` on **0**. Tier is built from L5/L10 hit trends +
opponent rank + `edge_pct`, with no projected stat value — so there is
no real edge to compute against the line. NFL is clean of the MLB leak
(reads a stored pre-game array, not a live fetch).
Fastest win: `rush_yds_over` is 20-29 (40.8%, **-23.1%/bet**) on n=49 —
ban it. `pass_completions_under` and `pass_attempts_over` also negative
but thin (n=18, n=13) — shadow rather than ban.
NOTE: a proper deep dive has NOT been done. Family ROI only.

---

### B19 · External picks + track records missing for 4 sports
Verified 09-22 (last 7 days):

    MLB    1065 picks · 12 sources · 139 record rows
    NFL     483 picks ·  7 sources ·  80 record rows
    NCAAF   544 picks ·  6 sources ·  72 record rows
    UFC       0       ·  0         ·   0   <-- IN SEASON
    NBA       0 / NHL 0 / NCAAB 0          <-- preseason, expected

**UFC is the live gap** — it is in season, `pull_externals_ufc.py` is in
`ufc_pipeline.yml`, and it has produced nothing. Needs root-causing, not
just a re-run.
NBA (10-21), NHL (10-08), NCAAB (11-03) need theirs working BEFORE their
openers, not after — MLB's externals took weeks to reach 12 sources.
VERIFY: count `external_picks` and `external_source_track_record` by
sport over a 7-day window.

### B20 · Recent-schedule table is unreadable on two axes
From Andy's 09-22 screenshots, `team_recent_games` rendering in
GameDetailV2:

1. **No year.** H2H is cross-season by design, so rows read
   `5/30, 5/29, 5/28, 9/2, 9/1` and look mis-sorted. They are NOT — the
   May rows are 2026 and the September rows are 2025, correctly ordered
   newest-first. The display just omits the year.
2. **No perspective.** `W 9-4` and `-1.5` do not say whose win or whose
   spread. The data is there — the matview has `score_us`, `score_them`,
   `won`, `is_home`, `spread_line`, `spread_result` — it simply is not
   surfaced. Andy: "who does a user tell from that which side the spread
   was on and who won?"

BUILD-GATED (client change).

### B22 · Models blank when a starter is unannounced
SD @ LAD 09-22 showed PANEL / V4 / MC as "—" while ARI @ COL showed all
five. Not a pipeline fault: `home_pitcher` is NULL because MLB had not
announced the Dodgers starter, and those three models need both arms.
Jerry and V3 run without.

Open question is presentation, not computation: a dash reads as "our
model is broken" when the truth is "the starter is not announced yet".
Worth saying which, since it resolves itself hours later.
VERIFY: compare a game with a NULL starter against one with both, on
panel_implied_*, model_pred_*, mc_probabilities.

### B21 · Screenshot QA pass
Andy is finding these by looking at his own app; nothing else is. Needs
a standing pass over the rendered surfaces per sport — not a code audit,
an actual look at what a user sees. Known open from 09-22 screenshots:
B20 above, and NCAAF SP+/MC showing "—" (already fixed in `d866ba1a`,
waiting on a build — the shipped 1.0.1 select omits
`sp_plus_pred_spread`, `sp_plus_pred_total`, `mc_probabilities` while
the data exists).

### B23 · SEA/NE "1-0" — the only ATS push of the season
**CORRECTION to my first read.** I said all 32 teams showed one game and
blamed the rollup refresh. Andy: "some say 2-0 and are updated." He is
right. The games-tab chip reads `nfl_game_context.*_season_ats_wins/
losses`, NOT `team_situational_records`. There, 30 of 32 Week-3 slots
show two games. Exactly two show one: SEA and NE.

What those two share: **NE @ SEA on 09-09 is the season's only ATS
push** — SEA closed -3 and won by exactly 3. The context table has wins
and losses and no pushes column, and the chip renders `{w}-{l}`, so the
game is not shown as a tie, it is not shown at all. Both teams read
"1-0" when they are 1-0-1.

`team_season_trends` already carries `ats_pushes`, and
`backfill_nfl_season_records_from_results.agg()` has been counting them
correctly all along — the payload just never wrote the number. The
arithmetic was never wrong; there was nowhere to put the answer.

**ORDER MATTERS — do not reorder these:**
1. Apply `supabase/migrations/20260922b_season_ats_pushes.sql` (adds
   `{home,away}_season_ats_{pushes,ou_pushes}` to nfl + ncaaf context).
2. THEN the writers may persist pushes, and the client SELECTs may
   request them.

Both writer edits and both client SELECT edits were written, tested
against the live schema, and **reverted** — a `PATCH` or `SELECT` naming
a column that does not exist returns `42703` and takes the WHOLE query
with it, which would blank NFL and NCAAF outright. That is the same
failure mode as the `0e3819e2` trailing comma. Verified before reverting:
both selects return 400 with the column, 200 without.

The chip render IS shipped — it reads `{w}-{l}-{p}` when pushes > 0 and
is a no-op while the field is absent, so it needs no second edit later.
VERIFY: `select away_team, away_season_ats_wins, away_season_ats_losses,
away_season_ats_pushes from nfl_game_context where game_date >=
'2026-09-24'` — expect NE and SEA at 1-0-1 once step 1 is applied.

### B28 · Rollup refresh functions broken since 09-16 (separate bug)
Real, but NOT what Andy saw — split out of B23 after the correction
above. `20260916a` renamed the situational matview and left the refresh
functions naming the old object, so they return `42809` on every call.
The 12 workflow steps calling them used `curl -s` with no status check
under `continue-on-error`, so a 400 and a 204 were the same event: six
days of frozen rollups across six pipelines, silently. Confirmed
independently — `team_situational_records` still shows SEA at 0-0-1
(one game) when SEA has two.
Fixed in `edb2f380` (migration `20260922a` + all 12 steps now emit
`::error::`). **OPEN — the migration still has to be applied.**
VERIFY: `select public.refresh_team_situational_records();` then count
spread/overall rows per NFL team — expect 2, not 1.

### B24 · NFL tab note exists but does not render
Andy, 09-22: "we should have NFL note." The data is there —
`sport_registry.NFL.today_note` is 143 chars ("Full analysis populates
by Thursday morning ahead of each week's slate..."). NCAAF, MLB and UFC
have one too; NBA/NCAAB/NHL are empty, which is correct (preseason).

So this is the render path, not the data — same family as the earlier
"the note isn't there every time" report. Notes were moved below the
filters in `055ef4a1`, which is **build-gated**: Andy's shipped 1.0.1
does not have that change. Re-check on the next build before doing any
further work here.
VERIFY: `select sport, today_note from sport_registry order by sport`
(confirmed populated for NFL 09-22) — then look at the NFL tab on a
build that includes `055ef4a1`.

### B25 · UFC end-to-end before the Apple push — BUILD GATE
Andy, 09-22: "make it pure UFC, do a full look at the entire process and
data and model performance." Three parts, all open:
- **Pure UFC — half done.** `ufc_upcoming_event` was itself impure: 10
  of 29 rows were Dana White's Contender Series, a tryout show whose
  fighters have no UFC record, so `ufc_fighter_stats` holds nothing for
  them. That table is what answers "is this a UFC fight" for the client
  filter, so the filter would have admitted exactly what Andy asked to
  exclude. `is_ufc_proper()` now gates the scraper and the 10 rows are
  purged — 19 events, 0 non-UFC, verified after a live run. The client
  filter itself (B9) is still open and BUILD-GATED.
- **Model performance — picks to shadow, and my first numbers were
  wrong.** I reported 25-38 / **-17.1% ROI**. The W-L is right; the ROI
  was computed from mismatched columns — `pick_result` grades
  `ev_recommended_side`, and I priced it with `recommended_side`. Those
  are two different picks that disagree on 22 of 63 fights (on every
  one, the model takes the favourite and EV takes the dog). I also
  wrongly suspected the grader; it is 63/63 correct.

  Recomputed consistently, from `ufc_fight_results` as the authority
  (`winner_actual` verified 90/90 against it):

      model side (recommended_side)   36-30  54.5%   +1.20u   +1.8%
      published EV side               25-38  39.7%   +7.34u  +11.7%
        of which PUBLISHABLE P/S/L    10-9   52.6%   -0.54u   -2.9%  n=19
        of which SKIP (not shown)     15-29  34.1%   +7.89u  +17.9%  n=44

  Every unit of profit is in the bucket the engine says to skip, and
  all of it is 29 longshots (3.00+) returning +14.11u inside a ±19.4u
  noise band. What users see is 19 picks at -2.9%.

  The model itself loses to the closing line outright — 54.5% accuracy
  vs the devigged line's 69.7%, worse log loss (0.661 vs 0.607) and
  Brier (0.238 vs 0.208), and worse at EVERY confidence band; at 0.80+
  it merely ties the line. **Ship the tab, the fight data and the card;
  hold the picks.** Not because they lose, but because n=19 on a leaking
  model is not evidence.

### B29 · UFC feature leak — career stats are not as-of-date
`ufc_features.build_features` calls `fetch_fighter_career_stats`, which
reads one CURRENT row from `ufc_fighter_stats` with no date filter. Every
striking/grappling feature (`slpm`, `sapm`, `str_acc`, `td_avg`,
`td_acc`, `td_def`, `sub_avg`) is the fighter's career-to-today figure
used to predict fights from years earlier. Only `compute_pre_fight_record`
is correctly date-scoped.

**5 of the 8 highest-gain features in ufc_v2_winner are on the leaked
side.** The model's own metadata shows the cost: train 83.5% / val 77.6%
/ test 64.3% accuracy, log loss 0.441 → 0.527 → 0.624 — and the test
number is itself inflated, since test fights use current stats too.

Same defect class as the MLB prop lookback leak (`20b443cc`). Second
sport, same shape: a convenience fetch that returns current state.
VERIFY: `rg -n "fetch_fighter_career_stats" mlb_pipeline/ufc_features.py`
— no `fight_date` argument anywhere in it.
FIX: snapshot fighter stats per fight date, or rebuild the career rates
from `ufc_fighter_history` rows strictly before the fight. Retrain after.

> **⚠ 2026-09-22 LATE — B30 / B33 / B34 / B35 BELOW ARE WRONG IN PART.**
> I tested the wrong population. Every "props have no edge" conclusion
> was computed across ALL tiers and ALL odds. The product publishes
> **PRIME/STRONG only, conviction != 0, banned families removed, odds
> inside [-300, +150]** (`compute_surface_records.pick_prop`). That is
> 2,040 graded rows, not 26,040 — I was measuring something Andy does
> not sell. I also split the history at 09-03 only, which averaged the
> losing pre-adjustment era together with the winning post-adjustment
> one and buried the signal.
>
> Under the ACTUAL published rules, verified against boxscores:
>
>       before 2026-08-20 (pre-adjustment)   53.3% vs 58.1%   -4.8pp  z=-3.27  n=1112
>       2026-08-20 -> 09-02  POST-ADJ, CLEAN  66.9% vs 57.5%   +9.4pp  z=+2.18  n=130
>       2026-09-03 -> 09-22  LEAK WINDOW      77.3% vs 55.6%  +21.8pp  z=+12.37 n=798
>
> **Andy's prop-pipeline adjustments around 08-20 worked.** The clean
> post-adjustment window is genuinely positive and ZERO of its 130
> grades are disputed by the boxscore cross-check. Read B36 for the
> corrected position; treat the vig analysis in B34 as true about the
> market and wrong about the model.

### B30 · Nothing compares a pick to the price it was bet into
The finding behind B25/B29 and the MLB/NFL prop audits. The pipeline
stores the closing price next to every pick and never compares the two,
so a tier can publish for months while hitting below its own implied
rate. Measured 09-22:

      MLB PRIME props, pre-leak   57.6% vs 58.4% implied   -0.9pp  n=580
      MLB PRIME props, in leak    83.4% vs 64.1% implied  +19.3pp  n=1621
      NFL props, all tiers        50.6% vs 54.2% implied   -3.7pp  n=1287
      UFC publishable             52.6%, -2.9% ROI                  n=19

MLB PRIME was at or BELOW the market-implied rate every month from June
to August (-2.3pp, -6.4pp, -13.3pp) and only clears it inside the leak
window. It was never beating the line. NFL tiers are not monotonic —
LEAN (+0.8pp) beats STRONG (-4.1pp) — and a projection exists on 2 of
1317 rows (0.2%), so no edge is being computed at all (see B10).
FIX: make edge-vs-implied the publishing gate, with a sample floor.
Report: https://claude.ai/artifact/CZ9Fqvrw74MjgkbiTaNZRj

### B26 · Markdown leaking into short_read
`Ari@Col` and `Tam@New` short_reads begin `**PITCHERS:**` and one
contains a literal newline. The card renders short_read as plain text,
so users see the asterisks. Cosmetic half of the non-uniformity Andy
reported; the pick/prose half is fixed in `76489d10`.
VERIFY: query MLB `jerry_reads.short_read` for today and grep for `**`.

### B27 · 41 files write to `jerry_reads`
Found while fixing `76489d10`. Several of them re-decide the pick rather
than only writing prose, which is why the same contradiction (CLE@BOS)
recurred three days apart after being fixed — a later writer overwrote
the aligned row. Collapsing the rule into one function fixed the rule;
it did not reduce the number of hands on the table.
VERIFY: `rg -l "rest/v1/jerry_reads" mlb_pipeline/*.py | wc -l`
FIX: classify the 41 into prose-writers vs pick-writers; pick-writers
must go through `enforce_primary_play_alignment` or lose write access.

### B31 - Prop projections: assessed 09-22, plan below
Andy: "do we need to assess and engineer projections for props then."
Assessed. Yes for MLB. For NFL the substrate already exists, but
plugging it in as-is does NOT produce an edge. Evidence first.

**NFL - the projections exist and nothing reads them.**
`nfl_player_projections` holds **7,688 rows** (sleeper + espn_fantasy,
2025 and 2026 wks 1-5, pulled 09-22). `nfl_pipeline_props.projection`
is populated on **2 of 1,317** graded rows.

They predict the STAT well, measured against `nfl_player_stats`:

      proj_rush_attempts  corr 0.870  MAE 1.72   n=1839
      proj_rush_yds       corr 0.770  MAE 10.3   n=1850
      proj_targets        corr 0.491  MAE 1.87   n=2463
      proj_rec_yds        corr 0.468  MAE 18.3   n=2463
      proj_receptions     corr 0.441  MAE 1.44   n=2461
      proj_pass_yds       corr 0.237  MAE 61.9   n=298
      proj_pass_tds       corr 0.131  MAE 0.93   n=295   <- noise

Skill at the stat is not edge at the line. Fitting bias plus a
heteroskedastic residual model on **2025 only**, testing on 2026:

      edge >= 0.10    94-82   53.4%   ROI  -1.0%   n=176
      edge >= 0.15    51-41   55.4%   ROI  +3.1%   n=92
      edge >= 0.20    28-21   57.1%   ROI  +6.6%   n=49

Monotone, but only positive where n is too small to bank. **An
in-sample version of this read +7.1% and was wrong** - the residual
spread had been fit on data including the test weeks. The honest
out-of-sample answer at a usable sample size is break-even.

Why: books price off the same public fantasy projections. Consuming
Sleeper gets us TO the market, not past it. Calibration says the same -
the 0.75+ band predicts 84% and delivers 59%.

**NFL, shippable now (a filter, not a model):** published P/S/L is
434-380, **-1.6% ROI, n=814**. Dropping the `rush_yds` and `pass_yds`
families lifts it to **-0.3%, n=716**. See B10 - `rush_yds_over` alone
is 40.8% / -23.1% on n=71.

**MLB - there is no substrate to project from.**
`player_game_log` is **EMPTY (0 rows)**. No batter-level table exists
anywhere; the mlb_* set is team, pitcher, park, umpire and context only.
Every batter signal is a LIVE MLB Stats API call at generation time,
never persisted - the same root that let the L5/L10 lookback leak
(`20b443cc`), and the reason that leak cannot be re-tested on history.

**Order of work:**
1. Persist a date-stamped `player_game_log` for MLB batters. Nothing on
   the MLB side is buildable or backtestable without it, and it is the
   permanent fix for the leak class.
2. Build our OWN NFL projection instead of consuming a fantasy one -
   usage based: target share x projected team pass volume x pace x
   opponent. A public projection by definition carries no information
   the market lacks.
3. Replace the Normal with the right distribution per family (NegBin for
   counts, Gamma for yards) and add an empirical calibration layer, so
   the edge number can be trusted before anything is gated on it.
4. Gate publishing on calibrated edge (B30), not on tier.
VERIFY: `select count(*) from player_game_log` (expect 0 today);
`select count(*) from nfl_player_projections` (expect ~7.7k).

### B32 - CLV is unmeasurable: we never store the closing price
The most consequential gap found on 09-22, and it explains why a season
of props could run without anyone knowing whether the model was sharp.

      mlb_pipeline_props  close_over_odds:    68 of 26,040 graded
      nfl_pipeline_props  close_over_odds:     0 of  1,317 graded

Closing line value is the one metric that works on a SMALL sample. Hit
rate and ROI need hundreds of settled bets before they say anything;
CLV says it in dozens, because the market is the benchmark and its move
after you bet is the verdict. Without it there is no way to answer "is
this model good" until a whole season has already been bet.

It also means every "vs market implied" figure in the 09-22 audit was
computed against `book_over_odds` - **the price we took**, not the
closing line. The ROI numbers are unaffected (ROI is settled at the
price you would have bet). The framing needs correcting: MLB PRIME was
not beating **the price it was offered**. Whether it beat the CLOSE is
still unknown and unknowable on current data.

WHY SO FEW. `freeze_prop_closing_lines.py` runs every 10 min on
`mlb_prop_close_freeze.yml` (22-23 and 00-04 UTC) and by design
"filters on today's PRIME/STRONG only" as a cost measure. That caps the
universe at ~4,100 graded rows - and it is landing 68 of them, ~1.7%.
So there are two separate failures stacked:
  1. Scope - LEAN / COVERAGE / SKIP can never be measured at all, which
     is exactly the population needed to prove a tier ladder orders
     anything.
  2. Yield - even inside PRIME/STRONG it captures ~1.7%. Unproven
     causes: the cron window misses afternoon starts, the freeze window
     is too narrow, or book name-matching drops rows. The workflow step
     ends in `|| echo "prop close freezer non-fatal"`, so any of those
     fails silently - the bare-mask class again.

NFL has NO freezer at all: 0 rows, and `close_locked_at` never set.

FIX, in order:
  1. Make the freezer's failures audible (drop the bare mask, emit
     ::error:: with counts attempted vs frozen).
  2. Diagnose the 1.7% yield before widening scope - widening a broken
     capture just fails on more rows.
  3. Widen to every published tier, then add NFL.
  4. Report CLV per tier in the morning audit. It is the earliest honest
     signal we can get, and it arrives weeks before ROI does.
VERIFY: `select count(*) from mlb_pipeline_props where close_over_odds
is not null and result is not null` (68 on 09-22).

### B33 - NFL prop edge: volume modelling is not the answer
Tested 09-22, recorded so it is not re-attempted blind.

Built our own usage projection - trailing target share x team target
volume x yards-per-target, all strictly from prior weeks - on 5 seasons
of `nfl_player_stats` (36,881 regular-season rows). Head to head against
Sleeper/ESPN on the same 1,545 player-weeks:

      Sleeper / ESPN     MAE 19.18   corr 0.436
      our usage model    MAE 19.17   corr 0.556
      blend 50/50        MAE 18.60   corr 0.549

Ours ranks players clearly better at equal MAE, and the blend beating
both means the two carry independent information. That is the
precondition for edge.

It does not convert. Fit on 2025, tested on 2026 props:

      usage  edge >= 0.10   164-153  51.7%   ROI  -2.7%   n=317
      usage  edge >= 0.20    81-69   54.0%   ROI  +1.1%   n=150
      blend  edge >= 0.10    95-87   52.2%   ROI  -1.9%   n=182

Break-even at best, and the blend is WORSE than ours alone at every
threshold. Conclusion: NFL prop lines are efficiently priced against
both public projections and a better volume model. A more accurate
stat projection is not where the edge is - do not spend a build there
expecting one.

What is left worth trying, roughly in order of cost:
  - B32 first. Without CLV we cannot evaluate ANY of this quickly.
  - Price shopping: `book_over_odds` is a single book. Best-of-N across
    books is a mechanical edge that needs no model.
  - Information the market is slow on (snap counts, late inactives),
    not information it already has.
  - Stop publishing families that lose: P/S/L is -1.6% (n=814); minus
    rush_yds and pass_yds it is -0.3% (n=716).

### B36 - CORRECTED position on prop edge (supersedes B30/B33/B34/B35)
Tested 09-22 late, against the exact selection `compute_surface_records`
publishes, and cross-checked against boxscores from the new
`mlb_player_game_log`.

      before 2026-08-20 (pre-adjustment)   53.3% vs 58.1%   -4.8pp  ROI  -8.6%  z=-3.27  n=1112
      2026-08-20 -> 09-02  POST-ADJ CLEAN   66.9% vs 57.5%   +9.4pp  ROI +16.8%  z=+2.18  n=130
        PRIME                               69.0% vs 57.1%  +11.9pp  ROI +20.1%  z=+1.83  n=58
        STRONG                              65.3% vs 57.8%   +7.5pp  ROI +14.1%  z=+1.29  n=72
      2026-09-03 -> 09-22  LEAK WINDOW      77.3% vs 55.6%  +21.8pp  ROI +40.3%  z=+12.37 n=798

Three separate facts, previously conflated:
  1. The WINS ARE REAL. 16,507 grades recomputed earlier with zero
     disagreements, and the boxscore cross-check disputes 0 of the 130
     clean-window grades.
  2. The METHOD HAS REAL EDGE. +9.4pp post-adjustment, pre-leak, at
     z=+2.18. Not conclusive on n=130, but credible and clean.
  3. The POSTED NUMBER IS INFLATED. 798 of the epoch's 928 picks sit in
     the leak window where tier selection was partly reading the
     outcome. 77.3% is not a repeatable hit rate. Overstated, not
     fabricated.

The leak is fixed (`20b443cc`) and the game log now makes it
structurally impossible (`mlb_player_game_log` + `mlb_player_form`).
**The next ~130 published props are a clean test of whether +9.4pp
holds.** That is a real question with a real chance of a good answer.

NOTE ON B34: its market analysis stands — MLB props carry 7.5pp of vig
(measured on 25,196 two-sided prices) and break-even needs ~3.8pp. Its
CONCLUSION ("the model has ~zero edge") was drawn from the unpublished
population and is wrong for the published one. A +9.4pp edge clears a
3.8pp toll; that is exactly why the published slice is +16.8% ROI.

### B37 - 208 props graded off a failed stat fetch (MEASURE ONLY)
Found by the `mlb_player_game_log --verify` cross-check. Per Andy's
standing instruction, **nothing was changed** — this is measurement.

      comparable (player, date, family) pairs : 22,643
      agree                                   : 22,407  (98.96%)
      doubleheader/suspended artifacts        :     28  (expected - verify sums legs)
      genuine single-game conflicts           :    208  (0.92%)

160 of the 208 have `final_value = 0` against a boxscore showing real
production — `outs_under` graded 0 for pitchers who recorded 12-21 outs.
That is a failed stat fetch written as `0` instead of left unknown: the
B11 silent-failure class reaching all the way into the record.

Impact if corrected: 100 grades change, 64 Win->Loss and 36 Loss->Win.
On PUBLISHED tiers (PRIME/STRONG) only **18** change, 10 of them
Win->Loss — a net of about -2 picks on the published record, and **zero
of them fall in the clean 08-20..09-02 window**, so B36 is unaffected.

Clustered by date (09-04: 32, 08-31: 23, 07-26: 10), which is the
signature of whole-day resolver failures rather than scattered noise.
FIX (needs an Andy decision first): make the resolver write NULL, never
0, when a stat fetch fails; then decide separately whether to re-grade
history.
VERIFY: `python backfill_mlb_player_game_log.py --verify`

### B38 - The MLB LR model runs on 19% constant features in production
Found during the 09-23 morning audit, chasing a stale-shadow warning.

`_lr_predict_ml` silently substitutes the TRAINING MEDIAN for any
feature the context does not carry:

      v = ctx.get(f)
      if v is None: v = m['imputer_medians'][i]

That is reasonable for an occasional gap. It is not reasonable as the
permanent state, and right now it is the permanent state for a fifth of
the model.

**20 of 107 ML features are never computed anywhere.** Not in
`game_context.py`, not as columns on `mlb_game_context`. Every game,
every day, they are the median:

      sharp_ml_money      sharp_ml_bets      sharp_ml_div
      sharp_ml_pick_home  sharp_total_money  sharp_total_bets
      sharp_total_div     sharp_total_pick_over
      home/away_ats_l10_wins + _losses
      home/away_ou_l10_overs + _unders
      home/away_ml_l10_wins + _losses

The model was trained WITH those columns carrying real values. In
production their coefficients contribute a fixed offset instead of
signal, so the thing scoring games is not the thing that was validated.
Eight of the twenty are sharp-money features — exactly the inputs a
market-facing model would lean on hardest.

**A further 9 features exist in memory but not in the database**
(`home/away_sp_k_pct`, `_whiff_rate`, `_gb_pct`, `_days_rest`,
`wind_mph`). `game_context.py` computes them and passes them in the ctx
dict; they are never persisted. So:

      main run (game_context)      model sees 87 of 107 features
      recompute_primary_play       reads select=* from DB -> 78 of 107

The two paths disagree on 9 features BY CONSTRUCTION, which means
recompute can legitimately produce a different pick for the same game
with the same data. Some of what recompute "fixes" is that.

NOT A BUG, checked and dismissed: the 0.4284 shared by 10 of 16 games
this morning is the all-features-missing prediction, stamped by
`game_context.py` before the enrichers run. `recompute_primary_play`
sits at mlb_pipeline.yml:460, after the enrichers and Monte Carlo, and
corrects it. The pipeline self-heals. I nearly "fixed" it.

Worth noting anyway: between those two steps the live board shows
COVERAGE on games that will become PRIME. On 09-23 that was 10 of 16
games for roughly half an hour.

FIX, in order:
  1. Make imputation audible. A model silently running on 19% medians
     should say so — count imputed features per prediction and refuse,
     or at minimum log, past a threshold. Same class as B11: a
     degradation that looks identical to normal operation.
  2. Populate or drop the 20. `home_ats_l10_wins` and friends are
     derivable from data we already hold (team_situational_records has
     exactly these). The 8 sharp-money features need a source decision.
  3. Persist the 9 in-memory features so both code paths see the same
     model.
  4. Re-validate after. Any backtest of this model measured a feature
     set production does not have.
VERIFY:
  `python -c "import defensive_gates as G; print(len(G._LR_MODEL_MLB_ML['features']))"`
  then diff that list against `select * from mlb_game_context limit 1`.

### B39 - The prop SELECTION works. The prop PIPELINE does not run.
Andy, 09-23: "WE NEED TO FIX THE PROP PIPELINE." Chased it properly and
the answer is not where I kept looking.

Held-out test. Clean history (leak window excluded) split
chronologically; signals chosen on the FIRST half only, then applied to
the second, which had no hand in selecting anything:

      HELD OUT 2026-08-13 -> 09-02              n=2843
      everything                 50.5% vs 54.4%   -3.9pp  ROI  -6.9%  z=-4.12
      what we publish (P/S)      69.8% vs 57.9%  +11.9pp  ROI +21.5%  z=+3.13   n=169
      my mined signal rule       57.5% vs 54.3%   +3.2pp  ROI  +6.3%  z=+0.57   n=80

**The existing PRIME/STRONG gate holds out at +21.5% ROI, z=+3.13.**
That is a second independent slice agreeing with B36's +9.4pp - different
window, different method, same answer. Andy said the 08-20 adjustments
worked and backtested well. They did.

**My signal-mining does NOT hold out.** In-sample it read +9.6pp
(z=2.84) on a rule stacking 2+ of 5 "high-lift" signals; held out it
decays to +3.2pp (z=0.57). Testing 100 signals guarantees ~5 clear z>2
by chance. Not shipping it. Same trap as the NFL projection (B33) which
read +7.1% in-sample and -1.0% out.

SO WHAT IS ACTUALLY BROKEN: the pipeline does not reliably RUN.
  * 09-23: crashed at play_of_day (my UnboundLocalError, 0a4153e0).
    Everything downstream - recompute_primary_play, jerry synthesis,
    prop scoring - never executed. Board sat at 3 PRIME/STRONG instead
    of the usual ~47, and 10 of 16 games held a stale LR shadow.
  * 09-16..09-22: rollup refresh RPCs returned 42809 every call, unheard
    (B28/B32).
  * 319 sites turn an HTTP failure into an empty list (B11).
  * close_over_odds lands on 68 of 26,040 props, so CLV - the one metric
    that works on a small sample - is unmeasurable (B32).

The model picks fine when it gets to run. Reliability is the work.

DO NOT re-mine signals for edge without a holdout. The lift table is
seductive: short_last showed +16.0pp lift at z=+4.30 on the full clean
set and evaporates out of sample.
VERIFY: split mlb_pipeline_props on game_date excluding 09-03..09-22,
pick signals by lift z>=2 on the first half, score the second.

### B40 - mlb_pipeline.yml: 172 serial steps, 8 of them repair work
Andy, 09-23: "the pipeline doesn't reliably run because you have added
so much shit to the mlb pipeline that it takes almost 2 hours and refuse
to take a deep look to simplify."

Measured rather than argued:

      file                2,959 lines / 150 KB
      jobs                1        <- fully serial, nothing parallel
      steps               172
      python invocations  202 across 164 distinct scripts
      continue-on-error   135 of 172 (78%)
      bare `|| echo`      39
      timeout-minutes     NONE SET  <- a hung step burns to GitHub's 6h default
      cron triggers       4/day

WHAT IS NOT THE PROBLEM. I first counted 27 scripts invoked more than
once and implied waste. Wrong - almost all are different sports, windows
or flags. Only **5** invocations are provably redundant (same script,
same args). The bloat is not duplicate calls.

WHAT IS THE PROBLEM.

1. ONE SERIAL JOB. 172 steps nose-to-tail is the ~2 hours. The 13
   enrichment steps (savant, team form, trends, arsenal, monte carlo,
   externals, recency, H2H) are largely independent of each other and
   currently queue behind one another for no reason.

2. A FATAL STEP KILLS EVERYTHING AFTER IT. 35 steps have no
   continue-on-error; 20 of those sit after step 60. play_of_day is
   **step 98 of 172**. When it crashed on 09-23 it took 74 unrelated
   downstream steps with it - jerry reads, prop scoring, Sharp Card,
   Sweat Card - none of which depend on it. A POTD failure should cost
   a POTD.

3. EIGHT STEPS EXIST ONLY TO REPAIR EARLIER STEPS:

       step  28  Jerry pick scrub (sync CALL fields to primary_play)
       step  49  Collapse Prop Jerry contradictions
       step  50  Collapse pitcher-thesis contradictions
       step  52  Dedup prop dupes (final pass)
       step  53  Cleanup orphaned jerry_reads (post-dedup)
       step 132  FINAL recompute primary_play (post-all-mutations barrier)
       step 157  Rescue - force MC + props + ladder + ledger if missing
       step   5  Yesterday catch-up - grade + aggregate + surface_records

   Step 132 is NAMED "post-all-mutations barrier". That is the design
   admitting dozens of steps mutate the same rows and something late has
   to reconcile them. This is exactly the manufacturing-line objection
   Andy raised on 09-22: do not add a step whose job is to fix step 4.

4. 78% OF FAILURES ARE INVISIBLE. 135 continue-on-error steps, 31 of
   them on critical-sounding work (game_context patches, jerry
   synthesis, grading, prop refit). B11's silent-failure class at the
   workflow layer.

PLAN, ordered by payoff per unit of risk:
  1. Split into parallel jobs with real `needs:` edges - ingest/enrich,
     score, publish, grade. Biggest wall-clock win, no logic changes.
  2. Contain fatal steps so a crash stops its branch, not the run.
  3. Delete the 8 repair steps by fixing the mutation ordering they
     paper over. This is the actual simplification and where the
     runtime lives.
  4. Add timeout-minutes to the job.
VERIFY: `python -c "import yaml;d=yaml.safe_load(open('.github/workflows/mlb_pipeline.yml'));s=d['jobs']['run-mlb-pipeline']['steps'];print(len(s),sum(1 for x in s if x.get('continue-on-error') is True))"`

### B41 - jerry_reads has NO database-level write protection
Found 09-23 while auditing migration state after the mutation-after-event
incident. Every freeze trigger built on 09-17/09-18 protects a different
table than the one that actually got rewritten.

      trigger                        guards
      enforce_publish_lock_tier()    mlb/nfl_pipeline_props
      enforce_publish_lock_side()    mlb/nfl/ncaaf_game_context.primary_play
      freeze_opening_lines()         *_game_context opening lines
      freeze_receipt_identity()      public_receipts
      ---
      jerry_reads                    NOTHING. 0 triggers.
      prop_jerry_reads               NOTHING. 0 triggers.

`jerry_reads` carries the published narrative call - `call_text`,
`call_market`, `conviction` - and it is the table
`backfill_jerry_pick_alignment.py:282` PATCHes. So on 09-23 the DB
accepted the rewrite of a finished game's winning read with no
objection, three hours after the final out.

The B12 guard added that day (`started_matchups()`) lives in Python, in
ONE script. It protects that script and nothing else. Anything else with
the service key - another script, a workflow step, a console - can still
rewrite a played game's read. That is a guard sitting one layer above
where the rule belongs.

FIX: a BEFORE UPDATE trigger on jerry_reads/prop_jerry_reads refusing
changes to call_text/call_market/conviction once the game has started,
mirroring enforce_publish_lock_side's shape. Needs a start-time source
the DB can see (game_context first pitch / kickoff), which is the real
work - the trigger itself is ~20 lines.

NOT the publish lock. That is keyed on time-of-day, so it protects a
finished game at 16:35 and protects nothing at 11:00 for a game that
started at 10:05.

VERIFY: `select tgrelid::regclass, tgname from pg_trigger
         where not tgisinternal and tgrelid::regclass::text like '%jerry_reads%';`

### B42 - the hallucination guard fires, then a later step erases it
We are not missing a number validator. We have one, it works, and the
pipeline overwrites its verdict three hours later.

Measured on 728 published MLB reads (2026-07-30 -> 09-23), grounding
every numeric claim against the data the writer was given:

      window          n     fabricated stat    misattributed
      before 09-16   624         9.8%              6.4%
      09-16 onward   104        45.2%             15.4%

Seven stable weeks, then a 4.6x step change on ONE day. Two commits
landed 2026-09-16 on the read generator: `51c67cbb` (analyst writeup v1
- a much denser, stat-heavy prompt) and `04aa9505` (widened the number
validator's whitelist so analyst-mode stats stopped tripping it). The
correlation is established; which of the two drives it is not, and that
is worth isolating before changing either.

WHAT ACTUALLY SHIPS. Running the EXISTING validator over today's slate:
12 of 16 reads come back is_valid=False, 21 untraceable figures - more
than my own checker found. Detection is not the problem. This is:

      generate_jerry_synthesis.py:1148-1179
        3+ bad numbers -> conviction hard-floored to 45 + user footer
        1-2 bad numbers -> conviction capped to 55, footer HIDDEN
        never -> refuse to publish

      backfill_jerry_pick_alignment.py:188  _FIELDS includes 'conviction'
        -> enforce_primary_play_alignment syncs conviction FROM
           primary_play, overwriting whatever the cap set

Today every read was written 12:55-13:00 and every primary_play was
recomputed 15:31-15:59. Result:

      9 of 16 reads published ABOVE their own validator's cap
      0 of 16 carry the integrity footer
      the only two sitting exactly at 55 are caps that happened to survive

So the CHW @ KC card that named the wrong pitcher for an xERA, the
Reds/Braves card asserting a 28.76 career ERA, and the Rays/Yankees card
that swapped Cole's and Seymour's numbers were all flagged by our own
code before they shipped, demoted, and then silently re-promoted.

This is the manufacturing-line objection exactly: step 4 flags a defect,
step 132 ("FINAL recompute primary_play - post-all-mutations barrier",
B40) erases the flag, and the defect ships at full conviction.

FIX, in order:
  1. A failed number validation must BLOCK the read, not discount it.
     Fall back to the structured card, which is not wrong.
  2. Conviction set by a safety cap must not be a field the aligner is
     allowed to overwrite. Carry the cap as its own column so a sync
     cannot silently undo it.
  3. Isolate which 09-16 commit moved the rate; consider reverting the
     whitelist widening on its own.
  4. Then slot-fill the numbers so the failure stops being possible
     rather than merely caught.

VERIFY: `python -c "import sys;sys.path.insert(0,'mlb_pipeline');
         import validate_jerry_read"` and compare its
         hallucinated_numbers against published conviction.

## P2 — structural (the ones that keep causing the others)

### B11 · 319 sites turn an HTTP failure into an empty list
`return r.json() if r.status_code == 200 else []` — a 400, 429, 500 and
"zero rows" are the same value. Root cause behind the pick-ledger
outage, the blank columns, and the POTD tier map. Fix is one honest
`get()` that raises, inherited by every call site — this **retires**
guards rather than adding them.
VERIFY: `grep -rc "status_code == 200 else \[\]" mlb_pipeline/*.py | ...`

### B12 · Prediction rows stay mutable after the event starts
The L5 leak, the tier rewriting, and the DH being overwritten by an
in-flight job are one missing rule: *no writer modifies a row whose game
has begun.* Enforced once (ideally a DB trigger) it makes the whole
class impossible.

### B13 · No reads-vs-select guard
Nothing checks that what the client reads matches what it asks for. This
is how 86 columns went dark for nine days. The column lists are now
generated from actual reads, but nothing prevents the next narrowing.
Better fix than a guard: a **view** that IS the app payload, so the
client does `select('*')` and adding a column becomes a migration
instead of an App Store release.

### B14 · Receipts layer is post-hoc
`public_receipts` is 11,233 `reconstructed` vs **38** `live`. No pick is
captured cleanly at publish time, so no record is independently
verifiable.

---

## P3 — small / cosmetic

- **B15** `is_opener` is False on all starters — flag never populated.
- **B16** `game.dome_game` is read but the column is `is_dome` — that
  branch has never fired.
- **B17** `calcAndPatchMLBContext` is dead code that writes
  `projected_total` back to the DB. Not called. Leave dead — client
  computing model values contradicts server-decides. Landmine if revived.
- **B18** 51 bare `|| echo` workflow steps mask failures.

---

## Closed 2026-09-22

| item | SHA |
|---|---|
| MLB/NFL/NCAAF context columns restored (161/88/84) | `e6f99ed8` `d866ba1a` `99f2f158` |
| Trailing comma that 400'd NFL+NCAAF (self-inflicted) | `0e3819e2` |
| Prop L5/L10 leak closed at source | `20b443cc` |
| Jerry badge-vs-read contradiction (5 → 0) | `d7f803fa` |
| `play_of_day.py` hardcoded % → live cohort resolver (20/23) | `034adc4b` |
| Prop Jerry rendered a family bucket constant as the score | `8c48c70b` |
| POTD tier map dead for 90 days — every POTD showed STRONG | `b04bb68c` |
| NFL pick ledger PGRST102 silent outage + backfill | `47617079` |
| Sharp: NHL preseason, unpriced ML, void pick (Jackson Kent) | `971943a1` `8bde8a2e` `f2faee04` |
| Ledger admin notice expired | data-only |
| `game_context.py` user-facing hardcoded % + shared resolver | `0e199a36` |
| DH sibling pairing scoped to same ET date | `5a079e2c` |
| daily log 09-22 + first BACKLOG.md | `8fb41619` |
| UFC forward slate + my write-order regression + pull_log | `fd1a6d3d` `29aa26d9` |
| balldontlie removed from client (key was live) | `5fd99e56` |
| MLB tab note moved to sport_registry, derived from real state | `ed469ff6` |
| notes relocated below filters | `055ef4a1` |
| conviction-0 chips render NO PLAY | pending build |
