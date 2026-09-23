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
